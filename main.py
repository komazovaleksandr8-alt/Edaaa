import asyncio
import logging
from decimal import Decimal

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from web3 import Web3
from eth_account import Account

from app.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.config import settings
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
from app.wallet_key_models import WalletKey
from app.blockchain_state_models import BlockchainState
from app.blockchain_scanner import scan_once


logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger("edaaa")


app = FastAPI(
    title="Edaaa Wallet",
    description="Edaaa Cryptocurrency Wallet API",
    version="0.7.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


security = HTTPBearer()


class AdminDepositRequest(BaseModel):
    user_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0)


blockchain_scanner_task = None


def get_wallet_fernet() -> Fernet:
    key = settings.WALLET_ENCRYPTION_KEY

    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WALLET_ENCRYPTION_KEY is not configured.",
        )

    try:
        return Fernet(key.encode())

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid WALLET_ENCRYPTION_KEY.",
        )


def create_real_ethereum_wallet():
    account = Account.create()

    address = Web3.to_checksum_address(
        account.address
    )

    private_key = account.key.hex()

    return address, private_key


def encrypt_private_key(
    private_key: str,
) -> str:
    fernet = get_wallet_fernet()

    encrypted = fernet.encrypt(
        private_key.encode()
    )

    return encrypted.decode()


def decrypt_private_key(
    encrypted_private_key: str,
) -> str:
    fernet = get_wallet_fernet()

    try:
        decrypted = fernet.decrypt(
            encrypted_private_key.encode()
        )

        return decrypted.decode()

    except InvalidToken:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt wallet private key.",
        )


async def blockchain_scanner_loop():
    logger.info(
        "Edaaa blockchain scanner started."
    )

    while True:
        try:
            result = await asyncio.to_thread(
                scan_once
            )

            logger.info(
                "Blockchain scan result: %s",
                result,
            )

        except Exception:
            logger.exception(
                "Blockchain scanner loop error."
            )

        await asyncio.sleep(15)


@app.on_event("startup")
async def startup():
    global blockchain_scanner_task

    init_database()

    blockchain_scanner_task = asyncio.create_task(
        blockchain_scanner_loop()
    )

    logger.info(
        "Edaaa Wallet API started."
    )


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Edaaa Wallet API is running",
        "version": "0.7.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "database": "initialized",
    }


@app.get("/blockchain/status")
def blockchain_status():
    if not settings.ETH_RPC_URL:
        raise HTTPException(
            status_code=503,
            detail="ETH_RPC_URL is not configured.",
        )

    web3 = Web3(
        Web3.HTTPProvider(
            settings.ETH_RPC_URL
        )
    )

    try:
        connected = web3.is_connected()

        if not connected:
            raise HTTPException(
                status_code=503,
                detail="Ethereum RPC connection failed.",
            )

        chain_id = web3.eth.chain_id
        block_number = web3.eth.block_number

        return {
            "connected": True,
            "network": settings.ETH_NETWORK,
            "chain_id": chain_id,
            "latest_block": block_number,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Blockchain connection error: {str(exc)}",
        )


@app.get("/blockchain/scanner-status")
def blockchain_scanner_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    state = (
        db.query(BlockchainState)
        .filter(
            BlockchainState.network
            == settings.ETH_NETWORK
        )
        .first()
    )

    if not state:
        return {
            "network": settings.ETH_NETWORK,
            "initialized": False,
            "last_scanned_block": None,
            "updated_at": None,
        }

    return {
        "network": state.network,
        "initialized": True,
        "last_scanned_block": state.last_scanned_block,
        "updated_at": state.updated_at,
    }


@app.post(
    "/admin/blockchain/sync"
)
def admin_blockchain_sync(
    admin: User = Depends(get_current_admin),
):
    try:
        return scan_once()

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Blockchain sync failed: {str(exc)}",
        )


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

    get_wallet_fernet()

    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        is_admin=False,
    )

    db.add(user)
    db.flush()

    address, private_key = create_real_ethereum_wallet()

    wallet = Wallet(
        user_id=user.id,
        address=address,
        network=settings.ETH_NETWORK,
    )

    db.add(wallet)
    db.flush()

    encrypted_private_key = encrypt_private_key(
        private_key
    )

    wallet_key = WalletKey(
        wallet_id=wallet.id,
        encrypted_private_key=encrypted_private_key,
    )

    db.add(wallet_key)

    usdt_balance = Balance(
        wallet_id=wallet.id,
        asset="USDT",
        amount=Decimal("0"),
    )

    eth_balance = Balance(
        wallet_id=wallet.id,
        asset="ETH",
        amount=Decimal("0"),
    )

    db.add(usdt_balance)
    db.add(eth_balance)

    try:
        db.commit()
        db.refresh(user)

    except Exception:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user wallet.",
        )

    return user


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


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(
        security
    ),
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


def get_current_admin(
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    return current_user


@app.get(
    "/me",
    response_model=UserResponse,
)
def me(
    current_user: User = Depends(get_current_user),
):
    return current_user


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
        .filter(
            Wallet.user_id == current_user.id
        )
        .first()
    )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found.",
        )

    return wallet


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
        .filter(
            Wallet.user_id == current_user.id
        )
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
        .filter(
            Wallet.user_id == current_user.id
        )
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


@app.get(
    "/wallet/ethereum-balance"
)
def ethereum_balance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.ETH_RPC_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ETH_RPC_URL is not configured.",
        )

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id == current_user.id
        )
        .first()
    )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found.",
        )

    if not Web3.is_address(wallet.address):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wallet has an invalid Ethereum address.",
        )

    web3 = Web3(
        Web3.HTTPProvider(
            settings.ETH_RPC_URL
        )
    )

    try:
        if not web3.is_connected():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ethereum RPC connection failed.",
            )

        checksum_address = Web3.to_checksum_address(
            wallet.address
        )

        balance_wei = web3.eth.get_balance(
            checksum_address
        )

        balance_eth = Web3.from_wei(
            balance_wei,
            "ether",
        )

        return {
            "address": checksum_address,
            "network": settings.ETH_NETWORK,
            "asset": "ETH",
            "balance": str(balance_eth),
            "balance_wei": balance_wei,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to read Ethereum balance: {str(exc)}",
        )


@app.get(
    "/wallet/key-status"
)
def wallet_key_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id == current_user.id
        )
        .first()
    )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found.",
        )

    wallet_key = (
        db.query(WalletKey)
        .filter(
            WalletKey.wallet_id == wallet.id
        )
        .first()
    )

    return {
        "wallet_address": wallet.address,
        "private_key_stored": wallet_key is not None,
        "private_key_encrypted": wallet_key is not None,
    }


@app.post(
    "/admin/deposit",
    response_model=TransactionResponse,
)
def admin_deposit(
    data: AdminDepositRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
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

    amount = Decimal(data.amount)

    balance.amount = (
        Decimal(balance.amount) + amount
    )

    transaction = Transaction(
        wallet_id=wallet.id,
        type="deposit",
        asset="USDT",
        amount=amount,
        status="completed",
        tx_hash=None,
    )

    db.add(transaction)

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
