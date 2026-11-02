import os
import io
import base64
import numpy as np
import pandas as pd

from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, r2_score

from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping

import joblib
import matplotlib.pyplot as plt
import tensorflow as tf


MODEL_DIR = "models_lstm"
os.makedirs(MODEL_DIR, exist_ok=True)


def create_sequences(values, lookback):
    X, y = [], []
    for i in range(len(values) - lookback):
        X.append(values[i:i + lookback])
        y.append(values[i + lookback])
    return np.array(X), np.array(y)


def build_lstm(input_shape, units=50, dropout=0.2):
    model = Sequential()
    model.add(LSTM(units, input_shape=input_shape, return_sequences=False))
    model.add(Dropout(dropout))
    model.add(Dense(1))

    model.compile(
        optimizer=tf.keras.optimizers.Adam(),
        loss=tf.keras.losses.MeanSquaredError(),
        metrics=[tf.keras.metrics.MeanSquaredError()]
    )
    return model


def train_lstm(
    df,
    feature_col="close",
    lookback=30,
    test_size=0.3,
    epochs=20,
    batch_size=32,
    patience=5,
    model_name="model"
):
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("DataFrame index must be DatetimeIndex")

    series = df[[feature_col]].astype(float)

    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(series)

    X, y = create_sequences(scaled, lookback)

    split_idx = int(len(X) * (1 - test_size))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    X_train = X_train.reshape((X_train.shape[0], X_train.shape[1], 1))
    X_test = X_test.reshape((X_test.shape[0], X_test.shape[1], 1))

    model = build_lstm((lookback, 1))

    es = EarlyStopping(
        monitor="val_loss",
        patience=patience,
        restore_best_weights=True,
        verbose=1
    )

    history = model.fit(
        X_train, y_train,
        validation_data=(X_test, y_test),
        epochs=epochs,
        batch_size=batch_size,
        callbacks=[es],
        verbose=1
    )

    y_pred = model.predict(X_test)

    y_test_inv = scaler.inverse_transform(y_test.reshape(-1, 1))
    y_pred_inv = scaler.inverse_transform(y_pred)

    mse = mean_squared_error(y_test_inv, y_pred_inv)
    rmse = np.sqrt(mse)
    mape = np.mean(np.abs((y_test_inv - y_pred_inv) / y_test_inv)) * 100
    r2 = r2_score(y_test_inv, y_pred_inv)

    model_path = os.path.join(MODEL_DIR, f"{model_name}.h5")
    scaler_path = os.path.join(MODEL_DIR, f"{model_name}_scaler.save")

    model.save(model_path)
    joblib.dump(scaler, scaler_path)

    img_uri = plot_pred(y_test_inv.flatten(), y_pred_inv.flatten())

    return {
        "model_path": model_path,
        "scaler_path": scaler_path,
        "rmse": float(rmse),
        "mape": float(mape),
        "r2": float(r2),
        "history": history.history,
        "plot_base64": img_uri,
        "y_true": [float(v) for v in y_test_inv.flatten()],
        "y_pred": [float(v) for v in y_pred_inv.flatten()],
        "test_dates": [d.strftime("%Y-%m-%d") for d in df.index[-len(y_test_inv):]],
    }


def plot_pred(y_true, y_pred):
    plt.figure(figsize=(10, 4))
    plt.plot(y_true)
    plt.plot(y_pred)
    plt.legend(["Actual", "Predicted"])
    plt.title("LSTM: Actual vs Predicted")

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png")
    plt.close()

    buf.seek(0)
    img_bytes = buf.getvalue()
    b64 = base64.b64encode(img_bytes).decode("ascii")
    return "data:image/png;base64," + b64


def load_trained_model(model_name):
    model_path = os.path.join(MODEL_DIR, f"{model_name}.h5")
    scaler_path = os.path.join(MODEL_DIR, f"{model_name}_scaler.save")

    if not os.path.exists(model_path) or not os.path.exists(scaler_path):
        return None, None

    model = load_model(model_path)
    scaler = joblib.load(scaler_path)
    return model, scaler


def forecast_future(df, model, scaler, lookback=30, days=7, feature_col="close"):
    series = df[[feature_col]].astype(float)
    scaled = scaler.transform(series)

    last_seq = scaled[-lookback:].reshape(1, lookback, 1)
    preds = []

    for _ in range(days):
        p = model.predict(last_seq, verbose=0)
        preds.append(p[0, 0])
        last_seq = np.roll(last_seq, -1, axis=1)
        last_seq[0, -1, 0] = p[0, 0]

    preds_inv = scaler.inverse_transform(
        np.array(preds).reshape(-1, 1)
    ).flatten()

    last_date = df.index[-1]
    forecast_dates = pd.date_range(
        start=last_date + pd.Timedelta(days=1),
        periods=days,
        freq="D"
    )

    forecast_rows = list(zip(
        forecast_dates.strftime("%Y-%m-%d"),
        preds_inv.tolist()
    ))

    return forecast_rows

