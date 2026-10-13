"""把扫描行和持仓收成一句人话。数字只用规则引擎已经算出的字段。"""

import glossary

NAMES = glossary.SHORT

SELL = {"X1", "X3", "X5"}
TRIM = {"X2", "X4", "X6", "X7", "X8"}
ENTRY = {"E1", "E2", "E3", "E4", "E5"}


def _px(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):.2f}"


def _rank(value) -> str:
    return "—" if value is None else str(int(value))


def _lead(candidates: list, rules: dict) -> dict | None:
    order = {key: i for i, key in enumerate(rules.get("priority") or [])}

    def pick(pool):
        chosen = list(pool)
        chosen.sort(key=lambda c: order.get(c.get("id"), 99))
        return chosen[0] if chosen else None

    ok = pick(c for c in candidates if c.get("ok"))
    if ok:
        return ok
    return pick(c for c in candidates if c.get("technical"))


def _block(row: dict, port: dict, rules: dict) -> str:
    bits = []
    regime = row.get("regime")
    if regime == "RED":
        bits.append("大盘红灯")
    elif regime == "YELLOW":
        bits.append("大盘黄灯")
    gross = port.get("gross")
    cap = (rules.get("account") or {}).get("max_gross") or 1
    if port.get("using_margin") or (gross is not None and gross > cap + 1e-9):
        shown = f"{gross * 100:.0f}%" if gross is not None else "未知"
        bits.append(f"总敞口 {shown} 超过 {cap * 100:.0f}% 上限")
    if port.get("stops_missing"):
        bits.append("持仓止损没填，风险合计未知")
    for reason in row.get("rejected_by") or []:
        if any(word in reason for word in ("主题", "单票", "本周", "回撤", "冷却")):
            bits.append(reason)
    return "、".join(bits)


def _plan(row: dict, blocked: bool) -> dict:
    entry = row.get("entry")
    stop = row.get("stop")
    dist = (entry - stop) if entry is not None and stop is not None and entry > stop else None
    model = row.get("shares_model") or 0
    risk_model = row.get("risk_usd_model")
    if not risk_model and dist and model:
        risk_model = model * dist
    text = f"入场 {_px(entry)}，止损 {_px(stop)}"
    if dist:
        text += f"，1R {_px(entry + dist)}，2R {_px(entry + 2 * dist)}"
    if blocked:
        text += f"。公式 {model} 股，模型风险 ${risk_model or 0:,.0f}。规则没有放行，建议股数是 0"
    else:
        text += f"。建议 {row.get('shares') or 0} 股，风险 ${row.get('risk_usd') or 0:,.0f}"
    return {
        "entry": entry,
        "stop": stop,
        "shares": 0 if blocked else (row.get("shares") or 0),
        "shares_model": model,
        "risk_usd": 0 if blocked else (row.get("risk_usd") or 0),
        "risk_model": risk_model,
        "text": text + "。",
    }


def _invalidate(code: str, cand: dict, row: dict) -> str:
    stop = _px(row.get("stop") or cand.get("stop"))
    low = _px(cand.get("base_low"))
    levels = row.get("levels") or {}
    if code == "E1":
        return f"收盘跌破突破结构低点 {low}，或跌破止损 {stop}，这个突破就失效。"
    if code == "E2":
        return f"收盘跌破回踩低点 {low}，回踩买点失效。止损放在 {stop}。"
    if code == "E3":
        return f"收盘跌破 10 日均线 {_px(levels.get('ema10'))}，或跌破止损 {stop}，口袋支点失效。"
    if code == "E4":
        return f"收盘跌破跳空当天低点 {low}，财报买点失效。"
    if code == "E5":
        bars = cand.get("time_stop") or 10
        return f"短线 {bars} 根 K 线内到不了 1R，或收盘跌破止损 {stop}，挤压买点失效。"
    return f"收盘跌破止损 {stop} 则失效。"


def _checks(cand: dict, row: dict) -> list:
    checks = list(cand.get("facts") or [])
    need = cand.get("pending_rs")
    if need is not None:
        rank = row.get("rs_rank")
        checks.append({
            "label": "相对强度排名",
            "pass": None if rank is None else bool(rank >= need),
            "text": f"观察列表内排名 {_rank(rank)}，这条需要 ≥ {need}",
        })
    if cand.get("pending_sector"):
        ok = row.get("sector_rs_ok")
        checks.append({
            "label": "行业相对强度",
            "pass": ok,
            "text": "行业 ETF 强于 SPY" if ok else ("数据不足" if ok is None else "行业 ETF 不强于 SPY"),
        })
    if not checks and cand.get("reason"):
        checks.append({"label": "条件", "pass": bool(cand.get("ok")), "text": cand["reason"]})
    return checks


def _line(ticker: str, cand: dict, row: dict) -> str:
    blurb = cand.get("blurb") or NAMES.get(cand.get("id"), "有技术信号")
    score = row.get("c_score")
    score_txt = "买入确认分缺失" if score is None else f"买入确认分 {score}/3"
    return f"{ticker}：{blurb}，相对强度排名 {_rank(row.get('rs_rank'))}，{score_txt}"


