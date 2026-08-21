from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    address = Column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    network = Column(
        String(50),
        nullable=False,
        default="ethereum",
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user = relationship(
        "User",
        back_populates="wallets",
    )

    balances = relationship(
        "Balance",
        back_populates="wallet",
        cascade="all, delete-orphan",
    )


from app.balance_models import Balance
