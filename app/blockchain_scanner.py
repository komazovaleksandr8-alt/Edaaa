import logging
from decimal import Decimal

from sqlalchemy.orm import Session
from web3 import Web3

from app.balance_models import Balance
from app.blockchain_state_models import BlockchainState
from app.config import settings
from app.database import SessionLocal
from app.transaction_models import Transaction
from app.wallet_models import Wallet


logger = logging.getLogger("edaaa.blockchain")


# Количество подтверждений, после которого депозит
# считается подтвержденным.
CONFIRMATIONS_REQUIRED = 3

# При первом запуске проверяем последние N блоков.
# Этого достаточно, чтобы найти наш недавний депозит.
INITIAL_LOOKBACK_BLOCKS = 100


def get_web3() -> Web3:
    """
    Создаёт подключение к Ethereum RPC.
    """

    if not settings.ETH_RPC_URL:
        raise RuntimeError(
            "ETH_RPC_URL is not configured."
        )

    web3 = Web3(
        Web3.HTTPProvider(
            settings.ETH_RPC_URL
        )
    )

    if not web3.is_connected():
        raise RuntimeError(
            "Ethereum RPC connection failed."
        )

    return web3


def get_or_create_state(
    db: Session,
    latest_block: int,
) -> BlockchainState:
    """
    Получает состояние scanner для текущей сети.
    Если состояние отсутствует, создаёт его.
    """

    state = (
        db.query(BlockchainState)
        .filter(
            BlockchainState.network
            == settings.ETH_NETWORK
        )
        .first()
    )

    if state:
        return state

    start_block = max(
        0,
        latest_block - INITIAL_LOOKBACK_BLOCKS,
    )

    state = BlockchainState(
        network=settings.ETH_NETWORK,
        last_scanned_block=start_block - 1,
    )

    db.add(state)
    db.commit()
    db.refresh(state)

    logger.info(
        "Blockchain scanner initialized. "
        "Network=%s start_block=%s",
        settings.ETH_NETWORK,
        start_block,
    )

    return state


def get_wallet_map(db: Session) -> dict:
    """
    Загружает все кошельки Edaaa для текущей сети
    и создаёт быстрый lookup по Ethereum-адресу.
    """

    wallets = (
        db.query(Wallet)
        .filter(
            Wallet.network == settings.ETH_NETWORK
        )
        .all()
    )

    wallet_map = {}

    for wallet in wallets:
        try:
            checksum_address = Web3.to_checksum_address(
                wallet.address
            )

            wallet_map[
                checksum_address.lower()
            ] = wallet

        except Exception:
            logger.warning(
                "Invalid wallet address skipped: %s",
                wallet.address,
            )

    return wallet_map


def get_or_create_eth_balance(
    db: Session,
    wallet: Wallet,
) -> Balance:
    """
    Получает ETH balance пользователя.
    Если ETH balance ещё нет — создаёт.
    """

    balance = (
        db.query(Balance)
        .filter(
            Balance.wallet_id == wallet.id,
            Balance.asset == "ETH",
        )
        .first()
    )

    if balance:
        return balance

    balance = Balance(
        wallet_id=wallet.id,
        asset="ETH",
        amount=Decimal("0"),
    )

    db.add(balance)
    db.flush()

    return balance


def transaction_already_processed(
    db: Session,
    tx_hash: str,
) -> bool:
    """
    Проверяет, была ли blockchain-транзакция
    уже зачислена в Edaaa.
    """

    transaction = (
        db.query(Transaction)
        .filter(
            Transaction.tx_hash == tx_hash
        )
        .first()
    )

    return transaction is not None


