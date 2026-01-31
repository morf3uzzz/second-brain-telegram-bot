import json
import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class OpenAIService:
    def __init__(
        self,
        api_key: str,
        router_model: str = "gpt-4o",
        extract_model: str = "gpt-4o",
        timeout_seconds: float = 180.0,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)
        self._router_model = router_model
        self._extract_model = extract_model

    @property
    def router_model(self) -> str:
        return self._router_model

    @property
    def extract_model(self) -> str:
        return self._extract_model

    async def transcribe(self, audio_path: str) -> str:
        with open(audio_path, "rb") as audio_file:
            response = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
            )
        text = response if isinstance(response, str) else getattr(response, "text", "")
        return str(text).strip()

    async def chat_json(self, system_prompt: str, user_prompt: str, model: str) -> dict:
        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error("Bad JSON from OpenAI: %s", content)
            raise exc

    async def chat_text(self, system_prompt: str, user_prompt: str, model: str) -> str:
        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        return content.strip()
