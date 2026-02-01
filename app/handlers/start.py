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
        if settings.summary_chat_id is None:
            await settings_service.update({"summary_chat_id": message.chat.id})
            settings = await settings_service.load()
        kb = InlineKeyboardBuilder()
        kb.button(text="⚙️ Настройки", callback_data="menu:main")
        kb.button(text="❓ Как пользоваться", callback_data="menu:help")
        kb.button(text=f"🕒 Таймзона: {settings.timezone}", callback_data="menu:timezone")
        kb.adjust(1)

        await message.answer(
            "👋 Привет! Я бот‑второй мозг.\n\n"
            "Вот как пользоваться:\n"
            "🎙️ **Запись** — просто отправьте голосовое. Я сам сохраню его в нужный лист.\n"
            "❓ **Вопрос** — скажите голосом: «вопрос: …» или задайте вопрос обычным голосом.\n"
            "🗑️ **Удаление** — скажите: «удали …» или «убери …». Я покажу список и вы выберете.\n\n"
            "⭐️ **Обязательные поля** — это колонки, которые нельзя оставлять пустыми.\n"
            "Чтобы сделать поле обязательным, добавьте `*` в заголовок столбца, например: `Приоритет*`.\n"
            "Если в голосе нет значения — я спрошу уточнение.\n\n"
            "🧾 **Сводки** — это краткий отчёт за день или неделю по вашим записям.\n"
            "Они приходят в этот чат автоматически.\n\n"
            "Ниже — кнопки для настроек.",
            reply_markup=kb.as_markup(),
        )

    return router
