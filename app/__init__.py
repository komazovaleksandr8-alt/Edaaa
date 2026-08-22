from sqlalchemy import inspect, text

from app.database import Base, engine

from app import models
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction
from app.wallet_key_models import WalletKey
from app.send_models import SendTransaction
from app.blockchain_state_models import BlockchainState


def init_database():
    Base.metadata.create_all(
        bind=engine
    )

    inspector = inspect(engine)

    tables = inspector.get_table_names()

    if "users" not in tables:
        return

    columns = {
        column["name"]
        for column in inspector.get_columns(
            "users"
        )
    }

    with engine.begin() as connection:

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


if __name__ == "__main__":
    init_database()
