import logging
import os
import secrets
from decimal import Decimal, InvalidOperation

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
    ConversationHandler,
    MessageHandler,
    filters,
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

from app.support_models import (
    SupportTicket,
    SupportMessage,
)

from app.send_service import (
    get_wallet_private_key,
    validate_recipient_address,
    get_web3,
    create_eth_transaction,
    sign_and_send_eth_transaction,
)


logger = logging.getLogger(
    "edaaa.telegram"
)


# ============================================================
# STATES
# ============================================================

SUPPORT_CATEGORY = 1
SUPPORT_SUBJECT = 2
SUPPORT_MESSAGE = 3

SEND_ADDRESS = 10
SEND_AMOUNT = 11
SEND_CONFIRM = 12


# ============================================================
# DATABASE
# ============================================================

def get_db() -> Session:
    return SessionLocal()


# ============================================================
# ADMIN
# ============================================================

def get_admin_telegram_id() -> int | None:

    value = os.getenv(
        "ADMIN_TELEGRAM_ID"
    )

    if not value:
        return None

    try:
        return int(value)

    except ValueError:

        logger.error(
            "ADMIN_TELEGRAM_ID must be an integer."
        )

        return None


async def notify_admin_about_ticket(
    context: ContextTypes.DEFAULT_TYPE,
    ticket_id: int,
    user: User,
    category: str,
    subject: str,
    message_text: str,
):

    admin_telegram_id = (
        get_admin_telegram_id()
    )

    if not admin_telegram_id:
        return

    username = (
        f"@{user.telegram_username}"
        if user.telegram_username
        else "не указан"
    )

    text = (
        "🚨 *НОВОЕ ОБРАЩЕНИЕ EDAAA*\n\n"
        f"🎫 `#{ticket_id}`\n"
        f"📂 {category}\n"
        f"📌 {subject}\n\n"
        f"👤 `{username}`\n"
        f"User ID: `{user.id}`\n"
        f"Telegram ID: `{user.telegram_id}`\n\n"
        f"💬 {message_text}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📨 Открыть",
                    callback_data=(
                        f"admin_ticket_{ticket_id}"
                    ),
                )
            ]
        ]
    )

    try:

        await context.bot.send_message(
            chat_id=admin_telegram_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    except Exception:

        logger.exception(
            "Failed to notify admin about ticket #%s.",
            ticket_id,
        )


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

        return Fernet(
            key.encode()
        )

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

    return (
        address,
        private_key,
    )


def encrypt_private_key(
    private_key: str,
) -> str:

    return (
        get_wallet_fernet()
        .encrypt(
            private_key.encode()
        )
        .decode()
    )


# ============================================================
# BALANCE HELPERS
# ============================================================

def ensure_balance(
    db: Session,
    wallet: Wallet,
    asset: str,
) -> Balance:

    balance = (
        db.query(Balance)
        .filter(
            Balance.wallet_id == wallet.id,
            Balance.asset == asset,
        )
        .first()
    )

    if balance:

        return balance

    balance = Balance(
        wallet_id=wallet.id,
        asset=asset,
        amount=Decimal("0"),
    )

    db.add(balance)
    db.flush()

    return balance


# ============================================================
# USER / WALLET
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

        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == telegram_id_string
            )
            .first()
        )

        # ----------------------------------------------------
        # EXISTING USER
        # ----------------------------------------------------

        if user:

            user.telegram_username = (
                telegram_username
            )

            wallet = (
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

            # ------------------------------------------------
            # EXISTING WALLET
            # ------------------------------------------------

            if wallet:

                ensure_balance(
                    db,
                    wallet,
                    "ETH",
                )

                ensure_balance(
                    db,
                    wallet,
                    "USDT",
                )

                wallet_key = (
                    db.query(
                        WalletKey
                    )
                    .filter(
                        WalletKey.wallet_id
                        == wallet.id
                    )
                    .first()
                )

                if not wallet_key:

                    logger.warning(
                        "Wallet %s has no encrypted private key.",
                        wallet.id,
                    )

                db.commit()
                db.refresh(user)

                return user

            # ------------------------------------------------
            # USER WITHOUT WALLET
            # ------------------------------------------------

            logger.warning(
                "Existing user %s has no wallet. "
                "Creating one.",
                user.id,
            )

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
                encrypt_private_key(
                    private_key
                )
            )

            db.add(
                WalletKey(
                    wallet_id=wallet.id,
                    encrypted_private_key=(
                        encrypted_private_key
                    ),
                )
            )

            ensure_balance(
                db,
                wallet,
                "ETH",
            )

            ensure_balance(
                db,
                wallet,
                "USDT",
            )

            db.commit()
            db.refresh(user)

            logger.info(
                "Recovered wallet for existing "
                "Telegram user | "
                "user_id=%s | "
                "wallet=%s",
                user.id,
                address,
            )

            return user

        # ----------------------------------------------------
        # NEW USER
        # ----------------------------------------------------

        email = (
            f"telegram_{telegram_id}"
            "@edaaa.local"
        )

        random_password = (
            secrets.token_urlsafe(32)
        )

        user = User(
            email=email,
            password_hash=hash_password(
                random_password
            ),
            is_active=True,
            is_admin=False,
            telegram_id=(
                telegram_id_string
            ),
            telegram_username=(
                telegram_username
            ),
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

        db.add(
            WalletKey(
                wallet_id=wallet.id,
                encrypted_private_key=(
                    encrypt_private_key(
                        private_key
                    )
                ),
            )
        )

        ensure_balance(
            db,
            wallet,
            "ETH",
        )

        ensure_balance(
            db,
            wallet,
            "USDT",
        )

        db.commit()
        db.refresh(user)

        logger.info(
            "Created Telegram user | "
            "telegram_id=%s | "
            "user_id=%s | "
            "wallet=%s | "
            "network=%s",
            telegram_id_string,
            user.id,
            address,
            settings.ETH_NETWORK,
        )

        return user

    except Exception:

        db.rollback()

        logger.exception(
            "Failed to create/recover Telegram user."
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
                Wallet.user_id
                == user_id
            )
            .order_by(
                Wallet.id.asc()
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

                result["ETH"] = (
                    Decimal(
                        balance.amount
                    )
                )

            elif balance.asset == "USDT":

                result["USDT"] = (
                    Decimal(
                        balance.amount
                    )
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
            .limit(20)
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
                )
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
                )
            ],
            [
                InlineKeyboardButton(
                    "🆘 Поддержка",
                    callback_data="support",
                )
            ],
            [
                InlineKeyboardButton(
                    "👤 Профиль",
                    callback_data="profile",
                )
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


def send_cancel_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="send_cancel",
                )
            ]
        ]
    )


