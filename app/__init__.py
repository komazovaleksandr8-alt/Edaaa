from sqlalchemy import text
from web3 import Web3

from app.database import engine, Base
from app.config import settings

from app.models import User
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction


def init_database():
    Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:

        if engine.dialect.name == "postgresql":
            connection.execute(
                text(
                    """
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS is_admin
                    BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
            )

        elif engine.dialect.name == "sqlite":
            result = connection.execute(
                text("PRAGMA table_info(users)")
            )

            columns = [
                row[1]
                for row in result.fetchall()
            ]

            if "is_admin" not in columns:
                connection.execute(
                    text(
                        """
                        ALTER TABLE users
                        ADD COLUMN is_admin
                        BOOLEAN NOT NULL DEFAULT 0
                        """
                    )
                )

        connection.execute(
            text(
                """
                UPDATE users
                SET is_admin = TRUE
                WHERE LOWER(email) = LOWER(:email)
                """
            ),
            {
                "email": "testok@edaaa.com"
            },
        )

    # =========================
    # BLOCKCHAIN CONNECTION
    # =========================

    rpc_url = getattr(settings, "ETH_RPC_URL", None)

    if not rpc_url:
        print("ETH_RPC_URL is not configured.")
        return

    web3 = Web3(
        Web3.HTTPProvider(rpc_url)
    )

    if not web3.is_connected():
        print("WARNING: Ethereum RPC connection failed.")
        return

    chain_id = web3.eth.chain_id

    print(
        f"Ethereum RPC connected successfully. "
        f"Chain ID: {chain_id}"
    )
