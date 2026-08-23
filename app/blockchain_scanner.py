import logging
from decimal import Decimal

from sqlalchemy.orm import Session
from web3 import Web3

from app.balance_models import Balance
from app.blockchain_state_models import BlockchainState
from app.config import settings
from app.database import SessionLocal
from app.send_models import SendTransaction
from app.transaction_models import Transaction
from app.wallet_models import Wallet


logger = logging.getLogger(
    "edaaa.blockchain"
)


# ============================================================
# CONFIGURATION
# ============================================================

# Количество подтверждений, после которых
# blockchain transaction считается подтверждённой.
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
    уже записан в Transaction.
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
# ETH DEPOSIT
# ============================================================

def process_transaction(
    db: Session,
    tx,
    wallet_map: dict[str, Wallet],
    latest_block: int,
) -> bool:
    """
    Проверяет одну Ethereum-транзакцию.

    Обрабатываются только входящие ETH-транзакции
    на кошельки Edaaa с необходимым количеством
    подтверждений.
    """

    # --------------------------------------------------------
    # RECIPIENT
    # --------------------------------------------------------

    recipient_raw = tx.get(
        "to"
    )

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
        "ETH DEPOSIT CONFIRMED"
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
# PENDING OUTGOING TRANSACTIONS
# ============================================================

def get_pending_send_transactions(
    db: Session,
) -> list[SendTransaction]:
    """
    Возвращает все исходящие ETH-транзакции,
    которые ещё находятся в pending.
    """

    transactions = (
        db.query(SendTransaction)
        .filter(
            SendTransaction.asset == "ETH",
            SendTransaction.status == "pending",
            SendTransaction.tx_hash.isnot(None),
        )
        .all()
    )

    return transactions


# ============================================================
# PROCESS PENDING SEND
# ============================================================

def process_pending_send_transaction(
    db: Session,
    web3: Web3,
    send_transaction: SendTransaction,
    latest_block: int,
) -> bool:
    """
    Проверяет исходящую ETH-транзакцию.

    Возможные состояния:

        pending
            транзакция ещё не включена в блок

        pending
            транзакция включена, но мало confirmations

        completed
            транзакция успешно подтверждена

        failed
            blockchain receipt.status == 0
    """

    tx_hash = send_transaction.tx_hash

    if not tx_hash:

        return False

    # --------------------------------------------------------
    # GET RECEIPT
    # --------------------------------------------------------

    try:

        receipt = (
            web3.eth.get_transaction_receipt(
                tx_hash
            )
        )

    except Exception as exc:

        # TransactionNotFound / RPC error.
        # В обоих случаях не меняем pending.
        logger.info(
            "Pending ETH transaction not mined yet | "
            "tx=%s | error=%s",
            tx_hash,
            str(exc),
        )

        return False

    if not receipt:

        return False

    # --------------------------------------------------------
    # RECEIPT BLOCK
    # --------------------------------------------------------

    receipt_block = receipt.get(
        "blockNumber"
    )

    if receipt_block is None:

        return False

    try:

        receipt_block = int(
            receipt_block
        )

    except (
        TypeError,
        ValueError,
    ):

        logger.warning(
            "Invalid receipt block | "
            "tx=%s",
            tx_hash,
        )

        return False

    # --------------------------------------------------------
    # RECEIPT STATUS
    # --------------------------------------------------------

    receipt_status = receipt.get(
        "status"
    )

    try:

        receipt_status = int(
            receipt_status
        )

    except (
        TypeError,
        ValueError,
    ):

        receipt_status = 1

    # --------------------------------------------------------
    # FAILED TRANSACTION
    # --------------------------------------------------------

    if receipt_status == 0:

        send_transaction.status = (
            "failed"
        )

        logger.error(
            "=================================================="
        )

        logger.error(
            "ETH SEND FAILED"
        )

        logger.error(
            "SendTransaction ID: %s",
            send_transaction.id,
        )

        logger.error(
            "TX Hash: %s",
            tx_hash,
        )

        logger.error(
            "Wallet ID: %s",
            send_transaction.wallet_id,
        )

        logger.error(
            "=================================================="
        )

        return True

    # --------------------------------------------------------
    # CONFIRMATIONS
    # --------------------------------------------------------

    confirmations = (
        latest_block
        - receipt_block
        + 1
    )

    if (
        confirmations
        < CONFIRMATIONS_REQUIRED
    ):

        logger.info(
            "ETH send mined but waiting "
            "for confirmations | "
            "tx=%s | "
            "block=%s | "
            "confirmations=%s/%s",
            tx_hash,
            receipt_block,
            confirmations,
            CONFIRMATIONS_REQUIRED,
        )

        return False

    # --------------------------------------------------------
    # ALREADY COMPLETED
    # --------------------------------------------------------

    if (
        send_transaction.status
        == "completed"
    ):

        return False

    # --------------------------------------------------------
    # WALLET
    # --------------------------------------------------------

    wallet = (
        db.query(Wallet)
        .filter(
            Wallet.id
            == send_transaction.wallet_id
        )
        .first()
    )

    if not wallet:

        logger.error(
            "Wallet not found for SendTransaction | "
            "id=%s",
            send_transaction.id,
        )

        send_transaction.status = (
            "failed"
        )

        return True

    # --------------------------------------------------------
    # INTERNAL ETH BALANCE
    # --------------------------------------------------------

    balance = (
        get_or_create_eth_balance(
            db=db,
            wallet=wallet,
        )
    )

    amount = Decimal(
        send_transaction.amount
    )

    current_balance = Decimal(
        balance.amount
    )

    # --------------------------------------------------------
    # PROTECTION AGAINST NEGATIVE BALANCE
    # --------------------------------------------------------

    if current_balance < amount:

        logger.error(
            "ETH send confirmed on blockchain, "
            "but internal balance is insufficient | "
            "wallet=%s | "
            "balance=%s | "
            "amount=%s | "
            "tx=%s",
            wallet.address,
            current_balance,
            amount,
            tx_hash,
        )

        # Мы НЕ помечаем failed,
        # потому что blockchain transaction
        # уже реально выполнена.

        send_transaction.status = (
            "completed"
        )

        return True

    # --------------------------------------------------------
    # DEDUCT BALANCE
    # --------------------------------------------------------

    old_balance = current_balance

    new_balance = (
        current_balance
        - amount
    )

    balance.amount = (
        new_balance
    )

    # --------------------------------------------------------
    # MARK COMPLETED
    # --------------------------------------------------------

    send_transaction.status = (
        "completed"
    )

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    logger.info(
        "=================================================="
    )

    logger.info(
        "ETH SEND CONFIRMED"
    )

    logger.info(
        "SendTransaction ID: %s",
        send_transaction.id,
    )

    logger.info(
        "Wallet: %s",
        wallet.address,
    )

    logger.info(
        "Amount: %s ETH",
        amount,
    )

    logger.info(
        "Old internal balance: %s ETH",
        old_balance,
    )

    logger.info(
        "New internal balance: %s ETH",
        new_balance,
    )

    logger.info(
        "TX Hash: %s",
        tx_hash,
    )

    logger.info(
        "Receipt block: %s",
        receipt_block,
    )

    logger.info(
        "Confirmations: %s",
        confirmations,
    )

    logger.info(
        "Status: completed",
    )

    logger.info(
        "=================================================="
    )

    return True


