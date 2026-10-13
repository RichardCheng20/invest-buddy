#!/usr/bin/env python3
"""用 TradingAgents 研究一只股票，写出符合 schemas/research.schema.json 的对象。

TradingAgents
Copyright Tauric Research
Licensed under the Apache License, Version 2.0
https://github.com/TauricResearch/TradingAgents
固定提交见 requirements-research.txt。源码不放进本仓库。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_io  # noqa: E402
import yaml  # noqa: E402


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _points(text: str, limit: int = 4) -> list[str]:
    found = []
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if not line:
            continue
        found.append(_clip(line, 300))
        if len(found) >= limit:
            break
    return found or ["模型没有分开列出。"]


def _conclusion(state: dict, rating: str) -> str:
    for key in ("investment_plan", "final_trade_decision", "market_report"):
        text = " ".join(str(state.get(key) or "").split())
        if not text:
            continue
        for mark in ("。", "！", "？", "\n"):
            if mark in text:
                text = text.split(mark)[0]
                break
        text = text.strip()
        if text:
            return _clip(text, 500)
    return _clip(f"{rating}。模型没有另写一句话结论。", 500)


def _disable_social() -> None:
    import tradingagents.agents.analysts.sentiment_analyst as sentiment

    def reddit_off(*_args, **_kwargs):
        return "<unavailable> Reddit turned off"

    def stocktwits_off(*_args, **_kwargs):
        return "<unavailable> StockTwits turned off"

    sentiment.fetch_reddit_posts = reddit_off
    sentiment.fetch_stocktwits_messages = stocktwits_off


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--usage", required=True)
    args = parser.parse_args()
    research_io.load_local_env()
    limits = research_io.load_limits()
    settings = research_io.llm_settings(limits)
    if not research_io.key_present(settings):
        print("没有 LLM_API_KEY，跳过", flush=True)
        return 0
    research_io.apply_provider_env(settings)
    if not os.environ.get("SEC_EDGAR_USER_AGENT"):
        print("没有 SEC_EDGAR_USER_AGENT，SEC 文件可能被拒", flush=True)
    if not os.environ.get("FRED_API_KEY"):
        print("没有 FRED_API_KEY，宏观数据跳过", flush=True)
    ticker = research_io.norm_ticker(args.ticker)
    book = yaml.safe_load((research_io.REPO / "data" / "portfolio.yaml").read_text(encoding="utf-8"))
    payload = research_io.portfolio_payload(book)

    _disable_social()
    from langchain_core.callbacks import UsageMetadataCallbackHandler
    from tradingagents.default_config import build_default_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.portfolio import PortfolioContext, Position

    config = build_default_config()
    config["llm_provider"] = settings["provider"]
    if settings["provider"] == "anthropic":
        host = (urlparse(str(settings["base_url"] or "")).hostname or "").lower()
        config["backend_url"] = settings["base_url"] if host == "api.anthropic.com" else None
    else:
        config["backend_url"] = settings["base_url"]
    config["quick_think_llm"] = settings["model_fast"]
    config["deep_think_llm"] = settings["model_deep"]
    config["output_language"] = "Chinese"
    config["max_debate_rounds"] = int(limits["max_debate_rounds"])
    config["max_risk_discuss_rounds"] = int(limits["max_risk_rounds"])
    config["max_tool_rounds"] = int(limits["max_tool_rounds"])
    config["news_article_limit"] = 8
    config["global_news_article_limit"] = 5
    config["checkpoint_enabled"] = False
    handler = UsageMetadataCallbackHandler()
    graph = TradingAgentsGraph(
        selected_analysts=("market", "social", "news", "fundamentals"),
        config=config,
        callbacks=[handler],
    )
    portfolio = PortfolioContext(
        cash=payload["cash"],
        currency=payload["currency"],
        positions=[Position(**item) for item in payload["positions"]],
    )
    final_state, signal = graph.propagate(ticker, args.date, asset_type="stock", portfolio=portfolio)
    rating = research_io.EN_TO_CN.get(str(signal or ""))
    if not rating:
        print(f"{ticker} 的评级无法对应五档：{signal}", flush=True)
        return 2
    debate = final_state.get("investment_debate_state") or {}
    news = _clip(final_state.get("news_report"), 1800)
    sentiment = _clip(final_state.get("sentiment_report"), 1800)
    record = {
        "ticker": ticker,
        "date": args.date,
        "rating": rating,
        "conclusion": _conclusion(final_state, rating),
        "bull_points": _points(debate.get("bull_history")),
        "bear_points": _points(debate.get("bear_history")),
        "news_sentiment": _clip(f"{news}\n{sentiment}".strip(), 4000),
        "fundamentals": {"摘要": _clip(final_state.get("fundamentals_report"), 800)},
        "technical_view": _clip(final_state.get("market_report"), 4000) or "模型没有写出技术结论。",
        "risk_review": _clip(final_state.get("final_trade_decision"), 4000) or "模型没有写出风险结论。",
        "sources": [],
        "model_note": f"{research_io.provider_label(settings)} · TradingAgents Apache-2.0 · {settings['model_fast']} / {settings['model_deep']}",
    }
    research_io.validate_records(record)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    usage = {}
    for model, meta in (handler.usage_metadata or {}).items():
        usage[model] = {
            "input_tokens": int(meta.get("input_tokens") or 0),
            "output_tokens": int(meta.get("output_tokens") or 0),
        }
    Path(args.usage).write_text(json.dumps({"by_model": usage}, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{ticker} {rating}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
