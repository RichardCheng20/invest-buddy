"""中文日报。只复述规则引擎已经算出来的数，不另做判断。"""

from __future__ import annotations

import glossary
import research_io


def _pct(x, digits=1):
    if x is None:
        return "—"
    return f"{x * 100:+.{digits}f}%"


def _num(x, digits=2):
    if x is None:
        return "—"
    return f"{x:,.{digits}f}"


def _usd(x):
    if x is None:
        return "—"
    return f"${x:,.0f}"


def _pct_plain(x):
    if x is None:
        return "—"
    return f"{x * 100:.0f}%"


def _risk_lines(risk):
    plan = ((risk.get("plans") or {}).get("full") or {})
    progress = plan.get("progress") or {}
    before = plan.get("before") or {}
    gross = before.get("gross")
    lines = [
        "下面是按限额排的减仓计划，不是下单。买卖仍以规则引擎为准。",
        f"- 计划里的杠杆（含期权 delta）{_pct_plain(gross)}，目标 ≤100%。一步到位 {progress.get('done', 0)}/{progress.get('total', 0)} 步已完成，还要卖出约 {_usd(progress.get('remaining_proceeds'))}。",
    ]
    steps = [step for step in (plan.get("steps") or []) if step.get("status") != "done"]
    if not steps:
        lines.append("- 按当前持仓，总敞口、单票和主题都已经在限额内。")
        return lines
    nxt = steps[0]
    after = nxt.get("after") or {}
    worst = (after.get("worst_name") or {}).get("ticker")
    worst_w = (after.get("worst_name") or {}).get("weight")
    lines.append(
        f"- 下一步：{nxt.get('title')}，估计金额 {_usd(nxt.get('proceeds'))}。做完后杠杆 {_pct_plain(after.get('gross'))}"
        + (f"，最大单票 {worst} {_pct_plain(worst_w)}。" if worst else "。")
    )
    final = plan.get("final") or {}
    kept = final.get("rows") or []
    if kept:
        bits = "，".join(f"{row['ticker']} {row['kept']}（{_pct_plain(row.get('weight'))}）" for row in kept)
        lines.append(f"- 做完后留下：{bits}。现金 {_usd(final.get('cash'))}，杠杆 {_pct_plain(final.get('gross'))}。")
    if final.get("exited"):
        lines.append("- 规则卖出、按计划清掉：" + "、".join(final["exited"]) + "。")
    for alt in plan.get("alternatives") or []:
        if alt.get("text"):
            lines.append(f"- {alt['text']}")
    return lines


def _earnings_lines(earn):
    if not earn or earn.get("note") and not earn.get("holdings"):
        return [f"- {earn.get('note') or '这次没有财报风险数据。'}"]
    lines = []
    cluster = earn.get("cluster") or {}
    names = "、".join(cluster.get("tickers") or [])
    if names:
        lines.append(
            f"- {cluster.get('start')} 至 {cluster.get('end')} 有持仓财报：{names}。"
            f"若跳空同向，按隐含波动合计 {_usd(cluster.get('loss_implied'))}（{_pct_plain(cluster.get('loss_implied_pct'))} 净值），"
            f"按历史最大不利合计 {_usd(cluster.get('loss_adverse'))}（{_pct_plain(cluster.get('loss_adverse_pct'))}）。"
        )
    hot = []
    for item in earn.get("holdings") or []:
        if not item.get("date"):
            continue
        bits = []
        implied = item.get("flags_implied") or []
        adverse = item.get("flags_adverse") or []
        if implied:
            bits.append(f"隐含波动碰到 {implied[-1]['label']}")
        if adverse:
            bits.append(f"历史不利碰到 {adverse[-1]['label']}")
        if bits:
            hot.append(f"{item['ticker']} {item['date']} " + "、".join(bits))
    if hot:
        lines.append("- " + "；".join(hot) + "。")
    elif not lines:
        lines.append("- 持仓里没有算出会单独碰到 -3% / -5% / -10% 回撤熔断的财报。")
    return lines


def _stop_lines(risk):
    missing = []
    unplaced = []
    for item in risk.get("stops") or []:
        if item.get("stop") is None:
            missing.append(item["ticker"])
        elif not item.get("stop_placed"):
            unplaced.append(f"{item['ticker']} {item['stop']:.2f}")
    lines = []
    if missing:
        lines.append("- 还没填止损价：" + "、".join(missing) + "。这些仓的固定止损和加仓规则仍是未定。")
    if unplaced:
        lines.append("- 止损价已填、但还没勾「已在券商挂单」： " + "；".join(unplaced) + "。请在券商挂好止损单。")
    cap = risk.get("open_risk_cap_usd")
    if risk.get("stops_missing"):
        lines.append(f"- 风险合计还不完整。全部填上之后，上限是净值的 4%（{_usd(cap)}）。已填部分约 {_usd(risk.get('open_risk_partial_usd'))}。")
    else:
        lines.append(f"- 到止损的风险合计 {_usd(risk.get('open_risk_usd'))}，上限 {_usd(cap)}（净值的 4%）。")
    if not lines:
        lines.append("- 没有持仓止损数据。")
    return lines


