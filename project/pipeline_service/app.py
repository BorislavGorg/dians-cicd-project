import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:dejvid77@db:5432/dians"
)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Crypto(db.Model):
    __tablename__ = "crypto_database"

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


# Symbols supported by the LSTM models in main_app, mapped to CoinGecko ids
SYMBOL_TO_COINGECKO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "ETC": "ethereum-classic",
    "LTC": "litecoin",
    "ADA": "cardano",
    "ICX": "icon",
    "QTUM": "qtum",
    "TRX": "tron",
    "TUSD": "true-usd",
}

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
HISTORY_DAYS = 30


def fetch_symbol_data(symbol, coingecko_id):
    """Fetch OHLC + volume/market cap data for a given symbol from CoinGecko."""

    ohlc_resp = requests.get(
        f"{COINGECKO_BASE}/coins/{coingecko_id}/ohlc",
        params={"vs_currency": "usd", "days": HISTORY_DAYS},
        timeout=30,
    )
    ohlc_resp.raise_for_status()
    ohlc_data = ohlc_resp.json()

    chart_resp = requests.get(
        f"{COINGECKO_BASE}/coins/{coingecko_id}/market_chart",
        params={"vs_currency": "usd", "days": HISTORY_DAYS, "interval": "daily"},
        timeout=30,
    )
    chart_resp.raise_for_status()
    chart_data = chart_resp.json()

    volumes_by_date = {}
    for ts, vol in chart_data.get("total_volumes", []):
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
        volumes_by_date[d] = vol

    caps_by_date = {}
    for ts, cap in chart_data.get("market_caps", []):
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
        caps_by_date[d] = cap

    rows = []
    for ts, o, h, l, c in ohlc_data:
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
        rows.append({
            "symbol": symbol,
            "date": d,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": volumes_by_date.get(d),
            "market_cap": caps_by_date.get(d),
        })

    return rows


def upsert_rows(rows):
    inserted, updated = 0, 0
    for row in rows:
        existing = Crypto.query.filter_by(symbol=row["symbol"], date=row["date"]).first()
        if existing:
            existing.open = row["open"]
            existing.high = row["high"]
            existing.low = row["low"]
            existing.close = row["close"]
            existing.volume = row["volume"]
            existing.market_cap = row["market_cap"]
            updated += 1
        else:
            db.session.add(Crypto(**row))
            inserted += 1

    db.session.commit()
    return inserted, updated


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/run-pipeline", methods=["POST"])
def run_pipeline():
    summary = {}
    total_inserted, total_updated = 0, 0

    for symbol, coingecko_id in SYMBOL_TO_COINGECKO_ID.items():
        try:
            rows = fetch_symbol_data(symbol, coingecko_id)
            inserted, updated = upsert_rows(rows)
            total_inserted += inserted
            total_updated += updated
            summary[symbol] = {"inserted": inserted, "updated": updated}
        except Exception as exc:
            db.session.rollback()
            summary[symbol] = {"error": str(exc)}

    return jsonify({
        "status": "completed",
        "total_inserted": total_inserted,
        "total_updated": total_updated,
        "details": summary,
    })


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5001, debug=True)
