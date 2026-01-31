import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional

import gspread

logger = logging.getLogger(__name__)


class SheetsService:
    def __init__(self, spreadsheet: gspread.Spreadsheet) -> None:
        self._spreadsheet = spreadsheet
        self._settings_cache: Dict[str, str] = {}

    @classmethod
    async def create(cls, spreadsheet_id: str, service_account_path: Path) -> "SheetsService":
        client = await asyncio.to_thread(
            gspread.service_account,
            filename=str(service_account_path),
        )
        spreadsheet = await asyncio.to_thread(client.open_by_key, spreadsheet_id)
        return cls(spreadsheet)

    async def load_settings(self) -> Dict[str, str]:
        def _read() -> Dict[str, str]:
            worksheet = self._spreadsheet.worksheet("Settings")
            rows = worksheet.get_all_values()
            mapping: Dict[str, str] = {}
            for row in rows:
                if not row:
                    continue
                category = row[0].strip() if len(row) > 0 else ""
                if not category:
                    continue
                if category.lower() in ("category", "категория"):
                    continue
                description = row[1].strip() if len(row) > 1 else ""
                mapping[category] = description
            return mapping

        mapping = await asyncio.to_thread(_read)
        self._settings_cache = mapping
        return mapping

    async def ensure_worksheet(self, name: str, rows: int = 100, cols: int = 2) -> gspread.Worksheet:
        def _ensure() -> gspread.Worksheet:
            try:
                return self._spreadsheet.worksheet(name)
            except gspread.exceptions.WorksheetNotFound:
                return self._spreadsheet.add_worksheet(title=name, rows=rows, cols=cols)

        return await asyncio.to_thread(_ensure)

    async def get_prompts(self) -> Dict[str, str]:
        def _read() -> Dict[str, str]:
            worksheet = self._spreadsheet.worksheet("Prompts")
            rows = worksheet.get_all_values()
            data: Dict[str, str] = {}
            for row in rows:
                if len(row) < 2:
                    continue
                key = row[0].strip().lower()
                if not key or key in ("key", "ключ"):
                    continue
                data[key] = row[1]
            return data

        try:
            return await asyncio.to_thread(_read)
        except gspread.exceptions.WorksheetNotFound:
            await self.ensure_worksheet("Prompts")
            return {}

    async def set_prompt(self, key: str, value: str) -> None:
        def _upsert() -> None:
            worksheet = self._spreadsheet.worksheet("Prompts")
            rows = worksheet.get_all_values()
            target_row: Optional[int] = None
            for idx, row in enumerate(rows, start=1):
                if not row:
                    continue
                if row[0].strip().lower() == key.lower():
                    target_row = idx
                    break
            if target_row:
                worksheet.update(f"A{target_row}:B{target_row}", [[key, value]])
            else:
                if not rows:
                    worksheet.append_row(["Key", "Value"])
                worksheet.append_row([key, value])

        await asyncio.to_thread(_upsert)

    async def get_headers(self, sheet_name: str) -> List[str]:
        def _read() -> List[str]:
            worksheet = self._spreadsheet.worksheet(sheet_name)
            return worksheet.row_values(1)

        return await asyncio.to_thread(_read)

    async def get_all_values(self, sheet_name: str) -> List[List[str]]:
        def _read() -> List[List[str]]:
            worksheet = self._spreadsheet.worksheet(sheet_name)
            return worksheet.get_all_values()

        return await asyncio.to_thread(_read)

    async def list_worksheets(self) -> List[str]:
        def _read() -> List[str]:
            return [ws.title for ws in self._spreadsheet.worksheets()]

        return await asyncio.to_thread(_read)

    async def append_row(self, sheet_name: str, values: List[str]) -> None:
        def _append() -> None:
            worksheet = self._spreadsheet.worksheet(sheet_name)
            worksheet.append_row(values, value_input_option="USER_ENTERED")

        await asyncio.to_thread(_append)

    async def delete_row(self, sheet_name: str, row_index: int) -> None:
        def _delete() -> None:
            worksheet = self._spreadsheet.worksheet(sheet_name)
            worksheet.delete_rows(row_index)

        await asyncio.to_thread(_delete)
