from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards import profile_keyboard
from app.services.admin_service import is_admin_user
from app.services.subscription_service import get_user_subscriptions
from app.services.user_service import get_or_create_user
from app.utils.text import format_profile_text, format_subscription_line

router = Router()


@router.message(Command("my"))
async def my_subscriptions_command(message: Message, session: AsyncSession) -> None:
    rows = await get_user_subscriptions(session, message.from_user.id)
    if not rows:
        await message.answer("У тебя пока нет подписок.")
        return

    text = "\n\n".join(format_subscription_line(subscription, plan) for subscription, plan in rows)
    await message.answer(text)


@router.message(Command("profile"))
async def profile_command(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    rows = await get_user_subscriptions(session, message.from_user.id)
    await message.answer(format_profile_text(user, rows), reply_markup=profile_keyboard(is_admin=is_admin_user(message.from_user.id if message.from_user else None)))


@router.callback_query(lambda c: c.data == "my_subscriptions")
async def my_subscriptions_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    rows = await get_user_subscriptions(session, callback.from_user.id)
    if not rows:
        await callback.message.answer("У тебя пока нет подписок.")
        await callback.answer()
        return

    text = "\n\n".join(format_subscription_line(subscription, plan) for subscription, plan in rows)
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(lambda c: c.data == "my_profile")
async def my_profile_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    rows = await get_user_subscriptions(session, callback.from_user.id)
    await callback.message.edit_text(format_profile_text(user, rows), reply_markup=profile_keyboard(is_admin=is_admin_user(callback.from_user.id if callback.from_user else None)))
    await callback.answer()
