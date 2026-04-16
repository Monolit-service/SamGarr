from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.keyboards import prize_menu_keyboard
from app.services.plan_service import get_active_plans
from app.services.prize_service import (
    burn_previous_prizes,
    can_spin_now,
    create_prize_award,
    draw_prize,
    get_active_discount_award,
    get_prize_eligibility,
    has_active_subscription_access,
)
from app.services.subscription_service import grant_free_days_subscription
from app.services.user_service import get_or_create_user
from app.services.payment_service import (
    consume_prize_spin_purchase,
    create_prize_spin_purchase,
    get_available_prize_spin_purchase,
    send_prize_spin_invoice,
)

router = Router()
settings = get_settings()


async def _get_primary_plan(session: AsyncSession):
    plans = await get_active_plans(session)
    return plans[0] if plans else None



@router.callback_query(F.data == "prize_menu")
async def prize_menu_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    eligibility = await get_prize_eligibility(session, user)
    can_spin, next_time = await can_spin_now(session, user.id, settings.prize_spin_cooldown_hours)
    active_discount = await get_active_discount_award(session, user.id)
    has_subscription_access = await has_active_subscription_access(session, user.id)
    paid_spin_access = await get_available_prize_spin_purchase(session, user.id)
    has_any_access = has_subscription_access or paid_spin_access is not None

    text = [
        "<b>🎁 Рандомайзер призов</b>",
        "",
        "В призах доступны:",
        "• подписка на 1 день",
        "• скидка 5% на приват",
        "• скидка 10% на приват",
        "• скидка 25% на приват",
        "• самый редкий приз — месячный доступ к привату",
        "",
        "Доступ к рандомайзеру открывается либо при активной подписке, либо после отдельной оплаты доступа.",
    ]
    if active_discount:
        text.extend(["", f"Активный неиспользованный приз: <b>{active_discount.prize_title}</b>"])

    if has_subscription_access:
        text.extend(["", "✅ У тебя есть доступ к рандомайзеру через активную подписку."])
    elif paid_spin_access is not None:
        text.extend(["", "✅ У тебя есть 1 оплаченный доступ к рандомайзеру. Он спишется при следующем прокруте."])
    else:
        text.extend(["", f"🔒 Сейчас доступа нет. Можно купить 1 вход за <b>{settings.prize_access_price_xtr} XTR</b>."])

    if not eligibility.allowed:
        text.extend(["", f"<b>Антиабуз-защита:</b> {eligibility.reason}"])
        if eligibility.profile_ready_at:
            text.append(f"Рандомайзер откроется после: <b>{eligibility.profile_ready_at.strftime('%Y-%m-%d %H:%M UTC')}</b>")
    elif not has_any_access:
        text.extend(["", "Чтобы крутить рандомайзер, сначала оформи подписку или купи разовый доступ ниже."])
    elif can_spin:
        text.extend(["", "Рандомайзер готов. Нажми кнопку ниже."])
    elif next_time:
        text.extend(["", f"Следующая попытка будет доступна после: <b>{next_time.strftime('%Y-%m-%d %H:%M UTC')}</b>"])

    await callback.message.edit_text(
        "\n".join(text),
        reply_markup=prize_menu_keyboard(
            can_spin=can_spin and eligibility.allowed and has_any_access,
            can_buy_access=not has_subscription_access and paid_spin_access is None,
            access_price_xtr=settings.prize_access_price_xtr,
        ),
    )
    await callback.answer()



@router.callback_query(F.data == "prize_buy_access")
async def prize_buy_access_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    if await has_active_subscription_access(session, user.id):
        await callback.answer("У тебя уже есть доступ через активную подписку.", show_alert=True)
        return
    existing_purchase = await get_available_prize_spin_purchase(session, user.id)
    if existing_purchase is not None:
        await callback.answer("У тебя уже есть оплаченный доступ. Просто нажми «Крутить рандомайзер».", show_alert=True)
        return
    purchase = await create_prize_spin_purchase(session, user, amount_xtr=settings.prize_access_price_xtr)
    await send_prize_spin_invoice(callback.message, purchase)
    await callback.answer()


@router.callback_query(F.data == "prize_cooldown")
async def prize_cooldown_handler(callback: CallbackQuery) -> None:
    await callback.answer("Сейчас крутить ещё нельзя — попробуй позже.", show_alert=True)


@router.callback_query(F.data == "prize_spin")
async def prize_spin_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    eligibility = await get_prize_eligibility(session, user)
    if not eligibility.allowed:
        message = eligibility.reason or "Сейчас участие недоступно."
        if eligibility.profile_ready_at:
            message = f"{message}\n\nОткроется после {eligibility.profile_ready_at.strftime('%Y-%m-%d %H:%M UTC')}"
        await callback.answer(message, show_alert=True)
        return

    has_subscription_access = await has_active_subscription_access(session, user.id)
    paid_spin_access = await get_available_prize_spin_purchase(session, user.id)
    if not has_subscription_access and paid_spin_access is None:
        await callback.answer(
            f"Чтобы открыть рандомайзер, нужна активная подписка или разовая оплата {settings.prize_access_price_xtr} XTR.",
            show_alert=True,
        )
        return

    allowed, next_time = await can_spin_now(session, user.id, settings.prize_spin_cooldown_hours)
    if not allowed:
        formatted = next_time.strftime('%Y-%m-%d %H:%M UTC') if next_time else "позже"
        await callback.answer(f"Следующий прокрут будет доступен после {formatted}", show_alert=True)
        return

    if not has_subscription_access:
        await consume_prize_spin_purchase(session, paid_spin_access)

    burned_awards = await burn_previous_prizes(session, user.id)

    prize = draw_prize()
    await create_prize_award(session, user, prize)

    if prize.free_days > 0:
        plan = await _get_primary_plan(session)
        if plan is None:
            await callback.message.edit_text(
                f"<b>Твой приз: {prize.title}</b>\n\nНо сейчас в боте нет активного тарифа для выдачи доступа.",
                reply_markup=prize_menu_keyboard(can_spin=False),
            )
            await callback.answer()
            return

        subscription, access_links = await grant_free_days_subscription(
            session, user, plan, prize.free_days, callback.message.bot
        )
        links_text = "\n".join(access_links) if access_links else "Напиши администратору для выдачи доступа."
        rare = "\n🔥 Это самый редкий приз!" if prize.is_rarest else ""
        await callback.message.edit_text(
            (
            (f"<b>🎉 Ты выиграл: {prize.title}</b>{rare}\n\n")
            + ("⚠️ Предыдущий приз сгорел при этом прокруте.\n\n" if burned_awards else "")
            + f"Доступ уже активирован до: <b>{subscription.ends_at.strftime('%Y-%m-%d %H:%M UTC')}</b>\n\n"
            + f"Ссылка для входа:\n{links_text}"
        ),
            reply_markup=prize_menu_keyboard(can_spin=False),
        )
        await callback.answer()
        return

    rare = "\n🔥 Это самый редкий приз!" if prize.is_rarest else ""
    await callback.message.edit_text(
        (
            (f"<b>🎉 Ты выиграл: {prize.title}</b>{rare}\n\n")
            + ("⚠️ Предыдущий приз сгорел при этом прокруте.\n\n" if burned_awards else "")
            + "Приз сохранён за тобой и применится автоматически при следующей покупке привата.\n"
            + f"Следующий прокрут будет доступен через {settings.prize_spin_cooldown_hours} ч."
        ),
        reply_markup=prize_menu_keyboard(can_spin=False),
    )
    await callback.answer()
