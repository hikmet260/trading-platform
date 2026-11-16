from __future__ import annotations

from typing import Any

import pandas as pd
import numpy as np


def calculate_indicators(candles: list[dict[str, Any]]) -> dict[str, list[float | None]]:
    """Return aligned RSI, EMA, SMA, MACD, and Bollinger values for OHLCV rows."""
    if len(candles) < 2:
        raise ValueError("At least two candles are required")
    frame = pd.DataFrame(candles)
    required = {"timestamp", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Candles must contain: {', '.join(sorted(required))}")
    ohlc = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    if ohlc.isna().any().any() or not np.isfinite(ohlc.to_numpy()).all() or (ohlc <= 0).any().any():
        raise ValueError("Candle OHLC values must be finite positive numbers")
    if ((ohlc["high"] < ohlc[["open", "close"]].max(axis=1)) |
            (ohlc["low"] > ohlc[["open", "close"]].min(axis=1)) |
            (ohlc["high"] < ohlc["low"])).any():
        raise ValueError("Candle high/low values do not contain the open and close")
    prices = ohlc["close"]
    changes = prices.diff()
    gains = changes.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    losses = (-changes.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gains / losses.replace(0, float("nan"))
    rsi_values = (100 - 100 / (1 + rs)).mask((losses == 0) & (gains > 0), 100).mask((losses == 0) & (gains == 0), 50)
    middle = prices.rolling(20, min_periods=20).mean()
    deviation = prices.rolling(20, min_periods=20).std(ddof=0)
    macd_line = prices.ewm(span=12, adjust=False, min_periods=12).mean() - prices.ewm(span=26, adjust=False, min_periods=26).mean()
    macd_signal = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()

    def values(series: pd.Series) -> list[float | None]:
        return [None if pd.isna(value) else float(value) for value in series]

    return {
        "rsi_14": values(rsi_values),
        "ema_9": values(prices.ewm(span=9, adjust=False, min_periods=9).mean()),
        "ema_21": values(prices.ewm(span=21, adjust=False, min_periods=21).mean()),
        "sma_20": values(middle),
        "macd": values(macd_line),
        "macd_signal": values(macd_signal),
        "macd_histogram": values(macd_line - macd_signal),
        "bollinger_upper": values(middle + 2 * deviation),
        "bollinger_middle": values(middle),
        "bollinger_lower": values(middle - 2 * deviation),
    }
