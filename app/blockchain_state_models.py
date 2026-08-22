from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from app.database import Base


class BlockchainState(Base):
    __tablename__ = "blockchain_state"

    id = Column(
        Integer,
        primary_key=True,
    )

    network = Column(
        String(50),
        nullable=False,
        unique=True,
    )

    last_scanned_block = Column(
        Integer,
        nullable=False,
        default=0,
    )

    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
