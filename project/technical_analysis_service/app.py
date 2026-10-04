import pandas as pd
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def compute_sma(series, period):
    return series.rolling(window=period, min_periods=period).mean()


def analyze(df):
    """Return a (signal, score) tuple based on SMA crossover, RSI and MACD."""

    close = df["close"]
    n = len(close)

    if n < 2:
        return "HOLD", 0.0

    score = 0.0
    votes = 0

    # --- SMA crossover (trend) ---
    short_period = min(7, max(2, n // 4))
    long_period = min(25, max(short_period + 1, n // 2))

    if n >= long_period:
        sma_short = compute_sma(close, short_period)
        sma_long = compute_sma(close, long_period)
        if pd.notna(sma_short.iloc[-1]) and pd.notna(sma_long.iloc[-1]):
            diff = (sma_short.iloc[-1] - sma_long.iloc[-1]) / sma_long.iloc[-1]
            score += max(min(diff * 10, 1), -1)
            votes += 1

    # --- RSI (momentum) ---
    rsi_period = min(14, max(2, n - 1))
    rsi = compute_rsi(close, period=rsi_period)
    last_rsi = rsi.iloc[-1]
    if pd.notna(last_rsi):
        if last_rsi >= 70:
            rsi_score = -1.0
        elif last_rsi <= 30:
            rsi_score = 1.0
        else:
            rsi_score = (50 - last_rsi) / 20.0
        score += max(min(rsi_score, 1), -1)
        votes += 1

    # --- MACD (momentum/trend confirmation) ---
    if n >= 2:
        macd_line, signal_line = compute_macd(close)
        macd_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        denom = abs(close.iloc[-1]) if close.iloc[-1] else 1
        macd_score = max(min((macd_diff / denom) * 50, 1), -1)
        if prev_diff <= 0 < macd_diff:
            macd_score = max(macd_score, 0.5)
        elif prev_diff >= 0 > macd_diff:
            macd_score = min(macd_score, -0.5)
        score += macd_score
        votes += 1

    final_score = score / votes if votes else 0.0
    final_score = round(max(min(final_score, 1), -1), 4)

    if final_score >= 0.2:
        signal = "BUY"
    elif final_score <= -0.2:
        signal = "SELL"
    else:
        signal = "HOLD"

    return signal, final_score


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/technical-analysis", methods=["POST"])
def technical_analysis():
    timeframe = request.args.get("timeframe", "daily")
    payload = request.get_json(silent=True)

    if not payload:
        return jsonify({"signal": "HOLD", "score": 0.0, "timeframe": timeframe})

    df = pd.DataFrame(payload)
    required_cols = {"open", "high", "low", "close", "volume"}
    if not required_cols.issubset(df.columns) or df.empty:
        return jsonify({"signal": "HOLD", "score": 0.0, "timeframe": timeframe})

    df = df.dropna(subset=["close"])
    if df.empty:
        return jsonify({"signal": "HOLD", "score": 0.0, "timeframe": timeframe})

    signal, score = analyze(df)

    return jsonify({"signal": signal, "score": score, "timeframe": timeframe})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
