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
)

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction


logger = logging.getLogger("edaaa.telegram")


def get_db() -> Session:
    return SessionLocal()


def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💰 Кошелёк",
                    callback_data="wallet",
                ),
                InlineKeyboardButton(
                    "📥 Пополнить",
                    callback_data="deposit",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📤 Отправить",
                    callback_data="send",
                ),
                InlineKeyboardButton(
                    "📜 История",
                    callback_data="history",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💱 Купить USDT",
                    callback_data="buy_usdt",
                ),
                InlineKeyboardButton(
                    "💵 Продать USDT",
                    callback_data="sell_usdt",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👤 Профиль",
                    callback_data="profile",
                ),
            ],
        ]
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="main",
                )
            ]
        ]
    )


def get_or_create_user(
    telegram_id: int,
    telegram_username: str | None,
):
    db = get_db()

    try:
        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == str(telegram_id)
            )
            .first()
        )

        if user:
            if telegram_username:
                user.telegram_username = (
                    telegram_username
                )

                db.commit()

            return user

        # Если Telegram ещё не привязан,
        # проверяем, нет ли пользователя
        # с таким username.
        user = None

        if telegram_username:
            user = (
                db.query(User)
                .filter(
                    User.telegram_username
                    == telegram_username
                )
                .first()
            )

        # Если такого пользователя нет,
        # создаём нового.
        if not user:
            email = (
                f"telegram_{telegram_id}"
                "@edaaa.local"
            )

            # Временный технический пароль.
            # Пользователь Telegram им
            # напрямую не пользуется.
            password_hash = (
                "telegram_account"
            )

            user = User(
                email=email,
                password_hash=password_hash,
                is_active=True,
                is_admin=False,
                telegram_id=str(
                    telegram_id
                ),
                telegram_username=(
                    telegram_username
                ),
            )

            db.add(user)
            db.commit()
            db.refresh(user)

        else:
            user.telegram_id = str(
                telegram_id
            )

            user.telegram_username = (
                telegram_username
            )

            db.commit()

        return user

    finally:
        db.close()


def get_user_wallet(user_id: int):
    db = get_db()

    try:
        return (
            db.query(Wallet)
            .filter(
                Wallet.user_id == user_id
            )
            .first()
        )

    finally:
        db.close()


def get_balances(wallet_id: int):
    db = get_db()

    try:
        balances = (
            db.query(Balance)
            .filter(
                Balance.wallet_id
                == wallet_id
            )
            .all()
        )

        result = {
            "ETH": Decimal("0"),
            "USDT": Decimal("0"),
        }

        for balance in balances:
            if balance.asset in result:
                result[balance.asset] = (
                    Decimal(balance.amount)
                )

        return result

    finally:
        db.close()


def get_transactions(wallet_id: int):
    db = get_db()

    try:
        return (
            db.query(Transaction)
            .filter(
                Transaction.wallet_id
                == wallet_id
            )
            .order_by(
                Transaction.created_at.desc()
            )
            .limit(10)
            .all()
        )

    finally:
        db.close()


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    telegram_user = (
        update.effective_user
    )

    user = get_or_create_user(
        telegram_id=telegram_user.id,
        telegram_username=(
            telegram_user.username
        ),
    )

    wallet = get_user_wallet(
        user.id
    )

    if not wallet:
        await update.message.reply_text(
            "❌ Не удалось найти ваш Edaaa Wallet."
        )
        return

    text = (
        "👋 Добро пожаловать в Edaaa Wallet!\n\n"
        "🔐 Ваш Telegram привязан к Edaaa.\n\n"
        f"🌐 Сеть: {wallet.network}\n"
        f"📍 Адрес:\n"
        f"`{wallet.address}`\n\n"
        "Выберите действие:"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not query.from_user:
        return

    telegram_id = query.from_user.id

    user = get_or_create_user(
        telegram_id=telegram_id,
        telegram_username=(
            query.from_user.username
        ),
    )

    wallet = get_user_wallet(
        user.id
    )

    if not wallet:
        await query.edit_message_text(
            "❌ Кошелёк не найден."
        )
        return

    if query.data == "main":
        await show_main(
            query,
            wallet,
        )

    elif query.data == "wallet":
        await show_wallet(
            query,
            wallet,
        )

    elif query.data == "deposit":
        await show_deposit(
            query,
            wallet,
        )

    elif query.data == "history":
        await show_history(
            query,
            wallet,
        )

    elif query.data == "profile":
        await show_profile(
            query,
            user,
            wallet,
        )

    elif query.data == "send":
        await query.edit_message_text(
            "📤 Отправка ETH\n\n"
            "Эта функция будет подключена "
            "следующим этапом.\n\n"
            "Пока переводить средства через "
            "Telegram нельзя.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "buy_usdt":
        await query.edit_message_text(
            "💱 Покупка USDT\n\n"
            "P2P-модуль Edaaa будет подключён "
            "следующим этапом.",
            reply_markup=back_keyboard(),
        )

    elif query.data == "sell_usdt":
        await query.edit_message_text(
            "💵 Продажа USDT\n\n"
            "P2P-модуль Edaaa будет подключён "
            "следующим этапом.",
            reply_markup=back_keyboard(),
        )


async def show_main(
    query,
    wallet,
):
    text = (
        "🏦 *Edaaa Wallet*\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "Выберите действие:"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def show_wallet(
    query,
    wallet,
):
    balances = get_balances(
        wallet.id
    )

    eth = balances["ETH"]
    usdt = balances["USDT"]

    text = (
        "💰 *Ваш кошелёк*\n\n"
        f"Ξ ETH: `{eth}`\n"
        f"💵 USDT: `{usdt}`\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "📍 Адрес:\n"
        f"`{wallet.address}`"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


async def show_deposit(
    query,
    wallet,
):
    text = (
        "📥 *Пополнение*\n\n"
        "Чтобы пополнить ваш Ethereum-кошелёк, "
        "отправьте ETH на адрес:\n\n"
        f"`{wallet.address}`\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "⚠️ Отправляйте средства только в "
        "поддерживаемой сети."
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


async def show_history(
    query,
    wallet,
):
    transactions = get_transactions(
        wallet.id
    )

    if not transactions:
        text = (
            "📜 *История*\n\n"
            "У вас пока нет операций."
        )

    else:
        lines = [
            "📜 *Последние операции*\n"
        ]

        for transaction in transactions:
            amount = transaction.amount
            asset = transaction.asset
            tx_type = transaction.type
            status = transaction.status

            lines.append(
                f"• {tx_type} "
                f"{amount} {asset} "
                f"— {status}"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


async def show_profile(
    query,
    user,
    wallet,
):
    username = (
        f"@{user.telegram_username}"
        if user.telegram_username
        else "не указан"
    )

    text = (
        "👤 *Профиль*\n\n"
        f"Telegram: `{username}`\n"
        f"User ID: `{user.id}`\n\n"
        f"Wallet:\n"
        f"`{wallet.address}`\n\n"
        f"Сеть: `{wallet.network}`"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Telegram bot error",
        exc_info=context.error,
    )


def create_telegram_application():
    token = settings.TELEGRAM_BOT_TOKEN

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

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
            button_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    return application
