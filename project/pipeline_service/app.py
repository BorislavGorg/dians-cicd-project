import os
import threading
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


# Криптовалути што ги повлекува pipeline-от (симбол -> CoinGecko id)
SYMBOL_TO_COINGECKO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "TRX": "tron",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "XLM": "stellar",
    "ATOM": "cosmos",
    "UNI": "uniswap",
    "NEAR": "near",
    "SHIB": "shiba-inu",
    "ETC": "ethereum-classic",
    "QTUM": "qtum",
    "ICX": "icon",
    "TUSD": "true-usd",
}

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
# Дневна историја за 1 година (доволно за LSTM моделот со lookback=30)
HISTORY_DAYS = 365
# CoinGecko враќа дневни OHLC свеќи само за последните 30 дена
OHLC_DAYS = 30
# Бесплатниот CoinGecko API има ограничување на бројот на барања во минута,
# затоа правиме пауза меѓу барањата и повторен обид при 429 (Too Many Requests)
REQUEST_DELAY_SECONDS = 3
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


def to_date(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()


def fetch_symbol_data(symbol, coingecko_id):
    """Повлекува дневни цени, волумен и market cap за една година,
    а за последните 30 дена и вистински OHLC (open/high/low/close) вредности."""

    chart_data = get_json(
        f"{COINGECKO_BASE}/coins/{coingecko_id}/market_chart",
        {"vs_currency": "usd", "days": HISTORY_DAYS, "interval": "daily"},
    )
    ohlc_data = get_json(
        f"{COINGECKO_BASE}/coins/{coingecko_id}/ohlc",
        {"vs_currency": "usd", "days": OHLC_DAYS},
    )

    volumes_by_date = {to_date(ts): v for ts, v in chart_data.get("total_volumes", [])}
    caps_by_date = {to_date(ts): v for ts, v in chart_data.get("market_caps", [])}

    # Основа: дневна цена на затворање од market_chart
    rows_by_date = {}
    prev_close = None
    for ts, price in chart_data.get("prices", []):
        d = to_date(ts)
        open_price = prev_close if prev_close is not None else price
        rows_by_date[d] = {
            "symbol": symbol,
            "date": d,
            "open": open_price,
            "high": max(open_price, price),
            "low": min(open_price, price),
            "close": price,
            "volume": volumes_by_date.get(d),
            "market_cap": caps_by_date.get(d),
        }
        prev_close = price

    # Каде што има OHLC свеќи, ги користиме вистинските open/high/low/close
    candles_by_date = {}
    for ts, o, h, l, c in ohlc_data:
        candles_by_date.setdefault(to_date(ts), []).append((o, h, l, c))
    for d, candles in candles_by_date.items():
        row = rows_by_date.get(d)
        if row is None:
            continue
        row["open"] = candles[0][0]
        row["high"] = max(c[1] for c in candles)
        row["low"] = min(c[2] for c in candles)
        row["close"] = candles[-1][3]

    return list(rows_by_date.values())


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


pipeline_lock = threading.Lock()
last_run = {"status": "never_run"}


def run_pipeline_job():
    """Ги повлекува сите валути една по една и ги запишува во базата."""
    global last_run
    summary = {}
    total_inserted, total_updated = 0, 0
    last_run = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()}

    with app.app_context():
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
            last_run["progress"] = f"{len(summary)}/{len(SYMBOL_TO_COINGECKO_ID)}"

    last_run = {
        "status": "completed",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "total_inserted": total_inserted,
        "total_updated": total_updated,
        "details": summary,
    }


def run_pipeline_in_background():
    if not pipeline_lock.acquire(blocking=False):
        return
    try:
        run_pipeline_job()
    finally:
        pipeline_lock.release()


@app.route("/run-pipeline", methods=["POST"])
def run_pipeline():
    # Повлекувањето трае неколку минути (ограничување на CoinGecko API),
    # затоа се извршува во позадина, а барањето веднаш враќа одговор
    if pipeline_lock.locked():
        return jsonify({"status": "already_running", **last_run}), 202
    threading.Thread(target=run_pipeline_in_background, daemon=True).start()
    return jsonify({"status": "started"}), 202


@app.route("/pipeline-status")
def pipeline_status():
    return jsonify(last_run)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5001)
