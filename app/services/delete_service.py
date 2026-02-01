import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from app.services.sheets_service import SheetsService

logger = logging.getLogger(__name__)


@dataclass
class DeleteCandidate:
    sheet_name: str
    row_index: int
    headers: List[str]
    row_values: List[str]
    preview: str


class DeleteService:
    def __init__(self, sheets_service: SheetsService) -> None:
        self._sheets = sheets_service

    async def find_candidates(self, query: str, limit: int = 7) -> List[DeleteCandidate]:
        exclude = {"settings", "prompts", "inbox", "botsettings"}
        sheet_names = await self._sheets.list_worksheets()
        tokens = _tokenize(query)
        filters = _infer_filters(query)
        tokens = [token for token in tokens if token not in _STOP_WORDS]
        candidates: List[Tuple[int, DeleteCandidate]] = []

        for name in sheet_names:
            if name.strip().lower() in exclude:
                continue
            if filters.sheet_keywords and not _match_sheet(name, filters.sheet_keywords):
                continue
            rows = await self._sheets.get_all_values(name)
            if not rows or len(rows) < 2:
                continue
            headers = rows[0]
            date_idx = _find_date_index(headers) if filters.start_date else None
            for row_idx, row in enumerate(rows[1:], start=2):
                if not any(cell.strip() for cell in row):
                    continue
                if filters.start_date:
                    row_date = _extract_row_date(row, headers, date_idx)
                    if not row_date:
                        continue
                    if row_date < filters.start_date or row_date > filters.end_date:
                        continue
                record_text = _row_to_text(headers, row)
                score = _score(tokens, record_text)
                if tokens:
                    if score <= 0:
                        continue
                else:
                    if not (filters.start_date or filters.sheet_keywords):
                        continue
                    score = 1
                preview = _make_preview(name, headers, row)
                candidate = DeleteCandidate(
                    sheet_name=name,
                    row_index=row_idx,
                    headers=headers,
                    row_values=row,
                    preview=preview,
                )
                candidates.append((score, candidate))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return [candidate for _score_value, candidate in candidates[:limit]]

    async def delete_candidate(self, candidate: DeleteCandidate) -> Tuple[bool, bool]:
        await self._sheets.delete_row(candidate.sheet_name, candidate.row_index)
        inbox_deleted = await self._delete_from_inbox(candidate)
        return True, inbox_deleted

    async def _delete_from_inbox(self, candidate: DeleteCandidate) -> bool:
        rows = await self._sheets.get_all_values("Inbox")
        if not rows or len(rows) < 2:
            return False
        inbox_rows = list(enumerate(rows[1:], start=2))

        target_date = _get_date_value(candidate.headers, candidate.row_values)
        target_category = candidate.sheet_name

        best_score = 0
        best_index: Optional[int] = None
        candidate_tokens = _tokenize(" ".join(candidate.row_values))

        for row_index, row in inbox_rows:
            if len(row) < 3:
                continue
            date_val = row[0].strip()
            category_val = row[1].strip()
            transcript = row[2].strip()
            if target_category and category_val != target_category:
                continue
            if target_date and date_val != target_date:
                continue
            score = _score(candidate_tokens, transcript)
            if score > best_score:
                best_score = score
                best_index = row_index

        if best_index:
            await self._sheets.delete_row("Inbox", best_index)
            return True
        return False


def _row_to_text(headers: List[str], row: List[str]) -> str:
    parts = []
    for idx, header in enumerate(headers):
        value = row[idx] if idx < len(row) else ""
        parts.append(f"{header}: {value}")
    return " ".join(parts).lower()


def _make_preview(sheet_name: str, headers: List[str], row: List[str], max_len: int = 400) -> str:
    pairs = []
    for idx, header in enumerate(headers):
        value = row[idx] if idx < len(row) else ""
        if value.strip():
            pairs.append(f"{header}: {value}")
    preview = "; ".join(pairs)
    if len(preview) > max_len:
        preview = preview[: max_len - 3] + "..."
    return f"[{sheet_name}] {preview}"


def _get_date_value(headers: List[str], row: List[str]) -> Optional[str]:
    for idx, header in enumerate(headers):
        if header.strip().lower() in {"дата", "date", "дата добавления", "дата выполнения", "due date"}:
            if idx < len(row):
                return row[idx].strip()
    return None


def _tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[a-zа-я0-9]+", text.lower())
    return [token for token in tokens if len(token) > 2]


def _score(tokens: List[str], text: str) -> int:
    if not tokens:
        return 0
    lowered = text.lower()
    return sum(1 for token in tokens if token in lowered)


@dataclass
class DeleteFilters:
    sheet_keywords: set[str] | None = None
    start_date: date | None = None
    end_date: date | None = None


_STOP_WORDS = {
    "удали",
    "удалить",
    "удалип",
    "удаление",
    "за",
    "последние",
    "последний",
    "последнюю",
    "последних",
    "день",
    "дня",
    "дней",
    "сегодня",
    "вчера",
    "позавчера",
    "задачи",
    "задача",
    "task",
    "tasks",
}


def _infer_filters(query: str) -> DeleteFilters:
    lowered = query.lower()
    filters = DeleteFilters()

    sheet_keywords: set[str] = set()
    if any(word in lowered for word in ["задач", "task", "todo", "to-do"]):
        sheet_keywords.add("задач")
        sheet_keywords.add("task")
    if any(word in lowered for word in ["иде", "idea"]):
        sheet_keywords.add("иде")
        sheet_keywords.add("idea")
    if any(word in lowered for word in ["трат", "расход", "expense", "spend"]):
        sheet_keywords.add("трат")
        sheet_keywords.add("расход")
        sheet_keywords.add("expense")
        sheet_keywords.add("spend")
    filters.sheet_keywords = sheet_keywords or None

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


def _match_sheet(name: str, keywords: set[str]) -> bool:
    lowered = name.strip().lower()
    return any(keyword in lowered for keyword in keywords)


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
