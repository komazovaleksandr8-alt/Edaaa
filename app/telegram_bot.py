import logging
from decimal import Decimal

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import User
from app.config import settings


logger = logging.getLogger(__name__)


# ============================================================
# DATABASE
# ============================================================

def get_session() -> Session:
    return SessionLocal()


# ============================================================
# USER
# ============================================================

def get_or_create_user(
    telegram_user,
) -> User:

    db = get_session()

    try:

        telegram_id = str(
            telegram_user.id
        )

        user = (
            db.query(User)
            .filter(
                User.telegram_id == telegram_id
            )
            .first()
        )

        if user:

            user.telegram_username = (
                telegram_user.username
            )

            db.commit()
            db.refresh(user)

            return user

        # ----------------------------------------------------
        # Create Telegram-only account
        # ----------------------------------------------------

        username = (
            telegram_user.username
        )

        email = (
            f"tg_{telegram_id}@edaaa.local"
        )

        # ----------------------------------------------------
        # Check existing email
        # ----------------------------------------------------

        existing = (
            db.query(User)
            .filter(
                User.email == email
            )
            .first()
        )

        if existing:

            existing.telegram_id = (
                telegram_id
            )

            existing.telegram_username = (
                username
            )

            db.commit()
            db.refresh(existing)

            return existing

        # ----------------------------------------------------
        # Password
        # ----------------------------------------------------

        from app.auth import hash_password

        password_hash = hash_password(
            telegram_id
        )

        # ----------------------------------------------------
        # Create user
        # ----------------------------------------------------

        user = User(
            email=email,
            password_hash=password_hash,
            telegram_id=telegram_id,
            telegram_username=username,
            is_active=True,
            is_admin=False,
        )

        db.add(user)

        db.commit()

        db.refresh(user)

        return user

    except Exception:

        db.rollback()

        logger.exception(
            "Failed to get or create Telegram user."
        )

        raise

    finally:

        db.close()


# ============================================================
# MAIN MENU
# ============================================================

def build_main_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "💰 Кошелёк",
                callback_data="wallet",
            ),
        ],

        [
            InlineKeyboardButton(
                "📥 Депозит",
                callback_data="deposit",
            ),
            InlineKeyboardButton(
                "📤 Вывод",
                callback_data="withdraw",
            ),
        ],

        [
            InlineKeyboardButton(
                "💵 Купить USDT",
                callback_data="buy_usdt",
            ),
            InlineKeyboardButton(
                "💸 Продать USDT",
                callback_data="sell_usdt",
            ),
        ],

        [
            InlineKeyboardButton(
                "📊 История",
                callback_data="history",
            ),
        ],

        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile",
            ),
        ],

        [
            InlineKeyboardButton(
                "🆘 Поддержка",
                callback_data="support",
            ),
        ],

    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    telegram_user = (
        update.effective_user
    )

    if not telegram_user:
        return

    try:

        user = get_or_create_user(
            telegram_user
        )

    except Exception:

        logger.exception(
            "Failed to create Telegram user."
        )

        if update.message:

            await update.message.reply_text(
                "❌ Произошла ошибка при "
                "создании аккаунта."
            )

        return

    text = (
        "👋 <b>Добро пожаловать "
        "в Edaaa Wallet!</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        "Выберите действие:"
    )

    if update.message:

        await update.message.reply_text(
            text,
            reply_markup=build_main_menu(),
            parse_mode="HTML",
        )


# ============================================================
# MENU
# ============================================================

async def show_menu(
    query,
):

    await query.edit_message_text(
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите действие:",
        reply_markup=build_main_menu(),
        parse_mode="HTML",
    )


# ============================================================
# WALLET
# ============================================================

async def wallet(
    query,
    telegram_user,
):

    db = get_session()

    try:

        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == str(telegram_user.id)
            )
            .first()
        )

        if not user:

            await query.edit_message_text(
                "❌ Пользователь не найден."
            )

            return

        from app.wallet_models import Wallet

        wallets = (
            db.query(Wallet)
            .filter(
                Wallet.user_id
                == user.id
            )
            .order_by(
                Wallet.id.asc()
            )
            .all()
        )

        if not wallets:

            text = (
                "💰 <b>Ваш кошелёк</b>\n\n"
                "Кошелёк пока не создан."
            )

        else:

            lines = [
                "💰 <b>Ваши кошельки</b>",
                "",
            ]

            for wallet_obj in wallets:

                lines.append(
                    f"🌐 Сеть: "
                    f"<b>{wallet_obj.network}</b>"
                )

                lines.append(
                    "📍 Адрес:"
                )

                lines.append(
                    f"<code>"
                    f"{wallet_obj.address}"
                    f"</code>"
                )

                lines.append("")

            text = "\n".join(
                lines
            )

        keyboard = [

            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="menu",
                )
            ]

        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="HTML",
        )

    finally:

        db.close()


