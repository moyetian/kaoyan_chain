# -*- coding: utf-8 -*-
"""
Bing 检索（尽力而为的补充源）

**重要实测结论（2026-09-16）**：对 `www.bing.com/search` 发裸 urllib 请求时，
Bing 会返回 **HTTP 200 但内容与查询完全无关**的页面（软性反爬），而 HTML 结构
（`li.b_algo` + `h2>a`）保持正常 —— 正则能匹配到 10 条「结果」。

因此本 provider：
  * 不再作为主力（主力是 ddg，见该模块的对比实测）；
  * 结果必须过 `search.relevance` 的相关性守门，全部不相关时**记为失败**
    并说明「疑似被反爬」，而不是把垃圾当结果交出去。

保留它的价值在于：部分网络环境下 Bing 可通而 DDG 不通，两者互补。
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Dict, List, Optional

from ..models import SearchResult
from . import register
from ._http import BROWSER_HEADERS, USER_AGENT, clean_bing_url, clean_text, get_text
from .base import ProviderError, SearchProvider

_LOG = logging.getLogger(__name__)

_ENDPOINT = "https://www.bing.com/search"
#: Bing 中文站点偏好 Cookie（缺失时更易被判定为爬虫）
_BING_COOKIES = "SRCHHPGUSR=SRCHLANG=zh-Hans; _EDGE_S=mkt=zh-cn;"

_BLOCK_RE = re.compile(r'<li class="b_algo"[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>',
                       re.DOTALL | re.IGNORECASE)
#: 摘要在 b_caption 里；不同版本用 <p> 或直接文本
_SNIPPET_RE = re.compile(
    r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>',
    re.DOTALL | re.IGNORECASE)


@register
class BingProvider(SearchProvider):
    """Bing 网页检索（补充源；结果需通过相关性守门）。"""

    name = "bing"
    engine_type = "general"
    priority = 20
    description = "Bing 网页检索（补充源，实测易返回无关页，已加相关性守门）"

    def search(self, query: str, *, limit: int = 10,
               time_range: Optional[str] = None) -> List[SearchResult]:
        params = {"q": str(query), "mkt": "zh-CN", "setlang": "zh-Hans"}
        if str(time_range or "").lower() == "year":
            params["filters"] = "ex1%3a\"ez1\""      # Bing 的「过去一年」过滤器
        url = f"{_ENDPOINT}?{urllib.parse.urlencode(params)}"

        headers = dict(BROWSER_HEADERS)
        headers["User-Agent"] = USER_AGENT
        headers["Cookie"] = _BING_COOKIES
        html_text = get_text(url, headers=headers, timeout=12)

        blocks = _BLOCK_RE.findall(html_text)
        if not blocks:
            raise ProviderError("页面结构未匹配到结果块（可能被限流或改版）")

        items: List[Dict[str, str]] = []
        for block in blocks:
            m = _TITLE_RE.search(block)
            if not m:
                continue
            href, t_html = m.group(1), m.group(2)
            real_url = clean_bing_url(href)
            if not real_url.startswith("http"):
                continue
            s_match = _SNIPPET_RE.search(block)
            items.append({
                "title": clean_text(t_html),
                "url": real_url,
                "snippet": clean_text(s_match.group(1)) if s_match else "",
            })
            if len(items) >= max(limit, 1):
                break

        if not items:
            raise ProviderError("解析到结果块，但无可用链接")
        return self.normalize(items)


__all__ = ["BingProvider"]
