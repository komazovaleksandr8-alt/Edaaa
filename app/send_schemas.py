from decimal import Decimal

from pydantic import BaseModel, Field


class SendETHRequest(BaseModel):
    to_address: str = Field(
        min_length=42,
        max_length=42,
    )

    amount: Decimal = Field(
        gt=Decimal("0"),
        max_digits=30,
        decimal_places=18,
    )


class SendETHResponse(BaseModel):
    id: int
    asset: str
    from_address: str
    to_address: str
    amount: str
    tx_hash: str | None
    status: str
