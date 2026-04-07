from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan


async def get_active_plans(session: AsyncSession) -> list[Plan]:
    result = await session.scalars(
        select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.id.asc())
    )
    return list(result)


async def get_plan_by_id(session: AsyncSession, plan_id: int) -> Plan | None:
    return await session.get(Plan, plan_id)
