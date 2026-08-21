import secrets
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.init_db import init_database
from app.models import User
from app.schemas import (
    BalanceResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    TransactionResponse,
    UserResponse,
    WalletResponse,
)
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction


app = FastAPI(
    title="Edaaa Wallet",
    description="Edaaa Cryptocurrency Wallet API",
    version="0.4.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


security = HTTPBearer()


# =========================
# ADMIN DEPOSIT SCHEMA
# =========================

class AdminDepositRequest(BaseModel):
    user_id: int = Field(
        ...,
        gt=0,
        description="ID пользователя, которому зачисляются USDT",
    )

    amount: Decimal = Field(
        ...,
        gt=0,
        description="Количество USDT для зачисления",
    )


# =========================
# STARTUP
# =========================

@app.on_event("startup")
def startup():
    init_database()


# =========================
# ROOT
# =========================

@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Edaaa Wallet API is running",
        "version": "0.4.0",
    }


# =========================
# HEALTH
# =========================

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "database": "initialized",
    }


# =========================
# REGISTER
# =========================

@app.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    data: RegisterRequest,
    db: Session = Depends(get_db),
):
    existing_user = (
        db.query(User)
        .filter(User.email == data.email)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        is_admin=False,
    )

    db.add(user)
    db.flush()

    wallet = Wallet(
        user_id=user.id,
        address=f"edaaa_{secrets.token_hex(20)}",
        network="ethereum",
    )

    db.add(wallet)
    db.flush()

    balance = Balance(
        wallet_id=wallet.id,
        asset="USDT",
        amount=Decimal("0"),
    )

    db.add(balance)

    db.commit()
    db.refresh(user)

    return user


# =========================
# LOGIN
# =========================

@app.post(
    "/login",
    response_model=TokenResponse,
)
def login(
    data: LoginRequest,
    db: Session = Depends(get_db),
):
    user = (
        db.query(User)
        .filter(User.email == data.email)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not verify_password(
        data.password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    token = create_access_token(
        {
            "sub": str(user.id)
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
    }


# =========================
# CURRENT USER
# =========================

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    try:
        payload = decode_access_token(
            credentials.credentials
        )

        user_id = payload.get("sub")

        if not user_id:
            raise ValueError("Missing subject")

        user_id = int(user_id)

    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={
                "WWW-Authenticate": "Bearer"
            },
        )

    user = (
        db.query(User)
        .filter(User.id == user_id)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
            headers={
                "WWW-Authenticate": "Bearer"
            },
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    return user


# =========================
# ADMIN CHECK
# =========================

def get_current_admin(
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    return current_user


# =========================
# ME
# =========================

@app.get(
    "/me",
    response_model=UserResponse,
)
def me(
    current_user: User = Depends(get_current_user),
):
    return current_user


# =========================
# WALLET
# =========================

@app.get(
    "/wallet",
    response_model=WalletResponse,
)
def wallet(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = (
        db.query(Wallet)
        .filter(Wallet.user_id == current_user.id)
        .first()
    )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found.",
        )

    return wallet


# =========================
# WALLET BALANCE
# =========================

@app.get(
    "/wallet/balance",
    response_model=BalanceResponse,
)
def wallet_balance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = (
        db.query(Wallet)
        .filter(Wallet.user_id == current_user.id)
        .first()
    )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found.",
        )

    balance = (
        db.query(Balance)
        .filter(
            Balance.wallet_id == wallet.id,
            Balance.asset == "USDT",
        )
        .first()
    )

    if not balance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="USDT balance not found.",
        )

    return balance


# =========================
# WALLET TRANSACTIONS
# =========================

@app.get(
    "/wallet/transactions",
    response_model=list[TransactionResponse],
)
def wallet_transactions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = (
        db.query(Wallet)
        .filter(Wallet.user_id == current_user.id)
        .first()
    )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found.",
        )

    transactions = (
        db.query(Transaction)
        .filter(
            Transaction.wallet_id == wallet.id
        )
        .order_by(
            Transaction.created_at.desc()
        )
        .all()
    )

    return transactions


# =========================
# ADMIN DEPOSIT
# =========================

@app.post(
    "/admin/deposit",
    response_model=TransactionResponse,
)
def admin_deposit(
    data: AdminDepositRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    # =========================
    # FIND USER
    # =========================

    user = (
        db.query(User)
        .filter(User.id == data.user_id)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # =========================
    # FIND WALLET
    # =========================

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id == user.id
        )
        .first()
    )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found.",
        )

    # =========================
    # FIND USDT BALANCE
    # =========================

    balance = (
        db.query(Balance)
        .filter(
            Balance.wallet_id == wallet.id,
            Balance.asset == "USDT",
        )
        .first()
    )

    if not balance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="USDT balance not found.",
        )

    # =========================
    # VALIDATE AMOUNT
    # =========================

    amount = Decimal(data.amount)

    if amount <= Decimal("0"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deposit amount must be greater than zero.",
        )

    # =========================
    # UPDATE BALANCE
    # =========================

    balance.amount = (
        Decimal(balance.amount) + amount
    )

    # =========================
    # CREATE TRANSACTION
    # =========================

    transaction = Transaction(
        wallet_id=wallet.id,
        type="deposit",
        asset="USDT",
        amount=amount,
        status="completed",
        tx_hash=None,
    )

    db.add(transaction)

    # =========================
    # SAVE
    # =========================

    try:
        db.commit()
        db.refresh(transaction)

    except Exception:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process deposit.",
        )

    return transaction
