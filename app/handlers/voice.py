import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from gspread.exceptions import WorksheetNotFound

from app.prompts import DEFAULT_EXTRACT_USER, DEFAULT_ROUTER_USER, EXTRACT_PROMPT_KEY, ROUTER_PROMPT_KEY
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


class IntakeState(StatesGroup):
    waiting_required = State()


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

        try:
            logger.info("Скачиваю аудио, длительность=%ss", message.voice.duration)
            step_start = asyncio.get_running_loop().time()
            temp_path = await asyncio.wait_for(_download_voice(bot, message), timeout=30)
            file_size = os.path.getsize(temp_path)
            logger.info("Аудио скачано за %.2fs, размер=%s bytes", asyncio.get_running_loop().time() - step_start, file_size)

            logger.info("Отправляю в Whisper")
            step_start = asyncio.get_running_loop().time()
            transcript = await asyncio.wait_for(openai_service.transcribe(temp_path), timeout=180)
            if not transcript:
                raise ValueError("Empty transcription")
            logger.info("Транскрипция готова за %.2fs, символов=%s", asyncio.get_running_loop().time() - step_start, len(transcript))

            logger.info("Читаю настройки бота")
            bot_settings = await settings_service.load()
            model = bot_settings.openai_model

            logger.info("Определяю намерение пользователя")
            intent = await intent_service.detect(transcript, model=model)
            action = intent.get("action", "add")
            query = intent.get("query", transcript)

            if action == "ask":
                logger.info("Режим вопроса")
                await status_msg.edit_text("⏳ Ищу по базе, это может занять до минуты.")
                answer = await qa_service.answer_question(query or transcript, model=model)
                await status_msg.edit_text(answer)
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

            logger.info("Читаю настройки бота")
            bot_settings = await settings_service.load()
            model = bot_settings.openai_model

            logger.info("Читаю Prompts из Google Sheets")
            prompts = await sheets_service.get_prompts()
            router_prompt = prompts.get(ROUTER_PROMPT_KEY, DEFAULT_ROUTER_USER)
            extract_prompt = prompts.get(EXTRACT_PROMPT_KEY, DEFAULT_EXTRACT_USER)

            logger.info("Классифицирую категорию (model=%s)", model)
            category, _reasoning = await asyncio.wait_for(
                router_service.classify_category(transcript, settings, router_prompt, model=model),
                timeout=60,
            )
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

            missing_required = _get_missing_required(headers, row)
            if missing_required:
                await state.set_state(IntakeState.waiting_required)
                await state.update_data(
                    category=category,
                    headers=headers,
                    row=row,
                    transcript=transcript,
                    today_str=today_str,
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
        category = data.get("category", "")
        headers = data.get("headers", [])
        row = data.get("row", [])
        transcript = data.get("transcript", "")
        today_str = data.get("today_str", datetime.now().strftime("%d.%m.%Y"))

        if not category or not headers or not row:
            await state.clear()
            await callback.message.edit_text("⚠️ Не удалось восстановить контекст. Повторите запись.")
            await callback.answer()
            return

        row = _apply_text_fields(headers, row, transcript)
        row = _apply_text_fields(headers, row, transcript)
        row = _apply_text_fields(headers, row, transcript)
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
        category = data.get("category", "")
        headers = data.get("headers", [])
        row = data.get("row", [])
        transcript = data.get("transcript", "")
        today_str = data.get("today_str", datetime.now().strftime("%d.%m.%Y"))

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


def _get_summary_value(headers: list[str], row: list[str]) -> str:
    idx = _find_header_index(headers, {"суть", "описание", "summary"})
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


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
