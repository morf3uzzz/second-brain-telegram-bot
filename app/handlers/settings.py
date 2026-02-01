import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from datetime import date

from app.prompts import DEFAULT_EXTRACT_USER, DEFAULT_ROUTER_USER, EXTRACT_PROMPT_KEY, ROUTER_PROMPT_KEY
from app.services.bot_settings_service import BotSettingsService
from app.services.summary_service import SummaryService
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
    summary_service: SummaryService,
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
        if settings.summary_chat_id is None:
            await settings_service.update({"summary_chat_id": message.chat.id})
            settings = await settings_service.load()
        kb = _build_main_menu(settings)
        text = (
            "⚙️ Меню настроек.\n\n"
            "Здесь можно настроить:\n"
            "🧠 Инструкции — как бот понимает ваши данные.\n"
            "🤖 Модель ИИ — баланс цены и качества.\n"
            "📊 Сводки — когда и как присылать отчёты.\n"
            "🕒 Таймзона — чтобы время совпадало с вашим.\n\n"
            "Выберите нужный раздел 👇"
        )
        await message.answer(text, reply_markup=kb.as_markup())

    @router.callback_query(F.data == "menu:main")
    async def show_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.clear()
        settings = await settings_service.load()
        kb = _build_main_menu(settings)
        text = (
            "⚙️ Меню настроек.\n\n"
            "Здесь можно настроить:\n"
            "🧠 Инструкции — как бот понимает ваши данные.\n"
            "🤖 Модель ИИ — баланс цены и качества.\n"
            "📊 Сводки — когда и как присылать отчёты.\n"
            "🕒 Таймзона — чтобы время совпадало с вашим.\n\n"
            "Выберите нужный раздел 👇"
        )
        await _show_menu(callback, text, kb)
        await callback.answer()

    @router.callback_query(F.data == "menu:models")
    async def show_models_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.clear()
        settings = await settings_service.load()
        kb = _build_models_menu(settings.openai_model)
        await _show_menu(
            callback,
            "🤖 Выберите модель ИИ.\n\n"
            "Цены указаны за 1М токенов (Вход / Выход).\n"
            "Вход — это ваше сообщение + контекст.\n"
            "Выход — ответ нейросети.",
            kb,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("model:set:"))
    async def set_model(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        model = callback.data.split(":", 2)[2]
        await settings_service.update({"openai_model": model})
        settings = await settings_service.load()
        kb = _build_models_menu(settings.openai_model)
        await _show_menu(callback, f"✅ Модель изменена на {model}.", kb)
        await callback.answer()

    @router.callback_query(F.data == "menu:prompts")
    async def show_prompts_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.clear()
        kb = _build_prompts_menu()
        await _show_menu(callback, "Инструкции для ИИ:", kb)
        await callback.answer()

    @router.callback_query(F.data == "menu:summaries")
    async def show_summaries_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.clear()
        settings = await settings_service.load()
        kb = _build_summaries_menu(settings)
        await _show_menu(callback, "Сводки (приходят в этот чат):", kb)
        await callback.answer()

    @router.callback_query(F.data == "menu:timezone")
    async def show_timezone_menu(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_timezone)
        await _show_menu(
            callback,
            "Введите таймзону (например: Europe/Moscow или UTC).\n"
            "Чтобы отменить — нажмите «Отмена».",
            _build_cancel_menu("menu:main"),
        )
        await callback.answer()

    @router.callback_query(F.data == "menu:help")
    async def show_help(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        
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
            "Я буду присылать тебе краткие отчёты за день или неделю прямо сюда. Автоматически и без напоминаний."
        )
        await _show_menu(callback, text, _build_cancel_menu("menu:main", "⬅️ Назад"))
        await callback.answer()


    @router.callback_query(F.data == "prompt:show")
    async def show_prompts(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return

        prompts = await sheets_service.get_prompts()
        router_prompt = prompts.get(ROUTER_PROMPT_KEY, DEFAULT_ROUTER_USER)
        extract_prompt = prompts.get(EXTRACT_PROMPT_KEY, DEFAULT_EXTRACT_USER)

        text = (
            "Текущие инструкции:\n\n"
            "Определение категории:\n"
            f"{router_prompt}\n\n"
            "Заполнение таблицы:\n"
            f"{extract_prompt}"
        )
        await _show_menu(callback, text, _build_prompts_menu())
        await callback.answer()

    @router.callback_query(F.data == "prompt:router")
    async def edit_router_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_prompt)
        await state.update_data(prompt_key=ROUTER_PROMPT_KEY)
        await _show_menu(
            callback,
            "Отправьте текст инструкции для определения категории.\n"
            "Обязательные плейсхолдеры: {text}, {categories}\n"
            "Чтобы отменить — нажмите «Отмена».",
            _build_cancel_menu("menu:prompts"),
        )
        await callback.answer()

    @router.callback_query(F.data == "prompt:extract")
    async def edit_extract_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_prompt)
        await state.update_data(prompt_key=EXTRACT_PROMPT_KEY)
        await _show_menu(
            callback,
            "Отправьте текст инструкции для заполнения таблицы.\n"
            "Обязательные плейсхолдеры: {text}, {headers}\n"
            "Доступно: {today}\n"
            "Чтобы отменить — нажмите «Отмена».",
            _build_cancel_menu("menu:prompts"),
        )
        await callback.answer()

    @router.callback_query(F.data == "summary:set_chat")
    async def set_summary_chat(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        chat_id = callback.message.chat.id
        await settings_service.update({"summary_chat_id": chat_id})
        settings = await settings_service.load()
        kb = _build_summaries_menu(settings)
        await _show_menu(callback, "✅ Этот чат установлен для сводок.", kb)
        await callback.answer()

    @router.callback_query(F.data == "summary:toggle_daily")
    async def toggle_daily(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        settings = await settings_service.load()
        await settings_service.update({"daily_enabled": not settings.daily_enabled})
        settings = await settings_service.load()
        await _show_menu(callback, "✅ Режим ежедневных сводок обновлён.", _build_summaries_menu(settings))
        await callback.answer()

    @router.callback_query(F.data == "summary:toggle_weekly")
    async def toggle_weekly(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        settings = await settings_service.load()
        await settings_service.update({"weekly_enabled": not settings.weekly_enabled})
        settings = await settings_service.load()
        await _show_menu(callback, "✅ Режим еженедельных сводок обновлён.", _build_summaries_menu(settings))
        await callback.answer()

    @router.callback_query(F.data == "summary:daily_time")
    async def edit_daily_time(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_daily_time)
        await _show_menu(
            callback,
            "Введите время ежедневной сводки в формате HH:MM.\n"
            "Пример: 21:00\n"
            "Чтобы отменить — нажмите «Отмена».",
            _build_cancel_menu("menu:summaries"),
        )
        await callback.answer()

    @router.callback_query(F.data == "summary:weekly_time")
    async def edit_weekly_time(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_weekly_time)
        await _show_menu(
            callback,
            "Введите время еженедельной сводки в формате HH:MM.\n"
            "Пример: 20:00\n"
            "Чтобы отменить — нажмите «Отмена».",
            _build_cancel_menu("menu:summaries"),
        )
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
        kb.button(text="Назад", callback_data="menu:summaries")
        kb.adjust(4, 3, 1)
        await _show_menu(callback, "Выберите день недели:", kb)
        await callback.answer()

    @router.callback_query(F.data.startswith("summary:set_weekday:"))
    async def set_weekly_day(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        day_code = callback.data.split(":")[-1]
        await settings_service.update({"weekly_day": day_code})
        settings = await settings_service.load()
        await _show_menu(callback, "✅ День недели обновлён.", _build_summaries_menu(settings))
        await callback.answer()

    @router.callback_query(F.data == "summary:timezone")
    async def edit_timezone(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.set_state(SettingsState.editing_timezone)
        await callback.message.answer(
            "Введите таймзону, например: Europe/Moscow или UTC.\n"
            "Чтобы отменить — напишите «Отмена» или нажмите кнопку.",
            reply_markup=_build_cancel_menu("menu:main").as_markup(),
        )
        await callback.answer()

    @router.message(SettingsState.editing_daily_time, F.text)
    async def save_daily_time(message: Message, state: FSMContext) -> None:
        if _is_cancel(message.text):
            await _cancel_flow(message, state, settings_service)
            return
        time_text = message.text.strip()
        if not _is_valid_time(time_text):
            await message.answer("⚠️ Неверный формат. Пример: 21:00")
            return
        await settings_service.update({"daily_time": time_text})
        await state.clear()
        settings = await settings_service.load()
        await message.answer(
            "✅ Время ежедневной сводки обновлено.",
            reply_markup=_build_main_menu(settings).as_markup(),
        )

    @router.message(SettingsState.editing_weekly_time, F.text)
    async def save_weekly_time(message: Message, state: FSMContext) -> None:
        if _is_cancel(message.text):
            await _cancel_flow(message, state, settings_service)
            return
        time_text = message.text.strip()
        if not _is_valid_time(time_text):
            await message.answer("⚠️ Неверный формат. Пример: 20:00")
            return
        await settings_service.update({"weekly_time": time_text})
        await state.clear()
        settings = await settings_service.load()
        await message.answer(
            "✅ Время еженедельной сводки обновлено.",
            reply_markup=_build_main_menu(settings).as_markup(),
        )

    @router.message(SettingsState.editing_timezone, F.text)
    async def save_timezone(message: Message, state: FSMContext) -> None:
        if _is_cancel(message.text):
            await _cancel_flow(message, state, settings_service)
            return
        tz = message.text.strip()
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            await message.answer("⚠️ Таймзона не найдена. Пример: Europe/Moscow")
            return
        await settings_service.update({"timezone": tz})
        await state.clear()
        settings = await settings_service.load()
        await message.answer(
            "✅ Таймзона обновлена.",
            reply_markup=_build_main_menu(settings).as_markup(),
        )

    @router.message(SettingsState.editing_prompt, F.text)
    async def save_prompt(message: Message, state: FSMContext) -> None:
        if not is_allowed(message.from_user, allowed_user_ids, allowed_usernames):
            await message.answer("⛔️ Доступ запрещен.")
            return
        if _is_cancel(message.text):
            await _cancel_flow(message, state, settings_service)
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
        settings = await settings_service.load()
        await message.answer(
            "✅ Инструкция сохранена.",
            reply_markup=_build_main_menu(settings).as_markup(),
        )

    @router.callback_query(F.data == "summary:send_daily")
    async def send_daily_summary(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        text, _count = await summary_service.daily_summary(date.today())
        await callback.message.answer(text)
        await callback.answer()

    @router.callback_query(F.data == "summary:send_weekly")
    async def send_weekly_summary(callback: CallbackQuery) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        text, _count = await summary_service.weekly_summary(date.today())
        await callback.message.answer(text)
        await callback.answer()

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
    kb.button(text="🧠 Инструкции", callback_data="menu:prompts")
    kb.button(text="🤖 Модель ИИ", callback_data="menu:models")
    kb.button(text="📊 Сводки", callback_data="menu:summaries")
    kb.button(text=f"🕒 Таймзона: {settings.timezone}", callback_data="menu:timezone")
    kb.button(text="❓ Помощь", callback_data="menu:help")
    kb.button(text="⬅️ Назад", callback_data="menu:start")
    kb.adjust(2, 2, 2)
    return kb


def _build_models_menu(current: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    models = [
        ("gpt-4o", "GPT-4o (Умная)", "$2.5/$10"),
        ("gpt-4o-mini", "GPT-4o Mini (Быстрая)", "$0.15/$0.6"),
        ("o1-mini", "o1 Mini (Рассуждающая)", "$3/$12"),
    ]
    for model, label, price in models:
        prefix = "✅ " if model == current else ""
        text = f"{prefix}{label} — {price}"
        kb.button(text=text, callback_data=f"model:set:{model}")
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb


def _build_prompts_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Показать инструкции", callback_data="prompt:show")
    kb.button(text="🏷️ Категория (что это?)", callback_data="prompt:router")
    kb.button(text="🧾 Заполнение таблицы", callback_data="prompt:extract")
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb


def _build_summaries_menu(settings) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="📤 Сводка за сегодня", callback_data="summary:send_daily")
    kb.button(text="📤 Сводка за неделю", callback_data="summary:send_weekly")
    kb.button(
        text=f"🗓️ Ежедневные {'✅' if settings.daily_enabled else '❌'}",
        callback_data="summary:toggle_daily",
    )
    kb.button(
        text=f"📅 Еженедельные {'✅' if settings.weekly_enabled else '❌'}",
        callback_data="summary:toggle_weekly",
    )
    
    weekday_map = {
        "mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
        "fri": "Пт", "sat": "Сб", "sun": "Вс"
    }
    day_label = weekday_map.get(settings.weekly_day.lower(), settings.weekly_day)
    
    kb.button(text=f"⏰ Время дня: {settings.daily_time}", callback_data="summary:daily_time")
    kb.button(text=f"📌 День недели: {day_label}", callback_data="summary:weekly_day")
    kb.button(text=f"⏱️ Время недели: {settings.weekly_time}", callback_data="summary:weekly_time")
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb


def _build_cancel_menu(callback_data: str, text: str = "⬅️ Отмена") -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text=text, callback_data=callback_data)
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


def _is_cancel(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in {"отмена", "cancel", "назад", "back"}


async def _cancel_flow(message: Message, state: FSMContext, settings_service: BotSettingsService) -> None:
    await state.clear()
    settings = await settings_service.load()
    kb = _build_main_menu(settings)
    await message.answer("Ок, отменил. Возвращаюсь в главное меню.", reply_markup=kb.as_markup())


async def _show_menu(callback: CallbackQuery, text: str, kb: InlineKeyboardBuilder) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await callback.message.answer(text, reply_markup=kb.as_markup())
