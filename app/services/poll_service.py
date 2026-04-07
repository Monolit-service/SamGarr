from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Poll, PollOption, PollStatus, PollVote, User

settings = get_settings()


@dataclass(slots=True)
class PollStats:
    total_votes: int
    total_voters: int
    option_counts: dict[int, int]


def is_admin_user(telegram_id: int) -> bool:
    return telegram_id in settings.admin_ids


async def create_poll(
    session: AsyncSession,
    *,
    creator: User,
    question: str,
    options: list[str],
    allows_multiple_answers: bool,
) -> Poll:
    poll = Poll(
        creator_user_id=creator.id,
        question=question,
        allows_multiple_answers=allows_multiple_answers,
        status=PollStatus.ACTIVE,
    )
    session.add(poll)
    await session.flush()

    for index, option_text in enumerate(options, start=1):
        session.add(PollOption(poll_id=poll.id, position=index, text=option_text))

    await session.commit()
    return await get_poll(session, poll.id)


async def get_poll(session: AsyncSession, poll_id: int) -> Poll | None:
    stmt: Select[tuple[Poll]] = (
        select(Poll)
        .options(
            selectinload(Poll.options),
            selectinload(Poll.votes),
        )
        .where(Poll.id == poll_id)
    )
    return await session.scalar(stmt)


async def get_active_polls(session: AsyncSession, limit: int = 10) -> list[Poll]:
    stmt = (
        select(Poll)
        .options(selectinload(Poll.options), selectinload(Poll.votes))
        .where(Poll.status == PollStatus.ACTIVE)
        .order_by(Poll.created_at.desc())
        .limit(limit)
    )
    rows = await session.scalars(stmt)
    return list(rows)


async def close_poll(session: AsyncSession, poll: Poll) -> Poll:
    poll.status = PollStatus.CLOSED
    poll.closed_at = datetime.utcnow()
    await session.commit()
    await session.refresh(poll)
    return poll


async def get_all_users(session: AsyncSession) -> list[User]:
    rows = await session.scalars(select(User).order_by(User.id.asc()))
    return list(rows)


async def get_poll_stats(session: AsyncSession, poll_id: int) -> PollStats:
    counts_rows = await session.execute(
        select(PollVote.option_id, func.count(PollVote.id))
        .where(PollVote.poll_id == poll_id)
        .group_by(PollVote.option_id)
    )
    option_counts = {option_id: count for option_id, count in counts_rows.all()}

    total_votes = sum(option_counts.values())
    total_voters = await session.scalar(
        select(func.count(func.distinct(PollVote.user_id))).where(PollVote.poll_id == poll_id)
    )
    return PollStats(
        total_votes=total_votes,
        total_voters=int(total_voters or 0),
        option_counts=option_counts,
    )


async def get_user_selected_option_ids(session: AsyncSession, poll_id: int, user: User) -> set[int]:
    rows = await session.scalars(
        select(PollVote.option_id).where(PollVote.poll_id == poll_id, PollVote.user_id == user.id)
    )
    return set(rows)


async def cast_vote(
    session: AsyncSession,
    *,
    poll: Poll,
    option_id: int,
    user: User,
) -> tuple[set[int], PollStats]:
    option_ids = {option.id for option in poll.options}
    if option_id not in option_ids:
        raise ValueError("Invalid poll option")

    current_selected = await get_user_selected_option_ids(session, poll.id, user)

    if poll.allows_multiple_answers:
        if option_id in current_selected:
            await session.execute(
                delete(PollVote).where(
                    PollVote.poll_id == poll.id,
                    PollVote.user_id == user.id,
                    PollVote.option_id == option_id,
                )
            )
            current_selected.remove(option_id)
        else:
            session.add(PollVote(poll_id=poll.id, option_id=option_id, user_id=user.id))
            current_selected.add(option_id)
    else:
        await session.execute(
            delete(PollVote).where(PollVote.poll_id == poll.id, PollVote.user_id == user.id)
        )
        session.add(PollVote(poll_id=poll.id, option_id=option_id, user_id=user.id))
        current_selected = {option_id}

    await session.commit()
    stats = await get_poll_stats(session, poll.id)
    return current_selected, stats