def _empty_line(row: dict) -> str:
    ticker = row["ticker"]
    price = _px(row.get("price"))
    if row.get("above_sma200") is False:
        where = "在 200 日均线之下"
    elif row.get("above_sma50"):
        where = "在 50 日均线之上"
    else:
        where = "没有站上 50 日均线"
    return f"{ticker}：没有突破、回踩、口袋支点、财报跳空或挤压买点。收盘 {price}，{where}，相对强度排名 {_rank(row.get('rs_rank'))}"


def row_call(row: dict, port: dict, rules: dict, holding: dict | None) -> dict:
    exit_call = position_call(holding, row, port, rules) if holding and holding.get("instrument") != "option" else None
    if exit_call and exit_call["action"] in ("sell", "trim"):
        return exit_call
    cand = _lead(row.get("candidates") or [], rules)
    if cand and cand.get("id") in ENTRY and (cand.get("ok") or cand.get("technical")):
        action = "buy" if cand.get("ok") and row.get("shares") else "watch"
        if action == "watch" and cand.get("ok"):
            block = _block(row, port, rules)
        elif action == "watch":
            block = cand.get("reason") or "技术条件没有全部通过"
        else:
            block = ""
        return {
            "ticker": row["ticker"],
            "action": action,
            "action_label": "买入候选" if action == "buy" else "观望",
            "block": block,
            "line": _line(row["ticker"], cand, row),
            "code": cand["id"],
            "name": NAMES[cand["id"]],
            "c_tag": None if row.get("c_score") is None else f"C1:{row['c_score']}",
            "checks": _checks(cand, row),
            "confirmation": (row.get("c_detail") or {}).get("items") or [],
            "plan": _plan(row, action != "buy"),
            "invalidate": _invalidate(cand["id"], cand, row),
            "pending": [],
        }
    if row.get("held"):
        return exit_call or _hold(row)
    return {
        "ticker": row["ticker"],
        "action": "watch",
        "action_label": "观望",
        "block": "",
        "line": _empty_line(row),
        "code": None,
        "name": "",
        "c_tag": None if row.get("c_score") is None else f"C1:{row['c_score']}",
        "checks": [],
        "confirmation": (row.get("c_detail") or {}).get("items") or [],
        "plan": None,
        "invalidate": "出现突破、回踩、口袋支点、财报跳空或挤压买点，且组合限额允许时，才从观望改成买入候选。",
        "pending": [],
    }


def _hold(row: dict) -> dict:
    price = _px(row.get("price"))
    ch = _px((row.get("levels") or {}).get("chandelier"))
    return {
        "ticker": row["ticker"],
        "action": "hold",
        "action_label": "持有",
        "block": "",
        "line": f"{row['ticker']}：没有新的离场条件。收盘 {price}，吊灯移动止损 {ch}",
        "code": None,
        "name": "",
        "c_tag": None,
        "checks": [],
        "confirmation": [],
        "plan": None,
        "invalidate": f"收盘跌破吊灯止损 {ch} 会变成卖出。",
        "pending": [],
    }


def _exit_checks(pos: dict, row: dict) -> tuple[list, str]:
    levels = row.get("levels") or {}
    price = row.get("price")
    checks = []
    bits = []
    exits = set(pos.get("exits") or [])
    horizon = pos.get("horizon") or "position"
    if "X1" in exits:
        checks.append({"label": "固定止损", "pass": True, "text": f"最低价碰到止损 {_px(pos.get('stop'))}"})
        bits.append(f"最低价碰到止损 {_px(pos.get('stop'))}")
    if "X3" in exits:
        if horizon == "swing":
            line = levels.get("ema10") if (row.get("rs_rank") or 0) >= 90 else levels.get("ema21")
            name = "10 日均线" if (row.get("rs_rank") or 0) >= 90 else "21 日均线"
            text = f"收盘 {_px(price)} 跌破{name} {_px(line)}"
        else:
            text = f"收盘 {_px(price)} 跌破吊灯移动止损 {_px(levels.get('chandelier'))}"
        checks.append({"label": "跌破吊灯移动止损", "pass": True, "text": text})
        bits.append(text)
    if "X4" in exits:
        text = f"收盘 {_px(price)} 相对 50 日均线 {_px(row.get('sma50'))}，量比 {_px(row.get('rvol'))}"
        if levels.get("avwap_earn") and price is not None and price < levels["avwap_earn"]:
            text += f"，并低于财报锚定均价 {_px(levels.get('avwap_earn'))}"
        checks.append({"label": "跌破均线减仓", "pass": True, "text": text})
        bits.append("放量跌破 50 日均线或财报锚定均价，规则是减半")
    if "X6" in exits:
        why = "；".join(row.get("x6_why") or []) or "出现高潮或过度延伸"
        checks.append({"label": "高潮止盈", "pass": True, "text": why})
        bits.append(why)
    if "X7" in exits:
        note = next((n for n in pos.get("exit_notes") or [] if n.startswith("按 ")), "财报前跳空测试没通过")
        checks.append({"label": "财报前减仓", "pass": True, "text": note})
        bits.append(note)
    if "X8" in exits:
        why = "；".join(row.get("x8_why") or []) or "相对强度走坏或高位派发"
        checks.append({"label": "强度走坏减仓", "pass": True, "text": why})
        bits.append(why)
    if "X2" in exits:
        note = next((n for n in pos.get("exit_notes") or [] if n.startswith("X2")), "突破失败")
        checks.append({"label": "突破失败", "pass": True, "text": note})
        bits.append(note)
    if "X5" in exits:
        note = next((n for n in pos.get("exit_notes") or [] if n.startswith("X5")), "短线时间到了还没有 1R")
        checks.append({"label": "时间止损", "pass": True, "text": note})
        bits.append(note)
    return checks, "；".join(bits)


