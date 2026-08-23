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


logger = logging.getLogger(
    "edaaa.blockchain"
)


# ============================================================
# CONFIGURATION
# ============================================================

# После какого количества подтверждений
# депозит считается подтверждённым.
CONFIRMATIONS_REQUIRED = 3

# При первой инициализации scanner
# проверяет последние N блоков.
INITIAL_LOOKBACK_BLOCKS = 100

# Максимальное количество блоков
# за один цикл сканирования.
MAX_BLOCKS_PER_SCAN = 20


# ============================================================
# WEB3
# ============================================================

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


# ============================================================
# BLOCKCHAIN STATE
# ============================================================

def get_or_create_state(
    db: Session,
    latest_block: int,
) -> BlockchainState:
    """
    Получает состояние blockchain scanner.

    При первом запуске scanner начинает
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
        latest_block
        - INITIAL_LOOKBACK_BLOCKS,
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


# ============================================================
# WALLET MAP
# ============================================================

def get_wallet_map(
    db: Session,
) -> dict[str, Wallet]:
    """
    Загружает все Edaaa-кошельки текущей сети.

    Формат:

        {
            "0xaddress": Wallet
        }

    Адреса приводятся к lowercase,
    чтобы сравнение было надёжным.
    """

    wallets = (
        db.query(Wallet)
        .filter(
            Wallet.network
            == settings.ETH_NETWORK
        )
        .all()
    )

    wallet_map: dict[str, Wallet] = {}

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


# ============================================================
# ETH BALANCE
# ============================================================

def get_or_create_eth_balance(
    db: Session,
    wallet: Wallet,
) -> Balance:
    """
    Получает ETH balance кошелька.

    Если ETH balance отсутствует,
    создаёт его с нулевым балансом.
    """

    balance = (
        db.query(Balance)
        .filter(
            Balance.wallet_id
            == wallet.id,
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


# ============================================================
# DUPLICATE PROTECTION
# ============================================================

def transaction_already_processed(
    db: Session,
    tx_hash: str,
) -> bool:
    """
    Проверяет, был ли blockchain TX
    уже обработан Edaaa.

    Если tx_hash уже существует в Transaction,
    депозит повторно не зачисляется.
    """

    transaction = (
        db.query(Transaction)
        .filter(
            Transaction.tx_hash
            == tx_hash
        )
        .first()
    )

    return transaction is not None


# ============================================================
# PROCESS TRANSACTION
# ============================================================

def process_transaction(
    db: Session,
    tx,
    wallet_map: dict[str, Wallet],
    latest_block: int,
) -> bool:
    """
    Проверяет одну Ethereum-транзакцию.

    Обрабатываются только:

        ETH transfer
        ↓
        на Edaaa wallet
        ↓
        с достаточным количеством confirmations

    Возвращает:

        True
            депозит зачислен

        False
            транзакция не является новым депозитом
    """

    # --------------------------------------------------------
    # RECIPIENT
    # --------------------------------------------------------

    recipient_raw = tx.get(
        "to"
    )

    # Contract creation / transaction
    # без recipient нам не нужен.
    if not recipient_raw:

        return False

    try:

        recipient = (
            Web3.to_checksum_address(
                recipient_raw
            )
        )

    except Exception:

        return False

    # --------------------------------------------------------
    # EDAAA WALLET
    # --------------------------------------------------------

    wallet = wallet_map.get(
        recipient.lower()
    )

    if not wallet:

        return False

    # --------------------------------------------------------
    # VALUE
    # --------------------------------------------------------

    try:

        value_wei = int(
            tx["value"]
        )

    except (
        KeyError,
        TypeError,
        ValueError,
    ):

        logger.warning(
            "Invalid transaction value."
        )

        return False

    # Нулевая транзакция.
    if value_wei <= 0:

        return False

    # --------------------------------------------------------
    # TX HASH
    # --------------------------------------------------------

    try:

        raw_hash = tx["hash"]

        if hasattr(
            raw_hash,
            "hex",
        ):

            tx_hash = raw_hash.hex()

        else:

            tx_hash = str(
                raw_hash
            )

    except Exception:

        logger.warning(
            "Unable to extract transaction hash."
        )

        return False

    if not tx_hash:

        return False

    # --------------------------------------------------------
    # DUPLICATE CHECK
    # --------------------------------------------------------

    if transaction_already_processed(
        db=db,
        tx_hash=tx_hash,
    ):

        return False

    # --------------------------------------------------------
    # BLOCK
    # --------------------------------------------------------

    block_number = tx.get(
        "blockNumber"
    )

    if block_number is None:

        return False

    try:

        block_number = int(
            block_number
        )

    except (
        TypeError,
        ValueError,
    ):

        return False

    # --------------------------------------------------------
    # CONFIRMATIONS
    # --------------------------------------------------------

    confirmations = (
        latest_block
        - block_number
        + 1
    )

    if (
        confirmations
        < CONFIRMATIONS_REQUIRED
    ):

        return False

    # --------------------------------------------------------
    # ETH AMOUNT
    # --------------------------------------------------------

    try:

        amount_eth = Decimal(
            str(
                Web3.from_wei(
                    value_wei,
                    "ether",
                )
            )
        )

    except Exception:

        logger.exception(
            "Failed to convert ETH amount | "
            "tx=%s",
            tx_hash,
        )

        return False

    if amount_eth <= 0:

        return False

    # --------------------------------------------------------
    # BALANCE
    # --------------------------------------------------------

    balance = (
        get_or_create_eth_balance(
            db=db,
            wallet=wallet,
        )
    )

    old_balance = Decimal(
        balance.amount
    )

    new_balance = (
        old_balance
        + amount_eth
    )

    balance.amount = (
        new_balance
    )

    # --------------------------------------------------------
    # TRANSACTION RECORD
    # --------------------------------------------------------

    transaction = Transaction(
        wallet_id=wallet.id,
        type="deposit",
        asset="ETH",
        amount=amount_eth,
        status="completed",
        tx_hash=tx_hash,
    )

    db.add(
        transaction
    )

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    logger.info(
        "=================================================="
    )

    logger.info(
        "ETH DEPOSIT DETECTED"
    )

    logger.info(
        "Wallet: %s",
        wallet.address,
    )

    logger.info(
        "Amount: %s ETH",
        amount_eth,
    )

    logger.info(
        "Old balance: %s ETH",
        old_balance,
    )

    logger.info(
        "New balance: %s ETH",
        new_balance,
    )

    logger.info(
        "TX Hash: %s",
        tx_hash,
    )

    logger.info(
        "Block: %s",
        block_number,
    )

    logger.info(
        "Confirmations: %s",
        confirmations,
    )

    logger.info(
        "=================================================="
    )

    return True


# ============================================================
# SCAN BLOCK
# ============================================================

def scan_block(
    db: Session,
    web3: Web3,
    block_number: int,
    wallet_map: dict[str, Wallet],
    latest_block: int,
) -> int:
    """
    Сканирует один Ethereum-блок.

    Возвращает количество найденных
    и зачисленных ETH-депозитов.
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

    transactions = block.get(
        "transactions",
        [],
    )

    for tx in transactions:

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
                "in block %s.",
                block_number,
            )

    return processed


