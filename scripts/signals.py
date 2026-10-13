"""收盘信号。门槛全部来自 config/signal_rules.yaml。

止损合成默认用 nearer（框架里的「较近」）。signal_rules §5 的 min() 字面是更远的价格，
把 stops.combine 改成 farther 即切换。
"""

from __future__ import annotations

import math

import numpy as np

import indicators as indmod
import patterns


def _ok(x) -> bool:
    return x is not None and isinstance(x, (int, float, np.floating)) and math.isfinite(float(x))


def _f(x):
    return float(x) if _ok(x) else None


def _last(arr, n=1):
    if arr is None or len(arr) < n:
        return None
    return arr[-n]


def distribution_flags(close, volume, threshold: float) -> np.ndarray:
    flags = np.zeros(len(close), dtype=bool)
    for i in range(1, len(close)):
        prev = close[i - 1]
        if not _ok(prev) or prev == 0 or not _ok(close[i]) or not _ok(volume[i]) or not _ok(volume[i - 1]):
            continue
        if close[i] / prev - 1.0 <= threshold and volume[i] > volume[i - 1]:
            flags[i] = True
    return flags


def vix_lock_series(ratio: np.ndarray, invert: float, clear: float, clears_needed: int) -> np.ndarray:
    lock = False
    streak = 0
    out = np.zeros(len(ratio), dtype=bool)
    for i, x in enumerate(ratio):
        if not _ok(x):
            out[i] = lock
            continue
        if x > invert:
            lock = True
            streak = 0
        elif lock:
            if x < clear:
                streak += 1
                if streak >= clears_needed:
                    lock = False
                    streak = 0
            else:
                streak = 0
        out[i] = lock
    return out


def _dist_count_asof(spy_dates, spy_flags, qqq_dates, qqq_flags, lookback: int, offset: int) -> int | None:
    spy_map = {d: bool(f) for d, f in zip(spy_dates, spy_flags)}
    qqq_map = {d: bool(f) for d, f in zip(qqq_dates, qqq_flags)}
    days = list(spy_dates)
    end = len(days) - 1 - offset
    if end < 0:
        return None
    start = max(0, end - lookback + 1)
    if end - start + 1 < lookback:
        return None
    count = 0
    for d in days[start : end + 1]:
        if spy_map.get(d, False) or qqq_map.get(d, False):
            count += 1
    return count


def _point(close, sma, i) -> bool | None:
    if i < 0 or i >= len(close) or not _ok(close[i]) or not _ok(sma[i]):
        return None
    return bool(close[i] > sma[i])


