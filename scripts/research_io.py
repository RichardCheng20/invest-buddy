"""AI 研究的纯逻辑：校验、轮换、分歧、费用、日报句子。不导入 TradingAgents。"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import yaml

REPO = Path(__file__).resolve().parents[1]
SH = ZoneInfo("Asia/Shanghai")
ET = ZoneInfo("America/New_York")

RATINGS = ("买入", "增持", "持有", "减持", "卖出")
BULLISH = {"买入", "增持"}
BEARISH = {"卖出", "减持"}
EN_TO_CN = {
    "Buy": "买入",
    "Overweight": "增持",
    "Hold": "持有",
    "Underweight": "减持",
    "Sell": "卖出",
}
SKIP_RESEARCH_FILES = {"state.json", "usage.json", "dispatch.json"}
_FENCE = re.compile(r"```json\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_TICKER = re.compile(r"^[A-Z][A-Z0-9-]{0,14}$")
_AI_BLOCK = re.compile(r"\n## AI 研究\n.*?(?=\n## |\Z)", re.DOTALL)
MARKER = "## 近似与缺失"
PRICE_NOTE = "按 config/research.yaml 的标价估算。表里没有的模型记 0。没有调用时这里是 0。"
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ResearchRejected(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def load_limits(path: Path | None = None) -> dict:
    raw = yaml.safe_load((path or REPO / "config" / "research.yaml").read_text(encoding="utf-8"))
    schedule = dict(raw.get("schedule") or {})
    missing = [key for key in ("timezone", "weekdays", "time") if not schedule.get(key)]
    if missing:
        raise ResearchRejected([f"config/research.yaml 的 schedule 缺少 {', '.join(missing)}"])
    try:
        ZoneInfo(str(schedule["timezone"]))
    except Exception as exc:
        raise ResearchRejected([f"schedule.timezone 无法识别：{schedule['timezone']}"]) from exc
    weekdays = [int(day) for day in schedule["weekdays"]]
    if not weekdays or any(day < 1 or day > 7 for day in weekdays):
        raise ResearchRejected(["schedule.weekdays 必须是 1 到 7（周一到周日）"])
    if not _HHMM.fullmatch(str(schedule["time"]).strip()):
        raise ResearchRejected(["schedule.time 必须是 HH:MM"])
    schedule["weekdays"] = weekdays
    schedule["tolerance_minutes"] = float(schedule.get("tolerance_minutes") or 25)
    raw["schedule"] = schedule
    llm = dict(raw.get("llm") or {})
    for key in ("base_url", "model_fast", "model_deep"):
        if not str(llm.get(key) or "").strip():
            raise ResearchRejected([f"config/research.yaml 的 llm 缺少 {key}"])
    raw["llm"] = {
        "base_url": str(llm["base_url"]).strip(),
        "model_fast": str(llm["model_fast"]).strip(),
        "model_deep": str(llm["model_deep"]).strip(),
    }
    return raw


def load_local_env(path: Path | None = None) -> None:
    """读取仓库根目录的 .env。已经存在的环境变量优先，不覆盖。"""
    file = path or (REPO / ".env")
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _pick(source: dict, name: str) -> str:
    return str(source.get(name) or "").strip()


def llm_settings(limits: dict | None = None, env: dict | None = None) -> dict:
    """OpenAI 兼容接口的四个设置。环境变量非空时盖过 yaml。

    正式钥匙是 LLM_API_KEY。DEEPSEEK_API_KEY 只在前者为空时当作同一把钥匙。
    主机名是 api.deepseek.com 时走 DeepSeek 客户端，
    主机名是 api.anthropic.com（或 LLM_PROVIDER=anthropic）时走 Anthropic 原生接口。
    """
    limits = limits or load_limits()
    source = os.environ if env is None else env
    llm = limits["llm"]
    base_url = _pick(source, "LLM_BASE_URL") or llm["base_url"]
    model_fast = _pick(source, "LLM_MODEL_FAST") or llm["model_fast"]
    model_deep = _pick(source, "LLM_MODEL_DEEP") or llm["model_deep"]
    api_key = _pick(source, "LLM_API_KEY")
    host = (urlparse(base_url).hostname or "").lower()
    requested = _pick(source, "LLM_PROVIDER").lower()
    if host == "api.anthropic.com" or requested == "anthropic":
        provider = "anthropic"
        if not api_key:
            api_key = _pick(source, "ANTHROPIC_API_KEY")
    elif host == "api.deepseek.com":
        provider = "deepseek"
    else:
        provider = "openai_compatible"
    if not api_key:
        api_key = _pick(source, "DEEPSEEK_API_KEY")
    return {
        "provider": provider,
        "base_url": base_url,
        "model_fast": model_fast,
        "model_deep": model_deep,
        "api_key": api_key,
    }


def key_present(settings: dict) -> bool:
    if settings.get("api_key"):
        return True
    host = (urlparse(str(settings.get("base_url") or "")).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def apply_provider_env(settings: dict) -> None:
    """把 LLM_API_KEY 交给 TradingAgents 对应的客户端。不要求用户另设供应商钥匙。"""
    key = str(settings.get("api_key") or "")
    provider = settings.get("provider")
    if provider == "deepseek":
        if key:
            os.environ["DEEPSEEK_API_KEY"] = key
        return
    if provider == "anthropic":
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
        return
    if key:
        os.environ["OPENAI_COMPATIBLE_API_KEY"] = key
    else:
        os.environ.setdefault("OPENAI_COMPATIBLE_API_KEY", "ollama")


def provider_label(settings: dict) -> str:
    return {"deepseek": "DeepSeek", "anthropic": "Anthropic"}.get(str(settings.get("provider") or ""), "OpenAI 兼容")


def norm_ticker(value: str) -> str:
    return str(value or "").strip().upper().replace(".", "-")


def load_schema() -> dict:
    return json.loads((REPO / "schemas" / "research.schema.json").read_text(encoding="utf-8"))


def extract_json_block(body: str):
    """返回解析后的对象或数组。没有 json 围栏时返回 None。围栏坏了就拒绝。"""
    matches = list(_FENCE.finditer(body or ""))
    if not matches:
        return None
    if len(matches) > 1:
        raise ResearchRejected(["评论里有多段 ```json，只保留一段"])
    raw = matches[0].group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResearchRejected([f"JSON 无法解析：{exc.msg}（第 {exc.lineno} 行）"]) from exc


def _zh_error(err, prefix: str) -> str:
    path = prefix + ".".join(str(part) for part in err.absolute_path)
    loc = path or "根"
    if err.validator == "required":
        parts = err.message.split("'")
        missing = parts[1] if len(parts) >= 2 else err.message
        return f"缺少字段 {prefix}{missing}"
    if err.validator == "enum":
        return f"{loc} 必须是 {'、'.join(RATINGS)} 之一"
    if err.validator == "additionalProperties":
        return f"{loc} 有未定义的字段：{err.message}"
    if err.validator == "minLength":
        return f"{loc} 不能为空"
    if err.validator == "pattern" and loc.endswith("date"):
        return f"{loc} 必须是 YYYY-MM-DD"
    if err.validator == "pattern" and loc.endswith("url"):
        return f"{loc} 必须是 http 或 https 链接"
    if err.validator == "pattern" and loc.endswith("ticker"):
        return f"{loc} 不是股票代码"
    if err.validator == "type":
        return f"{loc} 类型不对"
    if err.validator == "maxItems":
        return f"{loc} 条数超过 {err.validator_value}"
    return f"{loc}：{err.message}"


def validate_records(payload, schema: dict | None = None) -> list[dict]:
    import jsonschema

    schema = schema or load_schema()
    item_schema = schema["$defs"]["research"]
    validator = jsonschema.Draft202012Validator(item_schema)
    if isinstance(payload, dict):
        items = [payload]
        many = False
    elif isinstance(payload, list):
        if not payload:
            raise ResearchRejected(["数组是空的"])
        items = payload
        many = True
    else:
        raise ResearchRejected(["JSON 必须是一个对象，或对象组成的数组"])

    normalized = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("ticker"), str):
            item = dict(item)
            item["ticker"] = norm_ticker(item["ticker"])
        normalized.append(item)

    errors = []
    for index, item in enumerate(normalized):
        prefix = f"[{index}]." if many else ""
        for err in sorted(validator.iter_errors(item), key=lambda e: list(e.absolute_path)):
            errors.append(_zh_error(err, prefix))
        if isinstance(item, dict) and isinstance(item.get("date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["date"]):
            try:
                datetime.strptime(item["date"], "%Y-%m-%d")
            except ValueError:
                errors.append(f"{prefix}date 不是有效日期")
    if errors:
        raise ResearchRejected(errors)

    seen = set()
    out = []
    for item in normalized:
        ticker = item["ticker"]
        if ticker in seen:
            raise ResearchRejected([f"同一条评论里 {ticker} 出现了两次"])
        seen.add(ticker)
        if not _TICKER.fullmatch(ticker):
            raise ResearchRejected([f"{ticker} 不是股票代码"])
        out.append(item)
    return out


def diverges(rule_action: str | None, rating: str | None) -> bool:
    if rule_action in ("sell", "trim") and rating in BULLISH:
        return True
    if rule_action == "buy" and rating in BEARISH:
        return True
    return False


def is_stale(research_date: str | None, bar_date: str | None, stale_days: int) -> bool:
    if not research_date or not bar_date:
        return False
    try:
        span = datetime.strptime(bar_date, "%Y-%m-%d") - datetime.strptime(research_date, "%Y-%m-%d")
    except ValueError:
        return False
    return span.days > stale_days


def rule_action_for(ticker: str, portfolio: dict | None, scanner: dict | None) -> tuple[str | None, str | None]:
    ticker = norm_ticker(ticker)
    for pos in (portfolio or {}).get("positions") or []:
        if pos.get("instrument") == "option":
            continue
        if norm_ticker(pos.get("ticker")) != ticker:
            continue
        call = pos.get("call") or {}
        if call.get("action"):
            return call.get("action"), call.get("action_label")
    for row in (scanner or {}).get("rows") or []:
        if norm_ticker(row.get("ticker")) != ticker:
            continue
        call = row.get("call") or {}
        return call.get("action"), call.get("action_label")
    return None, None


def stock_holdings(portfolio_yaml: dict) -> list[dict]:
    seen = []
    have = set()
    for pos in portfolio_yaml.get("positions") or []:
        if pos.get("instrument") == "option":
            continue
        ticker = norm_ticker(pos.get("ticker"))
        if not ticker or ticker in have:
            continue
        have.add(ticker)
        seen.append({
            "ticker": ticker,
            "quantity": pos.get("shares"),
            "average_price": pos.get("avg_cost"),
        })
    return seen


def portfolio_payload(portfolio_yaml: dict) -> dict:
    return {
        "cash": portfolio_yaml.get("cash"),
        "currency": portfolio_yaml.get("base_currency") or "USD",
        "positions": [
            {
                "ticker": item["ticker"],
                "quantity": item["quantity"],
                "average_price": item["average_price"],
            }
            for item in stock_holdings(portfolio_yaml)
        ],
    }


def rotate(holdings: list[dict], cursor: int, count: int) -> tuple[list[str], int]:
    if not holdings or count <= 0:
        return [], cursor
    start = cursor % len(holdings)
    take = min(count, len(holdings))
    picked = [holdings[(start + i) % len(holdings)]["ticker"] for i in range(take)]
    return picked, (start + take) % len(holdings)


def new_signals(scanner: dict, bar_date: str, exclude: set[str], last_by_ticker: dict, cooldown_days: int, limit: int) -> list[str]:
    rows = []
    for row in scanner.get("rows") or []:
        call = row.get("call") or {}
        if call.get("action") != "buy":
            continue
        if row.get("rejected_by"):
            continue
        if row.get("date") != bar_date:
            continue
        ticker = norm_ticker(row.get("ticker"))
        if not ticker or ticker in exclude:
            continue
        last = (last_by_ticker or {}).get(ticker)
        if last:
            try:
                age = (datetime.strptime(bar_date, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
            except ValueError:
                age = cooldown_days
            if age < cooldown_days:
                continue
        rows.append(row)
    rows.sort(key=lambda row: (-(row.get("rs_rank") if isinstance(row.get("rs_rank"), (int, float)) else -1), norm_ticker(row.get("ticker"))))
    return [norm_ticker(row["ticker"]) for row in rows[: max(0, limit)]]


def select_batch(holdings: list[dict], scanner: dict, state: dict, bar_date: str, limits: dict) -> tuple[list[str], int, list[str]]:
    held, new_cursor = rotate(holdings, int(state.get("cursor") or 0), int(limits["holdings_per_run"]))
    signals = new_signals(
        scanner,
        bar_date,
        set(held),
        state.get("last_by_ticker") or {},
        int(limits["signal_cooldown_days"]),
        int(limits["max_new_signals"]),
    )
    cap = int(limits["max_tickers_per_run"])
    return (held + signals)[:cap], new_cursor, held


def expected_closed_session(now_et: datetime) -> str:
    day = now_et.date()
    weekday = day.weekday()
    if weekday >= 5:
        return (day - timedelta(days=weekday - 4)).isoformat()
    closed = (now_et.hour, now_et.minute) >= (16, 15)
    if closed:
        return day.isoformat()
    return (day - timedelta(days=1 if weekday else 3)).isoformat()


def _parse_moment(created_at: str) -> datetime:
    text = created_at.strip().replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def schedule_due(created_at: str | None, limits: dict | None = None) -> bool:
    """本地时钟是否对准 config/research.yaml 的 schedule。夏令时交给 zoneinfo。"""
    if not created_at:
        return False
    spec = (limits or load_limits())["schedule"]
    local = _parse_moment(created_at).astimezone(ZoneInfo(spec["timezone"]))
    if local.isoweekday() not in [int(day) for day in spec["weekdays"]]:
        return False
    hour, minute = [int(part) for part in str(spec["time"]).split(":", 1)]
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    tolerance = float(spec.get("tolerance_minutes") or 25)
    return abs((local - target).total_seconds()) <= tolerance * 60


def should_run(*, event: str, has_key: bool, created_at: str | None, bar_date: str | None, last_bar_date: str | None, expected_bar: str | None, ticker: str | None, limits: dict | None = None) -> tuple[bool, str]:
    if not has_key:
        return False, "没有 LLM_API_KEY，跳过"
    if ticker:
        return True, ""
    if event in ("schedule", "workflow_run"):
        if not schedule_due(created_at, limits):
            return False, "不在 config/research.yaml 配置的研究时间，跳过"
        if expected_bar and bar_date and bar_date < expected_bar:
            return False, "行情还不是新收盘"
        if last_bar_date and bar_date and last_bar_date == bar_date:
            return False, "这一收盘日已经研究过，跳过"
    return True, ""


def estimate_usd(by_model: dict, prices: dict) -> float:
    total = 0.0
    for model, usage in (by_model or {}).items():
        price = prices.get(model) or {}
        incoming = float((usage or {}).get("input_tokens") or 0)
        outgoing = float((usage or {}).get("output_tokens") or 0)
        total += incoming / 1_000_000 * float(price.get("input") or 0)
        total += outgoing / 1_000_000 * float(price.get("output") or 0)
    return round(total, 4)


def month_estimate(usage: dict, month: str) -> float:
    total = 0.0
    for run in usage.get("runs") or []:
        if run.get("month") == month:
            total += float(run.get("estimated_usd") or 0)
    return round(total, 4)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_records(root: Path) -> list[dict]:
    folder = root / "data" / "research"
    if not folder.exists():
        return []
    records = []
    for path in sorted(folder.glob("*.json")):
        if path.name in SKIP_RESEARCH_FILES:
            continue
        if not _TICKER.fullmatch(path.stem):
            continue
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def ai_section_lines(records: list[dict], portfolio: dict | None, rows: list | None) -> list[str]:
    scanner = {"rows": rows or []}
    lines = ["## AI 研究", "", "模型写的对照。股数和动作以规则引擎为准。", ""]
    ordered = sorted(records, key=lambda rec: rec.get("ticker") or "")
    if not ordered:
        lines.append("今天没有 AI 研究。")
        return lines
    for rec in ordered:
        action, _label = rule_action_for(rec.get("ticker"), portfolio, scanner)
        flag = " ⚠️ 分歧" if diverges(action, rec.get("rating")) else ""
        lines.append(f"- **{rec.get('ticker')}** {rec.get('date')} {rec.get('rating')}{flag}：{rec.get('conclusion')}")
    return lines


def splice_report(text: str, section: str) -> str:
    stripped = _AI_BLOCK.sub("\n", text)
    block = section.rstrip() + "\n"
    if MARKER in stripped:
        return stripped.replace(MARKER, block + "\n" + MARKER, 1)
    return stripped.rstrip() + "\n\n" + block


def splice_existing_reports(root: Path, records: list[dict]) -> None:
    meta = _read_json(root / "site" / "data" / "meta.json", {})
    portfolio = _read_json(root / "site" / "data" / "portfolio.json", {})
    scanner = _read_json(root / "site" / "data" / "scanner.json", {})
    day = meta.get("generated_date_cn")
    if not day or not records:
        return
    section = "\n".join(ai_section_lines(records, portfolio, scanner.get("rows") or []))
    for name in (f"{day}-am.md", f"{day}-pm.md"):
        for folder in (root / "reports", root / "site" / "reports"):
            path = folder / name
            if path.exists():
                path.write_text(splice_report(path.read_text(encoding="utf-8"), section), encoding="utf-8")


def render_html(rec: dict) -> str:
    def p(text):
        return f"<p>{html.escape(str(text or ''))}</p>"

    def items(points):
        if not points:
            return "<p>没有列出。</p>"
        body = "".join(f"<li>{html.escape(str(point))}</li>" for point in points)
        return f"<ul>{body}</ul>"

    fundamentals = rec.get("fundamentals") or {}
    metrics = "".join(
        f"<div><span>{html.escape(str(key))}</span><b>{html.escape('—' if value is None else str(value))}</b></div>"
        for key, value in fundamentals.items()
    ) or "<p>没有列出。</p>"
    sources = rec.get("sources") or []
    links = "".join(
        f'<li><a href="{html.escape(str(item.get("url") or ""), quote=True)}">{html.escape(str(item.get("title") or ""))}</a></li>'
        for item in sources
    ) or "<li>没有链接。</li>"
    banner = "<p class=\"banner\">⚠️ 分歧</p>" if rec.get("diverge") else ""
    license_line = ""
    if "TradingAgents" in str(rec.get("model_note") or ""):
        license_line = "<p class=\"muted\">分析由 TradingAgents 生成，Apache-2.0，版权 Tauric Research。源码不在本仓库。</p>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(rec.get("ticker") or "")} AI 研究</title>
<script>
(function () {{
  var mode = "system";
  try {{
    var stored = localStorage.getItem("invest-buddy-theme");
    if (stored === "light" || stored === "dark" || stored === "system") mode = stored;
  }} catch (e) {{}}
  var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = mode === "system" ? (dark ? "dark" : "light") : mode;
}})();
</script>
<style>
:root {{
  color-scheme: dark;
  --bg: #1c1f26; --text: #f2f4f7; --muted: #b7bcc6; --card: #2a2e36;
  --banner-bg: #3a2e1c; --banner-ink: #ffe3b8; --link: #8eb6ff;
}}
:root[data-theme="light"] {{
  color-scheme: light;
  --bg: #f4f6f8; --text: #16181d; --muted: #4a5160; --card: #ffffff;
  --banner-bg: #fff4e0; --banner-ink: #6b3e08; --link: #0b57d0;
}}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 "Noto Sans SC", sans-serif; }}
main {{ max-width: 42rem; margin: 0 auto; padding: 24px 16px 48px; }}
h1 {{ font-size: 1.4rem; }}
.muted {{ color: var(--muted); }}
.banner {{ background: var(--banner-bg); color: var(--banner-ink); padding: 10px 12px; border-radius: 8px; }}
a {{ color: var(--link); }}
.kv {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
.kv div {{ background: var(--card); border-radius: 8px; padding: 8px 10px; }}
.kv span {{ display: block; color: var(--muted); font-size: 0.75rem; }}
</style>
</head>
<body>
<main>
<h1>{html.escape(rec.get("ticker") or "")} {html.escape(rec.get("rating") or "")}</h1>
<p class="muted">{html.escape(rec.get("date") or "")} · {html.escape(rec.get("model_note") or "")}</p>
{banner}
{p(rec.get("conclusion"))}
<h2>多方</h2>
{items(rec.get("bull_points"))}
<h2>空方</h2>
{items(rec.get("bear_points"))}
<h2>新闻与情绪</h2>
{p(rec.get("news_sentiment"))}
<h2>基本面</h2>
<div class="kv">{metrics}</div>
<h2>技术</h2>
{p(rec.get("technical_view"))}
<h2>风险</h2>
{p(rec.get("risk_review"))}
<h2>来源</h2>
<ul>{links}</ul>
<p class="muted">股数和动作以规则引擎为准。</p>
{license_line}
</main>
</body>
</html>
"""


