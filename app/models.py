from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
)

from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    email = Column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash = Column(
        String(255),
        nullable=False,
    )

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_admin = Column(
        Boolean,
        default=False,
        nullable=False,
    )

    telegram_id = Column(
        String(64),
        unique=True,
        index=True,
        nullable=True,
    )

    telegram_username = Column(
        String(255),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # ========================================================
    # WALLETS
    # ========================================================

    wallets = relationship(
        "Wallet",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    # ========================================================
    # SUPPORT TICKETS
    # ========================================================

    support_tickets = relationship(
        "SupportTicket",
        back_populates="user",
        cascade="all, delete-orphan",
    )
