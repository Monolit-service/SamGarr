from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, Plan, Subscription, SubscriptionStatus, User
from app.services.channel_service import create_access_links, revoke_access


async def activate_or_extend_subscription(
    session: AsyncSession,
    user: User,
    plan: Plan,
    payment: Payment,
) -> Subscription:
    now = datetime.utcnow()

    subscription = await session.scalar(
        select(Subscription)
        .where(
            and_(
                Subscription.user_id == user.id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.ends_at > now,
                Subscription.plan_id == plan.id,
            )
        )
        .order_by(Subscription.ends_at.desc())
    )

    if subscription is None:
        same_scope_active = await session.scalar(
            select(Subscription)
            .join(Plan, Subscription.plan_id == Plan.id)
            .where(
                and_(
                    Subscription.user_id == user.id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.ends_at > now,
                    Plan.channel_scope == plan.channel_scope,
                )
            )
            .order_by(Subscription.ends_at.desc())
        )
        subscription = same_scope_active

    if subscription is None:
        starts_at = now
        ends_at = now + timedelta(days=plan.duration_days)
        subscription = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            starts_at=starts_at,
            ends_at=ends_at,
            status=SubscriptionStatus.ACTIVE,
        )
        session.add(subscription)
    else:
        if subscription.ends_at < now:
            subscription.starts_at = now
            subscription.ends_at = now + timedelta(days=plan.duration_days)
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.plan_id = plan.id
        else:
            subscription.ends_at = subscription.ends_at + timedelta(days=plan.duration_days)
            subscription.plan_id = plan.id

    await session.commit()
    await session.refresh(subscription)
    return subscription


async def activate_and_get_links(session: AsyncSession, user: User, plan: Plan, payment: Payment, bot) -> tuple[Subscription, list[str]]:
    subscription = await activate_or_extend_subscription(session, user, plan, payment)
    links = await create_access_links(bot, plan.channel_scope)
    return subscription, links


async def get_user_subscriptions(session: AsyncSession, user_id: int) -> list[tuple[Subscription, Plan]]:
    rows = await session.execute(
        select(Subscription, Plan)
        .join(Plan, Subscription.plan_id == Plan.id)
        .join(User, Subscription.user_id == User.id)
        .where(User.telegram_id == user_id)
        .order_by(Subscription.ends_at.desc())
    )
    return list(rows.all())


async def expire_due_subscriptions(session: AsyncSession, bot) -> int:
    now = datetime.utcnow()
    rows = await session.execute(
        select(Subscription, User, Plan)
        .join(User, Subscription.user_id == User.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            and_(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.ends_at <= now,
            )
        )
    )

    expired_count = 0
    for subscription, user, plan in rows.all():
        await revoke_access(bot, user.telegram_id, plan.channel_scope)
        subscription.status = SubscriptionStatus.EXPIRED
        expired_count += 1

    await session.commit()
    return expired_count
