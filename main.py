import asyncio
import logging
from decimal import Decimal

from cryptography.fernet import Fernet, InvalidToken

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    status,
)

from fastapi.middleware.cors import CORSMiddleware

from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

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

from app.blockchain_state_models import (
    BlockchainState,
)

from app.blockchain_scanner import scan_once

from app.send_models import SendTransaction

from app.send_schemas import (
    SendETHRequest,
    SendETHResponse,
)

from app.send_service import (
    get_wallet_private_key,
    validate_recipient_address,
    get_web3,
    create_eth_transaction,
    sign_and_send_eth_transaction,
)

from app.telegram_bot import (
    create_telegram_application,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(
    "edaaa"
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Edaaa Wallet",
    description="Edaaa Cryptocurrency Wallet API",
    version="0.9.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


security = HTTPBearer()


# ============================================================
# ADMIN DEPOSIT
# ============================================================

class AdminDepositRequest(BaseModel):

    user_id: int = Field(
        gt=0
    )

    amount: Decimal = Field(
        gt=0
    )


# ============================================================
# GLOBAL TASKS
# ============================================================

blockchain_scanner_task = None

telegram_application = None

telegram_bot_task = None


# ============================================================
# WALLET ENCRYPTION
# ============================================================

def get_wallet_fernet() -> Fernet:

    key = settings.WALLET_ENCRYPTION_KEY

    if not key:

        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "WALLET_ENCRYPTION_KEY "
                "is not configured."
            ),
        )

    try:

        return Fernet(
            key.encode()
        )

    except Exception as exc:

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Invalid WALLET_ENCRYPTION_KEY."
            ),
        ) from exc


def create_real_ethereum_wallet():

    account = Account.create()

    address = Web3.to_checksum_address(
        account.address
    )

    private_key = account.key.hex()

    return (
        address,
        private_key,
    )


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

    except InvalidToken as exc:

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Failed to decrypt "
                "wallet private key."
            ),
        ) from exc


# ============================================================
# AUTHENTICATION
# ============================================================

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(
        security
    ),
    db: Session = Depends(
        get_db
    ),
):

    try:

        payload = decode_access_token(
            credentials.credentials
        )

        user_id = payload.get(
            "sub"
        )

        if not user_id:

            raise ValueError(
                "Missing subject"
            )

        user_id = int(
            user_id
        )

    except (
        ValueError,
        TypeError,
    ):

        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid or expired token."
            ),
            headers={
                "WWW-Authenticate": "Bearer"
            },
        )

    user = (
        db.query(User)
        .filter(
            User.id == user_id
        )
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "User not found."
            ),
            headers={
                "WWW-Authenticate": "Bearer"
            },
        )

    if not user.is_active:

        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail=(
                "User account is inactive."
            ),
        )

    return user


def get_current_admin(
    current_user: User = Depends(
        get_current_user
    ),
):

    if not current_user.is_admin:

        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail=(
                "Admin access required."
            ),
        )

    return current_user


# ============================================================
# BLOCKCHAIN SCANNER
# ============================================================

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

        except asyncio.CancelledError:

            logger.info(
                "Blockchain scanner task cancelled."
            )

            raise

        except Exception:

            logger.exception(
                "Blockchain scanner loop error."
            )

        await asyncio.sleep(
            30
        )


# ============================================================
# TELEGRAM BOT LOOP
# ============================================================

async def telegram_bot_loop():

    global telegram_application

    logger.info(
        "Starting Edaaa Telegram bot..."
    )

    try:

        telegram_application = (
            create_telegram_application()
        )

        await telegram_application.initialize()

        await telegram_application.start()

        if telegram_application.updater is None:

            raise RuntimeError(
                "Telegram updater is not available."
            )

        await telegram_application.updater.start_polling()

        logger.info(
            "Edaaa Telegram bot started successfully."
        )

        while True:

            await asyncio.sleep(
                3600
            )

    except asyncio.CancelledError:

        logger.info(
            "Telegram bot task cancelled."
        )

        raise

    except Exception:

        logger.exception(
            "Telegram bot crashed."
        )

    finally:

        if telegram_application:

            try:

                if telegram_application.updater:

                    await telegram_application.updater.stop()

            except Exception:

                logger.exception(
                    "Failed to stop Telegram updater."
                )

            try:

                await telegram_application.stop()

            except Exception:

                logger.exception(
                    "Failed to stop Telegram application."
                )

            try:

                await telegram_application.shutdown()

            except Exception:

                logger.exception(
                    "Failed to shutdown Telegram application."
                )

            telegram_application = None


