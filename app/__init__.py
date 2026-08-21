from sqlalchemy import text

from app.database import engine, Base

# Импортируем все модели, чтобы SQLAlchemy знал о таблицах
from app.models import User
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction


def init_database():
    # Создаём отсутствующие таблицы.
    # Существующие таблицы и данные не удаляются.
    Base.metadata.create_all(bind=engine)

    # Добавляем is_admin в уже существующую таблицу users.
    # Это нужно потому, что create_all() не добавляет новые
    # колонки в уже существующие таблицы.
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
            # SQLite не поддерживает IF NOT EXISTS
            # для ADD COLUMN во всех версиях одинаково.
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