def process_transaction(
    db: Session,
    tx,
    wallet_map: dict,
    latest_block: int,
) -> bool:
    """
    Проверяет одну Ethereum-транзакцию.

    Возвращает True, если депозит был зачислен.
    """

    # Нас интересуют только транзакции с получателем.
    if not tx.get("to"):
        return False

    try:
        recipient = Web3.to_checksum_address(
            tx["to"]
        )
    except Exception:
        return False

    wallet = wallet_map.get(
        recipient.lower()
    )

    # Адрес получателя не принадлежит Edaaa.
    if not wallet:
        return False

    value_wei = int(tx["value"])

    # Нулевая транзакция не является депозитом ETH.
    if value_wei <= 0:
        return False

    tx_hash = tx["hash"].hex()

    # Защита от двойного зачисления.
    if transaction_already_processed(
        db,
        tx_hash,
    ):
        return False

    block_number = tx["blockNumber"]

    confirmations = (
        latest_block
        - block_number
        + 1
    )

    # Пока недостаточно подтверждений.
    if confirmations < CONFIRMATIONS_REQUIRED:
        return False

    amount_eth = Decimal(
        str(
            Web3.from_wei(
                value_wei,
                "ether",
            )
        )
    )

    if amount_eth <= 0:
        return False

    balance = get_or_create_eth_balance(
        db,
        wallet,
    )

    balance.amount = (
        Decimal(balance.amount)
        + amount_eth
    )

    transaction = Transaction(
        wallet_id=wallet.id,
        type="deposit",
        asset="ETH",
        amount=amount_eth,
        status="completed",
        tx_hash=tx_hash,
    )

    db.add(transaction)

    logger.info(
        "ETH DEPOSIT DETECTED | "
        "wallet=%s | "
        "amount=%s ETH | "
        "tx=%s | "
        "confirmations=%s",
        wallet.address,
        amount_eth,
        tx_hash,
        confirmations,
    )

    return True


def scan_block(
    db: Session,
    web3: Web3,
    block_number: int,
    wallet_map: dict,
    latest_block: int,
) -> int:
    """
    Сканирует один блок.
    """

    block = web3.eth.get_block(
        block_number,
        full_transactions=True,
    )

    processed = 0

    for tx in block["transactions"]:
        try:
            if process_transaction(
                db=db,
                tx=tx,
                wallet_map=wallet_map,
                latest_block=latest_block,
            ):
                processed += 1

        except Exception:
            logger.exception(
                "Failed to process transaction "
                "in block %s",
                block_number,
            )

    return processed


def scan_once() -> dict:
    """
    Выполняет один полный цикл сканирования.
    """

    web3 = get_web3()

    latest_block = web3.eth.block_number

    confirmed_block = (
        latest_block
        - CONFIRMATIONS_REQUIRED
        + 1
    )

    if confirmed_block < 0:
        return {
            "status": "waiting",
            "network": settings.ETH_NETWORK,
            "latest_block": latest_block,
            "confirmed_block": confirmed_block,
            "processed_transactions": 0,
        }

    db = SessionLocal()

    try:
        state = get_or_create_state(
            db=db,
            latest_block=latest_block,
        )

        wallet_map = get_wallet_map(db)

        start_block = (
            state.last_scanned_block + 1
        )

        # Scanner уже дошёл до последнего
        # подтверждённого блока.
        if start_block > confirmed_block:
            return {
                "status": "up_to_date",
                "network": settings.ETH_NETWORK,
                "latest_block": latest_block,
                "confirmed_block": confirmed_block,
                "last_scanned_block": (
                    state.last_scanned_block
                ),
                "processed_transactions": 0,
            }

        processed_transactions = 0

        for block_number in range(
            start_block,
            confirmed_block + 1,
        ):
            processed_transactions += scan_block(
                db=db,
                web3=web3,
                block_number=block_number,
                wallet_map=wallet_map,
                latest_block=latest_block,
            )

            # Сохраняем прогресс после каждого блока.
            state.last_scanned_block = (
                block_number
            )

            db.commit()

        return {
            "status": "completed",
            "network": settings.ETH_NETWORK,
            "latest_block": latest_block,
            "confirmed_block": confirmed_block,
            "last_scanned_block": (
                state.last_scanned_block
            ),
            "processed_transactions": (
                processed_transactions
            ),
        }

    except Exception:
        db.rollback()

        logger.exception(
            "Blockchain scanner failed."
        )

        raise

    finally:
        db.close()
