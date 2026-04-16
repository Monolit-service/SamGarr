from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, engine
from app.handlers.admin import router as admin_router
from app.handlers.payments import router as payments_router
from app.handlers.polls import router as polls_router
from app.handlers.start import router as start_router
from app.handlers.prizes import router as prizes_router
from app.handlers.subscriptions import router as subscriptions_router
from app.models import Base, Payment, PaymentMethod, PaymentStatus
from app.seed import seed_plans
from app.services.order_service import fulfill_subscription_payment
from app.services.payment_service import sync_crypto_payment_status
from app.services.subscription_service import expire_due_subscriptions
from app.services.referral_service import backfill_missing_referral_codes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

settings = get_settings()


def register_routers(dp: Dispatcher) -> None:
    dp.include_router(start_router)
    dp.include_router(prizes_router)
    dp.include_router(admin_router)
    dp.include_router(payments_router)
    dp.include_router(polls_router)
    dp.include_router(subscriptions_router)


async def ensure_schema() -> None:
    async with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            rows = await conn.execute(text("PRAGMA table_info(payments)"))
            columns = {row[1] for row in rows.fetchall()}
        else:
            rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'payments'"
                )
            )
            columns = {row[0] for row in rows.fetchall()}

        if "payment_method" not in columns:
            await conn.execute(
                text("ALTER TABLE payments ADD COLUMN payment_method VARCHAR(50) DEFAULT 'stars' NOT NULL")
            )
        if "prize_award_id" not in columns:
            await conn.execute(
                text("ALTER TABLE payments ADD COLUMN prize_award_id INTEGER NULL")
            )

        if dialect == "sqlite":
            prize_rows = await conn.execute(text("PRAGMA table_info(prize_awards)"))
            prize_columns = {row[1] for row in prize_rows.fetchall()}
        else:
            prize_rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'prize_awards'"
                )
            )
            prize_columns = {row[0] for row in prize_rows.fetchall()}

        if "is_burned" not in prize_columns:
            await conn.execute(text("ALTER TABLE prize_awards ADD COLUMN is_burned BOOLEAN DEFAULT 0 NOT NULL"))
        if "burned_at" not in prize_columns:
            await conn.execute(text("ALTER TABLE prize_awards ADD COLUMN burned_at DATETIME NULL"))

        if dialect == "sqlite":
            user_rows = await conn.execute(text("PRAGMA table_info(users)"))
            user_columns = {row[1] for row in user_rows.fetchall()}
        else:
            user_rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users'"
                )
            )
            user_columns = {row[0] for row in user_rows.fetchall()}

        if "referral_code" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN referral_code VARCHAR(64) NULL"))
        if "referred_by_user_id" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN referred_by_user_id INTEGER NULL"))
        if "referred_at" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN referred_at DATETIME NULL"))
        if "referral_bonus_granted_at" not in user_columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN referral_bonus_granted_at DATETIME NULL"))


async def create_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await ensure_schema()

    async with SessionLocal() as session:
        await seed_plans(session)
        await backfill_missing_referral_codes(session)


async def expired_subscriptions_job(bot: Bot) -> None:
    async with SessionLocal() as session:
        expired_count = await expire_due_subscriptions(session, bot)
        if expired_count:
            logging.info("Expired subscriptions processed: %s", expired_count)


async def pending_crypto_payments_job(bot: Bot) -> None:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(Payment).where(
                Payment.status == PaymentStatus.PENDING,
                Payment.payment_method == PaymentMethod.CRYPTOBOT,
                Payment.provider_payment_charge_id.is_not(None),
            )
        )
        payments = list(rows)

        for payment in payments:
            try:
                payment, is_new = await sync_crypto_payment_status(session, payment)
                if payment is None or not is_new:
                    continue
                user, plan, subscription, access_links = await fulfill_subscription_payment(session, bot, payment)
                if user is None or plan is None or subscription is None:
                    continue
                links_text = "\n".join(access_links)
                ends_at_text = subscription.ends_at.strftime("%Y-%m-%d %H:%M UTC")
                access_label = "Ссылка для входа" if len(access_links) == 1 else "Ссылки для входа"
                access_note = "Ссылка одноразовая и ограничена по времени." if len(access_links) == 1 else "Каждая ссылка одноразовая и ограничена по времени."
                await bot.send_message(
                    user.telegram_id,
                    "Оплата через CryptoBot подтверждена ✅\n\n"
                    f"Тариф: {plan.title}\n"
                    f"Подписка активна до: {ends_at_text}\n\n"
                    f"{access_label}:\n{links_text}\n\n"
                    f"{access_note}",
                )
            except Exception:
                logging.exception("Failed to process pending crypto payment id=%s", payment.id)


async def session_middleware(handler, event, data):
    async with SessionLocal() as session:
        data["session"] = session
        return await handler(event, data)


async def main() -> None:
    await create_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(session_middleware)
    register_routers(dp)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        expired_subscriptions_job,
        trigger="interval",
        minutes=settings.check_expired_every_minutes,
        kwargs={"bot": bot},
    )
    if settings.crypto_pay_enabled:
        scheduler.add_job(
            pending_crypto_payments_job,
            trigger="interval",
            minutes=settings.check_pending_crypto_every_minutes,
            kwargs={"bot": bot},
        )
    scheduler.start()

    logging.info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

