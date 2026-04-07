from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ChannelScope(StrEnum):
    CHANNEL_1 = "channel_1"
    CHANNEL_2 = "channel_2"
    BUNDLE = "bundle"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class PaymentMethod(StrEnum):
    STARS = "stars"
    CRYPTOBOT = "cryptobot"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"


class PollStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class AnonymousQuestionStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    payments: Mapped[list[Payment]] = relationship(back_populates="user")
    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="user")
    anonymous_questions: Mapped[list[AnonymousQuestion]] = relationship(back_populates="user", cascade="all, delete-orphan")
    question_deliveries: Mapped[list[AnonymousQuestionDelivery]] = relationship(back_populates="admin_user", cascade="all, delete-orphan")
    created_polls: Mapped[list[Poll]] = relationship(back_populates="creator")
    poll_votes: Mapped[list[PollVote]] = relationship(back_populates="user")


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("code", name="uq_plan_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(1000))
    channel_scope: Mapped[str] = mapped_column(String(50))
    duration_days: Mapped[int] = mapped_column(Integer)
    price_xtr: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(default=True)

    payments: Mapped[list[Payment]] = relationship(back_populates="plan")
    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="plan")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    payload: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    payment_method: Mapped[str] = mapped_column(String(50), default=PaymentMethod.STARS)
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_payment_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_xtr: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), default=PaymentStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="payments")
    plan: Mapped[Plan] = relationship(back_populates="payments")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(50), default=SubscriptionStatus.ACTIVE)
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="subscriptions")
    plan: Mapped[Plan] = relationship(back_populates="subscriptions")


class AnonymousQuestion(Base):
    __tablename__ = "anonymous_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default=AnonymousQuestionStatus.PENDING, index=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="anonymous_questions")
    deliveries: Mapped[list[AnonymousQuestionDelivery]] = relationship(back_populates="question", cascade="all, delete-orphan")


class AnonymousQuestionDelivery(Base):
    __tablename__ = "anonymous_question_deliveries"
    __table_args__ = (UniqueConstraint("question_id", "admin_telegram_id", "admin_chat_id", name="uq_question_admin_delivery"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("anonymous_questions.id", ondelete="CASCADE"), index=True)
    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    admin_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    bot_message_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    question: Mapped[AnonymousQuestion] = relationship(back_populates="deliveries")
    admin_user: Mapped[User | None] = relationship(back_populates="question_deliveries")


class Poll(Base):
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(String(300))
    allows_multiple_answers: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default=PollStatus.ACTIVE, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    creator: Mapped[User] = relationship(back_populates="created_polls")
    options: Mapped[list[PollOption]] = relationship(back_populates="poll", cascade="all, delete-orphan")
    votes: Mapped[list[PollVote]] = relationship(back_populates="poll", cascade="all, delete-orphan")


class PollOption(Base):
    __tablename__ = "poll_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(200))

    poll: Mapped[Poll] = relationship(back_populates="options")
    votes: Mapped[list[PollVote]] = relationship(back_populates="option", cascade="all, delete-orphan")


class PollVote(Base):
    __tablename__ = "poll_votes"
    __table_args__ = (UniqueConstraint("poll_id", "user_id", "option_id", name="uq_poll_vote"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id", ondelete="CASCADE"), index=True)
    option_id: Mapped[int] = mapped_column(ForeignKey("poll_options.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    poll: Mapped[Poll] = relationship(back_populates="votes")
    option: Mapped[PollOption] = relationship(back_populates="votes")
    user: Mapped[User] = relationship(back_populates="poll_votes")
