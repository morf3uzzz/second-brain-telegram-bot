from typing import Optional

from aiogram.types import User


def is_allowed(user: Optional[User], allowed_user_ids: list[int], allowed_usernames: list[str]) -> bool:
    if not allowed_user_ids and not allowed_usernames:
        return True
    if not user:
        return False
    if allowed_user_ids and user.id not in allowed_user_ids:
        return False
    if allowed_usernames:
        username = (user.username or "").lower()
        if username not in allowed_usernames:
            return False
    return True


def user_label(user: Optional[User]) -> str:
    if not user:
        return "unknown"
    username = f"@{user.username}" if user.username else "no_username"
    return f"{user.id} ({username})"
