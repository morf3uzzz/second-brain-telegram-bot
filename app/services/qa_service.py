import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List

from app.services.openai_service import OpenAIService
from app.services.sheets_service import SheetsService

logger = logging.getLogger(__name__)


class QAService:
    def __init__(self, openai_service: OpenAIService, sheets_service: SheetsService) -> None:
        self._openai = openai_service
        self._sheets = sheets_service

    async def answer_question(self, question: str, model: str | None = None) -> str:
        filters = _infer_filters(question)
        records = await self._collect_records(filters)
        if not records:
            return "В базе пока нет данных для поиска."

        chunks = _chunk_records(records, max_chars=5000)
        intermediate_answers: List[str] = []

        system_prompt = (
            "Ты помощник поиска по личной базе. "
            "Отвечай кратко и по делу, с опорой на данные. "
            "НЕ используй Markdown (звездочки, решетки). Пиши просто текст. "
            "Формат: короткое резюме, затем список 3-7 записей. "
            "Каждая запись отдельным блоком, поля с новой строки."
        )

        for chunk in chunks:
            user_prompt = (
                f"Вопрос:\n{question}\n\n"
                f"Данные (фрагмент):\n{chunk}\n\n"
                "Дай краткий ответ и перечисли 3-7 самых релевантных записей.\n"
                "Формат примера:\n"
                "1. [Лист]\n"
                "   ДАТА: 01.02.2026\n"
                "   СУТЬ: ...\n"
                "\n"
                "2. [Лист]\n"
                "   ДАТА: ...\n"
                "   СУТЬ: ...\n"
                "Без Markdown."
            )
            answer = await self._openai.chat_text(system_prompt, user_prompt, model=model or self._openai.extract_model)
            if answer:
                intermediate_answers.append(_format_blocks(_strip_markdown(answer)))

        if len(intermediate_answers) == 1:
            return intermediate_answers[0]

        final_prompt = (
            f"Вопрос:\n{question}\n\n"
            "Собери единый ответ на основе промежуточных результатов ниже. "
            "Сделай короткое резюме и перечисли релевантные записи без Markdown:\n\n"
            + "\n\n---\n\n".join(intermediate_answers)
        )
        final_answer = await self._openai.chat_text(system_prompt, final_prompt, model=model or self._openai.extract_model)
        return _format_blocks(_strip_markdown(final_answer))

    async def _collect_records(self, filters: "QueryFilters") -> List[str]:
        exclude = {"settings", "prompts", "inbox", "botsettings"}
        sheet_names = await self._sheets.list_worksheets()
        result: List[str] = []

        for name in sheet_names:
            if name.strip().lower() in exclude:
                continue
            if filters.sheet_names and name.strip().lower() not in filters.sheet_names:
                continue
            rows = await self._sheets.get_all_values(name)
            if not rows:
                continue
            headers = rows[0]
            date_idx = _find_date_index(headers) if filters.start_date else None
            for row in rows[1:]:
                if not any(cell.strip() for cell in row):
                    continue
                if filters.start_date:
                    row_date = _extract_row_date(row, headers, date_idx)
                    if not row_date:
                        continue
                    if row_date < filters.start_date or row_date > filters.end_date:
                        continue
                pairs = []
                for idx, header in enumerate(headers):
                    value = row[idx] if idx < len(row) else ""
                    pairs.append(f"{header}: {value}")
                record = f"[{name}] " + "; ".join(pairs)
                result.append(record)

        logger.info("Собрано записей для поиска: %s", len(result))
        return result


def _chunk_records(records: List[str], max_chars: int) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for record in records:
        record_len = len(record) + 1
        if current_len + record_len > max_chars and current:
            chunks.append("\n".join(current))
            current = [record]
            current_len = record_len
        else:
            current.append(record)
            current_len += record_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _strip_markdown(text: str) -> str:
    return (
        text.replace("**", "")
        .replace("__", "")
        .replace("`", "")
        .replace("*", "")
    )


def _format_blocks(text: str) -> str:
    lines = text.splitlines()
    formatted: List[str] = []
    block_open = False
    current_fields: List[str] = []

    def flush_fields() -> None:
        nonlocal current_fields
        for field in current_fields:
            if ":" in field:
                key, value = field.split(":", 1)
                key_norm = key.strip().lower()
                value = _shorten_value(value.strip())
                prefix = "📅" if key_norm in {"дата", "дата добавления", "date"} else "-"
                formatted.append(f"   {prefix} {key.strip()}: {value}")
            else:
                formatted.append(f"   - {field}")
        current_fields = []

    for line in lines:
        stripped = line.strip()
        header_match = re.match(r"^(?:🧾\s*)?(\d+)\.\s*(.*)$", stripped)
        if header_match:
            if block_open:
                flush_fields()
                formatted.append("────────")
            block_open = True
            number = header_match.group(1)
            rest = header_match.group(2).strip()
            if rest:
                parts = [part.strip() for part in rest.split(";") if part.strip()]
                title = ""
                if parts and ":" not in parts[0]:
                    title = parts[0]
                    current_fields.extend(parts[1:])
                else:
                    current_fields.extend(parts)
                header = f"🧾 {number}. {title}".strip()
                formatted.append(header)
            else:
                formatted.append(f"🧾 {number}.")
            continue

        if block_open:
            if not stripped:
                continue
            if stripped == "────────":
                flush_fields()
                formatted.append("────────")
                continue
            if ":" in stripped:
                current_fields.append(stripped)
                continue

        formatted.append(line)

    if block_open:
        flush_fields()
    return "\n".join(formatted).strip()


def _shorten_value(value: str, max_len: int = 220) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


@dataclass
class QueryFilters:
    sheet_names: set[str] | None = None
    start_date: date | None = None
    end_date: date | None = None


def _infer_filters(question: str) -> QueryFilters:
    lowered = question.lower()
    filters = QueryFilters()

    if any(word in lowered for word in ["задач", "task", "to-do", "todo"]):
        filters.sheet_names = {"задачи", "tasks"}

    today = date.today()
    if "вчера" in lowered or "yesterday" in lowered:
        filters.start_date = today - timedelta(days=1)
        filters.end_date = today - timedelta(days=1)
        return filters
    if "позавчера" in lowered:
        filters.start_date = today - timedelta(days=2)
        filters.end_date = today - timedelta(days=2)
        return filters

    match = re.search(
        r"(?:за\s+последн\w*|последн\w*|за\s+прошл\w*|last)\s+(\d+)\s*(?:дн\w*|days)",
        lowered,
    )
    if match:
        days = int(match.group(1))
        days = max(1, min(days, 365))
        filters.start_date = today - timedelta(days=days - 1)
        filters.end_date = today
        return filters

    if "сегодня" in lowered or "today" in lowered:
        filters.start_date = today
        filters.end_date = today
        return filters

    return filters


def _find_date_index(headers: List[str]) -> int | None:
    normalized = [h.strip().lower() for h in headers]
    preferred = [
        "дата выполнения",
        "дата",
        "дата добавления",
        "date",
        "due date",
    ]
    for key in preferred:
        if key in normalized:
            return normalized.index(key)
    return None


def _extract_row_date(row: List[str], headers: List[str], idx: int | None) -> date | None:
    if idx is None or idx >= len(row):
        return None
    value = row[idx].strip()
    return _parse_date(value)


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None
