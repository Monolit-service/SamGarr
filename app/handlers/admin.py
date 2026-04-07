from __future__ import annotations

import asyncio
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards import admin_panel_keyboard
from app.services.admin_service import create_database_backup, get_admin_stats, is_admin_user
from app.utils.text import format_admin_panel_text

router = Router()


async def _require_admin_message(message: Message) -> bool:
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("Эта команда доступна только администратору.")
        return False
    return True


async def _require_admin_callback(callback: CallbackQuery) -> bool:
    if not callback.from_user or not is_admin_user(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return False
    return True


@router.message(Command("admin"))
async def admin_command(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not await _require_admin_message(message):
        return
    await state.clear()
    stats = await get_admin_stats(session)
    await message.answer(format_admin_panel_text(stats), reply_markup=admin_panel_keyboard())


@router.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not await _require_admin_callback(callback):
        return
    await state.clear()
    stats = await get_admin_stats(session)
    await callback.message.edit_text(format_admin_panel_text(stats), reply_markup=admin_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin_callback(callback):
        return
    stats = await get_admin_stats(session)
    await callback.message.edit_text(format_admin_panel_text(stats), reply_markup=admin_panel_keyboard())
    await callback.answer("Статистика обновлена")


@router.callback_query(F.data == "admin_backup")
async def admin_backup_callback(callback: CallbackQuery) -> None:
    if not await _require_admin_callback(callback):
        return

    progress = await callback.message.edit_text(
        "Собираю бэкап проекта…\n"
        "В архив попадут код и рабочая БД, но без .env и служебного мусора.",
        reply_markup=admin_panel_keyboard(is_busy=True),
    )
    await callback.answer()

    backup_path: Path | None = None
    try:
        backup_path = await asyncio.to_thread(create_database_backup)
        with backup_path.open("rb") as backup_file:
            await callback.message.answer_document(
                backup_file,
                caption=(
                    "📦 Бэкап проекта готов\n"
                    "В архив включены код и база данных."
                ),
            )
        await progress.edit_text(
            "Бэкап базы данных отправлен ✅",
            reply_markup=admin_panel_keyboard(),
        )
    except Exception as exc:
        await progress.edit_text(
            f"Не удалось собрать бэкап: <code>{str(exc)}</code>",
            reply_markup=admin_panel_keyboard(),
        )
    finally:
        if backup_path is not None:
            try:
                backup_path.unlink(missing_ok=True)
            except Exception:
                pass