def regime_snapshot(spy, qqq, vix_close, vix3m_close, spy_dates, qqq_dates, breadth, cfg) -> dict:
    """breadth: {0: (pct50, pct200, n), 1: ...} 以观察列表近似全市场。"""
    rc = cfg["regime"]
    threshold = rc["distribution_return"]
    spy_flags = distribution_flags(spy["close"], spy["volume"], threshold)
    qqq_flags = distribution_flags(qqq["close"], qqq["volume"], threshold)
    # VIX 对齐到 SPY 的日期
    vix_map = {d: v for d, v in zip(spy_dates, vix_close)} if len(vix_close) == len(spy_dates) else None
    if vix_map is None:
        # 调用方应已对齐。长度不一致时按位置只在等长时使用。
        vix_on_spy = np.full(len(spy_dates), np.nan)
        vix3_on_spy = np.full(len(spy_dates), np.nan)
    else:
        vix_on_spy = np.asarray(vix_close, dtype=float)
        vix3_on_spy = np.asarray(vix3m_close, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = vix_on_spy / vix3_on_spy
    locks = vix_lock_series(ratio, rc["vix_ratio_invert"], rc["vix_ratio_clear"], rc["vix_clear_closes"])

    def at(offset: int) -> dict:
        i = len(spy["close"]) - 1 - offset
        j = len(qqq["close"]) - 1 - offset
        b = breadth.get(offset)
        pct50 = pct200 = None
        b_n = 0
        if b is not None:
            pct50, pct200, b_n = b
        parts = {
            "spy_above_50": _point(spy["close"], spy["sma50"], i),
            "spy_above_200_stack": None,
            "qqq_above_50": _point(qqq["close"], qqq["sma50"], j) if j >= 0 else None,
            "breadth": None,
            "vix_contango": None,
        }
        if i >= 0 and _ok(spy["close"][i]) and _ok(spy["sma200"][i]) and _ok(spy["sma50"][i]):
            parts["spy_above_200_stack"] = bool(spy["close"][i] > spy["sma200"][i] and spy["sma50"][i] > spy["sma200"][i])
        if pct50 is not None and pct200 is not None:
            parts["breadth"] = bool(pct50 > rc["breadth_above_50_min"] and pct200 > rc["breadth_above_200_min"])
        if i >= 0 and _ok(ratio[i]):
            parts["vix_contango"] = bool(ratio[i] < 1.0 and vix_on_spy[i] < vix3_on_spy[i])
            # 规格是 VIX < VIX3M，与比值 < 1 相同（两者为正时）。
            parts["vix_contango"] = bool(_ok(vix_on_spy[i]) and _ok(vix3_on_spy[i]) and vix_on_spy[i] < vix3_on_spy[i])
        known = [parts[k] for k in ("spy_above_50", "spy_above_200_stack", "qqq_above_50", "breadth", "vix_contango")]
        if any(v is None for v in known):
            score = None
        else:
            score = int(sum(bool(v) for v in known))
        dist = _dist_count_asof(spy_dates, spy_flags, qqq_dates, qqq_flags, rc["distribution_lookback"], offset)
        dist_penalty = bool(dist is not None and dist >= rc["distribution_count_penalty"])
        if score is not None and dist_penalty:
            score = max(0, score - 1)
        if score is None:
            regime = None
        elif score >= rc["green_min_score"]:
            regime = "GREEN"
        elif score >= rc["yellow_min_score"]:
            regime = "YELLOW"
        else:
            regime = "RED"
        locked = bool(locks[i]) if i >= 0 else False
        if locked:
            regime = "RED"
        return {
            "score": score,
            "score_raw_before_lock": score,
            "regime": regime,
            "parts": parts,
            "distribution_count": dist,
            "distribution_penalty": dist_penalty,
            "vix": _f(vix_on_spy[i]) if i >= 0 else None,
            "vix3m": _f(vix3_on_spy[i]) if i >= 0 else None,
            "vix_ratio": _f(ratio[i]) if i >= 0 else None,
            "vix_lock": locked,
            "breadth_above_50": pct50,
            "breadth_above_200": pct200,
            "breadth_n": b_n,
        }

    today = at(0)
    yday = at(1)
    alerts = []
    if today["regime"] and yday["regime"] and today["regime"] != yday["regime"]:
        alerts.append({
            "id": "R1",
            "level": "primary",
            "title": "市场环境切换",
            "detail": f"{yday['regime']} → {today['regime']}（环境分 {today['score']}）",
        })
    if today["distribution_penalty"]:
        fresh = yday["distribution_count"] is not None and yday["distribution_count"] < rc["distribution_count_penalty"]
        alerts.append({
            "id": "R2",
            "level": "primary",
            "title": "派发日达到 5 个",
            "detail": f"SPY 或 QQQ 在最近 {rc['distribution_lookback']} 个交易日有 {today['distribution_count']} 个派发日"
            + ("，今天刚达到门槛" if fresh else ""),
        })
    if today["vix_ratio"] is not None and today["vix_ratio"] > rc["vix_ratio_invert"]:
        alerts.append({
            "id": "R3",
            "level": "primary",
            "title": "VIX 期限结构倒挂",
            "detail": f"VIX/VIX3M = {today['vix_ratio']:.2f}，环境按防守处理，直到连续 {rc['vix_clear_closes']} 天回到 {rc['vix_ratio_clear']} 以下",
        })
    elif today["vix_lock"]:
        alerts.append({
            "id": "R3",
            "level": "primary",
            "title": "VIX 倒挂锁定未解除",
            "detail": "比值已回到 1 以下，但还没有连续 2 个收盘低于 0.95",
        })
    idx = []
    i = len(spy["close"]) - 1
    if _point(spy["close"], spy["sma200"], i) is False:
        idx.append("SPY")
    j = len(qqq["close"]) - 1
    if _point(qqq["close"], qqq["sma200"], j) is False:
        idx.append("QQQ")
    if idx:
        alerts.append({
            "id": "IDX",
            "level": "primary",
            "title": "指数跌破 200 日均线",
            "detail": "、".join(idx) + " 收盘在 200 日均线下方。核心指数桶减半；重新站上并连续 2 天收在均线上方后再分三批买回",
        })
    risk_mult = rc["risk_mult"].get(today["regime"] or "", None)
    cap = rc["exposure_cap"].get(today["regime"] or "", None)
    today["risk_mult"] = risk_mult
    today["exposure_cap"] = cap
    today["yesterday_regime"] = yday["regime"]
    today["alerts"] = alerts
    today["index_below_200"] = idx
    today["breadth_approximate"] = True
    today["breadth_note"] = "宽度用观察列表（含持仓）计算，不是标普 500 全成分。只作近似。"
    return today


def _combine_stop(structural, atr_stop, entry, atr, max_pct, cfg) -> float | None:
    stops = cfg["stops"]
    prices = [p for p in (structural, atr_stop) if _ok(p)]
    if not prices or not _ok(entry) or entry <= 0 or not _ok(atr):
        return None
    if stops["combine"] == "farther":
        stop = min(prices)
    else:
        stop = max(prices)
    dist = entry - stop
    min_dist = stops["min_stop_atr"] * atr
    if dist < min_dist:
        if stops["below_min_atr"] == "widen":
            stop = entry - min_dist
            dist = min_dist
        else:
            return None
    if dist <= 0 or dist / entry > max_pct:
        return None
    return float(stop)


def _flat_market(close, sma50, cfg) -> bool | None:
    ind = cfg["indicators"]
    lb = ind["slope_lookback"]
    if len(sma50) < lb + 1:
        return None
    a, b = sma50[-1], sma50[-1 - lb]
    if not _ok(a) or not _ok(b) or b == 0:
        return None
    slope = (a - b) / b
    n_cross = indmod.crosses(close, sma50, ind["cross_lookback"])
    if n_cross < 0:
        return None
    return bool(abs(slope) < ind["flat_slope_abs"] and n_cross >= ind["flat_crosses"])


def _stage2(close, sma50, sma150, sma200, high_max, low_min, cfg) -> bool | None:
    need = [close, sma50, sma150, sma200, high_max, low_min]
    if any(not _ok(x) for x in need) or low_min <= 0 or high_max <= 0:
        return None
    s = cfg["stage2"]
    rising = True
    bars = s["sma200_rising_bars"]
    if len(sma200) < bars + 1 if False else False:
        return None
    # sma200 argument is the full array when passed that way. Here we pass scalars plus arrays separately.
    return None


def stage2_at(ind, cfg) -> bool | None:
    c = _last(ind["close"])
    s50 = _last(ind["sma50"])
    s150 = _last(ind["sma150"])
    s200 = _last(ind["sma200"])
    hh = _last(ind["hhv252"])
    ll = _last(ind["llv252"])
    bars = cfg["stage2"]["sma200_rising_bars"]
    if any(not _ok(x) for x in (c, s50, s150, s200, hh, ll)) or ll <= 0 or hh <= 0:
        return None
    if len(ind["sma200"]) < bars + 1 or not _ok(ind["sma200"][-1 - bars]):
        return None
    rising = ind["sma200"][-1] > ind["sma200"][-1 - bars]
    s = cfg["stage2"]
    return bool(
        c > s50 > s150 > s200
        and rising
        and c >= s["min_above_low"] * ll
        and c >= s["min_of_high"] * hh
    )


def _extended(ind, cfg) -> bool | None:
    c = _last(ind["close"])
    e = _last(ind["ema21"])
    a = _last(ind["atr14"])
    s = _last(ind["sma50"])
    if any(not _ok(x) for x in (c, e, a, s)) or s <= 0:
        return None
    return bool((c - e) > cfg["account"]["extended_atr"] * a or c > cfg["account"]["extended_sma50"] * s)


def _pullback_bounds(close, lookback: int):
    end = len(close) - 1
    start_limit = max(0, end - lookback)
    window = close[start_limit:end]
    if len(window) < 2 or not np.any(np.isfinite(window)):
        return None
    rel = int(np.nanargmax(window))
    swing = start_limit + rel
    if end - swing < 2:
        return None
    return swing, end


def _close_loc(high, low, close) -> float | None:
    rng = high - low
    if not _ok(rng) or rng <= 0 or not _ok(close) or not _ok(low):
        return None
    return (close - low) / rng


def _udvol(close, volume, rvol, n: int, rvol_min: float) -> float | None:
    if len(close) < n + 1:
        return None
    up = 0.0
    down = 0.0
    start = len(close) - n
    for i in range(start, len(close)):
        if not _ok(rvol[i]) or rvol[i] <= rvol_min or not _ok(close[i]) or not _ok(close[i - 1]):
            continue
        if close[i] > close[i - 1]:
            up += volume[i]
        elif close[i] < close[i - 1]:
            down += volume[i]
    if down <= 0:
        return None
    return up / down


def _momentum_ok(ind, cfg) -> bool | None:
    cf = cfg["confirmation"]
    rsi = ind["rsi"]
    hist = ind["macd_hist"]
    lb = cf["rsi_pullback_lookback"]
    if len(rsi) < lb + 1 or len(hist) < cf["macd_falling_bars"] + 2:
        return None
    window = rsi[-lb:]
    if not np.all(np.isfinite(window)) or not _ok(rsi[-1]) or not _ok(rsi[-2]):
        return None
    rsi_ok = cf["rsi_pullback_low"] <= float(np.min(window)) <= cf["rsi_pullback_high"] and rsi[-1] > rsi[-2]
    falling = True
    # 拐头前至少连续 macd_falling_bars 根下降
    need = cf["macd_falling_bars"]
    for k in range(need):
        a = hist[-2 - k]
        b = hist[-3 - k]
        if not _ok(a) or not _ok(b) or not (a < b):
            falling = False
            break
    macd_ok = bool(falling and _ok(hist[-1]) and _ok(hist[-2]) and hist[-1] > hist[-2])
    return bool(rsi_ok or macd_ok)


def _rs_at_high(rs_line: np.ndarray, bars: int) -> bool | None:
    if rs_line is None or len(rs_line) < bars:
        return None
    window = rs_line[-bars:]
    if not np.all(np.isfinite(window)):
        return None
    return bool(window[-1] >= np.max(window) * 0.999)


def _momentum_detail(ind, cfg) -> dict:
    cf = cfg["confirmation"]
    rsi = ind["rsi"]
    hist = ind["macd_hist"]
    lb = cf["rsi_pullback_lookback"]
    if len(rsi) < lb + 1 or len(hist) < cf["macd_falling_bars"] + 2:
        return {"pass": None, "text": "RSI 或 MACD 样本不够"}
    window = rsi[-lb:]
    if not np.all(np.isfinite(window)) or not _ok(rsi[-1]) or not _ok(rsi[-2]):
        return {"pass": None, "text": "RSI 有缺失"}
    rsi_low = float(np.min(window))
    rsi_ok = cf["rsi_pullback_low"] <= rsi_low <= cf["rsi_pullback_high"] and rsi[-1] > rsi[-2]
    falling = True
    need = cf["macd_falling_bars"]
    for k in range(need):
        a = hist[-2 - k]
        b = hist[-3 - k]
        if not _ok(a) or not _ok(b) or not (a < b):
            falling = False
            break
    macd_ok = bool(falling and _ok(hist[-1]) and _ok(hist[-2]) and hist[-1] > hist[-2])
    if rsi_ok:
        text = f"RSI 近 {lb} 日最低 {rsi_low:.1f}，落在 {cf['rsi_pullback_low']:.0f}–{cf['rsi_pullback_high']:.0f}，且 {float(rsi[-1]):.1f} 高于前一日 {float(rsi[-2]):.1f}"
    elif macd_ok:
        text = f"MACD 柱先连跌 {need} 根，最新一根转强"
    else:
        text = f"RSI {float(rsi[-1]):.1f}，近 {lb} 日最低 {rsi_low:.1f}，没有形成拐头"
    return {"pass": bool(rsi_ok or macd_ok), "text": text}


def confirmation_detail(ind, rs_line, cfg) -> dict:
    cf = cfg["confirmation"]
    rs_high = _rs_at_high(rs_line, cf["rs_high_bars"])
    ud = _udvol(ind["close"], ind["volume"], ind["rvol"], cf["udvol_lookback"], cf["udvol_rvol"])
    mom = _momentum_detail(ind, cfg)
    ud_pass = None if ud is None else bool(ud >= cf["udvol_min"])
    items = [
        {
            "id": "rs_high",
            "label": "相对强度线处在近一年高点",
            "pass": rs_high,
            "text": "数据不足" if rs_high is None else ("处在近一年高点" if rs_high else "没有处在近一年高点"),
        },
        {
            "id": "udvol",
            "label": "放量上涨量 / 放量下跌量",
            "pass": ud_pass,
            "text": "样本不足" if ud is None else f"{ud:.2f}，门槛 {cf['udvol_min']}",
        },
        {
            "id": "momentum",
            "label": "动量拐头（RSI 或 MACD，合计最多 1 分）",
            "pass": mom["pass"],
            "text": mom["text"],
        },
    ]
    if all(item["pass"] is None for item in items):
        score = None
    else:
        score = sum(1 for item in items if item["pass"] is True)
    return {"score": score, "items": items}


def confirmation_score(ind, rs_line, cfg) -> int | None:
    return confirmation_detail(ind, rs_line, cfg)["score"]


def _recent_cross(fast, slow, lookback=5) -> bool | None:
    if len(fast) < lookback + 1:
        return None
    found_up = False
    found_dn = False
    for i in range(-lookback, 0):
        if not all(_ok(x) for x in (fast[i], fast[i - 1], slow[i], slow[i - 1])):
            continue
        if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
            found_up = True
        if fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
            found_dn = True
    if found_up and not found_dn:
        return True
    if found_dn and not found_up:
        return False
    if found_up and found_dn:
        return None
    return False


def evaluate_tape(sig: dict, trade: dict, spy_close: np.ndarray, sector_ok, weekly, earnings_blk, regime: str | None, cfg, leverage: float = 1.0) -> dict:
    """sig 是用来判断信号的 K 线（杠杆 ETF 用标的），trade 是实际交易的工具。"""
    acct = cfg["account"]
    ent = cfg["entries"]
    close = sig["close"]
    c = _last(close)
    atr = _last(sig["atr14"])
    rvol = _last(sig["rvol"])
    blockers = []
    flags = []

    flat = _flat_market(close, sig["sma50"], cfg)
    st2 = stage2_at(sig, cfg)
    extended = _extended(sig, cfg)
    trade_extended = _extended(trade, cfg) if trade is not sig else extended
    liquid = None
    dvol = _last(trade["dollar_sma50"])
    if _ok(dvol):
        liquid = bool(dvol >= acct["liquid_dollar_avg"])
    hi_vol = None
    atr_pct = None
    if _ok(c) and c > 0 and _ok(atr):
        atr_pct = atr / c
        hi_vol = bool(atr_pct > acct["hi_vol_atr_pct"])

    rs_line = None
    if spy_close is not None and len(spy_close) == len(close):
        with np.errstate(divide="ignore", invalid="ignore"):
            rs_line = close / spy_close

    base = None
    if len(close) > 80 and np.isfinite(sig["rvol"]).sum() > 60:
        base = patterns.best_base(sig["high"], sig["low"], close, sig["rvol"], cfg)

    leader_pos = None
    leader_swing = None
    # RS rank 由外部填入后再判 leader。这里先留空位。
    out = {
        "close": _f(c),
        "atr14": _f(atr),
        "atr_pct": _f(atr_pct),
        "rvol": _f(rvol),
        "rsi": _f(_last(sig["rsi"])),
        "liquid": liquid,
        "extended": extended if trade is sig else (True if (extended or trade_extended) else (False if extended is False and trade_extended is False else None)),
        "flat_market": flat,
        "stage2": st2,
        "weekly_ok": weekly,
        "earnings_blk": earnings_blk,
        "hi_vol": hi_vol,
        "sector_rs_ok": sector_ok,
        "base": base,
        "rs_line_last": _f(rs_line[-1]) if rs_line is not None and _ok(rs_line[-1]) else None,
        "rs_line": rs_line,
        "levels": {
            "ema10": _f(_last(trade["ema10"])),
            "ema21": _f(_last(trade["ema21"])),
            "sma50": _f(_last(trade["sma50"])),
            "sma200": _f(_last(trade["sma200"])),
        },
        "sig_levels": {
            "ema10": _f(_last(sig["ema10"])),
            "ema21": _f(_last(sig["ema21"])),
            "sma50": _f(_last(sig["sma50"])),
            "sma150": _f(_last(sig["sma150"])),
            "sma200": _f(_last(sig["sma200"])),
            "chandelier": _f(_last(sig["chandelier"])),
        },
    }

    if liquid is False:
        blockers.append("50 日平均成交额低于 2 亿美元")
        flags.append("illiquid")
    if extended or trade_extended:
        blockers.append("价格相对 21 日 EMA 或 50 日 SMA 过度延伸")
        flags.append("extended")
    if earnings_blk is None:
        blockers.append("没有财报日期，入场信号抑制")
        flags.append("earnings_unknown")
    elif earnings_blk:
        blockers.append(f"财报前 {acct['earnings_block_days']} 个交易日内")
        flags.append("earnings_blk")
    if regime is None:
        blockers.append("市场环境数据缺失")
    elif regime == "RED":
        blockers.append("环境为防守，不开新的多头")
    if flat:
        blockers.append("50 日均线走平且价格反复穿越，均线类信号暂停")
        flags.append("flat_market")
    if hi_vol:
        flags.append("hi_vol")
    if sector_ok is False:
        flags.append("sector_lag")
    if weekly is False:
        flags.append("weekly_below")

    gate_open = (
        liquid is True
        and extended is not True
        and trade_extended is not True
        and earnings_blk is False
        and regime not in (None, "RED")
        and flat is not True
    )

    candidates = []
    if base is not None and _ok(c) and c > base["pivot"] and c <= base["pivot"] * (1 + acct["buy_zone_pct"]):
        reason = _fail_reasons_e1(out, rvol, ent, cfg)
        candidates.append(_pack_e1(sig, trade, base, rvol, reason, gate_open, cfg, leverage))
    e2 = _try_e2(sig, trade, out, gate_open, cfg, leverage)
    if e2 is not None:
        candidates.append(e2)
    e3 = _try_e3(sig, trade, gate_open, cfg, leverage)
    if e3 is not None:
        candidates.append(e3)
    e4 = _try_e4(sig, trade, gate_open, cfg, leverage)
    if e4 is not None:
        candidates.append(e4)
    e5 = _try_e5(sig, trade, out, gate_open, regime, cfg, leverage)
    if e5 is not None:
        candidates.append(e5)

    if leverage < 0:
        for cand in candidates:
            cand["ok"] = False
            cand["technical"] = False
            cand["reason"] = "反向 ETF 不生成做多信号"
        flags.append("inverse_etf")
    if not gate_open:
        gate_reason = "；".join(blockers) if blockers else "资格过滤未通过"
        for cand in candidates:
            if not cand.get("reason"):
                cand["reason"] = gate_reason

    c_detail = confirmation_detail(sig, rs_line, cfg)
    c_score = c_detail["score"]
    cf = cfg["confirmation"]
    size_mult = None
    if c_score is not None:
        size_mult = cf["size_mult_full"] if c_score >= cf["full_min_score"] else cf["size_mult_half"]

    cross = _recent_cross(sig["sma50"], sig["sma200"], 5)
    if cross is True:
        flags.append("golden_cross_bg")
    elif cross is False:
        # False means death cross recent, or no golden. _recent_cross returns False for death-only or neither.
        pass
    death = _recent_death(sig["sma50"], sig["sma200"], 5)
    if death:
        flags.append("death_cross_bg")

    ok_ones = [cnd for cnd in candidates if cnd.get("ok")]
    ok_ones.sort(key=lambda cnd: cfg["priority"].index(cnd["id"]) if cnd["id"] in cfg["priority"] else 99)
    primary = ok_ones[0] if ok_ones else None

    out.update({
        "blockers": blockers,
        "flags": flags,
        "candidates": [{k: v for k, v in cnd.items() if k != "debug"} for cnd in candidates],
        "primary_id": primary["id"] if primary else None,
        "entry": primary["entry"] if primary else None,
        "stop": primary["stop"] if primary else None,
        "pivot": primary.get("pivot") if primary else (base["pivot"] if base else None),
        "c_score": c_score,
        "c_detail": c_detail,
        "size_mult": size_mult,
        "horizon": cfg["horizon"].get(primary["id"]) if primary else None,
        "initial_fraction": cfg["initial_fraction"].get(primary["id"]) if primary else None,
        "time_stop": acct["swing_time_stop_bars"] if primary and cfg["horizon"].get(primary["id"]) == "swing" else None,
        "gate_open": gate_open,
        "leverage": leverage,
    })
    return out


def _recent_death(fast, slow, lookback) -> bool:
    if len(fast) < lookback + 1:
        return False
    for i in range(-lookback, 0):
        if not all(_ok(x) for x in (fast[i], fast[i - 1], slow[i], slow[i - 1])):
            continue
        if fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
            return True
    return False


def _fact(label: str, ok, text: str) -> dict:
    return {"label": label, "pass": None if ok is None else bool(ok), "text": text}


def _fail_reasons_e1(tape, rvol, ent, cfg) -> list[str]:
    reasons = []
    if tape["stage2"] is not True:
        reasons.append("不符合第二阶段模板")
    if tape["weekly_ok"] is not True:
        reasons.append("周线不在上升的 30 周均线之上")
    if tape["sector_rs_ok"] is not True:
        reasons.append("行业相对 SPY 不在 50 日均线之上")
    if not _ok(rvol) or rvol < ent["e1_rvol"]:
        reasons.append(f"RVOL {rvol:.2f}，需要 ≥ {ent['e1_rvol']}" if _ok(rvol) else "RVOL 缺失")
    return reasons


def _max_pct(horizon, cfg) -> float:
    acct = cfg["account"]
    return acct["max_stop_pct_swing"] if horizon == "swing" else acct["max_stop_pct_position"]


def _apply_leverage_stop(under_entry, under_stop, trade, horizon, cfg, leverage: float):
    entry = _last(trade["close"])
    atr = _last(trade["atr14"])
    if not _ok(entry) or not _ok(under_entry) or under_entry <= 0 or not _ok(under_stop):
        return None, None
    dist_pct = (under_entry - under_stop) / under_entry
    if leverage != 1:
        dist_pct = dist_pct * abs(leverage)
    stop = entry * (1 - dist_pct)
    checked = _combine_stop(stop, stop, entry, atr if _ok(atr) else dist_pct * entry, _max_pct(horizon, cfg), cfg)
    # _combine_stop with both prices equal just validates distance
    return (_f(entry), checked)


def _pack_e1(sig, trade, base, rvol, pre_reasons, gate_open, cfg, leverage) -> dict:
    horizon = cfg["horizon"]["E1"]
    entry_u = _last(sig["close"])
    atr_u = _last(sig["atr14"])
    structural = base["base_low"] - cfg["stops"]["structure_pad_atr"] * atr_u if _ok(atr_u) else None
    atr_stop = entry_u - cfg["stops"]["e1_atr_mult"] * atr_u if _ok(entry_u) and _ok(atr_u) else None
    under_stop = _combine_stop(structural, atr_stop, entry_u, atr_u, _max_pct(horizon, cfg), cfg) if leverage == 1 else (
        _combine_stop(structural, atr_stop, entry_u, atr_u, 1.0, cfg)  # 标的上先不套账户止损上限，换到 ETF 再套
    )
    # 杠杆不为 1 时，标的止损只要求距离为正，上限放到 ETF 上检查。
    if leverage != 1 and _ok(entry_u) and _ok(atr_u):
        prices = [p for p in (structural, atr_stop) if _ok(p)]
        if not prices:
            under_stop = None
        else:
            raw = min(prices) if cfg["stops"]["combine"] == "farther" else max(prices)
            under_stop = raw if entry_u > raw else None
    entry, stop = _apply_leverage_stop(entry_u, under_stop, trade, horizon, cfg, leverage) if under_stop else (None, None)
    reasons = list(pre_reasons)
    if stop is None:
        reasons.append("止损距离不在 1×ATR 与止损上限之间")
    technical = not reasons and stop is not None
    ok = gate_open and technical
    kind = {"vcp": "波动收缩", "cup": "杯柄", "flat": "平底"}.get(base["kind"], base["kind"])
    rvol_txt = f"{rvol:.2f}" if _ok(rvol) else "缺失"
    close_txt = f"{float(entry_u):.2f}" if _ok(entry_u) else "—"
    return {
        "id": "E1",
        "ok": ok,
        "technical": technical,
        "pending_rs": cfg["entries"]["leader_pos_rs"],
        "reason": "；".join(reasons),
        "entry": entry,
        "stop": stop,
        "pivot": base["pivot"],
        "base_low": base["base_low"],
        "pattern": base["kind"],
        "blurb": f"突破{kind}枢轴 {base['pivot']:.2f}",
        "facts": [
            _fact("突破", True, f"收盘 {close_txt} 高于{kind}枢轴 {base['pivot']:.2f}"),
            _fact("量比", not any("RVOL" in item for item in reasons), f"量比 {rvol_txt}，需要 ≥ {cfg['entries']['e1_rvol']}"),
            _fact("阶段", not pre_reasons, "；".join(pre_reasons) or "第二阶段、周线和行业条件通过"),
        ],
    }


def _try_e2(sig, trade, tape, gate_open, cfg, leverage):
    ent = cfg["entries"]
    if tape["stage2"] is not True:
        return None
    close = sig["close"]
    low = sig["low"]
    high = sig["high"]
    open_ = sig["open"]
    atr = sig["atr14"]
    rvol = sig["rvol"]
    bounds = _pullback_bounds(close, ent["pullback_max_lookback"])
    tagged_any = False
    for name, ma in (("EMA21", sig["ema21"]), ("SMA50", sig["sma50"])):
        if not all(_ok(x[-1]) for x in (ma, atr, low, close, open_, high)):
            continue
        if not (low[-1] <= ma[-1] + ent["e2_ma_atr"] * atr[-1] and close[-1] >= ma[-1]):
            continue
        tagged_any = True
        loc = _close_loc(high[-1], low[-1], close[-1])
        bullish = close[-1] > open_[-1] and loc is not None and loc >= ent["e2_close_location"]
        dry = False
        mean_rv = None
        if bounds is not None:
            seg = rvol[bounds[0] : bounds[1]]
            if np.any(np.isfinite(seg)):
                mean_rv = float(np.nanmean(seg))
                dry = mean_rv <= ent["e2_rvol_max"]
        if not (bullish and dry):
            continue
        pull_low = float(np.nanmin(low[bounds[0] : bounds[1] + 1])) if bounds else float(low[-1])
        horizon = cfg["horizon"]["E2"]
        entry_u = float(close[-1])
        structural = pull_low - cfg["stops"]["structure_pad_atr"] * atr[-1]
        atr_stop = entry_u - cfg["stops"]["e2_atr_mult"] * atr[-1]
        under_stop = _stop_on_underlying(structural, atr_stop, entry_u, atr[-1], horizon, cfg, leverage)
        entry, stop = _apply_leverage_stop(entry_u, under_stop, trade, horizon, cfg, leverage) if under_stop else (None, None)
        reason = "" if stop is not None else "止损距离不在允许区间"
        ma_cn = "21 日均线" if name == "EMA21" else "50 日均线"
        flat = tape["flat_market"] is True
        technical = stop is not None and not flat
        ok = gate_open and technical
        if flat:
            reason = "横盘，E2 暂停"
        elif stop is None:
            reason = "止损距离不在允许区间"
        return {
            "id": "E2",
            "ok": ok,
            "technical": technical,
            "pending_rs": ent["leader_swing_rs"],
            "reason": reason,
            "entry": entry,
            "stop": stop,
            "pivot": _f(ma[-1]),
            "ma": name,
            "base_low": pull_low,
            "blurb": f"缩量回踩 {ma_cn}后收阳",
            "facts": [
                _fact("回踩", True, f"最低 {pull_low:.2f} 触及 {ma_cn} {float(ma[-1]):.2f}，收盘 {float(close[-1]):.2f} 在均线之上"),
                _fact("收阳", True, f"收盘高于开盘，收在当天振幅的 {loc:.0%}"),
                _fact("缩量", True, f"回踩段平均量比 {mean_rv:.2f}，上限 {ent['e2_rvol_max']}"),
                _fact("止损", stop is not None, "止损距离在 1 倍 ATR 和止损上限之间" if stop is not None else "止损距离不在允许区间"),
            ],
        }
    if tagged_any:
        return {
            "id": "E2",
            "ok": False,
            "technical": False,
            "pending_rs": ent["leader_swing_rs"],
            "reason": "碰到 21 日均线或 50 日均线，但没有缩量收阳",
            "entry": None,
            "stop": None,
            "pivot": None,
            "blurb": "碰到均线，但量价没有确认",
            "facts": [
                _fact("回踩", True, "最低价触及 21 日均线或 50 日均线"),
                _fact("确认", False, "需要回踩段缩量，并且收阳、收在当天振幅上半部"),
            ],
        }
    return None


def _stop_on_underlying(structural, atr_stop, entry, atr, horizon, cfg, leverage):
    if leverage == 1:
        return _combine_stop(structural, atr_stop, entry, atr, _max_pct(horizon, cfg), cfg)
    prices = [p for p in (structural, atr_stop) if _ok(p)]
    if not prices or not _ok(entry):
        return None
    stop = min(prices) if cfg["stops"]["combine"] == "farther" else max(prices)
    if entry - stop < cfg["stops"]["min_stop_atr"] * atr and cfg["stops"]["below_min_atr"] != "widen":
        return None
    if entry <= stop:
        return None
    return float(stop)


def _try_e3(sig, trade, gate_open, cfg, leverage):
    ent = cfg["entries"]
    close, volume, high, low = sig["close"], sig["volume"], sig["high"], sig["low"]
    if len(close) < ent["e3_down_lookback"] + 2:
        return None
    if not (_ok(close[-1]) and _ok(close[-2]) and close[-1] > close[-2]):
        return None
    down_vols = []
    start = len(close) - 1 - ent["e3_down_lookback"]
    for i in range(start, len(close) - 1):
        if _ok(close[i]) and _ok(close[i - 1]) and close[i] < close[i - 1] and _ok(volume[i]):
            down_vols.append(volume[i])
    if not down_vols:
        return None
    if not _ok(volume[-1]) or volume[-1] <= max(down_vols):
        return None
    ema10, sma50, atr = sig["ema10"][-1], sig["sma50"][-1], sig["atr14"][-1]
    near = False
    if _ok(ema10) and ema10 > 0 and abs(close[-1] / ema10 - 1) <= ent["e3_ma_band"]:
        near = True
    if _ok(sma50) and sma50 > 0 and abs(close[-1] / sma50 - 1) <= ent["e3_ma_band"]:
        near = True
    if not near:
        return None
    if not _ok(sma50) or not _ok(atr) or (close[-1] - sma50) > ent["e3_sma50_atr"] * atr:
        return {
            "id": "E3",
            "ok": False,
            "technical": False,
            "pending_rs": None,
            "reason": "离 50 日均线超过 2 倍 ATR",
            "entry": None,
            "stop": None,
            "pivot": None,
            "blurb": "量能像口袋支点，但离 50 日均线太远",
            "facts": [_fact("偏离", False, "收盘高出 50 日均线超过 2 倍 ATR")],
        }
    horizon = cfg["horizon"]["E3"]
    entry_u = float(close[-1])
    atr_stop = entry_u - cfg["stops"]["e3_atr_mult"] * atr
    under_stop = _stop_on_underlying(None, atr_stop, entry_u, atr, horizon, cfg, leverage)
    entry, stop = _apply_leverage_stop(entry_u, under_stop, trade, horizon, cfg, leverage) if under_stop else (None, None)
    near_bits = []
    if _ok(ema10) and ema10 > 0 and abs(close[-1] / ema10 - 1) <= ent["e3_ma_band"]:
        near_bits.append(f"距 10 日均线 {abs(close[-1] / ema10 - 1):.1%}")
    if _ok(sma50) and sma50 > 0 and abs(close[-1] / sma50 - 1) <= ent["e3_ma_band"]:
        near_bits.append(f"距 50 日均线 {abs(close[-1] / sma50 - 1):.1%}")
    stretch = (close[-1] - sma50) / atr if _ok(atr) and atr else None
    if stretch is None:
        stretch_txt = "偏离算不出来"
    elif stretch >= 0:
        stretch_txt = f"高出 50 日均线 {stretch:.2f} 倍 ATR，上限 {ent['e3_sma50_atr']:.0f}"
    else:
        stretch_txt = f"低于 50 日均线 {abs(stretch):.2f} 倍 ATR，没有向上偏离过度"
    return {
        "id": "E3",
        "ok": gate_open and stop is not None,
        "technical": stop is not None,
        "pending_rs": None,
        "reason": "" if stop is not None else "止损距离不在允许区间",
        "entry": entry,
        "stop": stop,
        "pivot": _f(ema10),
        "blurb": "收盘高于前收，成交量盖过近期下跌日，并贴近均线",
        "facts": [
            _fact("收盘上涨", True, f"收盘 {close[-1]:.2f}，前收 {close[-2]:.2f}"),
            _fact("量能", True, f"今日量 {volume[-1]:.0f}，近 {ent['e3_down_lookback']} 日下跌日最大量 {max(down_vols):.0f}"),
            _fact("贴近均线", True, "；".join(near_bits) or "贴近 10 日或 50 日均线"),
            _fact("偏离", True, stretch_txt),
        ],
    }


def _try_e4(sig, trade, gate_open, cfg, leverage):
    """调用方把 earnings gap 信息放在 sig['e4']，没有则不评估。"""
    info = sig.get("e4")
    if not info:
        return None
    if not info.get("matched"):
        return {
            "id": "E4",
            "ok": False,
            "technical": False,
            "pending_rs": None,
            "reason": info.get("reason") or "没有符合条件的财报跳空",
            "entry": None,
            "stop": None,
            "pivot": info.get("pivot"),
            "blurb": info.get("reason") or "没有符合条件的财报跳空",
            "facts": [],
        }
    horizon = cfg["horizon"]["E4"]
    entry_u = float(sig["close"][-1])
    atr = sig["atr14"][-1]
    gap_low = info["gap_low"]
    structural = gap_low - cfg["stops"]["structure_pad_atr"] * atr if _ok(atr) else None
    under_stop = _stop_on_underlying(structural, structural, entry_u, atr, horizon, cfg, leverage)
    entry, stop = _apply_leverage_stop(entry_u, under_stop, trade, horizon, cfg, leverage) if under_stop else (None, None)
    return {
        "id": "E4",
        "ok": gate_open and stop is not None,
        "technical": stop is not None,
        "pending_rs": None,
        "reason": "" if stop is not None else "止损距离不在允许区间",
        "entry": entry,
        "stop": stop,
        "pivot": info.get("pivot"),
        "base_low": gap_low,
        "blurb": "财报跳空后仍站在跳空锚定均价之上",
        "facts": [
            _fact("跳空", True, f"跳空日高点 {float(info.get('pivot')):.2f}，跳空日低点 {float(gap_low):.2f}"),
            _fact("锚定均价", True, f"收盘 {entry_u:.2f} 仍在财报日锚定均价之上，且最低价没有跌破跳空日低点"),
        ],
    }


def _try_e5(sig, trade, tape, gate_open, regime, cfg, leverage):
    if len(sig["squeeze"]) < 2:
        return None
    prev = sig["squeeze"][-2]
    if prev is not True and not (isinstance(prev, (bool, np.bool_)) and bool(prev)):
        return None
    kc = sig["kc_upper"][-1]
    close = sig["close"][-1]
    rvol = sig["rvol"][-1]
    if not _ok(kc) or not _ok(close) or not (close > kc):
        return None
    ent = cfg["entries"]
    reasons = []
    rvol_ok = _ok(rvol) and rvol >= ent["e5_rvol"]
    if not rvol_ok:
        reasons.append(f"量比需要 ≥ {ent['e5_rvol']}")
    regime_ok = regime == "GREEN"
    if not regime_ok:
        reasons.append("挤压释放只在进攻环境做")
    horizon = cfg["horizon"]["E5"]
    atr = sig["atr14"][-1]
    entry_u = float(close)
    atr_stop = entry_u - cfg["stops"]["e5_atr_mult"] * atr if _ok(atr) else None
    under_stop = _stop_on_underlying(None, atr_stop, entry_u, atr, horizon, cfg, leverage)
    entry, stop = _apply_leverage_stop(entry_u, under_stop, trade, horizon, cfg, leverage) if under_stop else (None, None)
    if stop is None:
        reasons.append("止损距离不在允许区间")
    technical = rvol_ok and stop is not None
    return {
        "id": "E5",
        "ok": gate_open and technical and regime_ok,
        "technical": technical,
        "pending_rs": ent["leader_swing_rs"],
        "pending_sector": True,
        "reason": "；".join(reasons),
        "entry": entry,
        "stop": stop,
        "pivot": _f(kc),
        "time_stop": cfg["account"]["swing_time_stop_bars"],
        "blurb": f"波动挤压后收盘突破上轨 {float(kc):.2f}",
        "facts": [
            _fact("挤压", True, "前一根 K 线处于布林带收进肯特纳通道的挤压"),
            _fact("突破", True, f"收盘 {entry_u:.2f} 高于肯特纳上轨 {float(kc):.2f}"),
            _fact("量比", rvol_ok, f"量比 {rvol:.2f}，需要 ≥ {ent['e5_rvol']}" if _ok(rvol) else "量比缺失"),
            _fact("环境", regime_ok, "进攻环境" if regime_ok else "不是进攻环境，这条只观察"),
        ],
    }


def finalize_leaders(tape: dict, rs_rank, cfg) -> dict:
    """RS 排名算完后补上 leader 条件。pending_rs 不达标则把 ok 打回。"""
    ent = cfg["entries"]
    sector = tape.get("sector_rs_ok")
    weekly = tape.get("weekly_ok")
    st2 = tape.get("stage2")
    close = tape.get("close")
    ema21 = tape.get("sig_levels", {}).get("ema21")
    leader_pos = None
    if None not in (st2, weekly, rs_rank, sector):
        leader_pos = bool(st2 and weekly and rs_rank >= ent["leader_pos_rs"] and sector)
    elif st2 is None or weekly is None or rs_rank is None or sector is None:
        leader_pos = None
    leader_swing = None
    if rs_rank is None or sector is None or not _ok(close) or not _ok(ema21):
        leader_swing = None
    else:
        leader_swing = bool(close > ema21 and rs_rank >= ent["leader_swing_rs"] and sector)
    tape["leader_pos"] = leader_pos
    tape["leader_swing"] = leader_swing
    tape["rs_rank"] = rs_rank
    for cand in tape.get("candidates") or []:
        need = cand.get("pending_rs")
        if need is not None:
            if rs_rank is None:
                cand["ok"] = False
                cand["reason"] = (cand.get("reason") or "") + "；RS 排名缺失"
            elif rs_rank < need:
                cand["ok"] = False
                extra = f"RS 排名 {rs_rank}，需要 ≥ {need}"
                cand["reason"] = (cand["reason"] + "；" + extra) if cand.get("reason") else extra
        if cand.get("pending_sector") and sector is not True:
            cand["ok"] = False
            extra = "行业相对强度不合格"
            cand["reason"] = (cand["reason"] + "；" + extra) if cand.get("reason") else extra
        if cand["id"] == "E1" and leader_pos is not True:
            cand["ok"] = False
            if leader_pos is None and "RS" not in (cand.get("reason") or ""):
                cand["reason"] = ((cand.get("reason") or "") + "；领涨条件数据不全").strip("；")
        if cand["id"] == "E5" and leader_swing is not True:
            cand["ok"] = False
            if not cand.get("reason"):
                cand["reason"] = "不是短线领涨（收盘需在 21 日 EMA 上，RS ≥ 70，行业强于 SPY）"
    ok_ones = [c for c in tape.get("candidates") or [] if c.get("ok")]
    ok_ones.sort(key=lambda cnd: cfg["priority"].index(cnd["id"]) if cnd["id"] in cfg["priority"] else 99)
    primary = ok_ones[0] if ok_ones else None
    tape["primary_id"] = primary["id"] if primary else None
    tape["entry"] = primary["entry"] if primary else None
    tape["stop"] = primary["stop"] if primary else None
    tape["pivot"] = primary.get("pivot") if primary else tape.get("pivot")
    tape["horizon"] = cfg["horizon"].get(primary["id"]) if primary else None
    tape["initial_fraction"] = cfg["initial_fraction"].get(primary["id"]) if primary else None
    if primary and primary["id"] == "E5":
        tape["time_stop"] = cfg["account"]["swing_time_stop_bars"]
    return tape


def tape_exits(sig: dict, rs_rank, rs_line, flat_market, cfg) -> list[str]:
    """不依赖持仓成本的离场条件，供持仓页引用。X1/X5/X7 需要账户数据，不在这里。"""
    exits = []
    close = sig["close"][-1]
    # X3
    if flat_market is not True:
        if rs_rank is not None and rs_rank >= cfg["exits"]["trail_rs_rank"]:
            line = sig["ema10"][-1]
            name = "X3"
            if _ok(close) and _ok(line) and close < line:
                exits.append(name)
        else:
            line = sig["ema21"][-1]
            if _ok(close) and _ok(line) and close < line:
                exits.append("X3")
        ch = sig["chandelier"][-1]
        if _ok(close) and _ok(ch) and close < ch:
            if "X3" not in exits:
                exits.append("X3")
    # X4 SMA
    if flat_market is not True and _ok(close) and _ok(sig["sma50"][-1]) and _ok(sig["rvol"][-1]):
        if close < sig["sma50"][-1] and sig["rvol"][-1] >= cfg["exits"]["x4_rvol"]:
            exits.append("X4")
    avwap = sig.get("avwap_earn")
    if _ok(close) and _ok(avwap) and close < avwap:
        if "X4" not in exits:
            exits.append("X4")
    # X6 climax
    ex = cfg["exits"]
    climax = False
    if _ok(close) and _ok(sig["sma50"][-1]) and close > ex["x6_extension"] * sig["sma50"][-1]:
        climax = True
    if len(close) >= ex["x6_gap_days"] + 1:
        gaps = 0
        for k in range(1, ex["x6_gap_days"] + 1):
            if _ok(sig["open"][-k]) and _ok(close[-k - 1]) and sig["open"][-k] > close[-k - 1]:
                gaps += 1
        if gaps >= ex["x6_gap_days"]:
            climax = True
    rsi = sig["rsi"][-1]
    rvol = sig["rvol"][-1]
    if _ok(rsi) and rsi > ex["x6_rsi"] and _ok(rvol) and rvol >= ex["x6_rvol"]:
        if _ok(close) and _ok(sig["open"][-1]) and close - sig["open"][-1] <= 0:
            climax = True
    if climax:
        exits.append("X6")
    # X8
    if rs_line is not None and len(rs_line) >= ex["x8_rs_low_bars"] and len(close) > ex["x8_flat_bars"]:
        window = rs_line[-ex["x8_rs_low_bars"] :]
        if np.all(np.isfinite(window)) and window[-1] <= np.min(window) * 1.001:
            base = close[-1 - ex["x8_flat_bars"]]
            if _ok(base) and base != 0 and abs(close[-1] / base - 1) < ex["x8_flat_abs"]:
                exits.append("X8")
    hh = sig["hhv252"][-1]
    if _ok(hh) and hh > 0 and _ok(close) and close >= ex["x8_near_high"] * hh:
        count = 0
        start = max(1, len(close) - ex["x8_dist_lookback"])
        for i in range(start, len(close)):
            prev = close[i - 1]
            if not _ok(prev) or prev == 0 or not _ok(sig["rvol"][i]):
                continue
            if close[i] / prev - 1 <= ex["x8_dist_return"] and sig["rvol"][i] >= ex["x8_dist_rvol"]:
                count += 1
        if count >= ex["x8_dist_count"] and "X8" not in exits:
            exits.append("X8")
    return exits
