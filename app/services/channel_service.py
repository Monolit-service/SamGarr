from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app.config import get_settings
from app.models import ChannelScope

settings = get_settings()


def resolve_channel_targets(channel_scope: str) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []

    if settings.channel_1_id:
        targets.append((settings.channel_1_id, settings.channel_1_name))

    second_channel = []
    if settings.channel_2_id:
        second_channel.append((settings.channel_2_id, settings.channel_2_name or "Канал 2"))

    if channel_scope == ChannelScope.CHANNEL_1:
        return targets[:1]
    if channel_scope == ChannelScope.CHANNEL_2:
        return second_channel
    return targets[:1] + second_channel


async def create_access_links(bot: Bot, channel_scope: str) -> list[str]:
    links: list[str] = []
    expire_date = datetime.utcnow() + timedelta(hours=settings.invite_link_expire_hours)

    for chat_id, channel_name in resolve_channel_targets(channel_scope):
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            expire_date=expire_date,
            member_limit=1,
            creates_join_request=False,
            name=f"paid_sub_{channel_name}",
        )
        links.append(f"{channel_name}: {invite.invite_link}")
    return links


async def revoke_access(bot: Bot, user_id: int, channel_scope: str) -> None:
    for chat_id, _ in resolve_channel_targets(channel_scope):
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        except TelegramBadRequest:
            continue