# ============================================================
# DEPOSIT
# ============================================================

async def deposit(
    query,
    telegram_user,
):

    db = get_session()

    try:

        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == str(telegram_user.id)
            )
            .first()
        )

        if not user:

            await query.edit_message_text(
                "❌ Пользователь не найден."
            )

            return

        from app.wallet_models import Wallet

        wallet_obj = (
            db.query(Wallet)
            .filter(
                Wallet.user_id
                == user.id
            )
            .order_by(
                Wallet.id.asc()
            )
            .first()
        )

        if not wallet_obj:

            await query.edit_message_text(
                "❌ Кошелёк ещё не создан."
            )

            return

        text = (
            "📥 <b>Депозит</b>\n\n"
            "Отправьте ETH на адрес:\n\n"
            f"<code>{wallet_obj.address}</code>\n\n"
            f"🌐 Сеть: "
            f"<b>{wallet_obj.network}</b>\n\n"
            "⚠️ Отправляйте средства "
            "только через указанную сеть."
        )

        keyboard = [

            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="menu",
                )
            ]

        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="HTML",
        )

    finally:

        db.close()


# ============================================================
# WITHDRAW
# ============================================================

async def withdraw(
    query,
):

    keyboard = [

        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="menu",
            )
        ]

    ]

    await query.edit_message_text(
        "📤 <b>Вывод</b>\n\n"
        "Функция вывода находится "
        "в разработке.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="HTML",
    )


# ============================================================
# BUY USDT
# ============================================================

async def buy_usdt(
    query,
):

    keyboard = [

        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="menu",
            )
        ]

    ]

    await query.edit_message_text(
        "💵 <b>Купить USDT</b>\n\n"
        "Раздел покупки USDT находится "
        "в разработке.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="HTML",
    )


# ============================================================
# SELL USDT
# ============================================================

async def sell_usdt(
    query,
):

    keyboard = [

        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="menu",
            )
        ]

    ]

    await query.edit_message_text(
        "💸 <b>Продать USDT</b>\n\n"
        "Раздел продажи USDT находится "
        "в разработке.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="HTML",
    )


# ============================================================
# HISTORY
# ============================================================

async def history(
    query,
    telegram_user,
):

    db = get_session()

    try:

        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == str(telegram_user.id)
            )
            .first()
        )

        if not user:

            await query.edit_message_text(
                "❌ Пользователь не найден."
            )

            return

        from app.wallet_models import Wallet
        from app.transaction_models import (
            Transaction,
        )

        wallets = (
            db.query(Wallet)
            .filter(
                Wallet.user_id
                == user.id
            )
            .all()
        )

        if not wallets:

            text = (
                "📊 <b>История операций</b>\n\n"
                "Операций пока нет."
            )

        else:

            wallet_ids = [
                wallet_obj.id
                for wallet_obj in wallets
            ]

            transactions = (
                db.query(Transaction)
                .filter(
                    Transaction.wallet_id.in_(
                        wallet_ids
                    )
                )
                .order_by(
                    Transaction.created_at.desc()
                )
                .limit(20)
                .all()
            )

            if not transactions:

                text = (
                    "📊 <b>История операций</b>\n\n"
                    "Операций пока нет."
                )

            else:

                lines = [
                    "📊 <b>Последние операции</b>",
                    "",
                ]

                for tx in transactions:

                    lines.append(
                        f"• {tx.type} | "
                        f"{tx.asset} | "
                        f"{tx.amount} | "
                        f"{tx.status}"
                    )

                text = "\n".join(
                    lines
                )

        keyboard = [

            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="menu",
                )
            ]

        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="HTML",
        )

    finally:

        db.close()


# ============================================================
# PROFILE
# ============================================================

async def profile(
    query,
    telegram_user,
):

    db = get_session()

    try:

        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == str(telegram_user.id)
            )
            .first()
        )

        if not user:

            await query.edit_message_text(
                "❌ Пользователь не найден."
            )

            return

        username = (
            f"@{user.telegram_username}"
            if user.telegram_username
            else "не указан"
        )

        text = (
            "👤 <b>Профиль</b>\n\n"
            f"🆔 ID: "
            f"<code>{user.id}</code>\n"
            f"📱 Telegram: "
            f"<code>{user.telegram_id}</code>\n"
            f"👤 Username: "
            f"{username}\n"
            f"📧 Email: "
            f"{user.email}\n\n"
            f"📅 Регистрация: "
            f"{user.created_at}"
        )

        keyboard = [

            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="menu",
                )
            ]

        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            parse_mode="HTML",
        )

    finally:

        db.close()


# ============================================================
# SUPPORT
# ============================================================

async def support(
    query,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data[
        "waiting_for_support"
    ] = True

    keyboard = [

        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="menu",
            )
        ]

    ]

    await query.edit_message_text(
        "🆘 <b>Поддержка</b>\n\n"
        "Напишите сообщение с описанием "
        "вашей проблемы.\n\n"
        "После отправки оно будет передано "
        "в поддержку Edaaa.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        parse_mode="HTML",
    )


