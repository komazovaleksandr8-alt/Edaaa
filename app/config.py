import os

from dotenv import load_dotenv


load_dotenv()


class Settings:
    APP_NAME: str = os.getenv(
        "APP_NAME",
        "Edaaa Wallet",
    )

    APP_VERSION: str = os.getenv(
        "APP_VERSION",
        "0.1.0",
    )

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./edaaa.db",
    )

    JWT_SECRET: str = os.getenv(
        "JWT_SECRET",
        "CHANGE_THIS_SECRET_IN_PRODUCTION",
    )

    ETH_RPC_URL: str = os.getenv(
        "ETH_RPC_URL",
        "",
    )

    ETH_NETWORK: str = os.getenv(
        "ETH_NETWORK",
        "sepolia",
    )

    WALLET_ENCRYPTION_KEY: str = os.getenv(
        "WALLET_ENCRYPTION_KEY",
        "",
    )

    ADMIN_EMAIL: str = os.getenv(
        "ADMIN_EMAIL",
        "",
    )

    ADMIN_SETUP_KEY: str = os.getenv(
        "ADMIN_SETUP_KEY",
        "",
    )

    TELEGRAM_BOT_TOKEN: str = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    )


settings = Settings()
