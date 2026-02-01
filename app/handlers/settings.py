import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.prompts import DEFAULT_EXTRACT_USER, DEFAULT_ROUTER_USER, EXTRACT_PROMPT_KEY, ROUTER_PROMPT_KEY
from app.services.bot_settings_service import BotSettingsService
from app.services.sheets_service import SheetsService
from app.utils.auth import is_allowed, user_label

logger = logging.getLogger(__name__)


class SettingsState(StatesGroup):
    editing_prompt = State()
    editing_daily_time = State()
    editing_weekly_time = State()
    editing_timezone = State()


def create_settings_router(
    sheets_service: SheetsService,
    settings_service: BotSettingsService,
    allowed_user_ids: list[int],
    allowed_usernames: list[str],
) -> Router:
    router = Router()

    @router.message(Command("settings"))
    async def settings_menu(message: Message) -> None:
        if not is_allowed(message.from_user, allowed_user_ids, allowed_usernames):
            logger.warning("Unauthorized user: %s", user_label(message.from_user))
            await message.answer("⛔️ Доступ запрещен.")
            return

        settings = await settings_service.load()
        kb = _build_main_menu(settings)
        await message.answer(
            "⚙️ Настройки.\n\n"
            "Как пользоваться:\n"
            "- Просто отправляйте голосовые.\n"
            "- Бот сам поймёт: добавить / вопрос / удалить.\n"
            "- Если не хватает обязательных полей (*), он спросит уточнение.",
            reply_markup=kb.as_markup(),
        )

    @router.callback_query(F.data == "menu:main")
    async def show_main_menu(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        settings = await settings_service.load()
        kb = _build_main_menu(settings)
        await callback.message.answer("Главное меню настроек:", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(F.data == "menu:prompts")
    async def show_prompts_menu(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        kb = _build_prompts_menu()
        await callback.message.answer("Промпты:", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(F.data == "menu:summaries")
    async def show_summaries_menu(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        settings = await settings_service.load()
        kb = _build_summaries_menu(settings)
        await callback.message.answer("Сводки:", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(F.data == "menu:timezone")
    async def show_timezone_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_timezone)
        await callback.message.answer(
            "Введите таймзону, например: Europe/Moscow или UTC."
        )
        await callback.answer()

    @router.callback_query(F.data == "menu:help")
    async def show_help(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await callback.message.answer(
            "Краткая помощь:\n"
            "- Голосом: добавление / вопрос / удаление определяются автоматически.\n"
            "- Обязательные поля отмечайте * в заголовках.\n"
            "- Для удаления бот пришлёт список и кнопки с номерами."
        )
        await callback.answer()


    @router.callback_query(F.data == "prompt:show")
    async def show_prompts(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return

        prompts = await sheets_service.get_prompts()
        router_prompt = prompts.get(ROUTER_PROMPT_KEY, DEFAULT_ROUTER_USER)
        extract_prompt = prompts.get(EXTRACT_PROMPT_KEY, DEFAULT_EXTRACT_USER)

        await callback.message.answer(
            "Текущие prompts:\n\n"
            f"ROUTER:\n{router_prompt}\n\n"
            f"EXTRACT:\n{extract_prompt}"
        )
        await callback.answer()

    @router.callback_query(F.data == "prompt:router")
    async def edit_router_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_prompt)
        await state.update_data(prompt_key=ROUTER_PROMPT_KEY)
        await callback.message.answer(
            "Отправьте новый prompt для роутера.\n"
            "Обязательные плейсхолдеры: {text}, {categories}"
        )
        await callback.answer()

    @router.callback_query(F.data == "prompt:extract")
    async def edit_extract_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_prompt)
        await state.update_data(prompt_key=EXTRACT_PROMPT_KEY)
        await callback.message.answer(
            "Отправьте новый prompt для извлечения.\n"
            "Обязательные плейсхолдеры: {text}, {headers}"
        )
        await callback.answer()

    @router.callback_query(F.data == "summary:set_chat")
    async def set_summary_chat(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        chat_id = callback.message.chat.id
        await settings_service.update({"summary_chat_id": chat_id})
        await callback.message.answer("✅ Этот чат установлен для сводок.")
        await callback.answer()

    @router.callback_query(F.data == "summary:toggle_daily")
    async def toggle_daily(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        settings = await settings_service.load()
        await settings_service.update({"daily_enabled": not settings.daily_enabled})
        await callback.message.answer("✅ Обновил режим ежедневных сводок.")
        await callback.answer()

    @router.callback_query(F.data == "summary:toggle_weekly")
    async def toggle_weekly(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        settings = await settings_service.load()
        await settings_service.update({"weekly_enabled": not settings.weekly_enabled})
        await callback.message.answer("✅ Обновил режим еженедельных сводок.")
        await callback.answer()

    @router.callback_query(F.data == "summary:daily_time")
    async def edit_daily_time(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_daily_time)
        await callback.message.answer("Введите время ежедневной сводки в формате HH:MM.")
        await callback.answer()

    @router.callback_query(F.data == "summary:weekly_time")
    async def edit_weekly_time(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_weekly_time)
        await callback.message.answer("Введите время еженедельной сводки в формате HH:MM.")
        await callback.answer()

    @router.callback_query(F.data == "summary:weekly_day")
    async def edit_weekly_day(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        kb = InlineKeyboardBuilder()
        for code, label in [
            ("mon", "Пн"),
            ("tue", "Вт"),
            ("wed", "Ср"),
            ("thu", "Чт"),
            ("fri", "Пт"),
            ("sat", "Сб"),
            ("sun", "Вс"),
        ]:
            kb.button(text=label, callback_data=f"summary:set_weekday:{code}")
        kb.adjust(4, 3)
        await callback.message.answer("Выберите день недели:", reply_markup=kb.as_markup())
        await callback.answer()

    @router.callback_query(F.data.startswith("summary:set_weekday:"))
    async def set_weekly_day(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        day_code = callback.data.split(":")[-1]
        await settings_service.update({"weekly_day": day_code})
        await callback.message.answer("✅ День недели обновлен.")
        await callback.answer()

    @router.callback_query(F.data == "summary:timezone")
    async def edit_timezone(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_timezone)
        await callback.message.answer(
            "Введите таймзону, например: Europe/Moscow или UTC."
        )
        await callback.answer()

    @router.message(SettingsState.editing_daily_time, F.text)
    async def save_daily_time(message: Message, state: FSMContext) -> None:
        time_text = message.text.strip()
        if not _is_valid_time(time_text):
            await message.answer("⚠️ Неверный формат. Пример: 21:00")
            return
        await settings_service.update({"daily_time": time_text})
        await state.clear()
        await message.answer("✅ Время ежедневной сводки обновлено.")

    @router.message(SettingsState.editing_weekly_time, F.text)
    async def save_weekly_time(message: Message, state: FSMContext) -> None:
        time_text = message.text.strip()
        if not _is_valid_time(time_text):
            await message.answer("⚠️ Неверный формат. Пример: 20:00")
            return
        await settings_service.update({"weekly_time": time_text})
        await state.clear()
        await message.answer("✅ Время еженедельной сводки обновлено.")

    @router.message(SettingsState.editing_timezone, F.text)
    async def save_timezone(message: Message, state: FSMContext) -> None:
        tz = message.text.strip()
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            await message.answer("⚠️ Таймзона не найдена. Пример: Europe/Moscow")
            return
        await settings_service.update({"timezone": tz})
        await state.clear()
        await message.answer("✅ Таймзона обновлена.")

    @router.message(SettingsState.editing_prompt, F.text)
    async def save_prompt(message: Message, state: FSMContext) -> None:
        if not is_allowed(message.from_user, allowed_user_ids, allowed_usernames):
            await message.answer("⛔️ Доступ запрещен.")
            return

        data = await state.get_data()
        key = data.get("prompt_key")
        if not key:
            await message.answer("⚠️ Не удалось определить тип prompt.")
            await state.clear()
            return

        text = message.text.strip()
        missing = _missing_placeholders(key, text)
        if missing:
            await message.answer(
                "⚠️ В prompt нет обязательных плейсхолдеров: "
                + ", ".join(missing)
            )
            return

        await sheets_service.set_prompt(key, text)
        await state.clear()
        await message.answer("✅ Prompt сохранен.")

    return router


def _missing_placeholders(key: str, text: str) -> list[str]:
    required = []
    if key == ROUTER_PROMPT_KEY:
        required = ["{text}", "{categories}"]
    if key == EXTRACT_PROMPT_KEY:
        required = ["{text}", "{headers}"]
    return [ph for ph in required if ph not in text]


def _build_main_menu(settings) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Промпты", callback_data="menu:prompts")
    kb.button(text="Сводки", callback_data="menu:summaries")
    kb.button(text=f"Таймзона: {settings.timezone}", callback_data="menu:timezone")
    kb.button(text="Помощь", callback_data="menu:help")
    kb.adjust(2, 2)
    return kb


def _build_prompts_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Показать prompts", callback_data="prompt:show")
    kb.button(text="Изменить router", callback_data="prompt:router")
    kb.button(text="Изменить extract", callback_data="prompt:extract")
    kb.button(text="Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb


def _build_summaries_menu(settings) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Этот чат для сводок", callback_data="summary:set_chat")
    kb.button(
        text=f"Ежедневные {'✅' if settings.daily_enabled else '❌'}",
        callback_data="summary:toggle_daily",
    )
    kb.button(
        text=f"Еженедельные {'✅' if settings.weekly_enabled else '❌'}",
        callback_data="summary:toggle_weekly",
    )
    kb.button(text=f"Время дня: {settings.daily_time}", callback_data="summary:daily_time")
    kb.button(text=f"День недели: {settings.weekly_day}", callback_data="summary:weekly_day")
    kb.button(text=f"Время недели: {settings.weekly_time}", callback_data="summary:weekly_time")
    kb.button(text="Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb


def _is_valid_time(value: str) -> bool:
    if len(value) != 5 or value[2] != ":":
        return False
    hours, minutes = value.split(":", 1)
    if not hours.isdigit() or not minutes.isdigit():
        return False
    hour = int(hours)
    minute = int(minutes)
    return 0 <= hour <= 23 and 0 <= minute <= 59