def build(ctx: dict) -> str:
    meta = ctx["meta"]
    reg = ctx["regime"]
    port = ctx["portfolio"]
    sent = ctx["sentiment"]
    decisions = ctx["decisions"]
    session = "上午" if meta["session"] == "am" else "下午"
    lines = [
        f"# 投研日报 {meta['generated_date_cn']} {session}",
        "",
        f"生成时间：{meta['generated_at_cn']}（北京时间）。行情日期：{meta['bar_date']}（美股最近一个完整交易日的收盘）。",
        "",
        "> 这是规则引擎根据你写在配置里的门槛算出来的结果，只供对照清单，不是投资建议，也不是下单指令。",
        "",
        "## 市场环境",
        "",
        f"- 技术环境：**{glossary.regime_phrase(reg.get('regime'), reg.get('score'))}**，单笔风险系数 {reg.get('risk_mult') if reg.get('risk_mult') is not None else '—'}",
        f"- 宏观档位：{meta['macro']['note']} 上限 {meta['macro']['cap'] * 100:.0f}%。",
        f"- 权益敞口上限（取更严的一侧）：{meta['exposure_cap'] * 100:.0f}%。" if meta.get("exposure_cap") is not None else "- 权益敞口上限：数据不足。",
        f"- 宽度（观察列表近似，n={reg.get('breadth_n')}）：站上 50 日均线 {_pct(reg.get('breadth_above_50'), 0)}，站上 200 日均线 {_pct(reg.get('breadth_above_200'), 0)}。不是标普 500 全成分。",
        f"- VIX {_num(reg.get('vix'))}，VIX3M {_num(reg.get('vix3m'))}，比值 {_num(reg.get('vix_ratio'))}。倒挂锁定：{'是' if reg.get('vix_lock') else '否'}。",
        f"- 派发日（SPY 或 QQQ，25 个交易日）：{reg.get('distribution_count') if reg.get('distribution_count') is not None else '—'}。",
        "",
        "## 情绪与行业",
        "",
    ]
    if sent.get("line"):
        lines.append(f"- {glossary.plain(sent['line'])}")
    else:
        lines.append("- 恐惧与贪婪、看跌/看涨、高收益利差和 AAII 这次没有读数。")
    for item in sent.get("detail") or []:
        lines.append(f"- {glossary.plain(item)}")
    if sent.get("confirm"):
        lines.append(f"- {sent['confirm']}")
    hot = sent.get("sectors_top") or []
    cold = sent.get("sectors_bottom") or []
    if hot:
        lines.append("- 近 1 个月相对 SPY 较强：" + "，".join(f"{x['name']} {_pct(x.get('rs_1m'))}" for x in hot))
    if cold:
        lines.append("- 近 1 个月相对 SPY 较弱：" + "，".join(f"{x['name']} {_pct(x.get('rs_1m'))}" for x in cold))
    lines += ["", "## 账户与限额", ""]
    lines.append(f"- 估算净值 {_usd(port.get('nlv_estimated'))}（现金 {_usd(port.get('cash'))} + 持仓市值）。高水位 {_usd(port.get('hwm'))}，回撤 {_pct(port.get('drawdown'))}。")
    lines.append(f"- 多头敞口 {_pct(port.get('gross'), 0)}，上限 100%。融资：{'是' if port.get('using_margin') else '否'}。")
    if port.get("price_incomplete"):
        lines.append("- 有持仓没有取到价格，净值和敞口不完整。")
    for b in port.get("breaches") or []:
        lines.append(f"- 越限：{b['detail']}")
    if not port.get("breaches"):
        lines.append("- 单票、主题、总敞口没有越过配置里的上限。")
    lines += ["", "## 去杠杆进度", ""]
    lines.extend(_risk_lines(ctx.get("risk") or {}))
    lines += ["", "## 本周财报风险", ""]
    lines.extend(_earnings_lines(ctx.get("earnings_risk") or {}))
    lines += ["", "## 未挂止损的持仓", ""]
    lines.extend(_stop_lines(ctx.get("risk") or {}))
    lines += ["", "## 规则给出的动作", ""]
    actions = decisions.get("actions") or []
    if not actions:
        lines.append("今天没有被规则触发的动作。")
    for i, act in enumerate(actions, 1):
        lines.append(f"{i}. **{glossary.plain(act['title'])}** — {glossary.plain(act['detail'])}")
    lines += ["", "## 入场信号", ""]
    passed = [r for r in ctx["rows"] if r.get("signals") and not r.get("rejected_by") and r.get("shares")]
    rejected = [r for r in ctx["rows"] if r.get("primary_id") and r.get("rejected_by")]
    if passed:
        for r in passed:
            lines.append(
                f"- {r['ticker']} {','.join(glossary.plain(code) for code in r['signals'])}：入场 {_num(r.get('entry'))}，止损 {_num(r.get('stop'))}，"
                f"股数 {r.get('shares')}，风险 {_usd(r.get('risk_usd'))}，1R {_num((r.get('r_targets') or {}).get('1R'))}。"
            )
    else:
        lines.append("没有同时通过形态、环境和组合限额的新开仓。")
    if rejected:
        lines.append("")
        lines.append("技术条件成立、但被组合规则拒绝的：")
        for r in rejected[:12]:
            call = r.get("call") or {}
            lines.append(f"- {glossary.plain(call.get('line') or r['ticker'])}。{glossary.plain(call.get('block') or '；'.join(r.get('rejected_by') or []))}")
    research = research_io.load_records(research_io.REPO)
    if research:
        lines += [""] + research_io.ai_section_lines(research, port, ctx["rows"])
    lines += ["", "## 近似与缺失", ""]
    for note in meta.get("approximations") or []:
        lines.append(f"- {note}")
    for err in (meta.get("errors") or [])[:8]:
        lines.append(f"- 行情失败：{err}")
    lines += [
        "",
        "下单前仍要过框架里的 10 项核对清单。止损单在券商端挂出之后，这里的数字才算执行。",
        "",
    ]
    return "\n".join(lines) + "\n"
