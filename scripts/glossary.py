"""页面和日报共用的中文名称。规则代码只作辅文，不单独出现。"""

from __future__ import annotations

import re

SHORT = {
    "E1": "突破买点",
    "E2": "回踩买点",
    "E3": "口袋支点",
    "E4": "财报跳空买点",
    "E5": "挤压买点",
    "C1": "买入确认分",
    "X1": "固定止损",
    "X2": "突破失败",
    "X3": "跌破吊灯移动止损",
    "X4": "跌破均线减仓",
    "X5": "时间止损",
    "X6": "高潮止盈",
    "X7": "财报前减仓",
    "X8": "强度走坏减仓",
    "A1": "盈利加仓",
}

_PHRASES = (
    (re.compile(r"E1\s*[–-]\s*E5(?:\s*买点)?"), "突破、回踩、口袋支点、财报跳空或挤压买点"),
    (re.compile(r"X1\s*硬止损"), "固定止损"),
    (re.compile(r"X3\s*移动止损"), "跌破吊灯移动止损"),
    (re.compile(r"X2\s*突破失败"), "突破失败"),
    (re.compile(r"X4\s*跌破均线"), "跌破均线减仓"),
    (re.compile(r"X5\s*时间止损"), "时间止损"),
    (re.compile(r"X6\s*高潮止盈"), "高潮止盈"),
    (re.compile(r"X7\s*财报减仓"), "财报前减仓"),
    (re.compile(r"X8\s*强度走坏"), "强度走坏减仓"),
    (re.compile(r"C1:(\d)"), r"买入确认分 \1/3"),
    (re.compile(r"(?<!买入)确认分\s*(\d)\s*/\s*3"), r"买入确认分 \1/3"),
    (re.compile(r"RS\s*排名"), "相对强度排名"),
    (re.compile(r"RSI\s*14|RSI14"), "14日相对强弱指数"),
    (re.compile(r"ATR\s*14|ATR14"), "14日平均真实波幅"),
    (re.compile(r"ATR%"), "波动幅度"),
    (re.compile(r"吊灯止损"), "吊灯移动止损"),
    (re.compile(r"吊灯参考"), "吊灯移动止损参考价"),
    (re.compile(r"stage2", re.I), "第二阶段上升趋势"),
    (re.compile(r"\b1R\b"), "一倍风险距离"),
    (re.compile(r"\b2R\b"), "两倍风险距离"),
    (re.compile(r"\bMACD\b"), "指数平滑异同"),
    (re.compile(r"\bRSI\b"), "相对强弱指数"),
    (re.compile(r"\bATR\b"), "平均真实波幅"),
    (re.compile(r"\bRS\b"), "相对强度"),
    (re.compile(r"（GREEN）"), "（偏多，可进攻）"),
    (re.compile(r"（YELLOW）"), "（中性，宜谨慎）"),
    (re.compile(r"（RED）"), "（偏空，宜防守）"),
    (re.compile(r"恐惧与贪婪"), "CNN 恐惧贪婪指数"),
    (re.compile(r"散户差"), "AAII 散户看多减看空"),
    (re.compile(r"\b(E[1-5]|X[1-8]|A1|C1)\b"), lambda m: SHORT.get(m.group(1), m.group(1))),
    (re.compile(r"吊灯(?!移动)"), "吊灯移动止损"),
    (re.compile(r"没有\s+突破"), "没有突破"),
)


def plain(text: str) -> str:
    if not text:
        return ""
    out = str(text)
    for pattern, repl in _PHRASES:
        out = pattern.sub(repl, out)
    return out


def fg_read(score) -> str:
    n = float(score)
    if n <= 25:
        return "极度恐惧，逆向偏多"
    if n <= 45:
        return "偏恐惧，逆向偏多"
    if n < 55:
        return "中性"
    if n < 75:
        return "偏贪婪，逆向偏谨慎"
    return "极度贪婪，逆向偏空"


def pc_read(value) -> str:
    return "小于 1，正常" if float(value) < 1 else "大于 1，偏恐慌"


def term_read(ratio, backwardation=False) -> str:
    if backwardation or float(ratio) > 1:
        return "大于 1，近月更慌"
    return "小于 1，正常"


def aaii_read(spread) -> str:
    pts = float(spread) * 100
    if pts >= 10:
        return "散户明显偏多，逆向偏谨慎"
    if pts <= -10:
        return "散户明显偏空，逆向偏多"
    return "多空差不大"


def regime_phrase(regime: str | None, score) -> str:
    word = {"GREEN": "偏多，可进攻", "YELLOW": "中性，宜谨慎", "RED": "偏空，宜防守"}.get(regime or "", "未知")
    shown = "—" if score is None else score
    return f"市场环境 {shown}/5（{word}）"
