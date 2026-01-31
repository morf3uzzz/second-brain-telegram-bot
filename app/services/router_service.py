import logging
from typing import Dict, List, Tuple

from app.prompts import DEFAULT_EXTRACT_SYSTEM, DEFAULT_ROUTER_SYSTEM
from app.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)


class RouterService:
    def __init__(self, openai_service: OpenAIService) -> None:
        self._openai = openai_service

    @staticmethod
    def _normalize(value: str) -> str:
        return value.strip().lower()

    async def classify_category(
        self,
        text: str,
        settings: Dict[str, str],
        user_prompt_template: str,
        system_prompt: str = DEFAULT_ROUTER_SYSTEM,
    ) -> Tuple[str, str]:
        categories_text = "\n".join(
            f"- {name}: {desc}" if desc else f"- {name}"
            for name, desc in settings.items()
        )
        user_prompt = user_prompt_template.format(
            text=text,
            categories=categories_text,
        )
        data = await self._openai.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._openai.router_model,
        )

        raw_category = str(data.get("category", "")).strip()
        if not raw_category:
            raise ValueError("GPT did not return category")

        normalized_map = {self._normalize(k): k for k in settings.keys()}
        canonical = normalized_map.get(self._normalize(raw_category))
        if not canonical:
            raise ValueError(f"Category not found in settings: {raw_category}")

        reasoning = str(data.get("reasoning", "")).strip()
        return canonical, reasoning

    async def extract_row(
        self,
        text: str,
        headers: List[str],
        today_str: str,
        user_prompt_template: str,
        system_prompt: str = DEFAULT_EXTRACT_SYSTEM,
    ) -> List[str]:
        user_prompt = user_prompt_template.format(
            text=text,
            headers=headers,
            today=today_str,
        )
        data = await self._openai.chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._openai.extract_model,
        )

        normalized_headers = {self._normalize(h): h for h in headers}
        normalized_data: Dict[str, str] = {}
        for key, value in data.items():
            norm = self._normalize(str(key))
            if norm in normalized_headers:
                normalized_data[normalized_headers[norm]] = "" if value is None else str(value)

        for header in headers:
            normalized_data.setdefault(header, "")

        for header in headers:
            if self._normalize(header) == "дата" and not normalized_data.get(header).strip():
                normalized_data[header] = today_str

        return [normalized_data.get(header, "") for header in headers]