def send_confirm_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Подтвердить",
                    callback_data="send_confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="send_cancel",
                )
            ],
        ]
    )


# ============================================================
# START
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
                "❌ Кошелёк не найден."
            )

            return

        await update.message.reply_text(
            "🏦 *Edaaa Wallet*\n\n"
            "Ваш персональный Ethereum-кошелёк.\n\n"
            f"🌐 Сеть: `{wallet.network}`\n\n"
            "📍 Адрес:\n"
            f"`{wallet.address}`\n\n"
            "Выберите действие:",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    except Exception:

        logger.exception(
            "Telegram /start error."
        )

        await update.message.reply_text(
            "❌ Ошибка Edaaa.\n\n"
            "Попробуйте ещё раз."
        )


# ============================================================
# SEND START
# ============================================================

async def send_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.pop(
        "send_to_address",
        None,
    )

    context.user_data.pop(
        "send_amount",
        None,
    )

    await query.edit_message_text(
        "📤 *Отправка ETH*\n\n"
        "Введите адрес получателя:",
        parse_mode="Markdown",
        reply_markup=send_cancel_keyboard(),
    )

    return SEND_ADDRESS


# ============================================================
# SEND ADDRESS
# ============================================================

async def send_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:

        return SEND_ADDRESS

    address = (
        update.message.text
        or ""
    ).strip()

    try:

        address = (
            validate_recipient_address(
                address
            )
        )

    except Exception:

        await update.message.reply_text(
            "❌ Некорректный Ethereum-адрес.",
            reply_markup=send_cancel_keyboard(),
        )

        return SEND_ADDRESS

    telegram_user = (
        update.effective_user
    )

    if not telegram_user:

        return ConversationHandler.END

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
            "❌ Кошелёк не найден.",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    if (
        Web3.to_checksum_address(
            wallet.address
        )
        == address
    ):

        await update.message.reply_text(
            "❌ Нельзя отправить ETH "
            "на собственный адрес.",
            reply_markup=send_cancel_keyboard(),
        )

        return SEND_ADDRESS

    context.user_data[
        "send_to_address"
    ] = address

    await update.message.reply_text(
        "💰 Введите сумму ETH.\n\n"
        "Например: `0.001`",
        parse_mode="Markdown",
        reply_markup=send_cancel_keyboard(),
    )

    return SEND_AMOUNT


# ============================================================
# SEND AMOUNT
# ============================================================

async def send_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:

        return SEND_AMOUNT

    amount_text = (
        update.message.text
        or ""
    ).strip().replace(
        ",",
        ".",
    )

    try:

        amount = Decimal(
            amount_text
        )

    except InvalidOperation:

        await update.message.reply_text(
            "❌ Некорректная сумма.",
            reply_markup=send_cancel_keyboard(),
        )

        return SEND_AMOUNT

    if amount <= 0:

        await update.message.reply_text(
            "❌ Сумма должна быть больше нуля.",
            reply_markup=send_cancel_keyboard(),
        )

        return SEND_AMOUNT

    if amount.as_tuple().exponent < -18:

        await update.message.reply_text(
            "❌ Максимум 18 знаков после запятой.",
            reply_markup=send_cancel_keyboard(),
        )

        return SEND_AMOUNT

    telegram_user = (
        update.effective_user
    )

    if not telegram_user:

        return ConversationHandler.END

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

            raise RuntimeError(
                "Wallet not found."
            )

        web3 = get_web3()

        address = (
            Web3.to_checksum_address(
                wallet.address
            )
        )

        balance_wei = (
            web3.eth.get_balance(
                address,
                "pending",
            )
        )

        balance_eth = Decimal(
            str(
                Web3.from_wei(
                    balance_wei,
                    "ether",
                )
            )
        )

        gas_price = (
            web3.eth.gas_price
        )

        gas_limit = 21000

        gas_cost_wei = (
            gas_price
            * gas_limit
        )

        gas_cost_eth = Decimal(
            str(
                Web3.from_wei(
                    gas_cost_wei,
                    "ether",
                )
            )
        )

        amount_wei = Web3.to_wei(
            amount,
            "ether",
        )

        if (
            balance_wei
            < amount_wei
            + gas_cost_wei
        ):

            await update.message.reply_text(
                "❌ Недостаточно ETH.\n\n"
                f"Баланс: `{balance_eth} ETH`\n"
                f"Сумма: `{amount} ETH`\n"
                f"Газ: ~`{gas_cost_eth} ETH`",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

            return ConversationHandler.END

        context.user_data[
            "send_amount"
        ] = str(amount)

        context.user_data[
            "send_gas_cost"
        ] = str(gas_cost_eth)

        to_address = (
            context.user_data.get(
                "send_to_address"
            )
        )

        await update.message.reply_text(
            "🔎 *Проверка*\n\n"
            f"📤 `{to_address}`\n\n"
            f"💰 `{amount} ETH`\n"
            f"⛽ ~`{gas_cost_eth} ETH`\n"
            f"💳 Баланс: `{balance_eth} ETH`\n\n"
            "Подтвердите отправку.",
            parse_mode="Markdown",
            reply_markup=send_confirm_keyboard(),
        )

        return SEND_CONFIRM

    except Exception as exc:

        logger.exception(
            "Failed to prepare ETH transaction."
        )

        await update.message.reply_text(
            "❌ Не удалось подготовить транзакцию.\n\n"
            f"`{str(exc)[:1000]}`",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END


# ============================================================
# SEND CONFIRM
# ============================================================

async def send_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer(
        "Подписываем и отправляем..."
    )

    telegram_user = (
        query.from_user
    )

    if not telegram_user:

        return ConversationHandler.END

    to_address = (
        context.user_data.get(
            "send_to_address"
        )
    )

    amount_text = (
        context.user_data.get(
            "send_amount"
        )
    )

    if not to_address or not amount_text:

        await query.edit_message_text(
            "❌ Сессия отправки истекла.",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    db = get_db()

    transaction = None
    private_key = None

    try:

        amount = Decimal(
            amount_text
        )

        user = get_or_create_user(
            telegram_id=telegram_user.id,
            telegram_username=(
                telegram_user.username
            ),
        )

        wallet = (
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

        if not wallet:

            raise RuntimeError(
                "Wallet not found."
            )

        if (
            wallet.network
            != settings.ETH_NETWORK
            and wallet.network != "ethereum"
        ):

            raise RuntimeError(
                "Wallet network mismatch."
            )

        to_address = (
            validate_recipient_address(
                to_address
            )
        )

        if (
            Web3.to_checksum_address(
                wallet.address
            )
            == to_address
        ):

            raise RuntimeError(
                "Cannot send ETH to "
                "the same wallet."
            )

        # ----------------------------------------------------
        # Create history record BEFORE broadcast.
        # ----------------------------------------------------

        transaction = Transaction(
            wallet_id=wallet.id,
            type="withdraw",
            asset="ETH",
            amount=amount,
            status="broadcasting",
            tx_hash=None,
        )

        db.add(transaction)

        db.commit()
        db.refresh(transaction)

        # ----------------------------------------------------
        # RPC
        # ----------------------------------------------------

        web3 = get_web3()

        private_key = (
            get_wallet_private_key(
                wallet=wallet,
                db=db,
                decrypt_private_key=(
                    _decrypt_private_key
                ),
            )
        )

        try:

            transaction_data = (
                create_eth_transaction(
                    web3=web3,
                    wallet=wallet,
                    private_key=private_key,
                    to_address=to_address,
                    amount=amount,
                )
            )

            tx_hash = (
                sign_and_send_eth_transaction(
                    web3=web3,
                    private_key=private_key,
                    transaction=(
                        transaction_data
                    ),
                )
            )

        finally:

            private_key = None

        transaction.tx_hash = (
            tx_hash
        )

        transaction.status = (
            "pending"
        )

        db.commit()

        logger.info(
            "ETH transaction broadcast | "
            "transaction_id=%s | "
            "wallet=%s | "
            "tx=%s",
            transaction.id,
            wallet.address,
            tx_hash,
        )

        context.user_data.clear()

        await query.edit_message_text(
            "✅ *ETH отправлен!*\n\n"
            f"💰 Сумма: `{amount} ETH`\n\n"
            "📤 Получатель:\n"
            f"`{to_address}`\n\n"
            f"🌐 Сеть: `{settings.ETH_NETWORK}`\n\n"
            "📜 TX Hash:\n"
            f"`{tx_hash}`\n\n"
            "📊 Статус: `pending`\n\n"
            "Edaaa автоматически отслеживает "
            "подтверждение транзакции.",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    except Exception as exc:

        if transaction:

            try:

                transaction.status = (
                    "failed"
                )

                db.commit()

            except Exception:

                db.rollback()

        logger.exception(
            "ETH send failed."
        )

        await query.edit_message_text(
            "❌ *ETH не отправлен.*\n\n"
            f"`{str(exc)[:1000]}`",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    finally:

        private_key = None

        db.close()


def _decrypt_private_key(
    encrypted_private_key: str,
) -> str:

    return (
        get_wallet_fernet()
        .decrypt(
            encrypted_private_key.encode()
        )
        .decode()
    )


# ============================================================
# SEND CANCEL
# ============================================================

async def send_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.pop(
        "send_to_address",
        None,
    )

    context.user_data.pop(
        "send_amount",
        None,
    )

    context.user_data.pop(
        "send_gas_cost",
        None,
    )

    await query.edit_message_text(
        "❌ Отправка ETH отменена.",
        reply_markup=back_keyboard(),
    )

    return ConversationHandler.END


# ============================================================
# SUPPORT
# ============================================================

def support_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💰 Пополнение",
                    callback_data="support_cat_deposit",
                ),
                InlineKeyboardButton(
                    "📤 Вывод",
                    callback_data="support_cat_withdraw",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💱 P2P",
                    callback_data="support_cat_p2p",
                ),
                InlineKeyboardButton(
                    "💵 USDT",
                    callback_data="support_cat_usdt",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔐 Безопасность",
                    callback_data="support_cat_security",
                )
            ],
            [
                InlineKeyboardButton(
                    "⚠️ Другое",
                    callback_data="support_cat_other",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Главное меню",
                    callback_data="main",
                )
            ],
        ]
    )


def support_cancel_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="support_cancel",
                )
            ]
        ]
    )


