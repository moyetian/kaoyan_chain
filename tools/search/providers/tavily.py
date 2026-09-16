# -*- coding: utf-8 -*-
"""
Tavily 在线检索（**可选** Provider，需要 API Key）

存在的意义是证明「Provider 可插拔」不是空话：
新增一个检索源 = 写这个文件 + 在注册表登记，`service.py` 与 Agent 工具**一行都不用改**。

启用方式：设置环境变量 ``TAVILY_API_KEY``（未设置则该源不参与检索，
`SearchResponse.providers_failed` 也不会记它 —— 未启用不等于失败）。

其余在线检索源（Brave / Exa / 百度千帆等）按同样形状各写约 60~90 行即可；
项目默认仍是**零依赖 + 零 Key 可用**（ddg / bing / sogou-weixin）。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..models import SearchResult
from . import register
from .base import ProviderError, SearchProvider

_LOG = logging.getLogger(__name__)

_ENDPOINT = "https://api.tavily.com/search"
_ENV_KEY = "TAVILY_API_KEY"


@register
class TavilyProvider(SearchProvider):
    """Tavily Search API（面向 Agent 的检索服务）。"""

    name = "tavily"
    engine_type = "general"
    requires_key = True
    priority = 40
    description = "Tavily 在线检索（可选，需 TAVILY_API_KEY，设置后自动启用）"

    @classmethod
    def is_available(cls) -> bool:
        return bool(os.environ.get(_ENV_KEY, "").strip())

    def unavailable_reason(self) -> str:
        return f"未设置环境变量 {_ENV_KEY}"

    def search(self, query: str, *, limit: int = 10,
               time_range: Optional[str] = None) -> List[SearchResult]:
        api_key = os.environ.get(_ENV_KEY, "").strip()
        if not api_key:
            raise ProviderError(self.unavailable_reason())

        payload: Dict[str, Any] = {
            "api_key": api_key,
            "query": str(query),
            "max_results": max(1, min(int(limit or 10), 20)),
            "search_depth": "basic",
        }
        if str(time_range or "").lower() == "year":
            payload["days"] = 365

        req = urllib.request.Request(
            _ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "User-Agent": "Kaoyan-Study-Chain/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"Tavily 返回 HTTP {exc.code}（检查 API Key 或额度）") from exc
        except Exception as exc:
            raise ProviderError(f"Tavily 请求失败: {exc}") from exc

        raw_results = data.get("results") or []
        items: List[Dict[str, str]] = []
        for item in raw_results:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            items.append({
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("content") or "")[:400],
                "published_at": str(item.get("published_date") or ""),
            })
        return self.normalize(items)


__all__ = ["TavilyProvider"]
