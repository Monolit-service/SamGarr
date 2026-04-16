from html import escape

from app.models import Plan, Poll, PollStatus, Subscription, SubscriptionStatus, User
from app.services.admin_service import AdminStats
from app.services.poll_service import PollStats


def format_subscription_line(subscription: Subscription, plan: Plan) -> str:
    status_emoji = "✅" if subscription.status == SubscriptionStatus.ACTIVE else "⛔"
    ends_at = subscription.ends_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"{status_emoji} {plan.title}\nДо: {ends_at}"


def format_welcome_text(channel_1_name: str, channel_2_name: str) -> str:
    return (
        "Привет👋 Рад, что ты здесь🙂‍↕️\n\n"
        "Это пространство без цензуры, фильтров и лишних глаз. "
        "Твоя возможность узнать меня получше🤫\n\n"
        "Правила просты:\n"
        "• Хочешь больше горячего контента? Тебе в Приват.\n"
        "• Есть ко мне личный вопрос? Пиши анонимно.\n"
        "• Хочешь сказать «спасибо»? Я не откажусь от доната.\n\n"
        "С чего начнем наше знакомство?"
    )


def format_profile_text(
    user: User | None,
    subscription_rows: list[tuple[Subscription, Plan]],
    *,
    referral_link: str | None = None,
    referral_count: int = 0,
    referral_bonus_days: int = 0,
) -> str:
    username = f"@{user.username}" if user and user.username else "—"
    full_name = user.full_name if user and user.full_name else "—"
    telegram_id = user.telegram_id if user else "—"

    active_rows = [
        (subscription, plan)
        for subscription, plan in subscription_rows
        if subscription.status == SubscriptionStatus.ACTIVE
    ]

    text = [
        "<b>Мой профиль</b>",
        f"ID: <code>{telegram_id}</code>",
        f"Username: {username}",
        f"Имя: {full_name}",
        f"Активных подписок: {len(active_rows)}",
        f"Приглашено по ссылке: {referral_count}",
        f"Бонусных дней по рефералке: {referral_bonus_days}",
    ]

    if referral_link:
        text.append(f"\n<b>Твоя реферальная ссылка</b>\n<code>{escape(referral_link)}</code>")

    if not subscription_rows:
        text.append("\nПодписок пока нет.")
        return "\n".join(text)

    text.append("\n<b>Подписки</b>")
    for subscription, plan in subscription_rows:
        text.append(format_subscription_line(subscription, plan))

    return "\n\n".join(text)


def format_poll_preview(question: str, options: list[str], allows_multiple_answers: bool) -> str:
    answer_mode = "несколько вариантов" if allows_multiple_answers else "только один вариант"
    lines = [
        "<b>Предпросмотр опроса</b>",
        f"Вопрос: {escape(question)}",
        f"Режим ответа: {answer_mode}",
        "",
        "<b>Варианты:</b>",
    ]
    for index, option in enumerate(options, start=1):
        lines.append(f"{index}. {escape(option)}")
    return "\n".join(lines)


def format_poll_message(poll: Poll, stats: PollStats) -> str:
    is_closed = poll.status == PollStatus.CLOSED
    header = "📣 <b>Анонимный опрос</b>"
    if is_closed:
        header = "📣 <b>Анонимный опрос завершён</b>"

    lines = [header, "", escape(poll.question), "", "<b>Результаты</b>"]
    sorted_options = sorted(poll.options, key=lambda item: item.position)
    total = stats.total_votes
    for option in sorted_options:
        count = stats.option_counts.get(option.id, 0)
        percent = 0
        if total > 0:
            percent = round(count / total * 100)
        lines.append(f"• {escape(option.text)} — {count} ({percent}%)")

    lines.append("")
    lines.append(f"Участников: {stats.total_voters}")
    if is_closed:
        lines.append("Голосование закрыто.")
    else:
        action_text = "Можно выбрать несколько вариантов." if poll.allows_multiple_answers else "Можно выбрать только один вариант."
        lines.append(f"Нажми на кнопку ниже, чтобы проголосовать. {action_text}")

    return "\n".join(lines)


def format_admin_poll_summary(poll: Poll, stats: PollStats) -> str:
    lines = [format_poll_message(poll, stats), "", f"ID опроса: <code>{poll.id}</code>"]
    return "\n".join(lines)


def format_admin_panel_text(stats: AdminStats) -> str:
    return (
        "<b>🛠 Админ-панель</b>\n\n"
        f"Пользователей в базе: <b>{stats.total_users}</b>\n"
        f"Активных подписок: <b>{stats.active_subscriptions}</b>\n"
        f"Успешных оплат: <b>{stats.paid_payments}</b>\n"
        f"Ожидают ответа вопросов: <b>{stats.pending_questions}</b>\n"
        f"Активных опросов: <b>{stats.active_polls}</b>\n\n"
        "Через эту панель можно скачать актуальный бэкап проекта."
    )
