import logging
from typing import Dict

from app.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)


class IntentService:
    def __init__(self, openai_service: OpenAIService) -> None:
        self._openai = openai_service

    async def detect(self, text: str) -> Dict[str, str]:
        heuristic = _heuristic_intent(text)
        if heuristic:
            return heuristic

        system_prompt = (
            "Ты определяешь намерение пользователя. "
            "Варианты: add (добавить запись), delete (удалить запись), ask (задать вопрос). "
            "Отвечай строго JSON."
        )
        user_prompt = (
            f"Текст пользователя:\n{text}\n\n"
            'Верни JSON: {"action": "add|delete|ask", "query": "..."}. '
            "Если action=add, query может быть пустым."
        )
        data = await self._openai.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._openai.router_model,
        )
        action = str(data.get("action", "add")).strip().lower()
        query = str(data.get("query", "")).strip()
        if action not in {"add", "delete", "ask"}:
            action = "add"
        return {"action": action, "query": query}


def _heuristic_intent(text: str) -> Dict[str, str] | None:
    lowered = text.lower()
    if _contains_any(lowered, ["удали", "удалить", "убери", "отмени", "не надо", "remove", "delete"]):
        return {"action": "delete", "query": text}
    if _contains_any(lowered, ["вопрос", "спроси", "узнай", "что ", "как ", "почему", "?"]):
        return {"action": "ask", "query": text}
    return None


def _contains_any(text: str, keywords: list[str]) -> bool:
    for keyword in keywords:
        if keyword in text:
            return True
    return False
