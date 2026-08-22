import logging
import secrets
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

from app.auth import hash_password
from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction
from app.main_wallet import (
    create_real_ethereum_wallet,
    encrypt_private_key,
)

logger = logging.getLogger("edaaa.telegram")


def get_db():
    return SessionLocal()


def create_telegram_user(telegram_user):
    db = get_db()

    try:
        telegram_id = str(telegram_user.id)

        user = (
            db.query(User)
            .filter(User.telegram_id == telegram_id)
            .first()
        )

        if user:
            user.telegram_username = (
                telegram_user.username
            )

            db.commit()

            return user

        username = (
            telegram_user.username
            or f"user_{telegram_id}"
        )

        email = (
            f"telegram_{telegram_id}"
            "@telegram.edaaa.local"
        )

        random_password = secrets.token_urlsafe(32)

        user = User(
            email=email,
            password_hash=hash_password(
                random_password
            ),
            telegram_id=telegram_id,
            telegram_username=username,
            is_admin=False,
            is_active=True,
        )

        db.add(user)
        db.flush()

        address, private_key = (
            create_real_ethereum_wallet()
        )

        wallet = Wallet(
            user_id=user.id,
            address=address,
            network=settings.ETH_NETWORK,
        )

        db.add(wallet)
        db.flush()

        encrypted_private_key = (
            encrypt_private_key(private_key)
        )

        from app.wallet_key_models import WalletKey

        wallet_key = WalletKey(
            wallet_id=wallet.id,
            encrypted_private_key=(
                encrypted_private_key
            ),
        )

        db.add(wallet_key)

        eth_balance = Balance(
            wallet_id=wallet.id,
            asset="ETH",
            amount=Decimal("0"),
        )

        usdt_balance = Balance(
            wallet_id=wallet.id,
            asset="USDT",
            amount=Decimal("0"),
        )

        db.add(eth_balance)
        db.add(usdt_balance)

        db.commit()
        db.refresh(user)

        logger.info(
            "Created Telegram user %s",
            telegram_id,
        )

        return user

    except Exception:
        db.rollback()
        logger.exception(
            "Failed to create Telegram user"
        )
        raise

    finally:
        db.close()


def get_user_by_telegram_id(telegram_id):
    db = get_db()

    try:
        return (
            db.query(User)
            .filter(
                User.telegram_id
                == str(telegram_id)
            )
            .first()
        )

    finally:
        db.close()


def get_user_wallet(user_id):
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


def get_balances(wallet_id):
    db = get_db()

    try:
        balances = (
            db.query(Balance)
            .filter(
                Balance.wallet_id == wallet_id
            )
            .all()
        )

        result = {}

        for balance in balances:
            result[balance.asset] = Decimal(
                balance.amount
            )

        return result

    finally:
        db.close()


def get_transactions(wallet_id):
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


def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💰 Баланс",
                    callback_data="balance",
                ),
                InlineKeyboardButton(
                    "👛 Кошелёк",
                    callback_data="wallet",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📥 Пополнить",
                    callback_data="deposit",
                ),
                InlineKeyboardButton(
                    "📤 Вывести",
                    callback_data="withdraw",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📜 История",
                    callback_data="history",
                ),
                InlineKeyboardButton(
                    "👤 Профиль",
                    callback_data="profile",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="refresh",
                )
            ],
        ]
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="home",
                )
            ]
        ]
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user = update.effective_user

    try:
        user = create_telegram_user(
            telegram_user
        )

        wallet = get_user_wallet(user.id)

        if not wallet:
            await update.message.reply_text(
                "❌ Не удалось создать кошелёк."
            )
            return

        text = (
            "👋 Добро пожаловать в Edaaa Wallet!\n\n"
            "Ваш Telegram уже привязан к Edaaa.\n\n"
            f"👛 Кошелёк:\n"
            f"`{wallet.address}`\n\n"
            f"🌐 Сеть: {wallet.network}\n\n"
            "Выберите действие:"
        )

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    except Exception:
        logger.exception(
            "Telegram /start error"
        )

        await update.message.reply_text(
            "❌ Произошла ошибка при создании "
            "кошелька. Попробуйте ещё раз."
        )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    telegram_user = query.from_user

    user = get_user_by_telegram_id(
        telegram_user.id
    )

    if not user:
        user = create_telegram_user(
            telegram_user
        )

    wallet = get_user_wallet(user.id)

    if not wallet:
        await query.edit_message_text(
            "❌ Кошелёк не найден.",
            reply_markup=back_keyboard(),
        )
        return

    if query.data in (
        "home",
        "refresh",
    ):
        await show_home(
            query,
            user,
            wallet,
        )
        return

    if query.data == "balance":
        await show_balance(
            query,
            wallet,
        )
        return

    if query.data == "wallet":
        await show_wallet(
            query,
            wallet,
        )
        return

    if query.data == "history":
        await show_history(
            query,
            wallet,
        )
        return

    if query.data == "profile":
        await show_profile(
            query,
            user,
        )
        return

    if query.data == "deposit":
        await show_deposit(
            query,
            wallet,
        )
        return

    if query.data == "withdraw":
        await show_withdraw(
            query,
        )
        return


