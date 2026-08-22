from app.database import Base, engine

from app import models
from app.wallet_models import Wallet
from app.balance_models import Balance
from app.transaction_models import Transaction
from app.wallet_key_models import WalletKey
from app.send_models import SendTransaction
from app.blockchain_state_models import BlockchainState


def init_database():
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_database()
