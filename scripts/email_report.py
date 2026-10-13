#!/usr/bin/env python3
"""早报 / 盘前 HTML 邮件。无背景色、无白字，用粗体彩色文字和 emoji 前缀。"""

from __future__ import annotations

import argparse
import html
import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
INK = "#1a1a1a"
NAVY = "#1d4e89"
RED = "#b42318"
GREEN = "#0f766e"
AMBER = "#b45309"
PURPLE = "#6d28d9"


def _load_json(name: str, default):
    path = ROOT / "site" / "data" / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _esc(value) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def _pct(value, digits=1) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:+.{digits}f}%"


def _usd(value) -> str:
    if value is None:
        return "—"
    return f"${float(value):,.0f}"


def _num(value, digits=2) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.{digits}f}"


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "email.yaml").read_text(encoding="utf-8"))


def is_due(spec: dict, now: datetime) -> bool:
    zone = ZoneInfo(spec["timezone"])
    local = now.astimezone(zone)
    if local.isoweekday() not in [int(day) for day in spec["weekdays"]]:
        return False
    hour, minute = [int(part) for part in str(spec["time"]).split(":", 1)]
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    tolerance = float(spec.get("tolerance_minutes") or 25)
    return abs((local - target).total_seconds()) <= tolerance * 60


def choose(config: dict, now: datetime, requested: str) -> tuple[str, bool]:
    if requested in ("morning", "premarket"):
        return requested, True
    for name in ("morning", "premarket"):
        if is_due(config[name], now):
            return name, True
    return "", False


def _line(emoji: str, label: str, body: str, color: str = NAVY) -> str:
    return (
        f'<p style="color:{INK};margin:8px 0;">'
        f'<strong style="color:{color};">{emoji} {_esc(label)}</strong> {_esc(body)}</p>'
    )


def _heading(emoji: str, text: str, color: str) -> str:
    return f'<p style="color:{color};margin:18px 0 6px;"><strong style="color:{color};">{emoji} {_esc(text)}</strong></p>'


