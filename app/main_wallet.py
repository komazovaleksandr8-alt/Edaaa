from cryptography.fernet import (
    Fernet,
    InvalidToken,
)

from web3 import Web3
from eth_account import Account

from app.config import settings


def get_wallet_fernet() -> Fernet:
    key = settings.WALLET_ENCRYPTION_KEY

    if not key:
        raise RuntimeError(
            "WALLET_ENCRYPTION_KEY is not configured."
        )

    try:
        return Fernet(
            key.encode()
        )

    except Exception as exc:
        raise RuntimeError(
            "Invalid WALLET_ENCRYPTION_KEY."
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
        raise RuntimeError(
            "Failed to decrypt wallet private key."
        ) from exc