# ============================================================
# PROCESS PENDING SENDS
# ============================================================

def process_pending_sends(
    db: Session,
    web3: Web3,
    latest_block: int,
) -> dict:
    """
    Проверяет все pending ETH withdrawals.
    """

    pending_transactions = (
        get_pending_send_transactions(
            db
        )
    )

    if not pending_transactions:

        return {
            "checked": 0,
            "completed": 0,
            "failed": 0,
        }

    checked = 0
    completed = 0
    failed = 0

    logger.info(
        "Checking pending ETH sends | "
        "count=%s",
        len(
            pending_transactions
        ),
    )

    for send_transaction in (
        pending_transactions
    ):

        checked += 1

        old_status = (
            send_transaction.status
        )

        try:

            changed = (
                process_pending_send_transaction(
                    db=db,
                    web3=web3,
                    send_transaction=(
                        send_transaction
                    ),
                    latest_block=(
                        latest_block
                    ),
                )
            )

            if changed:

                if (
                    send_transaction.status
                    == "completed"
                ):

                    completed += 1

                elif (
                    send_transaction.status
                    == "failed"
                ):

                    failed += 1

                db.commit()

        except Exception:

            db.rollback()

            logger.exception(
                "Failed to process pending "
                "ETH send | "
                "id=%s | "
                "tx=%s",
                send_transaction.id,
                send_transaction.tx_hash,
            )

            # После rollback объект может быть
            # expired, поэтому ничего с ним больше
            # не делаем в этой итерации.

            continue

        if (
            old_status
            != send_transaction.status
        ):

            logger.info(
                "Send transaction status changed | "
                "id=%s | "
                "tx=%s | "
                "%s -> %s",
                send_transaction.id,
                send_transaction.tx_hash,
                old_status,
                send_transaction.status,
            )

    return {
        "checked": checked,
        "completed": completed,
        "failed": failed,
    }


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

    1. Проверяет pending ETH sends.
    2. Сканирует новые подтверждённые блоки.
    3. Обрабатывает ETH deposits.
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
            "pending_sends": {
                "checked": 0,
                "completed": 0,
                "failed": 0,
            },
        }

    db = SessionLocal()

    try:

        # ----------------------------------------------------
        # PENDING SENDS
        # ----------------------------------------------------

        pending_send_result = (
            process_pending_sends(
                db=db,
                web3=web3,
                latest_block=latest_block,
            )
        )

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
                "pending_sends": (
                    pending_send_result
                ),
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
            "Pending sends checked: %s",
            pending_send_result[
                "checked"
            ],
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

                # State двигается только после
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

                # Останавливаемся.
                # Неуспешный блок будет повторён
                # следующим циклом.
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
            "pending_sends": (
                pending_send_result
            ),
        }

        logger.info(
            "Blockchain scan completed | "
            "last_scanned_block=%s | "
            "processed_transactions=%s | "
            "pending_checked=%s | "
            "pending_completed=%s | "
            "pending_failed=%s",
            state.last_scanned_block,
            processed_transactions,
            pending_send_result[
                "checked"
            ],
            pending_send_result[
                "completed"
            ],
            pending_send_result[
                "failed"
            ],
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
