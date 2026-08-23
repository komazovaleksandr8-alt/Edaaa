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
# SUPPORT STATES
# ============================================================

SUPPORT_CATEGORY = 1
SUPPORT_SUBJECT = 2
SUPPORT_MESSAGE = 3


# ============================================================
# SEND ETH STATES
# ============================================================

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
        logger.warning(
            "ADMIN_TELEGRAM_ID is not configured."
        )
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

    telegram_username = (
        f"@{user.telegram_username}"
        if user.telegram_username
        else "не указан"
    )

    text = (
        "🚨 *НОВОЕ ОБРАЩЕНИЕ В ПОДДЕРЖКУ EDAAA*\n\n"
        f"🎫 Номер: `#{ticket_id}`\n"
        f"📂 Категория: *{category}*\n"
        f"📌 Тема: *{subject}*\n\n"
        "👤 *Пользователь*\n"
        f"Telegram: `{telegram_username}`\n"
        f"Edaaa User ID: `{user.id}`\n"
        f"Telegram ID: `{user.telegram_id}`\n\n"
        "💬 *Сообщение*\n"
        f"{message_text}\n\n"
        "⚠️ Не запрашивайте private key "
        "или seed-фразу."
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📨 Открыть обращение",
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
            telegram_id=telegram_id_string,
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
# MAIN KEYBOARD
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
                    "🆘 Поддержка",
                    callback_data="support",
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
# SEND KEYBOARDS
# ============================================================


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
                    "✅ Подтвердить отправку",
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
# /START
# ============================================================


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_user:
        return

    telegram_user = update.effective_user

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
            "🏦 *Добро пожаловать "
            "в Edaaa Wallet!*\n\n"
            "🔐 Ваш Telegram подключён "
            "к Edaaa.\n\n"
            "Ваш персональный Ethereum-"
            "кошелёк уже создан.\n\n"
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
        "Введите Ethereum-адрес получателя.\n\n"
        "Например:\n"
        "`0x742d35Cc6634C0532925a3b844Bc454e4438f44e`\n\n"
        f"🌐 Сеть: `{settings.ETH_NETWORK}`",
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

        address = validate_recipient_address(
            address
        )

    except Exception:

        await update.message.reply_text(
            "❌ Некорректный Ethereum-адрес.\n\n"
            "Введите адрес ещё раз:",
            reply_markup=send_cancel_keyboard(),
        )

        return SEND_ADDRESS

    telegram_user = update.effective_user

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
            "на собственный адрес.\n\n"
            "Введите другой адрес:",
            reply_markup=send_cancel_keyboard(),
        )

        return SEND_ADDRESS

    context.user_data[
        "send_to_address"
    ] = address

    await update.message.reply_text(
        "💰 *Введите сумму ETH*\n\n"
        "Укажите количество ETH, "
        "которое хотите отправить.\n\n"
        "Например:\n"
        "`0.001`",
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
            "❌ Некорректная сумма.\n\n"
            "Введите число, например:\n"
            "`0.001`",
            parse_mode="Markdown",
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
            "❌ Максимальная точность — "
            "18 знаков после запятой.",
            reply_markup=send_cancel_keyboard(),
        )

        return SEND_AMOUNT

    telegram_user = update.effective_user

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

            await update.message.reply_text(
                "❌ Кошелёк не найден.",
                reply_markup=back_keyboard(),
            )

            return ConversationHandler.END

        web3 = get_web3()

        sender_address = (
            Web3.to_checksum_address(
                wallet.address
            )
        )

        balance_wei = web3.eth.get_balance(
            sender_address
        )

        balance_eth = Decimal(
            str(
                Web3.from_wei(
                    balance_wei,
                    "ether",
                )
            )
        )

        gas_price = web3.eth.gas_price

        gas_limit = 21000

        gas_cost_wei = (
            gas_price * gas_limit
        )

        gas_cost_eth = Decimal(
            str(
                Web3.from_wei(
                    gas_cost_wei,
                    "ether",
                )
            )
        )

        if balance_wei <= 0:

            await update.message.reply_text(
                "❌ На кошельке нет ETH.",
                reply_markup=back_keyboard(),
            )

            return ConversationHandler.END

        if balance_wei < (
            Web3.to_wei(
                amount,
                "ether",
            )
            + gas_cost_wei
        ):

            await update.message.reply_text(
                "❌ Недостаточно ETH.\n\n"
                f"Баланс: `{balance_eth} ETH`\n"
                f"Сумма: `{amount} ETH`\n"
                f"Комиссия: ~`{gas_cost_eth} ETH`\n\n"
                "Уменьшите сумму или пополните кошелёк.",
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

        to_address = context.user_data.get(
            "send_to_address"
        )

        await update.message.reply_text(
            "🔎 *Проверка отправки*\n\n"
            f"🌐 Сеть: `{settings.ETH_NETWORK}`\n\n"
            f"📤 Получатель:\n`{to_address}`\n\n"
            f"💰 Сумма: `{amount} ETH`\n"
            f"⛽ Комиссия: ~`{gas_cost_eth} ETH`\n\n"
            f"💳 Баланс: `{balance_eth} ETH`\n\n"
            "Проверьте данные перед отправкой.",
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
            f"`{str(exc)}`",
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
        "Отправляем транзакцию..."
    )

    telegram_user = query.from_user

    if not telegram_user:

        await query.edit_message_text(
            "❌ Пользователь не найден.",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    to_address = context.user_data.get(
        "send_to_address"
    )

    amount_text = context.user_data.get(
        "send_amount"
    )

    if not to_address or not amount_text:

        await query.edit_message_text(
            "❌ Данные транзакции потеряны.\n\n"
            "Начните отправку заново.",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

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

        db = get_db()

        try:

            wallet = (
                db.query(Wallet)
                .filter(
                    Wallet.user_id
                    == user.id
                )
                .first()
            )

            if not wallet:

                await query.edit_message_text(
                    "❌ Кошелёк не найден.",
                    reply_markup=back_keyboard(),
                )

                return ConversationHandler.END

            if (
                wallet.network
                != settings.ETH_NETWORK
            ):

                await query.edit_message_text(
                    "❌ Сеть кошелька "
                    "не совпадает с сетью Edaaa.",
                    reply_markup=back_keyboard(),
                )

                return ConversationHandler.END

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

                await query.edit_message_text(
                    "❌ Нельзя отправить ETH "
                    "на собственный адрес.",
                    reply_markup=back_keyboard(),
                )

                return ConversationHandler.END

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

                transaction = (
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
                        transaction=transaction,
                    )
                )

            finally:

                private_key = None

            send_transaction = Transaction(
                wallet_id=wallet.id,
                type="withdraw",
                asset="ETH",
                amount=amount,
                status="pending",
                tx_hash=tx_hash,
            )

            db.add(
                send_transaction
            )

            db.commit()

            db.refresh(
                send_transaction
            )

        finally:

            db.close()

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
            "✅ *ETH отправлен!*\n\n"
            f"💰 Сумма: `{amount} ETH`\n\n"
            "📤 Получатель:\n"
            f"`{to_address}`\n\n"
            f"🌐 Сеть: `{settings.ETH_NETWORK}`\n\n"
            "📜 TX Hash:\n"
            f"`{tx_hash}`\n\n"
            "📊 Статус: `pending`\n\n"
            "После подтверждения блокчейном "
            "транзакция будет обработана.",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    except Exception as exc:

        logger.exception(
            "ETH send failed."
        )

        error_text = str(exc)

        if len(error_text) > 1000:
            error_text = error_text[:1000]

        await query.edit_message_text(
            "❌ *Не удалось отправить ETH.*\n\n"
            f"`{error_text}`",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END


# ============================================================
# PRIVATE KEY DECRYPTION
# ============================================================


def _decrypt_private_key(
    encrypted_private_key: str,
) -> str:

    fernet = get_wallet_fernet()

    return fernet.decrypt(
        encrypted_private_key.encode()
    ).decode()


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
# SUPPORT KEYBOARDS
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
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚠️ Другое",
                    callback_data="support_cat_other",
                ),
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


# ============================================================
# SUPPORT START
# ============================================================


async def support_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.pop(
        "support_category",
        None,
    )

    context.user_data.pop(
        "support_subject",
        None,
    )

    context.user_data.pop(
        "support_message",
        None,
    )

    await query.edit_message_text(
        "🆘 *Центр поддержки Edaaa*\n\n"
        "Выберите категорию обращения:",
        parse_mode="Markdown",
        reply_markup=support_keyboard(),
    )

    return SUPPORT_CATEGORY


# ============================================================
# SUPPORT CATEGORY
# ============================================================


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

        await query.edit_message_text(
            "❌ Неизвестная категория.",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    context.user_data[
        "support_category"
    ] = category

    await query.edit_message_text(
        "🆘 *Новое обращение*\n\n"
        f"Категория: *{category}*\n\n"
        "Теперь напишите краткую тему обращения.",
        parse_mode="Markdown",
        reply_markup=support_cancel_keyboard(),
    )

    return SUPPORT_SUBJECT


# ============================================================
# SUPPORT SUBJECT
# ============================================================


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

        await update.message.reply_text(
            "❌ Тема не может быть пустой.",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_SUBJECT

    if len(subject) > 255:

        await update.message.reply_text(
            "❌ Максимум — 255 символов.",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_SUBJECT

    context.user_data[
        "support_subject"
    ] = subject

    await update.message.reply_text(
        "📝 *Опишите проблему подробно.*\n\n"
        "⚠️ Никогда не отправляйте "
        "private key, seed-фразу или пароль.",
        parse_mode="Markdown",
        reply_markup=support_cancel_keyboard(),
    )

    return SUPPORT_MESSAGE


# ============================================================
# SUPPORT MESSAGE
# ============================================================


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

        await update.message.reply_text(
            "❌ Сообщение не может быть пустым.",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_MESSAGE

    if len(message_text) > 5000:

        await update.message.reply_text(
            "❌ Максимум — 5000 символов.",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_MESSAGE

    telegram_user = update.effective_user

    if not telegram_user:
        return ConversationHandler.END

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
            "Обращение в поддержку",
        )

        db = get_db()

        try:

            ticket = SupportTicket(
                user_id=user.id,
                category=category,
                subject=subject,
                status="open",
                priority="normal",
            )

            db.add(ticket)
            db.flush()

            support_message_record = SupportMessage(
                ticket_id=ticket.id,
                sender_type="user",
                sender_id=user.id,
                message=message_text,
            )

            db.add(
                support_message_record
            )

            db.commit()
            db.refresh(ticket)

            ticket_id = ticket.id

        except Exception:

            db.rollback()
            raise

        finally:

            db.close()

        await notify_admin_about_ticket(
            context=context,
            ticket_id=ticket_id,
            user=user,
            category=category,
            subject=subject,
            message_text=message_text,
        )

        context.user_data.pop(
            "support_category",
            None,
        )

        context.user_data.pop(
            "support_subject",
            None,
        )

        await update.message.reply_text(
            "✅ *Обращение создано!*\n\n"
            f"🎫 Номер: `#{ticket_id}`\n"
            f"📂 Категория: *{category}*\n"
            f"📌 Тема: *{subject}*",
            parse_mode="Markdown",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END

    except Exception:

        logger.exception(
            "Failed to create support ticket."
        )

        await update.message.reply_text(
            "❌ Не удалось создать обращение.",
            reply_markup=back_keyboard(),
        )

        return ConversationHandler.END


# ============================================================
# SUPPORT CANCEL
# ============================================================


async def support_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.pop(
        "support_category",
        None,
    )

    context.user_data.pop(
        "support_subject",
        None,
    )

    context.user_data.pop(
        "support_message",
        None,
    )

    await query.edit_message_text(
        "❌ Создание обращения отменено.",
        reply_markup=back_keyboard(),
    )

    return ConversationHandler.END


# ============================================================
# SHOW SUPPORT TICKET
# ============================================================


async def show_support_ticket(
    query,
    ticket_id: int,
):

    telegram_user = query.from_user

    user = get_or_create_user(
        telegram_id=telegram_user.id,
        telegram_username=(
            telegram_user.username
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
            f"📂 Категория: *{ticket.category}*",
            f"📌 Тема: *{ticket.subject}*",
            f"📊 Статус: *{ticket.status}*",
            "",
            "💬 *Переписка:*",
            "",
        ]

        for message in messages:

            sender = (
                "👤 Вы"
                if message.sender_type == "user"
                else "🛡 Поддержка"
                if message.sender_type == "admin"
                else "🤖 Edaaa"
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
                            "⬅️ Поддержка",
                            callback_data="support",
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
# SHOW SUPPORT
# ============================================================


async def show_support(query):

    await query.edit_message_text(
        "🆘 *Центр поддержки Edaaa*\n\n"
        "Выберите категорию проблемы:",
        parse_mode="Markdown",
        reply_markup=support_keyboard(),
    )


# ============================================================
# ADMIN SHOW TICKET
# ============================================================


async def show_admin_ticket(
    query,
    ticket_id: int,
):

    admin_telegram_id = (
        get_admin_telegram_id()
    )

    if (
        not admin_telegram_id
        or query.from_user.id
        != admin_telegram_id
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

        telegram_username = (
            f"@{user.telegram_username}"
            if user and user.telegram_username
            else "не указан"
        )

        lines = [
            "🛡 *Панель поддержки Edaaa*",
            "",
            f"🎫 Обращение `#{ticket.id}`",
            f"📂 Категория: *{ticket.category}*",
            f"📌 Тема: *{ticket.subject}*",
            f"📊 Статус: *{ticket.status}*",
            f"🔥 Приоритет: *{ticket.priority}*",
            "",
            "👤 *Пользователь*",
            f"Telegram: `{telegram_username}`",
            f"User ID: `{ticket.user_id}`",
            "",
            "💬 *Переписка*",
            "",
        ]

        for message in messages:

            sender = (
                "👤 Пользователь"
                if message.sender_type == "user"
                else "🛡 Администратор"
                if message.sender_type == "admin"
                else "🤖 Edaaa"
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
# CALLBACK HANDLER
# ============================================================


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not query.from_user:
        return

    telegram_user = query.from_user

    try:

        if query.data.startswith(
            "admin_ticket_"
        ):

            try:

                ticket_id = int(
                    query.data.replace(
                        "admin_ticket_",
                        "",
                        1,
                    )
                )

            except ValueError:

                await query.edit_message_text(
                    "❌ Некорректный номер обращения."
                )

                return

            await show_admin_ticket(
                query,
                ticket_id,
            )

            return

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

        elif query.data == "buy_usdt":

            await query.edit_message_text(
                "💱 *Купить USDT*\n\n"
                "P2P-модуль находится "
                "в разработке.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        elif query.data == "sell_usdt":

            await query.edit_message_text(
                "💵 *Продать USDT*\n\n"
                "P2P-модуль находится "
                "в разработке.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        elif query.data == "support":

            await show_support(query)

        elif query.data.startswith(
            "support_ticket_"
        ):

            try:

                ticket_id = int(
                    query.data.replace(
                        "support_ticket_",
                        "",
                        1,
                    )
                )

            except ValueError:

                await query.edit_message_text(
                    "❌ Некорректный номер обращения.",
                    reply_markup=back_keyboard(),
                )

                return

            await show_support_ticket(
                query,
                ticket_id,
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
                "❌ Произошла ошибка.\n\n"
                "Попробуйте ещё раз.",
                reply_markup=back_keyboard(),
            )

        except Exception:

            pass


# ============================================================
# MAIN MENU
# ============================================================


async def show_main(
    query,
    wallet,
):

    await query.edit_message_text(
        "🏦 *Edaaa Wallet*\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "Выберите действие:",
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

    await query.edit_message_text(
        "💰 *Ваш Edaaa Wallet*\n\n"
        f"Ξ ETH: `{balances['ETH']}`\n"
        f"💵 USDT: `{balances['USDT']}`\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "📍 Адрес:\n"
        f"`{wallet.address}`",
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

    await query.edit_message_text(
        "📥 *Пополнение кошелька*\n\n"
        "Отправьте ETH на адрес:\n\n"
        f"`{wallet.address}`\n\n"
        f"🌐 Сеть: `{wallet.network}`\n\n"
        "После необходимого количества "
        "подтверждений депозит будет зачислен.",
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

            lines.append(
                f"• {transaction.type} — "
                f"{transaction.amount} "
                f"{transaction.asset} — "
                f"{transaction.status}"
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
        "💼 Wallet:\n"
        f"`{wallet.address}`\n\n"
        f"🌐 Network: `{wallet.network}`",
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
# CREATE APPLICATION
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

    # ========================================================
    # SEND ETH CONVERSATION
    # ========================================================

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

    # ========================================================
    # SUPPORT CONVERSATION
    # ========================================================

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
