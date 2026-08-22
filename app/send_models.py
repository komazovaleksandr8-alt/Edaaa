from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base


class SendTransaction(Base):
    __tablename__ = "send_transactions"

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
        default="ETH",
    )

    to_address = Column(
        String(255),
        nullable=False,
    )

    amount = Column(
        Numeric(30, 18),
        nullable=False,
    )

    tx_hash = Column(
        String(255),
        nullable=True,
        unique=True,
    )

    status = Column(
        String(20),
        nullable=False,
        default="pending",
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    wallet = relationship(
        "Wallet",
    )
