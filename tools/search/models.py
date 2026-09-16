# -*- coding: utf-8 -*-
"""
搜索领域模型

设计要点（也是与「搜索结果直接丢给大模型」做法的区别）：
  * **Search 只负责召回，不返回全文** —— 结果是线索，证据要靠 fetch 取回原文；
    这样上下文不会被几篇长文塞满，也逼着调用方区分「搜到了」与「读到了」。
  * 每条结果带 **provenance**（engine / domain / source_type / published_at），
    否则「某公众号说招生 58 人」会被当成事实，而不是一条待核验的说法。
  * `SearchResponse` 同时记录**失败**（哪些 provider 挂了、为什么），
    这样「没找到」能区分成「资料不存在」还是「我们没搜到」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

#: 来源类型（由 source_registry 判定；此处给出取值域）
SOURCE_TYPES: Tuple[str, ...] = (
    "national_official",     # 研招网等国家级官方
    "graduate_school",       # 高校研究生院
    "university_official",   # 高校官网/二级学院
    "official_document",     # 官方 PDF（专业目录、名单）
    "wechat",                # 公众号
    "community",             # 知乎 / 论坛
    "video",                 # B 站等
    "aggregator",            # 培训机构 / 转载站
    "unknown",
)


def domain_of(url: str) -> str:
    """取域名的可比较形式（小写、去 www. 前缀）；失败返回空串。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


@dataclass(frozen=True)
class SearchResult:
    """一条检索结果（线索，不是证据）。"""

    title: str
    url: str
    snippet: str = ""
    engine: str = ""                       # 产出它的 provider 名
    domain: str = ""                       # 归一化域名（小写、去 www.）
    source_type: str = "unknown"           # 见 SOURCE_TYPES
    published_at: str = ""                 # ISO 日期或空串（未知不要编）
    authority: float = 0.0                 # 0~1，由 source_registry 填
    score: float = 0.0                     # 0~1，由 rank 填
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # domain 允许调用方不填；这里补齐，避免各处重复计算
        if not self.domain and self.url:
            object.__setattr__(self, "domain", domain_of(self.url))

    @classmethod
    def from_raw(cls, raw: Dict[str, Any], engine: str = "") -> "SearchResult":
        """把 provider 的原始 dict 归一成模型（键名容错）。"""
        url = str(raw.get("url") or raw.get("link") or raw.get("href") or "").strip()
        return cls(
            title=str(raw.get("title") or raw.get("name") or "").strip(),
            url=url,
            snippet=str(raw.get("snippet") or raw.get("desc")
                        or raw.get("summary") or "").strip(),
            engine=str(raw.get("engine") or engine or ""),
            domain=domain_of(url),
            source_type=str(raw.get("source_type") or "unknown"),
            published_at=str(raw.get("published_at") or raw.get("date") or ""),
            extra={k: v for k, v in raw.items()
                   if k not in ("title", "name", "url", "link", "href",
                                "snippet", "desc", "summary", "engine",
                                "source_type", "published_at", "date")},
        )

    @property
    def is_official(self) -> bool:
        """是否属于官方来源（判定只看来源类型，不看内容写得好不好）。"""
        return self.source_type in ("national_official", "graduate_school",
                                    "university_official", "official_document")

    def citation(self) -> str:
        """供回答里引用的简短出处文本。"""
        label = self.title or self.url
        return f"[{label}]({self.url})" if self.url else label

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title, "url": self.url, "snippet": self.snippet,
            "engine": self.engine, "domain": self.domain,
            "source_type": self.source_type, "published_at": self.published_at,
            "authority": round(self.authority, 3), "score": round(self.score, 3),
            "is_official": self.is_official,
        }


@dataclass(frozen=True)
class Document:
    """抓回来的正文（这才是证据）。"""

    url: str
    title: str = ""
    content: str = ""
    content_type: str = "html"             # html / pdf / docx / image / text
    links: Tuple[str, ...] = ()
    retrieved_at: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.content.strip())

    @property
    def length(self) -> int:
        return len(self.content or "")

    def excerpt(self, limit: int = 400) -> str:
        text = (self.content or "").strip().replace("\n\n", "\n")
        return text[:limit] + ("…" if len(text) > limit else "")

    def to_dict(self) -> Dict[str, Any]:
        return {"url": self.url, "title": self.title, "content_type": self.content_type,
                "length": self.length, "retrieved_at": self.retrieved_at,
                "links": list(self.links)}


