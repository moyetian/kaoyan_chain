# -*- coding: utf-8 -*-
"""
Provider 共用的抓取与文本清洗

HTTP 层复用项目既有的 `intelligence.fetcher.HTTPFetcher`（已有 gzip 解压、
编码嗅探、超时、SSL 回退），而不是每个 provider 各写一遍 urllib ——
「同一件事只做一次」是这次升级反复出现的原则。
"""

from __future__ import annotations

import base64
import html
import logging
import re
import urllib.parse
from typing import Dict, Optional

from ..providers.base import ProviderError

_LOG = logging.getLogger(__name__)

#: 桌面浏览器 UA（部分搜索引擎对非浏览器 UA 会返回无关内容）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

#: 通用浏览器请求头（Bing/DDG 缺失这些时会返回「200 但内容无关」）
BROWSER_HEADERS: Dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


def get_text(url: str, *, headers: Optional[Dict[str, str]] = None,
             timeout: int = 10) -> str:
    """抓取网页 HTML 文本；失败抛 :class:`ProviderError`。

    失败一律带上状态码/访问状态，便于上层把「被反爬」与「网络不通」区分开。
    """
    try:
        try:
            from intelligence.fetcher import HTTPFetcher
        except ImportError:                        # pragma: no cover
            from tools.intelligence.fetcher import HTTPFetcher  # type: ignore

        merged = dict(BROWSER_HEADERS)
        if headers:
            merged.update(headers)
        res = HTTPFetcher(timeout=timeout).fetch(url, extra_headers=merged)
    except Exception as exc:
        raise ProviderError(f"请求失败: {exc}") from exc

    if not getattr(res, "is_valid", False):
        status = getattr(res, "status_code", 0)
        access = getattr(res, "access_status", "")
        raise ProviderError(f"抓取失败（HTTP {status}{'/' + access if access else ''}）")
    return res.content or ""


def clean_text(raw_text: str) -> str:
    """去标签/实体/控制字符，得到单行可读文本。

    最后一步清洗窄空格与零宽字符：它们会让 GBK 控制台输出直接崩。
    """
    if not raw_text:
        return ""
    text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ",
                  raw_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[\u2000-\u200f\u202f\u205f\u3000\ufeff]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_ddg_url(raw_url: str) -> str:
    """还原 DuckDuckGo 的 `//duckduckgo.com/l/?uddg=...` 跳转链接。"""
    if not raw_url:
        return ""
    if "uddg=" in raw_url:
        try:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
            if params.get("uddg"):
                return params["uddg"][0]
        except Exception:                          # pragma: no cover
            return raw_url
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


def clean_bing_url(raw_url: str) -> str:
    """还原 Bing 的 `/ck/a?...&u=a1<base64>` 跳转链接。"""
    if not raw_url:
        return ""
    if "/ck/a?" in raw_url and "u=" in raw_url:
        try:
            m = re.search(r"[?&]u=([^&]+)", raw_url)
            if m:
                val = m.group(1)
                if val.startswith("a1"):
                    val = val[2:]
                val += "=" * (-len(val) % 4)
                decoded = base64.b64decode(val).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
        except Exception:                          # pragma: no cover
            return raw_url
    return raw_url


def absolute(url: str, base: str) -> str:
    """把相对链接补成绝对链接（搜狗的 /link?url= 属于此类）。"""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return urllib.parse.urljoin(base, url)


__all__ = [
    "BROWSER_HEADERS",
    "USER_AGENT",
    "absolute",
    "clean_bing_url",
    "clean_ddg_url",
    "clean_text",
    "get_text",
]
