import logging
import os
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
# DATABASE
# ============================================================


def get_db() -> Session:
    return SessionLocal()


# ============================================================
# ADMIN
# ============================================================


def get_admin_telegram_id() -> int | None:
    """
    Получает Telegram ID администратора
    из переменной окружения ADMIN_TELEGRAM_ID.

    Пример:

    ADMIN_TELEGRAM_ID=123456789
    """

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
    """
    Отправляет администратору уведомление
    о новом обращении пользователя.

    Ошибка отправки админу НЕ ломает создание
    тикета в базе данных.
    """

    admin_telegram_id = (
        get_admin_telegram_id()
    )

    if not admin_telegram_id:
        logger.warning(
            "Support ticket #%s created, "
            "but ADMIN_TELEGRAM_ID is not configured.",
            ticket_id,
        )
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
        "⚠️ Не запрашивайте у пользователя "
        "private key или seed-фразу."
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

        logger.info(
            "Support notification sent to admin | "
            "ticket=%s | "
            "admin=%s",
            ticket_id,
            admin_telegram_id,
        )

    except Exception:

        logger.exception(
            "Failed to notify admin about "
            "support ticket #%s.",
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

        db.add(eth_balance)

        usdt_balance = Balance(
            wallet_id=wallet.id,
            asset="USDT",
            amount=Decimal("0"),
        )

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
        "Теперь напишите краткую тему "
        "обращения.\n\n"
        "Например:\n"
        "`Не пришёл депозит`\n"
        "`Проблема с P2P`\n"
        "`Не могу вывести ETH`",
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
            "❌ Тема не может быть пустой.\n\n"
            "Напишите тему обращения:",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_SUBJECT

    if len(subject) > 255:

        await update.message.reply_text(
            "❌ Тема слишком длинная.\n\n"
            "Максимум — 255 символов.",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_SUBJECT

    context.user_data[
        "support_subject"
    ] = subject

    await update.message.reply_text(
        "📝 *Опишите проблему подробно.*\n\n"
        "Напишите одним сообщением всё, "
        "что поможет поддержке разобраться "
        "в ситуации.\n\n"
        "Например:\n"
        "• что произошло;\n"
        "• сумма;\n"
        "• адрес кошелька;\n"
        "• TX Hash;\n"
        "• номер P2P-сделки.\n\n"
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
            "❌ Сообщение слишком длинное.\n\n"
            "Максимум — 5000 символов.",
            reply_markup=support_cancel_keyboard(),
        )

        return SUPPORT_MESSAGE

    telegram_user = (
        update.effective_user
    )

    if not telegram_user:

        await update.message.reply_text(
            "❌ Не удалось определить "
            "Telegram пользователя."
        )

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

            support_message_record = (
                SupportMessage(
                    ticket_id=ticket.id,
                    sender_type="user",
                    sender_id=user.id,
                    message=message_text,
                )
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

        # ====================================================
        # NOTIFY ADMIN
        # ====================================================

        await notify_admin_about_ticket(
            context=context,
            ticket_id=ticket_id,
            user=user,
            category=category,
            subject=subject,
            message_text=message_text,
        )

        # ====================================================
        # CLEAR SUPPORT STATE
        # ====================================================

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

        # ====================================================
        # CLIENT RESPONSE
        # ====================================================

        await update.message.reply_text(
            "✅ *Обращение создано!*\n\n"
            f"🎫 Номер обращения: `#{ticket_id}`\n"
            f"📂 Категория: *{category}*\n"
            f"📌 Тема: *{subject}*\n\n"
            "Ваше сообщение получено "
            "и передано в службу поддержки.\n\n"
            "Сотрудник поддержки сможет "
            "ответить на него.\n\n"
            "⚠️ Никому не отправляйте "
            "private key или seed-фразу.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📨 Открыть обращение",
                            callback_data=(
                                f"support_ticket_{ticket_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Главное меню",
                            callback_data="main",
                        )
                    ],
                ]
            ),
        )

        return ConversationHandler.END

    except Exception:

        logger.exception(
            "Failed to create support ticket."
        )

        await update.message.reply_text(
            "❌ Не удалось создать обращение.\n\n"
            "Попробуйте ещё раз через несколько секунд.",
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
                SupportTicket.id
                == ticket_id,
                SupportTicket.user_id
                == user.id,
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

            if (
                message.sender_type
                == "user"
            ):

                sender = "👤 Вы"

            elif (
                message.sender_type
                == "admin"
            ):

                sender = "🛡 Поддержка"

            else:

                sender = "🤖 Edaaa"

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


async def show_support(
    query,
):

    await query.edit_message_text(
        "🆘 *Центр поддержки Edaaa*\n\n"
        "Здесь вы можете создать обращение "
        "в службу поддержки.\n\n"
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

            if message.sender_type == "user":

                sender = "👤 Пользователь"

            elif message.sender_type == "admin":

                sender = "🛡 Администратор"

            else:

                sender = "🤖 Edaaa"

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

    telegram_user = (
        query.from_user
    )

    try:

        # ====================================================
        # ADMIN TICKET
        # ====================================================

        if query.data.startswith(
            "admin_ticket_"
        ):

            ticket_id_string = (
                query.data.replace(
                    "admin_ticket_",
                    "",
                    1,
                )
            )

            try:

                ticket_id = int(
                    ticket_id_string
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

        # ====================================================
        # USER
        # ====================================================

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

        # ====================================================
        # MAIN
        # ====================================================

        if query.data == "main":

            await show_main(
                query,
                wallet,
            )

        # ====================================================
        # WALLET
        # ====================================================

        elif query.data == "wallet":

            await show_wallet(
                query,
                wallet,
            )

        # ====================================================
        # DEPOSIT
        # ====================================================

        elif query.data == "deposit":

            await show_deposit(
                query,
                wallet,
            )

        # ====================================================
        # SEND
        # ====================================================

        elif query.data == "send":

            await query.edit_message_text(
                "📤 *Отправка ETH*\n\n"
                "Функция отправки ETH "
                "доступна через Edaaa API.\n\n"
                "Telegram-интерфейс отправки "
                "будем расширять следующим этапом.\n\n"
                "План:\n"
                "1️⃣ Адрес получателя\n"
                "2️⃣ Сумма\n"
                "3️⃣ Проверка комиссии\n"
                "4️⃣ Подтверждение\n"
                "5️⃣ Отправка\n"
                "6️⃣ TX Hash",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        # ====================================================
        # HISTORY
        # ====================================================

        elif query.data == "history":

            await show_history(
                query,
                wallet,
            )

        # ====================================================
        # BUY
        # ====================================================

        elif query.data == "buy_usdt":

            await query.edit_message_text(
                "💱 *Купить USDT*\n\n"
                "P2P-модуль находится "
                "в разработке.\n\n"
                "Следующим этапом добавим "
                "заявки, оплату и защиту сделки.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        # ====================================================
        # SELL
        # ====================================================

        elif query.data == "sell_usdt":

            await query.edit_message_text(
                "💵 *Продать USDT*\n\n"
                "P2P-модуль находится "
                "в разработке.\n\n"
                "Следующим этапом добавим "
                "заявки, оплату и защиту сделки.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        # ====================================================
        # SUPPORT
        # ====================================================

        elif query.data == "support":

            await show_support(
                query
            )

        # ====================================================
        # SUPPORT TICKET
        # ====================================================

        elif query.data.startswith(
            "support_ticket_"
        ):

            ticket_id_string = (
                query.data.replace(
                    "support_ticket_",
                    "",
                    1,
                )
            )

            try:

                ticket_id = int(
                    ticket_id_string
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

        # ====================================================
        # PROFILE
        # ====================================================

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
        "После необходимого количества "
        "подтверждений транзакция будет "
        "зачислена в Edaaa."
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

            tx_type = transaction.type
            amount = transaction.amount
            asset = transaction.asset
            tx_status = transaction.status

            lines.append(
                f"• {tx_type} — "
                f"{amount} {asset} — "
                f"{tx_status}"
            )

        text = "\n".join(
            lines
        )

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

        username = "не указан"

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

    # ========================================================
    # /START
    # ========================================================

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # ========================================================
    # SUPPORT CONVERSATION
    # ========================================================

    support_conversation = (
        ConversationHandler(
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
                        pattern=(
                            "^support_cat_"
                        ),
                    ),
                    CallbackQueryHandler(
                        support_cancel,
                        pattern=(
                            "^support_cancel$"
                        ),
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
                        pattern=(
                            "^support_cancel$"
                        ),
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
                        pattern=(
                            "^support_cancel$"
                        ),
                    ),
                ],
            },
            fallbacks=[
                CallbackQueryHandler(
                    support_cancel,
                    pattern=(
                        "^support_cancel$"
                    ),
                )
            ],
            allow_reentry=True,
        )
    )

    application.add_handler(
        support_conversation
    )

    # ========================================================
    # NORMAL CALLBACKS
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    # ========================================================
    # ERRORS
    # ========================================================

    application.add_error_handler(
        error_handler
    )

    return application
