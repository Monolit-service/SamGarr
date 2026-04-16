from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.referral_service import build_referral_code


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    full_name: str | None,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, username=username, full_name=full_name, referral_code=build_referral_code(telegram_id))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    user.username = username
    user.full_name = full_name
    if not user.referral_code:
        user.referral_code = build_referral_code(telegram_id)
    await session.commit()
    await session.refresh(user)
    return user
