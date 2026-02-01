import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.services.delete_service import DeleteCandidate, DeleteService
from app.utils.auth import is_allowed, user_label

logger = logging.getLogger(__name__)


class DeleteState(StatesGroup):
    selecting = State()
    confirming = State()


def build_delete_keyboard(candidates: list[DeleteCandidate]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for idx in range(len(candidates)):
        kb.button(text=str(idx + 1), callback_data=f"del:pick:{idx}")
    kb.button(text="Отмена", callback_data="del:cancel")
    kb.adjust(min(5, len(candidates) + 1))
    return kb


def format_delete_list(candidates: list[DeleteCandidate]) -> str:
    lines = [
        f"Найдено записей: {len(candidates)}",
        "",
        "Выберите запись для удаления (нажмите кнопку с номером):",
        "",
    ]
    for idx, candidate in enumerate(candidates, start=1):
        lines.extend(_format_candidate_lines(candidate, index=idx))
        lines.append("────────")
    if lines and lines[-1] == "────────":
        lines.pop()
    return "\n".join(lines)


def _build_confirm_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Удалить", callback_data="del:confirm")
    kb.button(text="⬅️ Назад к списку", callback_data="del:back")
    kb.button(text="❌ Отмена", callback_data="del:cancel")
    kb.adjust(1)
    return kb


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardBuilder | None = None) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup.as_markup() if reply_markup else None)
    except Exception:
        await callback.message.answer(text, reply_markup=reply_markup.as_markup() if reply_markup else None)


def create_delete_router(
    delete_service: DeleteService,
    allowed_user_ids: list[int],
    allowed_usernames: list[str],
) -> Router:
    router = Router()

    @router.callback_query(F.data == "del:cancel")
    async def cancel_delete(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        await state.clear()
        await _safe_edit(callback, "Удаление отменено.")
        await callback.answer()

    @router.callback_query(F.data.startswith("del:pick:"))
    async def pick_delete(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return

        data = await state.get_data()
        candidates = data.get("candidates", [])
        if not candidates:
            await callback.message.answer("⚠️ Список кандидатов не найден.")
            await state.clear()
            await callback.answer()
            return

        try:
            index = int(callback.data.split(":")[-1])
        except ValueError:
            await callback.answer("Ошибка выбора", show_alert=True)
            return

        if index < 0 or index >= len(candidates):
            await callback.answer("Неверный выбор", show_alert=True)
            return

        candidate_dict = candidates[index]
        candidate = DeleteCandidate(
            sheet_name=candidate_dict["sheet_name"],
            row_index=candidate_dict["row_index"],
            headers=candidate_dict["headers"],
            row_values=candidate_dict["row_values"],
            preview=candidate_dict["preview"],
        )

        await state.set_state(DeleteState.confirming)
        await state.update_data(selected_index=index)
        preview_text = "\n".join(_format_candidate_lines(candidate))
        text = (
            "⚠️ Подтвердите удаление записи:\n\n"
            f"{preview_text}\n\n"
            "Удалить?"
        )
        await _safe_edit(callback, text, _build_confirm_keyboard())
        await callback.answer()

    @router.callback_query(DeleteState.confirming, F.data == "del:back")
    async def back_to_list(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        data = await state.get_data()
        candidates = data.get("candidates", [])
        if not candidates:
            await state.clear()
            await _safe_edit(callback, "⚠️ Список кандидатов не найден.")
            await callback.answer()
            return
        kb = build_delete_keyboard(
            [
                DeleteCandidate(
                    sheet_name=c["sheet_name"],
                    row_index=c["row_index"],
                    headers=c["headers"],
                    row_values=c["row_values"],
                    preview=c["preview"],
                )
                for c in candidates
            ]
        )
        text = format_delete_list(
            [
                DeleteCandidate(
                    sheet_name=c["sheet_name"],
                    row_index=c["row_index"],
                    headers=c["headers"],
                    row_values=c["row_values"],
                    preview=c["preview"],
                )
                for c in candidates
            ]
        )
        await state.set_state(DeleteState.selecting)
        await _safe_edit(callback, text, kb)
        await callback.answer()

    @router.callback_query(DeleteState.confirming, F.data == "del:confirm")
    async def confirm_delete(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_allowed(callback.from_user, allowed_user_ids, allowed_usernames):
            await callback.answer("Доступ запрещен", show_alert=True)
            return
        data = await state.get_data()
        candidates = data.get("candidates", [])
        index = data.get("selected_index")
        if not candidates or index is None:
            await state.clear()
            await _safe_edit(callback, "⚠️ Список кандидатов не найден.")
            await callback.answer()
            return
        if index < 0 or index >= len(candidates):
            await callback.answer("Неверный выбор", show_alert=True)
            return
        candidate_dict = candidates[index]
        candidate = DeleteCandidate(
            sheet_name=candidate_dict["sheet_name"],
            row_index=candidate_dict["row_index"],
            headers=candidate_dict["headers"],
            row_values=candidate_dict["row_values"],
            preview=candidate_dict["preview"],
        )
        deleted, inbox_deleted = await delete_service.delete_candidate(candidate)
        await state.clear()
        if deleted:
            if inbox_deleted:
                await _safe_edit(callback, "✅ Запись удалена из листа и Inbox.")
            else:
                await _safe_edit(callback, "✅ Запись удалена из листа. Inbox не найден.")
        else:
            await _safe_edit(callback, "⚠️ Не удалось удалить запись.")
        await callback.answer()

    return router


def _format_candidate_lines(candidate: DeleteCandidate, index: int | None = None) -> list[str]:
    header = f"🧾 {index}. [{candidate.sheet_name}]" if index else f"🧾 [{candidate.sheet_name}]"
    lines = [header]
    for idx, header_name in enumerate(candidate.headers):
        value = candidate.row_values[idx] if idx < len(candidate.row_values) else ""
        value = str(value).strip()
        if not value:
            continue
        display = header_name.replace("*", "").strip()
        emoji = _field_emoji(display)
        lines.append(f"   {emoji} {display}: {_shorten_value(value)}")
    return lines


def _shorten_value(value: str, max_len: int = 200) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _field_emoji(label: str) -> str:
    key = label.strip().lower()
    if key in {"дата", "дата добавления", "date"}:
        return "📅"
    return "-"
