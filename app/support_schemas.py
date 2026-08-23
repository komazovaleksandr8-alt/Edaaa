from datetime import datetime

from pydantic import (
    BaseModel,
    Field,
)


class SupportTicketCreate(
    BaseModel
):
    category: str = Field(
        min_length=2,
        max_length=50,
    )

    subject: str = Field(
        min_length=2,
        max_length=255,
    )

    message: str = Field(
        min_length=1,
        max_length=5000,
    )


class SupportMessageCreate(
    BaseModel
):
    message: str = Field(
        min_length=1,
        max_length=5000,
    )


class SupportMessageResponse(
    BaseModel
):
    id: int
    ticket_id: int
    sender_type: str
    sender_id: int
    message: str
    created_at: datetime

    class Config:
        from_attributes = True


class SupportTicketResponse(
    BaseModel
):
    id: int
    user_id: int
    category: str
    subject: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SupportTicketDetailResponse(
    SupportTicketResponse
):
    messages: list[
        SupportMessageResponse
    ] = []