def render(kind: str, config: dict, now: datetime) -> tuple[str, str]:
    spec = config[kind]
    zone = ZoneInfo(spec["timezone"])
    local = now.astimezone(zone)
    meta = _load_json("meta.json", {})
    regime = _load_json("regime.json", {})
    port = _load_json("portfolio.json", {})
    sent = _load_json("sentiment.json", {})
    decisions = _load_json("decisions.json", {})
    risk = _load_json("risk.json", {})
    earn = _load_json("earnings_risk.json", {})
    if not meta:
        raise SystemExit("还没有 site/data/meta.json。先跑 python scripts/refresh.py")

    label = spec["label"]
    subject = f"{label} {local.strftime('%Y-%m-%d %H:%M')} {spec['timezone']}"
    parts = [
        "<!DOCTYPE html>",
        f'<html><body style="color:{INK};font-family:sans-serif;line-height:1.5;">',
        _heading("📈" if kind == "morning" else "🌅", f"{label} · {local.strftime('%Y-%m-%d %H:%M')}", NAVY),
        _line("🕒", "生成", f"{local.strftime('%Y-%m-%d %H:%M')}（{spec['timezone']}）。行情日期 {meta.get('bar_date') or '—'}。", INK),
        _heading("🧭", "市场环境", PURPLE),
        _line("📌", "技术环境", f"{regime.get('regime') or '—'}，单笔风险系数 {_num(regime.get('risk_mult'), 2)}。"),
        _line("🌡️", "波动", f"VIX {_num(regime.get('vix'))}，VIX3M {_num(regime.get('vix3m'))}。"),
    ]
    if sent.get("line"):
        parts.append(_line("😶", "情绪", sent["line"]))
    parts.append(_heading("💼", "账户", NAVY))
    parts.append(_line("💵", "净值", f"{_usd(port.get('nlv_estimated'))}，现金 {_usd(port.get('cash'))}，回撤 {_pct(port.get('drawdown'))}。"))
    gross = port.get("gross")
    parts.append(_line("📏", "敞口", f"{_pct(gross, 0)}。融资：{'是' if port.get('using_margin') else '否'}。", AMBER if port.get("using_margin") else GREEN))
    breaches = port.get("breaches") or []
    if breaches:
        for item in breaches[:8]:
            parts.append(_line("🚨", "越限", item.get("detail") or item, RED))
    else:
        parts.append(_line("✅", "限额", "单票、主题、总敞口没有越过配置里的上限。", GREEN))

    plan = (risk.get("full") or risk.get("plan") or {}) if isinstance(risk, dict) else {}
    final = plan.get("final") or {}
    if final:
        parts.append(_heading("🪓", "去杠杆计划", AMBER))
        parts.append(_line("🎯", "做完以后", f"敞口 {_pct(final.get('gross'), 0)}，现金 {_usd(final.get('cash'))}。"))
        for row in (final.get("rows") or [])[:8]:
            parts.append(_line("📎", row.get("ticker"), f"留下 {row.get('kept')}，约占 {_pct(row.get('weight'), 0)}。"))

    cluster = (earn or {}).get("cluster") or {}
    if cluster:
        parts.append(_heading("📅", "财报风险", AMBER))
        parts.append(_line("⚠️", "若这些跳空同向", cluster.get("label") or cluster.get("window") or "见页面日历。", RED))

    parts.append(_heading("📋", "规则动作", PURPLE))
    actions = decisions.get("actions") or []
    if not actions:
        parts.append(_line("➖", "动作", "今天没有被规则触发的动作。"))
    for act in actions[:12]:
        parts.append(_line("▶️", act.get("title") or "动作", act.get("detail") or ""))

    parts.append(_heading("⚖️", "免责声明", INK))
    parts.append(_line("📄", "说明", "这是规则引擎的对照清单，不是投资建议，也不是下单指令。示例仓库里的持仓标了 EXAMPLE。"))
    parts.append("</body></html>")
    document = "\n".join(parts)
    banned = ("background", "#fff", "#ffffff", "white")
    lowered = document.lower()
    for token in banned:
        if token in lowered:
            raise SystemExit(f"邮件 HTML 含有不允许的样式：{token}")
    return subject, document


def send(subject: str, document: str) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "")
    recipients = [item.strip() for item in os.environ.get("REPORT_TO", "").split(",") if item.strip()]
    port = int(os.environ.get("SMTP_PORT") or "587")
    missing = [name for name, value in (
        ("SMTP_HOST", host), ("SMTP_USER", user), ("SMTP_PASS", password), ("REPORT_TO", recipients),
    ) if not value]
    if missing:
        raise SystemExit("缺少 SMTP 配置：" + "、".join(missing))
    message = MIMEText(document, "html", "utf-8")
    message["Subject"] = subject
    message["From"] = user
    message["To"] = ", ".join(recipients)
    if port == 465:
        client = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(host, port, timeout=30)
        client.starttls(context=ssl.create_default_context())
    try:
        client.login(user, password)
        client.sendmail(user, recipients, message.as_string())
    finally:
        client.quit()


def _emit(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--kind", default="auto", choices=["auto", "morning", "premarket"])
    args = parser.parse_args()
    config = load_config()
    now = datetime.now(timezone.utc)
    kind, force = choose(config, now, "" if args.kind == "auto" else args.kind)
    if args.kind == "auto" and not kind:
        print("还没到 config/email.yaml 里的发送时间", flush=True)
        _emit("send", "false")
        return 0
    if args.kind in ("morning", "premarket"):
        kind = args.kind
        force = True
    _emit("send", "true")
    _emit("kind", kind)
    if args.check and not args.render and not args.send:
        print(f"到点：{kind}", flush=True)
        return 0
    subject, document = render(kind or "morning", config, now)
    if args.render or not args.send:
        print(document)
        return 0
    if not force and not kind:
        return 0
    send(subject, document)
    print(f"已发送 {subject}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
