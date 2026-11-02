import os
import time
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
# Бесплатниот CoinGecko API има ограничување на бројот на барања во минута,
# затоа правиме пауза меѓу барањата и повторен обид при 429 (Too Many Requests)
REQUEST_DELAY_SECONDS = 2
RATE_LIMIT_WAIT_SECONDS = 20
MAX_RETRIES = 4


def get_json(url, params):
    for attempt in range(MAX_RETRIES):
        time.sleep(REQUEST_DELAY_SECONDS)
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            continue
        resp.raise_for_status()
        return resp.json()


def fetch_symbol_data(symbol, coingecko_id):
    """Fetch OHLC + volume/market cap data for a given symbol from CoinGecko."""

    ohlc_data = get_json(
        f"{COINGECKO_BASE}/coins/{coingecko_id}/ohlc",
        {"vs_currency": "usd", "days": HISTORY_DAYS},
    )

    chart_data = get_json(
        f"{COINGECKO_BASE}/coins/{coingecko_id}/market_chart",
        {"vs_currency": "usd", "days": HISTORY_DAYS, "interval": "daily"},
    )

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
