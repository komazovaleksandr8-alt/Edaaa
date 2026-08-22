from decimal import Decimal

from eth_account import Account
from web3 import Web3
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.wallet_models import Wallet
from app.wallet_key_models import WalletKey
from app.send_models import SendTransaction


def get_wallet_private_key(
    wallet: Wallet,
    db: Session,
    decrypt_private_key,
) -> str:
    wallet_key = (
        db.query(WalletKey)
        .filter(
            WalletKey.wallet_id == wallet.id
        )
        .first()
    )

    if not wallet_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Encrypted wallet private key not found.",
        )

    return decrypt_private_key(
        wallet_key.encrypted_private_key
    )


def validate_recipient_address(
    to_address: str,
) -> str:
    if not Web3.is_address(to_address):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Ethereum recipient address.",
        )

    return Web3.to_checksum_address(
        to_address
    )


def get_web3() -> Web3:
    if not settings.ETH_RPC_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ETH_RPC_URL is not configured.",
        )

    web3 = Web3(
        Web3.HTTPProvider(
            settings.ETH_RPC_URL
        )
    )

    if not web3.is_connected():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ethereum RPC connection failed.",
        )

    return web3


def create_eth_transaction(
    web3: Web3,
    wallet: Wallet,
    private_key: str,
    to_address: str,
    amount: Decimal,
):
    account = Account.from_key(
        private_key
    )

    sender_address = Web3.to_checksum_address(
        account.address
    )

    wallet_address = Web3.to_checksum_address(
        wallet.address
    )

    if sender_address != wallet_address:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Wallet private key does not match wallet address.",
        )

    amount_wei = Web3.to_wei(
        amount,
        "ether",
    )

    balance_wei = web3.eth.get_balance(
        sender_address
    )

    nonce = web3.eth.get_transaction_count(
        sender_address,
        "pending",
    )

    chain_id = web3.eth.chain_id

    gas_price = web3.eth.gas_price

    gas_limit = 21000

    estimated_gas_cost = (
        gas_price * gas_limit
    )

    if balance_wei < (
        amount_wei + estimated_gas_cost
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient ETH balance for amount and gas.",
        )

    transaction = {
        "nonce": nonce,
        "to": to_address,
        "value": amount_wei,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "chainId": chain_id,
    }

    return transaction


def sign_and_send_eth_transaction(
    web3: Web3,
    private_key: str,
    transaction: dict,
):
    signed_transaction = (
        web3.eth.account.sign_transaction(
            transaction,
            private_key,
        )
    )

    tx_hash = web3.eth.send_raw_transaction(
        signed_transaction.raw_transaction
    )

    return tx_hash.hex()
