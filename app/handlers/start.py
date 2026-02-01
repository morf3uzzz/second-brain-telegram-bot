import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
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
        kb.adjust(1)

        await message.answer(
            "👋 Как со мной работать? Всё просто!\n\n"
            "🎙 Запись данных\n"
            "Просто отправь мне голосовое сообщение. Я расшифрую его и сам разнесу данные в нужный лист таблицы.\n\n"
            "❓ Вопросы\n"
            "Хочешь что-то узнать? Просто спроси голосом или скажи: «Вопрос: сколько я потратил...». Я найду ответ в твоих записях.\n\n"
            "🗑 Удаление\n"
            "Ошибся? Скажи: «Удали...» или «Убери...». Я покажу список последних записей, и ты выберешь, что стереть.\n\n"
            "⭐ Умные колонки (Обязательные поля)\n"
            "Хочешь, чтобы я следил за порядком?\n"
            "Добавь звёздочку * к названию столбца в таблице (например: Сумма*).\n"
            "Если ты забудешь продиктовать это значение, я обязательно переспрошу!\n\n"
            "🧾 Сводки\n"
            "Я буду присылать тебе краткие отчёты за день или неделю прямо сюда. Автоматически и без напоминаний.\n\n"
            "👇 Настройки — в меню ниже",
            reply_markup=kb.as_markup(),
        )

    @router.callback_query(F.data == "menu:start")
    async def show_start(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        
        kb = InlineKeyboardBuilder()
        kb.button(text="⚙️ Настройки", callback_data="menu:main")
        kb.adjust(1)
        
        text = (
            "👋 Как со мной работать? Всё просто!\n\n"
            "🎙 Запись данных\n"
            "Просто отправь мне голосовое сообщение. Я расшифрую его и сам разнесу данные в нужный лист таблицы.\n\n"
            "❓ Вопросы\n"
            "Хочешь что-то узнать? Просто спроси голосом или скажи: «Вопрос: сколько я потратил...». Я найду ответ в твоих записях.\n\n"
            "🗑 Удаление\n"
            "Ошибся? Скажи: «Удали...» или «Убери...». Я покажу список последних записей, и ты выберешь, что стереть.\n\n"
            "⭐ Умные колонки (Обязательные поля)\n"
            "Хочешь, чтобы я следил за порядком?\n"
            "Добавь звёздочку * к названию столбца в таблице (например: Сумма*).\n"
            "Если ты забудешь продиктовать это значение, я обязательно переспрошу!\n\n"
            "🧾 Сводки\n"
            "Я буду присылать тебе краткие отчёты за день или неделю прямо сюда. Автоматически и без напоминаний.\n\n"
            "👇 Настройки — в меню ниже"
        )
        
        try:
            await callback.message.edit_text(text, reply_markup=kb.as_markup())
        except Exception:
            await callback.message.answer(text, reply_markup=kb.as_markup())
        await callback.answer()

    return router
