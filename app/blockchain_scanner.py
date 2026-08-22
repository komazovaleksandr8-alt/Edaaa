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


# После какого количества подтверждений
# депозит считается подтвержденным.
CONFIRMATIONS_REQUIRED = 3

# При первой инициализации scanner смотрит
# последние 100 блоков.
INITIAL_LOOKBACK_BLOCKS = 100

# Максимальное количество блоков за один цикл.
# Это защищает Render от слишком долгого сканирования.
MAX_BLOCKS_PER_SCAN = 20


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
            settings.ETH_RPC_URL,
            request_kwargs={
                "timeout": 30,
            },
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
    Получает состояние scanner.

    При первом запуске начинает сканирование
    с последних INITIAL_LOOKBACK_BLOCKS блоков.
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
        "Blockchain scanner initialized | "
        "network=%s | "
        "start_block=%s | "
        "latest_block=%s",
        settings.ETH_NETWORK,
        start_block,
        latest_block,
    )

    return state


def get_wallet_map(db: Session) -> dict:
    """
    Загружает все кошельки Edaaa текущей сети.

    Ключ dictionary:
        lowercase Ethereum address

    Значение:
        Wallet
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
            checksum_address = (
                Web3.to_checksum_address(
                    wallet.address
                )
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
    Получает ETH balance кошелька.
    Если его ещё нет — создаёт.
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
    Проверяет, существует ли уже транзакция
    с таким blockchain tx_hash.
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

    Если транзакция является входящим ETH-депозитом
    на кошелёк Edaaa и имеет необходимое количество
    подтверждений — зачисляет ETH.

    Возвращает:
        True  — депозит зачислен
        False — транзакция не является новым депозитом
    """

    # Контрактные/служебные транзакции без recipient
    # нас здесь не интересуют.
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

    # Получатель не является кошельком Edaaa.
    if not wallet:
        return False

    value_wei = int(
        tx["value"]
    )

    # Нулевая ETH-транзакция.
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

    if block_number is None:
        return False

    confirmations = (
        latest_block
        - block_number
        + 1
    )

    # Ещё недостаточно подтверждений.
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

    old_balance = Decimal(
        balance.amount
    )

    new_balance = (
        old_balance + amount_eth
    )

    balance.amount = new_balance

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
        "old_balance=%s ETH | "
        "new_balance=%s ETH | "
        "tx=%s | "
        "confirmations=%s",
        wallet.address,
        amount_eth,
        old_balance,
        new_balance,
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

    logger.info(
        "Scanning block %s",
        block_number,
    )

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
    Выполняет один цикл blockchain scanning.

    За один запуск обрабатывается не более
    MAX_BLOCKS_PER_SCAN блоков.
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

        if not wallet_map:
            logger.info(
                "No Edaaa wallets found for network %s.",
                settings.ETH_NETWORK,
            )

        start_block = (
            state.last_scanned_block + 1
        )

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

        end_block = min(
            start_block
            + MAX_BLOCKS_PER_SCAN
            - 1,
            confirmed_block,
        )

        processed_transactions = 0

        logger.info(
            "Scanner range | "
            "start=%s | "
            "end=%s | "
            "confirmed=%s",
            start_block,
            end_block,
            confirmed_block,
        )

        for block_number in range(
            start_block,
            end_block + 1,
        ):
            try:
                processed_transactions += (
                    scan_block(
                        db=db,
                        web3=web3,
                        block_number=block_number,
                        wallet_map=wallet_map,
                        latest_block=latest_block,
                    )
                )

                state.last_scanned_block = (
                    block_number
                )

                db.commit()

            except Exception:
                db.rollback()

                logger.exception(
                    "Failed to scan block %s.",
                    block_number,
                )

                # Не двигаем state дальше
                # при ошибке конкретного блока.
                break

        return {
            "status": "completed",
            "network": settings.ETH_NETWORK,
            "latest_block": latest_block,
            "confirmed_block": confirmed_block,
            "start_block": start_block,
            "end_block": end_block,
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
