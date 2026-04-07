from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ChannelScope, Plan

settings = get_settings()


def build_default_plans() -> list[dict]:
    return [
        {
            "code": "private_30",
            "title": "Приват — 1 месяц",
            "description": "Доступ в приватный канал на 30 дней",
            "channel_scope": ChannelScope.CHANNEL_1,
            "duration_days": 30,
            "price_xtr": settings.private_30_price_xtr,
        },
        {
            "code": "private_60",
            "title": "Приват — 2 месяца",
            "description": "Доступ в приватный канал на 60 дней",
            "channel_scope": ChannelScope.CHANNEL_1,
            "duration_days": 60,
            "price_xtr": settings.private_60_price_xtr,
        },
    ]


async def seed_plans(session: AsyncSession) -> None:
    default_plans = build_default_plans()
    active_codes = {item["code"] for item in default_plans}

    existing_plans = list((await session.scalars(select(Plan))).all())
    by_code = {plan.code: plan for plan in existing_plans}

    for plan in existing_plans:
        if plan.code not in active_codes:
            plan.is_active = False

    for item in default_plans:
        existing = by_code.get(item["code"])
        if existing is None:
            session.add(Plan(**item, is_active=True))
            continue

        existing.title = item["title"]
        existing.description = item["description"]
        existing.channel_scope = item["channel_scope"]
        existing.duration_days = item["duration_days"]
        existing.price_xtr = item["price_xtr"]
        existing.is_active = True

    await session.commit()
