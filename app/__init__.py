from sqlalchemy import inspect, text

from app.database import Base, engine

from app import models
from app import support_models

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

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    if "users" in tables:

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

    # --------------------------------------------------------
    # WALLETS
    # --------------------------------------------------------

    if "wallets" in tables:

        with engine.begin() as connection:

            # Проверяем наличие дублей user_id.
            duplicate_users = connection.execute(
                text(
                    """
                    SELECT user_id, COUNT(*) AS wallet_count
                    FROM wallets
                    GROUP BY user_id
                    HAVING COUNT(*) > 1
                    """
                )
            ).fetchall()

            if duplicate_users:

                print(
                    "WARNING: duplicate wallets detected "
                    "for some users. "
                    "Unique wallet-per-user index "
                    "was not created."
                )

            else:

                try:

                    connection.execute(
                        text(
                            """
                            CREATE UNIQUE INDEX
                            IF NOT EXISTS
                            ix_wallets_user_id_unique
                            ON wallets (user_id)
                            """
                        )
                    )

                except Exception:

                    pass

    # --------------------------------------------------------
    # BALANCES
    # --------------------------------------------------------

    if "balances" in tables:

        with engine.begin() as connection:

            try:

                connection.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX
                        IF NOT EXISTS
                        ix_balances_wallet_asset_unique
                        ON balances (
                            wallet_id,
                            asset
                        )
                        """
                    )
                )

            except Exception:

                pass


if __name__ == "__main__":
    init_database()