async def support_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "🆘 *Центр поддержки Edaaa*\n\n"
        "Выберите категорию:",
        parse_mode="Markdown",
        reply_markup=support_keyboard(),
    )

    return SUPPORT_CATEGORY


async def support_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    categories = {
        "support_cat_deposit": "Пополнение",
        "support_cat_withdraw": "Вывод",
        "support_cat_p2p": "P2P",
        "support_cat_usdt": "USDT",
        "support_cat_security": "Безопасность",
        "support_cat_other": "Другое",
    }

    category = categories.get(
        query.data
    )

    if not category:

        return ConversationHandler.END

    context.user_data[
        "support_category"
    ] = category

    await query.edit_message_text(
        "🆘 *Новое обращение*\n\n"
        f"Категория: *{category}*\n\n"
        "Напишите тему:",
        parse_mode="Markdown",
        reply_markup=support_cancel_keyboard(),
    )

    return SUPPORT_SUBJECT


async def support_subject(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:

        return SUPPORT_SUBJECT

    subject = (
        update.message.text
        or ""
    ).strip()

    if not subject:

        return SUPPORT_SUBJECT

    if len(subject) > 255:

        await update.message.reply_text(
            "❌ Максимум 255 символов.",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_SUBJECT

    context.user_data[
        "support_subject"
    ] = subject

    await update.message.reply_text(
        "📝 *Опишите проблему подробно.*\n\n"
        "⚠️ Не отправляйте private key, "
        "seed-фразу или пароль.",
        parse_mode="Markdown",
        reply_markup=support_cancel_keyboard(),
    )

    return SUPPORT_MESSAGE


async def support_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:

        return SUPPORT_MESSAGE

    message_text = (
        update.message.text
        or ""
    ).strip()

    if not message_text:

        return SUPPORT_MESSAGE

    if len(message_text) > 5000:

        await update.message.reply_text(
            "❌ Максимум 5000 символов.",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_MESSAGE

    telegram_user = (
        update.effective_user
    )

    if not telegram_user:

        return ConversationHandler.END

    db = get_db()

    try:

        user = get_or_create_user(
            telegram_id=telegram_user.id,
            telegram_username=(
                telegram_user.username
            ),
        )

        category = context.user_data.get(
            "support_category",
            "Другое",
        )

        subject = context.user_data.get(
            "support_subject",
            "Обращение",
        )

        ticket = SupportTicket(
            user_id=user.id,
            category=category,
            subject=subject,
            status="open",
            priority="normal",
        )

        db.add(ticket)
        db.flush()

        db.add(
            SupportMessage(
                ticket_id=ticket.id,
                sender_type="user",
                sender_id=user.id,
                message=message_text,
            )
        )

        db.commit()

        ticket_id = ticket.id

        await notify_admin_about_ticket(
            context=context,
            ticket_id=ticket_id,
            user=user,
            category=category,
            subject=subject,
            message_text=message_text,
        )

        context.user_data.clear()

        await update.message.reply_text(
            "✅ *Обращение создано!*\n\n"
            f"🎫 `#{ticket_id}`\n"
            f"📂 {category}\n"
            f"📌 {subject}",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    except Exception:

        db.rollback()

        logger.exception(
            "Failed to create support ticket."
        )

        await update.message.reply_text(
            "❌ Не удалось создать обращение.",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    finally:

        db.close()


async def support_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "❌ Обращение отменено.",
        reply_markup=back_keyboard(),
    )

    return ConversationHandler.END


# ============================================================
# SUPPORT TICKET VIEW
# ============================================================

async def show_support_ticket(
    query,
    ticket_id: int,
):

    user = get_or_create_user(
        telegram_id=query.from_user.id,
        telegram_username=(
            query.from_user.username
        ),
    )

    db = get_db()

    try:

        ticket = (
            db.query(SupportTicket)
            .filter(
                SupportTicket.id == ticket_id,
                SupportTicket.user_id == user.id,
            )
            .first()
        )

        if not ticket:

            await query.edit_message_text(
                "❌ Обращение не найдено.",
                reply_markup=back_keyboard(),
            )

            return

        messages = (
            db.query(SupportMessage)
            .filter(
                SupportMessage.ticket_id
                == ticket.id
            )
            .order_by(
                SupportMessage.created_at.asc()
            )
            .all()
        )

        lines = [
            f"🎫 *Обращение #{ticket.id}*",
            "",
            f"📂 {ticket.category}",
            f"📌 {ticket.subject}",
            f"📊 {ticket.status}",
            "",
            "💬 *Переписка*",
            "",
        ]

        for message in messages:

            sender = (
                "👤 Вы"
                if message.sender_type == "user"
                else "🛡 Поддержка"
            )

            lines.append(
                f"{sender}:\n"
                f"{message.message}\n"
            )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

    finally:

        db.close()


# ============================================================
# ADMIN TICKET
# ============================================================

async def show_admin_ticket(
    query,
    ticket_id: int,
):

    admin_id = (
        get_admin_telegram_id()
    )

    if (
        not admin_id
        or query.from_user.id
        != admin_id
    ):

        await query.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )

        return

    db = get_db()

    try:

        ticket = (
            db.query(SupportTicket)
            .filter(
                SupportTicket.id
                == ticket_id
            )
            .first()
        )

        if not ticket:

            await query.edit_message_text(
                "❌ Обращение не найдено."
            )

            return

        user = (
            db.query(User)
            .filter(
                User.id == ticket.user_id
            )
            .first()
        )

        messages = (
            db.query(SupportMessage)
            .filter(
                SupportMessage.ticket_id
                == ticket.id
            )
            .order_by(
                SupportMessage.created_at.asc()
            )
            .all()
        )

        lines = [
            "🛡 *Поддержка Edaaa*",
            "",
            f"🎫 `#{ticket.id}`",
            f"📂 {ticket.category}",
            f"📌 {ticket.subject}",
            f"📊 {ticket.status}",
            f"🔥 {ticket.priority}",
            "",
            f"👤 User ID: `{ticket.user_id}`",
            "",
        ]

        for message in messages:

            sender = (
                "👤 Пользователь"
                if message.sender_type == "user"
                else "🛡 Администратор"
            )

            lines.append(
                f"{sender}:\n"
                f"{message.message}\n"
            )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Обновить",
                            callback_data=(
                                f"admin_ticket_{ticket.id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🏠 Главное меню",
                            callback_data="main",
                        )
                    ],
                ]
            ),
        )

    finally:

        db.close()


