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
from ._http import (
    clean_bing_url,
    clean_text,
    get_browser_headers,
    get_text,
    looks_like_anti_bot,
)
from .base import ProviderError, SearchProvider

_LOG = logging.getLogger(__name__)

_ENDPOINT = "https://www.bing.com/search"
#: 备用端点（不同主机，限流桶独立；端点选择逻辑与 ddg.py 的双端点实现同思路）。
#: [P1 修复·2026-10-08 S5] 此前只有单一端点：主端点被限流时整个源停摆。
#: cn.bing.com 在国内网络下解析更稳，与主端点互为备份。
_CN_ENDPOINT = "https://cn.bing.com/search"
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
               time_range: Optional[str] = None,
               safe: bool = False) -> List[SearchResult]:
        params = {"q": str(query), "mkt": "zh-CN", "setlang": "zh-Hans"}
        if str(time_range or "").lower() == "year":
            params["filters"] = "ex1%3a\"ez1\""      # Bing 的「过去一年」过滤器
        query_str = urllib.parse.urlencode(params)

        # [P1 修复·2026-10-08 S5] 此前 UA 是单一固定常量（BROWSER_HEADERS 的
        # USER_AGENT）——同一指纹反复请求会更快被软性反爬盯上。改用共享的
        # get_browser_headers()（随机 UA 池 + 完整浏览器头，与 ddg/sogou 同源）；
        # Cookie 作为 extra 合并进去。
        headers = get_browser_headers({"Cookie": _BING_COOKIES})

        # [P1 修复·2026-10-08 S5] 双端点轮询（参照 ddg.py 的实现）：主端点
        # 网络异常或返回反爬验证页时自动换 cn.bing.com 再试一次，而不是直接
        # 判整个源失败。两处都失败才如实报错。
        last_anti_bot = ""
        last_error: Optional[Exception] = None
        blocks: List[str] = []
        for endpoint in (_ENDPOINT, _CN_ENDPOINT):
            try:
                html_text = get_text(f"{endpoint}?{query_str}",
                                     headers=headers, timeout=12)
            except Exception as exc:
                last_error = exc
                _LOG.debug("Bing 端点 %s 请求异常: %s", endpoint, exc)
                continue                      # 换端点再试
            anti_bot = looks_like_anti_bot(html_text)
            if anti_bot:
                last_anti_bot = anti_bot
                continue                      # 换端点再试
            blocks = _BLOCK_RE.findall(html_text)
            if blocks:
                break

        if not blocks:
            if safe:
                _LOG.warning("Bing 未获取到有效结果，优雅降级为空列表: 反爬=%s",
                             last_anti_bot or "无结果")
                return []
            # 判定优先级：反爬（确定性信号）> 网络异常 > 结构未匹配。
            if last_anti_bot:
                raise ProviderError(f"Bing 返回反爬验证页（命中特征 {last_anti_bot}）")
            if last_error is not None:
                raise ProviderError(f"Bing 网络请求失败: {last_error}") from last_error
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
            if safe:
                return []
            raise ProviderError("解析到结果块，但无可用链接")
        return self.normalize(items)

    def safe_search(self, query: str, *, limit: int = 10,
                    time_range: Optional[str] = None) -> List[SearchResult]:
        """安全检索：遇到网络异常或反爬验证码时优雅降级为空列表，绝不抛出异常。"""
        return self.search(query, limit=limit, time_range=time_range, safe=True)


__all__ = ["BingProvider"]
