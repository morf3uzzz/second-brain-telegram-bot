import asyncio
import logging
import re
from typing import List

from app.services.openai_service import OpenAIService
from app.services.sheets_service import SheetsService

logger = logging.getLogger(__name__)


class QAService:
    def __init__(self, openai_service: OpenAIService, sheets_service: SheetsService) -> None:
        self._openai = openai_service
        self._sheets = sheets_service

    async def answer_question(self, question: str, model: str | None = None) -> str:
        records = await self._collect_records()
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
    for line in lines:
        stripped = line.strip()
        if re.match(r"^\d+\.\s", stripped):
            number, rest = stripped.split(".", 1)
            rest = rest.strip()
            parts = [part.strip() for part in rest.split(";") if part.strip()]
            if parts:
                formatted.append(f"{number}. {parts[0]}")
                for part in parts[1:]:
                    formatted.append(f"   {part}")
                formatted.append("")
            else:
                formatted.append(line)
        else:
            formatted.append(line)
    return "\n".join(formatted).strip()
