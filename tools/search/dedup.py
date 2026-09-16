# -*- coding: utf-8 -*-
"""
结果去重与 URL 规范化

[为什么需要] 联邦检索 + 多路查询会产生大量「看起来不同、其实是同一页」的结果。
实测一条「南方医科大学085409招生简章」的多路检索里，前 5 条全是研招网同一个页面
`schoolInfo--schId-368409`，只是 query string 不同（`?category=`、`&start=` …）。
不去重的话：
  1. 模型以为拿到了 5 个独立来源，实际上只有 1 个 —— 这会直接放大误判风险；
  2. 浪费上下文与后续抓取。

规范化的取舍：**只丢弃"不影响指向"的差异**（跟踪参数、大小写、fragment、结尾斜杠），
**保留会改变内容的参数**（如 `id=`、`schId-`、`page=`、`year=`）。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: 不改变页面指向的跟踪/会话参数（命中即丢弃）
TRACKING_PARAMS: frozenset = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "eqid", "rut", "ntb", "share_token", "sid", "src", "ref",
    "share_source", "timestamp", "_t", "t", "source", "ozs", "ozw",
})


def canonical_url(url: str) -> str:
    """返回 URL 的可比较形式（小写 host、去 www./fragment/跟踪参数、参数排序）。"""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:
        return raw

    # 只用于**比较**，不用于抓取（抓取仍用原始 URL）：同一页面的 http/https 两种
    # 链接算同一条，否则会漏掉这种常见重复。
    scheme = "https"
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return raw

    # 保留非跟踪参数，并按名排序，使 ?a=1&b=2 与 ?b=2&a=1 归为同一条
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if k.lower() not in TRACKING_PARAMS]
    query = urlencode(sorted(pairs), doseq=True)

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"
    return urlunsplit((scheme, netloc, path, query, ""))


def _bigrams(text: str) -> Set[str]:
    """字符二元组集合（对中文标题的相似度判定比单字更稳）。"""
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def title_similarity(a: str, b: str) -> float:
    """标题相似度（Jaccard，0~1）。"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


class Deduplicator:
    """结果去重器：先按规范 URL，再按标题相似度。"""

    def __init__(self, similarity_threshold: float = 0.86):
        self.similarity_threshold = float(similarity_threshold)

    def dedup(self, results: Sequence) -> Tuple[List, int]:
        """返回 ``(保留的结果, 被去掉的数量)``。

        保留规则：**同一组内留下权威度最高的一条**（优先官方来源）。
        """
        kept: List = []
        seen_url: dict = {}                       # canonical_url -> result 在 kept 中的下标
        removed = 0

        for result in results:
            url_key = canonical_url(getattr(result, "url", ""))
            title = getattr(result, "title", "") or ""

            idx = seen_url.get(url_key) if url_key else None
            if idx is None:
                # 再按标题相似度比对（同一篇被不同站点转载、URL 不同的情况）
                idx = self._find_similar(kept, title)

            if idx is None:
                kept.append(result)
                if url_key:
                    seen_url[url_key] = len(kept) - 1
            else:
                removed += 1
                # 保留信息量更大的那条（权威度优先，其次摘要更长）
                if self._better(result, kept[idx]):
                    replaced = kept[idx]
                    kept[idx] = result
                    if url_key:
                        seen_url[url_key] = idx
                    # 旧结果的 URL 也要能从索引里找到（用新结果顶替了它）
                    old_key = canonical_url(getattr(replaced, "url", ""))
                    if old_key and old_key != url_key:
                        seen_url[old_key] = idx

        return kept, removed

    # ── 内部 ────────────────────────────────────────────────

    def _find_similar(self, kept: Sequence, title: str) -> int | None:
        if not title:
            return None
        for i, existing in enumerate(kept):
            if title_similarity(title, getattr(existing, "title", "")) >= self.similarity_threshold:
                return i
        return None

    @staticmethod
    def _better(candidate, current) -> bool:
        """哪一条更值得保留：先看权威度，再看摘要长度。"""
        cand_score = (getattr(candidate, "authority", 0.0), len(getattr(candidate, "snippet", "") or ""))
        curr_score = (getattr(current, "authority", 0.0), len(getattr(current, "snippet", "") or ""))
        return cand_score > curr_score


def dedup_urls(urls: Iterable[str]) -> List[str]:
    """便捷入口：把一批 URL 去重（保持原顺序）。"""
    out: List[str] = []
    seen: set = set()
    for url in urls:
        key = canonical_url(url)
        if key and key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


__all__ = [
    "TRACKING_PARAMS",
    "Deduplicator",
    "canonical_url",
    "dedup_urls",
    "title_similarity",
]
