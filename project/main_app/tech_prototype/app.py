import io
import os

from flask_cors import CORS
import pandas as pd
import requests

from flask import Flask, render_template, request, redirect, url_for, send_file
from sqlalchemy import func

from ext import db
from models import Crypto


from lstm import train_lstm, load_trained_model, forecast_future


app = Flask(__name__)
CORS(app)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:dejvid77@db:5432/dians"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

PIPELINE_SERVICE_URL = os.environ.get("FETCH_MICROSERVICE_URL", "http://pipeline_service:5001")
TECHNICAL_ANALYSIS_SERVICE_URL = os.environ.get(
    "TECHNICAL_ANALYSIS_MICROSERVICE_URL", "http://technical_analysis_service:5002"
)

COIN_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "BNB": "BNB", "SOL": "Solana",
    "XRP": "XRP", "ADA": "Cardano", "DOGE": "Dogecoin", "TRX": "TRON",
    "DOT": "Polkadot", "AVAX": "Avalanche", "LINK": "Chainlink", "LTC": "Litecoin",
    "BCH": "Bitcoin Cash", "XLM": "Stellar", "ATOM": "Cosmos", "UNI": "Uniswap",
    "NEAR": "NEAR Protocol", "SHIB": "Shiba Inu", "ETC": "Ethereum Classic",
    "QTUM": "Qtum", "ICX": "ICON", "TUSD": "TrueUSD",
}


def coin_name(symbol):
    return COIN_NAMES.get(symbol, symbol)


def pct_change(new, old):
    if new is None or old in (None, 0):
        return None
    return (new - old) / old * 100.0


@app.template_filter("price")
def fmt_price(v):
    if v is None:
        return "—"
    v = float(v)
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:,.4f}"
    return f"${v:.8f}".rstrip("0")


@app.template_filter("compact")
def fmt_compact(v, prefix="$"):
    if v is None:
        return "—"
    v = float(v)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"{prefix}{v / div:,.2f}{unit}"
    return f"{prefix}{v:,.2f}"


@app.template_filter("pct")
def fmt_pct(v):
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else ''}{v:.2f}%"


def coin_hue(symbol):
    return sum(ord(ch) * 37 for ch in symbol or "") % 360


@app.context_processor
def inject_helpers():
    return {"coin_name": coin_name, "coin_hue": coin_hue}


# -------------------------------------------------
# PIPELINE TRIGGER (MICROSERVICE CALL)
# -------------------------------------------------

@app.route("/run-fetch", methods=["POST"])
def run_fetch():
    try:
        r = requests.post(f"{PIPELINE_SERVICE_URL}/run-pipeline", timeout=30)
        if r.status_code not in (200, 202):
            print("Pipeline error:", r.text)
    except Exception as e:
        print("Pipeline service not available:", e)

    return redirect(url_for("index", fetching=1))


@app.route("/fetch-status")
def fetch_status():
    try:
        r = requests.get(f"{PIPELINE_SERVICE_URL}/pipeline-status", timeout=5)
        return r.json()
    except Exception:
        return {"status": "unavailable"}


@app.route("/")
def index():
    symbol = request.args.get("symbol")
    if symbol:
        return redirect(url_for("coin_detail") + f"?symbol={symbol}")

    subq = (
        db.session.query(
            Crypto.symbol.label("symbol"),
            func.max(Crypto.date).label("max_date")
        )
        .group_by(Crypto.symbol)
        .subquery()
    )

    coins = (
        db.session.query(Crypto)
        .join(
            subq,
            (Crypto.symbol == subq.c.symbol) &
            (Crypto.date == subq.c.max_date)
        )
        .order_by(Crypto.market_cap.desc().nullslast(), Crypto.symbol.asc())
        .limit(200)
        .all()
    )

    symbols = [
        r[0] for r in
        db.session.query(Crypto.symbol)
        .distinct()
        .order_by(Crypto.symbol.asc())
        .all()
    ]

    total_market_cap = sum((c.market_cap or 0) for c in coins)
    total_volume = sum((c.volume or 0) for c in coins)

    # 24h промена и мини-график (последни 30 дена) за секоја валута
    rows_view = []
    for c in coins:
        last30 = (
            Crypto.query
            .filter_by(symbol=c.symbol)
            .order_by(Crypto.date.desc())
            .limit(30)
            .all()
        )
        closes = [r.close for r in reversed(last30) if r.close is not None]
        change_24h = pct_change(last30[0].close, last30[1].close) if len(last30) > 1 else None
        change_7d = pct_change(last30[0].close, last30[7].close) if len(last30) > 7 else None
        rows_view.append({
            "symbol": c.symbol,
            "name": coin_name(c.symbol),
            "price": c.close,
            "change_24h": change_24h,
            "change_7d": change_7d,
            "volume": c.volume,
            "market_cap": c.market_cap,
            "spark": closes,
        })

    with_change = [r for r in rows_view if r["change_24h"] is not None]
    top_gainer = max(with_change, key=lambda r: r["change_24h"]) if with_change else None
    top_loser = min(with_change, key=lambda r: r["change_24h"]) if with_change else None
    last_update = max((c.date for c in coins), default=None)

    return render_template(
        "index.html",
        coins=rows_view,
        symbols=symbols,
        total_market_cap=total_market_cap,
        total_volume=total_volume,
        top_gainer=top_gainer,
        top_loser=top_loser,
        last_update=last_update,
        fetching=request.args.get("fetching") == "1",
    )


