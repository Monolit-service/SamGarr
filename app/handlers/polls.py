from __future__ import annotations

import asyncio
import contextlib

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards import (
    admin_poll_close_keyboard,
    admin_poll_confirm_keyboard,
    admin_poll_type_keyboard,
    poll_voting_keyboard,
)
from app.services.poll_service import (
    cast_vote,
    close_poll,
    create_poll,
    get_active_polls,
    get_all_users,
    get_poll,
    get_poll_stats,
    get_user_selected_option_ids,
    is_admin_user,
)
from app.services.user_service import get_or_create_user
from app.utils.text import format_admin_poll_summary, format_poll_message, format_poll_preview

router = Router()


class AdminPollStates(StatesGroup):
    waiting_question = State()
    waiting_options = State()
    waiting_type = State()
    waiting_confirmation = State()


async def _require_admin_message(message: Message) -> bool:
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return False
    return True


async def _require_admin_callback(callback: CallbackQuery) -> bool:
    if not callback.from_user or not is_admin_user(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return False
    return True


@router.message(Command("poll"))
async def start_poll_creation(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return

    await state.set_state(AdminPollStates.waiting_question)
    await state.update_data(question="", options=[], allows_multiple_answers=False)
    await message.answer(
        "Создание анонимного опроса.\n\n"
        "Пришли вопрос одним сообщением.\n"
        "Для отмены отправь /cancel_poll."
    )


@router.message(Command("cancel_poll"))
async def cancel_poll_creation(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await state.clear()
    await message.answer("Создание опроса отменено.")


@router.message(Command("polls"))
async def list_active_polls_handler(message: Message, session: AsyncSession) -> None:
    if not await _require_admin_message(message):
        return

    polls = await get_active_polls(session)
    if not polls:
        await message.answer("Сейчас нет активных опросов.")
        return

    for poll in polls:
        stats = await get_poll_stats(session, poll.id)
        await message.answer(
            format_admin_poll_summary(poll, stats),
            reply_markup=admin_poll_close_keyboard(poll.id),
        )


@router.message(AdminPollStates.waiting_question)
async def poll_question_handler(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return

    question = (message.text or "").strip()
    if len(question) < 5:
        await message.answer("Вопрос слишком короткий. Сформулируй подробнее.")
        return
    if len(question) > 300:
        await message.answer("Вопрос слишком длинный. Максимум 300 символов.")
        return

    await state.update_data(question=question)
    await state.set_state(AdminPollStates.waiting_options)
    await message.answer(
        "Теперь пришли варианты ответа, каждый с новой строки.\n\n"
        "Минимум 2 варианта, максимум 8.\n"
        "Пример:\n"
        "Да\nНет\nНе решил"
    )


@router.message(AdminPollStates.waiting_options)
async def poll_options_handler(message: Message, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return

    raw_text = message.text or ""
    options = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if len(options) < 2:
        await message.answer("Нужно минимум 2 варианта ответа.")
        return
    if len(options) > 8:
        await message.answer("Слишком много вариантов. Максимум 8.")
        return
    if len({option.casefold() for option in options}) != len(options):
        await message.answer("Варианты должны быть уникальными.")
        return
    if any(len(option) > 200 for option in options):
        await message.answer("Каждый вариант должен быть не длиннее 200 символов.")
        return

    await state.update_data(options=options)
    await state.set_state(AdminPollStates.waiting_type)
    await message.answer(
        "Выбери режим ответа:",
        reply_markup=admin_poll_type_keyboard(),
    )


@router.callback_query(F.data.startswith("poll_create:"))
async def poll_type_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return

    action = callback.data.split(":", maxsplit=1)[1]
    if action == "cancel":
        await state.clear()
        await callback.message.edit_text("Создание опроса отменено.")
        await callback.answer()
        return

    data = await state.get_data()
    question = data.get("question") or ""
    options = data.get("options") or []
    if not question or not options:
        await state.clear()
        await callback.message.edit_text("Данные опроса потерялись. Начни заново через /poll.")
        await callback.answer()
        return

    allows_multiple_answers = action == "multiple"
    await state.update_data(allows_multiple_answers=allows_multiple_answers)
    await state.set_state(AdminPollStates.waiting_confirmation)
    await callback.message.edit_text(
        format_poll_preview(question, options, allows_multiple_answers),
        reply_markup=admin_poll_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "poll_publish")
async def poll_publish_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return

    data = await state.get_data()
    question = data.get("question") or ""
    options = data.get("options") or []
    allows_multiple_answers = bool(data.get("allows_multiple_answers"))
    if not question or len(options) < 2:
        await state.clear()
        await callback.message.edit_text("Данные опроса потерялись. Начни заново через /poll.")
        await callback.answer()
        return

    admin_user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    poll = await create_poll(
        session,
        creator=admin_user,
        question=question,
        options=options,
        allows_multiple_answers=allows_multiple_answers,
    )
    stats = await get_poll_stats(session, poll.id)
    users = await get_all_users(session)

    sent_count = 0
    failed_count = 0
    for user in users:
        try:
            await callback.bot.send_message(
                chat_id=user.telegram_id,
                text=format_poll_message(poll, stats),
                reply_markup=poll_voting_keyboard(poll, selected_option_ids=set(), stats=stats),
            )
            sent_count += 1
        except Exception:
            failed_count += 1
        await asyncio.sleep(0)

    await state.clear()
    await callback.message.edit_text(
        format_admin_poll_summary(poll, stats)
        + f"\n\nРазослано: {sent_count}\nНе доставлено: {failed_count}",
        reply_markup=admin_poll_close_keyboard(poll.id),
    )
    await callback.answer("Опрос разослан")


@router.callback_query(F.data.startswith("poll_vote:"))
async def poll_vote_handler(callback: CallbackQuery, session: AsyncSession) -> None:
    _, poll_id_raw, option_id_raw = callback.data.split(":", maxsplit=2)
    poll = await get_poll(session, int(poll_id_raw))
    if poll is None:
        await callback.answer("Опрос не найден", show_alert=True)
        return

    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )

    if poll.status != "active":
        stats = await get_poll_stats(session, poll.id)
        await callback.message.edit_text(format_poll_message(poll, stats))
        await callback.answer("Голосование уже закрыто", show_alert=True)
        return

    try:
        selected_option_ids, stats = await cast_vote(
            session,
            poll=poll,
            option_id=int(option_id_raw),
            user=user,
        )
    except ValueError:
        await callback.answer("Вариант ответа не найден", show_alert=True)
        return

    refreshed_poll = await get_poll(session, poll.id)
    await callback.message.edit_text(
        format_poll_message(refreshed_poll, stats),
        reply_markup=poll_voting_keyboard(refreshed_poll, selected_option_ids=selected_option_ids, stats=stats),
    )
    await callback.answer("Голос учтён")


@router.callback_query(F.data.startswith("poll_refresh:"))
async def poll_refresh_handler(callback: CallbackQuery, session: AsyncSession) -> None:
    poll_id = int(callback.data.split(":", maxsplit=1)[1])
    poll = await get_poll(session, poll_id)
    if poll is None:
        await callback.answer("Опрос не найден", show_alert=True)
        return

    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    stats = await get_poll_stats(session, poll.id)
    selected_option_ids = await get_user_selected_option_ids(session, poll.id, user)

    markup = None
    if poll.status == "active":
        markup = poll_voting_keyboard(poll, selected_option_ids=selected_option_ids, stats=stats)

    await callback.message.edit_text(format_poll_message(poll, stats), reply_markup=markup)
    await callback.answer("Результаты обновлены")


@router.callback_query(F.data.startswith("poll_admin_close:"))
async def poll_admin_close_handler(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin_callback(callback):
        return

    poll_id = int(callback.data.split(":", maxsplit=1)[1])
    poll = await get_poll(session, poll_id)
    if poll is None:
        await callback.answer("Опрос не найден", show_alert=True)
        return

    if poll.status != "active":
        stats = await get_poll_stats(session, poll.id)
        await callback.message.edit_text(format_admin_poll_summary(poll, stats))
        await callback.answer("Опрос уже завершён")
        return

    poll = await close_poll(session, poll)
    refreshed_poll = await get_poll(session, poll.id)
    stats = await get_poll_stats(session, poll.id)
    await callback.message.edit_text(format_admin_poll_summary(refreshed_poll, stats))
    await callback.answer("Опрос завершён")
