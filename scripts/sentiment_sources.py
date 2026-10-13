"""免费、无需密钥的情绪数据。每个源独立超时和重试，失败时沿用上次的数。"""

from __future__ import annotations

import csv
import io
import json
import math
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

RATING_CN = {
    "extreme fear": "极度恐惧",
    "fear": "恐惧",
    "neutral": "中性",
    "greed": "贪婪",
    "extreme greed": "极度贪婪",
}

COMPONENTS = (
    ("market_momentum_sp500", "动量"),
    ("stock_price_strength", "新高/新低"),
    ("stock_price_breadth", "涨跌宽度"),
    ("put_call_options", "看跌/看涨"),
    ("market_volatility_vix_50", "VIX 相对 50 日"),
    ("junk_bond_demand", "垃圾债需求"),
    ("safe_haven_demand", "避险需求"),
)

PC_NAMES = {
    "total": "TOTAL PUT/CALL RATIO",
    "index": "INDEX PUT/CALL RATIO",
    "etp": "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO",
    "equity": "EQUITY PUT/CALL RATIO",
    "vix": "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO",
    "spx": "SPX + SPXW PUT/CALL RATIO",
}

NOTE_MARKS = (
    "恐惧与贪婪",
    "高收益债",
    "看跌/看涨",
    "AAII",
    "VIX 期限",
    "情绪读数不计入",
    "没有稳定的免费源",
)


class _Missing(Exception):
    pass


def _num(value, digits):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _fmt(value, digits=1):
    number = _num(value, digits)
    if number is None:
        return "—"
    text = f"{number:.{digits}f}"
    if digits == 1:
        text = text.rstrip("0").rstrip(".")
    return text


def _store_path(root: Path) -> Path:
    return root / "data" / "sentiment_store.json"


def load_store(root: Path) -> dict:
    path = _store_path(root)
    if not path.exists():
        return {"latest": {}, "history": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"latest": {}, "history": {}}
    data.setdefault("latest", {})
    data.setdefault("history", {})
    return data


def save_store(root: Path, store: dict):
    path = _store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    store["updated_at"] = datetime.now(ET).isoformat()
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _upsert(rows: list, row: dict, limit: int) -> list:
    date = row.get("date")
    kept = [item for item in rows if item.get("date") != date]
    kept.append(row)
    kept.sort(key=lambda item: item.get("date") or "")
    return kept[-limit:]


def http_bytes(url: str, timeout: float, headers: dict | None = None, retries: int = 2) -> bytes:
    last = None
    hdrs = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise _Missing(str(exc.code)) from exc
            last = exc
        except Exception as exc:
            last = exc
        if attempt + 1 < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError(url)


def _stored(store: dict, key: str, reason: Exception):
    latest = (store.get("latest") or {}).get(key)
    text = f"{type(reason).__name__}: {reason}"
    if not latest:
        print(f"情绪源 {key} 失败，没有上次的数：{text}", flush=True)
        return None
    item = json.loads(json.dumps(latest))
    item["stale"] = True
    print(f"情绪源 {key} 失败，沿用 {item.get('date')}：{text}", flush=True)
    return item


def _cnn_day(value) -> str | None:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).astimezone(ET).date().isoformat()
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 10**12 else value
        return datetime.fromtimestamp(seconds, ET).date().isoformat()
    return None


def _raw_label(cid: str, raw, vix_level) -> str:
    if raw is None:
        return ""
    if cid == "market_momentum_sp500":
        return f"标普 {raw:,.0f}"
    if cid == "put_call_options":
        return f"{raw:.2f}"
    if cid == "market_volatility_vix_50":
        if vix_level is not None:
            return f"VIX {vix_level:.2f} / 50日 {raw:.2f}"
        return f"50日 {raw:.2f}"
    return f"原始值 {raw:.2f}"


