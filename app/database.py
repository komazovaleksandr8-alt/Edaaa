from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings


# ============================================================
# DATABASE URL
# ============================================================

DATABASE_URL = (
    settings.DATABASE_URL or ""
).strip()


if not DATABASE_URL:

    raise RuntimeError(
        "DATABASE_URL is not configured. "
        "Edaaa requires a persistent PostgreSQL database."
    )


# Render иногда использует postgres://,
# а современные версии SQLAlchemy ожидают
# postgresql://
if DATABASE_URL.startswith(
    "postgres://"
):

    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1,
    )


# ============================================================
# ENGINE
# ============================================================

connect_args = {}


if DATABASE_URL.startswith(
    "sqlite"
):

    connect_args = {
        "check_same_thread": False,
    }


engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)


# ============================================================
# SESSION
# ============================================================

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ============================================================
# BASE
# ============================================================

Base = declarative_base()


# ============================================================
# DATABASE DEPENDENCY
# ============================================================

def get_db():

    db = SessionLocal()

    try:

        yield db

    finally:

        db.close()
