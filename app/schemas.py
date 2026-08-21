from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# =========================
# REGISTER
# =========================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
    )


# =========================
# LOGIN
# =========================

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
    )


# =========================
# TOKEN
# =========================

class TokenResponse(BaseModel):
    access_token: str
    token_type: str


# =========================
# USER
# =========================

class UserResponse(BaseModel):
    id: int
    email: EmailStr
    is_active: bool
    is_admin: bool

    class Config:
        from_attributes = True


# =========================
# WALLET
# =========================

class WalletResponse(BaseModel):
    id: int
    user_id: int
    address: str
    network: str

    class Config:
        from_attributes = True


# =========================
# BALANCE
# =========================

class BalanceResponse(BaseModel):
    id: int
    wallet_id: int
    asset: str
    amount: Decimal

    class Config:
        from_attributes = True


# =========================
# TRANSACTION
# =========================

class TransactionResponse(BaseModel):
    id: int
    wallet_id: int
    type: str
    asset: str
    amount: Decimal
    status: str
    tx_hash: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
