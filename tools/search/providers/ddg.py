# -*- coding: utf-8 -*-
"""
DuckDuckGo HTML 检索（默认主力源）

[为什么是主力] 实测对比（2026-09-16，本机直连）：

    查询「南方医科大学 085409 招生」
      DDG → 南方医科大学研究生招生网 / 2027 生物医学工程考研专业介绍 / 学院招生页 ✓
      Bing→ Glodon CAD 用户协议 / 隐私政策 / CAD 看图触屏版 ✗（完全无关）
    查询「华中科技大学 计算机 复试线」
      DDG → 研究生院历年复试分数线（官方）/ 知乎数据深度分析 ✓
      Bing→ 日文「エアコン2027年問題」省电标准文章 ✗
    查询「考研数学二 大纲」
      DDG → 考纲 PDF / 知乎考纲汇总 ✓
      Bing→ YouTube TV Help / OBS 下载页 ✗

Bing 对裸 `urllib` 请求会返回 **HTTP 200 + 无关内容**（软性反爬），HTML 结构
（`li.b_algo`、`h2>a`）却完全正常 —— 旧实现因此把垃圾当成检索结果返回。
故本模块把 DDG 提为主力，Bing 降为尽力而为的补充源，并统一由
`search.relevance` 做相关性守门（见该模块说明）。
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Dict, List, Optional

from ..models import SearchResult
from . import register
from ._http import clean_ddg_url, clean_text, get_text, looks_like_anti_bot
from .base import ProviderError, SearchProvider

_LOG = logging.getLogger(__name__)

_ENDPOINT = "https://html.duckduckgo.com/html/"
#: 备用端点（不同主机，限流桶独立；实测主端点被限流时它有时仍可用）
_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
#: 结果块：标题链接 + 摘要
_TITLE_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE)
_SNIPPET_RE = re.compile(
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE)
#: 备用：部分返回版本把摘要放在 <div class="result__snippet">
_SNIPPET_DIV_RE = re.compile(
    r'<div[^>]+class="result__snippet"[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE)
#: 时间过滤语法：DDG 支持 df=y / df=m / df=w
_TIME_MAP = {"year": "y", "month": "m", "week": "w"}


@register
class DuckDuckGoProvider(SearchProvider):
    """DuckDuckGo HTML 版检索（零依赖，无需 API Key）。"""

    name = "ddg"
    engine_type = "general"
    priority = 10
    description = "DuckDuckGo HTML 检索（默认主力源，实测中文考研查询相关性最好）"

    def search(self, query: str, *, limit: int = 10,
               time_range: Optional[str] = None) -> List[SearchResult]:
        params = {"q": str(query)}
        df = _TIME_MAP.get(str(time_range or "").lower())
        if df:
            params["df"] = df
        query_str = urllib.parse.urlencode(params)
        last_anti_bot = ""
        html_text = ""
        titles: List = []
        for endpoint in (_ENDPOINT, _LITE_ENDPOINT):
            html_text = get_text(f"{endpoint}?{query_str}", timeout=12)
            anti_bot = looks_like_anti_bot(html_text)
            if anti_bot:
                last_anti_bot = anti_bot
                continue                      # 换端点再试
            titles = _TITLE_RE.findall(html_text)
            if titles:
                break
        if not titles:
            if last_anti_bot:
                # 说清楚「是被挡了」而不是「没有结果」——两者对用户的处置完全不同
                raise ProviderError(
                    f"DuckDuckGo 返回反爬验证页（命中特征 {last_anti_bot}），"
                    f"本次跳过该源（稍后重试或改用其它源）")
            raise ProviderError("页面结构未匹配到结果块（可能被限流或改版）")
        snippets = _SNIPPET_RE.findall(html_text) or _SNIPPET_DIV_RE.findall(html_text)

        items: List[Dict[str, str]] = []
        for i, (href, t_html) in enumerate(titles[: max(limit, 1)]):
            real_url = clean_ddg_url(href)
            if not real_url.startswith("http"):
                continue
            items.append({
                "title": clean_text(t_html) or f"检索结果 {i + 1}",
                "url": real_url,
                "snippet": clean_text(snippets[i]) if i < len(snippets) else "",
            })
        if not items:
            raise ProviderError("解析到结果块，但无可用链接")
        return self.normalize(items)


__all__ = ["DuckDuckGoProvider"]
