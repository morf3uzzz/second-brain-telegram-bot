import logging
from collections import Counter
from datetime import date, datetime, timedelta
from typing import List, Tuple

import gspread.exceptions

from app.services.openai_service import OpenAIService
from app.services.sheets_service import SheetsService

logger = logging.getLogger(__name__)


class SummaryService:
    def __init__(self, openai_service: OpenAIService, sheets_service: SheetsService) -> None:
        self._openai = openai_service
        self._sheets = sheets_service

    async def daily_summary(self, target_date: date) -> Tuple[str, int]:
        rows = await self._get_inbox_rows()
        filtered = [row for row in rows if _parse_date(row[0]) == target_date]
        return await self._build_summary(filtered, f"за {_format_date(target_date)}")

    async def weekly_summary(self, end_date: date) -> Tuple[str, int]:
        rows = await self._get_inbox_rows()
        start_date = end_date - timedelta(days=6)
        filtered = [
            row
            for row in rows
            if (row_date := _parse_date(row[0])) is not None
            and start_date <= row_date <= end_date
        ]
        period = f"за период {_format_date(start_date)} — {_format_date(end_date)}"
        return await self._build_summary(filtered, period)

    async def _get_inbox_rows(self) -> List[List[str]]:
        try:
            rows = await self._sheets.get_all_values("Inbox")
        except gspread.exceptions.WorksheetNotFound:
            logger.warning("Inbox sheet not found, summary will be empty")
            return []
        if not rows:
            return []
        # Skip header row if first cell doesn't look like a date (DD.MM.YYYY or YYYY-MM-DD)
        first_cell = (rows[0][0] or "").strip()
        if _parse_date(first_cell) is not None:
            data_rows = rows
        else:
            data_rows = rows[1:]
        return [row for row in data_rows if row]

    async def _build_summary(self, rows: List[List[str]], period: str) -> Tuple[str, int]:
        if not rows:
            return f"🧾 Сводка {period}\n\n❗️Нет записей.", 0

        categories = [row[1].strip() for row in rows if len(row) > 1]
        counts = Counter(categories)
        stats = "\n".join(f"• {cat}: {count}" for cat, count in counts.most_common())

        transcripts = [row[2] for row in rows if len(row) > 2 and row[2].strip()]
        short_texts = [text[:300] for text in transcripts[:50]]
        joined_text = "\n".join(f"- {text}" for text in short_texts)

        system_prompt = (
            "Сделай краткую сводку по заметкам пользователя. "
            "Ответ дай в 3-5 коротких пунктах без Markdown."
        )
        user_prompt = (
            f"Период: {period}\n"
            f"Статистика по категориям:\n{stats}\n\n"
            "Заметки:\n"
            f"{joined_text}\n\n"
            "Сформируй 3-5 коротких пунктов резюме."
        )
        try:
            summary = await self._openai.chat_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=self._openai.extract_model,
            )
        except Exception:
            logger.exception("Failed to build LLM summary, sending stats only")
            summary = ""

        header = (
            f"🧾 Сводка {period}\n\n"
            f"📌 Всего записей: {len(rows)}\n\n"
            f"📂 Категории:\n{stats}"
        )
        if summary:
            bullets = _normalize_bullets(summary)
            return f"{header}\n\n🧠 Резюме:\n{bullets}", len(rows)
        return header, len(rows)


def _parse_date(value: str) -> date | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_bullets(text: str) -> str:
    lines = [line.strip("-• ").strip() for line in text.splitlines() if line.strip()]
    return "\n".join(f"• {line}" for line in lines[:5])


def _format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")
