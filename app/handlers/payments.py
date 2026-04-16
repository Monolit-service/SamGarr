from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards import (
    after_purchase_keyboard,
    crypto_donation_keyboard,
    crypto_invoice_keyboard,
    donation_input_keyboard,
)
from app.config import get_settings
from app.models import PaymentMethod
from app.services.order_service import fulfill_subscription_payment
from app.services.payment_service import (
    approve_pre_checkout,
    create_crypto_donation_invoice,
    create_crypto_invoice_for_payment,
    create_pending_payment,
    get_payment_by_id,
    mark_payment_paid,
    send_donation_invoice,
    send_plan_invoice,
    sync_crypto_payment_status,
    mark_prize_spin_purchase_paid,
)
from app.services.plan_service import get_plan_by_id
from app.services.user_service import get_or_create_user
from app.services.prize_service import apply_discount_to_price, redeem_discount_award

router = Router()
settings = get_settings()


class DonationStates(StatesGroup):
    waiting_stars_amount = State()
    waiting_crypto_amount = State()


async def _send_access_message(message: Message, session: AsyncSession, payment) -> None:
    user, plan, subscription, access_links, referral_bonus_granted, referrer, referrer_subscription = await fulfill_subscription_payment(session, message.bot, payment)
    if payment.prize_award_id:
        from app.models import PrizeAward
        award = await session.get(PrizeAward, payment.prize_award_id)
        await redeem_discount_award(session, award)
    if user is None or plan is None or subscription is None:
        await message.answer("Не удалось активировать подписку. Напиши администратору.")
        return

    links_text = "\n".join(access_links)
    ends_at_text = subscription.ends_at.strftime("%Y-%m-%d %H:%M UTC")
    access_label = "Ссылка для входа" if len(access_links) == 1 else "Ссылки для входа"
    access_note = "Ссылка одноразовая и ограничена по времени." if len(access_links) == 1 else "Каждая ссылка одноразовая и ограничена по времени."
    if not access_links:
        links_text = "Не удалось автоматически создать ссылку. Напиши администратору."
        access_label = "Доступ"
        access_note = ""

    text = (
        "Оплата прошла успешно ✅\n\n"
        f"Тариф: {plan.title}\n"
        f"Подписка активна до: {ends_at_text}\n\n"
        f"{access_label}:\n{links_text}"
    )
    if access_note:
        text += f"\n\n{access_note}"

    await message.answer(text, reply_markup=after_purchase_keyboard())

    if referral_bonus_granted and referrer is not None and referrer_subscription is not None:
        bonus_days = settings.referral_bonus_days
        bonus_until = referrer_subscription.ends_at.strftime("%Y-%m-%d %H:%M UTC")
        try:
            await message.bot.send_message(
                referrer.telegram_id,
                "🎉 По твоей реферальной ссылке пришёл новый оплативший пользователь.\n\n"
                f"Начислил тебе +{bonus_days} дня(ей) подписки.\n"
                f"Теперь доступ активен до: {bonus_until}",
            )
        except Exception:
            pass



def _test_payments_allowed(telegram_id: int | None) -> bool:
    return settings.is_test_payments_enabled_for(telegram_id)


async def _simulate_successful_subscription_payment(message: Message, session: AsyncSession, payment, method_label: str) -> None:
    payment, is_new = await mark_payment_paid(
        session=session,
        payload=payment.payload,
        telegram_payment_charge_id=f"test-{method_label.lower()}-{payment.id}",
        provider_payment_charge_id=f"test-{method_label.lower()}-{payment.id}",
    )
    if payment is None:
        await message.answer("Не удалось найти тестовый платёж.")
        return
    if not is_new:
        await message.answer("Этот тестовый платёж уже был обработан.")
        return
    await message.answer(f"🧪 Тестовая оплата {escape(method_label)} подтверждена.")
    await _send_access_message(message, session, payment)


