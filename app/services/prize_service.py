from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Payment, PaymentStatus, Plan, PrizeAward, Subscription, SubscriptionStatus, User


@dataclass(frozen=True)
class PrizeDefinition:
    code: str
    title: str
    weight: int
    discount_percent: int = 0
    free_days: int = 0
    is_rarest: bool = False


@dataclass(frozen=True)
class PrizeEligibility:
    allowed: bool
    reason: str | None = None
    profile_ready_at: datetime | None = None


def get_prizes() -> tuple[PrizeDefinition, ...]:
    settings = get_settings()
    prizes = (
        PrizeDefinition(
            code="sub_1_day",
            title="Подписка на 1 день",
            weight=settings.prize_weight_sub_1_day,
            free_days=1,
        ),
        PrizeDefinition(
            code="discount_5",
            title="Скидка 5% на приват",
            weight=settings.prize_weight_discount_5,
            discount_percent=5,
        ),
        PrizeDefinition(
            code="discount_10",
            title="Скидка 10% на приват",
            weight=settings.prize_weight_discount_10,
            discount_percent=10,
        ),
        PrizeDefinition(
            code="discount_25",
            title="Скидка 25% на приват",
            weight=settings.prize_weight_discount_25,
            discount_percent=25,
        ),
        PrizeDefinition(
            code="sub_30_days",
            title="Месячный доступ к привату",
            weight=settings.prize_weight_sub_30_days,
            free_days=30,
            is_rarest=True,
        ),
    )
    if sum(max(item.weight, 0) for item in prizes) <= 0:
        raise RuntimeError("At least one PRIZE_WEIGHT_* value must be greater than zero")
    return prizes


def get_prizes_by_code() -> dict[str, PrizeDefinition]:
    return {item.code: item for item in get_prizes()}


def draw_prize() -> PrizeDefinition:
    prizes = get_prizes()
    population = [item for item in prizes if item.weight > 0]
    weights = [item.weight for item in population]
    return random.choices(population, weights=weights, k=1)[0]


async def get_last_prize_award(session: AsyncSession, user_id: int) -> PrizeAward | None:
    return await session.scalar(
        select(PrizeAward)
        .where(PrizeAward.user_id == user_id)
        .order_by(PrizeAward.created_at.desc())
        .limit(1)
    )


async def can_spin_now(session: AsyncSession, user_id: int, cooldown_hours: int) -> tuple[bool, datetime | None]:
    last_award = await get_last_prize_award(session, user_id)
    if last_award is None:
        return True, None
    next_time = last_award.created_at + timedelta(hours=cooldown_hours)
    return datetime.utcnow() >= next_time, next_time


async def get_prize_eligibility(session: AsyncSession, user: User) -> PrizeEligibility:
    settings = get_settings()
    if not settings.prize_anti_abuse_enabled:
        return PrizeEligibility(True)

    if settings.prize_require_username and not (user.username or '').strip():
        return PrizeEligibility(
            allowed=False,
            reason="Для участия в рандомайзере добавь username в настройках Telegram. Это помогает отсекать мультиаккаунты.",
        )

    min_profile_age_hours = max(settings.prize_min_profile_age_hours, 0)
    if min_profile_age_hours > 0:
        ready_at = user.created_at + timedelta(hours=min_profile_age_hours)
        if datetime.utcnow() < ready_at:
            return PrizeEligibility(
                allowed=False,
                reason=(
                    "Рандомайзер открывается не сразу после старта бота. "
                    "Это ограничение защищает от мультиаккаунтов."
                ),
                profile_ready_at=ready_at,
            )

    min_paid_payments = max(settings.prize_min_paid_payments, 0)
    if min_paid_payments > 0:
        paid_count = await session.scalar(
            select(func.count(Payment.id)).where(
                and_(
                    Payment.user_id == user.id,
                    Payment.status == PaymentStatus.PAID,
                )
            )
        )
        paid_count = int(paid_count or 0)
        if paid_count < min_paid_payments:
            return PrizeEligibility(
                allowed=False,
                reason=(
                    "Рандомайзер доступен только после хотя бы одной успешной оплаты в боте. "
                    "Это ограничение снижает фарм призов через мультиаккаунты."
                ),
            )

    return PrizeEligibility(True)


async def create_prize_award(session: AsyncSession, user: User, prize: PrizeDefinition) -> PrizeAward:
    award = PrizeAward(
        user_id=user.id,
        prize_code=prize.code,
        prize_title=prize.title,
        discount_percent=prize.discount_percent,
        free_days=prize.free_days,
        is_redeemed=prize.free_days > 0,
        redeemed_at=datetime.utcnow() if prize.free_days > 0 else None,
    )
    session.add(award)
    await session.commit()
    await session.refresh(award)
    return award


async def get_active_discount_award(session: AsyncSession, user_id: int) -> PrizeAward | None:
    return await session.scalar(
        select(PrizeAward)
        .where(
            and_(
                PrizeAward.user_id == user_id,
                PrizeAward.discount_percent > 0,
                PrizeAward.is_redeemed.is_(False),
                PrizeAward.is_burned.is_(False),
            )
        )
        .order_by(PrizeAward.created_at.asc())
        .limit(1)
    )


async def apply_discount_to_price(session: AsyncSession, user_id: int, base_amount_xtr: int) -> tuple[int, PrizeAward | None]:
    award = await get_active_discount_award(session, user_id)
    if award is None:
        return base_amount_xtr, None
    discounted = round(base_amount_xtr * (100 - award.discount_percent) / 100)
    discounted = max(1, discounted)
    return discounted, award


async def redeem_discount_award(session: AsyncSession, award: PrizeAward | None) -> None:
    if award is None or award.is_redeemed or award.is_burned:
        return
    award.is_redeemed = True
    award.redeemed_at = datetime.utcnow()
    await session.commit()


async def _remove_burned_free_days_from_subscription(session: AsyncSession, user_id: int, days_to_remove: int) -> None:
    if days_to_remove <= 0:
        return

    now = datetime.utcnow()
    subscription = await session.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.ends_at.desc())
        .limit(1)
    )
    if subscription is None:
        return

    new_end = subscription.ends_at - timedelta(days=days_to_remove)
    if new_end <= now:
        subscription.ends_at = now
        subscription.status = SubscriptionStatus.EXPIRED
    else:
        subscription.ends_at = new_end
        subscription.status = SubscriptionStatus.ACTIVE


async def burn_previous_prizes(session: AsyncSession, user_id: int) -> list[PrizeAward]:
    rows = await session.scalars(
        select(PrizeAward)
        .where(
            and_(
                PrizeAward.user_id == user_id,
                PrizeAward.is_burned.is_(False),
            )
        )
        .order_by(PrizeAward.created_at.asc())
    )
    awards = list(rows)
    if not awards:
        return []

    days_to_remove = sum(max(int(award.free_days or 0), 0) for award in awards if award.is_redeemed)
    if days_to_remove > 0:
        await _remove_burned_free_days_from_subscription(session, user_id, days_to_remove)

    now = datetime.utcnow()
    for award in awards:
        award.is_burned = True
        award.burned_at = now

    await session.commit()
    return awards


async def has_active_subscription_access(session: AsyncSession, user_id: int) -> bool:
    now = datetime.utcnow()
    active_subscription = await session.scalar(
        select(Subscription.id)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.ends_at > now,
            Plan.is_active.is_(True),
        )
        .limit(1)
    )
    return active_subscription is not None
