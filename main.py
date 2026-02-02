import asyncio
import logging
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.handlers.voice import create_voice_router
from app.handlers.start import create_start_router
from app.handlers.settings import create_settings_router
from app.handlers.delete import create_delete_router
from app.logging_setup import setup_logging
from app.scheduler import scheduler_loop
from app.services.bot_settings_service import BotSettingsService
from app.services.delete_service import DeleteService
from app.services.intent_service import IntentService
from app.services.openai_service import OpenAIService
from app.services.qa_service import QAService
from app.services.router_service import RouterService
from app.services.sheets_service import SheetsService
from app.services.summary_service import SummaryService
from config import Config


async def main() -> None:
    setup_logging()
    config = Config.from_env()
    logger = logging.getLogger(__name__)

    base_dir = Path(__file__).resolve().parent
    service_account_path = base_dir / "service_account.json"
    if not service_account_path.exists():
        fallback_path = base_dir / "service_account.json.json"
        if fallback_path.exists():
            logger.warning("service_account.json not found, using %s", fallback_path.name)
            service_account_path = fallback_path
        else:
            raise FileNotFoundError(
                f"Service account key not found in {service_account_path} or {fallback_path}"
            )

    openai_service = OpenAIService(api_key=config.openai_api_key)
    sheets_service = await SheetsService.create(
        spreadsheet_id=config.google_sheet_id,
        service_account_path=service_account_path,
    )
    await sheets_service.ensure_worksheet("Inbox", rows=1000, cols=10)
    router_service = RouterService(openai_service)
    intent_service = IntentService(openai_service)
    settings_service = BotSettingsService(base_dir / "data" / "settings.json")
    qa_service = QAService(openai_service, sheets_service)
    delete_service = DeleteService(sheets_service)
    summary_service = SummaryService(openai_service, sheets_service)

    bot = Bot(token=config.telegram_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(
        create_voice_router(
            openai_service,
            sheets_service,
            router_service,
            intent_service,
            settings_service,
            qa_service,
            delete_service,
            allowed_user_ids=config.allowed_user_ids,
            allowed_usernames=config.allowed_usernames,
        )
    )
    dp.include_router(
        create_start_router(
            settings_service,
            allowed_user_ids=config.allowed_user_ids,
            allowed_usernames=config.allowed_usernames,
        )
    )
    dp.include_router(
        create_delete_router(
            delete_service,
            allowed_user_ids=config.allowed_user_ids,
            allowed_usernames=config.allowed_usernames,
        )
    )
    dp.include_router(
        create_settings_router(
            sheets_service,
            settings_service,
            summary_service,
            allowed_user_ids=config.allowed_user_ids,
            allowed_usernames=config.allowed_usernames,
        )
    )

    asyncio.create_task(scheduler_loop(bot, settings_service, summary_service))
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
