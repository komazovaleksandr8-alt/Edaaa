from app.database import Base, engine
from app import models
from app.wallet_models import Wallet


def init_database():
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_database()
