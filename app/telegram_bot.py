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
from app.wallet_key_models import WalletKey
from app.main_wallet import (
    create_real_ethereum_wallet,
    encrypt_private_key,
)


logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(
    "edaaa.telegram"
)


def create_wallet_for_user(
    db,
    user,
):
    existing_wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id == user.id
        )
        .first()
    )

    if existing_wallet:
        return existing_wallet

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

    return wallet


def get_or_create_telegram_user(
    telegram_user,
):
    db = SessionLocal()

    try:
        telegram_id = str(
            telegram_user.id
        )

        user = (
            db.query(User)
            .filter(
                User.telegram_id
                == telegram_id
            )
            .first()
        )

        if user:
            user.telegram_username = (
                telegram_user.username
            )

            wallet = create_wallet_for_user(
                db,
                user,
            )

            db.commit()

            return user, wallet

        email = (
            f"telegram_{telegram_id}"
            "@telegram.edaaa.local"
        )

        random_password = (
            secrets.token_urlsafe(32)
        )

        user = User(
            email=email,
            password_hash=hash_password(
                random_password
            ),
            telegram_id=telegram_id,
            telegram_username=(
                telegram_user.username
            ),
            is_active=True,
            is_admin=False,
        )

        db.add(user)
        db.flush()

        wallet = create_wallet_for_user(
            db,
            user,
        )

        db.commit()

        db.refresh(user)
        db.refresh(wallet)

        logger.info(
            "Telegram user created: %s",
            telegram_id,
        )

        return user, wallet

    except Exception:
        db.rollback()

        logger.exception(
            "Failed to create Telegram user"
        )

        raise

    finally:
        db.close()


def get_balances(wallet_id):
    db = SessionLocal()

    try:
        balances = (
            db.query(Balance)
            .filter(
                Balance.wallet_id
                == wallet_id
            )
            .all()
        )

        result = {}

        for balance in balances:
            result[
                balance.asset
            ] = Decimal(
                balance.amount
            )

        return result

    finally:
        db.close()


def get_transactions(wallet_id):
    db = SessionLocal()

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
                    callback_data="home",
                ),
            ],
        ]
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user = (
        update.effective_user
    )

    try:
        user, wallet = (
            get_or_create_telegram_user(
                telegram_user
            )
        )

        text = (
            "👋 *Добро пожаловать "
            "в Edaaa Wallet!*\n\n"
            "Ваш криптокошелёк создан.\n\n"
            f"👛 Адрес:\n"
            f"`{wallet.address}`\n\n"
            f"🌐 Сеть: "
            f"`{wallet.network}`\n\n"
            "Выберите действие:"
        )

        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    except Exception:
        logger.exception(
            "Telegram /start failed"
        )

        await update.message.reply_text(
            "❌ Не удалось открыть Edaaa Wallet.\n\n"
            "Попробуйте ещё раз через несколько секунд."
        )


async def buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    telegram_user = query.from_user

    try:
        user, wallet = (
            get_or_create_telegram_user(
                telegram_user
            )
        )

        if query.data in (
            "home",
            "refresh",
        ):
            await show_home(
                query,
                wallet,
            )

        elif query.data == "balance":
            await show_balance(
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

        elif query.data == "withdraw":
            await show_withdraw(
                query,
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
            )

    except Exception:
        logger.exception(
            "Telegram button error"
        )

        await query.edit_message_text(
            "❌ Произошла ошибка.",
            reply_markup=back_keyboard(),
        )


async def show_home(
    query,
    wallet,
):
    balances = get_balances(
        wallet.id
    )

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
        f"Ξ ETH: `{eth}`\n"
        f"₮ USDT: `{usdt}`\n\n"
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
    balances = get_balances(
        wallet.id
    )

    eth = balances.get(
        "ETH",
        Decimal("0"),
    )

    usdt = balances.get(
        "USDT",
        Decimal("0"),
    )

    text = (
        "💰 *Баланс*\n\n"
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
        "👛 *Мой кошелёк*\n\n"
        "Ethereum-адрес:\n\n"
        f"`{wallet.address}`\n\n"
        f"Сеть: `{wallet.network}`"
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
        "Ваш Ethereum-адрес:\n\n"
        f"`{wallet.address}`\n\n"
        f"Сеть: `{wallet.network}`\n\n"
        "⚠️ Используйте только поддерживаемую "
        "сеть и актив."
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
        "Функция вывода будет подключена "
        "на следующем этапе.\n\n"
        "Перед отправкой добавим:\n"
        "• ввод адреса\n"
        "• ввод суммы\n"
        "• проверку комиссии\n"
        "• подтверждение операции"
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
            "📜 *Последние операции*\n"
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
        f"Статус: "
        f"`{'Активен' if user.is_active else 'Заблокирован'}`"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_keyboard(),
    )


def create_application():
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
            buttons,
        )
    )

    return application


def main():
    application = (
        create_application()
    )

    logger.info(
        "Starting Edaaa Telegram bot..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
