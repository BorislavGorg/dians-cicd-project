import os
import threading
import time
from datetime import datetime, timedelta, timezone

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
    """Дневни податоци (OHLC, волумен, market cap) за секоја валута."""
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


class CoinInfo(db.Model):
    """Метаподатоци за валутата: име, лого и ранг по market cap."""
    __tablename__ = "coin_info"

    symbol = db.Column(db.String, primary_key=True)
    coingecko_id = db.Column(db.String)
    name = db.Column(db.String)
    image = db.Column(db.String)
    market_cap_rank = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime)


# ---------------------------------------------------------------
# Конфигурација
# ---------------------------------------------------------------
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
# Колку валути (по market cap) да се повлечат
COINS_LIMIT = int(os.environ.get("COINS_LIMIT", "100"))
# Дневна историја за 1 година (доволно за LSTM моделот со lookback=30)
HISTORY_DAYS = 365
# CoinGecko враќа дневни OHLC свеќи само за последните 30 дена;
# тие се повлекуваат само за најголемите валути за да се штедат барања
OHLC_DAYS = 30
OHLC_TOP_N = int(os.environ.get("OHLC_TOP_N", "25"))
# Опционален бесплатен "demo" API клуч - овозможува повеќе барања во минута
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "").strip()

# Бесплатниот CoinGecko API има ограничување на бројот на барања во минута,
# затоа правиме пауза меѓу барањата и повторен обид при 429 (Too Many Requests)
REQUEST_DELAY_SECONDS = float(os.environ.get("REQUEST_DELAY_SECONDS", "2.5" if COINGECKO_API_KEY else "4"))
RATE_LIMIT_WAIT_SECONDS = 30
MAX_RETRIES = 6

# Резервна листа ако повикот за топ валути не успее
FALLBACK_COINS = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "TRX": "tron",
    "DOT": "polkadot", "AVAX": "avalanche-2", "LINK": "chainlink", "LTC": "litecoin",
    "BCH": "bitcoin-cash", "XLM": "stellar", "ATOM": "cosmos", "UNI": "uniswap",
    "NEAR": "near", "SHIB": "shiba-inu", "ETC": "ethereum-classic",
}


def get_json(url, params):
    headers = {"accept": "application/json"}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    for attempt in range(MAX_RETRIES):
        time.sleep(REQUEST_DELAY_SECONDS)
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
            wait = resp.headers.get("Retry-After")
            wait = int(wait) if wait and wait.isdigit() else RATE_LIMIT_WAIT_SECONDS
            last_run["note"] = f"CoinGecko rate limit – waiting {wait}s"
            time.sleep(min(wait, 90))
            last_run.pop("note", None)
            continue
        resp.raise_for_status()
        return resp.json()


def to_date(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()


def fetch_top_coins(limit):
    """Ги враќа најголемите валути по market cap (една страница = до 250)."""
    coins, page = [], 1
    while len(coins) < limit:
        per_page = min(250, limit - len(coins))
        data = get_json(f"{COINGECKO_BASE}/coins/markets", {
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": per_page, "page": page, "sparkline": "false",
        })
        if not data:
            break
        coins.extend(data)
        page += 1
    return coins[:limit]


def save_coin_info(markets):
    """Ги зачувува имињата и логоата; првиот (најголем) добива симбол ако се повторува."""
    seen = {}
    for c in markets:
        sym = (c.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen[sym] = c
        info = db.session.get(CoinInfo, sym) or CoinInfo(symbol=sym)
        info.coingecko_id = c.get("id")
        info.name = c.get("name")
        info.image = c.get("image")
        info.market_cap_rank = c.get("market_cap_rank")
        info.updated_at = datetime.now(timezone.utc)
        db.session.merge(info)
    db.session.commit()
    return {sym: c["id"] for sym, c in seen.items()}


def fetch_symbol_data(symbol, coingecko_id, days, with_ohlc):
    """Повлекува дневни цени, волумен и market cap; за најголемите валути
    и вистински OHLC (open/high/low/close) вредности за последните 30 дена."""

    chart_data = get_json(
        f"{COINGECKO_BASE}/coins/{coingecko_id}/market_chart",
        {"vs_currency": "usd", "days": days, "interval": "daily"},
    )

    volumes_by_date = {to_date(ts): v for ts, v in chart_data.get("total_volumes", [])}
    caps_by_date = {to_date(ts): v for ts, v in chart_data.get("market_caps", [])}

    # Основа: дневна цена на затворање од market_chart
    rows_by_date = {}
    prev_close = None
    for ts, price in chart_data.get("prices", []):
        if price is None:
            continue
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
    if with_ohlc:
        ohlc_data = get_json(
            f"{COINGECKO_BASE}/coins/{coingecko_id}/ohlc",
            {"vs_currency": "usd", "days": OHLC_DAYS},
        )
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


def upsert_rows(symbol, rows):
    """Вметнува нови и ажурира постоечки редови (еден SELECT по валута)."""
    if not rows:
        return 0, 0
    dates = [r["date"] for r in rows]
    existing = {
        r.date: r for r in Crypto.query
        .filter(Crypto.symbol == symbol, Crypto.date >= min(dates))
        .all()
    }
    inserted, updated = 0, 0
    for row in rows:
        current = existing.get(row["date"])
        if current:
            for key in ("open", "high", "low", "close", "volume", "market_cap"):
                setattr(current, key, row[key])
            updated += 1
        else:
            db.session.add(Crypto(**row))
            inserted += 1
    db.session.commit()
    return inserted, updated


def days_to_fetch(symbol, today):
    """Колку денови назад треба да се повлечат (инкрементално ажурирање)."""
    latest = db.session.query(db.func.max(Crypto.date)).filter(Crypto.symbol == symbol).scalar()
    if latest is None:
        return HISTORY_DAYS
    gap = (today - latest).days
    if gap <= 0:
        return 0  # веќе е ажурно денес
    return min(HISTORY_DAYS, gap + 2)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


pipeline_lock = threading.Lock()
last_run = {"status": "never_run"}


def run_pipeline_job():
    """Ги повлекува најголемите валути една по една и ги запишува во базата."""
    global last_run
    summary = {}
    total_inserted, total_updated = 0, 0
    last_run = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "progress": "0/?"}

    with app.app_context():
        try:
            markets = fetch_top_coins(COINS_LIMIT)
            coins = save_coin_info(markets)
        except Exception as exc:
            db.session.rollback()
            summary["_markets"] = {"error": str(exc)}
            coins = dict(FALLBACK_COINS)

        today = datetime.now(timezone.utc).date()
        total = len(coins)
        for i, (symbol, coingecko_id) in enumerate(coins.items()):
            last_run["progress"] = f"{i}/{total}"
            last_run["current"] = symbol
            try:
                days = days_to_fetch(symbol, today)
                if days == 0:
                    summary[symbol] = {"skipped": "up to date"}
                    continue
                rows = fetch_symbol_data(symbol, coingecko_id, days, with_ohlc=i < OHLC_TOP_N)
                inserted, updated = upsert_rows(symbol, rows)
                total_inserted += inserted
                total_updated += updated
                summary[symbol] = {"inserted": inserted, "updated": updated}
            except Exception as exc:
                db.session.rollback()
                summary[symbol] = {"error": str(exc)}
        last_run["progress"] = f"{total}/{total}"

    last_run = {
        "status": "completed",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "coins": len(summary),
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