@app.route("/coin")
def coin_detail():
    symbol = request.args.get("symbol")
    if not symbol:
        return redirect(url_for("index"))

    rows_30 = (
        Crypto.query
        .filter_by(symbol=symbol)
        .order_by(Crypto.date.desc())
        .limit(30)
        .all()
    )

    rows_365 = (
        Crypto.query
        .filter_by(symbol=symbol)
        .order_by(Crypto.date.desc())
        .limit(365)
        .all()
    )

    if not rows_30:
        return redirect(url_for("index"))

    latest = rows_30[0]

    market_cap = latest.market_cap
    volume_24h = latest.volume or 0
    circulating_supply = None

    stats_rows = []
    prev_close = None

    for r in reversed(rows_30):  # noqa
        change_pct = None
        if prev_close not in (None, 0) and r.close is not None:
            change_pct = (r.close - prev_close) / prev_close * 100.0

        stats_rows.append({
            "date": r.date.strftime("%Y-%m-%d"),
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "market_cap": r.market_cap,
            "change_pct": change_pct
        })
        prev_close = r.close

    rows_365_sorted = list(reversed(rows_365))
    chart_labels = [r.date.strftime("%Y-%m-%d") for r in rows_365_sorted]
    chart_close = [float(r.close) if r.close is not None else None for r in rows_365_sorted]
    chart_volume = [float(r.volume) if r.volume is not None else None for r in rows_365_sorted]

    return render_template(
        "coinDetails.html",
        symbol=symbol,
        full_name=coin_name(symbol),
        latest_close=latest.close,
        change_24h=pct_change(rows_30[0].close, rows_30[1].close) if len(rows_30) > 1 else None,
        change_7d=pct_change(rows_30[0].close, rows_30[7].close) if len(rows_30) > 7 else None,
        change_30d=pct_change(rows_30[0].close, rows_30[-1].close) if len(rows_30) > 1 else None,
        high_365=max((r.high for r in rows_365 if r.high is not None), default=None),
        low_365=min((r.low for r in rows_365 if r.low is not None), default=None),
        last_date=latest.date.strftime("%Y-%m-%d"),
        rows=rows_30,
        market_cap=market_cap,
        volume_24h=volume_24h,
        circulating_supply=circulating_supply,
        stats_rows=stats_rows,
        chart_labels=chart_labels,
        chart_close=chart_close,
        chart_volume=chart_volume
    )


@app.route("/analysis")
def analysis_page():
    symbol = request.args.get("symbol")
    if not symbol:
        return redirect(url_for("index"))

    rows = (
        Crypto.query
        .filter_by(symbol=symbol)
        .order_by(Crypto.date)
        .all()
    )

    if not rows:
        return "No data", 404

    df = pd.DataFrame([{
        "date": r.date,
        "open": r.open,
        "high": r.high,
        "low": r.low,
        "close": r.close,
        "volume": r.volume
    } for r in rows])

    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)

    # -------- TIMEFRAMES --------
    daily_df = df.copy()
    weekly_df = df.resample("W").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }).dropna()

    monthly_df = df.resample("ME").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }).dropna()

    def call_tech_service(frame_df, timeframe):
        if frame_df.empty:
            return {"signal": "HOLD", "score": 0.0}

        payload = frame_df.reset_index()[[
            "open", "high", "low", "close", "volume"
        ]].to_dict(orient="records")

        r = requests.post(
            f"{TECHNICAL_ANALYSIS_SERVICE_URL}/technical-analysis?timeframe={timeframe}",
            json=payload,
            timeout=30
        )
        return r.json()

    daily_result = call_tech_service(daily_df, "daily")
    weekly_result = call_tech_service(weekly_df, "weekly")
    monthly_result = call_tech_service(monthly_df, "monthly")

    signals = {
        "daily": {
            "signal": daily_result.get("signal", "HOLD"),
            "score": daily_result.get("score", 0.0)
        },
        "weekly": {
            "signal": weekly_result.get("signal", "HOLD"),
            "score": weekly_result.get("score", 0.0)
        },
        "monthly": {
            "signal": monthly_result.get("signal", "HOLD"),
            "score": monthly_result.get("score", 0.0)
        }
    }

    return render_template(
        "analysis.html",
        symbol=symbol,
        full_name=coin_name(symbol),
        signals=signals,
        latest_close=float(daily_df["close"].iloc[-1]),
        chart_labels=[d.strftime("%Y-%m-%d") for d in daily_df.tail(120).index],
        chart_close=[float(v) for v in daily_df["close"].tail(120)],
        table=daily_df.tail(50).to_html(classes="table table-striped")
    )