def _history_from_cnn(block: dict, limit: int = 90) -> list:
    points = []
    for item in (block or {}).get("data") or []:
        day = _cnn_day(item.get("x"))
        score = _num(item.get("y"), 1)
        if day and score is not None:
            points.append({"date": day, "score": score})
    dedup = {}
    for point in points:
        dedup[point["date"]] = point
    ordered = [dedup[key] for key in sorted(dedup)]
    return ordered[-limit:]


def fetch_fear_greed(store: dict) -> dict | None:
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    try:
        raw = http_bytes(
            url,
            timeout=20,
            headers={"Referer": "https://www.cnn.com/", "Accept": "application/json"},
            retries=2,
        )
        data = json.loads(raw.decode())
        fg = data.get("fear_and_greed") or {}
        score = _num(fg.get("score"), 1)
        if score is None:
            raise ValueError("没有 score")
        rating = fg.get("rating") or ""
        hist_block = data.get("fear_and_greed_historical") or {}
        vix_level = None
        vix_block = data.get("market_volatility_vix") or {}
        vix_points = vix_block.get("data") or []
        if vix_points:
            vix_level = _num(vix_points[-1].get("y"), 2)
        components = []
        for cid, name in COMPONENTS:
            block = data.get(cid) or {}
            points = block.get("data") or []
            raw_y = _num(points[-1].get("y"), 2) if points else None
            comp_rating = block.get("rating") or ""
            components.append({
                "id": cid,
                "name": name,
                "score": _num(block.get("score"), 1),
                "rating": comp_rating,
                "rating_cn": RATING_CN.get(comp_rating, comp_rating),
                "raw": raw_y,
                "raw_label": _raw_label(cid, raw_y, vix_level),
            })
        value = {
            "score": score,
            "rating": rating,
            "rating_cn": RATING_CN.get(rating, rating),
            "date": _cnn_day(fg.get("timestamp")),
            "previous_close": _num(fg.get("previous_close"), 1),
            "previous_1_week": _num(fg.get("previous_1_week"), 1),
            "previous_1_month": _num(fg.get("previous_1_month"), 1),
            "previous_1_year": _num(fg.get("previous_1_year"), 1),
            "stale": False,
            "source": "CNN",
            "unofficial": True,
            "history": _history_from_cnn(hist_block),
            "components": components,
        }
        snapshot = {
            "date": value["date"],
            "score": score,
            "rating": rating,
            "previous_close": value["previous_close"],
            "previous_1_week": value["previous_1_week"],
            "previous_1_month": value["previous_1_month"],
            "previous_1_year": value["previous_1_year"],
            "components": [
                {"id": item["id"], "score": item["score"], "rating": item["rating"], "raw": item["raw"]}
                for item in components
            ],
        }
        if snapshot["date"]:
            store["history"]["fear_greed"] = _upsert(store["history"].get("fear_greed") or [], snapshot, 400)
        store["latest"]["fear_greed"] = value
        print(f"情绪源 fear_greed {value['date']} {score} {rating}", flush=True)
        return value
    except Exception as exc:
        return _stored(store, "fear_greed", exc)


def _weekdays(start, count: int) -> list:
    days = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return days


def _one_put_call(day):
    url = f"https://cdn.cboe.com/data/us/options/market_statistics/daily/{day.isoformat()}_daily_options"
    try:
        raw = http_bytes(url, timeout=12, retries=2)
    except _Missing:
        return None
    except Exception as exc:
        print(f"情绪源 put_call {day.isoformat()} 跳过：{type(exc).__name__}: {exc}", flush=True)
        return None
    data = json.loads(raw.decode())
    ratios = {}
    for item in data.get("ratios") or []:
        name = item.get("name")
        number = _num(item.get("value"), 2)
        if name and number is not None:
            ratios[name] = number
    row = {"date": day.isoformat()}
    for key, name in PC_NAMES.items():
        row[key] = ratios.get(name)
    if row.get("total") is None and row.get("equity") is None:
        return None
    return row


