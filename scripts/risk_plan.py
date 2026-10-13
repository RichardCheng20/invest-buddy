"""按持仓快照排减仓计划。只使用已经算好的价格和规则，不下单。"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "risk_plan_state.json"
FAR = date(9999, 12, 31)


def _num(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _date(value):
    if not value:
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


def _round(value, digits=2):
    number = _num(value)
    if number is None:
        return None
    return round(number, digits)


def suggest_stop(price, chandelier, sma50, atr, k):
    """建议价 = max(吊灯, 50 日均线 − k×ATR)，而且必须低于现价。"""
    price = _num(price)
    chandelier = _num(chandelier)
    sma50 = _num(sma50)
    atr = _num(atr)
    k = _num(k) if _num(k) is not None else 2.0
    sma_stop = None if sma50 is None or atr is None else sma50 - k * atr
    parts = []
    if chandelier is not None:
        parts.append({"name": "吊灯移动止损", "price": _round(chandelier, 2)})
    if sma_stop is not None:
        parts.append({"name": f"50日均线减 {k:g} 倍波动", "price": _round(sma_stop, 2)})
    below = [part for part in parts if price is not None and part["price"] is not None and part["price"] < price]
    chosen = max(below, key=lambda part: part["price"]) if below else None
    note = ""
    if not parts:
        note = "没有吊灯止损或 50 日均线，给不出建议价。"
    elif chosen is None:
        note = "两条参考都在现价上方，不能当作多头止损。"
    else:
        note = f"取两条里更靠近现价、且仍低于现价的那条：{chosen['name']}。"
    return {
        "k": k,
        "chandelier": _round(chandelier, 2),
        "sma50_stop": _round(sma_stop, 2),
        "price": None if chosen is None else chosen["price"],
        "basis": None if chosen is None else chosen["name"],
        "parts": parts,
        "note": note,
    }


def _option_id(opt):
    return f"{opt.get('underlying') or opt.get('ticker')}:{opt.get('expiry')}:{opt.get('strike')}:{opt.get('right')}"


class Book:
    def __init__(self, cash, nlv, stocks, options, themes):
        self.cash = float(cash)
        self.nlv = float(nlv)
        self.stocks = stocks
        self.options = options
        self.themes = themes

    def clone(self):
        return Book(self.cash, self.nlv, {k: dict(v) for k, v in self.stocks.items()}, [dict(v) for v in self.options], dict(self.themes))

    def stock_mv(self, ticker):
        row = self.stocks.get(ticker)
        if not row or row["price"] is None:
            return 0.0
        return row["shares"] * row["price"]

    def name_exposure(self, ticker):
        extra = 0.0
        price = (self.stocks.get(ticker) or {}).get("price")
        for opt in self.options:
            if (opt.get("underlying") or opt["ticker"]) != ticker:
                continue
            delta = opt.get("delta")
            if delta is not None and price:
                extra += float(opt["contracts"]) * float(opt.get("multiplier") or 100) * float(delta) * price
            elif float(opt.get("contracts") or 0) > 0 and opt.get("market_value"):
                extra += float(opt["market_value"])
        return self.stock_mv(ticker) + extra

    def gross_exposure(self):
        names = set(self.stocks) | {(opt.get("underlying") or opt["ticker"]) for opt in self.options}
        return sum(max(0.0, self.name_exposure(ticker)) for ticker in names)

    def theme_exposure(self, theme_id):
        total = 0.0
        for ticker, row in self.stocks.items():
            if row.get("theme") == theme_id:
                total += max(0.0, self.name_exposure(ticker))
        return total

    def metrics(self, max_pos, max_theme):
        names = []
        for ticker in self.stocks:
            exposure = self.name_exposure(ticker)
            weight = exposure / self.nlv if self.nlv else None
            names.append({"ticker": ticker, "exposure": _round(exposure), "weight": weight})
        names.sort(key=lambda item: item["exposure"] or 0, reverse=True)
        themes = []
        for theme_id, name in self.themes.items():
            exposure = self.theme_exposure(theme_id)
            if exposure <= 1:
                continue
            weight = exposure / self.nlv if self.nlv else None
            themes.append({"id": theme_id, "name": name, "exposure": _round(exposure), "weight": weight})
        themes.sort(key=lambda item: item["exposure"] or 0, reverse=True)
        gross = self.gross_exposure() / self.nlv if self.nlv else None
        worst_name = names[0] if names else None
        worst_theme = themes[0] if themes else None
        return {
            "cash": _round(self.cash),
            "gross": gross,
            "margin": self.cash < -1,
            "worst_name": worst_name,
            "worst_theme": worst_theme,
            "name_breach": any((item["weight"] or 0) > max_pos + 1e-6 for item in names),
            "theme_breach": any((item["weight"] or 0) > max_theme + 1e-6 for item in themes),
        }


def _build_book(port, rows, deltas):
    row_of = {row["ticker"]: row for row in rows}
    themes = {}
    stocks = {}
    for pos in port.get("positions") or []:
        if pos.get("instrument") == "option":
            continue
        ticker = pos["ticker"]
        row = row_of.get(ticker) or {}
        theme = row.get("theme") or "other"
        themes[theme] = row.get("theme_name") or theme
        stocks[ticker] = {
            "shares": float(pos.get("shares") or 0),
            "price": _num(pos.get("price")),
            "theme": theme,
            "theme_name": themes[theme],
            "earnings": _date(row.get("earnings_date")),
            "x3": "X3" in (pos.get("exits") or []),
            "sell": ((pos.get("call") or {}).get("action") == "sell"),
            "rs": row.get("rs_rank"),
            "line": ((pos.get("call") or {}).get("line") or ""),
        }
    options = []
    for pos in port.get("positions") or []:
        if pos.get("instrument") != "option":
            continue
        underlying = pos.get("underlying") or pos["ticker"]
        key = _option_id({"underlying": underlying, "expiry": pos.get("expiry"), "strike": pos.get("strike"), "right": pos.get("right")})
        delta = (deltas or {}).get(key)
        options.append({
            "ticker": pos["ticker"],
            "underlying": underlying,
            "expiry": pos.get("expiry"),
            "strike": pos.get("strike"),
            "right": pos.get("right"),
            "contracts": float(pos.get("contracts") or 0),
            "multiplier": float(pos.get("multiplier") or 100),
            "market_value": _num(pos.get("market_value")),
            "price": _num(pos.get("price")),
            "delta": _num(delta.get("delta") if isinstance(delta, dict) else delta),
            "delta_note": (delta or {}).get("note") if isinstance(delta, dict) else "",
        })
        row = row_of.get(underlying) or {}
        if underlying not in stocks:
            theme = row.get("theme") or "other"
            themes[theme] = row.get("theme_name") or theme
    nlv = _num(port.get("nlv_basis")) or _num(port.get("nlv_estimated")) or 0
    return Book(float(port.get("cash") or 0), nlv, stocks, options, themes)


def _short_calls(book, ticker):
    return [
        opt for opt in book.options
        if opt["underlying"] == ticker and opt["contracts"] < -1e-9 and str(opt.get("right") or "C").upper().startswith("C")
    ]


def _long_calls(book, ticker):
    return [
        opt for opt in book.options
        if opt["underlying"] == ticker and opt["contracts"] > 1e-9 and str(opt.get("right") or "C").upper().startswith("C")
    ]


def _call_exposure(book, ticker):
    """多头看涨按 delta 计入的敞口。没有 delta 时退回权利金。"""
    price = (book.stocks.get(ticker) or {}).get("price") or 0
    total = 0.0
    for opt in _long_calls(book, ticker):
        delta = opt.get("delta")
        if delta is not None and price:
            total += float(opt["contracts"]) * float(opt.get("multiplier") or 100) * float(delta) * price
        elif opt.get("market_value"):
            total += max(0.0, float(opt["market_value"]))
    return total


def _call_unfit(book, ticker, limits):
    """看涨的 delta 自己就已经超过单票上限，没法再和正股一起放进 10%。"""
    return _call_exposure(book, ticker) > limits["name"] * book.nlv + 1


def _excess(book, ticker, limits):
    row = book.stocks[ticker]
    name_gap = max(0.0, book.name_exposure(ticker) - limits["name"] * book.nlv)
    theme_gap = max(0.0, book.theme_exposure(row["theme"]) - limits["theme"] * book.nlv)
    return name_gap + theme_gap


def _stock_room_mv(book, ticker, limits):
    """持有仓的正股最多留到这个市值。看涨单独超限时，先把正股收到单票上限，不把正股减到零。"""
    row = book.stocks[ticker]
    name_cap = limits["name"] * book.nlv
    theme_cap = limits["theme"] * book.nlv
    call = _call_exposure(book, ticker)
    unfit = call > name_cap + 1
    name_room = name_cap if unfit else max(0.0, name_cap - call)
    other = 0.0
    for other_ticker, other_row in book.stocks.items():
        if other_ticker == ticker or other_row.get("theme") != row.get("theme"):
            continue
        if _call_unfit(book, other_ticker, limits):
            other += book.stock_mv(other_ticker)
        else:
            other += max(0.0, book.name_exposure(other_ticker))
    theme_room = theme_cap - other - (0.0 if unfit else call)
    if unfit and call > theme_cap - other:
        theme_room = name_room
    if theme_room < 0:
        theme_room = 0.0
    return max(0.0, min(name_room, theme_room))


def _hold_sell_cap(book, ticker, limits):
    """持有仓最多能卖的整股。剩下的不低于上限，也不会变成零。"""
    row = book.stocks[ticker]
    price = row["price"]
    if not price or price <= 0 or row["shares"] < 1:
        return 0
    room = _stock_room_mv(book, ticker, limits)
    keep = room / price
    if keep < 0.05:
        keep = min(row["shares"], 1.0)
    can_sell = row["shares"] - keep
    if can_sell < 1 - 1e-6:
        return 0
    need = int(math.ceil(can_sell - 1e-9))
    whole = int(math.floor(row["shares"] + 1e-9))
    need = min(need, whole)
    if row["shares"] - need < 0.05:
        need = max(0, need - 1)
    return need


def _gap(book, limits):
    return max(0.0, -book.cash, book.gross_exposure() - limits["gross"] * book.nlv)


def _sell_helps(book, ticker, mode, limits):
    row = book.stocks[ticker]
    if row["shares"] < 1:
        return False
    name_over = book.stock_mv(ticker) > _stock_room_mv(book, ticker, limits) + 1 or book.name_exposure(ticker) > limits["name"] * book.nlv + 1
    if _call_unfit(book, ticker, limits):
        name_over = book.stock_mv(ticker) > limits["name"] * book.nlv + 1
    theme_over = book.theme_exposure(row["theme"]) > limits["theme"] * book.nlv + 1
    if _call_unfit(book, ticker, limits):
        theme_over = _policy_theme(book, row["theme"], limits) > limits["theme"] * book.nlv + 1
    gap = _gap(book, limits) > 1
    if mode == "phase1":
        return gap
    if mode == "phase2":
        return name_over or theme_over
    return gap or name_over or theme_over


def _policy_theme(book, theme_id, limits):
    total = 0.0
    for ticker, row in book.stocks.items():
        if row.get("theme") != theme_id:
            continue
        if _call_unfit(book, ticker, limits):
            total += book.stock_mv(ticker)
        else:
            total += max(0.0, book.name_exposure(ticker))
    return total


def _pending_unfit_calls(book, limits):
    """delta 单独超过单票上限的多头看涨。默认要卖掉，否则单票和主题过不了。"""
    found = []
    seen = set()
    for opt in book.options:
        if opt["contracts"] <= 1e-9:
            continue
        ticker = opt["underlying"]
        if ticker in seen or not _call_unfit(book, ticker, limits):
            continue
        seen.add(ticker)
        found.append(opt)
    found.sort(key=lambda opt: _call_exposure(book, opt["underlying"]), reverse=True)
    return found


def _close_long_call(book, opt, limits):
    ticker = opt["underlying"]
    contracts = abs(float(opt["contracts"]))
    proceeds = abs(float(opt.get("market_value") or 0))
    row = book.stocks.get(ticker) or {}
    book.cash += proceeds
    opt["contracts"] = 0
    opt["market_value"] = 0
    opt["delta"] = 0
    earn = row.get("earnings")
    why = "这张看涨的 delta 已经超过单票 10%，主题也会被它顶过 25%。权利金不高，默认卖掉，正股留在 10% 以内。"
    if earn:
        why += f"财报 {earn.isoformat()} 之前，这是最大的单票风险，所以放在降杠杆的这一步。"
    return {
        "id": f"sellopt:{_option_id(opt)}",
        "status": "open",
        "kind": "sell_option",
        "ticker": ticker,
        "title": f"卖出 {ticker} {opt.get('expiry')} {opt.get('strike')} 看涨 {int(contracts)} 张",
        "why": why,
        "shares": 0,
        "contracts": int(contracts),
        "proceeds": _round(proceeds),
        "shares_after": _round((book.stocks.get(ticker) or {}).get("shares") or 0, 4),
        "after": _after(book, limits),
    }


def _limits_met(book, limits):
    if _pending_unfit_calls(book, limits):
        return False
    metrics = book.metrics(limits["name"], limits["theme"])
    return not metrics["name_breach"] and not metrics["theme_breach"]


def _done(book, mode, limits):
    gap_ok = _gap(book, limits) <= 1
    calls_ok = not _pending_unfit_calls(book, limits)
    if mode == "phase1":
        return gap_ok and calls_ok
    if mode == "phase2":
        return _limits_met(book, limits)
    return gap_ok and _limits_met(book, limits)


def _qty(book, ticker, mode, limits):
    row = book.stocks.get(ticker)
    if not row or not row["price"] or row["price"] <= 0:
        return 0
    whole = int(math.floor(row["shares"] + 1e-6))
    if whole < 1:
        return 0
    if row["sell"]:
        if not _sell_helps(book, ticker, mode, limits):
            return 0
        return whole
    cap = _hold_sell_cap(book, ticker, limits)
    if cap < 1:
        return 0
    if mode == "phase1":
        gap = _gap(book, limits)
        if gap <= 1:
            return 0
        need = int(math.ceil(gap / row["price"] - 1e-9))
        return max(0, min(need, cap))
    if mode == "phase2" or _excess(book, ticker, limits) > 1 or book.stock_mv(ticker) > _stock_room_mv(book, ticker, limits) + 1:
        return cap
    if mode == "full" and _gap(book, limits) > 1 and not any(item["sell"] and item["shares"] >= 1 for item in book.stocks.values()):
        gap = _gap(book, limits)
        need = int(math.ceil(gap / row["price"] - 1e-9))
        return max(0, min(need, cap))
    return 0


def _why(book, ticker, limits):
    row = book.stocks[ticker]
    bits = []
    if row["sell"]:
        bits.append("规则卖出：收盘跌破吊灯移动止损，可以整仓退出" if row["x3"] else "规则卖出，可以整仓退出")
    else:
        bits.append("规则是持有，只减到上限，不会清仓")
    if row["earnings"]:
        bits.append(f"财报 {row['earnings'].isoformat()}")
    name_gap = book.stock_mv(ticker) - limits["name"] * book.nlv
    if row["sell"]:
        if name_gap > 1:
            bits.append(f"正股高出单票上限约 ${name_gap:,.0f}")
        elif book.cash < -1 or book.gross_exposure() > limits["gross"] * book.nlv + 1:
            bits.append("用来把杠杆降到 1 倍并还掉融资")
        return "。".join(bits)
    if _call_unfit(book, ticker, limits):
        bits.append("多头看涨的 delta 自己已经超过单票 10%，所以先减正股")
    theme_gap = _policy_theme(book, row["theme"], limits) - limits["theme"] * book.nlv
    if name_gap > 1:
        bits.append(f"正股高出单票上限约 ${name_gap:,.0f}")
    elif theme_gap > 1:
        bits.append(f"{row['theme_name']}高出主题上限")
    elif book.cash < -1 or book.gross_exposure() > limits["gross"] * book.nlv + 1:
        bits.append("用来把杠杆降到 1 倍并还掉融资")
    return "。".join(bits) or "按限额继续减"


def _after(book, limits):
    metrics = book.metrics(limits["name"], limits["theme"])
    return {
        "cash": metrics["cash"],
        "gross": metrics["gross"],
        "worst_name": metrics["worst_name"],
        "worst_theme": metrics["worst_theme"],
    }


def _apply_sell(book, ticker, qty):
    row = book.stocks[ticker]
    proceeds = qty * row["price"]
    row["shares"] -= qty
    book.cash += proceeds
    return proceeds


def _plan(book, mode, limits):
    steps = []
    guard = 0
    while guard < 24:
        guard += 1
        if _done(book, mode, limits):
            break
        ranked = _candidates(book, limits)
        acted = False
        for ticker in ranked:
            if not book.stocks[ticker]["sell"] and _pending_unfit_calls(book, limits):
                steps.append(_close_long_call(book, _pending_unfit_calls(book, limits)[0], limits))
                acted = True
                break
            shorts = _short_calls(book, ticker)
            qty = _qty(book, ticker, mode, limits)
            if qty < 1:
                continue
            cover = sum(abs(opt["contracts"]) * opt["multiplier"] for opt in shorts)
            if shorts and book.stocks[ticker]["shares"] - qty < cover - 1e-6:
                opt = shorts[0]
                contracts = abs(float(opt["contracts"]))
                cost = abs(contracts * opt["multiplier"] * float(opt["price"] or 0))
                if cost <= 0:
                    cost = abs(float(opt["market_value"] or 0))
                book.cash -= cost
                opt["contracts"] = 0
                opt["market_value"] = 0
                steps.append({
                    "id": f"buyback:{_option_id(opt)}",
                    "status": "open",
                    "kind": "buyback",
                    "ticker": ticker,
                    "title": f"先买回 {ticker} {opt.get('expiry')} {opt.get('strike')} 看涨 {int(contracts)} 张",
                    "why": "正股还覆盖着这张空头看涨。先买回，否则卖掉正股会变成裸卖。",
                    "shares": 0,
                    "contracts": int(contracts),
                    "proceeds": _round(-cost),
                    "shares_after": _round(book.stocks[ticker]["shares"], 4),
                    "after": _after(book, limits),
                })
                acted = True
                break
            why = _why(book, ticker, limits)
            proceeds = _apply_sell(book, ticker, qty)
            left = book.stocks[ticker]["shares"]
            steps.append({
                "id": f"sell:{ticker}:{int(math.floor(left + 1e-6))}",
                "status": "open",
                "kind": "sell",
                "ticker": ticker,
                "title": f"卖出 {ticker} {qty} 股",
                "why": why,
                "shares": qty,
                "proceeds": _round(proceeds),
                "shares_after": _round(left, 4),
                "fraction_left": _round(left, 4) if left > 0.01 and left < 1 else None,
                "after": _after(book, limits),
            })
            acted = True
            break
        if not acted:
            pending = _pending_unfit_calls(book, limits)
            if pending:
                steps.append(_close_long_call(book, pending[0], limits))
                continue
            break
    return steps


def _candidates(book, limits):
    """先卖规则给出卖出的，再按最弱相对强度、超限更多来减持有仓。"""

    def sell_key(ticker):
        row = book.stocks[ticker]
        return (row["earnings"] or FAR, -_excess(book, ticker, limits))

    def hold_key(ticker):
        row = book.stocks[ticker]
        rs = row["rs"] if row["rs"] is not None else -1
        over = 0 if _excess(book, ticker, limits) > 1 or book.stock_mv(ticker) > _stock_room_mv(book, ticker, limits) + 1 else 1
        return (over, rs, -_excess(book, ticker, limits))

    sells = [ticker for ticker, row in book.stocks.items() if row["sell"]]
    holds = [ticker for ticker, row in book.stocks.items() if not row["sell"]]
    return sorted(sells, key=sell_key) + sorted(holds, key=hold_key)


def _load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _mark_done(previous, book):
    done = []
    seen = set()
    for step in previous or []:
        if step.get("id") in seen:
            continue
        status = step.get("status")
        ticker = step.get("ticker")
        row = book.stocks.get(ticker) or {}
        finished = status == "done"
        if step.get("kind") == "sell":
            finished = finished or row.get("shares", 0) <= float(step.get("shares_after") or 0) + 0.05
        elif step.get("kind") in ("buyback", "sell_option"):
            live = [
                opt for opt in book.options
                if _option_id(opt) == step["id"].split(":", 1)[-1]
            ]
            contracts = live[0]["contracts"] if live else 0
            if step["kind"] == "buyback":
                finished = finished or contracts >= -1e-9
            else:
                finished = finished or abs(contracts) < 0.01
        if not finished:
            continue
        kept = dict(step)
        kept["status"] = "done"
        done.append(kept)
        seen.add(step.get("id"))
    return done


def _fmt_shares(number):
    text = f"{float(number):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _allocation(book, limits):
    rows = []
    exited = []
    for ticker, row in book.stocks.items():
        live_opts = [opt for opt in book.options if opt["underlying"] == ticker and abs(opt["contracts"]) > 0.01]
        if row["shares"] < 1 and not live_opts:
            if row["shares"] > 0.01:
                exited.append(f"{ticker}（剩 {_fmt_shares(row['shares'])} 股）")
            elif row["sell"]:
                exited.append(ticker)
            continue
        bits = []
        if row["shares"] > 0.05:
            bits.append(f"{_fmt_shares(row['shares'])} 股")
        for opt in live_opts:
            side = "空头 " if opt["contracts"] < 0 else ""
            right = "看涨" if str(opt.get("right") or "C").upper().startswith("C") else "看跌"
            bits.append(f"{side}{opt.get('expiry')} {opt.get('strike')} {right} {int(abs(opt['contracts']))} 张")
        exposure = book.name_exposure(ticker)
        rows.append({
            "ticker": ticker,
            "kept": " + ".join(bits),
            "shares": _round(row["shares"], 4),
            "weight": (exposure / book.nlv) if book.nlv else None,
            "stock_weight": (book.stock_mv(ticker) / book.nlv) if book.nlv else None,
            "exposure": _round(exposure),
        })
    rows.sort(key=lambda item: item["exposure"] or 0, reverse=True)
    metrics = book.metrics(limits["name"], limits["theme"])
    return {
        "rows": rows,
        "exited": exited,
        "cash": metrics["cash"],
        "gross": metrics["gross"],
    }


def _keep_call_warnings(before, after, limits):
    """默认已经卖掉超限的看涨。留下它只作为标了警告的备选。"""
    notes = []
    seen = set()
    for opt in before.options:
        ticker = opt.get("underlying")
        if not ticker or ticker in seen or opt["contracts"] <= 1e-9:
            continue
        if not _call_unfit(before, ticker, limits) or _call_unfit(after, ticker, limits):
            continue
        seen.add(ticker)
        calls = _long_calls(before, ticker)
        call = _call_exposure(before, ticker)
        proceeds = sum(abs(float(item.get("market_value") or 0)) for item in calls)
        row = after.stocks.get(ticker) or {}
        combined = (after.stock_mv(ticker) + call) / after.nlv if after.nlv else None
        theme_w = (after.theme_exposure(row.get("theme")) + call) / after.nlv if after.nlv else None
        gross = (after.gross_exposure() + call) / after.nlv if after.nlv else None
        label = "、".join(
            f"{item.get('expiry')} {item.get('strike')} 看涨 {int(abs(item['contracts']))} 张" for item in calls
        )
        text = f"⚠️ 仍超单票/主题上限：若留下 {ticker} {label}，单票和主题都会超过上限。默认计划卖掉这张看涨。"
        if combined is not None and theme_w is not None and gross is not None:
            text = (
                f"⚠️ 仍超单票/主题上限：若留下 {ticker} {label}，"
                f"正股 {_fmt_shares(row.get('shares') or 0)} 股加上 delta 约 ${call:,.0f}，这一只占 {combined:.0%}，"
                f"{row.get('theme_name') or '主题'} 到 {theme_w:.0%}。杠杆大约 {gross:.0%}，现金少大约 ${proceeds:,.0f}。"
                f"默认计划卖掉这张看涨。"
            )
        notes.append({
            "ticker": ticker,
            "warning": True,
            "text": text,
            "call_exposure": _round(call),
            "proceeds": _round(proceeds),
            "weight_if_kept": combined,
            "theme_if_kept": theme_w,
            "gross_if_kept": gross,
        })
    return notes


def _pack(steps, before):
    done = [step for step in steps if step.get("status") == "done"]
    open_steps = [step for step in steps if step.get("status") != "done"]
    target = sum(max(0.0, float(step.get("proceeds") or 0)) for step in open_steps)
    done_proceeds = sum(max(0.0, float(step.get("proceeds") or 0)) for step in done)
    return {
        "before": before,
        "steps": steps,
        "progress": {
            "done": len(done),
            "total": len(steps),
            "done_proceeds": _round(done_proceeds),
            "remaining_proceeds": _round(target),
        },
    }


def build(port, rows, rules, deltas=None, bar_date=None):
    limits = {
        "gross": float(rules["account"]["max_gross"]),
        "name": float(rules["account"]["max_pos_pct"]),
        "theme": float(rules["account"]["max_theme_pct"]),
        "open_risk": float(rules["account"]["max_open_risk"]),
    }
    k = float((rules.get("stops") or {}).get("suggest_sma50_atr") or 2)
    book = _build_book(port, rows, deltas)
    row_of = {row["ticker"]: row for row in rows}
    stops = []
    filled = 0
    missing = 0
    partial = 0.0
    for pos in port.get("positions") or []:
        if pos.get("instrument") == "option":
            continue
        ticker = pos["ticker"]
        row = row_of.get(ticker) or {}
        levels = row.get("levels") or {}
        suggestion = suggest_stop(pos.get("price"), levels.get("chandelier"), row.get("sma50") or levels.get("sma50"), row.get("atr14"), k)
        stop = _num(pos.get("stop"))
        risk = None
        if stop is not None and pos.get("price") is not None:
            risk = max(0.0, float(pos["price"]) - stop) * float(pos.get("shares") or 0)
            partial += risk
            filled += 1
        else:
            missing += 1
        stops.append({
            "key": f"stk:{ticker}",
            "ticker": ticker,
            "suggest": suggestion,
            "stop": stop,
            "avg_cost": _num(pos.get("avg_cost")),
            "entry_date": pos.get("entry_date"),
            "stop_placed": bool(pos.get("stop_placed")),
            "risk_usd": _round(risk),
            "price": _num(pos.get("price")),
        })
    nlv = book.nlv
    state = _load_state()
    before = _after(book, limits)
    before["gross_market_value"] = port.get("gross")
    full_book = book.clone()
    full_steps = _mark_done(state.get("full"), book) + _plan(full_book, "full", limits)
    phase_book = book.clone()
    phase1 = _plan(phase_book, "phase1", limits)
    phase2 = _plan(phase_book, "phase2", limits)
    staged_steps = _mark_done(state.get("staged"), book) + phase1 + phase2
    full_final = _allocation(full_book, limits)
    staged_final = _allocation(phase_book, limits)
    full_alt = _keep_call_warnings(book, full_book, limits)
    staged_alt = _keep_call_warnings(book, phase_book, limits)
    # 分两步的阶段边界用第一段的步数，已完成的旧步骤算在进度里，不重排进某一段。
    done_count = len(_mark_done(state.get("staged"), book))
    payload = {
        "bar_date": str(bar_date) if bar_date else None,
        "nlv": _round(nlv),
        "limits": limits,
        "suggest_k": k,
        "stops": stops,
        "stops_filled": filled,
        "stops_missing": missing,
        "open_risk_usd": None if missing else _round(partial),
        "open_risk_partial_usd": _round(partial),
        "open_risk_cap_usd": _round(limits["open_risk"] * nlv) if nlv else None,
        "delta_notes": [opt["delta_note"] for opt in book.options if opt.get("delta_note")],
        "options": [
            {
                "id": _option_id(opt),
                "underlying": opt["underlying"],
                "expiry": opt.get("expiry"),
                "strike": opt.get("strike"),
                "right": opt.get("right"),
                "contracts": opt["contracts"],
                "delta": opt.get("delta"),
                "note": opt.get("delta_note") or "",
            }
            for opt in book.options
        ],
        "plans": {
            "full": _pack(full_steps, before) | {"final": full_final, "alternatives": full_alt},
            "staged": _pack(staged_steps, before) | {
                "phases": [
                    {"id": "phase1", "title": "10/15 前，先降到 1 倍以内", "count": len(phase1)},
                    {"id": "phase2", "title": "10/29 前，单票 ≤10%、主题 ≤25%", "count": len(phase2)},
                ],
                "done_count": done_count,
                "final": staged_final,
                "alternatives": staged_alt,
            },
        },
        "notice": "这是按限额排的减仓计划，不是下单指令。默认做完后，杠杆、现金、单票和主题都在上限以内。规则给出卖出的可以整仓退出；规则是持有的只减到上限，不会减到零。买卖仍以规则引擎为准。",
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({
        "full": full_steps,
        "staged": staged_steps,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
