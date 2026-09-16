# -*- coding: utf-8 -*-
"""
检索结果的相关性守门（防「引擎返回 200 但内容是垃圾」）

[为什么必须有这一层] 实测发现：对 `https://www.bing.com/search?q=...` 发裸 urllib
请求（带普通 UA 与 Cookie）时，Bing 会返回 **HTTP 200 + 完全无关的内容**：

    查询「南方医科大学 085409 招生」→ 首条是 Glodon CAD 用户协议页
    查询「华中科技大学 计算机 复试线」→ 首条是日文空调省电标准文章
    查询「考研数学二 大纲」        → 首条是 YouTube TV Help

这是软性反爬：状态码、HTML 结构（`li.b_algo`、`h2>a`）都正常，正则能匹配到
10 条「结果」—— 于是旧实现把这些垃圾**当成检索结果返回**，再由 Agent 当成
素材写进回答。这比「搜不到」危险得多。

本模块提供一道便宜的守门：若某 provider 返回的结果里**没有一条含查询关键词**，
就把该 provider 记为失败（而不是把垃圾当结果），并在原因里说清「疑似被反爬」。
逐条过滤也顺带去掉混在真实结果里的无关项。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

#: 检索词里的操作符/噪声（这些词不能当作相关性证据）
_OPERATORS = re.compile(r"\b(?:site|inurl|intitle|filetype|related):\S+", re.IGNORECASE)
_STOPWORDS = frozenset({
    "考研", "招生", "专业", "大学", "学院", "研究生", "硕士", "the", "and", "for",
    "com", "www", "http", "https", "cn", "org",
})

#: 数字 token：专业代码/年份/院校代码（单独出现不足以判定相关，见 is_relevant）
_STRONG_RE = re.compile(r"\b\d{4,}\b")
#: 常规 token：连续中文 2 字以上，或连续英文/数字 3 字以上
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{3,}")


def significant_tokens(query: str) -> List[str]:
    """从检索词里抽取「有辨别力」的 token（去掉操作符与常见噪声词）。"""
    text = _OPERATORS.sub(" ", str(query or ""))
    tokens: List[str] = []
    # 先按空白/标点切片，保留「南方医科大学」这类整体
    for chunk in re.split(r"[\s,，、。;；|]+", text):
        chunk = chunk.strip().strip('"\'')
        if len(chunk) >= 2 and chunk not in _STOPWORDS:
            tokens.append(chunk.lower())
    # 再抽子 token（供长句查询使用）
    for m in _TOKEN_RE.findall(text):
        low = m.lower()
        if len(low) >= 2 and low not in _STOPWORDS and low not in tokens:
            tokens.append(low)
    # 数字 token 前置（专业代码等对排序/展示有用）
    strong = [t for t in _STRONG_RE.findall(text) if t not in tokens]
    return strong + tokens


#: 年份形态的 4 位数字区间（考研语境下「2027」这类年份几乎没有辨别力）
_YEAR_MIN, _YEAR_MAX = 1900, 2100


def token_kind(token: str) -> str:
    """把 token 分为三类，决定它的命中能证明多少相关性：

    * ``year``  —— 4 位年份（1900~2100）：**几乎没有辨别力**。
      实测反例：查询「华中科技大学 计算机 2027 复试线」时，Bing 返回的日文垃圾页
      `エアコン2027年問題` 里恰好含「2027年」；若年份命中就算相关，这条垃圾会被放行。
    * ``code``  —— 更长的数字（如专业代码 085409、院校代码 10487）：辨别力强，命中即可。
    * ``word``  —— 中文/英文关键词：命中即可。
    """
    if token.isdigit():
        if len(token) == 4 and _YEAR_MIN <= int(token) <= _YEAR_MAX:
            return "year"
        return "code"
    return "word"


def is_relevant(title: str, snippet: str, url: str,
                tokens: Sequence[str]) -> bool:
    """结果是否与检索词相关。

    规则：**命中任一 `word` 或 `code` 即算相关；仅命中 `year` 不算**。
    这样既拦得住「反爬返回的无关页里恰好带个年份」，也不会因为结果里只出现
    专业代码（如「085409 生物医学工程」）而误杀真实结果。
    """
    if not tokens:
        # 抽不出关键词（查询过短或全是符号）时**无法判定**，此时不误杀。
        # 反爬守门针对的是「查询有明确关键词、结果却毫不相关」的情形，
        # 对无法判定的查询一律放行更安全（宁可少拦，不要错杀真实结果）。
        return True
    haystack = f"{title or ''} {snippet or ''} {url or ''}".lower()
    for tok in tokens:
        if token_kind(tok) == "year":
            continue
        if tok in haystack:
            return True
    return False


def filter_relevant(results: Iterable, query: str) -> Tuple[List, int]:
    """逐条过滤，返回 ``(保留的结果, 被丢弃的数量)``。"""
    tokens = significant_tokens(query)
    kept: List = []
    dropped = 0
    for r in results:
        if is_relevant(getattr(r, "title", ""), getattr(r, "snippet", ""),
                       getattr(r, "url", ""), tokens):
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


def anti_bot_reason(total: int, kept: int) -> str:
    """构造给用户看的失败原因（不含技术噪音，但保留可判断的信息）。"""
    return (f"返回 {total} 条结果，但无一与查询关键词相关 —— "
            f"疑似被搜索引擎反爬拦截或返回了无关页面（已丢弃，不作为检索结果）")


__all__ = [
    "anti_bot_reason",
    "filter_relevant",
    "is_relevant",
    "significant_tokens",
    "token_kind",
]