def _average(rows: list, key: str):
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def fetch_put_call(store: dict) -> dict | None:
    try:
        start = datetime.now(ET).date()
        days = _weekdays(start, 14)
        found = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for row in pool.map(_one_put_call, days):
                if row:
                    found.append(row)
        if not found:
            raise RuntimeError("近 14 个工作日都没有取到")
        history = list(store["history"].get("put_call") or [])
        for row in found:
            history = _upsert(history, row, 80)
        store["history"]["put_call"] = history
        ordered = sorted(history, key=lambda item: item["date"])
        latest = ordered[-1]
        last5 = ordered[-5:]
        last10 = ordered[-10:]
        value = {
            "date": latest["date"],
            "stale": False,
            "source": "CBOE",
            "unofficial": True,
            "total": latest.get("total"),
            "equity": latest.get("equity"),
            "index": latest.get("index"),
            "etp": latest.get("etp"),
            "vix": latest.get("vix"),
            "spx": latest.get("spx"),
            "avg_5d": {
                "equity": _average(last5, "equity"),
                "total": _average(last5, "total"),
                "n": len(last5),
            },
            "avg_10d": {
                "equity": _average(last10, "equity"),
                "total": _average(last10, "total"),
                "n": len(last10),
            },
            "history": [{"date": row["date"], "equity": row.get("equity"), "total": row.get("total")} for row in ordered[-20:]],
        }
        store["latest"]["put_call"] = value
        print(
            f"情绪源 put_call {value['date']} 股票 {value.get('equity')} 合计 {value.get('total')} "
            f"（本次取到 {len(found)} 天）",
            flush=True,
        )
        return value
    except Exception as exc:
        return _stored(store, "put_call", exc)


def _parse_cboe_index(text: str) -> list:
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for rec in reader:
        raw_date = (rec.get("DATE") or rec.get("Date") or "").strip()
        close = rec.get("CLOSE") if rec.get("CLOSE") not in (None, "") else rec.get("Close")
        number = _num(close, 4)
        if not raw_date or number is None:
            continue
        day = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
        rows.append((day, number))
    return rows


def _yf_closes(symbol: str) -> list:
    import yfinance as yf

    frame = yf.download(symbol, period="6mo", interval="1d", auto_adjust=True, progress=False, threads=False)
    close = frame["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    rows = []
    for stamp, value in close.dropna().items():
        day = stamp.date().isoformat() if hasattr(stamp, "date") else str(stamp)[:10]
        number = _num(value, 4)
        if number is not None:
            rows.append((day, number))
    return rows


def fetch_vix_term(store: dict) -> dict | None:
    try:
        series = {}
        for name, key in (("VIX_History.csv", "vix"), ("VIX3M_History.csv", "vix3m")):
            url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}"
            raw = http_bytes(url, timeout=25, retries=2)
            series[key] = _parse_cboe_index(raw.decode("utf-8", "replace"))
            if not series[key]:
                raise ValueError(f"{name} 是空的")
        source = "CBOE"
    except Exception as first:
        print(f"情绪源 vix_term 改走 yfinance：{type(first).__name__}: {first}", flush=True)
        try:
            series = {"vix": _yf_closes("^VIX"), "vix3m": _yf_closes("^VIX3M")}
            if not series["vix"] or not series["vix3m"]:
                raise ValueError("yfinance 没有 VIX")
            source = "yfinance"
        except Exception as exc:
            return _stored(store, "vix_term", exc)
    vix_map = dict(series["vix"])
    vix3_map = dict(series["vix3m"])
    common = sorted(set(vix_map) & set(vix3_map))
    if not common:
        return _stored(store, "vix_term", ValueError("VIX 和 VIX3M 没有共同日期"))
    history = []
    for day in common[-40:]:
        vix = round(vix_map[day], 2)
        vix3 = round(vix3_map[day], 2)
        ratio = round(vix / vix3, 4) if vix3 else None
        history.append({"date": day, "vix": vix, "vix3m": vix3, "ratio": ratio})
    last = history[-1]
    value = {
        "date": last["date"],
        "vix": last["vix"],
        "vix3m": last["vix3m"],
        "ratio": last["ratio"],
        "backwardation": bool(last["ratio"] is not None and last["ratio"] > 1),
        "stale": False,
        "source": source,
        "history": history[-30:],
    }
    store["latest"]["vix_term"] = value
    store["history"]["vix_term"] = history[-40:]
    print(f"情绪源 vix_term {value['date']} {value['vix']}/{value['vix3m']} = {value['ratio']} {source}", flush=True)
    return value