# ============================================================
# SUPPORT MESSAGE
# ============================================================

async def support_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.user_data.get(
        "waiting_for_support"
    ):
        return

    telegram_user = (
        update.effective_user
    )

    if not telegram_user:
        return

    message = (
        update.effective_message
    )

    if not message:
        return

    text = message.text

    if not text:
        return

    db = SessionLocal()

    try:

        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == str(telegram_user.id)
            )
            .first()
        )

        if not user:

            # ------------------------------------------------
            # Создаём пользователя прямо в этой сессии.
            # Не открываем вторую SessionLocal().
            # ------------------------------------------------

            telegram_id = str(
                telegram_user.id
            )

            username = (
                telegram_user.username
            )

            email = (
                f"tg_{telegram_id}@edaaa.local"
            )

            from app.auth import hash_password

            user = User(
                email=email,
                password_hash=hash_password(
                    telegram_id
                ),
                telegram_id=telegram_id,
                telegram_username=username,
                is_active=True,
                is_admin=False,
            )

            db.add(user)

            db.flush()

        # ----------------------------------------------------
        # Support ticket
        # ----------------------------------------------------

        from app.support_models import (
            SupportTicket,
        )

        ticket = SupportTicket(
            user_id=user.id,
            message=text,
            status="open",
        )

        db.add(ticket)

        db.commit()

        db.refresh(ticket)

        context.user_data[
            "waiting_for_support"
        ] = False

        await message.reply_text(
            "✅ <b>Сообщение отправлено "
            "в поддержку.</b>\n\n"
            f"Номер обращения: "
            f"#{ticket.id}",
            parse_mode="HTML",
        )

    except Exception:

        db.rollback()

        logger.exception(
            "Failed to create support ticket."
        )

        await message.reply_text(
            "❌ Не удалось отправить сообщение "
            "в поддержку.\n\n"
            "Попробуйте ещё раз."
        )

    finally:

        db.close()


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    telegram_user = (
        update.effective_user
    )

    if not telegram_user:
        return

    data = query.data

    try:

        if data == "menu":

            context.user_data[
                "waiting_for_support"
            ] = False

            await show_menu(
                query
            )

        elif data == "wallet":

            await wallet(
                query,
                telegram_user,
            )

        elif data == "deposit":

            await deposit(
                query,
                telegram_user,
            )

        elif data == "withdraw":

            await withdraw(
                query
            )

        elif data == "buy_usdt":

            await buy_usdt(
                query
            )

        elif data == "sell_usdt":

            await sell_usdt(
                query
            )

        elif data == "history":

            await history(
                query,
                telegram_user,
            )

        elif data == "profile":

            await profile(
                query,
                telegram_user,
            )

        elif data == "support":

            await support(
                query,
                context,
            )

        else:

            await query.edit_message_text(
                "❌ Неизвестная команда."
            )

    except Exception:

        logger.exception(
            "Telegram callback error: %s",
            data,
        )

        try:

            await query.edit_message_text(
                "❌ Произошла ошибка. "
                "Попробуйте ещё раз."
            )

        except Exception:

            pass


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Telegram bot error.",
        exc_info=context.error,
    )


# ============================================================
# CREATE TELEGRAM APPLICATION
# ============================================================

def create_telegram_application():

    token = (
        settings.TELEGRAM_BOT_TOKEN
    )

    if not token:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN "
            "is not configured."
        )

    application = (
        Application.builder()
        .token(token)
        .build()
    )

    # --------------------------------------------------------
    # /start
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # --------------------------------------------------------
    # Inline buttons
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # --------------------------------------------------------
    # Text messages
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            support_message,
        )
    )

    # --------------------------------------------------------
    # Error handler
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Telegram application created successfully."
    )

    return application


# ============================================================
# BACKWARD COMPATIBILITY
# ============================================================

def create_bot_application(
    token: str,
):

    application = (
        Application.builder()
        .token(token)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            support_message,
        )
    )

    application.add_error_handler(
        error_handler
    )

    return application


# ============================================================
# RUN BOT
# ============================================================

async def run_bot(
    token: str,
):

    application = (
        create_bot_application(
            token
        )
    )

    logger.info(
        "Starting Edaaa Telegram bot..."
    )

    await application.initialize()

    await application.start()

    if application.updater is None:

        raise RuntimeError(
            "Telegram updater is not available."
        )

    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES
    )

    logger.info(
        "Edaaa Telegram bot started."
    )

    try:

        import asyncio

        while True:

            await asyncio.sleep(
                3600
            )

    finally:

        logger.info(
            "Stopping Edaaa Telegram bot..."
        )

        if application.updater:

            await (
                application
                .updater
                .stop()
            )

        await application.stop()

        await application.shutdown()