# ============================================================
# MAIN VIEWS
# ============================================================

async def show_main(
    query,
    wallet,
):

    await query.edit_message_text(
        "🏦 *Edaaa Wallet*\n\n"
        f"🌐 `{wallet.network}`\n\n"
        "Выберите действие:",
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

    await query.edit_message_text(
        "💰 *Ваш Edaaa Wallet*\n\n"
        f"Ξ ETH: `{balances['ETH']}`\n"
        f"💵 USDT: `{balances['USDT']}`\n\n"
        f"🌐 `{wallet.network}`\n\n"
        "📍\n"
        f"`{wallet.address}`",
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


async def show_deposit(
    query,
    wallet,
):

    await query.edit_message_text(
        "📥 *Пополнение*\n\n"
        "Отправьте ETH на адрес:\n\n"
        f"`{wallet.address}`\n\n"
        f"🌐 `{settings.ETH_NETWORK}`\n\n"
        "После 3 подтверждений депозит "
        "будет зачислен.",
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
            "Операций пока нет."
        )

    else:

        lines = [
            "📜 *История операций*",
            "",
        ]

        for transaction in transactions:

            tx_icon = (
                "📥"
                if transaction.type == "deposit"
                else "📤"
                if transaction.type == "withdraw"
                else "🔄"
            )

            lines.append(
                f"{tx_icon} "
                f"{transaction.type} — "
                f"{transaction.amount} "
                f"{transaction.asset} — "
                f"`{transaction.status}`"
            )

        text = "\n".join(
            lines
        )

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

    await query.edit_message_text(
        "👤 *Профиль Edaaa*\n\n"
        f"Telegram: `{username}`\n"
        f"Edaaa User ID: `{user.id}`\n"
        f"Telegram ID: `{user.telegram_id}`\n\n"
        f"💼 `{wallet.address}`\n"
        f"🌐 `{wallet.network}`",
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


async def show_support(
    query,
):

    await query.edit_message_text(
        "🆘 *Центр поддержки Edaaa*\n\n"
        "Выберите категорию:",
        parse_mode="Markdown",
        reply_markup=support_keyboard(),
    )


# ============================================================
# CALLBACK
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not query.from_user:

        return

    try:

        if query.data.startswith(
            "admin_ticket_"
        ):

            ticket_id = int(
                query.data.replace(
                    "admin_ticket_",
                    "",
                    1,
                )
            )

            await show_admin_ticket(
                query,
                ticket_id,
            )

            return

        user = get_or_create_user(
            telegram_id=query.from_user.id,
            telegram_username=(
                query.from_user.username
            ),
        )

        wallet = get_user_wallet(
            user.id
        )

        if not wallet:

            await query.edit_message_text(
                "❌ Кошелёк не найден.",
                reply_markup=back_keyboard(),
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

        elif query.data == "support":

            await show_support(
                query
            )

        elif query.data.startswith(
            "support_ticket_"
        ):

            ticket_id = int(
                query.data.replace(
                    "support_ticket_",
                    "",
                    1,
                )
            )

            await show_support_ticket(
                query,
                ticket_id,
            )

        elif query.data == "buy_usdt":

            await query.edit_message_text(
                "💱 *Купить USDT*\n\n"
                "P2P-модуль готовим следующим этапом.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        elif query.data == "sell_usdt":

            await query.edit_message_text(
                "💵 *Продать USDT*\n\n"
                "P2P-модуль готовим следующим этапом.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

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

        try:

            await query.edit_message_text(
                "❌ Произошла ошибка.",
                reply_markup=back_keyboard(),
            )

        except Exception:

            pass


# ============================================================
# ERROR
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
# APPLICATION
# ============================================================

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

    send_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                send_start,
                pattern="^send$",
            )
        ],
        states={
            SEND_ADDRESS: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    send_address,
                ),
                CallbackQueryHandler(
                    send_cancel,
                    pattern="^send_cancel$",
                ),
            ],
            SEND_AMOUNT: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    send_amount,
                ),
                CallbackQueryHandler(
                    send_cancel,
                    pattern="^send_cancel$",
                ),
            ],
            SEND_CONFIRM: [
                CallbackQueryHandler(
                    send_confirm,
                    pattern="^send_confirm$",
                ),
                CallbackQueryHandler(
                    send_cancel,
                    pattern="^send_cancel$",
                ),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(
                send_cancel,
                pattern="^send_cancel$",
            )
        ],
        allow_reentry=True,
    )

    application.add_handler(
        send_conversation
    )

    support_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                support_start,
                pattern="^support$",
            )
        ],
        states={
            SUPPORT_CATEGORY: [
                CallbackQueryHandler(
                    support_category,
                    pattern="^support_cat_",
                ),
                CallbackQueryHandler(
                    support_cancel,
                    pattern="^support_cancel$",
                ),
            ],
            SUPPORT_SUBJECT: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    support_subject,
                ),
                CallbackQueryHandler(
                    support_cancel,
                    pattern="^support_cancel$",
                ),
            ],
            SUPPORT_MESSAGE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    support_message,
                ),
                CallbackQueryHandler(
                    support_cancel,
                    pattern="^support_cancel$",
                ),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(
                support_cancel,
                pattern="^support_cancel$",
            )
        ],
        allow_reentry=True,
    )

    application.add_handler(
        support_conversation
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
