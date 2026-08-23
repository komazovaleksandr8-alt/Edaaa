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


CONFIRMATIONS_REQUIRED = 3

INITIAL_LOOKBACK_BLOCKS = 100

MAX_BLOCKS_PER_SCAN = 20


# ============================================================
# WEB3
# ============================================================

def get_web3() -> Web3:

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
        last_scanned_block=(
            start_block - 1
        ),
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
    Сначала ищет кошельки текущей сети.

    Если таких нет, использует существующие Wallet
    как legacy fallback. Это позволяет старым кошелькам,
    созданным до фикса network, продолжить работать
    на текущем Sepolia RPC.
    """

    wallets = (
        db.query(Wallet)
        .filter(
            Wallet.network
            == settings.ETH_NETWORK
        )
        .all()
    )

    if not wallets:

        legacy_wallets = (
            db.query(Wallet)
            .all()
        )

        if legacy_wallets:

            logger.warning(
                "No wallets found for configured network '%s'. "
                "Using %s legacy wallet(s) as fallback.",
                settings.ETH_NETWORK,
                len(legacy_wallets),
            )

            wallets = legacy_wallets

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

            logger.info(
                "Monitoring wallet | "
                "id=%s | "
                "user_id=%s | "
                "address=%s | "
                "network=%s",
                wallet.id,
                wallet.user_id,
                checksum_address,
                wallet.network,
            )

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
# TX DUPLICATE CHECK
# ============================================================

def transaction_already_processed(
    db: Session,
    tx_hash: str,
) -> bool:

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

    wallet = wallet_map.get(
        recipient.lower()
    )

    if not wallet:

        return False

    try:

        value_wei = int(
            tx["value"]
        )

    except (
        KeyError,
        TypeError,
        ValueError,
    ):

        return False

    if value_wei <= 0:

        return False

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

        return False

    if not tx_hash:

        return False

    if transaction_already_processed(
        db=db,
        tx_hash=tx_hash,
    ):

        return False

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
# PENDING OUTGOING
# ============================================================

def get_pending_send_transactions(
    db: Session,
) -> list[Transaction]:

    return (
        db.query(Transaction)
        .filter(
            Transaction.type == "withdraw",
            Transaction.asset == "ETH",
            Transaction.status == "pending",
            Transaction.tx_hash.isnot(None),
        )
        .all()
    )


def process_pending_send_transaction(
    db: Session,
    web3: Web3,
    transaction: Transaction,
    latest_block: int,
) -> bool:

    tx_hash = transaction.tx_hash

    if not tx_hash:

        return False

    try:

        receipt = (
            web3.eth.get_transaction_receipt(
                tx_hash
            )
        )

    except Exception:

        return False

    if not receipt:

        return False

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

        return False

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

        return False

    # --------------------------------------------------------
    # FAILED
    # --------------------------------------------------------

    if receipt_status == 0:

        transaction.status = (
            "failed"
        )

        logger.warning(
            "ETH SEND FAILED | "
            "id=%s | tx=%s",
            transaction.id,
            tx_hash,
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
            "ETH send awaiting confirmations | "
            "id=%s | "
            "tx=%s | "
            "confirmations=%s/%s",
            transaction.id,
            tx_hash,
            confirmations,
            CONFIRMATIONS_REQUIRED,
        )

        return False

    if (
        transaction.status
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
            == transaction.wallet_id
        )
        .first()
    )

    if not wallet:

        transaction.status = (
            "failed"
        )

        logger.error(
            "Wallet not found for withdrawal | "
            "transaction_id=%s",
            transaction.id,
        )

        return True

    # --------------------------------------------------------
    # INTERNAL BALANCE
    # --------------------------------------------------------

    balance = (
        get_or_create_eth_balance(
            db=db,
            wallet=wallet,
        )
    )

    amount = Decimal(
        transaction.amount
    )

    current_balance = Decimal(
        balance.amount
    )

    if current_balance < amount:

        logger.error(
            "Withdrawal confirmed on blockchain "
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

        transaction.status = (
            "completed"
        )

        return True

    old_balance = current_balance

    balance.amount = (
        current_balance
        - amount
    )

    transaction.status = (
        "completed"
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "ETH SEND CONFIRMED"
    )

    logger.info(
        "Transaction ID: %s",
        transaction.id,
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
        balance.amount,
    )

    logger.info(
        "TX Hash: %s",
        tx_hash,
    )

    logger.info(
        "Confirmations: %s",
        confirmations,
    )

    logger.info(
        "=================================================="
    )

    return True


def process_pending_sends(
    db: Session,
    web3: Web3,
    latest_block: int,
) -> dict:

    transactions = (
        get_pending_send_transactions(
            db
        )
    )

    checked = 0
    completed = 0
    failed = 0

    for transaction in transactions:

        checked += 1

        try:

            changed = (
                process_pending_send_transaction(
                    db=db,
                    web3=web3,
                    transaction=transaction,
                    latest_block=latest_block,
                )
            )

            if changed:

                if (
                    transaction.status
                    == "completed"
                ):

                    completed += 1

                elif (
                    transaction.status
                    == "failed"
                ):

                    failed += 1

                db.commit()

        except Exception:

            db.rollback()

            logger.exception(
                "Failed to process pending "
                "withdrawal | "
                "id=%s | "
                "tx=%s",
                transaction.id,
                transaction.tx_hash,
            )

    return {
        "checked": checked,
        "completed": completed,
        "failed": failed,
    }


# ============================================================
# BLOCK
# ============================================================

def scan_block(
    db: Session,
    web3: Web3,
    block_number: int,
    wallet_map: dict[str, Wallet],
    latest_block: int,
) -> int:

    logger.info(
        "Scanning block %s",
        block_number,
    )

    block = web3.eth.get_block(
        block_number,
        full_transactions=True,
    )

    processed = 0

    for tx in block.get(
        "transactions",
        [],
    ):

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

    web3 = get_web3()

    latest_block = (
        web3.eth.block_number
    )

    confirmed_block = (
        latest_block
        - CONFIRMATIONS_REQUIRED
        + 1
    )

    db = SessionLocal()

    try:

        # ----------------------------------------------------
        # OUTGOING
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
            get_wallet_map(db)
        )

        if not wallet_map:

            logger.warning(
                "No Edaaa wallets found."
            )

        # ----------------------------------------------------
        # RANGE
        # ----------------------------------------------------

        start_block = (
            state.last_scanned_block
            + 1
        )

        if (
            start_block
            > confirmed_block
        ):

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
                "wallets_monitored": (
                    len(wallet_map)
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
        # BLOCKS
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

                break

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
            "wallets_monitored": (
                len(wallet_map)
            ),
        }

        logger.info(
            "Blockchain scan completed | "
            "last_scanned_block=%s | "
            "processed_transactions=%s | "
            "pending_checked=%s | "
            "pending_completed=%s | "
            "pending_failed=%s | "
            "wallets_monitored=%s",
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
            len(wallet_map),
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
