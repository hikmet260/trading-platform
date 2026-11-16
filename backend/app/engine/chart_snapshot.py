from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw


def render_candlestick_snapshot(candles: list[dict[str, Any]], symbol: str, timeframe: str) -> BytesIO:
    """Render a compact PNG chart from validated candle values without network access."""
    rows = candles[-72:]
    if not rows:
        raise ValueError("At least one candle is required to render a chart")
    width, height = 960, 480
    image = Image.new("RGB", (width, height), "#0b111a")
    draw = ImageDraw.Draw(image)
    values = [float(row[key]) for row in rows for key in ("low", "high")]
    low, high = min(values), max(values)
    if not all(value > 0 for value in values):
        raise ValueError("Candles must have positive prices")
    span = high - low or max(abs(high) * 0.001, 1e-8)
    top, bottom, left, right = 58, height - 42, 64, width - 24
    draw.text((left, 20), f"{symbol} · {timeframe} · paper setup", fill="#d8e3f0")
    for index in range(5):
        y = top + index * (bottom - top) / 4
        draw.line((left, y, right, y), fill="#202b39", width=1)
        price = high - span * index / 4
        draw.text((8, y - 7), f"{price:.6g}", fill="#8292a7")
    step = (right - left) / len(rows)
    body_width = max(3, int(step * 0.58))
    for index, candle in enumerate(rows):
        opening, closing = float(candle["open"]), float(candle["close"])
        candle_high, candle_low = float(candle["high"]), float(candle["low"])
        x = left + (index + 0.5) * step
        y_high = bottom - (candle_high - low) / span * (bottom - top)
        y_low = bottom - (candle_low - low) / span * (bottom - top)
        y_open = bottom - (opening - low) / span * (bottom - top)
        y_close = bottom - (closing - low) / span * (bottom - top)
        color = "#22c55e" if closing >= opening else "#ef4444"
        draw.line((x, y_high, x, y_low), fill=color, width=2)
        draw.rectangle((x - body_width / 2, min(y_open, y_close), x + body_width / 2,
                        max(y_open, y_close, min(y_open, y_close) + 1)), fill=color)
    draw.text((left, height - 25), f"{len(rows)} recent candles", fill="#8292a7")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
