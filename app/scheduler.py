import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.services.bot_settings_service import BotSettingsService
from app.services.summary_service import SummaryService

logger = logging.getLogger(__name__)


WEEKDAY_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


async def scheduler_loop(
    bot: Bot,
    settings_service: BotSettingsService,
    summary_service: SummaryService,
    poll_seconds: int = 30,
) -> None:
    while True:
        try:
            settings = await settings_service.load()
            if not settings.summary_chat_id:
                await asyncio.sleep(poll_seconds)
                continue

            tz = ZoneInfo(settings.timezone)
            now = datetime.now(tz)
            time_str = now.strftime("%H:%M")
            today_str = now.strftime("%Y-%m-%d")

            if settings.daily_enabled and time_str == settings.daily_time:
                if settings.last_daily_sent != today_str:
                    text, _count = await summary_service.daily_summary(now.date())
                    await bot.send_message(settings.summary_chat_id, text)
                    await settings_service.update({"last_daily_sent": today_str})

            if settings.weekly_enabled and time_str == settings.weekly_time:
                weekday = WEEKDAY_MAP.get(settings.weekly_day.lower(), None)
                if weekday is not None and now.weekday() == weekday:
                    if settings.last_weekly_sent != today_str:
                        text, _count = await summary_service.weekly_summary(now.date())
                        await bot.send_message(settings.summary_chat_id, text)
                        await settings_service.update({"last_weekly_sent": today_str})
        except Exception:
            logger.exception("Scheduler loop error")

        await asyncio.sleep(poll_seconds)
