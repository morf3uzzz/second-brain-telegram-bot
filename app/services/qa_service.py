import asyncio
import logging
from typing import List

from app.services.openai_service import OpenAIService
from app.services.sheets_service import SheetsService

logger = logging.getLogger(__name__)


class QAService:
    def __init__(self, openai_service: OpenAIService, sheets_service: SheetsService) -> None:
        self._openai = openai_service
        self._sheets = sheets_service

    async def answer_question(self, question: str) -> str:
        records = await self._collect_records()
        if not records:
            return "В базе пока нет данных для поиска."

        chunks = _chunk_records(records, max_chars=5000)
        intermediate_answers: List[str] = []

        system_prompt = (
            "Ты помощник поиска по личной базе. "
            "Отвечай кратко и по делу, с опорой на данные."
        )

        for chunk in chunks:
            user_prompt = (
                f"Вопрос:\n{question}\n\n"
                f"Данные (фрагмент):\n{chunk}\n\n"
                "Дай краткий ответ и перечисли 3-7 самых релевантных записей."
            )
            answer = await self._openai.chat_text(system_prompt, user_prompt, model=self._openai.extract_model)
            if answer:
                intermediate_answers.append(answer)

        if len(intermediate_answers) == 1:
            return intermediate_answers[0]

        final_prompt = (
            f"Вопрос:\n{question}\n\n"
            "Собери единый ответ на основе промежуточных результатов ниже. "
            "Сделай короткое резюме и перечисли релевантные записи:\n\n"
            + "\n\n---\n\n".join(intermediate_answers)
        )
        return await self._openai.chat_text(system_prompt, final_prompt, model=self._openai.extract_model)

    async def _collect_records(self) -> List[str]:
        exclude = {"settings", "prompts", "inbox", "botsettings"}
        sheet_names = await self._sheets.list_worksheets()
        result: List[str] = []

        for name in sheet_names:
            if name.strip().lower() in exclude:
                continue
            rows = await self._sheets.get_all_values(name)
            if not rows:
                continue
            headers = rows[0]
            for row in rows[1:]:
                if not any(cell.strip() for cell in row):
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