def rebuild_site(root: Path, limits: dict | None = None, now: datetime | None = None) -> dict:
    limits = limits or load_limits()
    now = now or datetime.now(SH)
    month = now.astimezone(SH).strftime("%Y-%m")
    usage = _read_json(root / "data" / "research" / "usage.json", {"runs": []})
    records = load_records(root)
    portfolio = _read_json(root / "site" / "data" / "portfolio.json", {})
    scanner = _read_json(root / "site" / "data" / "scanner.json", {})
    tickers = {}
    for rec in records:
        action, _label = rule_action_for(rec.get("ticker"), portfolio, scanner)
        tickers[rec["ticker"]] = {
            "date": rec.get("date"),
            "rating": rec.get("rating"),
            "conclusion": rec.get("conclusion"),
            "diverge": diverges(action, rec.get("rating")),
        }
    index = {
        "tickers": tickers,
        "month": month,
        "estimated_usd": month_estimate(usage, month),
        "stale_days": int(limits.get("stale_days") or 3),
        "backend": "openai-compatible",
        "price_note": PRICE_NOTE,
    }
    _write_json(root / "site" / "data" / "research.json", index)
    return index


def apply_records(root: Path, records: list[dict], now: datetime | None = None) -> None:
    now = now or datetime.now(SH)
    portfolio = _read_json(root / "site" / "data" / "portfolio.json", {})
    scanner = _read_json(root / "site" / "data" / "scanner.json", {})
    limits = load_limits()
    for rec in records:
        action, label = rule_action_for(rec["ticker"], portfolio, scanner)
        stored = dict(rec)
        stored["ingested_at"] = now.astimezone(SH).isoformat(timespec="seconds")
        stored["rule_action"] = action
        stored["rule_action_label"] = label
        stored["diverge"] = diverges(action, rec.get("rating"))
        path = root / "data" / "research" / f"{rec['ticker']}.json"
        _write_json(path, stored)
        history = root / "data" / "research" / "history" / f"{rec['ticker']}.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stored, ensure_ascii=False) + "\n")
        page = render_html(stored)
        for target in (
            root / "data" / "research" / f"{rec['ticker']}.html",
            root / "site" / "research" / f"{rec['ticker']}.html",
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")
        _write_json(root / "site" / "data" / "research" / f"{rec['ticker']}.json", stored)
    all_records = load_records(root)
    rebuild_site(root, limits, now)
    splice_existing_reports(root, all_records)


def append_usage(root: Path, run: dict, now: datetime | None = None) -> None:
    now = now or datetime.now(SH)
    path = root / "data" / "research" / "usage.json"
    usage = _read_json(path, {"runs": []})
    usage.setdefault("runs", []).append(run)
    _write_json(path, usage)
    rebuild_site(root, load_limits(), now)


def ingest_comment(body: str, root: Path, now: datetime | None = None) -> dict:
    try:
        payload = extract_json_block(body)
        if payload is None:
            return {"status": "ignore", "tickers": [], "reply": ""}
        records = validate_records(payload)
    except ResearchRejected as exc:
        reply = "校验没有通过：\n" + "\n".join(f"- {item}" for item in exc.errors)
        return {"status": "error", "tickers": [], "reply": reply}
    apply_records(root, records, now)
    bits = [f"{rec['ticker']}（{rec['date']}，{rec['rating']}）" for rec in records]
    return {"status": "ok", "tickers": [rec["ticker"] for rec in records], "reply": "✅ 已写入 " + "、".join(bits) + "。"}


def _fail(label: str):
    raise SystemExit(label)


def self_check() -> None:
    import tempfile

    schema = load_schema()
    sample = {
        "ticker": "aapl",
        "date": "2026-10-09",
        "rating": "持有",
        "conclusion": "规则仍是对照清单；这只是模型的一句话结论。",
        "bull_points": ["数据中心资本开支仍在"],
        "bear_points": ["毛利率还没稳住"],
        "news_sentiment": "近一周新闻中性，没有单条改变仓位的消息。",
        "fundamentals": {"市盈率": "—", "营收同比": "待填"},
        "technical_view": "收盘仍在 50 日均线下方。",
        "risk_review": "不改变股数。规则引擎给出的动作优先。",
        "sources": [{"title": "公司投资者关系", "url": "https://www.apple.com/"}],
        "model_note": "DeepSeek 示例，不是下单指令",
    }
    body = "先看这只。\n\n```json\n" + json.dumps(sample, ensure_ascii=False, indent=2) + "\n```\n"
    if extract_json_block("没有围栏") is not None:
        _fail("无围栏应忽略")
    try:
        extract_json_block("```json\n{}\n```\n```json\n{}\n```")
        _fail("多段围栏应拒绝")
    except ResearchRejected:
        pass

    records = validate_records(json.loads(_FENCE.search(body).group(1)), schema)
    if records[0]["ticker"] != "AAPL":
        _fail("代码应规范成 AAPL")
    try:
        validate_records({**sample, "ticker": "AAPL", "rating": "观望"}, schema)
        _fail("非法评级应拒绝")
    except ResearchRejected:
        pass
    try:
        validate_records({**sample, "ticker": "AAPL", "date": "2026-02-31"}, schema)
        _fail("非法日期应拒绝")
    except ResearchRejected:
        pass
    try:
        validate_records({**sample, "ticker": "AAPL", "extra": 1}, schema)
        _fail("多余字段应拒绝")
    except ResearchRejected:
        pass
    try:
        bad = {**sample, "ticker": "AAPL", "sources": [{"title": "x", "url": "javascript:alert(1)"}]}
        validate_records(bad, schema)
        _fail("非法链接应拒绝")
    except ResearchRejected:
        pass

    if not diverges("sell", "买入") or not diverges("trim", "增持") or not diverges("buy", "卖出"):
        _fail("分歧判定漏了")
    if diverges("sell", "持有") or diverges("hold", "买入") or diverges("watch", "卖出") or diverges("buy", "增持"):
        _fail("不该标成分歧")
    if not is_stale("2026-10-05", "2026-10-09", 3) or is_stale("2026-10-06", "2026-10-09", 3):
        _fail("过期天数不对")

    book = yaml.safe_load((REPO / "data" / "portfolio.yaml").read_text(encoding="utf-8"))
    held = [item["ticker"] for item in stock_holdings(book)]
    if held != ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "UNH", "XOM"]:
        _fail(f"持仓轮换名单不对：{held}")
    if any(pos.get("instrument") == "option" for pos in portfolio_payload(book)["positions"]):
        _fail("期权不该进研究组合")
    first, cursor = rotate(stock_holdings(book), 0, 3)
    second, cursor2 = rotate(stock_holdings(book), cursor, 3)
    if first != ["AAPL", "MSFT", "NVDA"] or cursor != 3 or second != ["AMZN", "GOOGL", "JPM"] or cursor2 != 6:
        _fail("轮换切片不对")
    scanner = {
        "rows": [
            {"ticker": "AAA", "date": "2026-10-09", "rs_rank": 10, "rejected_by": [], "call": {"action": "buy"}},
            {"ticker": "BBB", "date": "2026-10-09", "rs_rank": 90, "rejected_by": [], "call": {"action": "buy"}},
            {"ticker": "CCC", "date": "2026-10-09", "rs_rank": 50, "rejected_by": ["限额"], "call": {"action": "buy"}},
            {"ticker": "DDD", "date": "2026-10-08", "rs_rank": 99, "rejected_by": [], "call": {"action": "buy"}},
            {"ticker": "EEE", "date": "2026-10-09", "rs_rank": 70, "rejected_by": [], "call": {"action": "buy"}},
            {"ticker": "FFF", "date": "2026-10-09", "rs_rank": 40, "rejected_by": [], "call": {"action": "watch"}},
            {"ticker": "AAPL", "date": "2026-10-09", "rs_rank": 100, "rejected_by": [], "call": {"action": "buy"}},
        ]
    }
    limits = load_limits()
    batch, new_cursor, held_batch = select_batch(stock_holdings(book), scanner, {"cursor": 0, "last_by_ticker": {"EEE": "2026-10-08"}}, "2026-10-09", limits)
    if held_batch != ["AAPL", "MSFT", "NVDA"] or batch != ["AAPL", "MSFT", "NVDA", "BBB", "AAA"] or new_cursor != 3:
        _fail(f"选股不对：{batch} cursor {new_cursor}")

    prices = limits["prices_per_million"]
    if estimate_usd({"deepseek-flash": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}}, prices) != 0.75:
        _fail("flash 估价不对")
    if estimate_usd({"deepseek-v4-pro": {"input_tokens": 1_000_000, "output_tokens": 0}}, prices) != 0.66:
        _fail("pro 估价不对")

    friday_after = datetime(2026, 10, 9, 16, 15, tzinfo=ET)
    friday_before = datetime(2026, 10, 9, 16, 14, tzinfo=ET)
    saturday = datetime(2026, 10, 10, 9, 0, tzinfo=ET)
    if expected_closed_session(friday_after) != "2026-10-09":
        _fail("收盘后应是当天")
    if expected_closed_session(friday_before) != "2026-10-08":
        _fail("收盘前应是前一交易日")
    if expected_closed_session(saturday) != "2026-10-09":
        _fail("周末应回到周五")
    if limits["schedule"]["timezone"] != "Asia/Shanghai" or limits["schedule"]["time"] != "07:30":
        _fail("默认研究时刻不对")
    if limits["llm"]["base_url"] != "https://api.deepseek.com":
        _fail("默认接口不对")
    if limits["llm"]["model_fast"] != "deepseek-flash" or limits["llm"]["model_deep"] != "deepseek-v4-pro":
        _fail("默认模型不对")
    summer = datetime(2026, 7, 15, 7, 30, tzinfo=SH)
    winter = datetime(2026, 1, 15, 7, 30, tzinfo=SH)
    if summer.utcoffset() != timedelta(hours=8) or winter.utcoffset() != timedelta(hours=8):
        _fail("中国应无夏令时")
    ny = ZoneInfo("America/New_York")
    if datetime(2026, 7, 15, 7, 30, tzinfo=ny).utcoffset() == datetime(2026, 1, 15, 7, 30, tzinfo=ny).utcoffset():
        _fail("纽约夏令时和冬令时的偏移应不同")

    def stamp(zone_name: str, year: int, month: int, day: int, hour: int, minute: int) -> str:
        local = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone_name))
        return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not schedule_due(stamp("Asia/Shanghai", 2026, 10, 10, 7, 30), limits):
        _fail("北京时间周六 07:30 应放行")
    if not schedule_due(stamp("Asia/Shanghai", 2026, 10, 10, 7, 50), limits):
        _fail("容差内应放行")
    if schedule_due(stamp("Asia/Shanghai", 2026, 10, 10, 8, 10), limits):
        _fail("超出容差不应放行")
    if schedule_due(stamp("Asia/Shanghai", 2026, 10, 11, 7, 30), limits):
        _fail("周日不应跑")

    def first_monday(year: int, month: int) -> int:
        for day in range(1, 8):
            if datetime(year, month, day).isoweekday() == 1:
                return day
        raise SystemExit("找不到周一")

    ny_limits = {
        **limits,
        "schedule": {
            "timezone": "America/New_York",
            "weekdays": [1, 2, 3, 4, 5],
            "time": "07:30",
            "tolerance_minutes": 25,
        },
    }
    summer_day = first_monday(2026, 7)
    winter_day = first_monday(2026, 1)
    if not schedule_due(stamp("America/New_York", 2026, 7, summer_day, 7, 30), ny_limits):
        _fail("纽约夏令时本地 07:30 应放行")
    if not schedule_due(stamp("America/New_York", 2026, 1, winter_day, 7, 30), ny_limits):
        _fail("纽约冬令时本地 07:30 应放行")
    summer_utc = datetime(2026, 7, summer_day, 7, 30, tzinfo=ny).astimezone(timezone.utc)
    wrong = f"2026-01-{winter_day:02d}T{summer_utc.hour:02d}:{summer_utc.minute:02d}:00Z"
    if schedule_due(wrong, ny_limits):
        _fail("把夏令时的 UTC 钟点用在冬天不应放行")

    defaults = llm_settings(limits, {})
    if defaults["provider"] != "deepseek" or defaults["model_fast"] != "deepseek-flash" or key_present(defaults):
        _fail("缺省应是 DeepSeek 且没有钥匙")
    overridden = llm_settings(limits, {
        "LLM_BASE_URL": "https://api.openai.com/v1",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL_FAST": "fast-x",
        "LLM_MODEL_DEEP": "deep-x",
    })
    if overridden["provider"] != "openai_compatible" or overridden["model_fast"] != "fast-x" or overridden["api_key"] != "test-key":
        _fail("环境变量应盖过配置")
    if llm_settings(limits, {"LLM_BASE_URL": "https://api.anthropic.com", "LLM_API_KEY": "test-key"})["provider"] != "anthropic":
        _fail("Anthropic 主机应走原生接口")
    alias = llm_settings(limits, {"DEEPSEEK_API_KEY": "legacy-alias"})
    if alias["api_key"] != "legacy-alias" or alias["provider"] != "deepseek":
        _fail("DEEPSEEK_API_KEY 应作为 LLM_API_KEY 的别名")
    if llm_settings(limits, {"LLM_API_KEY": "canonical", "DEEPSEEK_API_KEY": "legacy-alias"})["api_key"] != "canonical":
        _fail("LLM_API_KEY 应优先于旧别名")
    if llm_settings(limits, {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "claude-key"})["provider"] != "anthropic":
        _fail("LLM_PROVIDER=anthropic 应走原生接口")
    local_llm = llm_settings(limits, {"LLM_BASE_URL": "http://127.0.0.1:11434/v1"})
    if not key_present(local_llm):
        _fail("本机 Ollama 可以不填钥匙")
    saved_keys = {name: os.environ.get(name) for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_COMPATIBLE_API_KEY")}
    try:
        apply_provider_env(defaults | {"api_key": "from-llm"})
        if os.environ.get("DEEPSEEK_API_KEY") != "from-llm":
            _fail("DeepSeek 客户端应拿到 LLM_API_KEY")
    finally:
        for name, value in saved_keys.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    on_time = stamp("Asia/Shanghai", 2026, 10, 10, 7, 30)
    ok, _why = should_run(event="workflow_dispatch", has_key=True, created_at=None, bar_date="2026-10-09", last_bar_date=None, expected_bar="2026-10-09", ticker="AAPL", limits=limits)
    if not ok:
        _fail("手动指定代码应放行")
    ok, why = should_run(event="schedule", has_key=True, created_at="2026-10-10T05:00:00Z", bar_date="2026-10-09", last_bar_date=None, expected_bar="2026-10-09", ticker=None, limits=limits)
    if ok or "研究时间" not in why:
        _fail("没对准钟点的定时任务不应跑")
    ok, why = should_run(event="workflow_dispatch", has_key=False, created_at=None, bar_date="2026-10-09", last_bar_date=None, expected_bar="2026-10-09", ticker="AAPL", limits=limits)
    if ok or "LLM_API_KEY" not in why:
        _fail("缺密钥应跳过")
    ok, why = should_run(event="workflow_run", has_key=True, created_at=on_time, bar_date="2026-10-09", last_bar_date="2026-10-09", expected_bar="2026-10-09", ticker=None, limits=limits)
    if ok or "已经研究过" not in why:
        _fail("同一收盘日不应再跑")
    ok, why = should_run(event="workflow_run", has_key=True, created_at=on_time, bar_date="2026-10-08", last_bar_date=None, expected_bar="2026-10-09", ticker=None, limits=limits)
    if ok or "新收盘" not in why:
        _fail("行情未更新应跳过")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "site" / "data").mkdir(parents=True)
        _write_json(root / "site" / "data" / "portfolio.json", {
            "positions": [{"ticker": "AAPL", "instrument": "stock", "call": {"action": "sell", "action_label": "卖出"}}]
        })
        _write_json(root / "site" / "data" / "scanner.json", {"rows": []})
        _write_json(root / "site" / "data" / "meta.json", {"generated_date_cn": "2026-10-10", "bar_date": "2026-10-09"})
        report = "# 日报\n\n## 近似与缺失\n\n- 无\n"
        for folder in (root / "reports", root / "site" / "reports"):
            folder.mkdir(parents=True)
            (folder / "2026-10-10-am.md").write_text(report, encoding="utf-8")
        ignored = ingest_comment("普通评论", root)
        if ignored["status"] != "ignore":
            _fail("普通评论应忽略")
        bought = dict(sample)
        bought["ticker"] = "AAPL"
        bought["rating"] = "买入"
        result = ingest_comment("```json\n" + json.dumps(bought, ensure_ascii=False) + "\n```", root, datetime(2026, 10, 10, 8, 0, tzinfo=SH))
        if result["status"] != "ok" or result["tickers"] != ["AAPL"] or not result["reply"].startswith("✅"):
            _fail(f"入库结果不对：{result}")
        stored = json.loads((root / "data" / "research" / "AAPL.json").read_text(encoding="utf-8"))
        if not stored["diverge"] or stored["rule_action"] != "sell":
            _fail("卖出对买入应写成分歧")
        history = (root / "data" / "research" / "history" / "AAPL.jsonl").read_text(encoding="utf-8").strip().splitlines()
        if len(history) != 1:
            _fail("历史应有一行")
        page = (root / "site" / "research" / "AAPL.html").read_text(encoding="utf-8")
        if "⚠️ 分歧" not in page or "https://www.apple.com/" not in page:
            _fail("HTML 缺分歧或来源")
        am = (root / "reports" / "2026-10-10-am.md").read_text(encoding="utf-8")
        if am.count("## AI 研究") != 1 or "⚠️ 分歧" not in am or "## 近似与缺失" not in am.split("## AI 研究", 1)[1]:
            _fail("日报插入位置不对")
        again = ingest_comment("```json\n" + json.dumps([bought, {**bought, "ticker": "MSFT", "rating": "持有"}], ensure_ascii=False) + "\n```", root)
        if again["status"] != "ok" or again["tickers"] != ["AAPL", "MSFT"]:
            _fail(f"数组入库不对：{again}")
        history = (root / "data" / "research" / "history" / "AAPL.jsonl").read_text(encoding="utf-8").strip().splitlines()
        if len(history) != 2:
            _fail("再次入库应追加历史")
        am = (root / "reports" / "2026-10-10-am.md").read_text(encoding="utf-8")
        if am.count("## AI 研究") != 1:
            _fail("日报段落应替换而不是叠两段")
        index = json.loads((root / "site" / "data" / "research.json").read_text(encoding="utf-8"))
        if set(index["tickers"]) != {"AAPL", "MSFT"} or index["backend"] != "openai-compatible":
            _fail("站点索引不对")
        rejected = ingest_comment("```json\n" + json.dumps({**bought, "rating": "观望"}) + "\n```", root)
        if rejected["status"] != "error" or "✅" in rejected["reply"]:
            _fail("校验失败不该打勾")
        if "观望" in (root / "data" / "research" / "AAPL.json").read_text(encoding="utf-8"):
            _fail("失败的评论不该覆盖原文件")

    print("self-check ok")
