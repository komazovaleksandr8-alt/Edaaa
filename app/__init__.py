from sqlalchemy import text

from app.database import Base, engine

from app import models
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction
from app.wallet_key_models import WalletKey
from app.send_models import SendTransaction
from app.blockchain_state_models import BlockchainState


def init_database():
    Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS
                telegram_id VARCHAR(64)
                """
            )
        )

        connection.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS
                telegram_username VARCHAR(255)
                """
            )
        )

        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                ix_users_telegram_id
                ON users (telegram_id)
                """
            )
        )


if __name__ == "__main__":
    init_database()
