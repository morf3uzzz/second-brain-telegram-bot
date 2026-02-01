import logging
import re
from typing import Dict

from app.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)


class IntentService:
    def __init__(self, openai_service: OpenAIService) -> None:
        self._openai = openai_service

    async def detect(self, text: str, model: str | None = None) -> Dict[str, str]:
        heuristic = _heuristic_intent(text)
        if heuristic:
            return heuristic

        system_prompt = (
            "Ты определяешь намерение пользователя. "
            "Варианты: add (добавить, записать, запомнить, отметить), delete (удалить, убрать, стереть), ask (найти, узнать, спросить). "
            "Если текст похож на новую задачу, заметку или расход — выбирай 'add'. "
            "Только если явно сказано 'удали' или 'убери' — выбирай 'delete'. "
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
            model=model or self._openai.router_model,
        )
        action = str(data.get("action", "add")).strip().lower()
        query = str(data.get("query", "")).strip()
        if action not in {"add", "delete", "ask"}:
            action = "add"
        if action == "ask" and not _strong_question_signal(text):
            action = "add"
            query = ""
        return {"action": action, "query": query}


def _heuristic_intent(text: str) -> Dict[str, str] | None:
    lowered = text.lower()
    # "не надо" removed because it matches "мне надо" (I need).
    # "отмени" removed to avoid confusion with "отметить" (mark).
    if _contains_any(lowered, ["удали", "удалить", "убери", "стереть", "remove", "delete"]):
        return {"action": "delete", "query": text}
    if _strong_question_signal(lowered):
        return {"action": "ask", "query": text}
    return None


def _strong_question_signal(text: str) -> bool:
    lowered = text.lower()
    if "?" in lowered:
        return True
    if _contains_any(
        lowered,
        [
            "вопрос",
            "спроси",
            "узнай",
            "расскажи",
            "объясни",
            "подскажи",
            "помоги",
            "покажи",
            "найди",
            "напомни",
            "сколько",
            "почему",
            "зачем",
            "где",
            "когда",
        ],
    ):
        return True
    return bool(
        re.match(
            r"^\s*(что|как|когда|где|почему|зачем|сколько|какие|какая|какой|каких|какими|есть ли|можно ли|нужно ли)\b",
            lowered,
        )
    )


def _looks_like_add(text: str) -> bool:
    lowered = text.lower()
    return _contains_any(
        lowered,
        [
            "задача",
            "идея",
            "трат",
            "расход",
            "потрат",
            "купил",
            "купила",
            "нужно",
            "надо",
            "хочу",
            "запиши",
            "добавь",
            "сохрани",
            "поставь",
            "напомни",
            "отметь",
            "сделать",
            "сделай",
            "создать",
        ],
    )


def _contains_any(text: str, keywords: list[str]) -> bool:
    for keyword in keywords:
        if keyword in text:
            return True
    return False