def _pending_checks(pos: dict) -> list:
    labels = {
        "X1": "在持仓里填上止损价。不填的话，价格碰到止损也判断不了",
        "X2": "在交易日志里写下这次突破的枢轴价和突破当天的最低价。不写就无法判断突破有没有失败",
        "X5": "填上建仓日、成本和止损价。不填就无法判断短线是否已经超时",
        "A1": "填上成本和止损价，才能算出一倍风险距离。不填就不能判断能不能加仓",
    }
    return [{"label": NAMES.get(code, code), "pass": None, "text": labels.get(code, "输入缺失")} for code in pos.get("rules_null") or []]


def position_call(pos: dict | None, row: dict | None, port: dict, rules: dict) -> dict | None:
    if not pos or pos.get("instrument") == "option" or not row:
        return None
    exits = set(pos.get("exits") or [])
    full_x2 = any("全部离场" in n for n in pos.get("exit_notes") or [])
    if exits & SELL or ("X2" in exits and full_x2):
        action, label = "sell", "卖出"
    elif exits & TRIM or "X2" in exits:
        action, label = "trim", "减仓"
    else:
        action, label = "hold", "持有"
    checks, detail = _exit_checks(pos, row)
    pending = _pending_checks(pos)
    if action == "hold":
        base = _hold(row)
        base["pending"] = pending
        base["checks"] = pending
        return base
    codes = [c for c in ("X1", "X2", "X3", "X5", "X4", "X6", "X7", "X8") if c in exits]
    invalidate = _exit_invalidate(exits, row)
    return {
        "ticker": pos["ticker"],
        "action": action,
        "action_label": label,
        "block": "",
        "line": f"{pos['ticker']}：{detail}",
        "code": codes[0] if codes else None,
        "name": "、".join(NAMES[c] for c in codes),
        "c_tag": "+".join(codes),
        "checks": checks + pending,
        "confirmation": [],
        "plan": {"text": "卖出剩余仓位。" if action == "sell" else "减一部分，不是一次性清仓。", "entry": None, "stop": pos.get("stop"), "shares": 0, "shares_model": 0, "risk_usd": 0, "risk_model": 0},
        "invalidate": invalidate,
        "pending": [c for c in pos.get("rules_null") or []],
    }


def _exit_invalidate(exits: set, row: dict) -> str:
    levels = row.get("levels") or {}
    notes = []
    if "X3" in exits:
        notes.append(f"收盘重新站上吊灯移动止损 {_px(levels.get('chandelier'))} 后，这条就不再成立")
    if "X4" in exits:
        notes.append(f"收盘回到 50 日均线 {_px(row.get('sma50'))} 之上且不再放量，减半条件取消")
    if "X6" in exits:
        notes.append("高潮条件消失后，止盈可以停在已经减掉的部分")
    if not notes:
        notes.append("条件消失后这条离场就不再成立")
    return "。".join(notes) + "。"


def _scrub(call: dict | None) -> dict | None:
    if not call:
        return call
    for key in ("line", "name", "block", "invalidate"):
        if call.get(key):
            call[key] = glossary.plain(call[key])
    if call.get("plan") and call["plan"].get("text"):
        call["plan"]["text"] = glossary.plain(call["plan"]["text"])
    for item in (call.get("checks") or []) + (call.get("confirmation") or []):
        if item.get("label"):
            item["label"] = glossary.plain(item["label"])
        if item.get("text"):
            item["text"] = glossary.plain(item["text"])
    return call


def attach(rows: list, port: dict, rules: dict):
    held = {}
    for pos in port.get("positions") or []:
        if pos.get("instrument") == "stock":
            held[pos["ticker"]] = pos
    by_row = {r["ticker"]: r for r in rows}
    for row in rows:
        row["call"] = _scrub(row_call(row, port, rules, held.get(row["ticker"])))
    for pos in port.get("positions") or []:
        if pos.get("instrument") != "stock":
            pos["call"] = None
            continue
        pos["call"] = _scrub(position_call(pos, by_row.get(pos["ticker"]), port, rules))