@app.route("/export")
def export_csv():
    symbol = request.args.get("symbol")
    q = Crypto.query
    if symbol:
        q = q.filter_by(symbol=symbol)

    rows = q.order_by(Crypto.symbol.asc(), Crypto.date.asc()).all()
    if not rows:
        return "No data", 404

    df = pd.DataFrame([{
        "symbol": r.symbol,
        "date": r.date.strftime("%Y-%m-%d"),
        "open": r.open,
        "high": r.high,
        "low": r.low,
        "close": r.close,
        "volume": r.volume,
        "market_cap": r.market_cap
    } for r in rows])

    buf = io.StringIO()
    df.to_csv(buf, index=False)
    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    mem.seek(0)

    filename = f"{symbol}_data.csv" if symbol else "all_crypto_data.csv"
    return send_file(
        mem,
        as_attachment=True,
        download_name=filename,
        mimetype="text/csv"
    )


@app.route("/compare")
def compare():
    symbols = [r[0] for r in db.session.query(Crypto.symbol).distinct().all()]
    a = request.args.get("a") or (symbols[0] if symbols else None)
    b = request.args.get("b") or (symbols[1] if len(symbols) > 1 else a)

    rows_a = (
        Crypto.query.filter_by(symbol=a)
        .order_by(Crypto.date.desc())
        .limit(90)
        .all()
        if a else []
    )
    rows_b = (
        Crypto.query.filter_by(symbol=b)
        .order_by(Crypto.date.desc())
        .limit(90)
        .all()
        if b else []
    )

    paired = []
    for i in range(max(len(rows_a), len(rows_b))):
        date_val = rows_a[i].date if i < len(rows_a) else rows_b[i].date
        paired.append({
            "date": date_val.strftime("%Y-%m-%d"),
            "a": rows_a[i].close if i < len(rows_a) else None,
            "b": rows_b[i].close if i < len(rows_b) else None
        })

    return render_template(
        "compare.html",
        symbols=sorted(symbols),
        a=a,
        b=b,
        rows_a=rows_a,
        rows_b=rows_b,
        paired=paired
    )


@app.route("/symbols")
def symbols_page():
    symbols = [
        r[0] for r in
        db.session.query(Crypto.symbol)
        .distinct()
        .order_by(Crypto.symbol.asc())
        .all()
    ]
    return render_template("symbols.html", symbols=symbols)


@app.route("/lstm_train")
def lstm_train_route():
    symbol = request.args.get("symbol")
    if not symbol:
        return redirect(url_for("index"))

    rows = (
        Crypto.query
        .filter_by(symbol=symbol)
        .order_by(Crypto.date)
        .all()
    )

    if not rows:
        return "No data", 404

    df = pd.DataFrame([{
        "date": r.date,
        "close": r.close
    } for r in rows])

    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)

    result = train_lstm(df, model_name=symbol)

    return render_template(
        "lstm.html",
        symbol=symbol,
        full_name=coin_name(symbol),
        rmse=result["rmse"],
        mape=result["mape"],
        r2=result["r2"],
        plot_data=result["plot_base64"],
        y_true=result["y_true"],
        y_pred=result["y_pred"],
        test_dates=result["test_dates"],
    )


@app.route("/lstm_forecast")
def lstm_forecast():
    symbol = request.args.get("symbol")
    if not symbol:
        return redirect(url_for("index"))

    rows = (
        Crypto.query
        .filter_by(symbol=symbol)
        .order_by(Crypto.date)
        .all()
    )

    if not rows:
        return "No data", 404

    df = pd.DataFrame([{
        "date": r.date,
        "close": r.close
    } for r in rows])

    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)

    model, scaler = load_trained_model(symbol)
    if model is None or scaler is None:
        return redirect(url_for("lstm_train_route", symbol=symbol))

    forecast_rows = forecast_future(df, model, scaler, days=7)

    return render_template(
        "lstm_forecast.html",
        symbol=symbol,
        full_name=coin_name(symbol),
        forecast_rows=forecast_rows,
        last_close=float(df["close"].iloc[-1]),
        history_labels=[d.strftime("%Y-%m-%d") for d in df.tail(30).index],
        history_close=[float(v) for v in df["close"].tail(30)],
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
