from aiogram import F, Router
import re
from html import escape
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.keyboards import (
    admin_question_answer_keyboard,
    admin_question_reply_keyboard,
    ask_question_keyboard,
    donation_methods_keyboard,
    main_menu,
    plan_payment_keyboard,
    plans_keyboard,
)
from app.services.admin_service import is_admin_user
from app.services.plan_service import get_active_plans, get_plan_by_id
from app.services.question_service import (
    answer_question,
    create_anonymous_question,
    get_question_by_delivery,
    get_question_by_id,
    register_question_delivery,
)
from app.services.user_service import get_or_create_user
from app.services.referral_service import attach_referrer_from_start_argument
from app.services.prize_service import apply_discount_to_price, get_active_discount_award
from app.models import AnonymousQuestionStatus, User
from app.utils.text import format_welcome_text


class AskQuestionStates(StatesGroup):
    waiting_question = State()


class AdminAnswerQuestionStates(StatesGroup):
    waiting_answer = State()

router = Router()
settings = get_settings()
QUESTION_ID_RE = re.compile(r"ID вопроса:\s*(\d+)")


@router.message(CommandStart())
async def start_handler(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    db_user = await get_or_create_user(
        session=session,
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )
    start_argument = None
    if message.text and " " in message.text:
        start_argument = message.text.split(" ", maxsplit=1)[1].strip()
    await attach_referrer_from_start_argument(session, db_user, start_argument)
    text = format_welcome_text(settings.channel_1_name, settings.channel_2_name)
    await message.answer(text, reply_markup=main_menu(is_admin=is_admin_user(user.id)))


@router.callback_query(lambda c: c.data == "menu")
async def menu_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        format_welcome_text(settings.channel_1_name, settings.channel_2_name),
        reply_markup=main_menu(is_admin=is_admin_user(callback.from_user.id if callback.from_user else None)),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "show_plans")
async def show_plans_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    plans = await get_active_plans(session)
    await callback.message.edit_text("Выбери тариф для доступа в приват:", reply_markup=plans_keyboard(plans))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("plan:"))
async def plan_details_handler(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    plan_id = int(callback.data.split(":", maxsplit=1)[1])
    plan = await get_plan_by_id(session, plan_id)
    if plan is None or not plan.is_active:
        await callback.answer("Тариф не найден или отключён", show_alert=True)
        return

    price_line = f"Цена в Telegram: {plan.price_xtr} ⭐"
    if callback.from_user:
        user = await get_or_create_user(
            session=session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name,
        )
        discounted_amount, discount_award = await apply_discount_to_price(session, user.id, plan.price_xtr)
        if discount_award is not None and discounted_amount != plan.price_xtr:
            price_line = (
                f"Цена в Telegram: <s>{plan.price_xtr} ⭐</s> → <b>{discounted_amount} ⭐</b>\n"
                f"Активный приз: {escape(discount_award.prize_title)}"
            )

    text = (
        f"<b>{escape(plan.title)}</b>\n\n"
        f"{escape(plan.description)}\n\n"
        f"Срок: {plan.duration_days} дней\n"
        f"{price_line}\n\n"
        "Выбери способ оплаты:"
    )
    await callback.message.edit_text(text, reply_markup=plan_payment_keyboard(plan))
    await callback.answer()


@router.callback_query(lambda c: c.data == "show_donations")
async def donations_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Поддержать проект можно звёздами внутри Telegram или через CryptoBot.\n"
        "После выбора способа бот попросит ввести любую сумму доната.",
        reply_markup=donation_methods_keyboard(allow_test_buttons=settings.is_test_payments_enabled_for(callback.from_user.id if callback.from_user else None)),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "ask_bold_question")
async def ask_bold_question_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AskQuestionStates.waiting_question)
    await callback.message.edit_text(
        "Я слушаю. Напиши свой вопрос следующим сообщением. Никто - даже я - не узнает, кто автор.\n"
        "Постарайся спросить что-то действительно интересное.",
        reply_markup=ask_question_keyboard(),
    )
    await callback.answer()


