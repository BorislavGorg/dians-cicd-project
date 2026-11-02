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

    return redirect(url_for("index"))

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

    top_gainer = type("X", (), {"symbol": None, "change": None})
    top_loser = type("X", (), {"symbol": None, "change": None})

    if coins:
        sample_symbols = [c.symbol for c in coins[:80]]
        changes = []

        for sym in sample_symbols:
            last2 = (
                Crypto.query
                .filter_by(symbol=sym)
                .order_by(Crypto.date.desc())
                .limit(2)
                .all()
            )
            if len(last2) == 2 and last2[1].close and last2[0].close:
                prev = last2[1].close
                curr = last2[0].close
                if prev != 0:
                    pct = (curr - prev) / prev * 100.0
                    changes.append((sym, pct))

        if changes:
            changes.sort(key=lambda x: x[1])
            top_loser = type(
                "X", (),
                {"symbol": changes[0][0], "change": round(changes[0][1], 2)}
            )
            top_gainer = type(
                "X", (),
                {"symbol": changes[-1][0], "change": round(changes[-1][1], 2)}
            )

    return render_template(
        "index.html",
        coins=coins,
        symbols=symbols,
        total_market_cap=total_market_cap,
        top_gainer=top_gainer,
        top_loser=top_loser
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

    market_cap = sum((r.market_cap or 0) for r in rows_30) / len(rows_30)
    volume_24h = latest.volume or 0
    circulating_supply = None

    stats_rows = []
    prev_close = None

    for r in reversed(rows_30):
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
        full_name=f"{symbol} Name",
        latest_close=latest.close,
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
        signals=signals,
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
        .limit(30)
        .all()
        if a else []
    )
    rows_b = (
        Crypto.query.filter_by(symbol=b)
        .order_by(Crypto.date.desc())
        .limit(30)
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
        symbols=symbols,
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
        rmse=result["rmse"],
        mape=result["mape"],
        r2=result["r2"],
        plot_data=result["plot_base64"]
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
        forecast_rows=forecast_rows
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