@dataclass(frozen=True)
class SearchQuery:
    """一次检索请求。"""

    text: str
    limit: int = 10
    domains: Tuple[str, ...] = ()           # 限定站点（site: 语义）
    exclude_domains: Tuple[str, ...] = ()
    year: Optional[int] = None              # 年份锁：只认这一年的数据
    time_range: Optional[str] = None        # year / month / week
    providers: Tuple[str, ...] = ()         # 空 = 用当前全部可用 provider

    def with_text(self, text: str) -> "SearchQuery":
        """派生一条同参数、不同关键词的查询（供查询改写批量展开）。"""
        return SearchQuery(
            text=text, limit=self.limit, domains=self.domains,
            exclude_domains=self.exclude_domains, year=self.year,
            time_range=self.time_range, providers=self.providers,
        )


@dataclass(frozen=True)
class SearchResponse:
    """一次检索的完整结果，含「为什么是这几条」与「哪些路没走通」。"""

    query: str
    results: Tuple[SearchResult, ...] = ()
    providers_used: Tuple[str, ...] = ()
    providers_failed: Tuple[Tuple[str, str], ...] = ()   # (provider, 失败原因)
    candidates: int = 0                    # 去重前候选数
    duplicates: int = 0                    # 被去掉的重复数
    queries_run: int = 1                   # 实际发起的查询数（含改写展开）
    year: Optional[int] = None

    @property
    def has_results(self) -> bool:
        return bool(self.results)

    @property
    def official_results(self) -> Tuple[SearchResult, ...]:
        return tuple(r for r in self.results if r.is_official)

    def top(self, n: int = 5) -> Tuple[SearchResult, ...]:
        return self.results[:n]

    def summary_line(self) -> str:
        """一行解释器输出，回答「为什么只有这几条」。"""
        parts = [f"查询 {self.queries_run} 路",
                 f"provider {len(self.providers_used)} 个"]
        if self.candidates:
            parts.append(f"候选 {self.candidates}")
        if self.duplicates:
            parts.append(f"去重 {self.duplicates}")
        parts.append(f"结果 {len(self.results)}")
        if self.providers_failed:
            failed = "、".join(f"{n}({why})" for n, why in self.providers_failed)
            parts.append(f"失败 {failed}")
        return " · ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "year": self.year,
            "summary": self.summary_line(),
            "providers_used": list(self.providers_used),
            "providers_failed": [{"provider": n, "reason": w}
                                 for n, w in self.providers_failed],
            "candidates": self.candidates,
            "duplicates": self.duplicates,
            "results": [r.to_dict() for r in self.results],
        }


def merge_responses(responses: Sequence[SearchResponse],
                    query: str = "") -> SearchResponse:
    """把多条查询的响应合并成一条（供查询改写展开后汇总）。"""
    results: List[SearchResult] = []
    used: List[str] = []
    failed: List[Tuple[str, str]] = []
    candidates = duplicates = 0
    year: Optional[int] = None
    for resp in responses:
        results.extend(resp.results)
        for name in resp.providers_used:
            if name not in used:
                used.append(name)
        for item in resp.providers_failed:
            if item not in failed:
                failed.append(item)
        candidates += resp.candidates
        duplicates += resp.duplicates
        if resp.year is not None:
            year = resp.year
    return SearchResponse(
        query=query or (responses[0].query if responses else ""),
        results=tuple(results), providers_used=tuple(used),
        providers_failed=tuple(failed), candidates=candidates,
        duplicates=duplicates, queries_run=max(1, len(responses)), year=year,
    )


__all__ = [
    "SOURCE_TYPES",
    "Document",
    "SearchQuery",
    "SearchResponse",
    "SearchResult",
    "domain_of",
    "merge_responses",
]
