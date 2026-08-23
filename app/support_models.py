from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

from sqlalchemy.orm import relationship

from app.database import Base


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    category = Column(
        String(50),
        nullable=False,
    )

    subject = Column(
        String(255),
        nullable=False,
    )

    status = Column(
        String(30),
        default="open",
        nullable=False,
        index=True,
    )

    priority = Column(
        String(30),
        default="normal",
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    messages = relationship(
        "SupportMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportMessage.created_at",
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    ticket_id = Column(
        Integer,
        ForeignKey(
            "support_tickets.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    sender_type = Column(
        String(30),
        nullable=False,
    )

    sender_id = Column(
        Integer,
        nullable=False,
    )

    message = Column(
        Text,
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    ticket = relationship(
        "SupportTicket",
        back_populates="messages",
    )
