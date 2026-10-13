#!/usr/bin/env python3
"""OpenAI 兼容模型 / TradingAgents 研究。定时任务只在 config/research.yaml 的本地钟点写 data/research。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_io  # noqa: E402
import yaml  # noqa: E402


def _load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--event", default="workflow_dispatch")
    parser.add_argument("--ticker", default="")
    parser.add_argument("--created-at", default="")
    args = parser.parse_args()
    if args.self_check:
        research_io.self_check()
        return 0

    research_io.load_local_env()
    limits = research_io.load_limits()
    settings = research_io.llm_settings(limits)
    ticker = research_io.norm_ticker(args.ticker)
    meta = _load(research_io.REPO / "site" / "data" / "meta.json", {})
    state_path = research_io.REPO / "data" / "research" / "state.json"
    state = _load(state_path, {"cursor": 0, "last_bar_date": None, "last_by_ticker": {}})
    bar_date = meta.get("bar_date")
    expected = research_io.expected_closed_session(datetime.now(research_io.ET))
    run, why = research_io.should_run(
        event=args.event,
        has_key=research_io.key_present(settings),
        created_at=args.created_at or None,
        bar_date=bar_date,
        last_bar_date=state.get("last_bar_date"),
        expected_bar=expected,
        ticker=ticker or None,
        limits=limits,
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"run={'true' if run else 'false'}\n")
    if not run:
        print(why, flush=True)
        return 0
    if args.gate:
        print("gate=run", flush=True)
        return 0
    if not os.environ.get("FRED_API_KEY"):
        print("没有 FRED_API_KEY，宏观数据跳过", flush=True)

    book = yaml.safe_load((research_io.REPO / "data" / "portfolio.yaml").read_text(encoding="utf-8"))
    holdings = research_io.stock_holdings(book)
    scanner = _load(research_io.REPO / "site" / "data" / "scanner.json", {"rows": []})
    held_batch: list[str] = []
    new_cursor = int(state.get("cursor") or 0)
    if ticker:
        # 页面上的「立即研究」和手动 workflow_dispatch 都走这里：只跑这一只，不推进每天的轮换。
        batch = [ticker]
    else:
        if not bar_date:
            print("没有行情日期，跳过", flush=True)
            return 0
        batch, new_cursor, held_batch = research_io.select_batch(holdings, scanner, state, bar_date, limits)
    if not batch:
        print("这一轮没有要研究的代码", flush=True)
        return 0
    trade_date = bar_date or datetime.now(research_io.ET).date().isoformat()
    print("研究 " + "、".join(batch), flush=True)

    ok_records = []
    failed = []
    by_model: dict[str, dict[str, int]] = {}
    timeout = int(limits["per_ticker_minutes"]) * 60
    work_cm = tempfile.TemporaryDirectory()
    work = Path(work_cm.name)
    for name in batch:
        out = work / f"{name}.json"
        usage_path = work / f"{name}.usage.json"
        out.unlink(missing_ok=True)
        usage_path.unlink(missing_ok=True)
        cmd = [
            sys.executable,
            str(research_io.REPO / "scripts" / "research_one.py"),
            "--ticker", name,
            "--date", trade_date,
            "--out", str(out),
            "--usage", str(usage_path),
        ]
        try:
            proc = subprocess.run(cmd, cwd=research_io.REPO, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            failed.append(name)
            print(f"{name} 超时", flush=True)
            if exc.stderr:
                print(str(exc.stderr)[-2000:], flush=True)
            continue
        if proc.returncode != 0 or not out.exists():
            failed.append(name)
            tail = (proc.stderr or proc.stdout or "").strip()
            print(f"{name} 失败 {proc.returncode} {tail[-2000:]}", flush=True)
            continue
        record = json.loads(out.read_text(encoding="utf-8"))
        research_io.validate_records(record)
        ok_records.append(record)
        usage = _load(usage_path, {}).get("by_model") or {}
        for model, meta_usage in usage.items():
            bucket = by_model.setdefault(model, {"input_tokens": 0, "output_tokens": 0})
            bucket["input_tokens"] += int(meta_usage.get("input_tokens") or 0)
            bucket["output_tokens"] += int(meta_usage.get("output_tokens") or 0)
        print(f"{name} 完成 {record['rating']}", flush=True)

    now = datetime.now(research_io.SH)
    if ok_records:
        research_io.apply_records(research_io.REPO, ok_records, now)
        last = dict(state.get("last_by_ticker") or {})
        for rec in ok_records:
            last[rec["ticker"]] = rec["date"]
        state["last_by_ticker"] = last
        if not ticker:
            state["cursor"] = new_cursor
            state["last_bar_date"] = trade_date
        _write(state_path, state)
    elif not ticker:
        print("这一轮没有成功的研究，不推进轮换，备跑可以再试", flush=True)

    cost = research_io.estimate_usd(by_model, limits["prices_per_million"])
    if ok_records or failed:
        research_io.append_usage(research_io.REPO, {
            "at": now.isoformat(timespec="seconds"),
            "month": now.strftime("%Y-%m"),
            "bar_date": trade_date,
            "tickers": batch,
            "ok": [rec["ticker"] for rec in ok_records],
            "failed": failed,
            "by_model": by_model,
            "estimated_usd": cost,
        }, now)
    work_cm.cleanup()
    print(f"完成 {len(ok_records)}，失败 {len(failed)}，估算 ${cost}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