@router.callback_query(F.data.startswith("buy_stars:"))
async def buy_stars_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    plan_id = int(callback.data.split(":", maxsplit=1)[1])
    plan = await get_plan_by_id(session, plan_id)
    if plan is None or not plan.is_active:
        await callback.answer("Тариф не найден или отключён", show_alert=True)
        return

    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    amount_xtr, discount_award = await apply_discount_to_price(session, user.id, plan.price_xtr)
    payment = await create_pending_payment(
        session,
        user,
        plan,
        payment_method=PaymentMethod.STARS,
        amount_xtr=amount_xtr,
        prize_award_id=discount_award.id if discount_award else None,
    )
    await send_plan_invoice(callback.message, payment, plan)
    await callback.answer()


@router.callback_query(F.data.startswith("buy_crypto:"))
async def buy_crypto_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    plan_id = int(callback.data.split(":", maxsplit=1)[1])
    plan = await get_plan_by_id(session, plan_id)
    if plan is None or not plan.is_active:
        await callback.answer("Тариф не найден или отключён", show_alert=True)
        return

    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    amount_xtr, discount_award = await apply_discount_to_price(session, user.id, plan.price_xtr)
    payment = await create_pending_payment(
        session,
        user,
        plan,
        payment_method=PaymentMethod.CRYPTOBOT,
        amount_xtr=amount_xtr,
        prize_award_id=discount_award.id if discount_award else None,
    )

    try:
        pay_url = await create_crypto_invoice_for_payment(session, payment, plan)
    except Exception as exc:
        await callback.answer("Не удалось создать счёт в CryptoBot", show_alert=True)
        await callback.message.answer(f"Ошибка: <code>{exc}</code>")
        return

    await callback.message.edit_text(
        "Счёт создан. Оплати его в CryptoBot, затем нажми «Проверить оплату».\n"
        "Если оплата уже прошла, доступ выдастся автоматически или после ручной проверки.",
        reply_markup=crypto_invoice_keyboard(pay_url, payment.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_crypto:"))
async def check_crypto_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    payment_id = int(callback.data.split(":", maxsplit=1)[1])
    payment = await get_payment_by_id(session, payment_id)
    if payment is None or payment.payment_method != PaymentMethod.CRYPTOBOT:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )
    if payment.user_id != user.id:
        await callback.answer("Это не ваш платёж", show_alert=True)
        return

    payment, is_new = await sync_crypto_payment_status(session, payment)
    if payment is None:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if not is_new and payment.status != "paid":
        await callback.answer("Оплата ещё не подтверждена", show_alert=True)
        return

    if is_new:
        await callback.message.edit_text("Оплата подтверждена, выдаю доступ…")
        await _send_access_message(callback.message, session, payment)
    else:
        await callback.answer("Этот платёж уже был обработан")

    await callback.answer()


@router.callback_query(F.data.startswith("test_pay:"))
async def test_payment_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    if not _test_payments_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Тестовый режим оплаты выключен", show_alert=True)
        return

    _, method, plan_id_raw = callback.data.split(":", maxsplit=2)
    plan_id = int(plan_id_raw)
    plan = await get_plan_by_id(session, plan_id)
    if plan is None or not plan.is_active:
        await callback.answer("Тариф не найден или отключён", show_alert=True)
        return

    user = await get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )

    payment_method = PaymentMethod.STARS if method == "stars" else PaymentMethod.CRYPTOBOT
    method_label = "Stars" if method == "stars" else "CryptoBot"
    amount_xtr, discount_award = await apply_discount_to_price(session, user.id, plan.price_xtr)
    payment = await create_pending_payment(
        session,
        user,
        plan,
        payment_method=payment_method,
        amount_xtr=amount_xtr,
        prize_award_id=discount_award.id if discount_award else None,
    )
    await _simulate_successful_subscription_payment(callback.message, session, payment, method_label)
    await callback.answer("Тестовая оплата обработана")


@router.callback_query(F.data == "test_donate:stars")
async def test_donate_stars_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not _test_payments_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Тестовый режим оплаты выключен", show_alert=True)
        return

    await callback.message.edit_text(
        "🧪 Тест доната Stars прошёл успешно.\n\n"
        "Оу, это было красиво. Спасибо за донат! ⚡️\n"
        "Обожаю такую взаимность. Обещаю пустить эти ресурсы на создание еще более горячего контента для тебя💎",
        reply_markup=after_purchase_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "test_donate:crypto")
