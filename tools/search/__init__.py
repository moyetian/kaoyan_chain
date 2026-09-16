# -*- coding: utf-8 -*-
"""
考研学习链 · 检索运行时（Search Runtime）

对外只暴露这里。一次检索 = 多源联邦召回 → 域名过滤 → 来源归类 → 去重规范化
→ 重排 → 带 provenance 的结果；正文另由 ``fetch`` 取回，两者职责分离。

用法::

    from search import SearchService, SearchQuery
    svc = SearchService.default()
    resp = svc.search(SearchQuery(text="南方医科大学 085409 2027 招生", limit=8))
    print(resp.summary_line())         # 查询 1 路 · provider 2 个 · 候选 47 · 结果 8
    doc = svc.fetch(resp.results[0].url)   # 真正读原文

包式导入同样可用：``from tools.search import SearchService``。

新增检索源 = 写一个 `providers.SearchProvider` 子类并在
`providers/__init__.py` 登记，`service.py` 与 Agent 工具无需改动。
"""

from __future__ import annotations

from .models import (
    SOURCE_TYPES,
    Document,
    SearchQuery,
    SearchResponse,
    SearchResult,
    domain_of,
    merge_responses,
)
from .providers import (
    ProviderError,
    SearchProvider,
    available_providers,
    describe_providers,
    provider_names,
    register,
)
from .service import (
    DEFAULT_LIMIT,
    SearchService,
    format_results,
    quick_search,
)
from .source_registry import (
    authority_of,
    classify,
    domains_for_school,
    is_official,
)

__all__ = [
    # 模型
    "SOURCE_TYPES",
    "Document",
    "SearchQuery",
    "SearchResponse",
    "SearchResult",
    "domain_of",
    "merge_responses",
    # provider
    "ProviderError",
    "SearchProvider",
    "available_providers",
    "describe_providers",
    "provider_names",
    "register",
    # 服务
    "DEFAULT_LIMIT",
    "SearchService",
    "format_results",
    "quick_search",
    # 来源权威度
    "authority_of",
    "classify",
    "domains_for_school",
    "is_official",
]
