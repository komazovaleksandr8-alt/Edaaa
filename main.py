import asyncio
import logging
from decimal import Decimal

from cryptography.fernet import (
    Fernet,
    InvalidToken,
)

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
)

from fastapi.middleware.cors import (
    CORSMiddleware,
)

from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from pydantic import (
    BaseModel,
    Field,
)

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

from app.blockchain_scanner import (
    scan_once,
)

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
    version="1.0.0",
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
# ADMIN REQUESTS
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
# WALLET SECURITY
# ============================================================

def get_wallet_fernet() -> Fernet:

    key = settings.WALLET_ENCRYPTION_KEY

    if not key:

        raise HTTPException(
            status_code=503,
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
            status_code=500,
            detail=(
                "Invalid WALLET_ENCRYPTION_KEY."
            ),
        ) from exc


def create_real_ethereum_wallet():

    account = Account.create()

    address = (
        Web3.to_checksum_address(
            account.address
        )
    )

    return (
        address,
        account.key.hex(),
    )


def encrypt_private_key(
    private_key: str,
) -> str:

    return (
        get_wallet_fernet()
        .encrypt(
            private_key.encode()
        )
        .decode()
    )


def decrypt_private_key(
    encrypted_private_key: str,
) -> str:

    try:

        return (
            get_wallet_fernet()
            .decrypt(
                encrypted_private_key.encode()
            )
            .decode()
        )

    except InvalidToken as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to decrypt "
                "wallet private key."
            ),
        ) from exc


# ============================================================
# DATABASE VALIDATION
# ============================================================

def validate_database_configuration():

    database_url = (
        settings.DATABASE_URL
        or ""
    ).strip()

    if not database_url:

        raise RuntimeError(
            "DATABASE_URL is not configured. "
            "Edaaa requires a persistent PostgreSQL "
            "database in production."
        )

    if database_url.startswith(
        "sqlite:///"
    ):

        raise RuntimeError(
            "SQLite is not allowed in production. "
            "Configure DATABASE_URL with the "
            "Render PostgreSQL database."
        )

    if not (
        database_url.startswith(
            "postgresql://"
        )
        or database_url.startswith(
            "postgresql+psycopg2://"
        )
        or database_url.startswith(
            "postgresql+asyncpg://"
        )
    ):

        raise RuntimeError(
            "Unsupported DATABASE_URL. "
            "Edaaa production requires PostgreSQL."
        )

    logger.info(
        "Persistent PostgreSQL database configuration detected."
    )


# ============================================================
# AUTH
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

            raise ValueError()

        user_id = int(
            user_id
        )

    except (
        ValueError,
        TypeError,
    ):

        raise HTTPException(
            status_code=401,
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
            status_code=401,
            detail="User not found.",
            headers={
                "WWW-Authenticate": "Bearer"
            },
        )

    if not user.is_active:

        raise HTTPException(
            status_code=403,
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
            status_code=403,
            detail="Admin access required.",
        )

    return current_user


# ============================================================
# BLOCKCHAIN SCANNER LOOP
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
# TELEGRAM SUPERVISOR
# ============================================================

