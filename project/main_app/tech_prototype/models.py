import os

from ext import db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

class Crypto(db.Model):
    __tablename__ = 'crypto_database'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String, index=True)
    date = db.Column(db.Date, index=True)
    open = db.Column(db.Float)
    high = db.Column(db.Float)
    low = db.Column(db.Float)
    close = db.Column(db.Float)
    volume = db.Column(db.Float)
    market_cap = db.Column(db.Float)
    favorite = db.Column(db.Boolean, default=False)


Base = db.Model

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:dejvid77@db:5432/dians"
)
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

if __name__ == "__main__":
    db.Model.metadata.create_all(engine)
