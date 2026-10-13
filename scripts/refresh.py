#!/usr/bin/env python3
"""拉取免费行情，按 config/signal_rules.yaml 计算指标和信号，写出静态站数据。"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import earnings_risk as earnmod  # noqa: E402
import explain as explainmod  # noqa: E402
import indicators as indmod  # noqa: E402
import report as reportmod  # noqa: E402
import risk_plan as riskmod  # noqa: E402
import sentiment_sources as sentmod  # noqa: E402
import signals as sigmod  # noqa: E402

SH = ZoneInfo("Asia/Shanghai")
ET = ZoneInfo("America/New_York")


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def scrub(value):
    if isinstance(value, dict):
        return {str(k): scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, 6)
    if isinstance(value, pd.Timestamp):
        return str(value.date())
    if value is None or isinstance(value, (str, int)):
        return value
    return str(value)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scrub(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def parse_watchlist(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8").split()
    out = []
    seen = set()
    for token in raw:
        if token.upper() == "LIST2153":
            continue
        ticker = token.upper().replace(".", "-")
        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)
    return out


THEME_SHORT = {
    "半导体 / 存储": "半导体",
    "能源 / 电力": "能源",
    "大型科技 / 平台": "科技",
    "贵金属 / 金属": "金属",
    "金融 / 券商 / 金融科技": "金融",
    "宽基 / 风格 / 利率": "宽基",
    "消费": "消费",
    "医疗 / 生物": "医疗",
    "国防 / 航天": "国防",
    "加密相关": "加密",
    "量子计算": "量子",
    "中概 / 海外": "中概",
    "不动产": "地产",
}


def write_themes(universe: dict | None = None):
    if universe is None:
        universe = load_yaml(ROOT / "config" / "universe.yaml") or {}
    groups = []
    for key, theme in (universe.get("themes") or {}).items():
        name = theme.get("name") or key
        short = THEME_SHORT.get(name) or str(name).split("/")[0].split("／")[0].strip()
        groups.append({"id": key, "name": name, "short": short})
    write_json(ROOT / "site" / "data" / "themes.json", {"groups": groups})


def theme_map(universe: dict) -> dict:
    mapping = {}
    for key, theme in (universe.get("themes") or {}).items():
        for ticker in theme.get("tickers") or []:
            mapping[ticker.replace(".", "-")] = {
                "id": key,
                "name": theme.get("name") or key,
                "sector_etf": theme.get("sector_etf"),
                "sector_mode": theme.get("sector_mode"),
            }
    return mapping


def normalize(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    frame = df.copy()
    frame.columns = [str(col).title() for col in frame.columns]
    if "Close" not in frame.columns and "Adj Close" in frame.columns:
        frame = frame.rename(columns={"Adj Close": "Close"})
    need = ["Open", "High", "Low", "Close", "Volume"]
    if any(col not in frame.columns for col in need):
        return None
    frame = frame[need].apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    if frame.empty:
        return None
    idx = pd.to_datetime(frame.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    frame.index = pd.DatetimeIndex(idx).normalize()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame["Volume"] = frame["Volume"].fillna(0.0)
    return frame


def drop_partial(frame: pd.DataFrame, now_et: datetime) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    last = frame.index[-1].date()
    if last == now_et.date() and (now_et.hour, now_et.minute) < (16, 15):
        return frame.iloc[:-1]
    return frame


def frames_from_download(df: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    if df is None or len(df) == 0:
        return out
    if not isinstance(df.columns, pd.MultiIndex):
        if len(tickers) == 1:
            norm = normalize(df)
            if norm is not None:
                out[tickers[0]] = norm
        return out
    values = {level: set(map(str, df.columns.get_level_values(level))) for level in range(df.columns.nlevels)}
    wanted = set(tickers)
    level = next((lvl for lvl, vals in values.items() if vals & wanted), None)
    if level is None:
        return out
    have = set(df.columns.get_level_values(level))
    for ticker in tickers:
        if ticker not in have:
            continue
        try:
            sub = df.xs(ticker, axis=1, level=level)
        except Exception:
            continue
        norm = normalize(sub)
        if norm is not None and len(norm) >= 30:
            out[ticker] = norm
    return out


def download(symbols: list[str], now_et: datetime) -> tuple[dict[str, pd.DataFrame], list[str]]:
    import yfinance as yf

    frames: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    unique = list(dict.fromkeys(symbols))
    for i in range(0, len(unique), 25):
        chunk = unique[i : i + 25]
        try:
            df = yf.download(
                chunk,
                period="2y",
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
            got = frames_from_download(df, chunk)
        except Exception as exc:
            got = {}
            errors.append(f"批次 {chunk[0]}… 失败：{exc}")
        for ticker in chunk:
            frame = got.get(ticker)
            if frame is None:
                try:
                    single = yf.download(ticker, period="2y", interval="1d", auto_adjust=True, progress=False)
                    frame = normalize(single)
                except Exception as exc:
                    errors.append(f"{ticker}：{exc}")
                    continue
            if frame is None or len(frame) < 30:
                errors.append(f"{ticker}：没有足够的日线")
                continue
            frames[ticker] = drop_partial(frame, now_et)
        time.sleep(0.2)
    fill_missing_session(frames, now_et)
    return frames, errors


def _chart_session_bar(ticker: str, day):
    """日线 Close 还是空的时候，用图表接口里已经公布的收盘报价。"""
    import urllib.parse

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(ticker, safe="")
        + "?interval=1d&range=5d"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode())
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return None
    block = result[0]
    meta = block.get("meta") or {}
    raw_time = meta.get("regularMarketTime")
    price = meta.get("regularMarketPrice")
    if raw_time is None or price is None:
        return None
    if datetime.fromtimestamp(int(raw_time), ET).date() != day:
        return None
    price = float(price)
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    open_ = high = low = volume = None
    for i, raw in enumerate(block.get("timestamp") or []):
        if datetime.fromtimestamp(int(raw), ET).date() != day:
            continue
        def at(key, index=i):
            seq = quote.get(key) or []
            if index >= len(seq) or seq[index] is None:
                return None
            return float(seq[index])
        open_, high, low, volume = at("open"), at("high"), at("low"), at("volume")
    if open_ is None:
        open_ = price
    if high is None:
        high = max(open_, price)
    if low is None:
        low = min(open_, price)
    high = max(high, open_, price)
    low = min(low, open_, price)
    if volume is None and meta.get("regularMarketVolume") is not None:
        volume = float(meta["regularMarketVolume"])
    if volume is None:
        volume = 0.0
    return {"o": open_, "h": high, "l": low, "c": price, "v": volume}


def fill_missing_session(frames: dict, now_et: datetime) -> int:
    """收盘已过，但 Yahoo 日线的 Close 仍是空值时，补上当天这根。"""
    if now_et.weekday() >= 5 or (now_et.hour, now_et.minute) < (16, 15):
        return 0
    day = now_et.date()
    needed = [
        ticker for ticker, frame in frames.items()
        if frame is not None and not frame.empty and frame.index[-1].date() < day
    ]
    if not needed:
        return 0

    def job(ticker):
        try:
            return ticker, _chart_session_bar(ticker, day)
        except Exception:
            return ticker, None

    filled = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for ticker, bar in pool.map(job, needed):
            if not bar:
                continue
            frame = frames[ticker]
            frame.loc[pd.Timestamp(day), ["Open", "High", "Low", "Close", "Volume"]] = [
                bar["o"], bar["h"], bar["l"], bar["c"], bar["v"],
            ]
            frames[ticker] = frame.sort_index()
            filled += 1
    print(f"日线收盘字段未齐，用收盘报价补上 {filled}/{len(needed)} 只。", flush=True)
    return filled


def fetch_earnings_one(ticker: str):
    import yfinance as yf

    try:
        cal = yf.Ticker(ticker).calendar
    except Exception:
        cal = None
    if isinstance(cal, dict):
        dates = cal.get("Earnings Date") or []
        if dates:
            return dates[0]
    return None


def as_date(value):
    if value is None:
        return None
    if hasattr(value, "date") and not isinstance(value, datetime):
        try:
            return value.date() if not isinstance(value, type(datetime.now(ET).date())) else value
        except Exception:
            pass
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York").tz_localize(None)
    return ts.date()


def option_mark(underlying: str, expiry: str, strike: float, right: str):
    import yfinance as yf

    try:
        ticker = yf.Ticker(underlying)
        expiries = list(ticker.options or [])
        if expiry not in expiries:
            return None, "到期日不在期权链中"
        chain = ticker.option_chain(expiry)
        table = chain.calls if str(right).upper() == "C" else chain.puts
        gap = (table["strike"] - float(strike)).abs()
        row = table.loc[gap < 0.011]
        if row.empty:
            return None, "行权价不在链上"
        quote = row.iloc[0]
        bid = float(quote.get("bid") or 0)
        ask = float(quote.get("ask") or 0)
        last = float(quote.get("lastPrice") or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0, "买卖中间价"
        if last > 0:
            return last, "最近成交"
        return None, "链上没有报价"
    except Exception as exc:
        return None, f"期权链失败：{exc}"


def change(close: np.ndarray, bars: int):
    if close is None or len(close) <= bars:
        return None
    prev = close[-1 - bars]
    if not sigmod._ok(prev) or prev == 0 or not sigmod._ok(close[-1]):
        return None
    return float(close[-1] / prev - 1.0)


def dist(price, line):
    if not sigmod._ok(price) or not sigmod._ok(line) or line == 0:
        return None
    return float(price / line - 1.0)


def return_ratio(stock: np.ndarray, spy: np.ndarray, bars: int):
    if stock is None or spy is None or len(stock) <= bars or len(spy) <= bars:
        return None
    if not all(sigmod._ok(x) and x != 0 for x in (stock[-1], stock[-1 - bars], spy[-1], spy[-1 - bars])):
        return None
    return float((stock[-1] / stock[-1 - bars]) / (spy[-1] / spy[-1 - bars]))


def quote_block(frame: pd.DataFrame, spec: dict, spy: pd.DataFrame | None, rules: dict):
    ind = indmod.compute(frame, rules)
    close = ind["close"]
    price = float(close[-1])
    kind = spec.get("kind")
    if kind == "yield" and price > 20:
        # 旧版 Yahoo 把收益率乘过 10。现在的序列已经是百分数，这里只防回归。
        price = price / 10.0
        close = close / 10.0
    item = {
        "symbol": spec["symbol"],
        "name": spec["name"],
        "group": spec["group"],
        "kind": kind or "price",
        "price": price,
        "change_1d": change(close, 1),
        "change_5d": change(close, 5),
        "change_1m": change(close, 21),
        "sma50": sigmod._f(ind["sma50"][-1]),
        "sma200": sigmod._f(ind["sma200"][-1]),
        "above_50": bool(price > ind["sma50"][-1]) if sigmod._ok(ind["sma50"][-1]) else None,
        "above_200": bool(price > ind["sma200"][-1]) if sigmod._ok(ind["sma200"][-1]) else None,
        "date": str(frame.index[-1].date()),
    }
    if spy is not None and kind != "yield":
        joined = frame["Close"].to_frame("c").join(spy["Close"].rename("s"), how="inner")
        item["rs_1m"] = return_ratio(joined["c"].to_numpy(), joined["s"].to_numpy(), 21)
        item["rs_3m"] = return_ratio(joined["c"].to_numpy(), joined["s"].to_numpy(), 63)
    return item


def render_docs():
    import markdown

    converter = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    out_dir = ROOT / "site" / "content"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("01_profile.md", "02_framework.md"):
        text = (ROOT / "content" / name).read_text(encoding="utf-8")
        html = converter.convert(text)
        converter.reset()
        (out_dir / name.replace(".md", ".html")).write_text(html, encoding="utf-8")
        (out_dir / name).write_text(text, encoding="utf-8")


def earnings_gap_info(ind: dict, earn_date, rules: dict):
    if earn_date is None:
        return None
    dates = list(ind["dates"])
    if earn_date > dates[-1]:
        return None
    idx = next((i for i, d in enumerate(dates) if d >= earn_date), None)
    ent = rules["entries"]
    if idx is None or idx < 1:
        return {"matched": False, "reason": "财报日早于现有日线或不在样本内"}
    today = len(dates) - 1
    gap_i = None
    for j in {idx, min(idx + 1, today)}:
        if j < 1:
            continue
        prev = ind["close"][j - 1]
        high, low = ind["high"][j], ind["low"][j]
        if not sigmod._ok(prev) or prev == 0 or not sigmod._ok(ind["open"][j]) or not sigmod._ok(ind["rvol"][j]):
            continue
        gap = ind["open"][j] / prev - 1.0
        loc = (ind["close"][j] - low) / (high - low) if sigmod._ok(high) and sigmod._ok(low) and high > low else 0
        if gap >= ent["e4_gap"] and ind["rvol"][j] >= ent["e4_rvol"] and loc >= ent["e4_close_location"]:
            gap_i = j
            break
    if gap_i is None:
        return {"matched": False, "reason": "财报日附近没有同时满足跳空、RVOL 和收盘位置的 K 线"}
    if today < gap_i or today - gap_i > ent["e4_window_bars"]:
        return {"matched": False, "reason": "财报跳空已超出 10 根 K 线的入场窗口"}
    av = ind.get("avwap_earn")
    if not sigmod._ok(av) or ind["close"][-1] <= av or ind["low"][-1] < ind["low"][gap_i]:
        return {"matched": False, "reason": "收盘未站上财报 AVWAP，或最低价跌破跳空当天低点"}
    return {"matched": True, "gap_low": float(ind["low"][gap_i]), "pivot": float(ind["high"][gap_i])}


def _bars_after(dates, start) -> int | None:
    if start is None:
        return None
    day = as_date(start)
    return sum(1 for d in dates if d > day)


def _latest_e1_entry(trades, ticker):
    rows = [
        t for t in trades
        if t.get("ticker") == ticker and t.get("action") == "entry" and t.get("signal") == "E1" and t.get("date")
    ]
    if not rows:
        return None
    rows.sort(key=lambda t: t["date"])
    return rows[-1]


def holding_exits(row: dict, pos: dict, ind: dict, nlv: float, rules: dict, earnings_blk, implied: float, trades):
    """按持仓期限评估离场和加仓。缺输入的规则记为未定，不写入 exits。"""
    exits = []
    notes = []
    pending = []
    add = None
    close = ind["close"][-1]
    flat = row.get("flat_market") is True
    horizon = pos.get("horizon") or "position"
    ex = rules["exits"]
    dates = ind.get("dates") or []
    if pos.get("stop") is None or not sigmod._ok(ind["low"][-1]):
        pending.append("X1")
    elif ind["low"][-1] <= float(pos["stop"]):
        exits.append("X1")
    entry_e1 = _latest_e1_entry(trades, pos["ticker"])
    if entry_e1 is None or entry_e1.get("pivot") is None or entry_e1.get("breakout_low") is None:
        pending.append("X2")
    else:
        held = _bars_after(dates, entry_e1["date"])
        if held is not None and held <= ex["fail_breakout_bars"] and sigmod._ok(close):
            rvol = ind["rvol"][-1]
            full = close < float(entry_e1["breakout_low"])
            half = sigmod._ok(rvol) and close < float(entry_e1["pivot"]) and rvol >= ex["fail_rvol"]
            if full:
                exits.append("X2")
                notes.append("X2：收盘跌破突破日最低价，规则要求全部离场。")
            elif half:
                exits.append("X2")
                notes.append("X2：突破后 3 根 K 线内收盘跌回 pivot 之下且放量，减一半。")
    if horizon == "swing" and not flat:
        line = ind["ema10"][-1] if (row.get("rs_rank") or 0) >= ex["trail_rs_rank"] else ind["ema21"][-1]
        if sigmod._ok(close) and sigmod._ok(line) and close < line:
            exits.append("X3")
    if horizon == "position" and sigmod._ok(close) and sigmod._ok(ind["chandelier"][-1]) and close < ind["chandelier"][-1]:
        exits.append("X3")
    if not flat and sigmod._ok(close) and sigmod._ok(ind["sma50"][-1]) and sigmod._ok(ind["rvol"][-1]):
        if close < ind["sma50"][-1] and ind["rvol"][-1] >= ex["x4_rvol"]:
            exits.append("X4")
    av = ind.get("avwap_earn")
    if sigmod._ok(av) and sigmod._ok(close) and close < av and "X4" not in exits:
        exits.append("X4")
    if row.get("x6"):
        exits.append("X6")
    if row.get("x8") and "X8" not in exits:
        exits.append("X8")
    if earnings_blk and pos.get("instrument") == "stock" and sigmod._ok(close) and nlv:
        mark = float(pos["avg_cost"]) if pos.get("avg_cost") else float(close)
        loss = abs(float(pos["shares"])) * mark * rules["account"]["implied_move_stress"] * implied
        if loss > rules["account"]["earnings_gap_loss_cap"] * nlv:
            exits.append("X7")
            notes.append(f"按 {implied:.0%} 跳空（近似）估算的亏损约 ${loss:,.0f}，超过净值的 1%")
    if horizon == "swing":
        if pos.get("entry_date") is None or pos.get("avg_cost") is None or pos.get("stop") is None:
            pending.append("X5")
        else:
            held = _bars_after(dates, pos["entry_date"])
            entry_px = float(pos["avg_cost"])
            one_r = entry_px - float(pos["stop"])
            if held is None or one_r <= 0:
                pending.append("X5")
            else:
                start = as_date(pos["entry_date"])
                highs = [float(ind["high"][i]) for i, d in enumerate(dates) if d >= start and sigmod._ok(ind["high"][i])]
                favorable = (max(highs) - entry_px) if highs else None
                limit = rules["account"]["swing_time_stop_bars"]
                if favorable is None:
                    pending.append("X5")
                elif held >= limit and favorable < one_r:
                    exits.append("X5")
                    notes.append(f"X5：短线已持有 {held} 根 K 线，最大有利波动还不到 1R。")
    entry_px = pos.get("avg_cost")
    stop_px = pos.get("stop")
    if entry_px is None or stop_px is None or not sigmod._ok(close):
        pending.append("A1")
    else:
        entry_px = float(entry_px)
        stop_px = float(stop_px)
        one_r = entry_px - stop_px
        unreal = float(close) - entry_px
        if one_r <= 0:
            pending.append("A1")
        elif unreal >= one_r:
            e1 = any(c.get("id") == "E1" and c.get("ok") for c in (row.get("candidates") or []))
            prev_high = ind["high"][-2] if len(ind["high"]) > 1 else None
            low = ind["low"][-1]
            touched = (
                sigmod._ok(low)
                and (
                    (sigmod._ok(ind["ema10"][-1]) and low <= ind["ema10"][-1])
                    or (sigmod._ok(ind["ema21"][-1]) and low <= ind["ema21"][-1])
                )
            )
            resumed = touched and sigmod._ok(prev_high) and float(close) > float(prev_high)
            if e1 or resumed:
                initial = abs(float(pos["shares"]))
                model = int(initial * 0.5) if initial >= 2 else 0
                add = {
                    "id": "A1",
                    "shares_model": model,
                    "shares": model,
                    "detail": f"浮盈已到 1R。规则允许加仓不超过原仓位的一半（{model} 股）。",
                }
            if unreal >= one_r and stop_px < entry_px:
                notes.append(f"BE：浮盈已到 1R，把止损从 {stop_px:.2f} 抬到成本 {entry_px:.2f}。")
    if pos.get("instrument") == "option" and pos.get("contracts", 0) < 0 and pos.get("covered_by_shares"):
        notes.append("空头看涨目前被正股覆盖。卖掉正股之前要先买回期权，否则变成裸卖。")
    return exits, notes, pending, add


def x6_x8(ind: dict, rs_line, rules: dict):
    ex = rules["exits"]
    close = ind["close"]
    x6_why = []
    if sigmod._ok(close[-1]) and sigmod._ok(ind["sma50"][-1]) and close[-1] > ex["x6_extension"] * ind["sma50"][-1]:
        x6_why.append(f"收盘 {close[-1]:.2f} 高于 50 日均线 {ind['sma50'][-1]:.2f} 的 {ex['x6_extension']:.2f} 倍")
    gaps = 0
    if len(close) > ex["x6_gap_days"]:
        for k in range(1, ex["x6_gap_days"] + 1):
            if sigmod._ok(ind["open"][-k]) and sigmod._ok(close[-k - 1]) and ind["open"][-k] > close[-k - 1]:
                gaps += 1
        if gaps >= ex["x6_gap_days"]:
            x6_why.append(f"连续 {gaps} 根跳空高开")
    if sigmod._ok(ind["rsi"][-1]) and ind["rsi"][-1] > ex["x6_rsi"] and sigmod._ok(ind["rvol"][-1]) and ind["rvol"][-1] >= ex["x6_rvol"]:
        if sigmod._ok(close[-1]) and sigmod._ok(ind["open"][-1]) and close[-1] - ind["open"][-1] <= 0:
            x6_why.append(f"RSI {ind['rsi'][-1]:.1f}，量比 {ind['rvol'][-1]:.2f}，收盘没有高于开盘")
    x6 = bool(x6_why)
    x8_why = []
    if rs_line is not None and len(rs_line) >= ex["x8_rs_low_bars"] and len(close) > ex["x8_flat_bars"]:
        window = rs_line[-ex["x8_rs_low_bars"] :]
        base = close[-1 - ex["x8_flat_bars"]]
        if np.all(np.isfinite(window)) and window[-1] <= np.min(window) * 1.001 and sigmod._ok(base) and base != 0:
            if abs(close[-1] / base - 1) < ex["x8_flat_abs"]:
                x8_why.append(f"相对强度处在近 {ex['x8_rs_low_bars']} 日低点，20 日涨跌 {close[-1] / base - 1:.1%}")
    hh = ind["hhv252"][-1]
    if sigmod._ok(hh) and hh > 0 and sigmod._ok(close[-1]) and close[-1] >= ex["x8_near_high"] * hh:
        count = 0
        start = max(1, len(close) - ex["x8_dist_lookback"])
        for i in range(start, len(close)):
            prev = close[i - 1]
            if not sigmod._ok(prev) or prev == 0 or not sigmod._ok(ind["rvol"][i]):
                continue
            if close[i] / prev - 1 <= ex["x8_dist_return"] and ind["rvol"][i] >= ex["x8_dist_rvol"]:
                count += 1
        if count >= ex["x8_dist_count"]:
            x8_why.append(f"靠近 52 周高点，近 {ex['x8_dist_lookback']} 根里有 {count} 个放量下跌日")
    return x6, bool(x8_why), x6_why, x8_why


def resolve_session(now_sh: datetime, requested: str) -> str:
    if requested == "auto":
        return "am" if now_sh.hour < 12 else "pm"
    return requested


def stored_bar_date() -> str | None:
    path = ROOT / "site" / "data" / "meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("bar_date")
    except (OSError, json.JSONDecodeError):
        return None


def latest_close_date(now_et: datetime):
    """只拉 SPY，看最近一根完整日线。失败时返回 None，调用方改为完整刷新。"""
    import yfinance as yf

    try:
        df = yf.download("SPY", period="6mo", interval="1d", auto_adjust=True, progress=False, threads=False)
    except Exception as exc:
        print(f"探测 SPY 收盘失败：{exc}", flush=True)
        return None
    frame = normalize(df)
    if frame is None:
        frame = frames_from_download(df, ["SPY"]).get("SPY")
    if frame is None or frame.empty:
        print("探测 SPY 收盘没有得到日线。", flush=True)
        return None
    frame = drop_partial(frame, now_et)
    if frame is None or frame.empty:
        return None
    last = frame.index[-1].date()
    if now_et.weekday() < 5 and (now_et.hour, now_et.minute) >= (16, 15) and last < now_et.date():
        try:
            bar = _chart_session_bar("SPY", now_et.date())
        except Exception as exc:
            print(f"探测 SPY 收盘报价失败：{exc}", flush=True)
            bar = None
        if bar:
            return now_et.date()
    return last


def refresh_saved_sentiment():
    """跳过全市场下载时仍更新情绪源，并改掉 meta 里过时的缺失说明。"""
    data = ROOT / "site" / "data"
    path = data / "sentiment.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    current.update(sentmod.fetch(ROOT))
    write_json(path, current)
    sentmod.patch_approximations(data / "meta.json", current)


def publish_risk(port, rows, rules, bar_date):
    """财报风险要访问行情源。失败时仍写出减仓计划，并在财报文件里写明原因。"""
    data = ROOT / "site" / "data"
    seed = load_yaml(ROOT / "data" / "earnings_seed.yaml") or {}
    try:
        earnings = earnmod.build(port, rows, seed, rules, bar_date, fetch=True)
    except Exception as exc:
        print(f"财报风险没有算成：{exc}", flush=True)
        earnings = {
            "bar_date": str(bar_date),
            "nlv": port.get("nlv_basis"),
            "holdings": [],
            "watchlist": [],
            "deltas": {},
            "cluster": {},
            "note": f"这次没有算成：{exc}",
        }
    risk = riskmod.build(port, rows, rules, earnings.get("deltas") or {}, bar_date)
    write_json(data / "earnings_risk.json", earnings)
    write_json(data / "risk.json", risk)
    return risk, earnings


def publish_report(now_sh: datetime, session: str) -> str:
    """用已经落盘的计算结果写当天的上午或下午日报。不重新下载。"""
    data = ROOT / "site" / "data"
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    meta["generated_at"] = now_sh.astimezone(timezone.utc).isoformat()
    meta["generated_at_cn"] = now_sh.strftime("%Y-%m-%d %H:%M")
    meta["generated_date_cn"] = now_sh.strftime("%Y-%m-%d")
    meta["session"] = session
    regime = json.loads((data / "regime.json").read_text(encoding="utf-8"))
    port = json.loads((data / "portfolio.json").read_text(encoding="utf-8"))
    sentiment = json.loads((data / "sentiment.json").read_text(encoding="utf-8"))
    decisions = json.loads((data / "decisions.json").read_text(encoding="utf-8"))
    rows = json.loads((data / "scanner.json").read_text(encoding="utf-8")).get("rows") or []
    risk_path = data / "risk.json"
    earn_path = data / "earnings_risk.json"
    text = reportmod.build({
        "meta": meta,
        "regime": regime,
        "portfolio": port,
        "sentiment": sentiment,
        "decisions": decisions,
        "rows": rows,
        "risk": json.loads(risk_path.read_text(encoding="utf-8")) if risk_path.exists() else {},
        "earnings_risk": json.loads(earn_path.read_text(encoding="utf-8")) if earn_path.exists() else {},
    })
    name = f"{now_sh.strftime('%Y-%m-%d')}-{session}.md"
    for folder in (ROOT / "reports", ROOT / "site" / "reports"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    index = sorted(p.name for p in (ROOT / "reports").glob("*.md"))
    write_json(data / "reports.json", {"files": index})
    write_json(data / "meta.json", meta)
    print(f"日报 {name}，行情日期 {meta.get('bar_date')}。", flush=True)
    return name


def write_sparks(frames: dict):
    sparks = {}
    for ticker, frame in frames.items():
        if frame is None or "Close" not in frame.columns:
            continue
        closes = [float(v) for v in frame["Close"].tail(30).tolist() if pd.notna(v)]
        if len(closes) < 2:
            continue
        sparks[ticker] = [round(v, 4) for v in closes]
    path = ROOT / "site" / "data" / "sparks.json"
    path.write_text(json.dumps(sparks, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="auto", choices=["auto", "am", "pm"])
    args = parser.parse_args()

    now_sh = datetime.now(SH)
    now_et = datetime.now(ET)
    session = resolve_session(now_sh, args.session)
    try:
        write_themes()
    except Exception as exc:
        print(f"分组列表没有写上：{exc}", flush=True)
    close = latest_close_date(now_et)
    stored = stored_bar_date()
    # 探测日不新于已保存的收盘就跳过。日线 Close 仍为空时，探测会停在前一天，不能把更新的数据盖掉。
    if close is not None and stored is not None and str(close) <= str(stored):
        print(f"最近收盘 {stored} 已经处理过，跳过下载。", flush=True)
        try:
            refresh_saved_sentiment()
        except Exception as exc:
            print(f"情绪源更新失败，日报沿用上次情绪：{exc}", flush=True)
        try:
            publish_report(now_sh, session)
            return
        except Exception as exc:
            print(f"只写日报失败，改为完整刷新：{exc}", flush=True)

    rules = load_yaml(ROOT / "config" / "signal_rules.yaml")
    universe = load_yaml(ROOT / "config" / "universe.yaml")
    portfolio_cfg = load_yaml(ROOT / "data" / "portfolio.yaml")
    events = load_yaml(ROOT / "data" / "events.yaml") or {}
    seed = load_yaml(ROOT / "data" / "earnings_seed.yaml") or {}
    trade_log = load_yaml(ROOT / "data" / "trade_log.yaml") or {}
    themes = theme_map(universe)
    watch = parse_watchlist(ROOT / "data" / "watchlist.txt")
    held = []
    for pos in portfolio_cfg.get("positions") or []:
        symbol = str(pos["ticker"]).replace(".", "-")
        if symbol not in held:
            held.append(symbol)
    scan = list(dict.fromkeys(watch + held))

    market_specs = universe.get("market") or []
    sector_specs = universe.get("sectors") or []
    leveraged = universe.get("leveraged") or {}
    extra = []
    for spec in market_specs + sector_specs:
        extra.append(spec["symbol"])
    for item in leveraged.values():
        extra.append(item["underlying"])
    extra += universe.get("core_symbols") or []
    for theme in (universe.get("themes") or {}).values():
        if theme.get("sector_etf"):
            extra.append(theme["sector_etf"])

    print(f"下载 {len(set(scan + extra))} 个代码…", flush=True)
    frames, errors = download(scan + extra, now_et)
    if "SPY" not in frames or "QQQ" not in frames:
        print("SPY 或 QQQ 没有数据，停止。", file=sys.stderr)
        for err in errors[:20]:
            print(err, file=sys.stderr)
        sys.exit(1)

    spy = frames["SPY"]
    bar_date = spy.index[-1].date()
    spy_dates = [ts.date() for ts in spy.index]

    print("计算指标…", flush=True)
    pack = {ticker: indmod.compute(frame, rules) | {"dates": [ts.date() for ts in frame.index]} for ticker, frame in frames.items()}

    def breadth(offset: int):
        above50 = above200 = n = 0
        for ticker in scan:
            data = pack.get(ticker)
            if data is None:
                continue
            i = len(data["close"]) - 1 - offset
            if i < 0:
                continue
            price, s50, s200 = data["close"][i], data["sma50"][i], data["sma200"][i]
            if not sigmod._ok(price) or not sigmod._ok(s50) or not sigmod._ok(s200):
                continue
            n += 1
            above50 += int(price > s50)
            above200 += int(price > s200)
        if n == 0:
            return None
        return above50 / n, above200 / n, n

    def aligned_close(symbol: str, dates):
        frame = frames.get(symbol)
        if frame is None:
            return np.full(len(dates), np.nan)
        series = frame["Close"].copy()
        series.index = [ts.date() for ts in frame.index]
        return np.array([series.get(d, np.nan) for d in dates], dtype=float)

    spy_dates_list = pack["SPY"]["dates"]
    qqq_dates = pack["QQQ"]["dates"]
    vix_on_spy = aligned_close("^VIX", spy_dates_list)
    vix3_on_spy = aligned_close("^VIX3M", spy_dates_list)
    regime = sigmod.regime_snapshot(
        pack["SPY"], pack["QQQ"], vix_on_spy, vix3_on_spy, spy_dates_list, qqq_dates, {0: breadth(0), 1: breadth(1)}, rules
    )

    sector_ok_cache = {}

    def sector_state(etf: str | None, mode: str | None):
        if mode == "market":
            return True
        if not etf:
            return None
        if etf in sector_ok_cache:
            return sector_ok_cache[etf]
        frame = frames.get(etf)
        if frame is None:
            sector_ok_cache[etf] = None
            return None
        joined = frame["Close"].to_frame("e").join(spy["Close"].rename("s"), how="inner")
        ratio = (joined["e"] / joined["s"]).to_numpy(float)
        sma = indmod.sma(ratio, 50)
        if not sigmod._ok(ratio[-1]) or not sigmod._ok(sma[-1]):
            sector_ok_cache[etf] = None
        else:
            sector_ok_cache[etf] = bool(ratio[-1] > sma[-1])
        return sector_ok_cache[etf]

    print("财报日期…", flush=True)
    seed_map = {}
    for item in seed.get("items") or []:
        seed_map.setdefault(item["ticker"].replace(".", "-"), item)
    earnings = {}
    skip_earn = set(universe.get("no_earnings") or [])
    need = [t for t in scan if t in frames and t not in skip_earn]
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {pool.submit(fetch_earnings_one, t): t for t in need}
        try:
            for future in as_completed(future_map, timeout=180):
                ticker = future_map[future]
                try:
                    earnings[ticker] = future.result()
                except Exception:
                    earnings[ticker] = None
        except Exception:
            pass

    def resolve_earnings(ticker: str):
        seeded = seed_map.get(ticker) or {}
        if seeded.get("date"):
            return as_date(seeded["date"]), bool(seeded.get("confirmed")), seeded.get("note"), "seed"
        live = earnings.get(ticker)
        if live is not None:
            return as_date(live), False, None, "yfinance"
        if seeded.get("note"):
            return None, False, seeded.get("note"), "seed"
        return None, False, None, None

    print("相对强度…", flush=True)
    raw_source = {}
    for ticker in scan:
        if ticker not in pack:
            continue
        lev = leveraged.get(ticker)
        source = lev["underlying"] if lev else ticker
        if source in raw_source or source not in pack:
            if source in pack and source not in raw_source:
                raw_source[source] = indmod.rs_raw(pack[source]["close"], rules["indicators"]["rs_weights"], rules["indicators"]["rs_span"])
            continue
        raw_source[source] = indmod.rs_raw(pack[source]["close"], rules["indicators"]["rs_weights"], rules["indicators"]["rs_span"])
    ranks = _ranks(raw_source)

    rows = []
    ind_by_ticker = {}
    for ticker in scan:
        if ticker not in pack:
            continue
        lev = leveraged.get(ticker) or {}
        source = lev.get("underlying") or ticker
        if source not in pack:
            source = ticker
            lev = {}
        sig = pack[source]
        trade = pack[ticker]
        earn_date, earn_conf, earn_note, earn_src = resolve_earnings(source if lev else ticker)
        days = None
        blk = None
        if ticker in set(universe.get("no_earnings") or []) or source in set(universe.get("no_earnings") or []):
            blk = False
            earn_date, earn_conf, earn_note, earn_src = None, None, "ETF，没有个股财报", "etf"
        elif earn_date is None:
            blk = None
        elif earn_date <= bar_date:
            blk = False
        else:
            # 历史日线里没有未来交易日，用工作日近似。交易所假日会让这个数字差 1 天左右。
            days = len(pd.bdate_range(pd.Timestamp(bar_date) + pd.Timedelta(days=1), pd.Timestamp(earn_date)))
            blk = bool(days <= rules["account"]["earnings_block_days"])
        if earn_date is not None:
            idx = next((i for i, d in enumerate(sig["dates"]) if d >= earn_date), None)
            sig["avwap_earn"] = indmod.avwap(sig["high"], sig["low"], sig["close"], sig["volume"], idx) if idx is not None else None
        else:
            sig["avwap_earn"] = None
        sig["e4"] = earnings_gap_info(sig, earn_date, rules)
        spy_aligned = aligned_close("SPY", sig["dates"])
        theme = themes.get(ticker) or themes.get(source) or {"id": "other", "name": "未分类", "sector_etf": None, "sector_mode": None}
        sec_ok = sector_state(theme.get("sector_etf"), theme.get("sector_mode"))
        weekly = indmod.weekly_ok(frames[source], rules["indicators"]["weekly_sma"])
        leverage = float(lev.get("leverage") or 1)
        tape = sigmod.evaluate_tape(
            sig, trade, spy_aligned, sec_ok, weekly, blk, regime.get("regime"), rules, leverage=leverage
        )
        tape = sigmod.finalize_leaders(tape, ranks.get(source), rules)
        rs_line = tape.pop("rs_line", None)
        x6, x8, x6_why, x8_why = x6_x8(sig, rs_line, rules)
        tape["x6"] = x6
        tape["x8"] = x8
        price = float(trade["close"][-1])
        spy_on_trade = aligned_close("SPY", trade["dates"])
        row = {
            "date": str(frames[ticker].index[-1].date()),
            "ticker": ticker,
            "theme": theme["id"],
            "theme_name": theme["name"],
            "price": price,
            "change_1d": change(trade["close"], 1),
            "change_5d": change(trade["close"], 5),
            "change_1m": change(trade["close"], 21),
            "sma20": sigmod._f(trade["sma20"][-1]),
            "sma50": sigmod._f(trade["sma50"][-1]),
            "sma200": sigmod._f(trade["sma200"][-1]),
            "ema10": sigmod._f(trade["ema10"][-1]),
            "ema21": sigmod._f(trade["ema21"][-1]),
            "dist_sma20": dist(price, trade["sma20"][-1]),
            "dist_ema10": dist(price, trade["ema10"][-1]),
            "dist_ema21": dist(price, trade["ema21"][-1]),
            "dist_sma50": dist(price, trade["sma50"][-1]),
            "dist_sma200": dist(price, trade["sma200"][-1]),
            "rsi14": sigmod._f(trade["rsi"][-1]),
            "atr14": sigmod._f(trade["atr14"][-1]),
            "atr_pct": dist_atr(trade),
            "rvol": sigmod._f(trade["rvol"][-1]),
            "vol_ratio_20": sigmod._f(trade["vol_ratio_20"][-1]),
            "dollar_avg50": sigmod._f(trade["dollar_sma50"][-1]),
            "rs_rank": ranks.get(source),
            "rs_raw": raw_source.get(source),
            "rs_3m": return_ratio(trade["close"], spy_on_trade, 63),
            "rs_6m": return_ratio(trade["close"], spy_on_trade, 126),
            "high_52w": sigmod._f(trade["hhv252"][-1]),
            "low_52w": sigmod._f(trade["llv252"][-1]),
            "dist_52w_high": dist(price, trade["hhv252"][-1]),
            "dist_52w_low": dist(price, trade["llv252"][-1]),
            "above_sma50": bool(price > trade["sma50"][-1]) if sigmod._ok(trade["sma50"][-1]) else None,
            "above_sma200": bool(price > trade["sma200"][-1]) if sigmod._ok(trade["sma200"][-1]) else None,
            "stage2": tape.get("stage2"),
            "weekly_ok": weekly,
            "flat_market": tape.get("flat_market"),
            "sector_rs_ok": sec_ok,
            "liquid": tape.get("liquid"),
            "extended": tape.get("extended"),
            "earnings_blk": blk,
            "earnings_date": str(earn_date) if earn_date else None,
            "earnings_in_days": days,
            "earnings_confirmed": earn_conf if earn_date or earn_note else None,
            "earnings_note": earn_note,
            "earnings_source": earn_src,
            "regime": regime.get("regime"),
            "primary_id": tape.get("primary_id"),
            "entry": tape.get("entry"),
            "stop": tape.get("stop"),
            "pivot": tape.get("pivot"),
            "c_score": tape.get("c_score"),
            "c_detail": tape.get("c_detail"),
            "size_mult": tape.get("size_mult"),
            "horizon": tape.get("horizon"),
            "initial_fraction": tape.get("initial_fraction"),
            "levels": {
                "ema10": sigmod._f(trade["ema10"][-1]),
                "ema21": sigmod._f(trade["ema21"][-1]),
                "sma50": sigmod._f(trade["sma50"][-1]),
                "sma200": sigmod._f(trade["sma200"][-1]),
                "avwap_earn": sigmod._f(sig.get("avwap_earn")),
                "chandelier": sigmod._f(trade["chandelier"][-1]),
            },
            "flags": tape.get("flags") or [],
            "blockers": tape.get("blockers") or [],
            "candidates": tape.get("candidates") or [],
            "base": tape.get("base"),
            "leader_pos": tape.get("leader_pos"),
            "leader_swing": tape.get("leader_swing"),
            "hi_vol": tape.get("hi_vol"),
            "leverage": leverage,
            "signal_source": source if source != ticker else None,
            "max_hold": rules["account"]["leveraged_max_hold_bars"] if lev else None,
            "held": ticker in held,
            "x6": x6,
            "x8": x8,
            "x6_why": x6_why,
            "x8_why": x8_why,
            "gate_open": tape.get("gate_open"),
        }
        if leverage < 0:
            row["flags"] = list(row["flags"]) + ["inverse_no_long"]
        rows.append(row)
        ind_by_ticker[ticker] = trade
        _write_bars(ticker, frames[ticker], trade)
    write_sparks({ticker: frames[ticker] for ticker in frames if ticker in {row["ticker"] for row in rows}})

    print("持仓与期权…", flush=True)
    port = build_portfolio(portfolio_cfg, rows, frames, rules, regime, bar_date)
    state_path = ROOT / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    hwm = float(state.get("hwm") or portfolio_cfg.get("nlv") or rules["account"]["nlv_base"])
    est = port.get("nlv_estimated")
    if est is not None and not port.get("price_incomplete") and est > hwm and (est - hwm) / hwm < 0.15:
        hwm = est
        state["hwm_date"] = str(bar_date)
    state["hwm"] = round(hwm, 2)
    port["hwm"] = state["hwm"]
    if est is not None and state["hwm"]:
        port["drawdown"] = est / state["hwm"] - 1.0
        port["drawdown_levels"] = _drawdown_levels(state["hwm"], est, rules)
    _apply_portfolio_rejects(rows, port, rules, regime, trade_log, spy_dates, bar_date)
    _attach_holding_exits(rows, port, ind_by_ticker, rules, trade_log.get("trades") or [])
    explainmod.attach(rows, port, rules)

    market = {"groups": []}
    grouped = {"us": "美国股指", "global": "全球股指", "rates": "利率与美元", "commodity": "商品与加密"}
    for gid, title in grouped.items():
        items = []
        for spec in market_specs:
            if spec["group"] != gid:
                continue
            frame = frames.get(spec["symbol"])
            if frame is None:
                errors.append(f"{spec['symbol']}（{spec['name']}）：没有行情")
                continue
            items.append(quote_block(frame, spec, spy if spec.get("kind") != "yield" else None, rules))
        market["groups"].append({"id": gid, "title": title, "items": items})

    sectors = []
    for spec in sector_specs:
        frame = frames.get(spec["symbol"])
        if frame is None:
            continue
        block = quote_block(frame, {**spec, "group": "sector"}, spy, rules)
        sectors.append(block)
    sectors_sorted = sorted([s for s in sectors if s.get("rs_1m") is not None], key=lambda s: s["rs_1m"], reverse=True)
    sentiment = {
        "vix": regime.get("vix"),
        "vix3m": regime.get("vix3m"),
        "vix_ratio": regime.get("vix_ratio"),
        "vix_lock": regime.get("vix_lock"),
        "breadth_above_50": regime.get("breadth_above_50"),
        "breadth_above_200": regime.get("breadth_above_200"),
        "breadth_n": regime.get("breadth_n"),
        "breadth_note": regime.get("breadth_note"),
        "distribution_count": regime.get("distribution_count"),
        "distribution_lookback": (rules.get("regime") or {}).get("distribution_lookback") or 25,
        "distribution_penalty": (rules.get("regime") or {}).get("distribution_count_penalty") or 5,
        "sectors": sectors,
        "sectors_top": sectors_sorted[:3],
        "sectors_bottom": list(reversed(sectors_sorted[-3:])) if sectors_sorted else [],
    }
    try:
        sentiment.update(sentmod.fetch(ROOT))
    except Exception as exc:
        print(f"情绪源更新失败：{exc}", flush=True)
        sentiment.setdefault("fear_greed", None)
        sentiment.setdefault("put_call", None)
        sentiment.setdefault("hy_oas", None)

    signal_rows = []
    for row in rows:
        signal_ids = []
        if row.get("primary_id"):
            signal_ids.append(row["primary_id"])
            if row.get("c_score") is not None:
                signal_ids.append(f"C1:{row['c_score']}")
        if row.get("add_signal"):
            signal_ids.append("A1")
        for cand in row.get("candidates") or []:
            if cand.get("ok") and cand["id"] not in signal_ids:
                signal_ids.append(cand["id"])
        row["signals"] = signal_ids
        targets = None
        if row.get("entry") is not None and row.get("stop") is not None and row["entry"] > row["stop"]:
            risk = row["entry"] - row["stop"]
            targets = {"1R": row["entry"] + risk, "2R": row["entry"] + 2 * risk}
        row["r_targets"] = targets
        signal_rows.append({
            "date": row["date"],
            "ticker": row["ticker"],
            "regime": row["regime"],
            "signals": signal_ids,
            "entry": row.get("entry"),
            "stop": row.get("stop"),
            "pivot": row.get("pivot"),
            "atr14": row.get("atr14"),
            "rs_rank": row.get("rs_rank"),
            "rvol": row.get("rvol"),
            "shares": row.get("shares") or 0,
            "risk_usd": row.get("risk_usd") or 0,
            "r_targets": targets or {"1R": None, "2R": None},
            "levels": row.get("levels"),
            "flags": row.get("flags"),
            "exits": row.get("exits") or [],
            "add": row.get("add_signal"),
            "rejected_by": row.get("rejected_by") or [],
            "call": row.get("call"),
        })

    decisions = build_decisions(rows, port, regime, rules, events, bar_date, now_sh)
    approximations = [
        "市场宽度用观察列表，不是标普 500 全部成分。",
        "财报潜在亏损优先用平值跨式；跨式取不到时该格留空，不再用 15% 填上。近 8 次跳空用日线高低价。",
        "距离财报的交易日用工作日近似，没有扣除交易所假日。",
        "行业相对强度用主题对应的 ETF，不是 GICS 官方行业。",
        "净值 = 配置里的现金 + 本次行情算出的持仓市值，不含融资利息。",
        "宏观档位来自 config/signal_rules.yaml，需要你自己在利率或通胀变化后修改。",
        "RS 排名在观察列表和持仓里计算，不是全市场。",
        "止损合成默认取较近的那个（stops.combine: nearer）。",
    ]
    approximations.extend(sentmod.approximation_notes(sentiment))

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {
        "generated_at": now_sh.astimezone(timezone.utc).isoformat(),
        "generated_at_cn": now_sh.strftime("%Y-%m-%d %H:%M"),
        "generated_date_cn": now_sh.strftime("%Y-%m-%d"),
        "bar_date": str(bar_date),
        "session": session,
        "timezone": "Asia/Shanghai",
        "watchlist_count": len(watch),
        "scan_count": len(rows),
        "macro": rules["macro_tier"],
        "exposure_cap": _exposure_cap(rules, regime),
        "regime_risk_mult": regime.get("risk_mult"),
        "approximations": approximations,
        "errors": errors,
        "account": {
            "nlv_base": rules["account"]["nlv_base"],
            "risk_swing": rules["account"]["risk_swing"],
            "risk_position": rules["account"]["risk_position"],
            "max_open_risk": rules["account"]["max_open_risk"],
            "max_pos_pct": rules["account"]["max_pos_pct"],
            "max_theme_pct": rules["account"]["max_theme_pct"],
            "max_gross": rules["account"]["max_gross"],
            "max_stop_pct_swing": rules["account"]["max_stop_pct_swing"],
            "max_stop_pct_position": rules["account"]["max_stop_pct_position"],
        },
        "disclaimer": "规则输出只供个人研究对照，不构成投资建议，也不构成买卖任何证券的推荐。",
    }
    regime_out = {k: v for k, v in regime.items() if k != "parts"}
    regime_out["parts"] = regime.get("parts")
    regime_out["exposure_cap"] = meta["exposure_cap"]
    regime_out["macro"] = rules["macro_tier"]

    data = ROOT / "site" / "data"
    write_json(data / "meta.json", meta)
    write_json(data / "market.json", market)
    write_json(data / "sentiment.json", sentiment)
    write_json(data / "regime.json", regime_out)
    write_json(data / "scanner.json", {"date": str(bar_date), "rows": rows})
    write_json(data / "signals.json", {"date": str(bar_date), "regime": regime.get("regime"), "rows": signal_rows})
    write_json(data / "portfolio.json", port)
    write_json(data / "events.json", build_events(events, rows, bar_date))
    write_json(data / "decisions.json", decisions)
    publish_risk(port, rows, rules, bar_date)

    render_docs()
    publish_report(now_sh, session)
    print(f"完成。环境 {regime.get('regime')} 分数 {regime.get('score')}，扫描 {len(rows)} 只，错误 {len(errors)}。", flush=True)


def dist_atr(ind):
    price = ind["close"][-1]
    atr = ind["atr14"][-1]
    if not sigmod._ok(price) or price == 0 or not sigmod._ok(atr):
        return None
    return float(atr / price)


def _ranks(raw: dict) -> dict:
    items = [(k, v) for k, v in raw.items() if v is not None and math.isfinite(v)]
    n = len(items)
    ranks = {k: None for k in raw}
    if n < 2:
        return ranks
    items.sort(key=lambda kv: kv[1])
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        avg = (i + j) / 2.0
        pct = int(round(1 + avg / (n - 1) * 98))
        pct = max(1, min(99, pct))
        for k in range(i, j + 1):
            ranks[items[k][0]] = pct
        i = j + 1
    return ranks


def _write_bars(ticker: str, frame: pd.DataFrame, ind: dict):
    start = max(0, len(frame) - 180)
    bars = []
    index = list(frame.index)
    for i in range(start, len(frame)):
        bars.append({
            "d": str(index[i].date()),
            "o": float(frame["Open"].iloc[i]),
            "h": float(frame["High"].iloc[i]),
            "l": float(frame["Low"].iloc[i]),
            "c": float(frame["Close"].iloc[i]),
            "v": float(frame["Volume"].iloc[i]),
            "e21": sigmod._f(ind["ema21"][i]),
            "s50": sigmod._f(ind["sma50"][i]),
            "s200": sigmod._f(ind["sma200"][i]),
        })
    path = ROOT / "site" / "data" / "bars" / f"{ticker}.json"
    write_json(path, {"ticker": ticker, "bars": bars})


def _model_shares(row, nlv, regime, rules):
    if not row.get("primary_id") or row.get("entry") is None or row.get("stop") is None or not nlv:
        return 0, 0.0
    risk_base = rules["account"]["risk_swing"] if row.get("horizon") == "swing" else rules["account"]["risk_position"]
    risk_mult = regime.get("risk_mult") or 0
    if row.get("hi_vol"):
        risk_mult *= 0.5
    size_mult = row.get("size_mult") if row.get("size_mult") is not None else rules["confirmation"]["size_mult_half"]
    frac = row.get("initial_fraction") or 1
    dist_px = row["entry"] - row["stop"]
    if dist_px <= 0 or risk_mult <= 0:
        return 0, 0.0
    raw = nlv * risk_base * risk_mult * size_mult * frac / dist_px
    shares = math.floor(raw)
    cap = math.floor(rules["account"]["max_pos_pct"] * nlv / row["entry"])
    shares = max(0, min(shares, cap))
    return shares, shares * dist_px


def _size_and_constrain(rows, rules, regime, portfolio_cfg, trade_log, spy_dates, bar_date):
    nlv = float(portfolio_cfg.get("nlv") or rules["account"]["nlv_base"])
    for row in rows:
        shares, risk = _model_shares(row, nlv, regime, rules)
        row["shares_model"] = shares
        row["risk_usd_model"] = risk
        row["shares"] = shares
        row["risk_usd"] = risk
        row["rejected_by"] = []


def build_portfolio(cfg, rows, frames, rules, regime, bar_date):
    price_of = {row["ticker"]: row["price"] for row in rows}
    positions = []
    incomplete = False
    for pos in cfg.get("positions") or []:
        ticker = str(pos["ticker"]).replace(".", "-")
        item = dict(pos)
        item["ticker"] = ticker
        if item.get("instrument") == "option":
            mark, source = option_mark(item.get("underlying") or ticker, str(item["expiry"]), float(item["strike"]), item.get("right") or "C")
            if mark is None and item.get("fallback_mark") is not None:
                mark = float(item["fallback_mark"])
                source = f"截图参考价 {item.get('fallback_mark_asof')}，不是实时报价"
                item["mark_stale"] = True
            else:
                item["mark_stale"] = False
            item["mark_source"] = source
            item["price"] = mark
            if mark is None:
                incomplete = True
                item["market_value"] = None
            else:
                item["market_value"] = mark * float(item["contracts"]) * float(item.get("multiplier") or 100)
        else:
            item["price"] = price_of.get(ticker)
            item["mark_source"] = "收盘价" if item["price"] is not None else None
            item["mark_stale"] = False
            if item["price"] is None:
                incomplete = True
                item["market_value"] = None
            else:
                item["market_value"] = item["price"] * float(item["shares"])
        if item.get("avg_cost") is not None and item.get("market_value") is not None and item.get("price") is not None:
            if item.get("instrument") == "option":
                item["pnl"] = (item["price"] - float(item["avg_cost"])) * float(item["contracts"]) * float(item.get("multiplier") or 100)
            else:
                item["pnl"] = (item["price"] - float(item["avg_cost"])) * float(item["shares"])
        else:
            item["pnl"] = None
        positions.append(item)

    # 备兑标记
    stock_shares = {}
    for item in positions:
        if item.get("instrument") != "option":
            stock_shares[item["ticker"]] = stock_shares.get(item["ticker"], 0) + float(item.get("shares") or 0)
    for item in positions:
        if item.get("instrument") == "option" and float(item.get("contracts") or 0) < 0:
            covered = stock_shares.get(item.get("underlying") or item["ticker"], 0)
            item["covered_by_shares"] = covered >= abs(float(item["contracts"])) * float(item.get("multiplier") or 100)

    cash = float(cfg.get("cash") or 0)
    known = [p["market_value"] for p in positions if p["market_value"] is not None]
    long_mv = sum(v for v in known if v > 0)
    short_mv = sum(v for v in known if v < 0)
    nlv = cash + sum(known) if not incomplete else None
    # 用估算净值做权重；估不出来就退回配置里的净值，并标明。
    base_nlv = nlv if nlv else float(cfg.get("nlv") or rules["account"]["nlv_base"])
    for item in positions:
        mv = item.get("market_value")
        item["weight"] = (mv / base_nlv) if mv is not None and base_nlv else None
    name_mv = {}
    for item in positions:
        if item.get("market_value") is None or item["market_value"] <= 0:
            continue
        key = item.get("underlying") or item["ticker"]
        name_mv[key] = name_mv.get(key, 0) + item["market_value"]
    theme_mv = {}
    theme_name = {}
    row_theme = {r["ticker"]: r for r in rows}
    for item in positions:
        if item.get("market_value") is None or item["market_value"] <= 0:
            continue
        key = item.get("underlying") or item["ticker"]
        theme = row_theme.get(key) or row_theme.get(item["ticker"]) or {}
        tid = theme.get("theme") or "other"
        theme_mv[tid] = theme_mv.get(tid, 0) + item["market_value"]
        theme_name[tid] = theme.get("theme_name") or tid
    breaches = []
    gross = long_mv / base_nlv if base_nlv else None
    if cash < 0 or (gross is not None and gross > rules["account"]["max_gross"] + 1e-6):
        breaches.append({
            "id": "gross",
            "detail": f"多头敞口 {gross:.0%}，现金 {cash:,.0f} 美元。上限是 {rules['account']['max_gross']:.0%}，且不用融资。",
        })
    names = []
    for ticker, mv in sorted(name_mv.items(), key=lambda kv: kv[1], reverse=True):
        weight = mv / base_nlv
        entry = {"ticker": ticker, "market_value": mv, "weight": weight, "breach": weight > rules["account"]["max_pos_pct"]}
        price = price_of.get(ticker)
        if entry["breach"] and price:
            cap_mv = rules["account"]["max_pos_pct"] * base_nlv
            entry["reduce_to_mv"] = cap_mv
            entry["reduce_shares"] = (mv - cap_mv) / price
        names.append(entry)
        if entry["breach"]:
            breaches.append({"id": "name", "detail": f"{ticker} 占净值 {weight:.0%}，单票上限 {rules['account']['max_pos_pct']:.0%}。"})
    themes = []
    for tid, mv in sorted(theme_mv.items(), key=lambda kv: kv[1], reverse=True):
        weight = mv / base_nlv
        themes.append({"id": tid, "name": theme_name.get(tid, tid), "market_value": mv, "weight": weight, "breach": weight > rules["account"]["max_theme_pct"]})
        if weight > rules["account"]["max_theme_pct"]:
            breaches.append({"id": "theme", "detail": f"{theme_name.get(tid, tid)} 占净值 {weight:.0%}，主题上限 {rules['account']['max_theme_pct']:.0%}。"})
    option_premium = 0.0
    for item in positions:
        if item.get("instrument") == "option" and float(item.get("contracts") or 0) > 0 and item.get("market_value"):
            option_premium += item["market_value"]
            if item["market_value"] > rules["account"]["max_option_premium_pct"] * base_nlv:
                breaches.append({"id": "option", "detail": f"{item['ticker']} 期权权利金占净值 {item['market_value'] / base_nlv:.1%}，单笔上限 {rules['account']['max_option_premium_pct']:.0%}。"})
    if option_premium > rules["account"]["max_option_premium_total"] * base_nlv:
        breaches.append({"id": "option_total", "detail": f"买入期权权利金合计占净值 {option_premium / base_nlv:.1%}，上限 {rules['account']['max_option_premium_total']:.0%}。"})
    stops_missing = any(p.get("instrument") == "stock" and p.get("stop") in (None, "") for p in positions)
    open_risk = 0.0
    if not stops_missing:
        for item in positions:
            if item.get("instrument") != "stock" or item.get("price") is None or item.get("stop") is None:
                continue
            open_risk += max(0.0, float(item["price"]) - float(item["stop"])) * float(item["shares"])
    return {
        "as_of_config": cfg.get("as_of"),
        "cash": cash,
        "nlv_config": cfg.get("nlv"),
        "nlv_estimated": nlv,
        "nlv_basis": base_nlv,
        "long_mv": long_mv,
        "short_mv": short_mv,
        "gross": gross,
        "using_margin": cash < 0,
        "price_incomplete": incomplete,
        "positions": positions,
        "names": names,
        "themes": themes,
        "breaches": breaches,
        "stops_missing": stops_missing,
        "open_risk": None if stops_missing else open_risk,
        "open_risk_pct": None if stops_missing or not base_nlv else open_risk / base_nlv,
        "snapshot": cfg.get("snapshot"),
        "need_proceeds": max(0.0, -cash, long_mv - rules["account"]["max_gross"] * base_nlv),
    }


def _apply_portfolio_rejects(rows, port, rules, regime, trade_log, spy_dates, bar_date):
    acct = rules["account"]
    base_nlv = port["nlv_basis"]
    for row in rows:
        shares, risk = _model_shares(row, base_nlv, regime, rules)
        row["shares_model"] = shares
        row["risk_usd_model"] = risk
        row["shares"] = shares
        row["risk_usd"] = risk
        row["rejected_by"] = []
    dd = port.get("drawdown")
    trades = trade_log.get("trades") or []
    week_cap = (rules["regime"]["max_new_entries_per_week"] or {}).get(regime.get("regime"))
    week_count = 0
    if bar_date is not None:
        iso = pd.Timestamp(bar_date).isocalendar()
        for trade in trades:
            if trade.get("action") != "entry" or not trade.get("date"):
                continue
            got = pd.Timestamp(trade["date"]).isocalendar()
            if (got[0], got[1]) == (iso[0], iso[1]):
                week_count += 1
    candidates = [r for r in rows if r.get("primary_id") and r.get("shares_model")]
    candidates.sort(key=lambda r: (r.get("rs_rank") or 0), reverse=True)
    accepted_theme = set()
    running_risk = port["open_risk"] if port["open_risk"] is not None else None
    running_gross = port["gross"] or 0
    theme_weight = {t["id"]: t["weight"] for t in port["themes"]}
    name_weight = {n["ticker"]: n["weight"] for n in port["names"]}
    for row in rows:
        if not row.get("primary_id"):
            row["shares"] = 0
            row["risk_usd"] = 0
            continue
        reasons = []
        if port["stops_missing"]:
            reasons.append("持仓没有填写止损，风险合计未知，新开仓被抑制")
        if port["using_margin"] or (port["gross"] is not None and port["gross"] > acct["max_gross"]):
            reasons.append("总敞口或融资已经超过上限")
        if dd is not None and dd <= rules["drawdown"]["flat"]:
            reasons.append("回撤达到 -10%，不开新仓")
        elif dd is not None and dd <= rules["drawdown"]["pause_swing"] and row.get("horizon") == "swing":
            reasons.append("回撤达到 -3%，暂停新的短线")
        if row.get("horizon") == "swing" and _macro_blackout(bar_date, rules):
            reasons.append("48 小时内有 FOMC / CPI / 非农 / PCE，不开新的短线")
        if week_cap is not None and week_count >= week_cap:
            reasons.append(f"本周新开仓已记录 {week_count} 笔，谨慎环境上限 {week_cap}")
        if _in_cooldown(trades, row["ticker"], spy_dates, bar_date, rules):
            reasons.append("该标的连续两次止损后的冷却期还没结束")
        if reasons:
            row["rejected_by"] = reasons
            row["shares"] = 0
            row["risk_usd"] = 0
            continue
        # 组合容量按 RS 从高到低占用
        if row not in candidates:
            row["shares"] = 0
            row["risk_usd"] = 0
            continue
    for row in candidates:
        if row.get("rejected_by"):
            continue
        reasons = []
        if row["theme"] in accepted_theme:
            reasons.append("同一主题今天已经有一笔新开仓")
        new_mv = (row.get("shares_model") or 0) * (row.get("entry") or 0) * abs(row.get("leverage") or 1)
        new_w = new_mv / base_nlv if base_nlv else 1
        if name_weight.get(row["ticker"], 0) + new_w > acct["max_pos_pct"]:
            reasons.append("加上之后单票会超过 10%")
        if theme_weight.get(row["theme"], 0) + new_w > acct["max_theme_pct"]:
            reasons.append("加上之后主题会超过 25%")
        if running_gross + new_w > acct["max_gross"]:
            reasons.append("加上之后总敞口会超过 100%")
        new_risk = row.get("risk_usd_model") or 0
        if running_risk is None:
            reasons.append("风险合计未知")
        elif base_nlv and (running_risk + new_risk) / base_nlv > acct["max_open_risk"]:
            reasons.append("加上之后总风险会超过净值的 4%")
        if reasons:
            row["rejected_by"] = reasons
            row["shares"] = 0
            row["risk_usd"] = 0
        else:
            accepted_theme.add(row["theme"])
            running_gross += new_w
            name_weight[row["ticker"]] = name_weight.get(row["ticker"], 0) + new_w
            theme_weight[row["theme"]] = theme_weight.get(row["theme"], 0) + new_w
            if running_risk is not None:
                running_risk += new_risk
            row["shares"] = row["shares_model"]
            row["risk_usd"] = row["risk_usd_model"]
            row["rejected_by"] = []


def _macro_blackout(bar_date, rules):
    # 事件文件在 build_events 之前这里直接再读一次，避免互相引用。
    events = load_yaml(ROOT / "data" / "events.yaml") or {}
    window = rules["account"]["macro_swing_blackout_calendar_days"]
    for item in events.get("items") or []:
        if not item.get("date"):
            continue
        tags = set(item.get("tags") or [])
        if not tags & {"fomc", "cpi", "nfp", "pce"}:
            continue
        delta = (as_date(item["date"]) - bar_date).days
        if 0 <= delta <= window:
            return True
    return False


def _in_cooldown(trades, ticker, spy_dates, bar_date, rules) -> bool:
    exits = [t for t in trades if t.get("ticker") == ticker and t.get("action") == "exit" and t.get("date")]
    exits.sort(key=lambda t: t["date"])
    need = rules["account"]["consecutive_stops_for_cooldown"]
    if len(exits) < need or not all(t.get("result") == "stop" for t in exits[-need:]):
        return False
    stop_day = as_date(exits[-1]["date"])
    after = [d for d in spy_dates if d > stop_day]
    bars = rules["account"]["cooldown_bars_after_stops"]
    if len(after) < bars:
        return True
    return bar_date <= after[bars - 1]


def _attach_holding_exits(rows, port, ind_by_ticker, rules, trades):
    by_ticker = {r["ticker"]: r for r in rows}
    implied = rules["account"]["implied_move_fallback"]
    nlv = port["nlv_basis"]
    for pos in port["positions"]:
        row = by_ticker.get(pos.get("underlying") or pos["ticker"]) if pos.get("instrument") == "option" else by_ticker.get(pos["ticker"])
        ind = ind_by_ticker.get(pos["ticker"])
        if row is None or ind is None or pos.get("instrument") == "option":
            pos["exits"] = []
            pos["rules_null"] = []
            pos["add"] = None
            if pos.get("instrument") == "option" and pos.get("contracts", 0) < 0 and pos.get("covered_by_shares"):
                pos["exit_notes"] = ["空头看涨目前被正股覆盖。卖掉正股之前要先买回期权，否则变成裸卖。"]
            else:
                pos["exit_notes"] = []
            continue
        exits, notes, pending, add = holding_exits(row, pos, ind, nlv, rules, row.get("earnings_blk"), implied, trades)
        if add and (port.get("using_margin") or (port.get("gross") or 0) > rules["account"]["max_gross"] or port.get("stops_missing")):
            add["shares"] = 0
            add["rejected_by"] = ["账户仍在融资、总敞口超限，或持仓止损没填"]
            add["detail"] += "当前不放行加仓。"
        pos["exits"] = exits
        pos["exit_notes"] = notes
        pos["rules_null"] = pending
        pos["add"] = add
        existing = set(row.get("exits") or [])
        for ex in exits:
            existing.add(ex)
        row["exits"] = sorted(existing)
        if add:
            row["add_signal"] = add


def _drawdown_levels(hwm, nlv, rules):
    bands = [
        ("-3%", rules["drawdown"]["pause_swing"], "暂停新的短线，复盘最近的交易"),
        ("-5%", rules["drawdown"]["gross_60"], "总敞口降到 60% 或以下"),
        ("-7.5%", rules["drawdown"]["gross_35"], "总敞口降到 35% 或以下，只留核心指数和黄金"),
        ("-10%", rules["drawdown"]["flat"], "权益清掉，换成现金或短债；冷静 10 个交易日"),
    ]
    out = []
    dd = nlv / hwm - 1 if hwm else None
    for label, level, action in bands:
        line = hwm * (1 + level)
        out.append({
            "label": label,
            "level": level,
            "nlv": line,
            "action": action,
            "breached": dd is not None and dd <= level,
            "distance": nlv - line if nlv is not None else None,
        })
    return out


def _exposure_cap(rules, regime):
    macro = rules["macro_tier"].get("cap")
    tech = (rules["regime"].get("exposure_cap") or {}).get(regime.get("regime"))
    if macro is None or tech is None:
        return macro or tech
    return min(macro, tech)


def build_events(events, rows, bar_date):
    items = []
    for item in events.get("items") or []:
        rec = dict(item)
        rec["confirmed"] = bool(item.get("confirmed"))
        if item.get("date"):
            rec["days_away"] = (as_date(item["date"]) - bar_date).days
        else:
            rec["days_away"] = None
        items.append(rec)
    earnings = []
    for row in rows:
        if not row.get("earnings_date") and not row.get("earnings_note"):
            continue
        if row.get("earnings_in_days") is not None and row["earnings_in_days"] > 60:
            continue
        if row.get("earnings_date") is None and not row.get("held") and row.get("earnings_note") is None:
            continue
        # 只列出持仓，以及 45 天内的观察列表财报，避免页面被 132 条淹没
        soon = row.get("earnings_in_days") is not None and row["earnings_in_days"] <= 45
        if not row.get("held") and not soon and not row.get("earnings_note"):
            continue
        if not row.get("held") and row.get("earnings_date") is None:
            continue
        earnings.append({
            "ticker": row["ticker"],
            "date": row.get("earnings_date"),
            "confirmed": row.get("earnings_confirmed"),
            "note": row.get("earnings_note"),
            "source": row.get("earnings_source"),
            "days": row.get("earnings_in_days"),
            "held": row.get("held"),
        })
    earnings.sort(key=lambda e: (e.get("date") is None, e.get("date") or "9999"))
    return {"macro": items, "earnings": earnings, "bar_date": str(bar_date)}


def build_decisions(rows, port, regime, rules, events, bar_date, now_sh):
    actions = []
    dd = port.get("drawdown")
    if dd is not None and dd <= rules["drawdown"]["flat"]:
        actions.append({"id": "P1", "title": "回撤熔断 -10%", "detail": "规则要求清掉权益，留现金或短债，冷静 10 个交易日。黄金可以对冲保留。"})
    elif dd is not None and dd <= rules["drawdown"]["gross_35"]:
        actions.append({"id": "P1", "title": "回撤熔断 -7.5%", "detail": "总敞口降到 35% 或以下，只留核心指数和黄金。"})
    elif dd is not None and dd <= rules["drawdown"]["gross_60"]:
        actions.append({"id": "P1", "title": "回撤熔断 -5%", "detail": "总敞口降到 60% 或以下。"})
    elif dd is not None and dd <= rules["drawdown"]["pause_swing"]:
        actions.append({"id": "P1", "title": "回撤 -3%", "detail": "暂停新的短线，先复盘最近的交易。"})
    if port.get("using_margin"):
        actions.append({
            "id": "gross",
            "title": "先还融资",
            "detail": f"现金 {port['cash']:,.0f} 美元。按现价把敞口降到 100% 并且现金回到 0，大约需要卖出 {port['need_proceeds']:,.0f} 美元。下面是超限标的削到单票上限能回收多少，供对照，不是下单清单。",
        })
    for name in port.get("names") or []:
        if name.get("breach"):
            actions.append({
                "id": "name",
                "title": f"{name['ticker']} 超过单票上限",
                "detail": f"市值 ${name['market_value']:,.0f}，占净值 {name['weight']:.0%}。削到 10% 大约要减 ${name['market_value'] - name['reduce_to_mv']:,.0f}（约 {name['reduce_shares']:.1f} 股）。",
            })
    for theme in port.get("themes") or []:
        if theme.get("breach"):
            cap = rules["account"]["max_theme_pct"] * port["nlv_basis"]
            actions.append({
                "id": "theme",
                "title": f"{theme['name']} 超过主题上限",
                "detail": f"市值 ${theme['market_value']:,.0f}，占净值 {theme['weight']:.0%}。主题上限对应 ${cap:,.0f}。",
            })
    reviews = []
    for pos in port.get("positions") or []:
        call = pos.get("call") or {}
        if call.get("action") in ("sell", "trim"):
            actions.append({"id": call["action"], "title": f"{pos['ticker']} {call['action_label']}", "detail": call["line"]})
            reviews.append(call)
        if pos.get("add"):
            actions.append({"id": "A1", "title": f"{pos['ticker']} 加仓", "detail": pos["add"]["detail"]})
    if any(pos.get("rules_null") for pos in port.get("positions") or []):
        actions.append({
            "id": "null",
            "title": "止损和加仓这次算不了",
            "detail": "持仓没填成本或止损，交易日志也没有突破记录。这些规则是未定，不是没触发。",
        })
    for row in rows:
        call = row.get("call") or {}
        if call.get("code") not in {"E1", "E2", "E3", "E4", "E5"}:
            continue
        detail = call["line"]
        if call.get("block"):
            detail = call["block"] + "。" + detail
        actions.append({"id": call["action"], "title": f"{row['ticker']} {call['action_label']}", "detail": detail})
        reviews.append(call)
    regime_cn = {"GREEN": "进攻", "YELLOW": "谨慎", "RED": "防守"}.get(regime.get("regime"), "未知")
    checklist = [
        {"n": 1, "text": "宏观档位允许吗？本周权益敞口上限还有空间吗？未来 48 小时内有没有 FOMC / CPI / 非农？", "auto": _auto_macro(port, regime, rules, bar_date)},
        {"n": 2, "text": "在能力圈内吗？能用一句话说清买入逻辑，并写下什么情况说明自己错了吗？", "auto": None},
        {"n": 3, "text": "是领涨股吗？环境分 ≥ 2；中线要周线在 30 周均线上方、第二阶段、RS ≥ 80；短线 RS ≥ 70 且行业强于 SPY。", "auto": f"今天环境是{regime_cn}，分数 {regime.get('score')}。具体标的看扫描表的 RS 和阶段列。"},
        {"n": 4, "text": "入场点属于 E1–E5 吗？突破时 RVOL ≥ 1.5？在买入区间内，离 21 日 EMA 不超过 2.5 倍 ATR？", "auto": "只把信号列里出现 E1–E5 的当作候选。延伸和流动性不够的已经标在旗帜里。"},
        {"n": 5, "text": "止损放在结构位下方了吗？盈亏比至少 2:1（短线）或 3:1（中线）？", "auto": None},
        {"n": 6, "text": "仓位按公式算了吗？单笔风险 ≤ 0.5%（短线）/ 1%（中线）。", "auto": "股数 = 净值 × 单笔风险 × 环境系数 × 确认系数 × 首笔比例 ÷ (入场 − 止损)，再受单票 10% 限制。"},
        {"n": 7, "text": "单票 ≤ 10%，单行业 ≤ 25%，风险合计 ≤ 4%，总敞口 ≤ 1 倍，不用融资？", "auto": "；".join(b["detail"] for b in port.get("breaches") or []) or "按当前市值，这几条限额没有被现有持仓打破。"},
        {"n": 8, "text": "未来 10 个交易日有财报吗？按 1.5 倍隐含波动（没有报价时按 15%）估算的跳空亏损 ≤ 1% 净值吗？财报前 5 天不开新仓。", "auto": "跳空用的是 15% 近似，不是期权跨式。财报前 5 天的标的已经被标成 earnings_blk。"},
        {"n": 9, "text": "日均成交额大于 2 亿美元吗？期权买卖价差小于权利金的 10% 吗？", "auto": "成交额不够的扫描行有 illiquid 标记。期权价差需要你在券商里看。"},
        {"n": 10, "text": "不是在追涨、不是想把亏的赚回来、不是因为社交媒体才下单？回撤熔断没有触发？", "auto": None},
    ]
    sell = [
        {"n": "S1", "text": "触及初始止损、移动止损或时间止损了吗？触发了就执行，不重新讨论。"},
        {"n": "S2", "text": "买入逻辑被证伪了吗？指引下调、份额流失、行业周期拐点。"},
        {"n": "S3", "text": "到了目标价、RSI 在 75 以上拐头，或放量滞涨了吗？先止盈一半。"},
        {"n": "S4", "text": "账户回撤熔断或单票 / 行业超限了吗？"},
        {"n": "S5", "text": "有没有更好的机会，需要腾出仓位？"},
    ]
    return {
        "actions": actions,
        "reviews": reviews,
        "checklist": checklist,
        "sell_checklist": sell,
        "formula": "股数 = floor(净值 × 单笔风险% × 环境系数 × 确认系数 × 首笔比例 / (入场价 − 止损价))",
        "as_of": now_sh.strftime("%Y-%m-%d %H:%M"),
    }


def _exit_text(code, pos):
    text = {
        "X1": "日线最低价已经碰到你填写的止损价。",
        "X2": "E1 突破后 3 根 K 线内失败。收盘跌回 pivot 之下且放量则减半；跌破突破日低点则全部离场。",
        "X3": "收盘跌破这套期限对应的移动止损（短线看 10/21 日 EMA，中线看吊灯）。",
        "X5": "短线持有满 10 根 K 线，最大有利波动仍不到 1R。",
        "X4": "收盘跌破 50 日均线且放量，或跌破财报日锚定 VWAP。中线仓减半。",
        "X6": "出现高潮或过度延伸。规则是先止盈大约三分之一到一半。",
        "X7": "财报前的跳空测试没通过，按 15% 近似。减到跳空亏损不超过净值的 1%。",
        "X8": "横盘时相对强度创新低，或高位派发日过多。收紧止损或减一部分。",
    }.get(code, code)
    return f"{pos['ticker']}：{text}"


def _auto_macro(port, regime, rules, bar_date):
    cap = _exposure_cap(rules, regime)
    gross = port.get("gross")
    bits = [rules["macro_tier"].get("note") or ""]
    if cap is not None:
        bits.append(f"权益敞口上限 {cap:.0%}。")
    if gross is not None:
        bits.append(f"当前多头敞口 {gross:.0%}。")
    if _macro_blackout(bar_date, rules):
        bits.append("未来 48 小时内有 FOMC、CPI、非农或 PCE，短线不开新仓。")
    else:
        bits.append("从最近收盘日算，48 小时内没有 FOMC / CPI / 非农 / PCE。")
    return "".join(bits)


if __name__ == "__main__":
    main()
