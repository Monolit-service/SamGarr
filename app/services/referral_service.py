from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ChannelScope, Payment, PaymentStatus, Plan, User
from app.services.subscription_service import grant_free_days_subscription

REF_PREFIX = "ref_"


settings = get_settings()


def build_referral_code(telegram_id: int) -> str:
    return f"{REF_PREFIX}{telegram_id}"


def extract_referral_code(start_argument: str | None) -> str | None:
    if not start_argument:
        return None
    start_argument = start_argument.strip()
    if not start_argument.startswith(REF_PREFIX):
        return None
    return start_argument


async def ensure_user_referral_code(session: AsyncSession, user: User) -> User:
    expected_code = build_referral_code(user.telegram_id)
    if user.referral_code != expected_code:
        user.referral_code = expected_code
        await session.commit()
        await session.refresh(user)
    return user


async def attach_referrer_from_start_argument(
    session: AsyncSession,
    user: User,
    start_argument: str | None,
) -> User:
    referral_code = extract_referral_code(start_argument)
    if not referral_code:
        return user

    if user.referred_by_user_id is not None:
        return user

    if user.referral_code == referral_code:
        return user

    referrer = await session.scalar(select(User).where(User.referral_code == referral_code))
    if referrer is None:
        return user

    if referrer.id == user.id:
        return user

    user.referred_by_user_id = referrer.id
    user.referred_at = datetime.utcnow()
    await session.commit()
    await session.refresh(user)
    return user


async def get_referral_count(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count()).select_from(User).where(User.referred_by_user_id == user_id)
    return int((await session.scalar(stmt)) or 0)


def build_referral_link(bot_username: str, referral_code: str) -> str:
    return f"https://t.me/{bot_username}?start={referral_code}"


async def backfill_missing_referral_codes(session: AsyncSession) -> int:
    users = list((await session.scalars(select(User).where(User.referral_code.is_(None)))).all())
    updated = 0
    for user in users:
        user.referral_code = build_referral_code(user.telegram_id)
        updated += 1
    if updated:
        await session.commit()
    return updated


async def _get_referral_bonus_plan(session: AsyncSession) -> Plan | None:
    plan = await session.scalar(
        select(Plan)
        .where(
            Plan.is_active.is_(True),
            Plan.channel_scope == ChannelScope.CHANNEL_1,
        )
        .order_by(Plan.duration_days.asc(), Plan.id.asc())
    )
    return plan


async def maybe_grant_referral_bonus(
    session: AsyncSession,
    bot,
    paid_user: User,
) -> tuple[bool, User | None, object | None]:
    if paid_user.referred_by_user_id is None:
        return False, None, None

    if paid_user.referral_bonus_granted_at is not None:
        return False, None, None

    paid_count = await session.scalar(
        select(func.count())
        .select_from(Payment)
        .where(
            and_(
                Payment.user_id == paid_user.id,
                Payment.status == PaymentStatus.PAID,
            )
        )
    )
    if int(paid_count or 0) != 1:
        return False, None, None

    referrer = await session.get(User, paid_user.referred_by_user_id)
    if referrer is None:
        return False, None, None

    bonus_plan = await _get_referral_bonus_plan(session)
    if bonus_plan is None:
        return False, referrer, None

    subscription, _ = await grant_free_days_subscription(
        session=session,
        user=referrer,
        plan=bonus_plan,
        free_days=settings.referral_bonus_days,
        bot=bot,
    )
    paid_user.referral_bonus_granted_at = datetime.utcnow()
    await session.commit()
    await session.refresh(paid_user)
    return True, referrer, subscription


async def get_referral_bonus_days_granted(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count()).select_from(User).where(
        User.referred_by_user_id == user_id,
        User.referral_bonus_granted_at.is_not(None),
    )
    rewarded_referrals = int((await session.scalar(stmt)) or 0)
    return rewarded_referrals * settings.referral_bonus_days