def _parse_hy_csv(raw: bytes) -> list[dict]:
    points = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or not parts[0][:4].isdigit():
            continue
        if parts[1] in ("", "."):
            continue
        number = _num(parts[1], 2)
        if number is None:
            continue
        points.append({"date": parts[0][:10], "value": number})
    return points


def _hy_value(points: list[dict], source: str) -> dict:
    tail = points[-90:]
    latest = tail[-1]
    month_cut = (datetime.strptime(latest["date"], "%Y-%m-%d").date() - timedelta(days=30)).isoformat()
    prior = None
    for point in tail:
        if point["date"] <= month_cut:
            prior = point
    change = None
    if prior is not None:
        change = round(latest["value"] - prior["value"], 2)
    return {
        "date": latest["date"],
        "value": latest["value"],
        "series": "BAMLH0A0HYM2",
        "source": source,
        "stale": False,
        "change_1m": change,
        "change_1m_date": prior["date"] if prior else None,
        "history": tail[-60:],
        "reason": "",
    }


def fetch_hy(store: dict) -> dict | None:
    """ICE BofA 高收益债利差。FRED 图的 CSV 不需要密钥，但云上经常连上后不再回包。"""
    attempts = (
        ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2", "FRED", 12),
        ("https://govspending.org/api/export/fred/BAMLH0A0HYM2.csv", "FRED 转载", 20),
    )
    last_exc: Exception = RuntimeError("没有尝试")
    for url, source, timeout in attempts:
        try:
            raw = http_bytes(url, timeout=timeout, retries=1)
            points = _parse_hy_csv(raw)
            if len(points) < 5:
                raise ValueError(f"{source} 没有足够的点")
            value = _hy_value(points, source)
            store["latest"]["hy_oas"] = value
            print(f"情绪源 hy_oas {value['date']} {value['value']} {source}", flush=True)
            return value
        except Exception as exc:
            last_exc = exc
            print(f"情绪源 hy_oas {source} 失败：{type(exc).__name__}: {exc}", flush=True)
    stored = _stored(store, "hy_oas", last_exc)
    if stored is None:
        return {
            "value": None,
            "stale": False,
            "source": "FRED",
            "series": "BAMLH0A0HYM2",
            "reason": "FRED 的 CSV 连上后没有返回，备用地址也没有取到。没有可沿用的上次读数。",
        }
    stored["reason"] = f"这次没有取到新数，沿用 {stored.get('date')}。"
    return stored


