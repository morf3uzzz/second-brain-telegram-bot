import logging
import re
from dataclasses import dataclass
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
        candidates: List[Tuple[int, DeleteCandidate]] = []

        for name in sheet_names:
            if name.strip().lower() in exclude:
                continue
            rows = await self._sheets.get_all_values(name)
            if not rows or len(rows) < 2:
                continue
            headers = rows[0]
            for row_idx, row in enumerate(rows[1:], start=2):
                if not any(cell.strip() for cell in row):
                    continue
                record_text = _row_to_text(headers, row)
                score = _score(tokens, record_text)
                if score <= 0:
                    continue
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
        if header.strip().lower() in {"дата", "date"}:
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