@router.message(AskQuestionStates.waiting_question, F.text)
async def receive_bold_question(message: Message, session: AsyncSession, state: FSMContext) -> None:
    question = (message.text or "").strip()
    if question.lower() in {"отмена", "/cancel", "cancel"}:
        await state.clear()
        await message.answer(format_welcome_text(settings.channel_1_name, settings.channel_2_name), reply_markup=main_menu(is_admin=is_admin_user(message.from_user.id if message.from_user else None)))
        return

    if len(question) < 3:
        await message.answer("Вопрос слишком короткий. Напиши чуть подробнее.", reply_markup=ask_question_keyboard())
        return

    if not settings.admin_ids:
        await state.clear()
        await message.answer("Сейчас приём вопросов временно недоступен.", reply_markup=main_menu(is_admin=is_admin_user(message.from_user.id if message.from_user else None)))
        return

    user = await get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    saved_question = await create_anonymous_question(session, user, question)

    sent_count = 0
    admin_text = (
        "❓ <b>Новый анонимный вопрос</b>\n"
        f"ID вопроса: {saved_question.id}\n\n"
        f"{escape(saved_question.question_text)}\n\n"
        "Ответь на это сообщение текстом, и бот анонимно отправит ответ автору."
    )
    for admin_id in settings.admin_ids:
        try:
            admin_user = await get_or_create_user(
                session=session,
                telegram_id=admin_id,
                username=None,
                full_name=None,
            )
            sent = await message.bot.send_message(
                admin_id,
                admin_text,
                reply_markup=admin_question_reply_keyboard(saved_question.id),
            )
            await register_question_delivery(
                session,
                question=saved_question,
                admin_telegram_id=admin_id,
                admin_chat_id=sent.chat.id,
                bot_message_id=sent.message_id,
                admin_user=admin_user,
            )
            sent_count += 1
        except Exception:
            continue

    await state.clear()
    if sent_count:
        await message.answer(
            "Принято. Твой вопрос улетел ко мне в сейф. Если он меня зацепит - отвечу на него лично или разберу в основном канале. Жди.",
            reply_markup=main_menu(is_admin=is_admin_user(message.from_user.id if message.from_user else None)),
        )
    else:
        await message.answer(
            "Не удалось доставить вопрос администраторам. Попробуй позже.",
            reply_markup=main_menu(is_admin=is_admin_user(message.from_user.id if message.from_user else None)),
        )


@router.callback_query(lambda c: c.data and c.data.startswith("answer_question:"))
async def admin_answer_question_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if callback.from_user.id not in settings.admin_ids:
        await callback.answer()
        return

    question_id = int(callback.data.split(":", maxsplit=1)[1])
    question = await get_question_by_id(session, question_id)
    if question is None:
        await callback.answer("Вопрос не найден", show_alert=True)
        return

    if str(question.status) == str(AnonymousQuestionStatus.ANSWERED):
        await callback.answer("На этот вопрос уже ответили", show_alert=True)
        return

    await state.set_state(AdminAnswerQuestionStates.waiting_answer)
    await state.update_data(question_id=question.id)
    await callback.message.answer(
        "Напиши ответ следующим сообщением. Бот анонимно отправит его автору вопроса.",
        reply_markup=admin_question_answer_keyboard(question.id),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("answer_question_cancel:"))
async def admin_answer_question_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id not in settings.admin_ids:
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        format_welcome_text(settings.channel_1_name, settings.channel_2_name),
        reply_markup=main_menu(is_admin=True),
    )
    await callback.answer("Ответ отменён")


