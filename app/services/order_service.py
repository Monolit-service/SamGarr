from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, Plan, User
from app.services.plan_service import get_plan_by_id
from app.services.subscription_service import activate_and_get_links


async def fulfill_subscription_payment(
    session: AsyncSession,
    bot: Bot,
    payment: Payment,
) -> tuple[User | None, Plan | None, object | None, list[str]]:
    user = await session.get(User, payment.user_id)
    if user is None:
        return None, None, None, []

    plan = await get_plan_by_id(session, payment.plan_id)
    if plan is None:
        return user, None, None, []

    subscription, access_links = await activate_and_get_links(session, user, plan, payment, bot)
    return user, plan, subscription, access_links
