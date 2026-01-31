from dataclasses import dataclass
from pathlib import Path
import os
from typing import List

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)


@dataclass(frozen=True)
class Config:
    telegram_token: str
    openai_api_key: str
    google_sheet_id: str
    allowed_user_ids: List[int]
    allowed_usernames: List[str]

    @staticmethod
    def _parse_int_list(value: str) -> List[int]:
        items = [item.strip() for item in value.split(",") if item.strip()]
        result: List[int] = []
        for item in items:
            if item.isdigit():
                result.append(int(item))
        return result

    @staticmethod
    def _parse_str_list(value: str) -> List[str]:
        return [item.strip().lstrip("@").lower() for item in value.split(",") if item.strip()]

    @classmethod
    def from_env(cls) -> "Config":
        telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        google_sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
        allowed_user_ids = cls._parse_int_list(os.getenv("ALLOWED_USER_IDS", ""))
        allowed_usernames = cls._parse_str_list(os.getenv("ALLOWED_USERNAMES", ""))

        missing = [
            name
            for name, value in (
                ("TELEGRAM_TOKEN", telegram_token),
                ("OPENAI_API_KEY", openai_api_key),
                ("GOOGLE_SHEET_ID", google_sheet_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

        return cls(
            telegram_token=telegram_token,
            openai_api_key=openai_api_key,
            google_sheet_id=google_sheet_id,
            allowed_user_ids=allowed_user_ids,
            allowed_usernames=allowed_usernames,
        )
