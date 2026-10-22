"""Technical indicators calculation"""

import numpy as np
import pandas as pd


def calc_indicators(candles: list) -> dict:
    """
    Calculate technical indicators from OHLCV candles.
    candles: list of [timestamp, open, high, low, close, volume]
    Returns dict with indicator values.
    """
    if not candles or len(candles) < 5:
        return {}

    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])

    result = {}

    # Current price
    result["current_price"] = float(df["close"].iloc[-1])

    # Bollinger Bands (20-period, 2 std)
    window = min(20, len(df))
    sma = df["close"].rolling(window=window).mean()
    std = df["close"].rolling(window=window).std()
    result["bb_upper"] = round(float(sma.iloc[-1] + 2 * std.iloc[-1]), 4)
    result["bb_middle"] = round(float(sma.iloc[-1]), 4)
    result["bb_lower"] = round(float(sma.iloc[-1] - 2 * std.iloc[-1]), 4)

    # Bollinger Band position: (price - lower) / (upper - lower) * 100
    bb_range = result["bb_upper"] - result["bb_lower"]
    if bb_range > 0:
        result["bb_position"] = round((result["current_price"] - result["bb_lower"]) / bb_range * 100, 2)
    else:
        result["bb_position"] = 50.0

    # RSI (14-period)
    rsi_period = min(14, len(df) - 1)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    result["rsi"] = round(float(rsi.iloc[-1]), 2) if not np.isnan(rsi.iloc[-1]) else 50.0

    # MACD (12, 26, 9)
    if len(df) >= 12:
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=min(26, len(df)), adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line
        result["macd"] = round(float(macd_line.iloc[-1]), 4)
        result["macd_signal"] = round(float(signal_line.iloc[-1]), 4)
        result["macd_histogram"] = round(float(histogram.iloc[-1]), 4)
    else:
        result["macd"] = 0.0
        result["macd_signal"] = 0.0
        result["macd_histogram"] = 0.0

    # Volume
    result["avg_volume"] = round(float(df["volume"].mean()), 2)
    result["last_volume"] = round(float(df["volume"].iloc[-1]), 2)

    # Volume change ratio: (last_volume - avg_volume) / avg_volume
    if result["avg_volume"] > 0:
        result["volume_change"] = round(
            (result["last_volume"] - result["avg_volume"]) / result["avg_volume"] * 100, 2
        )
    else:
        result["volume_change"] = 0.0

    # Upper shadow ratio: (high - max(open, close)) / (high - low)
    last = df.iloc[-1]
    hl_range = float(last["high"] - last["low"])
    if hl_range > 0:
        upper_shadow = float(last["high"] - max(last["open"], last["close"]))
        result["upper_shadow_ratio"] = round(upper_shadow / hl_range * 100, 2)
    else:
        upper_shadow = 0.0
        result["upper_shadow_ratio"] = 0.0

    # Upper shadow / body ratio (for long upper shadow detection, >2 = long upper shadow)
    body = abs(float(last["close"] - last["open"]))
    if body > 0:
        result["upper_shadow_body_ratio"] = round(upper_shadow / body, 2)
    else:
        result["upper_shadow_body_ratio"] = 99.0 if upper_shadow > 0 else 0.0

    # Volume trend of last 3 candles
    if len(df) >= 3:
        v1, v2, v3 = float(df["volume"].iloc[-3]), float(df["volume"].iloc[-2]), float(df["volume"].iloc[-1])
        if v3 > v2 > v1:
            result["volume_trend"] = "放量"
        elif v3 < v2 < v1:
            result["volume_trend"] = "缩量"
        else:
            result["volume_trend"] = "持平"
    else:
        result["volume_trend"] = "数据不足"

    return result


def format_klines(candles: list) -> str:
    """Format OHLCV candles as a readable table string for AI prompt."""
    if not candles:
        return "无K线数据"

    lines = ["时间 | 开 | 高 | 低 | 收 | 量"]
    lines.append("-" * 60)
    for c in candles[-20:]:
        ts = pd.Timestamp(c[0], unit="ms").strftime("%m-%d %H:%M")
        lines.append(f"{ts} | {c[1]:.2f} | {c[2]:.2f} | {c[3]:.2f} | {c[4]:.2f} | {c[5]:.2f}")
    return "\n".join(lines)
