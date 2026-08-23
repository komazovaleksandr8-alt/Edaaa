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

from app.support_models import (
    SupportTicket,
    SupportMessage,
)


logger = logging.getLogger(
    "edaaa.telegram"
)


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
# SUPPORT
# ============================================================


def create_support_ticket(
    user_id: int,
    category: str,
    subject: str,
    message: str,
):

    db = get_db()

    try:

        ticket = SupportTicket(
            user_id=user_id,
            category=category,
            subject=subject,
            status="open",
            priority="normal",
        )

        db.add(ticket)

        db.flush()

        support_message = SupportMessage(
            ticket_id=ticket.id,
            sender_type="user",
            sender_id=user_id,
            message=message,
        )

        db.add(
            support_message
        )

        db.commit()

        db.refresh(ticket)

        return ticket

    except Exception:

        db.rollback()

        logger.exception(
            "Failed to create support ticket."
        )

        raise

    finally:

        db.close()


def get_user_support_tickets(
    user_id: int,
):

    db = get_db()

    try:

        return (
            db.query(SupportTicket)
            .filter(
                SupportTicket.user_id
                == user_id
            )
            .order_by(
                SupportTicket.created_at.desc()
            )
            .limit(20)
            .all()
        )

    finally:

        db.close()


def get_support_ticket(
    user_id: int,
    ticket_id: int,
):

    db = get_db()

    try:

        return (
            db.query(SupportTicket)
            .filter(
                SupportTicket.id
                == ticket_id,
                SupportTicket.user_id
                == user_id,
            )
            .first()
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
            [
                InlineKeyboardButton(
                    "👨‍💻 Поддержка",
                    callback_data="support",
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


def support_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📝 Новое обращение",
                    callback_data="support_new",
                )
            ],
            [
                InlineKeyboardButton(
                    "📂 Мои обращения",
                    callback_data="support_my",
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


def support_categories_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💱 P2P",
                    callback_data="support_cat_p2p",
                ),
                InlineKeyboardButton(
                    "💰 Кошелёк",
                    callback_data="support_cat_wallet",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📥 Депозит",
                    callback_data="support_cat_deposit",
                ),
                InlineKeyboardButton(
                    "📤 Вывод",
                    callback_data="support_cat_withdraw",
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚙️ Техническая проблема",
                    callback_data="support_cat_technical",
                )
            ],
            [
                InlineKeyboardButton(
                    "❓ Другое",
                    callback_data="support_cat_general",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="support",
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

        # ====================================================
        # HISTORY
        # ====================================================

        elif query.data == "history":

            await show_history(
                query,
                wallet,
            )

        # ====================================================
        # BUY USDT
        # ====================================================

        elif query.data == "buy_usdt":

            await query.edit_message_text(
                "💱 *Купить USDT*\n\n"
                "P2P-модуль пока находится "
                "в разработке.\n\n"
                "Следующим этапом подключим "
                "полноценные P2P-сделки.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

        # ====================================================
        # SELL USDT
        # ====================================================

        elif query.data == "sell_usdt":

            await query.edit_message_text(
                "💵 *Продать USDT*\n\n"
                "P2P-модуль пока находится "
                "в разработке.\n\n"
                "Следующим этапом подключим "
                "полноценные P2P-сделки.",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
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

        # ====================================================
        # SUPPORT
        # ====================================================

        elif query.data == "support":

            await show_support(
                query
            )

        # ====================================================
        # SUPPORT NEW
        # ====================================================

        elif query.data == "support_new":

            await show_support_categories(
                query
            )

        # ====================================================
        # SUPPORT CATEGORY
        # ====================================================

        elif query.data.startswith(
            "support_cat_"
        ):

            category = (
                query.data.replace(
                    "support_cat_",
                    "",
                )
            )

            context.user_data[
                "support_category"
            ] = category

            await query.edit_message_text(
                "📝 *Новое обращение*\n\n"
                f"Категория: `{category}`\n\n"
                "Теперь отправьте следующим "
                "сообщением тему обращения.\n\n"
                "Например:\n"
                "`Не пришёл депозит`",
                parse_mode="Markdown",
                reply_markup=back_keyboard(),
            )

            context.user_data[
                "support_waiting_subject"
            ] = True

        # ====================================================
        # MY SUPPORT
        # ====================================================

        elif query.data == "support_my":

            await show_my_support(
                query,
                user,
            )

        # ====================================================
        # OPEN TICKET
        # ====================================================

        elif query.data.startswith(
            "support_ticket_"
        ):

            ticket_id = int(
                query.data.replace(
                    "support_ticket_",
                    "",
                )
            )

            await show_support_ticket(
                query,
                user,
                ticket_id,
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
# SUPPORT SCREEN
# ============================================================


async def show_support(
    query,
):

    text = (
        "👨‍💻 *Поддержка Edaaa*\n\n"
        "Здесь вы можете обратиться "
        "в службу поддержки.\n\n"
        "Мы рекомендуем указывать:\n"
        "• номер сделки;\n"
        "• TX Hash;\n"
        "• адрес кошелька;\n"
        "• подробное описание проблемы.\n\n"
        "Выберите действие:"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=support_keyboard(),
    )


async def show_support_categories(
    query,
):

    text = (
        "📝 *Создание обращения*\n\n"
        "Выберите категорию:"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=support_categories_keyboard(),
    )


async def show_my_support(
    query,
    user,
):

    tickets = get_user_support_tickets(
        user.id
    )

    if not tickets:

        await query.edit_message_text(
            "📂 *Мои обращения*\n\n"
            "У вас пока нет обращений.",
            parse_mode="Markdown",
            reply_markup=support_keyboard(),
        )

        return

    buttons = []

    for ticket in tickets:

        buttons.append(
            [
                InlineKeyboardButton(
                    f"#{ticket.id} — {ticket.subject[:30]}",
                    callback_data=(
                        f"support_ticket_{ticket.id}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="support",
            )
        ]
    )

    await query.edit_message_text(
        "📂 *Мои обращения*\n\n"
        "Выберите обращение:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
    )


async def show_support_ticket(
    query,
    user,
    ticket_id: int,
):

    ticket = get_support_ticket(
        user.id,
        ticket_id,
    )

    if not ticket:

        await query.edit_message_text(
            "❌ Обращение не найдено.",
            reply_markup=support_keyboard(),
        )

        return

    db = get_db()

    try:

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
            f"Тема: *{ticket.subject}*",
            f"Категория: `{ticket.category}`",
            f"Статус: `{ticket.status}`",
            "",
        ]

        for message in messages:

            sender = (
                "👤 Вы"
                if message.sender_type == "user"
                else "👨‍💻 Поддержка"
            )

            lines.append(
                f"{sender}:"
            )

            lines.append(
                message.message
            )

            lines.append("")

        text = "\n".join(
            lines
        )

        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=support_keyboard(),
        )

    finally:

        db.close()


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

            lines.append(
                f"• {transaction.type} — "
                f"{transaction.amount} "
                f"{transaction.asset} — "
                f"{transaction.status}"
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
# TEXT MESSAGE HANDLER
# ============================================================


async def text_message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_user:
        return

    if not update.message:
        return

    if not update.message.text:
        return

    user = get_or_create_user(
        telegram_id=(
            update.effective_user.id
        ),
        telegram_username=(
            update.effective_user.username
        ),
    )

    text = update.message.text.strip()

    # ========================================================
    # SUBJECT
    # ========================================================

    if context.user_data.get(
        "support_waiting_subject"
    ):

        context.user_data[
            "support_subject"
        ] = text

        context.user_data[
            "support_waiting_subject"
        ] = False

        context.user_data[
            "support_waiting_message"
        ] = True

        await update.message.reply_text(
            "✍️ Теперь опишите проблему "
            "подробно.\n\n"
            "Можно указать номер сделки, "
            "TX Hash и другие детали."
        )

        return

    # ========================================================
    # MESSAGE
    # ========================================================

    if context.user_data.get(
        "support_waiting_message"
    ):

        category = context.user_data.get(
            "support_category",
            "general",
        )

        subject = context.user_data.get(
            "support_subject",
            "Обращение пользователя",
        )

        try:

            ticket = create_support_ticket(
                user_id=user.id,
                category=category,
                subject=subject,
                message=text,
            )

            context.user_data.pop(
                "support_category",
                None,
            )

            context.user_data.pop(
                "support_subject",
                None,
            )

            context.user_data.pop(
                "support_waiting_message",
                None,
            )

            await update.message.reply_text(
                "✅ *Обращение создано!*\n\n"
                f"🎫 Номер: `#{ticket.id}`\n"
                f"📌 Тема: `{ticket.subject}`\n"
                f"📂 Категория: `{ticket.category}`\n"
                f"📊 Статус: `{ticket.status}`\n\n"
                "Поддержка рассмотрит ваше "
                "обращение.",
                parse_mode="Markdown",
                reply_markup=support_keyboard(),
            )

        except Exception:

            logger.exception(
                "Failed to create support ticket."
            )

            await update.message.reply_text(
                "❌ Не удалось создать обращение.\n\n"
                "Попробуйте ещё раз."
            )

        return


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

    application.add_handler(
        # Обрабатываем обычные текстовые сообщения.
        # В дальнейшем здесь же сделаем
        # сообщения P2P и апелляций.
        __import__(
            "telegram.ext",
            fromlist=[
                "MessageHandler",
                "filters",
            ],
        ).MessageHandler(
            __import__(
                "telegram.ext",
                fromlist=[
                    "filters",
                ],
            ).filters.TEXT
            & ~__import__(
                "telegram.ext",
                fromlist=[
                    "filters",
                ],
            ).filters.COMMAND,
            text_message_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    return application
