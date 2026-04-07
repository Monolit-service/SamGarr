from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import unquote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AnonymousQuestion, AnonymousQuestionStatus, Payment, PaymentStatus, Poll, PollStatus, Subscription, SubscriptionStatus, User

settings = get_settings()


@dataclass(frozen=True)
class AdminStats:
    total_users: int
    active_subscriptions: int
    paid_payments: int
    pending_questions: int
    active_polls: int


def is_admin_user(telegram_id: int | None) -> bool:
    return telegram_id is not None and telegram_id in settings.admin_ids


async def get_admin_stats(session: AsyncSession) -> AdminStats:
    total_users = await session.scalar(select(func.count()).select_from(User)) or 0
    active_subscriptions = await session.scalar(
        select(func.count()).select_from(Subscription).where(Subscription.status == SubscriptionStatus.ACTIVE)
    ) or 0
    paid_payments = await session.scalar(
        select(func.count()).select_from(Payment).where(Payment.status == PaymentStatus.PAID)
    ) or 0
    pending_questions = await session.scalar(
        select(func.count()).select_from(AnonymousQuestion).where(AnonymousQuestion.status == AnonymousQuestionStatus.PENDING)
    ) or 0
    active_polls = await session.scalar(
        select(func.count()).select_from(Poll).where(Poll.status == PollStatus.ACTIVE)
    ) or 0
    return AdminStats(
        total_users=int(total_users),
        active_subscriptions=int(active_subscriptions),
        paid_payments=int(paid_payments),
        pending_questions=int(pending_questions),
        active_polls=int(active_polls),
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _configured_sqlite_path() -> Path:
    db_url = settings.database_url.strip()
    if db_url.startswith("sqlite+aiosqlite:///"):
        raw_path = db_url.removeprefix("sqlite+aiosqlite:///")
    elif db_url.startswith("sqlite:///"):
        raw_path = db_url.removeprefix("sqlite:///")
    else:
        raise RuntimeError("Скачивание бэкапа БД поддерживается только для SQLite")

    raw_path = unquote(raw_path)
    if raw_path.startswith("./"):
        return (_project_root() / raw_path[2:]).resolve()
    if raw_path.startswith("/"):
        return Path(raw_path)
    return (_project_root() / raw_path).resolve()


def create_database_backup() -> Path:
    source_path = _configured_sqlite_path()
    if not source_path.exists():
        raise FileNotFoundError(f"Файл базы данных не найден: {source_path}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(gettempdir()) / f"bot_db_backup_{timestamp}.sqlite3"

    source_conn = sqlite3.connect(source_path)
    dest_conn = sqlite3.connect(output_path)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    return output_path
