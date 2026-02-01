import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.bot_settings_service import BotSettingsService
from app.utils.auth import is_allowed, user_label

logger = logging.getLogger(__name__)


def create_start_router(
    settings_service: BotSettingsService,
    allowed_user_ids: list[int],
    allowed_usernames: list[str],
) -> Router:
    router = Router()

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if not is_allowed(message.from_user, allowed_user_ids, allowed_usernames):
            logger.warning("Unauthorized user: %s", user_label(message.from_user))
            await message.answer("⛔️ Доступ запрещен.")
            return

        settings = await settings_service.load()
        kb = InlineKeyboardBuilder()
        kb.button(text="⚙️ Настройки", callback_data="menu:main")
        kb.button(text="❓ Как пользоваться", callback_data="menu:help")
        kb.button(text="📌 Сводки: этот чат", callback_data="summary:set_chat")
        kb.button(text=f"🕒 Таймзона: {settings.timezone}", callback_data="menu:timezone")
        kb.adjust(1)

        await message.answer(
            "Привет! Я бот‑второй мозг.\n\n"
            "Как пользоваться:\n"
            "1) Отправляйте голосовые — я сам пойму: добавить / вопрос / удалить.\n"
            "2) Если есть обязательные поля (*) и данных нет — я спрошу.\n"
            "3) «Сводки: этот чат» назначает этот чат для ежедневных/недельных сводок.\n\n"
            "Дальше всё на кнопках ниже.",
            reply_markup=kb.as_markup(),
        )

    return router
