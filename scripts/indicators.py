"""日线指标。缺失数据保持 NaN，调用方把规则判成 null 而不是 false。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def wilder(values: np.ndarray, n: int) -> np.ndarray:
    """Wilder 平滑。前 n 个有效值做种子均值，之后 (prev*(n-1)+x)/n。"""
    arr = np.asarray(values, dtype=float)
    out = np.full(len(arr), np.nan)
    if n <= 0 or len(arr) < n:
        return out
    start = None
    for i in range(len(arr) - n + 1):
        window = arr[i : i + n]
        if np.all(np.isfinite(window)):
            start = i
            break
    if start is None:
        return out
    seed_at = start + n - 1
    out[seed_at] = float(np.mean(arr[start : start + n]))
    for i in range(seed_at + 1, len(arr)):
        x = arr[i]
        if not np.isfinite(x) or not np.isfinite(out[i - 1]):
            continue
        out[i] = (out[i - 1] * (n - 1) + x) / n
    return out


def sma(values: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(values, dtype=float).rolling(n, min_periods=n).mean().to_numpy()


def ema(values: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(values, dtype=float).ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev = np.roll(close, 1)
    prev[0] = np.nan
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    tr[0] = high[0] - low[0]
    return tr


def atr(high, low, close, n: int) -> np.ndarray:
    return wilder(true_range(high, low, close), n)


def rsi_wilder(close: np.ndarray, n: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=np.nan)
    gain = np.where(np.isfinite(delta), np.maximum(delta, 0.0), np.nan)
    loss = np.where(np.isfinite(delta), np.maximum(-delta, 0.0), np.nan)
    avg_gain = wilder(gain, n)
    avg_loss = wilder(loss, n)
    out = np.full(len(close), np.nan)
    for i in range(len(close)):
        g, l = avg_gain[i], avg_loss[i]
        if not np.isfinite(g) or not np.isfinite(l):
            continue
        if l == 0:
            out[i] = 100.0 if g > 0 else 50.0
        else:
            rs = g / l
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd_hist(close: np.ndarray, fast: int, slow: int, signal: int):
    line = ema(close, fast) - ema(close, slow)
    sig = pd.Series(line).ewm(span=signal, adjust=False, min_periods=signal).mean().to_numpy()
    hist = line - sig
    return line, sig, hist


def rolling_max(values: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(values, dtype=float).rolling(n, min_periods=n).max().to_numpy()


def rolling_min(values: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(values, dtype=float).rolling(n, min_periods=n).min().to_numpy()


def crosses(close: np.ndarray, line: np.ndarray, lookback: int) -> int:
    """最近 lookback 根里收盘穿越 line 的次数。数据不够返回 -1。"""
    if len(close) < lookback + 1:
        return -1
    c = close[-(lookback + 1) :]
    m = line[-(lookback + 1) :]
    if not np.all(np.isfinite(c) & np.isfinite(m)):
        return -1
    above = c > m
    return int(np.sum(above[1:] != above[:-1]))


def compute(df: pd.DataFrame, cfg: dict) -> dict:
    """在一份已对齐的 OHLCV 上计算指标。返回与行数相同的数组。"""
    ind = cfg["indicators"]
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    v = df["Volume"].to_numpy(float)
    atr14 = atr(h, l, c, ind["atr"])
    atr20 = atr(h, l, c, ind["atr_slow"])
    atr22 = atr(h, l, c, ind["atr_chandelier"])
    ema10 = ema(c, ind["ema_fast"])
    ema21 = ema(c, ind["ema_mid"])
    sma20 = sma(c, ind["bb_period"])
    sma50 = sma(c, ind["sma_mid"])
    sma150 = sma(c, ind["sma_long"])
    sma200 = sma(c, ind["sma_trend"])
    ema20 = ema(c, ind["kc_period"])
    vol_sma20 = sma(v, ind["volume_avg_short"])
    vol_sma50 = sma(v, ind["rvol"])
    with np.errstate(divide="ignore", invalid="ignore"):
        rvol = v / vol_sma50
        vol_ratio_20 = v / vol_sma20
        dollar = c * v
    dollar_sma50 = sma(dollar, ind["rvol"])
    rsi = rsi_wilder(c, ind["rsi"])
    _macd, _sig, hist = macd_hist(c, ind["macd_fast"], ind["macd_slow"], ind["macd_signal"])
    std20 = pd.Series(c).rolling(ind["bb_period"], min_periods=ind["bb_period"]).std(ddof=0).to_numpy()
    bb_u = sma20 + ind["bb_std"] * std20
    bb_l = sma20 - ind["bb_std"] * std20
    kc_u = ema20 + ind["kc_mult"] * atr20
    kc_l = ema20 - ind["kc_mult"] * atr20
    with np.errstate(divide="ignore", invalid="ignore"):
        bb_width = (bb_u - bb_l) / sma20
    width_pct = (
        pd.Series(bb_width)
        .rolling(ind["squeeze_lookback"], min_periods=ind["squeeze_lookback"])
        .quantile(ind["squeeze_width_pct"])
        .to_numpy()
    )
    squeeze_kc = (bb_u < kc_u) & (bb_l > kc_l)
    squeeze_width = bb_width <= width_pct
    squeeze = squeeze_kc | squeeze_width
    hhv252 = rolling_max(h, ind["high_low_bars"])
    llv252 = rolling_min(l, ind["high_low_bars"])
    hhv22 = rolling_max(h, ind["atr_chandelier"])
    chandelier = hhv22 - ind["chandelier_mult"] * atr22
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "atr14": atr14,
        "atr20": atr20,
        "atr22": atr22,
        "ema10": ema10,
        "ema21": ema21,
        "ema20": ema20,
        "sma20": sma20,
        "sma50": sma50,
        "sma150": sma150,
        "sma200": sma200,
        "rvol": rvol,
        "vol_ratio_20": vol_ratio_20,
        "dollar_sma50": dollar_sma50,
        "rsi": rsi,
        "macd_hist": hist,
        "bb_upper": bb_u,
        "bb_lower": bb_l,
        "kc_upper": kc_u,
        "kc_lower": kc_l,
        "squeeze": squeeze,
        "hhv252": hhv252,
        "llv252": llv252,
        "chandelier": chandelier,
    }


def weekly_ok(df: pd.DataFrame, weeks: int) -> bool | None:
    if df.empty:
        return None
    weekly = (
        df.resample("W-FRI")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Close"])
    )
    if len(weekly) < weeks + 1:
        return None
    sma_w = weekly["Close"].rolling(weeks, min_periods=weeks).mean()
    c = float(weekly["Close"].iloc[-1])
    s0 = float(sma_w.iloc[-1])
    s1 = float(sma_w.iloc[-2])
    if not np.isfinite(s0) or not np.isfinite(s1):
        return None
    return bool(c > s0 and s0 > s1)


def ret_over(close: np.ndarray, n: int, lag: int = 0) -> float | None:
    end = len(close) - 1 - lag
    start = end - n
    if start < 0 or end < 0:
        return None
    a, b = close[end], close[start]
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return None
    return float(a / b - 1.0)


def rs_raw(close: np.ndarray, weights: list[float], span: int) -> float | None:
    parts = []
    for i, w in enumerate(weights):
        r = ret_over(close, span, lag=span * i)
        if r is None:
            return None
        parts.append(w * r)
    return float(sum(parts))


def avwap(high, low, close, volume, start: int) -> float | None:
    if start is None or start < 0 or start >= len(close):
        return None
    tp = (high[start:] + low[start:] + close[start:]) / 3.0
    vol = volume[start:]
    denom = np.nansum(vol)
    if not np.isfinite(denom) or denom <= 0:
        return None
    return float(np.nansum(tp * vol) / denom)


def last_gap_index(open_, close, threshold: float) -> int | None:
    if len(close) < 2:
        return None
    found = None
    for i in range(1, len(close)):
        prev = close[i - 1]
        if not np.isfinite(prev) or prev == 0 or not np.isfinite(open_[i]):
            continue
        if abs(open_[i] / prev - 1.0) >= threshold:
            found = i
    return found


def ytd_index(index: pd.DatetimeIndex) -> int | None:
    if len(index) == 0:
        return None
    year = index[-1].year
    hits = np.where(index.year == year)[0]
    if len(hits) == 0:
        return None
    return int(hits[0])
