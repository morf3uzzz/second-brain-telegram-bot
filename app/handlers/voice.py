import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from gspread.exceptions import WorksheetNotFound

from app.prompts import (
    DEFAULT_EXTRACT_USER,
    DEFAULT_MULTI_SYSTEM,
    DEFAULT_MULTI_USER,
    DEFAULT_ROUTER_USER,
    DEFAULT_THINKING_SYSTEM,
    DEFAULT_THINKING_USER,
    EXTRACT_PROMPT_KEY,
    ROUTER_PROMPT_KEY,
)
from app.handlers.delete import DeleteState, build_delete_keyboard, format_delete_list
from app.services.bot_settings_service import BotSettingsService
from app.services.delete_service import DeleteService
from app.services.intent_service import IntentService
from app.services.openai_service import OpenAIService
from app.services.qa_service import QAService
from app.services.router_service import RouterService
from app.services.sheets_service import SheetsService
from app.utils.auth import is_allowed, user_label

logger = logging.getLogger(__name__)

MAX_VOICE_SECONDS = 12 * 60
THINKING_MODE_SECONDS = 2 * 60
LONG_TRANSCRIPT_CHARS = 2500
MAX_TRANSCRIBE_TIMEOUT = 900
MAX_TG_CHARS = 3500


class IntakeState(StatesGroup):
    waiting_required = State()


class DuplicateState(StatesGroup):
    confirming = State()


class CategoryState(StatesGroup):
    selecting = State()


class ThinkingState(StatesGroup):
    waiting_choice = State()