# ============================================================
# SCAN ONCE
# ============================================================

def scan_once() -> dict:
    """
    Выполняет один цикл blockchain scanning.

    За один запуск обрабатывается
    максимум MAX_BLOCKS_PER_SCAN блоков.
    """

    web3 = get_web3()

    latest_block = (
        web3.eth.block_number
    )

    # --------------------------------------------------------
    # CONFIRMED BLOCK
    # --------------------------------------------------------

    confirmed_block = (
        latest_block
        - CONFIRMATIONS_REQUIRED
        + 1
    )

    if confirmed_block < 0:

        return {
            "status": "waiting",
            "network": (
                settings.ETH_NETWORK
            ),
            "latest_block": (
                latest_block
            ),
            "confirmed_block": (
                confirmed_block
            ),
            "processed_transactions": 0,
        }

    db = SessionLocal()

    try:

        # ----------------------------------------------------
        # STATE
        # ----------------------------------------------------

        state = (
            get_or_create_state(
                db=db,
                latest_block=latest_block,
            )
        )

        # ----------------------------------------------------
        # WALLETS
        # ----------------------------------------------------

        wallet_map = (
            get_wallet_map(
                db
            )
        )

        if not wallet_map:

            logger.info(
                "No Edaaa wallets found "
                "for network %s.",
                settings.ETH_NETWORK,
            )

        # ----------------------------------------------------
        # RANGE
        # ----------------------------------------------------

        start_block = (
            state.last_scanned_block
            + 1
        )

        if start_block > confirmed_block:

            return {
                "status": "up_to_date",
                "network": (
                    settings.ETH_NETWORK
                ),
                "latest_block": (
                    latest_block
                ),
                "confirmed_block": (
                    confirmed_block
                ),
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
            "=================================================="
        )

        logger.info(
            "BLOCKCHAIN SCAN"
        )

        logger.info(
            "Network: %s",
            settings.ETH_NETWORK,
        )

        logger.info(
            "Latest block: %s",
            latest_block,
        )

        logger.info(
            "Confirmed block: %s",
            confirmed_block,
        )

        logger.info(
            "Start block: %s",
            start_block,
        )

        logger.info(
            "End block: %s",
            end_block,
        )

        logger.info(
            "Wallets monitored: %s",
            len(wallet_map),
        )

        logger.info(
            "=================================================="
        )

        # ----------------------------------------------------
        # SCAN BLOCKS
        # ----------------------------------------------------

        for block_number in range(
            start_block,
            end_block + 1,
        ):

            try:

                processed_transactions += (
                    scan_block(
                        db=db,
                        web3=web3,
                        block_number=(
                            block_number
                        ),
                        wallet_map=(
                            wallet_map
                        ),
                        latest_block=(
                            latest_block
                        ),
                    )
                )

                # Очень важно:
                # state обновляется только после
                # успешного сканирования блока.
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

                # Не переходим к следующему блоку.
                # Этот блок будет повторно обработан
                # на следующем цикле.
                break

        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

        result = {
            "status": "completed",
            "network": (
                settings.ETH_NETWORK
            ),
            "latest_block": (
                latest_block
            ),
            "confirmed_block": (
                confirmed_block
            ),
            "start_block": (
                start_block
            ),
            "end_block": (
                end_block
            ),
            "last_scanned_block": (
                state.last_scanned_block
            ),
            "processed_transactions": (
                processed_transactions
            ),
        }

        logger.info(
            "Blockchain scan completed | "
            "last_scanned_block=%s | "
            "processed_transactions=%s",
            state.last_scanned_block,
            processed_transactions,
        )

        return result

    except Exception:

        db.rollback()

        logger.exception(
            "Blockchain scanner failed."
        )

        raise

    finally:

        db.close()
