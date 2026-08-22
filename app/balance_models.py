from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base


class Balance(Base):
    __tablename__ = "balances"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    wallet_id = Column(
        Integer,
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    asset = Column(
        String(20),
        nullable=False,
        default="USDT",
        index=True,
    )

    amount = Column(
        Numeric(30, 18),
        nullable=False,
        default=Decimal("0"),
    )

    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    wallet = relationship(
        "Wallet",
        back_populates="balances",
    )
