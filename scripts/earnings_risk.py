"""持仓财报风险：下一次财报、近 8 次跳空、能取到的平值跨式。观察列表只带日期。"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pandas as pd


def _date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def call_delta(spot, strike, t_years, iv, rate=0.04):
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv is None or iv <= 0:
        return None
    sigma = float(iv)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t_years) / (sigma * math.sqrt(t_years))
    return _norm_cdf(d1)


def _session_from_ts(ts):
    if ts is None:
        return None
    hour = getattr(ts, "hour", None)
    if hour is None:
        return None
    if hour >= 16:
        return "after"
    if 6 <= hour < 12:
        return "before"
    return None


def _session_cn(session):
    if session == "before":
        return "盘前"
    if session == "after":
        return "盘后"
    return "时段未标明"


def _history(frame):
    if frame is None or frame.empty:
        return []
    data = frame
    if isinstance(frame.columns, pd.MultiIndex):
        data = frame.copy()
        data.columns = data.columns.get_level_values(0)
    bars = []
    for ts, row in data.iterrows():
        try:
            day = pd.Timestamp(ts).date()
            bars.append({
                "date": day,
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
            })
        except (TypeError, ValueError, KeyError):
            continue
    bars.sort(key=lambda item: item["date"])
    return [bar for bar in bars if all(math.isfinite(bar[k]) and bar[k] > 0 for k in ("o", "h", "l", "c"))]


def _reaction(bars, event_day, session):
    idx = next((i for i, bar in enumerate(bars) if bar["date"] >= event_day), None)
    if idx is None:
        return None
    if session == "after":
        if bars[idx]["date"] == event_day:
            prev_i = idx
            react_i = idx + 1
        else:
            prev_i = idx - 1
            react_i = idx
    else:
        react_i = idx
        prev_i = idx - 1
    if prev_i < 0 or react_i >= len(bars):
        return None
    prev = bars[prev_i]["c"]
    react = bars[react_i]
    move = react["c"] / prev - 1.0
    adverse = min(0.0, react["o"] / prev - 1.0, react["l"] / prev - 1.0)
    return {"date": str(event_day), "move": move, "adverse": adverse}


def gap_stats(bars, events):
    """events: [{date, session}] 从新到旧。adverse 对多头是下跌。"""
    samples = []
    for event in events:
        day = _date(event.get("date"))
        if day is None:
            continue
        got = _reaction(bars, day, event.get("session"))
        if got:
            samples.append(got)
        if len(samples) >= 8:
            break
    if not samples:
        return {"n": 0, "avg_abs": None, "max_adverse": None, "samples": []}
    return {
        "n": len(samples),
        "avg_abs": sum(abs(item["move"]) for item in samples) / len(samples),
        "max_adverse": min(item["adverse"] for item in samples),
        "samples": samples,
    }


def _mid(row):
    bid = float(row.get("bid") or 0)
    ask = float(row.get("ask") or 0)
    last = float(row.get("lastPrice") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last > 0:
        return last
    return None


def _chain_row(table, strike):
    if table is None or table.empty:
        return None
    gap = (table["strike"] - float(strike)).abs()
    picked = table.loc[gap.idxmin()]
    if abs(float(picked["strike"]) - float(strike)) > 0.02 and abs(float(picked["strike"]) - float(strike)) / float(strike) > 0.002:
        return None
    return picked


def implied_move(ticker_obj, spot, after_day):
    try:
        expiries = [date.fromisoformat(str(item)[:10]) for item in (ticker_obj.options or [])]
    except Exception:
        return None
    future = sorted(day for day in expiries if after_day is None or day >= after_day)
    if not future:
        return None
    expiry = future[0]
    try:
        chain = ticker_obj.option_chain(expiry.isoformat())
    except Exception:
        return None
    calls = chain.calls
    puts = chain.puts
    if calls is None or calls.empty or puts is None or puts.empty or not spot:
        return None
    strike = float(calls.iloc[(calls["strike"] - spot).abs().argmin()]["strike"])
    call = _chain_row(calls, strike)
    put = _chain_row(puts, strike)
    if call is None or put is None:
        return None
    call_mid = _mid(call)
    put_mid = _mid(put)
    if call_mid is None or put_mid is None or call_mid + put_mid <= 0:
        return None
    return {
        "expiry": expiry.isoformat(),
        "strike": strike,
        "straddle": call_mid + put_mid,
        "move": (call_mid + put_mid) / spot,
        "source": "平值跨式买卖中间价",
    }


def contract_delta(ticker_obj, spot, expiry, strike, right, bar_date, rate=0.04):
    expiry_day = _date(expiry)
    if expiry_day is None or not spot:
        return None
    try:
        chain = ticker_obj.option_chain(expiry_day.isoformat())
    except Exception as exc:
        return {"delta": None, "note": f"期权链没有取到：{exc}"}
    table = chain.calls if str(right).upper().startswith("C") else chain.puts
    row = _chain_row(table, strike)
    if row is None:
        return {"delta": None, "note": "行权价不在这次的期权链上，敞口只按权利金计。"}
    iv = row.get("impliedVolatility")
    try:
        iv = float(iv)
    except (TypeError, ValueError):
        iv = None
    if iv is None or not math.isfinite(iv) or iv <= 0:
        return {"delta": None, "note": "链上没有隐含波动，敞口只按权利金计。"}
    t_years = max(1.0, (expiry_day - bar_date).days) / 365.25
    delta = call_delta(spot, float(strike), t_years, iv, rate) if str(right).upper().startswith("C") else None
    if delta is not None and str(right).upper().startswith("P"):
        delta = delta - 1
    if delta is None:
        return {"delta": None, "note": "隐含波动有了，但 delta 没有算出来。敞口只按权利金计。"}
    return {
        "delta": delta,
        "iv": iv,
        "note": f"delta {delta:.2f} 用期权链隐含波动 {iv:.0%} 和 4% 利率近似，不是券商希腊值。",
    }


def _past_events(frame, today):
    events = []
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return events
    for ts, _row in frame.iterrows():
        day = pd.Timestamp(ts).date()
        if day >= today:
            continue
        events.append({"date": day, "session": _session_from_ts(pd.Timestamp(ts))})
    events.sort(key=lambda item: item["date"], reverse=True)
    return events


def _loss(exposure, move):
    if exposure is None or move is None:
        return None
    return abs(float(exposure)) * abs(float(move))


def _flags(loss, nlv):
    if loss is None or not nlv:
        return []
    ratio = loss / nlv
    flags = []
    for level, label in ((0.03, "-3%"), (0.05, "-5%"), (0.10, "-10%")):
        if ratio + 1e-12 >= level:
            flags.append({"level": level, "label": label})
    return flags


def build(port, rows, seed, rules, bar_date, fetch=True):
    import yfinance as yf

    bar_date = _date(bar_date) or date.today()
    seed_of = {}
    for item in (seed or {}).get("items") or []:
        seed_of[str(item.get("ticker") or "").upper()] = item
    held = []
    for pos in port.get("positions") or []:
        if pos.get("instrument") == "option":
            continue
        held.append(pos["ticker"])
    row_of = {row["ticker"]: row for row in rows}
    nlv = float(port.get("nlv_basis") or port.get("nlv_estimated") or 0)
    no_earnings = set()
    histories = {}
    if fetch and held:
        try:
            histories = yf.download(held, period="3y", interval="1d", auto_adjust=True, progress=False, threads=False, group_by="ticker")
        except Exception:
            histories = {}
    items = []
    deltas = {}
    for ticker in held:
        row = row_of.get(ticker) or {}
        pos = next(p for p in port["positions"] if p.get("instrument") != "option" and p["ticker"] == ticker)
        seeded = seed_of.get(ticker) or {}
        note = seeded.get("note") or row.get("earnings_note")
        etf = ticker in (row.get("no_earnings") or []) or (row.get("earnings_source") == "etf") or "ETF" in str(note or "")
        next_day = _date(seeded.get("date")) or _date(row.get("earnings_date"))
        session = seeded.get("session")
        confirmed = bool(seeded.get("confirmed")) if seeded.get("date") else bool(row.get("earnings_confirmed"))
        source = "已核对" if seeded.get("date") else (row.get("earnings_source") or "")
        bars = []
        past = []
        live_note = ""
        if fetch and not etf:
            try:
                ticker_obj = yf.Ticker(ticker)
                past = _past_events(ticker_obj.get_earnings_dates(limit=16), bar_date)
            except Exception as exc:
                ticker_obj = None
                live_note = f"历史财报日没有取到：{exc}"
            else:
                frame = None
                if isinstance(histories, pd.DataFrame) and not isinstance(histories.columns, pd.MultiIndex):
                    frame = histories
                elif isinstance(histories, pd.DataFrame) and ticker in histories.columns.get_level_values(0):
                    frame = histories[ticker]
                bars = _history(frame)
                if next_day is None:
                    try:
                        cal = ticker_obj.calendar
                        dates = (cal or {}).get("Earnings Date") if isinstance(cal, dict) else None
                        if dates:
                            nxt = _date(dates[0])
                            if nxt and nxt >= bar_date:
                                next_day = nxt
                                source = source or "yfinance"
                    except Exception:
                        pass
        else:
            ticker_obj = None
        stats = gap_stats(bars, past) if bars else {"n": 0, "avg_abs": None, "max_adverse": None, "samples": []}
        implied = None
        if fetch and ticker_obj is not None and next_day and pos.get("price"):
            implied = implied_move(ticker_obj, float(pos["price"]), next_day)
        exposure = float(pos["market_value"] or 0) if pos.get("market_value") is not None else None
        move_imp = implied["move"] if implied else None
        move_hist = abs(stats["max_adverse"]) if stats.get("max_adverse") is not None else None
        loss_imp = _loss(exposure, move_imp)
        loss_hist = _loss(exposure, move_hist)
        days = (next_day - bar_date).days if next_day else None
        items.append({
            "ticker": ticker,
            "held": True,
            "etf": etf,
            "date": next_day.isoformat() if next_day else None,
            "session": session,
            "session_cn": _session_cn(session),
            "confirmed": confirmed,
            "days": days,
            "note": note or live_note,
            "source": source,
            "price": pos.get("price"),
            "exposure": exposure,
            "history": {"n": stats["n"], "avg_abs": stats["avg_abs"], "max_adverse": stats["max_adverse"]},
            "implied": implied,
            "loss_implied": loss_imp,
            "loss_implied_pct": (loss_imp / nlv) if loss_imp is not None and nlv else None,
            "loss_adverse": loss_hist,
            "loss_adverse_pct": (loss_hist / nlv) if loss_hist is not None and nlv else None,
            "flags_implied": _flags(loss_imp, nlv),
            "flags_adverse": _flags(loss_hist, nlv),
        })
    for pos in port.get("positions") or []:
        if pos.get("instrument") != "option":
            continue
        underlying = pos.get("underlying") or pos["ticker"]
        spot = next((p.get("price") for p in port["positions"] if p.get("instrument") != "option" and p["ticker"] == underlying), None)
        key = f"{underlying}:{pos.get('expiry')}:{pos.get('strike')}:{pos.get('right')}"
        if not fetch or not spot:
            deltas[key] = {"delta": None, "note": "没有正股价格，delta 没算。"}
            continue
        try:
            deltas[key] = contract_delta(yf.Ticker(underlying), float(spot), pos.get("expiry"), pos.get("strike"), pos.get("right"), bar_date)
        except Exception as exc:
            deltas[key] = {"delta": None, "note": f"delta 没算成：{exc}"}
    watch = []
    held_set = set(held)
    for row in rows:
        if row["ticker"] in held_set:
            continue
        if not row.get("earnings_date") and not row.get("earnings_note"):
            continue
        days = row.get("earnings_in_days")
        if row.get("earnings_date") and days is not None and days > 45:
            continue
        if not row.get("held") and not row.get("earnings_date"):
            continue
        watch.append({
            "ticker": row["ticker"],
            "held": False,
            "date": row.get("earnings_date"),
            "days": days,
            "confirmed": row.get("earnings_confirmed"),
            "note": row.get("earnings_note") or row.get("earnings_source") or "",
        })
    watch.sort(key=lambda item: (item.get("date") is None, item.get("date") or "9999"))
    cluster_start = date(bar_date.year, 10, 15) if bar_date.month <= 10 else None
    cluster_end = date(bar_date.year, 10, 29) if cluster_start else None
    if bar_date.month == 10 and cluster_start and bar_date > cluster_end:
        cluster_start = None
    cluster_items = []
    if cluster_start:
        cluster_items = [
            item for item in items
            if item.get("date") and cluster_start <= _date(item["date"]) <= cluster_end and not item.get("etf")
        ]
    def _sum(key):
        vals = [item[key] for item in cluster_items if item.get(key) is not None]
        return sum(vals) if vals else None
    cluster_loss_i = _sum("loss_implied")
    cluster_loss_a = _sum("loss_adverse")
    return {
        "bar_date": bar_date.isoformat(),
        "nlv": nlv,
        "holdings": items,
        "watchlist": watch,
        "deltas": deltas,
        "cluster": {
            "start": cluster_start.isoformat() if cluster_start else "2026-10-15",
            "end": cluster_end.isoformat() if cluster_end else "2026-10-29",
            "tickers": [item["ticker"] for item in cluster_items],
            "loss_implied": cluster_loss_i,
            "loss_implied_pct": (cluster_loss_i / nlv) if cluster_loss_i is not None and nlv else None,
            "loss_adverse": cluster_loss_a,
            "loss_adverse_pct": (cluster_loss_a / nlv) if cluster_loss_a is not None and nlv else None,
            "flags_implied": _flags(cluster_loss_i, nlv),
            "flags_adverse": _flags(cluster_loss_a, nlv),
            "note": "同一窗口里的潜在亏损相加，表示若这些跳空同向。不是预测它们会一起发生。",
        },
        "breakers": [0.03, 0.05, 0.10],
        "note": "跳空用财报日前后的收盘和最低价。隐含波动是财报后最近到期日的平值跨式；取不到就空着，不用 15% 去填。",
    }