def fetch_aaii(store: dict) -> dict | None:
    url = "https://www.aaii.com/files/surveys/sentiment.xls"
    try:
        raw = http_bytes(url, timeout=25, retries=2)
        import xlrd

        book = xlrd.open_workbook(file_contents=raw)
        sheet = book.sheet_by_name("SENTIMENT")
        rows = []
        for index in range(sheet.nrows):
            day = sheet.cell_value(index, 0)
            bull = sheet.cell_value(index, 1)
            neutral = sheet.cell_value(index, 2)
            bear = sheet.cell_value(index, 3)
            if not isinstance(day, float) or not isinstance(bull, float):
                continue
            if day < 30000 or day > 60000 or not 0 < bull < 1.5:
                continue
            if not isinstance(neutral, float) or not isinstance(bear, float):
                continue
            stamp = xlrd.xldate_as_datetime(day, book.datemode).date().isoformat()
            rows.append({
                "date": stamp,
                "bull": round(bull, 4),
                "neutral": round(neutral, 4),
                "bear": round(bear, 4),
                "spread": round(bull - bear, 4),
            })
        if not rows:
            raise ValueError("AAII 表里没有周度行")
        latest = rows[-1]
        value = {
            **latest,
            "stale": False,
            "source": "AAII",
            "history": rows[-52:],
        }
        store["latest"]["aaii"] = value
        store["history"]["aaii"] = rows[-104:]
        print(
            f"情绪源 aaii {value['date']} 多 {value['bull']:.1%} 空 {value['bear']:.1%} 差 {value['spread']:+.1%}",
            flush=True,
        )
        return value
    except Exception as exc:
        return _stored(store, "aaii", exc)


def _stale_bit(item: dict, text: str) -> str:
    if item.get("stale"):
        return text + " 过期"
    return text


def build_line(fields: dict) -> str:
    import glossary

    parts = []
    fg = fields.get("fear_greed") or {}
    if fg.get("score") is not None:
        parts.append(_stale_bit(fg, f"CNN 恐惧贪婪指数 {_fmt(fg['score'], 0)}（{glossary.fg_read(fg['score'])}）"))
    pc = fields.get("put_call") or {}
    if pc.get("equity") is not None:
        text = f"CBOE 股票看跌/看涨期权比 {pc['equity']:.2f}（{glossary.pc_read(pc['equity'])}"
        avg = (pc.get("avg_5d") or {}).get("equity")
        count = (pc.get("avg_5d") or {}).get("n") or 0
        if avg is not None and count:
            label = "5日" if count >= 5 else f"近{count}日"
            text += f"，{label} {avg:.2f}"
        text += "）"
        parts.append(_stale_bit(pc, text))
    term = fields.get("vix_term") or {}
    if term.get("ratio") is not None:
        parts.append(_stale_bit(
            term,
            f"VIX/VIX3M 期限结构 {term['ratio']:.2f}（{glossary.term_read(term['ratio'], term.get('backwardation'))}）",
        ))
    hy = fields.get("hy_oas") or {}
    if hy.get("value") is not None:
        parts.append(_stale_bit(hy, f"高收益债利差 {hy['value']:.2f}"))
    aaii = fields.get("aaii") or {}
    if aaii.get("spread") is not None:
        parts.append(_stale_bit(
            aaii,
            f"AAII 散户看多减看空 {aaii['spread'] * 100:+.1f}（{glossary.aaii_read(aaii['spread'])}）",
        ))
    return " · ".join(parts)


