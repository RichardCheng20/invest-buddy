"""底部形态。识别失败返回 None，不当成「没有形态」以外的结论。"""

from __future__ import annotations

import numpy as np


def _finite_slice(values: np.ndarray, a: int, b: int) -> bool:
    if a < 0 or b >= len(values) or a > b:
        return False
    return bool(np.all(np.isfinite(values[a : b + 1])))


def detect_flat(high, low, cfg) -> dict | None:
    n = len(high)
    p = cfg["patterns"]
    # 形态截止到昨天，今天才有资格当突破日。
    for length in range(p["flat_min_bars"], p["flat_max_bars"] + 1):
        h1 = n - 2
        h0 = h1 - length + 1
        if h0 < 0:
            break
        if not _finite_slice(high, h0, h1) or not _finite_slice(low, h0, h1):
            continue
        hh = float(np.max(high[h0 : h1 + 1]))
        ll = float(np.min(low[h0 : h1 + 1]))
        if hh <= 0:
            continue
        if (hh - ll) / hh <= p["flat_depth"]:
            return {"kind": "flat", "pivot": hh, "base_low": ll, "length": length}
    return None


def detect_cup(high, low, close, rvol, cfg) -> dict | None:
    n = len(close)
    p = cfg["patterns"]
    i = n - 1
    for handle_len in range(p["handle_min_bars"], p["handle_max_bars"] + 1):
        h0 = i - handle_len
        h1 = i - 1
        if h0 < p["cup_min_bars"]:
            break
        if not _finite_slice(high, h0, h1) or not _finite_slice(low, h0, h1):
            continue
        hh = float(np.max(high[h0 : h1 + 1]))
        ll = float(np.min(low[h0 : h1 + 1]))
        if hh <= 0:
            continue
        if (hh - ll) / hh > p["handle_depth_max"]:
            continue
        rv = rvol[h0 : h1 + 1]
        if not np.all(np.isfinite(rv)) or float(np.mean(rv)) >= p["handle_rvol_max"]:
            continue
        left_start = max(0, h0 - p["cup_max_bars"])
        left_end = h0 - p["cup_min_bars"]
        if left_end <= left_start:
            continue
        if not _finite_slice(high, left_start, left_end):
            continue
        left_idx = left_start + int(np.argmax(high[left_start : left_end + 1]))
        left_high = float(high[left_idx])
        if left_high <= 0 or left_idx + 1 >= h0:
            continue
        if not _finite_slice(low, left_idx, h0 - 1):
            continue
        cup_low_idx = left_idx + int(np.argmin(low[left_idx:h0]))
        cup_low = float(low[cup_low_idx])
        depth = (left_high - cup_low) / left_high
        if not (p["cup_depth_min"] <= depth <= p["cup_depth_max"]):
            continue
        cup_mid = (left_high + cup_low) / 2.0
        if ll < cup_mid:
            continue
        if hh > left_high * p["handle_vs_left_high"]:
            continue
        recover_high = float(np.max(high[cup_low_idx : h0 + 1]))
        if recover_high < left_high * p["cup_recover"]:
            continue
        return {
            "kind": "cup_handle",
            "pivot": hh,
            "base_low": cup_low,
            "length": int(i - left_idx),
        }
    return None


def _swing_points(high, low, start: int, end: int):
    highs, lows = [], []
    for k in range(max(start, 3), end - 2):
        if high[k] == np.max(high[k - 3 : k + 4]) and np.isfinite(high[k]):
            highs.append(k)
        if low[k] == np.min(low[k - 3 : k + 4]) and np.isfinite(low[k]):
            lows.append(k)
    return highs, lows


def detect_vcp(high, low, rvol, cfg) -> dict | None:
    n = len(high)
    p = cfg["patterns"]
    i = n - 1
    start = max(0, i - p["vcp_window"])
    if i - start < 30:
        return None
    highs, lows = _swing_points(high, low, start, i)
    pullbacks = []
    for hi in highs:
        nxt = [x for x in lows if x > hi]
        if not nxt:
            continue
        lo = nxt[0]
        hh = float(high[hi])
        if hh <= 0 or not np.isfinite(low[lo]):
            continue
        depth = (hh - float(low[lo])) / hh
        if depth < 0.02:
            continue
        rv = rvol[hi : lo + 1]
        if not np.any(np.isfinite(rv)):
            continue
        pullbacks.append((hi, lo, depth, float(np.nanmean(rv))))
    if len(pullbacks) < p["vcp_segments_min"]:
        return None
    tail = pullbacks[-p["vcp_segments_max"] :]
    for nseg in range(min(p["vcp_segments_max"], len(tail)), p["vcp_segments_min"] - 1, -1):
        segs = tail[-nseg:]
        ok = True
        for a, b in zip(segs, segs[1:]):
            if b[2] >= a[2] * p["vcp_contraction"]:
                ok = False
                break
        last = segs[-1]
        if last[2] > p["vcp_last_depth"] or last[3] >= p["vcp_last_rvol"]:
            ok = False
        if i - last[1] > p["vcp_recency"]:
            ok = False
        if ok:
            return {
                "kind": "vcp",
                "pivot": float(high[last[0]]),
                "base_low": float(low[last[1]]),
                "length": int(segs[-1][0] - segs[0][0]),
            }
    return None


def best_base(high, low, close, rvol, cfg) -> dict | None:
    """优先更具体的形态。几个都成立时取 VCP、杯柄、平台这个顺序里的第一个。"""
    for fn in (
        lambda: detect_vcp(high, low, rvol, cfg),
        lambda: detect_cup(high, low, close, rvol, cfg),
        lambda: detect_flat(high, low, cfg),
    ):
        found = fn()
        if found is not None:
            return found
    return None