# ============================================================
# STARTUP
# ============================================================

@app.on_event(
    "startup"
)
async def startup():

    global blockchain_scanner_task
    global telegram_bot_task

    logger.info(
        "=================================================="
    )

    logger.info(
        "Edaaa Wallet API startup started."
    )

    logger.info(
        "=================================================="
    )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    try:

        logger.info(
            "Initializing database..."
        )

        await asyncio.to_thread(
            init_database
        )

        logger.info(
            "Database initialization completed."
        )

    except Exception:

        logger.exception(
            "Database initialization failed."
        )

        logger.warning(
            "Continuing API startup despite database error."
        )

    # --------------------------------------------------------
    # BLOCKCHAIN SCANNER
    # --------------------------------------------------------

    try:

        blockchain_scanner_task = (
            asyncio.create_task(
                blockchain_scanner_loop()
            )
        )

        logger.info(
            "Blockchain scanner task created."
        )

    except Exception:

        logger.exception(
            "Failed to create blockchain scanner task."
        )

    # --------------------------------------------------------
    # TELEGRAM BOT
    # --------------------------------------------------------

    if settings.TELEGRAM_BOT_TOKEN:

        try:

            telegram_bot_task = (
                asyncio.create_task(
                    telegram_bot_loop()
                )
            )

            logger.info(
                "Telegram bot task created."
            )

        except Exception:

            logger.exception(
                "Failed to create Telegram bot task."
            )

    else:

        logger.warning(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    # --------------------------------------------------------
    # API READY
    # --------------------------------------------------------

    logger.info(
        "=================================================="
    )

    logger.info(
        "Edaaa Wallet API is READY."
    )

    logger.info(
        "=================================================="
    )


# ============================================================
# SHUTDOWN
# ============================================================

@app.on_event(
    "shutdown"
)
async def shutdown():

    global blockchain_scanner_task
    global telegram_bot_task

    logger.info(
        "Edaaa Wallet API shutdown started."
    )

    # --------------------------------------------------------
    # STOP BLOCKCHAIN SCANNER
    # --------------------------------------------------------

    if blockchain_scanner_task:

        blockchain_scanner_task.cancel()

        try:

            await blockchain_scanner_task

        except asyncio.CancelledError:

            pass

        blockchain_scanner_task = None

    # --------------------------------------------------------
    # STOP TELEGRAM BOT
    # --------------------------------------------------------

    if telegram_bot_task:

        telegram_bot_task.cancel()

        try:

            await telegram_bot_task

        except asyncio.CancelledError:

            pass

        telegram_bot_task = None

    logger.info(
        "Edaaa Wallet API shutdown completed."
    )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "status": "ok",
        "message": (
            "Edaaa Wallet API is running"
        ),
        "version": "0.9.0",
        "telegram": bool(
            settings.TELEGRAM_BOT_TOKEN
        ),
    }


# ============================================================
# HEALTH
# ============================================================

@app.get(
    "/health"
)
def health():

    return {
        "status": "healthy",
        "database": "initialized",
        "telegram": bool(
            settings.TELEGRAM_BOT_TOKEN
        ),
    }


# ============================================================
# BLOCKCHAIN STATUS
# ============================================================

@app.get(
    "/blockchain/status"
)
def blockchain_status():

    if not settings.ETH_RPC_URL:

        raise HTTPException(
            status_code=503,
            detail=(
                "ETH_RPC_URL is not configured."
            ),
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
                detail=(
                    "Ethereum RPC connection failed."
                ),
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
            detail=(
                "Blockchain connection error: "
                f"{str(exc)}"
            ),
        )


# ============================================================
# SCANNER STATUS
# ============================================================

@app.get(
    "/blockchain/scanner-status"
)
def blockchain_scanner_status(
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):

    state = (
        db.query(
            BlockchainState
        )
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
        "last_scanned_block": (
            state.last_scanned_block
        ),
        "updated_at": (
            state.updated_at
        ),
    }