async def show_home(
    query,
    user,
    wallet,
):
    balances = get_balances(wallet.id)

    eth = balances.get(
        "ETH",
        Decimal("0"),
    )

    usdt = balances.get(
        "USDT",
        Decimal("0"),
    )

    text = (
        "💳 *Edaaa Wallet*\n\n"
        f"ETH: `{eth}`\n"
        f"USDT: `{usdt}`\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "Выберите действие:"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def show_balance(
    query,
    wallet,
):
    balances = get_balances(wallet.id)

    eth = balances.get(
        "ETH",
        Decimal("0"),
    )

    usdt = balances.get(
        "USDT",
        Decimal("0"),
    )

    text = (
        "💰 *Ваш баланс*\n\n"
        f"Ξ ETH: `{eth}`\n"
        f"₮ USDT: `{usdt}`\n\n"
        f"🌐 Сеть: `{wallet.network}`"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


async def show_wallet(
    query,
    wallet,
):
    text = (
        "👛 *Ваш Edaaa Wallet*\n\n"
        f"Адрес:\n"
        f"`{wallet.address}`\n\n"
        f"Сеть: `{wallet.network}`\n\n"
        "Этот адрес можно использовать "
        "для получения ETH в соответствующей сети."
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
        "Для пополнения ETH отправьте средства "
        "на этот адрес:\n\n"
        f"`{wallet.address}`\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "⚠️ Отправляйте только активы и сети, "
        "которые поддерживает Edaaa."
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


async def show_withdraw(
    query,
):
    text = (
        "📤 *Вывод ETH*\n\n"
        "Функция отправки ETH через Telegram "
        "будет подключена следующим этапом.\n\n"
        "Перед отправкой мы добавим подтверждение "
        "адреса, суммы и комиссии."
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
            "Транзакций пока нет."
        )

    else:
        lines = [
            "📜 *Последние транзакции*\n"
        ]

        for transaction in transactions:
            lines.append(
                f"• {transaction.type} | "
                f"{transaction.asset} | "
                f"{transaction.amount} | "
                f"{transaction.status}"
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
):
    username = (
        f"@{user.telegram_username}"
        if user.telegram_username
        else "не указан"
    )

    text = (
        "👤 *Профиль*\n\n"
        f"ID Edaaa: `{user.id}`\n"
        f"Telegram: `{username}`\n"
        f"Email: `{user.email}`\n"
        f"Аккаунт активен: "
        f"`{'Да' if user.is_active else 'Нет'}`"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


def create_application():
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

    return application


async def run_bot():
    application = create_application()

    logger.info(
        "Starting Edaaa Telegram bot..."
    )

    await application.initialize()
    await application.start()

    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES
    )

    logger.info(
        "Edaaa Telegram bot is running."
    )

    try:
        while True:
            await __import__(
                "asyncio"
            ).sleep(3600)

    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