async def telegram_bot_loop():

    global telegram_application

    retry_delay = 10

    while True:

        try:

            logger.info(
                "Starting Edaaa Telegram bot..."
            )

            telegram_application = (
                create_telegram_application()
            )

            await telegram_application.initialize()

            await telegram_application.start()

            if (
                telegram_application.updater
                is None
            ):

                raise RuntimeError(
                    "Telegram updater is not available."
                )

            await (
                telegram_application
                .updater
                .start_polling(
                    drop_pending_updates=False,
                )
            )

            logger.info(
                "Edaaa Telegram bot started successfully."
            )

            while True:

                await asyncio.sleep(
                    3600
                )

        except asyncio.CancelledError:

            raise

        except Exception:

            logger.exception(
                "Telegram bot crashed. "
                "Restarting automatically."
            )

        finally:

            if telegram_application:

                try:

                    if (
                        telegram_application.updater
                    ):

                        await (
                            telegram_application
                            .updater
                            .stop()
                        )

                except Exception:

                    logger.exception(
                        "Failed to stop Telegram updater."
                    )

                try:

                    await (
                        telegram_application.stop()
                    )

                except Exception:

                    logger.exception(
                        "Failed to stop Telegram application."
                    )

                try:

                    await (
                        telegram_application
                        .shutdown()
                    )

                except Exception:

                    logger.exception(
                        "Failed to shutdown Telegram application."
                    )

                telegram_application = None

        await asyncio.sleep(
            retry_delay
        )


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
        "Edaaa Wallet API startup started."
    )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    try:

        validate_database_configuration()

        await asyncio.to_thread(
            init_database
        )

        logger.info(
            "Database initialization completed successfully."
        )

    except Exception:

        logger.exception(
            "CRITICAL: Database initialization failed."
        )

        # Очень важно:
        # приложение НЕ должно продолжать работу,
        # если постоянная БД недоступна.
        raise

    # --------------------------------------------------------
    # BLOCKCHAIN SCANNER
    # --------------------------------------------------------

    blockchain_scanner_task = (
        asyncio.create_task(
            blockchain_scanner_loop()
        )
    )

    logger.info(
        "Blockchain scanner task created."
    )

    # --------------------------------------------------------
    # TELEGRAM BOT
    # --------------------------------------------------------

    if settings.TELEGRAM_BOT_TOKEN:

        telegram_bot_task = (
            asyncio.create_task(
                telegram_bot_loop()
            )
        )

        logger.info(
            "Telegram supervisor task created."
        )

    else:

        logger.warning(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    logger.info(
        "Edaaa Wallet API is READY."
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
    # BLOCKCHAIN SCANNER
    # --------------------------------------------------------

    if blockchain_scanner_task:

        blockchain_scanner_task.cancel()

        try:

            await blockchain_scanner_task

        except asyncio.CancelledError:

            pass

        blockchain_scanner_task = None

    # --------------------------------------------------------
    # TELEGRAM
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
        "version": "1.0.0",
        "telegram": bool(
            settings.TELEGRAM_BOT_TOKEN
        ),
    }


@app.head("/")
def root_head():

    return None


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
            settings.ETH_RPC_URL,
            request_kwargs={
                "timeout": 30,
            },
        )
    )

    try:

        if not web3.is_connected():

            raise HTTPException(
                status_code=503,
                detail=(
                    "Ethereum RPC connection failed."
                ),
            )

        return {
            "connected": True,
            "network": settings.ETH_NETWORK,
            "chain_id": (
                web3.eth.chain_id
            ),
            "latest_block": (
                web3.eth.block_number
            ),
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
            status_code=503,
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
    status_code=201,
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
            status_code=409,
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

    db.add(
        WalletKey(
            wallet_id=wallet.id,
            encrypted_private_key=(
                encrypt_private_key(
                    private_key
                )
            ),
        )
    )

    db.add(
        Balance(
            wallet_id=wallet.id,
            asset="USDT",
            amount=Decimal("0"),
        )
    )

    db.add(
        Balance(
            wallet_id=wallet.id,
            asset="ETH",
            amount=Decimal("0"),
        )
    )

    try:

        db.commit()

        db.refresh(user)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=500,
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
            status_code=401,
            detail=(
                "Invalid email or password."
            ),
        )

    if not verify_password(
        data.password,
        user.password_hash,
    ):

        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid email or password."
            ),
        )

    if not user.is_active:

        raise HTTPException(
            status_code=403,
            detail=(
                "User account is inactive."
            ),
        )

    return {
        "access_token": create_access_token(
            {
                "sub": str(
                    user.id
                )
            }
        ),
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
        .order_by(
            Wallet.id.asc()
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=404,
            detail="Wallet not found.",
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
        .order_by(
            Wallet.id.asc()
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=404,
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
            status_code=404,
            detail="USDT balance not found.",
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
        .order_by(
            Wallet.id.asc()
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=404,
            detail="Wallet not found.",
        )

    return (
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


# ============================================================
# ETH BALANCE
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

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id
            == current_user.id
        )
        .order_by(
            Wallet.id.asc()
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=404,
            detail="Wallet not found.",
        )

    web3 = get_web3()

    try:

        address = (
            Web3.to_checksum_address(
                wallet.address
            )
        )

        balance_wei = (
            web3.eth.get_balance(
                address
            )
        )

        return {
            "address": address,
            "network": (
                settings.ETH_NETWORK
            ),
            "asset": "ETH",
            "balance": str(
                Web3.from_wei(
                    balance_wei,
                    "ether",
                )
            ),
            "balance_wei": balance_wei,
        }

    except Exception as exc:

        raise HTTPException(
            status_code=503,
            detail=(
                "Failed to read "
                f"Ethereum balance: {str(exc)}"
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
        .order_by(
            Wallet.id.asc()
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=404,
            detail="Wallet not found.",
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
# SEND ETH API
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
        .order_by(
            Wallet.id.asc()
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=404,
            detail="Wallet not found.",
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
            status_code=400,
            detail=(
                "Cannot send ETH to "
                "the same wallet."
            ),
        )

    transaction_record = Transaction(
        wallet_id=wallet.id,
        type="withdraw",
        asset="ETH",
        amount=data.amount,
        status="broadcasting",
        tx_hash=None,
    )

    db.add(
        transaction_record
    )

    db.commit()
    db.refresh(
        transaction_record
    )

    private_key = None

    try:

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

        transaction_data = (
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
                transaction=transaction_data,
            )
        )

        transaction_record.tx_hash = (
            tx_hash
        )

        transaction_record.status = (
            "pending"
        )

        db.commit()

        db.refresh(
            transaction_record
        )

    except Exception:

        transaction_record.status = (
            "failed"
        )

        db.commit()

        raise

    finally:

        private_key = None

    return {
        "id": transaction_record.id,
        "asset": "ETH",
        "from_address": wallet.address,
        "to_address": to_address,
        "amount": str(
            transaction_record.amount
        ),
        "tx_hash": (
            transaction_record.tx_hash
        ),
        "status": (
            transaction_record.status
        ),
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
            User.id
            == data.user_id
        )
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.user_id
            == user.id
        )
        .order_by(
            Wallet.id.asc()
        )
        .first()
    )

    if not wallet:

        raise HTTPException(
            status_code=404,
            detail="Wallet not found.",
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
            status_code=404,
            detail="USDT balance not found.",
        )

    balance.amount = (
        Decimal(
            balance.amount
        )
        + Decimal(
            data.amount
        )
    )

    transaction = Transaction(
        wallet_id=wallet.id,
        type="deposit",
        asset="USDT",
        amount=Decimal(
            data.amount
        ),
        status="completed",
        tx_hash=None,
    )

    db.add(
        transaction
    )

    db.commit()
    db.refresh(
        transaction
    )

    return transaction