# ============================================================
# ADMIN BLOCKCHAIN SYNC
# ============================================================

@app.post(
    "/admin/blockchain/sync"
)
def admin_blockchain_sync(
    admin: User = Depends(
        get_current_admin
    ),
):

    try:

        return scan_once()

    except Exception as exc:

        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Blockchain sync failed: "
                f"{str(exc)}"
            ),
        )


# ============================================================
# REGISTER
# ============================================================

@app.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    data: RegisterRequest,
    db: Session = Depends(
        get_db
    ),
):

    existing_user = (
        db.query(User)
        .filter(
            User.email == data.email
        )
        .first()
    )

    if existing_user:

        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "A user with this email "
                "already exists."
            ),
        )

    get_wallet_fernet()

    user = User(
        email=data.email,
        password_hash=hash_password(
            data.password
        ),
        is_admin=False,
    )

    db.add(user)

    db.flush()

    address, private_key = (
        create_real_ethereum_wallet()
    )

    wallet = Wallet(
        user_id=user.id,
        address=address,
        network=settings.ETH_NETWORK,
    )

    db.add(wallet)

    db.flush()

    encrypted_private_key = (
        encrypt_private_key(
            private_key
        )
    )

    wallet_key = WalletKey(
        wallet_id=wallet.id,
        encrypted_private_key=(
            encrypted_private_key
        ),
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
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Failed to create "
                "user wallet."
            ),
        )

    return user


# ============================================================
# LOGIN
# ============================================================

@app.post(
    "/login",
    response_model=TokenResponse,
)
def login(
    data: LoginRequest,
    db: Session = Depends(
        get_db
    ),
):

    user = (
        db.query(User)
        .filter(
            User.email == data.email
        )
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid email or password."
            ),
        )

    if not verify_password(
        data.password,
        user.password_hash,
    ):

        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid email or password."
            ),
        )

    if not user.is_active:

        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail=(
                "User account is inactive."
            ),
        )

    token = create_access_token(
        {
            "sub": str(
                user.id
            )
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
    }


# ============================================================
# ME
# ============================================================

@app.get(
    "/me",
    response_model=UserResponse,
)
def me(
    current_user: User = Depends(
        get_current_user
    ),
):

    return current_user


# ============================================================
# WALLET
# ============================================================

@app.get(
    "/wallet",
    response_model=WalletResponse,
)
def wallet(
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id
            == current_user.id
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Wallet not found."
            ),
        )

    return wallet


# ============================================================
# USDT BALANCE
# ============================================================

@app.get(
    "/wallet/balance",
    response_model=BalanceResponse,
)
def wallet_balance(
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id
            == current_user.id
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Wallet not found."
            ),
        )

    balance = (
        db.query(Balance)
        .filter(
            Balance.wallet_id
            == wallet.id,
            Balance.asset == "USDT",
        )
        .first()
    )

    if not balance:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "USDT balance not found."
            ),
        )

    return balance


# ============================================================
# TRANSACTIONS
# ============================================================

@app.get(
    "/wallet/transactions",
    response_model=list[
        TransactionResponse
    ],
)
def wallet_transactions(
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id
            == current_user.id
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Wallet not found."
            ),
        )

    transactions = (
        db.query(Transaction)
        .filter(
            Transaction.wallet_id
            == wallet.id
        )
        .order_by(
            Transaction.created_at.desc()
        )
        .all()
    )

    return transactions


# ============================================================
# ETHEREUM BALANCE
# ============================================================

