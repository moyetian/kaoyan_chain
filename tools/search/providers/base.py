# -*- coding: utf-8 -*-
"""
检索 Provider 抽象

所有搜索源（Bing / DuckDuckGo / 搜狗 / 可选在线 API / 本地索引）都实现同一个
接口，调用方只知道「往哪儿搜」，不关心「怎么搜」。

**为什么不用 async**：本项目其余部分是同步栈（urllib、无 asyncio），且要被
同步 REPL、textual 的 worker 线程、Qt 的 QThread 三处调用。引入 asyncio 会污染
四端的事件循环，Qt 侧还要跨线程调度协程 —— 复杂度远大于收益。
需要并发时由调用方把整次检索丢进线程即可。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from ..models import SearchResult

_LOG = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Provider 检索失败（网络不可达、页面结构变化、被反爬拦截等）。

    单独成类型是为了让上层能把它记进 `SearchResponse.providers_failed`，
    而不是把「这个源挂了」和「这个源没结果」混为一谈。
    """


class SearchProvider(ABC):
    """检索源基类。

    子类必须实现 :meth:`search`；建议同时声明 ``name`` 与 ``engine_type``。
    """

    #: 稳定标识（出现在结果 provenance 里，改名会影响缓存的可用性判断）
    name: str = "provider"
    #: 来源类型倾向：official / general / community / resource
    engine_type: str = "general"
    #: 是否需要 API Key（内建零依赖 provider 为 False）
    requires_key: bool = False
    #: 说明文案，供 `ky search --providers` 展示
    description: str = ""

    @abstractmethod
    def search(self, query: str, *, limit: int = 10,
               time_range: Optional[str] = None) -> List[SearchResult]:
        """执行一次检索。

        :param query: 检索词（调用方已做过改写；provider 不再二次加工）
        :param limit: 期望条数上限（provider 可以少给，不要求补足）
        :param time_range: year / month / week，None 表示不限
        :raises ProviderError: 检索失败（上层会记录并继续用其它 provider）
        """

    # ── 可选钩子 ────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> bool:
        """当前环境是否可用（依赖缺失、缺 API Key 时返回 False）。"""
        return True

    def unavailable_reason(self) -> str:
        """不可用原因，供「为什么这个源没参与检索」的解释输出。"""
        return ""

    # ── 工具 ────────────────────────────────────────────────

    def normalize(self, raw_items: Sequence[Dict[str, Any]]) -> List[SearchResult]:
        """把原始 dict 列表归一成模型，并盖上本 provider 的名字。"""
        out: List[SearchResult] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            result = SearchResult.from_raw(raw, engine=self.name)
            if result.url:
                out.append(result)
        return out


class NullProvider(SearchProvider):
    """占位 provider：永远返回空结果。

    用于「刻意不检索」的显式场景（如纯本地知识库模式），
    比给调用方传 None 更不容易写出分支。
    """

    name = "null"
    description = "不检索（占位）"

    def search(self, query: str, *, limit: int = 10,
               time_range: Optional[str] = None) -> List[SearchResult]:
        return []


__all__ = ["NullProvider", "ProviderError", "SearchProvider"]
