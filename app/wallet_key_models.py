from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database import Base


class WalletKey(Base):
    __tablename__ = "wallet_keys"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    wallet_id = Column(
        Integer,
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    encrypted_private_key = Column(
        String(1000),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