@app.get(
    "/wallet/ethereum-balance"
)
def ethereum_balance(
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):

    if not settings.ETH_RPC_URL:

        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "ETH_RPC_URL is not configured."
            ),
        )

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id
            == current_user.id
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Wallet not found."
            ),
        )

    if not Web3.is_address(
        wallet.address
    ):

        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "Wallet has an invalid "
                "Ethereum address."
            ),
        )

    web3 = Web3(
        Web3.HTTPProvider(
            settings.ETH_RPC_URL
        )
    )

    try:

        if not web3.is_connected():

            raise HTTPException(
                status_code=(
                    status.HTTP_503_SERVICE_UNAVAILABLE
                ),
                detail=(
                    "Ethereum RPC connection failed."
                ),
            )

        checksum_address = (
            Web3.to_checksum_address(
                wallet.address
            )
        )

        balance_wei = (
            web3.eth.get_balance(
                checksum_address
            )
        )

        balance_eth = (
            Web3.from_wei(
                balance_wei,
                "ether",
            )
        )

        return {
            "address": checksum_address,
            "network": settings.ETH_NETWORK,
            "asset": "ETH",
            "balance": str(
                balance_eth
            ),
            "balance_wei": balance_wei,
        }

    except HTTPException:

        raise

    except Exception as exc:

        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Failed to read "
                "Ethereum balance: "
                f"{str(exc)}"
            ),
        )


# ============================================================
# KEY STATUS
# ============================================================

@app.get(
    "/wallet/key-status"
)
def wallet_key_status(
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id
            == current_user.id
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Wallet not found."
            ),
        )

    wallet_key = (
        db.query(WalletKey)
        .filter(
            WalletKey.wallet_id
            == wallet.id
        )
        .first()
    )

    return {
        "wallet_address": wallet.address,
        "private_key_stored": (
            wallet_key is not None
        ),
        "private_key_encrypted": (
            wallet_key is not None
        ),
    }


# ============================================================
# SEND ETH
# ============================================================

@app.post(
    "/wallet/send-eth",
    response_model=SendETHResponse,
)
def send_eth(
    data: SendETHRequest,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id
            == current_user.id
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Wallet not found."
            ),
        )

    if (
        wallet.network
        != settings.ETH_NETWORK
    ):

        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "Wallet network does not "
                "match configured "
                "Ethereum network."
            ),
        )

    to_address = (
        validate_recipient_address(
            data.to_address
        )
    )

    if (
        Web3.to_checksum_address(
            wallet.address
        )
        == to_address
    ):

        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=(
                "Cannot send ETH to "
                "the same wallet."
            ),
        )

    web3 = get_web3()

    private_key = (
        get_wallet_private_key(
            wallet=wallet,
            db=db,
            decrypt_private_key=(
                decrypt_private_key
            ),
        )
    )

    try:

        transaction = (
            create_eth_transaction(
                web3=web3,
                wallet=wallet,
                private_key=private_key,
                to_address=to_address,
                amount=data.amount,
            )
        )

        tx_hash = (
            sign_and_send_eth_transaction(
                web3=web3,
                private_key=private_key,
                transaction=transaction,
            )
        )

    finally:

        private_key = None

    send_transaction = SendTransaction(
        wallet_id=wallet.id,
        asset="ETH",
        to_address=to_address,
        amount=data.amount,
        tx_hash=tx_hash,
        status="pending",
    )

    db.add(
        send_transaction
    )

    try:

        db.commit()

        db.refresh(
            send_transaction
        )

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Transaction was broadcast "
                "but could not be saved."
            ),
        )

    return {
        "id": send_transaction.id,
        "asset": "ETH",
        "from_address": wallet.address,
        "to_address": to_address,
        "amount": str(
            send_transaction.amount
        ),
        "tx_hash": send_transaction.tx_hash,
        "status": send_transaction.status,
    }


# ============================================================
# ADMIN DEPOSIT
# ============================================================

@app.post(
    "/admin/deposit",
    response_model=TransactionResponse,
)
def admin_deposit(
    data: AdminDepositRequest,
    admin: User = Depends(
        get_current_admin
    ),
    db: Session = Depends(
        get_db
    ),
):

    user = (
        db.query(User)
        .filter(
            User.id == data.user_id
        )
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "User not found."
            ),
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
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Wallet not found."
            ),
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
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "USDT balance not found."
            ),
        )

    amount = Decimal(
        data.amount
    )

    balance.amount = (
        Decimal(
            balance.amount
        )
        + amount
    )

    transaction = Transaction(
        wallet_id=wallet.id,
        type="deposit",
        asset="USDT",
        amount=amount,
        status="completed",
        tx_hash=None,
    )

    db.add(
        transaction
    )

    try:

        db.commit()

        db.refresh(
            transaction
        )

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Failed to process deposit."
            ),
        )

    return transaction
