import logging
import secrets
from decimal import Decimal

from cryptography.fernet import Fernet

from eth_account import Account
from web3 import Web3

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

from app.auth import hash_password
from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction
from app.wallet_key_models import WalletKey


logger = logging.getLogger("edaaa.telegram")


# ============================================================
# DATABASE
# ============================================================


def get_db() -> Session:
    return SessionLocal()


# ============================================================
# WALLET SECURITY
# ============================================================


def get_wallet_fernet() -> Fernet:
    key = settings.WALLET_ENCRYPTION_KEY

    if not key:
        raise RuntimeError(
            "WALLET_ENCRYPTION_KEY is not configured."
        )

    try:
        return Fernet(key.encode())

    except Exception as exc:
        raise RuntimeError(
            "Invalid WALLET_ENCRYPTION_KEY."
        ) from exc


def create_real_ethereum_wallet():
    account = Account.create()

    address = Web3.to_checksum_address(
        account.address
    )

    private_key = account.key.hex()

    return address, private_key


def encrypt_private_key(
    private_key: str,
) -> str:
    fernet = get_wallet_fernet()

    encrypted = fernet.encrypt(
        private_key.encode()
    )

    return encrypted.decode()


# ============================================================
# TELEGRAM USER / WALLET
# ============================================================


def get_or_create_user(
    telegram_id: int,
    telegram_username: str | None,
):
    db = get_db()

    try:
        telegram_id_string = str(
            telegram_id
        )

        # ----------------------------------------------------
        # 1. Ищем пользователя по Telegram ID
        # ----------------------------------------------------

        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == telegram_id_string
            )
            .first()
        )

        if user:

            user.telegram_username = (
                telegram_username
            )

            db.commit()
            db.refresh(user)

            return user

        # ----------------------------------------------------
        # 2. Новый Telegram пользователь
        # ----------------------------------------------------

        email = (
            f"telegram_{telegram_id}"
            "@edaaa.local"
        )

        # Генерируем случайный технический пароль.
        # Пользователь Telegram его не видит.
        random_password = secrets.token_urlsafe(
            32
        )

        user = User(
            email=email,
            password_hash=hash_password(
                random_password
            ),
            is_active=True,
            is_admin=False,
            telegram_id=telegram_id_string,
            telegram_username=(
                telegram_username
            ),
        )

        db.add(user)
        db.flush()

        # ----------------------------------------------------
        # 3. Создаём настоящий Ethereum wallet
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 4. Шифруем private key
        # ----------------------------------------------------

        encrypted_private_key = (
            encrypt_private_key(
                private_key
            )
        )

        wallet_key = WalletKey(
            wallet_id=wallet.id,
            encrypted_private_key=(
                encrypted_private_key
            ),
        )

        db.add(wallet_key)

        # ----------------------------------------------------
        # 5. Создаём ETH balance
        # ----------------------------------------------------

        eth_balance = Balance(
            wallet_id=wallet.id,
            asset="ETH",
            amount=Decimal("0"),
        )

        db.add(eth_balance)

        # ----------------------------------------------------
        # 6. Создаём USDT balance
        # ----------------------------------------------------

        usdt_balance = Balance(
            wallet_id=wallet.id,
            asset="USDT",
            amount=Decimal("0"),
        )

        db.add(usdt_balance)

        # ----------------------------------------------------
        # 7. Сохраняем всё одной транзакцией
        # ----------------------------------------------------

        db.commit()

        db.refresh(user)

        logger.info(
            "Created Telegram user %s with wallet %s",
            telegram_id_string,
            address,
        )

        return user

    except Exception:
        db.rollback()
        logger.exception(
            "Failed to create Telegram user."
        )
        raise

    finally:
        db.close()


def get_user_wallet(
    user_id: int,
):
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


# ============================================================
# BALANCES
# ============================================================


def get_balances(
    wallet_id: int,
):
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

            if balance.asset == "ETH":
                result["ETH"] = Decimal(
                    balance.amount
                )

            elif balance.asset == "USDT":
                result["USDT"] = Decimal(
                    balance.amount
                )

        return result

    finally:
        db.close()


# ============================================================
# TRANSACTIONS
# ============================================================


def get_transactions(
    wallet_id: int,
):
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


# ============================================================
# KEYBOARDS
# ============================================================


def main_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💰 Кошелёк",
                    callback_data="wallet",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📥 Пополнить",
                    callback_data="deposit",
                ),
                InlineKeyboardButton(
                    "📤 Отправить",
                    callback_data="send",
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
                    "📜 История",
                    callback_data="history",
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
                    "⬅️ Главное меню",
                    callback_data="main",
                )
            ]
        ]
    )


