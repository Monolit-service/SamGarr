from __future__ import annotations

from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnonymousQuestion, AnonymousQuestionDelivery, AnonymousQuestionStatus, User


async def create_anonymous_question(session: AsyncSession, user: User, question_text: str) -> AnonymousQuestion:
    question = AnonymousQuestion(
        user_id=user.id,
        question_text=question_text,
        status=AnonymousQuestionStatus.PENDING,
    )
    session.add(question)
    await session.commit()
    await session.refresh(question)
    return question


async def get_question_by_id(session: AsyncSession, question_id: int) -> AnonymousQuestion | None:
    return await session.get(AnonymousQuestion, question_id)


async def register_question_delivery(
    session: AsyncSession,
    *,
    question: AnonymousQuestion,
    admin_telegram_id: int,
    admin_chat_id: int,
    bot_message_id: int,
    admin_user: User | None = None,
) -> AnonymousQuestionDelivery:
    delivery = AnonymousQuestionDelivery(
        question_id=question.id,
        admin_user_id=admin_user.id if admin_user else None,
        admin_telegram_id=admin_telegram_id,
        admin_chat_id=admin_chat_id,
        bot_message_id=bot_message_id,
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    return delivery


async def get_question_by_delivery(
    session: AsyncSession,
    *,
    admin_telegram_id: int,
    admin_chat_id: int,
    bot_message_id: int,
) -> AnonymousQuestion | None:
    stmt = (
        select(AnonymousQuestion)
        .join(AnonymousQuestionDelivery, AnonymousQuestionDelivery.question_id == AnonymousQuestion.id)
        .where(
            AnonymousQuestionDelivery.admin_telegram_id == admin_telegram_id,
            AnonymousQuestionDelivery.admin_chat_id == admin_chat_id,
            AnonymousQuestionDelivery.bot_message_id == bot_message_id,
        )
        .limit(1)
    )
    return await session.scalar(stmt)


async def answer_question(
    session: AsyncSession,
    question: AnonymousQuestion,
    *,
    answer_text: str,
    answered_by_telegram_id: int,
) -> AnonymousQuestion:
    question.answer_text = answer_text
    question.answered_by_telegram_id = answered_by_telegram_id
    question.answered_at = datetime.utcnow()
    question.status = AnonymousQuestionStatus.ANSWERED
    await session.commit()
    await session.refresh(question)
    return question