def build_detail(fields: dict) -> list[str]:
    lines = []
    fg = fields.get("fear_greed") or {}
    if fg.get("score") is not None:
        mark = "，过期" if fg.get("stale") else ""
        lines.append(
            f"恐惧与贪婪 {_fmt(fg['score'], 0)}（{fg.get('rating_cn') or fg.get('rating') or '—'}，"
            f"{fg.get('date') or '—'}{mark}）。前收 {_fmt(fg.get('previous_close'))}，"
            f"一周 {_fmt(fg.get('previous_1_week'))}，一月 {_fmt(fg.get('previous_1_month'))}，"
            f"一年 {_fmt(fg.get('previous_1_year'))}。"
        )
        bits = [
            f"{item['name']} {_fmt(item.get('score'), 0)}"
            for item in fg.get("components") or []
            if item.get("score") is not None
        ]
        if bits:
            lines.append("分项：" + "，".join(bits) + "。")
    pc = fields.get("put_call") or {}
    if pc.get("equity") is not None or pc.get("total") is not None:
        mark = "，过期" if pc.get("stale") else ""
        avg5 = pc.get("avg_5d") or {}
        avg10 = pc.get("avg_10d") or {}
        lines.append(
            f"看跌/看涨（CBOE {pc.get('date') or '—'}{mark}）：股票 {_fmt(pc.get('equity'), 2)}，"
            f"合计 {_fmt(pc.get('total'), 2)}。5日均股票 {_fmt(avg5.get('equity'), 2)} / 合计 {_fmt(avg5.get('total'), 2)}，"
            f"10日均股票 {_fmt(avg10.get('equity'), 2)} / 合计 {_fmt(avg10.get('total'), 2)}。"
        )
    term = fields.get("vix_term") or {}
    if term.get("ratio") is not None:
        mark = "，过期" if term.get("stale") else ""
        state = "倒挂" if term.get("backwardation") else "没有倒挂"
        lines.append(
            f"VIX 期限（{term.get('source') or '—'} {term.get('date') or '—'}{mark}）："
            f"{_fmt(term.get('vix'), 2)} / {_fmt(term.get('vix3m'), 2)} = {_fmt(term.get('ratio'), 2)}，{state}。"
        )
    hy = fields.get("hy_oas") or {}
    if hy.get("value") is not None:
        mark = "，过期" if hy.get("stale") else ""
        change = ""
        if hy.get("change_1m") is not None:
            change = f"较 {hy.get('change_1m_date')} {hy['change_1m']:+.2f}。"
        lines.append(f"高收益债利差 {_fmt(hy['value'], 2)}（FRED {hy.get('series')}，{hy.get('date')}{mark}）。{change}")
    aaii = fields.get("aaii") or {}
    if aaii.get("spread") is not None:
        mark = "，过期" if aaii.get("stale") else ""
        lines.append(
            f"AAII（{aaii.get('date')}{mark}）：看多 {aaii['bull'] * 100:.1f}%，中性 {aaii['neutral'] * 100:.1f}%，"
            f"看空 {aaii['bear'] * 100:.1f}%，多空差 {aaii['spread'] * 100:+.1f} 个百分点。"
        )
    return lines


def approximation_notes(fields: dict) -> list[str]:
    specs = (
        ("fear_greed", "CNN 恐惧与贪婪", "score"),
        ("put_call", "CBOE 看跌/看涨", "equity"),
        ("vix_term", "VIX 期限", "ratio"),
        ("hy_oas", "高收益债利差", "value"),
        ("aaii", "AAII 调查", "spread"),
    )
    notes = []
    for key, label, field in specs:
        item = fields.get(key)
        if not item or item.get(field) is None:
            notes.append(f"{label}这次没有取到。")
        elif item.get("stale"):
            notes.append(f"{label}这次没有取到，沿用 {item.get('date')} 的数。")
    notes.append("CNN 恐惧与贪婪和 CBOE 日统计来自非官方接口。情绪读数不计入环境分。")
    return notes


def patch_approximations(meta_path: Path, fields: dict):
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    kept = [note for note in meta.get("approximations") or [] if not any(mark in note for mark in NOTE_MARKS)]
    meta["approximations"] = kept + approximation_notes(fields)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(root: Path) -> dict:
    """拉取五个源。返回要合并进 sentiment.json 的字段，并写 data/sentiment_store.json。"""
    store = load_store(root)
    fields = {
        "fear_greed": fetch_fear_greed(store),
        "put_call": fetch_put_call(store),
        "vix_term": fetch_vix_term(store),
        "hy_oas": fetch_hy(store),
        "aaii": fetch_aaii(store),
    }
    fields["line"] = build_line(fields)
    fields["detail"] = build_detail(fields)
    fields["confirm"] = "情绪只作对照，不计入环境分，门槛未改。CNN 恐惧与贪婪和 CBOE 日统计来自非官方接口。"
    save_store(root, store)
    print(f"情绪：{fields['line'] or '没有读数'}", flush=True)
    return fields