# ============================================================
# /START
# ============================================================


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_user:
        return

    telegram_user = (
        update.effective_user
    )

    try:

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
                "❌ Не удалось создать "
                "или найти ваш кошелёк."
            )

            return

        text = (
            "🏦 *Добро пожаловать в Edaaa Wallet!*\n\n"
            "🔐 Ваш Telegram подключён к Edaaa.\n\n"
            "Ваш персональный Ethereum-кошелёк "
            "уже создан.\n\n"
            f"🌐 Сеть: `{wallet.network}`\n\n"
            "📍 Ваш адрес:\n"
            f"`{wallet.address}`\n\n"
            "Выберите действие:"
        )

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    except Exception:

        logger.exception(
            "Telegram /start error."
        )

        await update.message.reply_text(
            "❌ Произошла ошибка при "
            "создании Edaaa Wallet.\n\n"
            "Попробуйте ещё раз через несколько секунд."
        )


# ============================================================
# CALLBACKS
# ============================================================


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not query.from_user:
        return

    telegram_user = (
        query.from_user
    )

    try:

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

            await query.edit_message_text(
                "❌ Кошелёк не найден."
            )

            return

        # ----------------------------------------------------
        # MAIN
        # ----------------------------------------------------

        if query.data == "main":

            await show_main(
                query,
                wallet,
            )

        # ----------------------------------------------------
        # WALLET
        # ----------------------------------------------------

        elif query.data == "wallet":

            await show_wallet(
                query,
                wallet,
            )

        # ----------------------------------------------------
        # DEPOSIT
        # ----------------------------------------------------

        elif query.data == "deposit":

            await show_deposit(
                query,
                wallet,
            )

        # ----------------------------------------------------
        # SEND
        # ----------------------------------------------------

        elif query.data == "send":

            await query.edit_message_text(
                "📤 *Отправка ETH*\n\n"
                "Функция отправки уже реализована "
                "в Edaaa API.\n\n"
                "Сейчас мы подключаем её к "
                "Telegram-интерфейсу.\n\n"
                "Следующим этапом добавим:\n"
                "1️⃣ Адрес получателя\n"
                "2️⃣ Сумму\n"
                "3️⃣ Подтверждение\n"
                "4️⃣ Отправку транзакции\n"
                "5️⃣ TX Hash",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        # ----------------------------------------------------
        # HISTORY
        # ----------------------------------------------------

        elif query.data == "history":

            await show_history(
                query,
                wallet,
            )

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        elif query.data == "buy_usdt":

            await query.edit_message_text(
                "💱 *Купить USDT*\n\n"
                "P2P-модуль пока находится "
                "в разработке.\n\n"
                "Следующим этапом подключим "
                "заявки на покупку USDT.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        elif query.data == "sell_usdt":

            await query.edit_message_text(
                "💵 *Продать USDT*\n\n"
                "P2P-модуль пока находится "
                "в разработке.\n\n"
                "Следующим этапом подключим "
                "заявки на продажу USDT.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        # ----------------------------------------------------
        # PROFILE
        # ----------------------------------------------------

        elif query.data == "profile":

            await show_profile(
                query,
                user,
                wallet,
            )

    except Exception:

        logger.exception(
            "Telegram callback error."
        )

        await query.edit_message_text(
            "❌ Произошла ошибка.\n\n"
            "Попробуйте ещё раз.",
            reply_markup=back_keyboard(),
        )


# ============================================================
# MAIN MENU
# ============================================================


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


# ============================================================
# WALLET
# ============================================================


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
        "💰 *Ваш Edaaa Wallet*\n\n"
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


# ============================================================
# DEPOSIT
# ============================================================


async def show_deposit(
    query,
    wallet,
):

    text = (
        "📥 *Пополнение кошелька*\n\n"
        "Отправьте ETH на следующий адрес:\n\n"
        f"`{wallet.address}`\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "⚠️ Обязательно проверяйте сеть "
        "перед отправкой.\n\n"
        "После подтверждения транзакции "
        "баланс будет отображён в Edaaa."
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


# ============================================================
# HISTORY
# ============================================================


async def show_history(
    query,
    wallet,
):

    transactions = get_transactions(
        wallet.id
    )

    if not transactions:

        text = (
            "📜 *История операций*\n\n"
            "Операций пока нет."
        )

    else:

        lines = [
            "📜 *Последние операции*",
            "",
        ]

        for transaction in transactions:

            tx_type = (
                transaction.type
            )

            amount = (
                transaction.amount
            )

            asset = (
                transaction.asset
            )

            tx_status = (
                transaction.status
            )

            lines.append(
                f"• {tx_type} — "
                f"{amount} {asset} — "
                f"{tx_status}"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


# ============================================================
# PROFILE
# ============================================================


async def show_profile(
    query,
    user,
    wallet,
):

    if user.telegram_username:

        username = (
            "@"
            + user.telegram_username
        )

    else:

        username = (
            "не указан"
        )

    text = (
        "👤 *Профиль Edaaa*\n\n"
        f"Telegram: `{username}`\n"
        f"Edaaa User ID: `{user.id}`\n"
        f"Telegram ID: `{user.telegram_id}`\n\n"
        "💼 Wallet:\n"
        f"`{wallet.address}`\n\n"
        f"🌐 Network: `{wallet.network}`"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


# ============================================================
# ERROR HANDLER
# ============================================================


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Telegram bot error: %s",
        context.error,
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
            button_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    return application