def create_voice_router(
    openai_service: OpenAIService,
    sheets_service: SheetsService,
    router_service: RouterService,
    intent_service: IntentService,
    settings_service: BotSettingsService,
    qa_service: QAService,
    delete_service: DeleteService,
    allowed_user_ids: list[int],
    allowed_usernames: list[str],
) -> Router:
    router = Router()

    @router.message(F.voice)
    async def handle_voice(message: Message, bot: Bot, state: FSMContext) -> None:
        if not is_allowed(message.from_user, allowed_user_ids, allowed_usernames):
            logger.warning("Unauthorized user: %s", user_label(message.from_user))
            await message.answer("⛔️ Доступ запрещен.")
            return

        logger.info("Получил аудио")
        status_msg = await message.answer("⏳ Обрабатываю сообщение, это может занять до минуты.")
        temp_path: Optional[str] = None
        transcript = ""
        category = ""
        today_str = datetime.now().strftime("%d.%m.%Y")
        today_date = datetime.now().date()

        try:
            if message.voice.duration > MAX_VOICE_SECONDS:
                await status_msg.edit_text(
                    "⚠️ Сообщение слишком длинное. "
                    "Максимум — 12 минут. "
                    "Разбейте на несколько голосовых."
                )
                return

            if message.voice.duration > THINKING_MODE_SECONDS:
                minutes = max(1, round(message.voice.duration / 60))
                await status_msg.edit_text(
                    f"⏳ Длинное сообщение ({minutes} мин). "
                    "Это может занять несколько минут."
                )

            logger.info("Скачиваю аудио, длительность=%ss", message.voice.duration)
            step_start = asyncio.get_running_loop().time()
            temp_path = await asyncio.wait_for(_download_voice(bot, message), timeout=30)
            file_size = os.path.getsize(temp_path)
            logger.info("Аудио скачано за %.2fs, размер=%s bytes", asyncio.get_running_loop().time() - step_start, file_size)

            logger.info("Отправляю в Whisper")
            step_start = asyncio.get_running_loop().time()
            transcribe_timeout = max(180, min(MAX_TRANSCRIBE_TIMEOUT, int(message.voice.duration * 3)))
            transcript = await asyncio.wait_for(openai_service.transcribe(temp_path), timeout=transcribe_timeout)
            if not transcript:
                raise ValueError("Empty transcription")
            logger.info("Транскрипция готова за %.2fs, символов=%s", asyncio.get_running_loop().time() - step_start, len(transcript))

            logger.info("Читаю настройки бота")
            bot_settings = await settings_service.load()
            model = bot_settings.openai_model
            try:
                tz = ZoneInfo(bot_settings.timezone)
            except Exception:
                tz = ZoneInfo("UTC")
            now_tz = datetime.now(tz)
            today_str = now_tz.strftime("%d.%m.%Y")
            today_date = now_tz.date()

            if (
                message.voice.duration >= THINKING_MODE_SECONDS
                or len(transcript) >= LONG_TRANSCRIPT_CHARS
            ):
                await _handle_thinking_mode(
                    status_msg,
                    state,
                    openai_service,
                    transcript,
                    today_str,
                    model,
                )
                return

            logger.info("Определяю намерение пользователя")
            intent = await intent_service.detect(transcript, model=model)
            action = intent.get("action", "add")
            query = intent.get("query", transcript)

            if action == "ask":
                logger.info("Режим вопроса")
                await status_msg.edit_text("⏳ Ищу по базе, это может занять до минуты.")
                answer = await qa_service.answer_question(query or transcript, model=model)
                await _send_long_text(status_msg, message, answer, safe_mode=bot_settings.safe_output)
                return

            if action == "delete":
                logger.info("Режим удаления")
                candidates = await delete_service.find_candidates(query or transcript, limit=7)
                if not candidates:
                    await status_msg.edit_text("⚠️ Не нашел записей для удаления.")
                    return
                await state.set_state(DeleteState.selecting)
                await state.update_data(
                    candidates=[
                        {
                            "sheet_name": c.sheet_name,
                            "row_index": c.row_index,
                            "headers": c.headers,
                            "row_values": c.row_values,
                            "preview": c.preview,
                        }
                        for c in candidates
                    ]
                )
                kb = build_delete_keyboard(candidates)
                text = format_delete_list(candidates)
                await status_msg.edit_text(text, reply_markup=kb.as_markup())
                return

            logger.info("Читаю Settings из Google Sheets")
            settings = await sheets_service.load_settings()
            logger.info("Категорий найдено: %s", len(settings))

            logger.info("Читаю Prompts из Google Sheets")
            prompts = await sheets_service.get_prompts()
            router_prompt = prompts.get(ROUTER_PROMPT_KEY, DEFAULT_ROUTER_USER)
            extract_prompt = prompts.get(EXTRACT_PROMPT_KEY, DEFAULT_EXTRACT_USER)

            multi_items = await _split_multi_items(
                openai_service,
                transcript,
                settings,
                model,
            )
            if len(multi_items) > 1:
                await _process_multi_items(
                    status_msg,
                    message,
                    state,
                    sheets_service,
                    router_service,
                    settings,
                    extract_prompt,
                    today_str,
                    multi_items,
                    model,
                    transcript,
                )
                return

            logger.info("Классифицирую категорию (model=%s)", model)
            try:
                category, _reasoning = await asyncio.wait_for(
                    router_service.classify_category(transcript, settings, router_prompt, model=model),
                    timeout=60,
                )
            except Exception:
                logger.exception("Failed to classify category")
                categories = list(settings.keys())
                await _prompt_category_choice(
                    status_msg,
                    state,
                    categories,
                    transcript,
                    today_str,
                )
                return
            logger.info("Определил категорию: %s", category)

            logger.info("Читаю заголовки листа: %s", category)
            headers = await sheets_service.get_headers(category)
            if not headers:
                raise ValueError("No headers found in target sheet")
            logger.info("Нашел столбцы: %s", headers)

            logger.info("Извлекаю данные под заголовки (model=%s)", model)
            clean_headers = [_clean_header(header) for header in headers]
            row = await asyncio.wait_for(
                router_service.extract_row(transcript, clean_headers, today_str, extract_prompt, model=model),
                timeout=60,
            )
            row = _apply_text_fields(headers, row, transcript)
            row = _apply_date_fields(headers, row, transcript, today_date)

            missing_required = _get_missing_required(headers, row)
            if missing_required:
                await state.set_state(IntakeState.waiting_required)
                await state.update_data(
                    category=category,
                    headers=headers,
                    row=row,
                    transcript=transcript,
                    today_str=today_str,
                    missing_required_indices=[idx for idx, _name in missing_required],
                )
                if len(missing_required) == 1 and _is_priority_header(missing_required[0][1]):
                    kb = _build_priority_keyboard()
                    await status_msg.edit_text(
                        "⚠️ Нужно выбрать приоритет задачи:",
                        reply_markup=kb.as_markup(),
                    )
                    return

                missing_names = ", ".join(name for _idx, name in missing_required)
                await status_msg.edit_text(
                    "⚠️ Нужно заполнить обязательные поля:\n"
                    f"{missing_names}\n\n"
                    "Напишите ответ так:\n"
                    "Поле=значение; Поле=значение\n"
                    "Пример: Приоритет=Высокий\n\n"
                    "Чтобы пропустить — нажмите «Пропустить» или скажите «off».\n"
                    "Чтобы отменить — напишите «Отмена».",
                    reply_markup=_build_required_keyboard().as_markup(),
                )
                return

            duplicate_preview = await _find_duplicate(sheets_service, category, headers, row)
            if duplicate_preview:
                await state.set_state(DuplicateState.confirming)
                await state.update_data(
                    category=category,
                    headers=headers,
                    row=row,
                    transcript=transcript,
                    today_str=today_str,
                    duplicate_preview=duplicate_preview,
                )
                await status_msg.edit_text(
                    "⚠️ Похоже, это дубликат.\n\n"
                    f"{duplicate_preview}\n\n"
                    "Добавить новую запись?",
                    reply_markup=_build_duplicate_keyboard().as_markup(),
                )
                return

            logger.info("Записываю строку в лист: %s", category)
            await sheets_service.append_row(category, row)
            logger.info("Пишу в Inbox")
            await sheets_service.append_row("Inbox", [today_str, category, transcript])
            logger.info("Записал строку")

            short_text = transcript if len(transcript) <= 300 else transcript[:297] + "..."
            short_text = _get_summary_value(headers, row) or short_text
            await status_msg.edit_text(
                f"✅ Сохранено в '{category}'.\n"
                f"Суть: {short_text}\n"
                f"Категория: {category}"
            )
        except json.JSONDecodeError:
            logger.exception("GPT returned invalid JSON")
            await status_msg.edit_text("⚠️ GPT вернул некорректный JSON. Попробуйте еще раз.")
            await _safe_inbox(sheets_service, today_str, category or "Unknown", transcript)
        except asyncio.TimeoutError:
            logger.exception("Timeout while processing message")
            await status_msg.edit_text("⚠️ Превышено время ожидания ответа от ИИ. Попробуйте еще раз.")
            await _safe_inbox(sheets_service, today_str, category or "Unknown", transcript)
        except WorksheetNotFound:
            logger.exception("Worksheet not found")
            await status_msg.edit_text("⚠️ Не найден лист в Google Sheets. Проверьте название категории.")
            await _safe_inbox(sheets_service, today_str, category or "Unknown", transcript)
        except Exception:
            logger.exception("Unhandled error")
            await status_msg.edit_text("⚠️ Ошибка обработки сообщения. Попробуйте еще раз.")
            await _safe_inbox(sheets_service, today_str, category or "Unknown", transcript)
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    logger.warning("Failed to remove temp file: %s", temp_path)

    @router.callback_query(IntakeState.waiting_required, F.data == "req:cancel")
    async def cancel_required(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text("Ок, отменил.")
        await callback.answer()

    @router.callback_query(IntakeState.waiting_required, F.data == "req:skip")
    async def skip_required(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return

        data = await state.get_data()
        pending_info = _get_pending_item(data)
        if pending_info:
            pending, _idx, item = pending_info
            results = data.get("multi_results", [])
            missing_indices = data.get("missing_required_indices", [])
            row = item.get("row", [])
            if missing_indices:
                for idx in missing_indices:
                    if isinstance(idx, int) and idx < len(row):
                        row[idx] = ""
                item["row"] = row
                pending[_idx] = item
                await state.update_data(pending_items=pending)
            await _finalize_multi_item(callback.message, callback.message, state, sheets_service, item, results)
            await callback.answer()
            return

        category = data.get("category", "")
        headers = data.get("headers", [])
        row = data.get("row", [])
        transcript = data.get("transcript", "")
        today_str = data.get("today_str", datetime.now().strftime("%d.%m.%Y"))
        missing_indices = data.get("missing_required_indices", [])
        today_date = _parse_date_value(today_str) or datetime.now().date()
        today_date = _parse_date_value(today_str) or datetime.now().date()
        today_date = _parse_date_value(today_str) or datetime.now().date()
        today_date = _parse_date_value(today_str) or datetime.now().date()

        if not category or not headers or not row:
            await state.clear()
            await callback.message.edit_text("⚠️ Не удалось восстановить контекст. Повторите запись.")
            await callback.answer()
            return

        if missing_indices:
            for idx in missing_indices:
                if isinstance(idx, int) and idx < len(row):
                    row[idx] = ""
            await state.update_data(row=row)

        duplicate_preview = await _find_duplicate(sheets_service, category, headers, row)
        if duplicate_preview:
            await state.set_state(DuplicateState.confirming)
            await state.update_data(
                category=category,
                headers=headers,
                row=row,
                transcript=transcript,
                today_str=today_str,
                duplicate_preview=duplicate_preview,
            )
            await callback.message.edit_text(
                "⚠️ Похоже, это дубликат.\n\n"
                f"{duplicate_preview}\n\n"
                "Добавить новую запись?",
                reply_markup=_build_duplicate_keyboard().as_markup(),
            )
            await callback.answer()
            return

        row = _apply_text_fields(headers, row, transcript)
        row = _apply_date_fields(headers, row, transcript, today_date)
        await sheets_service.append_row(category, row)
        await sheets_service.append_row("Inbox", [today_str, category, transcript])
        await state.clear()
        short_text = transcript if len(transcript) <= 300 else transcript[:297] + "..."
        short_text = _get_summary_value(headers, row) or short_text
        await callback.message.edit_text(
            f"✅ Сохранено в '{category}' без обязательных полей.\n"
            f"Суть: {short_text}\n"
            f"Категория: {category}"
        )
        await callback.answer()

    @router.callback_query(IntakeState.waiting_required, F.data.startswith("req:priority:"))
    async def handle_required_priority(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        value_map = {
            "low": "Низкий",
            "medium": "Средний",
            "high": "Высокий",
        }
        code = callback.data.split(":")[-1]
        value = value_map.get(code)
        if not value:
            await callback.answer("Неизвестный приоритет", show_alert=True)
            return

        data = await state.get_data()
        pending_info = _get_pending_item(data)
        if pending_info:
            pending, idx, item = pending_info
            headers = item.get("headers", [])
            row = item.get("row", [])
            transcript = item.get("transcript", "")
            today_str = item.get("today_str", datetime.now().strftime("%d.%m.%Y"))
            today_date = _parse_date_value(today_str) or datetime.now().date()
            if len(row) < len(headers):
                row.extend([""] * (len(headers) - len(row)))

            item_idx = None
            for i, header in enumerate(headers):
                if _is_priority_header(_display_header(header)):
                    item_idx = i
                    break
            if item_idx is None:
                await callback.message.edit_text("⚠️ Поле приоритета не найдено.")
                await callback.answer()
                return
            if item_idx < len(row):
                row[item_idx] = value

            item["row"] = row
            pending[idx] = item
            await state.update_data(pending_items=pending)

            missing_after = _get_missing_required(headers, row)
            if missing_after:
                await _prompt_required_item(callback.message, state)
                await callback.answer()
                return

            results = data.get("multi_results", [])
            await _finalize_multi_item(callback.message, callback.message, state, sheets_service, item, results)
            await callback.answer()
            return
        category = data.get("category", "")
        headers = data.get("headers", [])
        row = data.get("row", [])
        transcript = data.get("transcript", "")
        today_str = data.get("today_str", datetime.now().strftime("%d.%m.%Y"))
        today_date = _parse_date_value(today_str) or datetime.now().date()
        if len(row) < len(headers):
            row.extend([""] * (len(headers) - len(row)))

        if not category or not headers or not row:
            await state.clear()
            await callback.message.edit_text("⚠️ Не удалось восстановить контекст. Повторите запись.")
            await callback.answer()
            return

        idx = None
        for i, header in enumerate(headers):
            if _is_priority_header(_display_header(header)):
                idx = i
                break
        if idx is None:
            await callback.message.edit_text("⚠️ Поле приоритета не найдено.")
            await callback.answer()
            return

        if idx < len(row):
            row[idx] = value

        missing_after = _get_missing_required(headers, row)
        if missing_after:
            missing_names = ", ".join(name for _idx, name in missing_after)
            await state.update_data(row=row)
            await callback.message.edit_text(
                "⚠️ Нужно заполнить обязательные поля:\n"
                f"{missing_names}\n\n"
                "Напишите ответ так:\n"
                "Поле=значение; Поле=значение\n"
                "Пример: Приоритет=Высокий\n"
                "Можно нажать «Пропустить».",
                reply_markup=_build_required_keyboard().as_markup(),
            )
            await callback.answer()
            return

        duplicate_preview = await _find_duplicate(sheets_service, category, headers, row)
        if duplicate_preview:
            await state.set_state(DuplicateState.confirming)
            await state.update_data(
                category=category,
                headers=headers,
                row=row,
                transcript=transcript,
                today_str=today_str,
                duplicate_preview=duplicate_preview,
            )
            await callback.message.edit_text(
                "⚠️ Похоже, это дубликат.\n\n"
                f"{duplicate_preview}\n\n"
                "Добавить новую запись?",
                reply_markup=_build_duplicate_keyboard().as_markup(),
            )
            await callback.answer()
            return

        row = _apply_text_fields(headers, row, transcript)
        row = _apply_date_fields(headers, row, transcript, today_date)
        await sheets_service.append_row(category, row)
        await sheets_service.append_row("Inbox", [today_str, category, transcript])
        await state.clear()
        short_text = transcript if len(transcript) <= 300 else transcript[:297] + "..."
        short_text = _get_summary_value(headers, row) or short_text
        await callback.message.edit_text(
            f"✅ Сохранено в '{category}'.\n"
            f"Суть: {short_text}\n"
            f"Категория: {category}"
        )
        await callback.answer()

    @router.callback_query(DuplicateState.confirming, F.data == "dup:add")
    async def confirm_duplicate_add(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        data = await state.get_data()
        pending_info = _get_pending_item(data)
        if pending_info:
            _pending, _idx, item = pending_info
            category = item.get("category", "")
            headers = item.get("headers", [])
            row = item.get("row", [])
            transcript = item.get("transcript", "")
            today_str = item.get("today_str", datetime.now().strftime("%d.%m.%Y"))
        else:
            category = data.get("category", "")
            headers = data.get("headers", [])
            row = data.get("row", [])
            transcript = data.get("transcript", "")
            today_str = data.get("today_str", datetime.now().strftime("%d.%m.%Y"))
        today_date = _parse_date_value(today_str) or datetime.now().date()

        if not category or not headers or not row:
            await state.clear()
            await callback.message.edit_text("⚠️ Не удалось восстановить контекст. Повторите запись.")
            await callback.answer()
            return

        row = _apply_text_fields(headers, row, transcript)
        row = _apply_date_fields(headers, row, transcript, today_date)
        await sheets_service.append_row(category, row)
        await sheets_service.append_row("Inbox", [today_str, category, transcript])
        await state.clear()
        short_text = transcript if len(transcript) <= 300 else transcript[:297] + "..."
        short_text = _get_summary_value(headers, row) or short_text
        await callback.message.edit_text(
            f"✅ Добавлено как новая запись в '{category}'.\n"
            f"Суть: {short_text}\n"
            f"Категория: {category}"
        )
        await callback.answer()

    @router.callback_query(DuplicateState.confirming, F.data == "dup:skip")
    async def confirm_duplicate_skip(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text("Ок, не добавляю дубликат.")
        await callback.answer()

    @router.callback_query(CategoryState.selecting, F.data == "cat:cancel")
    async def cancel_category_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text("Ок, отменил.")
        await callback.answer()

    @router.callback_query(CategoryState.selecting, F.data.startswith("cat:pick:"))
    async def pick_category(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return

        data = await state.get_data()
        categories = data.get("categories", [])
        transcript = data.get("transcript", "")
        today_str = data.get("today_str", datetime.now().strftime("%d.%m.%Y"))

        try:
            index = int(callback.data.split(":")[-1])
        except ValueError:
            await callback.answer("Ошибка выбора", show_alert=True)
            return

        if index < 0 or index >= len(categories):
            await callback.answer("Неверный выбор", show_alert=True)
            return

        category = categories[index]

        try:
            headers = await sheets_service.get_headers(category)
            if not headers:
                await callback.message.edit_text("⚠️ Не найден список столбцов для категории.")
                await state.clear()
                await callback.answer()
                return

            prompts = await sheets_service.get_prompts()
            extract_prompt = prompts.get(EXTRACT_PROMPT_KEY, DEFAULT_EXTRACT_USER)

            bot_settings = await settings_service.load()
            model = bot_settings.openai_model

            clean_headers = [_clean_header(header) for header in headers]
            row = await asyncio.wait_for(
                router_service.extract_row(transcript, clean_headers, today_str, extract_prompt, model=model),
                timeout=60,
            )
            row = _apply_text_fields(headers, row, transcript)
            row = _apply_date_fields(headers, row, transcript, _parse_date_value(today_str) or datetime.now().date())
            row = _apply_date_fields(headers, row, transcript, today_date)

            missing_required = _get_missing_required(headers, row)
            if missing_required:
                await state.set_state(IntakeState.waiting_required)
                await state.update_data(
                    category=category,
                    headers=headers,
                    row=row,
                    transcript=transcript,
                    today_str=today_str,
                    missing_required_indices=[idx for idx, _name in missing_required],
                )
                if len(missing_required) == 1 and _is_priority_header(missing_required[0][1]):
                    kb = _build_priority_keyboard()
                    await callback.message.edit_text(
                        "⚠️ Нужно выбрать приоритет задачи:",
                        reply_markup=kb.as_markup(),
                    )
                    await callback.answer()
                    return

                missing_names = ", ".join(name for _idx, name in missing_required)
                await callback.message.edit_text(
                    "⚠️ Нужно заполнить обязательные поля:\n"
                    f"{missing_names}\n\n"
                    "Напишите ответ так:\n"
                    "Поле=значение; Поле=значение\n"
                    "Пример: Приоритет=Высокий\n\n"
                    "Чтобы пропустить — нажмите «Пропустить».\n"
                    "Чтобы отменить — напишите «Отмена».",
                    reply_markup=_build_required_keyboard().as_markup(),
                )
                await callback.answer()
                return

            duplicate_preview = await _find_duplicate(sheets_service, category, headers, row)
            if duplicate_preview:
                await state.set_state(DuplicateState.confirming)
                await state.update_data(
                    category=category,
                    headers=headers,
                    row=row,
                    transcript=transcript,
                    today_str=today_str,
                    duplicate_preview=duplicate_preview,
                )
                await callback.message.edit_text(
                    "⚠️ Похоже, это дубликат.\n\n"
                    f"{duplicate_preview}\n\n"
                    "Добавить новую запись?",
                    reply_markup=_build_duplicate_keyboard().as_markup(),
                )
                await callback.answer()
                return

            await sheets_service.append_row(category, row)
            await sheets_service.append_row("Inbox", [today_str, category, transcript])
            await state.clear()
            short_text = transcript if len(transcript) <= 300 else transcript[:297] + "..."
            short_text = _get_summary_value(headers, row) or short_text
            await callback.message.edit_text(
                f"✅ Сохранено в '{category}'.\n"
                f"Суть: {short_text}\n"
                f"Категория: {category}"
            )
            await callback.answer()
        except Exception:
            logger.exception("Failed to process category selection")
            await callback.message.edit_text("⚠️ Не удалось обработать выбор категории. Попробуйте ещё раз.")
            await state.clear()
            await callback.answer()

    @router.message(IntakeState.waiting_required, F.text)
    async def handle_required_fields(message: Message, state: FSMContext) -> None:
        if not is_allowed(message.from_user, allowed_user_ids, allowed_usernames):
            await message.answer("⛔️ Доступ запрещен.")
            return

        text = message.text.strip()
        if text.lower() in {"отмена", "cancel", "стоп"}:
            await state.clear()
            await message.answer("Ок, отменил.")
            return

        data = await state.get_data()
        category = data.get("category", "")
        headers = data.get("headers", [])
        row = data.get("row", [])
        transcript = data.get("transcript", "")
        today_str = data.get("today_str", datetime.now().strftime("%d.%m.%Y"))

        if not category or not headers or not row:
            await state.clear()
            await message.answer("⚠️ Не удалось восстановить контекст. Повторите запись.")
            return

        if text.lower() in {"off", "пропустить", "skip"}:
            row = _apply_text_fields(headers, row, transcript)
            row = _apply_date_fields(headers, row, transcript, today_date)
            await sheets_service.append_row(category, row)
            await sheets_service.append_row("Inbox", [today_str, category, transcript])
            await state.clear()
            short_text = transcript if len(transcript) <= 300 else transcript[:297] + "..."
            short_text = _get_summary_value(headers, row) or short_text
            await message.answer(
                f"✅ Сохранено в '{category}' без обязательных полей.\n"
                f"Суть: {short_text}\n"
                f"Категория: {category}"
            )
            return

        required = _get_missing_required(headers, row)
        required_map = {name.lower(): idx for idx, name in required}

        updates = _parse_key_values(text, required_map)
        if not updates and len(required) == 1:
            idx, _name = required[0]
            row[idx] = text
        else:
            for idx, value in updates.items():
                if idx < len(row):
                    row[idx] = value

        missing_after = _get_missing_required(headers, row)
        if missing_after:
            missing_names = ", ".join(name for _idx, name in missing_after)
            await message.answer(
                "⚠️ Нужно заполнить обязательные поля:\n"
                f"{missing_names}\n\n"
                "Напишите ответ так:\n"
                "Поле=значение; Поле=значение\n"
                "Пример: Приоритет=Высокий\n"
                "Можно написать «off» или «Пропустить»."
            )
            await state.update_data(row=row)
            return

        duplicate_preview = await _find_duplicate(sheets_service, category, headers, row)
        if duplicate_preview:
            await state.set_state(DuplicateState.confirming)
            await state.update_data(
                category=category,
                headers=headers,
                row=row,
                transcript=transcript,
                today_str=today_str,
                duplicate_preview=duplicate_preview,
            )
            await message.answer(
                "⚠️ Похоже, это дубликат.\n\n"
                f"{duplicate_preview}\n\n"
                "Добавить новую запись?",
                reply_markup=_build_duplicate_keyboard().as_markup(),
            )
            return

        row = _apply_text_fields(headers, row, transcript)
        row = _apply_date_fields(headers, row, transcript, today_date)
        await sheets_service.append_row(category, row)
        await sheets_service.append_row("Inbox", [today_str, category, transcript])
        await state.clear()

        short_text = transcript if len(transcript) <= 300 else transcript[:297] + "..."
        short_text = _get_summary_value(headers, row) or short_text
        await message.answer(
            f"✅ Сохранено в '{category}'.\n"
            f"Суть: {short_text}\n"
            f"Категория: {category}"
        )

    @router.callback_query(ThinkingState.waiting_choice, F.data.startswith("thinking:"))
    async def handle_thinking_choice(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await callback.answer()
        data = await state.get_data()
        structured = data.get("thinking_structured") or {}
        transcript = data.get("thinking_transcript") or ""
        today_str = data.get("thinking_today_str") or datetime.now().strftime("%d.%m.%Y")

        action = callback.data.split(":", 1)[1]
        if action == "cancel":
            await state.clear()
            await callback.message.edit_text("Ок, ничего не сохраняю.")
            return
        if action != "save":
            await callback.message.edit_text("⚠️ Неизвестное действие.")
            return

        save_text = _build_thinking_inbox_text(structured, transcript)
        try:
            await sheets_service.append_row("Прочее", [today_str, "Thinking", save_text])
            await state.clear()
            await callback.message.edit_text("✅ Сохранено в «Прочее».")
        except WorksheetNotFound:
            await sheets_service.append_row("Inbox", [today_str, "Thinking", save_text])
            await state.clear()
            await callback.message.edit_text("⚠️ Лист «Прочее» не найден. Сохранил в Inbox.")

    return router


async def _download_voice(bot: Bot, message: Message) -> str:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
    temp_path = temp_file.name
    temp_file.close()

    file = await bot.get_file(message.voice.file_id)
    await bot.download_file(file.file_path, destination=temp_path)
    return temp_path


async def _safe_inbox(
    sheets_service: SheetsService,
    today_str: str,
    category: str,
    transcript: str,
) -> None:
    if not transcript:
        return
    try:
        await sheets_service.append_row("Inbox", [today_str, category, transcript])
    except Exception:
        logger.exception("Failed to write to Inbox")


def _get_missing_required(headers: list[str], row: list[str]) -> list[tuple[int, str]]:
    missing = []
    for idx, header in enumerate(headers):
        if header.strip().endswith("*"):
            value = row[idx] if idx < len(row) else ""
            if not str(value).strip():
                missing.append((idx, _display_header(header)))
    return missing


def _display_header(header: str) -> str:
    return header.replace("*", "").strip()


def _clean_header(header: str) -> str:
    return header.replace("*", "").strip()


def _parse_key_values(text: str, header_map: dict[str, int]) -> dict[int, str]:
    result: dict[int, str] = {}
    parts = [part.strip() for part in text.split(";") if part.strip()]
    lines = []
    for part in parts:
        lines.extend([line.strip() for line in part.split("\n") if line.strip()])

    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
        elif "=" in line:
            key, value = line.split("=", 1)
        elif " - " in line:
            key, value = line.split(" - ", 1)
        else:
            continue
        key_norm = _display_header(key).lower()
        if key_norm in header_map:
            result[header_map[key_norm]] = value.strip()

    return result


def _apply_text_fields(headers: list[str], row: list[str], transcript: str) -> list[str]:
    raw_idx = _find_header_index(headers, {"сырой текст", "raw text", "original text", "исходный текст"})
    summary_idx = _find_header_index(headers, {"суть", "описание", "summary"})

    if raw_idx is not None and raw_idx < len(row):
        row[raw_idx] = transcript

    if summary_idx is not None and summary_idx < len(row):
        summary_value = str(row[summary_idx]).strip()
        raw_value = transcript.strip()
        raw_col_value = ""
        if raw_idx is not None and raw_idx < len(row):
            raw_col_value = str(row[raw_idx]).strip()

        if (
            not summary_value
            or _normalize_text(summary_value) == _normalize_text(raw_value)
            or _normalize_text(summary_value) == _normalize_text(raw_col_value)
        ):
            row[summary_idx] = _make_summary(transcript)

    return row


def _apply_date_fields(headers: list[str], row: list[str], transcript: str, today: date) -> list[str]:
    date_explicit = _extract_explicit_date(transcript)
    date_relative = _extract_relative_date(transcript, today)
    target_date = date_explicit or date_relative

    today_str = today.strftime("%d.%m.%Y")
    for idx, header in enumerate(headers):
        key = _display_header(header).lower().strip()
        if key == "дата добавления":
            if idx < len(row):
                row[idx] = today_str

    if target_date:
        target_str = target_date.strftime("%d.%m.%Y")
        for idx, header in enumerate(headers):
            key = _display_header(header).lower().strip()
            if key in {"дата выполнения", "дата", "date", "due date"}:
                if idx < len(row):
                    row[idx] = target_str
    return row


def _extract_explicit_date(text: str) -> date | None:
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    if match:
        return _parse_date_value(match.group(1))
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return _parse_date_value(match.group(1))
    return None


def _extract_relative_date(text: str, today: date) -> date | None:
    lowered = text.lower()
    if "послезавтра" in lowered:
        return today + timedelta(days=2)
    if "завтра" in lowered:
        return today + timedelta(days=1)
    if "сегодня" in lowered:
        return today

    weekday = _find_weekday(lowered)
    if weekday is None:
        return None

    if "следующ" in lowered:
        days_to_next_monday = (7 - today.weekday()) or 7
        next_monday = today + timedelta(days=days_to_next_monday)
        return next_monday + timedelta(days=weekday)

    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta)


def _find_weekday(text: str) -> int | None:
    mapping = {
        "понед": 0,
        "втор": 1,
        "сред": 2,
        "четвер": 3,
        "четверг": 3,
        "пятниц": 4,
        "суббот": 5,
        "воскрес": 6,
    }
    for key, idx in mapping.items():
        if key in text:
            return idx
    return None


def _parse_date_value(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _get_summary_value(headers: list[str], row: list[str]) -> str:
    idx = _find_header_index(headers, {"суть", "описание", "summary"})
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


async def _find_duplicate(
    sheets_service: SheetsService,
    category: str,
    headers: list[str],
    row: list[str],
    limit: int = 50,
) -> str | None:
    try:
        rows = await sheets_service.get_all_values(category)
    except Exception:
        logger.exception("Failed to read sheet for duplicate check: %s", category)
        return None

    if not rows or len(rows) < 2:
        return None

    header_row = rows[0]
    summary_new = _get_value_by_headers(headers, row, {"суть", "описание", "summary"})
    raw_new = _get_value_by_headers(headers, row, {"сырой текст", "raw text", "original text", "исходный текст"})
    date_new = _get_value_by_headers(headers, row, {"дата", "дата добавления", "дата выполнения", "date"})

    recent_rows = rows[1:][-limit:]
    for old in reversed(recent_rows):
        summary_old = _get_value_by_headers(header_row, old, {"суть", "описание", "summary"})
        raw_old = _get_value_by_headers(header_row, old, {"сырой текст", "raw text", "original text", "исходный текст"})
        date_old = _get_value_by_headers(header_row, old, {"дата", "дата добавления", "дата выполнения", "date"})

        if _is_duplicate(summary_new, raw_new, date_new, summary_old, raw_old, date_old):
            return _format_duplicate_preview(header_row, old)

    return None


def _is_duplicate(
    summary_new: str,
    raw_new: str,
    date_new: str,
    summary_old: str,
    raw_old: str,
    date_old: str,
) -> bool:
    if summary_new and summary_old and _normalize_text(summary_new) == _normalize_text(summary_old):
        return _same_or_empty(date_new, date_old)
    if raw_new and raw_old and _normalize_text(raw_new) == _normalize_text(raw_old):
        return _same_or_empty(date_new, date_old)
    return False


def _same_or_empty(left: str, right: str) -> bool:
    if not left or not right:
        return True
    return _normalize_text(left) == _normalize_text(right)


def _get_value_by_headers(headers: list[str], row: list[str], names: set[str]) -> str:
    idx = _find_header_index(headers, names)
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def _format_duplicate_preview(headers: list[str], row: list[str]) -> str:
    date_value = _get_value_by_headers(headers, row, {"дата", "дата добавления", "дата выполнения", "date"})
    summary_value = _get_value_by_headers(headers, row, {"суть", "описание", "summary", "на что потрачено"})
    raw_value = _get_value_by_headers(headers, row, {"сырой текст", "raw text", "original text", "исходный текст"})

    lines = []
    if date_value:
        lines.append(f"📅 Дата: {_shorten(date_value)}")
    if summary_value:
        lines.append(f"📝 Суть: {_shorten(summary_value)}")
    if raw_value and _normalize_text(raw_value) != _normalize_text(summary_value):
        lines.append(f"🗣️ Сырой текст: {_shorten(raw_value, 120)}")
    return "\n".join(lines) if lines else "Похожая запись найдена."


def _shorten(value: str, limit: int = 80) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _find_header_index(headers: list[str], names: set[str]) -> int | None:
    for idx, header in enumerate(headers):
        header_norm = _display_header(header).lower().strip()
        if header_norm in names:
            return idx
    return None


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _make_summary(text: str) -> str:
    summary = text.strip()
    if not summary:
        return summary

    prefixes = [
        "слушай",
        "а слушай",
        "мне надо",
        "мне нужно",
        "нужно",
        "я хочу",
        "хочу",
        "можешь",
        "можешь пожалуйста",
        "пожалуйста",
        "надо",
    ]
    changed = True
    while changed:
        changed = False
        lowered = summary.lower().lstrip()
        for pref in prefixes:
            if lowered.startswith(pref):
                summary = summary[len(pref):].lstrip(" ,.-")
                changed = True
                break

    suffixes = [
        "можешь поставить задачку",
        "можешь поставить задачу",
        "поставь задачку",
        "поставь задачу",
        "добавь в задачи",
        "добавь задачу",
        "запомни это",
        "пожалуйста",
    ]
    lowered = summary.lower()
    for suf in suffixes:
        if lowered.endswith(suf):
            summary = summary[: -len(suf)].rstrip(" ,.-")
            break

    summary = " ".join(summary.split())
    if len(summary) > 160:
        summary = summary[:157].rstrip() + "..."
    return summary or text


def _is_priority_header(header: str) -> bool:
    return "приоритет" in header.strip().lower()


def _build_priority_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Низкий", callback_data="req:priority:low")
    kb.button(text="Средний", callback_data="req:priority:medium")
    kb.button(text="Высокий", callback_data="req:priority:high")
    kb.button(text="Пропустить", callback_data="req:skip")
    kb.button(text="Отмена", callback_data="req:cancel")
    kb.adjust(3, 1, 1)
    return kb


def _build_required_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить", callback_data="req:skip")
    kb.button(text="Отмена", callback_data="req:cancel")
    kb.adjust(2)
    return kb


def _build_duplicate_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Добавить", callback_data="dup:add")
    kb.button(text="❌ Не добавлять", callback_data="dup:skip")
    kb.adjust(2)
    return kb


async def _prompt_category_choice(
    status_msg: Message,
    state: FSMContext,
    categories: list[str],
    transcript: str,
    today_str: str,
) -> None:
    if not categories:
        await status_msg.edit_text("⚠️ Категории не найдены. Проверьте лист Settings.")
        return
    await state.set_state(CategoryState.selecting)
    await state.update_data(
        categories=categories,
        transcript=transcript,
        today_str=today_str,
    )
    kb = _build_category_keyboard(categories)
    await status_msg.edit_text(
        "⚠️ Не смог определить категорию.\n"
        "Выберите нужную:",
        reply_markup=kb.as_markup(),
    )


def _build_category_keyboard(categories: list[str]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for idx, name in enumerate(categories):
        kb.button(text=name, callback_data=f"cat:pick:{idx}")
    kb.button(text="Отмена", callback_data="cat:cancel")
    kb.adjust(2)
    return kb


def _safe_format(template: str, mapping: dict[str, str]) -> str:
    tokens: dict[str, str] = {}
    result = template
    for key, value in mapping.items():
        token = f"__PLACEHOLDER_{key.upper()}__"
        tokens[token] = value
        result = result.replace("{" + key + "}", token)
    result = result.replace("{", "{{").replace("}", "}}")
    for token, value in tokens.items():
        result = result.replace(token, value)
    return result


def _ensure_expected_categories(
    transcript: str,
    settings: dict[str, str],
    items: list[dict[str, str]],
) -> list[dict[str, str]]:
    lowered = transcript.lower()
    result = list(items)

    def has_category(cat_name: str) -> bool:
        return any(item.get("category") == cat_name for item in result)

    task_category = _find_category_by_keywords(settings, ["задач", "task", "todo", "to-do"])
    idea_category = _find_category_by_keywords(settings, ["иде", "idea"])
    expense_category = _find_category_by_keywords(settings, ["трат", "расход", "expense", "spend"])

    if task_category and _contains_keywords(
        lowered,
        ["надо", "нужно", "задач", "сделать", "поставить", "видео", "созвон", "позвон", "встрет"],
    ) and not has_category(task_category):
        text = _extract_sentence(
            transcript,
            ["надо", "нужно", "задач", "сделать", "поставить", "видео", "созвон", "позвон", "встрет"],
        )
        result.append({"category": task_category, "text": text, "source": "heuristic"})

    if idea_category and _contains_keywords(
        lowered,
        ["иде", "идея", "idea", "мечта", "план", "создать"],
    ) and not has_category(idea_category):
        text = _extract_sentence(transcript, ["иде", "идея", "idea", "мечта", "план", "создать"])
        result.append({"category": idea_category, "text": text, "source": "heuristic"})

    if expense_category and _contains_keywords(
        lowered,
        ["потрат", "заплатил", "купил", "расход", "expense", "spend", "руб", "рубл", "доллар", "магазин"],
    ) and not has_category(expense_category):
        text = _extract_sentence(
            transcript,
            ["потрат", "заплатил", "купил", "расход", "expense", "spend", "руб", "рубл", "доллар", "магазин"],
        )
        result.append({"category": expense_category, "text": text, "source": "heuristic"})

    return result


def _find_category_by_keywords(settings: dict[str, str], keywords: list[str]) -> str | None:
    for name in settings.keys():
        lowered = name.strip().lower()
        if any(keyword in lowered for keyword in keywords):
            return name
    return None


def _contains_keywords(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _extract_sentence(text: str, keywords: list[str]) -> str:
    parts = re.split(r"[.!?]\s+|\n", text)
    for part in parts:
        lowered = part.lower()
        if any(keyword in lowered for keyword in keywords):
            return part.strip()
    return text.strip()


def _explicit_category_signals(text: str) -> set[str]:
    lowered = text.lower()
    signals: set[str] = set()
    if _contains_keywords(lowered, ["задач", "задача", "созвон", "позвон", "нужно", "надо"]):
        signals.add("task")
    if _contains_keywords(lowered, ["иде", "идея", "мечта", "план", "создать"]):
        signals.add("idea")
    if _contains_keywords(lowered, ["потрат", "заплатил", "купил", "расход", "руб", "доллар", "магазин"]):
        signals.add("expense")
    return signals


def _coerce_list(value: object) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
        return [item for item in items if item]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _format_thinking_blocks(structured: dict) -> str:
    summary = str(structured.get("summary", "")).strip()
    ideas = _coerce_list(structured.get("ideas"))
    tasks = _coerce_list(structured.get("tasks"))
    expenses = _coerce_list(structured.get("expenses"))
    other = _coerce_list(structured.get("other"))

    parts: list[str] = []
    if summary:
        parts.append(f"Коротко: {summary}")

    sections = [
        ("Идеи", "💡", ideas),
        ("Потенциальные задачи", "✅", tasks),
        ("Траты", "💸", expenses),
        ("Прочее", "🗂️", other),
    ]
    for title, emoji, items in sections:
        if not items:
            continue
        parts.append(f"\n{emoji} {title}")
        parts.extend([f"• {item}" for item in items])

    return "\n".join(parts).strip()


def _build_thinking_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Сохранить в «Прочее»", callback_data="thinking:save")
    kb.button(text="❌ Не сохранять", callback_data="thinking:cancel")
    kb.adjust(1)
    return kb


def _build_thinking_inbox_text(structured: dict, transcript: str) -> str:
    blocks = _format_thinking_blocks(structured)
    if blocks:
        return f"{blocks}\n\nСырой текст:\n{transcript}".strip()
    return transcript.strip()


async def _handle_thinking_mode(
    status_msg: Message,
    state: FSMContext,
    openai_service: OpenAIService,
    transcript: str,
    today_str: str,
    model: str,
) -> None:
    await status_msg.edit_text("🧠 Длинное сообщение. Привожу мысли в порядок...")
    user_prompt = _safe_format(DEFAULT_THINKING_USER, {"text": transcript})
    try:
        structured = await openai_service.chat_json(
            system_prompt=DEFAULT_THINKING_SYSTEM,
            user_prompt=user_prompt,
            model=model,
        )
    except Exception:
        logger.exception("Failed to structure long message")
        structured = {
            "summary": _make_summary(transcript),
            "ideas": [],
            "tasks": [],
            "expenses": [],
            "other": [transcript],
        }

    text = _format_thinking_blocks(structured) or transcript
    prompt = (
        f"{text}\n\n"
        "Сохранить результат в «Прочее»?"
    )
    await state.set_state(ThinkingState.waiting_choice)
    await state.update_data(
        thinking_structured=structured,
        thinking_transcript=transcript,
        thinking_today_str=today_str,
    )
    await status_msg.edit_text(prompt, reply_markup=_build_thinking_keyboard().as_markup())


def _rule_based_items_from_transcript(
    transcript: str,
    settings: dict[str, str],
) -> list[dict[str, str]]:
    task_category = _find_category_by_keywords(settings, ["задач", "task", "todo", "to-do"])
    idea_category = _find_category_by_keywords(settings, ["иде", "idea"])
    expense_category = _find_category_by_keywords(settings, ["трат", "расход", "expense", "spend"])

    if not any([task_category, idea_category, expense_category]):
        return []

    sentences = _split_sentences(transcript)
    items: list[dict[str, str]] = []
    for sentence in sentences:
        parts = _split_clauses(sentence)
        for part in parts:
            subparts = _split_by_and_if_multi(part)
            for subpart in subparts:
                category = _classify_clause(
                    subpart,
                    task_category,
                    idea_category,
                    expense_category,
                )
                if not category:
                    continue
                if category == task_category:
                    subtasks = _split_task_part(subpart)
                    for sub in subtasks:
                        items.append({"category": category, "text": sub, "source": "rule"})
                else:
                    items.append({"category": category, "text": subpart.strip(), "source": "rule"})

    return items


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[.!?]\s+|\n", text)
    return [part.strip() for part in parts if part.strip()]


def _split_clauses(text: str) -> list[str]:
    markers = [
        r"\bтакже\b",
        r"\bтакже я\b",
        r"\bтак же\b",
        r"\bдополнительно\b",
        r"\bкроме того\b",
        r"\bа еще\b",
        r"\bи еще\b",
        r"\bи у меня\b",
        r"\bи я\b",
        r"\bи также\b",
        r"\bплюс\b",
    ]
    parts = re.split("|".join(markers), text, flags=re.IGNORECASE)
    return [part.strip(" ,.-") for part in parts if part.strip()]


def _split_by_and_if_multi(text: str) -> list[str]:
    signals = _explicit_category_signals(text)
    if len(signals) < 2:
        return [text]
    parts = re.split(r"\s+и\s+", text, flags=re.IGNORECASE)
    return [part.strip(" ,.-") for part in parts if part.strip()]


def _classify_clause(
    text: str,
    task_category: str | None,
    idea_category: str | None,
    expense_category: str | None,
) -> str:
    lowered = text.lower()
    scores = []
    if task_category:
        task_score = _keyword_score(
            lowered,
            ["задач", "задача", "нужно", "надо", "созвон", "позвон", "сделать", "видео"],
        )
        scores.append((task_score, task_category))
    if idea_category:
        idea_score = _keyword_score(
            lowered,
            ["иде", "идея", "план", "создать", "курс", "клуб", "мечта"],
        )
        scores.append((idea_score, idea_category))
    if expense_category:
        expense_score = _keyword_score(
            lowered,
            ["потрат", "заплатил", "купил", "расход", "руб", "доллар", "магазин"],
        )
        scores.append((expense_score, expense_category))

    if not scores:
        return ""
    scores.sort(reverse=True, key=lambda item: item[0])
    if scores[0][0] <= 0:
        return ""
    return scores[0][1]


def _keyword_score(text: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _split_task_part(text: str) -> list[str]:
    if ":" in text:
        _, rest = text.split(":", 1)
        text = rest.strip()
    parts = [part.strip(" ,.-") for part in text.split(" и ") if part.strip()]
    if len(parts) <= 1:
        return [text.strip()]
    return parts


def _expand_item_text(item_text: str, transcript: str, category: str) -> str:
    item_text = item_text.strip() or transcript.strip()
    word_count = len(item_text.split())
    category_lower = (category or "").lower()

    if any(key in category_lower for key in ["трат", "расход", "expense", "spend"]):
        keywords = ["потрат", "купил", "заплатил", "руб", "доллар", "подписк", "магазин"]
    elif any(key in category_lower for key in ["иде", "idea"]):
        keywords = ["иде", "идея", "хочу", "план", "курс", "запустить", "создать"]
    elif any(key in category_lower for key in ["задач", "task", "todo"]):
        keywords = ["задач", "нужно", "надо", "сделать", "созвон", "позвон", "видео"]
    else:
        keywords = ["надо", "нужно", "хочу", "потрат", "иде", "задач"]

    expanded = _extract_sentence(transcript, keywords)
    if len(expanded.split()) > word_count:
        return expanded
    return item_text


async def _split_multi_items(
    openai_service: OpenAIService,
    transcript: str,
    settings: dict[str, str],
    model: str,
) -> list[dict[str, str]]:
    rule_items = _rule_based_items_from_transcript(transcript, settings)
    if len(_explicit_category_signals(transcript)) >= 1 and rule_items:
        return rule_items

    categories_text = "\n".join(
        f"- {name}: {desc}" if desc else f"- {name}"
        for name, desc in settings.items()
    )
    user_prompt = _safe_format(
        DEFAULT_MULTI_USER,
        {
            "text": transcript,
            "categories": categories_text,
        },
    )
    try:
        data = await openai_service.chat_json(
            system_prompt=DEFAULT_MULTI_SYSTEM,
            user_prompt=user_prompt,
            model=model,
        )
    except Exception:
        logger.exception("Failed to split multi items")
        return [{"category": "", "text": transcript}]

    items = data.get("items", [])
    if not isinstance(items, list) or not items:
        return [{"category": "", "text": transcript}]

    normalized_map = {name.strip().lower(): name for name in settings.keys()}
    result: list[dict[str, str]] = []
    for item in items:
        category = str(item.get("category", "")).strip()
        text = str(item.get("text", "")).strip() or transcript
        category_norm = category.strip().lower()
        canonical = normalized_map.get(category_norm)
        if not canonical:
            canonical = ""
        result.append({"category": canonical, "text": text})
    return _ensure_expected_categories(transcript, settings, result)


async def _process_multi_items(
    status_msg: Message,
    message: Message,
    state: FSMContext,
    sheets_service: SheetsService,
    router_service: RouterService,
    settings: dict[str, str],
    extract_prompt: str,
    today_str: str,
    items: list[dict[str, str]],
    model: str,
    full_transcript: str,
) -> None:
    results: list[str] = []
    pending: list[dict[str, object]] = []
    today_date = _parse_date_value(today_str) or datetime.now().date()
    for item in items:
        item_text = item.get("text", "")
        category = item.get("category", "")
        if item.get("source") != "rule":
            item_text = _expand_item_text(item_text, full_transcript, category)

        if not category:
            try:
                category, _reasoning = await router_service.classify_category(
                    item_text,
                    settings,
                    DEFAULT_ROUTER_USER,
                    model=model,
                )
            except Exception:
                results.append("⚠️ Не удалось определить категорию для одного пункта.")
                continue

        try:
            headers = await sheets_service.get_headers(category)
            if not headers:
                results.append(f"⚠️ Не найден лист для категории: {category}")
                continue
            clean_headers = [_clean_header(header) for header in headers]
            row = await router_service.extract_row(
                item_text,
                clean_headers,
                today_str,
                extract_prompt,
                model=model,
            )
            row = _apply_text_fields(headers, row, item_text)
            row = _apply_date_fields(headers, row, item_text, today_date)

            missing_required = _get_missing_required(headers, row)
            if missing_required:
                pending.append(
                    {
                        "category": category,
                        "headers": headers,
                        "row": row,
                        "transcript": item_text,
                        "today_str": today_str,
                    }
                )
                continue

            duplicate_preview = await _find_duplicate(sheets_service, category, headers, row)
            if duplicate_preview:
                results.append(f"⚠️ Дубликат пропущен: {category}")
                continue

            await sheets_service.append_row(category, row)
            await sheets_service.append_row("Inbox", [today_str, category, item_text])

            summary = _get_summary_value(headers, row) or _make_summary(item_text)
            results.append(f"✅ {category}: {summary}")
        except Exception:
            logger.exception("Failed to process multi item")
            results.append("⚠️ Ошибка при обработке одного пункта.")

    if pending:
        await state.set_state(IntakeState.waiting_required)
        await state.update_data(
            pending_items=pending,
            pending_index=0,
            multi_results=results,
        )
        await _prompt_required_item(status_msg, state)
        return

    text = "Готово. Я разобрал сообщение на несколько пунктов:\n\n" + "\n".join(results)
    await _send_long_text(status_msg, message, text, safe_mode=True)


async def _send_long_text(
    status_msg: Message,
    message: Message,
    text: str,
    safe_mode: bool = True,
) -> None:
    chunks = _split_text(text, MAX_TG_CHARS)
    if not chunks:
        return
    if safe_mode and len(chunks) > 3:
        chunks = chunks[:3]
        chunks[-1] = (
            chunks[-1]
            + "\n\n…Слишком много результатов. Уточните запрос."
        )
    try:
        await status_msg.edit_text(chunks[0])
    except Exception:
        await message.answer(chunks[0])
    for chunk in chunks[1:]:
        await message.answer(chunk)


async def _prompt_required_item(target_msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    pending = data.get("pending_items", [])
    idx = int(data.get("pending_index", 0))
    if idx < 0 or idx >= len(pending):
        return
    item = pending[idx]
    headers = item.get("headers", [])
    row = item.get("row", [])
    missing_required = _get_missing_required(headers, row)
    await state.update_data(missing_required_indices=[i for i, _name in missing_required])
    if not missing_required:
        return
    if len(missing_required) == 1 and _is_priority_header(missing_required[0][1]):
        kb = _build_priority_keyboard()
        await target_msg.edit_text(
            "⚠️ Нужно выбрать приоритет задачи:",
            reply_markup=kb.as_markup(),
        )
        return
    missing_names = ", ".join(name for _idx, name in missing_required)
    await target_msg.edit_text(
        "⚠️ Нужно заполнить обязательные поля:\n"
        f"{missing_names}\n\n"
        "Напишите ответ так:\n"
        "Поле=значение; Поле=значение\n"
        "Пример: Приоритет=Высокий\n\n"
        "Можно нажать «Пропустить».",
        reply_markup=_build_required_keyboard().as_markup(),
    )


def _get_pending_item(data: dict) -> tuple[list[dict], int, dict] | None:
    pending = data.get("pending_items")
    if not pending:
        return None
    idx = int(data.get("pending_index", 0))
    if idx < 0 or idx >= len(pending):
        return None
    return pending, idx, pending[idx]


async def _finalize_multi_item(
    target_msg: Message,
    message: Message,
    state: FSMContext,
    sheets_service: SheetsService,
    item: dict,
    results: list[str],
) -> None:
    category = item.get("category", "")
    headers = item.get("headers", [])
    row = item.get("row", [])
    transcript = item.get("transcript", "")
    today_str = item.get("today_str", datetime.now().strftime("%d.%m.%Y"))
    today_date = _parse_date_value(today_str) or datetime.now().date()

    row = _apply_text_fields(headers, row, transcript)
    row = _apply_date_fields(headers, row, transcript, today_date)

    duplicate_preview = await _find_duplicate(sheets_service, category, headers, row)
    if duplicate_preview:
        results.append(f"⚠️ Дубликат пропущен: {category}")
    else:
        await sheets_service.append_row(category, row)
        await sheets_service.append_row("Inbox", [today_str, category, transcript])
        summary = _get_summary_value(headers, row) or _make_summary(transcript)
        results.append(f"✅ {category}: {summary}")

    data = await state.get_data()
    pending = data.get("pending_items", [])
    idx = int(data.get("pending_index", 0)) + 1
    if idx < len(pending):
        await state.update_data(pending_index=idx, multi_results=results)
        await _prompt_required_item(target_msg, state)
        return

    await state.clear()
    text = "Готово. Я разобрал сообщение на несколько пунктов:\n\n" + "\n".join(results)
    await _send_long_text(target_msg, message, text, safe_mode=True)


def _split_text(text: str, max_len: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if line_len > max_len:
            if current:
                chunks.append("\n".join(current).strip())
                current = []
                current_len = 0
            for i in range(0, len(line), max_len):
                chunks.append(line[i : i + max_len])
            continue
        if current_len + line_len > max_len and current:
            chunks.append("\n".join(current).strip())
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]
