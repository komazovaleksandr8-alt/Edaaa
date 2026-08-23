import logging

from sqlalchemy.orm import Session
from web3 import Web3

from app.config import settings
from app.database import SessionLocal
from app.send_models import SendTransaction


logger = logging.getLogger(
    "edaaa.send_scanner"
)


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


def scan_pending_send_transactions() -> dict:
    """
    Проверяет все исходящие ETH-транзакции
    со статусом pending.

    Blockchain receipt:
        status=1 -> completed
        status=0 -> failed
    """

    web3 = get_web3()

    db: Session = SessionLocal()

    try:

        transactions = (
            db.query(
                SendTransaction
            )
            .filter(
                SendTransaction.status
                == "pending",
                SendTransaction.tx_hash
                .isnot(None),
            )
            .all()
        )

        checked = 0
        completed = 0
        failed = 0

        for transaction in transactions:

            checked += 1

            try:

                tx_hash = (
                    transaction.tx_hash
                )

                receipt = (
                    web3.eth
                    .get_transaction_receipt(
                        tx_hash
                    )
                )

                if receipt is None:
                    continue

                receipt_status = (
                    receipt.get(
                        "status"
                    )
                )

                block_number = (
                    receipt.get(
                        "blockNumber"
                    )
                )

                if receipt_status == 1:

                    transaction.status = (
                        "completed"
                    )

                    completed += 1

                    logger.info(
                        "ETH SEND COMPLETED | "
                        "id=%s | "
                        "tx=%s | "
                        "block=%s",
                        transaction.id,
                        tx_hash,
                        block_number,
                    )

                elif receipt_status == 0:

                    transaction.status = (
                        "failed"
                    )

                    failed += 1

                    logger.warning(
                        "ETH SEND FAILED | "
                        "id=%s | "
                        "tx=%s | "
                        "block=%s",
                        transaction.id,
                        tx_hash,
                        block_number,
                    )

            except Exception:

                logger.exception(
                    "Failed to check send "
                    "transaction | "
                    "id=%s | "
                    "tx=%s",
                    transaction.id,
                    transaction.tx_hash,
                )

        db.commit()

        return {
            "status": "completed",
            "network": settings.ETH_NETWORK,
            "checked": checked,
            "completed": completed,
            "failed": failed,
        }

    except Exception:

        db.rollback()

        logger.exception(
            "Send transaction scanner failed."
        )

        raise

    finally:

        db.close()