async def test_donate_crypto_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if not _test_payments_allowed(callback.from_user.id if callback.from_user else None):
        await callback.answer("Тестовый режим оплаты выключен", show_alert=True)
        return

    await callback.message.edit_text(
        "🧪 Тест доната CryptoBot прошёл успешно.\n\n"
        "Оу, это было красиво. Спасибо за донат! ⚡️\n"
        "Обожаю такую взаимность. Обещаю пустить эти ресурсы на создание еще более горячего контента для тебя💎",
        reply_markup=after_purchase_keyboard(),
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    await approve_pre_checkout(pre_checkout_query)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    successful_payment = message.successful_payment
    payload = successful_payment.invoice_payload

    if payload.startswith("donate:stars:"):
        parts = payload.split(":")
        amount = parts[3] if len(parts) > 3 else ""
        thanks_text = (
            "Оу, это было красиво. Спасибо за донат! ⚡️\n"
            "Обожаю такую взаимность. Обещаю пустить эти ресурсы на создание еще более горячего контента для тебя💎"
        )
        if amount:
            thanks_text = (
                f"Оу, это было красиво. Спасибо за донат на {amount} ⭐! ⚡️\n"
                "Обожаю такую взаимность. Обещаю пустить эти ресурсы на создание еще более горячего контента для тебя💎"
            )
        await message.answer(thanks_text, reply_markup=after_purchase_keyboard())
        return

    payment, is_new = await mark_payment_paid(
        session=session,
        payload=payload,
        telegram_payment_charge_id=successful_payment.telegram_payment_charge_id,
        provider_payment_charge_id=successful_payment.provider_payment_charge_id,
    )

    if payment is None:
        await message.answer("Не удалось найти оплату. Напиши администратору.")
        return

    if not is_new:
        await message.answer("Этот платёж уже был обработан ранее.")
        return

    await _send_access_message(message, session, payment)


@router.callback_query(F.data == "donate:stars")
async def donate_stars_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DonationStates.waiting_stars_amount)
    await callback.message.edit_text(
        "Введи сумму доната в звёздах одним сообщением.\n\n"
        "Например: <code>250</code>",
        reply_markup=donation_input_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "donate:crypto")
async def donate_crypto_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(DonationStates.waiting_crypto_amount)
    await callback.message.edit_text(
        "Введи сумму доната через CryptoBot одним сообщением.\n\n"
        "Например: <code>5</code> или <code>12.5</code>.",
        reply_markup=donation_input_keyboard(),
    )
    await callback.answer()


@router.message(DonationStates.waiting_stars_amount)
async def donate_stars_amount_handler(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() in {"отмена", "/cancel", "cancel"}:
        await state.clear()
        await message.answer("Донат отменён.")
        return

    if not text.isdigit():
        await message.answer("Введи целое число звёзд, например: <code>250</code>.")
        return

    amount = int(text)
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if amount > 250000:
        await message.answer("Слишком большая сумма. Введи сумму поменьше.")
        return

    await state.clear()
    await send_donation_invoice(message, amount)


@router.message(DonationStates.waiting_crypto_amount)
async def donate_crypto_amount_handler(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(",", ".")
    if text.lower() in {"отмена", "/cancel", "cancel"}:
        await state.clear()
        await message.answer("Донат отменён.")
        return

    try:
        amount = Decimal(text)
    except InvalidOperation:
        await message.answer("Введи сумму числом, например: <code>5</code> или <code>12.5</code>.")
        return

    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if amount > Decimal("100000"):
        await message.answer("Слишком большая сумма. Введи сумму поменьше.")
        return

    amount_str = format(amount.normalize(), "f") if amount == amount.normalize() else format(amount, "f")
    await state.clear()

    try:
        pay_url = await create_crypto_donation_invoice(amount_str)
    except Exception as exc:
        await message.answer(f"Не удалось создать донат-счёт: <code>{exc}</code>")
        return

    await message.answer(
        "Спасибо за поддержку ❤️\nОткрой счёт в CryptoBot и отправь донат.",
        reply_markup=crypto_donation_keyboard(pay_url),
    )
