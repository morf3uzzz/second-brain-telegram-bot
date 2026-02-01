import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class BotSettings:
    timezone: str = "UTC"
    daily_enabled: bool = True
    daily_time: str = "21:00"
    weekly_enabled: bool = False
    weekly_day: str = "sun"
    weekly_time: str = "20:00"
    summary_chat_id: Optional[int] = None
    last_daily_sent: str = ""
    last_weekly_sent: str = ""
    openai_model: str = "gpt-4o"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotSettings":
        return cls(
            timezone=str(data.get("timezone", "UTC")),
            daily_enabled=bool(data.get("daily_enabled", True)),
            daily_time=str(data.get("daily_time", "21:00")),
            weekly_enabled=bool(data.get("weekly_enabled", False)),
            weekly_day=str(data.get("weekly_day", "sun")),
            weekly_time=str(data.get("weekly_time", "20:00")),
            summary_chat_id=data.get("summary_chat_id"),
            last_daily_sent=str(data.get("last_daily_sent", "")),
            last_weekly_sent=str(data.get("last_weekly_sent", "")),
            openai_model=str(data.get("openai_model", "gpt-4o")),
        )


class BotSettingsService:
    def __init__(self, settings_path: Path) -> None:
        self._path = settings_path

    async def load(self) -> BotSettings:
        def _read() -> BotSettings:
            if not self._path.exists():
                return BotSettings()
            with self._path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return BotSettings.from_dict(data)

        return await asyncio.to_thread(_read)

    async def save(self, settings: BotSettings) -> None:
        def _write() -> None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._path.with_suffix(".tmp")
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(asdict(settings), file, ensure_ascii=False, indent=2)
            temp_path.replace(self._path)

        await asyncio.to_thread(_write)

    async def update(self, updates: Dict[str, Any]) -> BotSettings:
        settings = await self.load()
        for key, value in updates.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
            else:
                logger.warning("Unknown settings key: %s", key)
        await self.save(settings)
        return settings