@router.message(AdminAnswerQuestionStates.waiting_answer, F.text)
async def admin_answer_question_text(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if message.from_user.id not in settings.admin_ids:
        await state.clear()
        return

    answer_text_raw = (message.text or "").strip()
    if answer_text_raw.lower() in {"отмена", "/cancel", "cancel"}:
        await state.clear()
        await message.answer(
            format_welcome_text(settings.channel_1_name, settings.channel_2_name),
            reply_markup=main_menu(is_admin=True),
        )
        return

    if not answer_text_raw:
        await message.answer(
            "Пришли ответ обычным текстом или нажми отмену.",
            reply_markup=admin_question_answer_keyboard((await state.get_data()).get("question_id", 0)),
        )
        return

    data = await state.get_data()
    question_id = data.get("question_id")
    if not question_id:
        await state.clear()
        await message.answer("Не удалось определить вопрос для ответа.")
        return

    question = await get_question_by_id(session, int(question_id))
    if question is None:
        await state.clear()
        await message.answer("Вопрос не найден в базе.")
        return

    if str(question.status) == str(AnonymousQuestionStatus.ANSWERED):
        await state.clear()
        await message.answer("На этот вопрос уже ответили.")
        return

    author = await session.get(User, question.user_id)
    if author is None:
        await state.clear()
        await message.answer("Не удалось определить автора вопроса.")
        return

    answer_text = (
        "💌 <b>Ответ на твой смелый вопрос</b>\n\n"
        f"{escape(answer_text_raw)}"
    )

    try:
        await message.bot.send_message(author.telegram_id, answer_text)
    except Exception as exc:
        await message.answer(f"Не удалось доставить ответ автору: <code>{escape(str(exc))}</code>")
        return

    await answer_question(
        session,
        question,
        answer_text=answer_text_raw,
        answered_by_telegram_id=message.from_user.id,
    )
    await state.clear()
    await message.answer(
        "Ответ анонимно отправлен автору вопроса ✅",
        reply_markup=main_menu(is_admin=True),
    )


@router.message(F.text, F.reply_to_message)
async def admin_answer_to_question(message: Message, session: AsyncSession) -> None:
    if message.from_user.id not in settings.admin_ids:
        return

    reply_msg = message.reply_to_message
    bot_message_id = getattr(reply_msg, "message_id", None)
    bot_chat_id = getattr(reply_msg.chat, "id", None) if getattr(reply_msg, "chat", None) else None

    question = None
    if bot_message_id is not None and bot_chat_id is not None:
        question = await get_question_by_delivery(
            session,
            admin_telegram_id=message.from_user.id,
            admin_chat_id=bot_chat_id,
            bot_message_id=bot_message_id,
        )

    if question is None:
        reply_text = getattr(reply_msg, "text", None) or getattr(reply_msg, "caption", None) or ""
        if not reply_text or "ID вопроса:" not in reply_text:
            return
        match = QUESTION_ID_RE.search(reply_text)
        if not match:
            return
        question = await get_question_by_id(session, int(match.group(1)))

    answer_text_raw = (message.text or "").strip()
    if not answer_text_raw:
        return

    if question is None:
        await message.reply("Не нашёл этот вопрос в базе.")
        return

    if str(question.status) == str(AnonymousQuestionStatus.ANSWERED):
        await message.reply("На этот вопрос уже отправлен ответ.")
        return

    author = await session.get(User, question.user_id)
    if author is None:
        await message.reply("Не удалось определить автора вопроса.")
        return

    answer_text = (
        "💌 <b>Ответ на твой смелый вопрос</b>\n\n"
        f"{escape(answer_text_raw)}"
    )
    try:
        await message.bot.send_message(author.telegram_id, answer_text)
    except Exception as exc:
        await message.reply(f"Не удалось доставить ответ автору: <code>{escape(str(exc))}</code>")
        return

    await answer_question(
        session,
        question,
        answer_text=answer_text_raw,
        answered_by_telegram_id=message.from_user.id,
    )
    await message.reply("Ответ анонимно отправлен автору вопроса ✅")


@router.message(AdminAnswerQuestionStates.waiting_answer)
async def admin_answer_question_non_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question_id = int(data.get("question_id", 0) or 0)
    await message.answer(
        "Пришли ответ обычным текстом или нажми отмену.",
        reply_markup=admin_question_answer_keyboard(question_id),
    )


@router.message(AskQuestionStates.waiting_question)
async def receive_non_text_bold_question(message: Message) -> None:
    await message.answer(
        "Пришли вопрос обычным текстовым сообщением.",
        reply_markup=ask_question_keyboard(),
    )
