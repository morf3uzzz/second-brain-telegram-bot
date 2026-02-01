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


def build_delete_keyboard(candidates: list[DeleteCandidate]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for idx in range(len(candidates)):
        kb.button(text=str(idx + 1), callback_data=f"del:pick:{idx}")
    kb.button(text="Отмена", callback_data="del:cancel")
    kb.adjust(min(5, len(candidates) + 1))
    return kb


def format_delete_list(candidates: list[DeleteCandidate]) -> str:
    lines = ["Выберите запись для удаления (нажмите кнопку с номером):\n"]
    for idx, c in enumerate(candidates, start=1):
        lines.append(f"{idx}. {c.preview}\n")
    return "".join(lines)


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
        await callback.message.answer("Удаление отменено.")
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

        deleted, inbox_deleted = await delete_service.delete_candidate(candidate)
        await state.clear()

        if deleted:
            if inbox_deleted:
                await callback.message.answer("✅ Запись удалена из листа и Inbox.")
            else:
                await callback.message.answer("✅ Запись удалена из листа. Inbox не найден.")
        else:
            await callback.message.answer("⚠️ Не удалось удалить запись.")

        await callback.answer()

    return router
