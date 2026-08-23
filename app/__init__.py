from sqlalchemy import inspect, text

from app.database import Base, engine

# ============================================================
# MODELS
# ============================================================

from app import models

from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction
from app.wallet_key_models import WalletKey
from app.send_models import SendTransaction
from app.blockchain_state_models import BlockchainState

# Support
from app.support_models import (
    SupportTicket,
    SupportMessage,
)


def init_database():
    """
    Создаёт все таблицы Edaaa.

    Base.metadata.create_all()
    создаёт только отсутствующие таблицы
    и не удаляет существующие данные.
    """

    # ========================================================
    # CREATE TABLES
    # ========================================================

    Base.metadata.create_all(
        bind=engine
    )

    # ========================================================
    # INSPECT DATABASE
    # ========================================================

    inspector = inspect(engine)

    tables = inspector.get_table_names()

    # ========================================================
    # USERS
    # ========================================================

    if "users" not in tables:
        return

    columns = {
        column["name"]
        for column in inspector.get_columns(
            "users"
        )
    }

    # ========================================================
    # MIGRATIONS
    # ========================================================

    with engine.begin() as connection:

        # ----------------------------------------------------
        # TELEGRAM ID
        # ----------------------------------------------------

        if "telegram_id" not in columns:

            connection.execute(
                text(
                    """
                    ALTER TABLE users
                    ADD COLUMN telegram_id
                    VARCHAR(64)
                    """
                )
            )

        # ----------------------------------------------------
        # TELEGRAM USERNAME
        # ----------------------------------------------------

        if "telegram_username" not in columns:

            connection.execute(
                text(
                    """
                    ALTER TABLE users
                    ADD COLUMN telegram_username
                    VARCHAR(255)
                    """
                )
            )

        # ----------------------------------------------------
        # UNIQUE TELEGRAM INDEX
        # ----------------------------------------------------

        try:

            connection.execute(
                text(
                    """
                    CREATE UNIQUE INDEX
                    IF NOT EXISTS
                    ix_users_telegram_id
                    ON users (telegram_id)
                    """
                )
            )

        except Exception:
            pass


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    init_database()
