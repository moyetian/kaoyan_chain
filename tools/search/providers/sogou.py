# -*- coding: utf-8 -*-
"""
搜狗微信检索（面向公众号文章 / 经验贴，即「资料检索」场景）

[为什么不直接做通用网页检索] 实测（2026-09-16）：
  * `www.sogou.com/web?query=...` 对自动化请求直接返回**验证码页**
    （页面含「请协助验证 / SourceVerifyCode」），拿不到结果 ——
    与其把验证码文本当结果返回，不如不提供该 provider；
  * `weixin.sogou.com/weixin?type=2&query=...` 正常响应（本机实测 33KB、
    10 条 `div.txt-box`），适合检索公众号文章与经验贴。

[已知边界] 结果链接是搜狗的 `/link?url=...&token=...` 跳转地址，需要搜索会话的
cookie 才能在浏览器/抓取器里还原成真正的 `mp.weixin.qq.com` 原文地址；
把跳转解析成原文是 `skills/wechat_searcher.py` 已有的能力，本 provider 不重复实现，
只如实标注来源类型为 wechat（避免被域名表按 sogou.com 误判成普通聚合站）。
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Dict, List, Optional

from ..models import SearchResult
from . import register
from ._http import absolute, clean_text, get_text
from .base import ProviderError, SearchProvider

_LOG = logging.getLogger(__name__)

_ENDPOINT = "https://weixin.sogou.com/weixin"
_BASE = "https://weixin.sogou.com"

#: 结果块：li 内含 div.txt-box（标题 + 摘要 + 账号/日期）
_ITEM_RE = re.compile(r'<div class="txt-box">(.*?)</div>\s*</li>', re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                       re.DOTALL | re.IGNORECASE)
_SNIPPET_RE = re.compile(r'<p class="txt-info"[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)
_ACCOUNT_RE = re.compile(r'<a[^>]+class="account"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_DATE_RE = re.compile(r"timeConvert\('(\d+)'\)")

#: 无结果（属正常结果，不是错误）
_NO_RESULT_MARKERS = ("没有找到相关的微信公众号文章", "没有找到相关文章")
#: 反爬/验证码（属错误，必须显式失败）
_BLOCK_MARKERS = ("SourceVerifyCode", "请协助验证", "antispider", "请输入验证码",
                  "您的访问过于频繁")


@register
class SogouWeixinProvider(SearchProvider):
    """搜狗微信文章检索（公众号文章/经验贴）。"""

    name = "sogou-weixin"
    engine_type = "resource"
    priority = 30
    description = "搜狗微信文章检索（公众号经验贴/资料，覆盖「资料发现」场景）"

    def search(self, query: str, *, limit: int = 10,
               time_range: Optional[str] = None) -> List[SearchResult]:
        params = {"type": "2", "query": str(query)}
        url = f"{_ENDPOINT}?{urllib.parse.urlencode(params)}"
        html_text = get_text(url, timeout=12)

        if any(marker in html_text for marker in _BLOCK_MARKERS):
            raise ProviderError("搜狗要求验证码/限制访问（反爬），本次跳过该源")
        if any(marker in html_text for marker in _NO_RESULT_MARKERS):
            return []                    # 确实没有相关文章，属正常结果

        blocks = _ITEM_RE.findall(html_text)
        if not blocks:
            raise ProviderError("页面结构未匹配到文章块（可能改版或被限流）")

        items: List[Dict[str, str]] = []
        for block in blocks[: max(limit, 1)]:
            m = _TITLE_RE.search(block)
            if not m:
                continue
            href, title_html = m.group(1), m.group(2)
            s_match = _SNIPPET_RE.search(block)
            a_match = _ACCOUNT_RE.search(block)
            d_match = _DATE_RE.search(block)
            items.append({
                "title": clean_text(title_html),
                "url": absolute(href.replace("&amp;", "&"), _BASE),
                "snippet": clean_text(s_match.group(1)) if s_match else "",
                "source_type": "wechat",     # 链接指向公众号文章，而非搜狗自身
                "account": clean_text(a_match.group(1)) if a_match else "",
                "published_at": d_match.group(1) if d_match else "",
            })
        if not items:
            raise ProviderError("解析到文章块，但无可用链接")
        return self.normalize(items)


__all__ = ["SogouWeixinProvider"]
