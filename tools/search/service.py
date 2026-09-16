# -*- coding: utf-8 -*-
"""
检索服务（Search Runtime 的对外唯一入口）

一次检索的完整链路：

    检索词 → provider 联邦召回 → 域名过滤 → 来源归类（权威度）
           → 去重/规范化 → 重排 → 截断 → 带 provenance 的结果

与「把搜索结果直接丢给大模型」的三点区别：
  1. **Search 只召回**，正文要用 :meth:`fetch` 单独取（避免上下文被长文塞满）；
  2. 每条结果都带来源类型与权威分，且**失败信息一并返回**
     （这样「没找到」能区分成「资料不存在」还是「我们没搜到」）；
  3. 去重与重排发生在**送进模型之前**，而不是靠模型自己忽略重复。

provider 的失败被收集而非上抛：一个源被反爬挡住了，不该让整次检索失败。
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple, Union

from . import relevance, source_registry
from .models import (
    Document,
    SearchQuery,
    SearchResponse,
    SearchResult,
    domain_of,
)
from .providers import ProviderError, SearchProvider, available_providers

_LOG = logging.getLogger(__name__)

#: 单条查询的默认条数
DEFAULT_LIMIT = 10

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _domain_matches(host: str, pattern: str) -> bool:
    """判断 host 是否属于 pattern（含子域），用于 site: 语义。"""
    host = (host or "").lower().strip().lstrip(".")
    pattern = (pattern or "").lower().strip().lstrip(".")
    if not host or not pattern:
        return False
    return host == pattern or host.endswith("." + pattern)


class SearchService:
    """联邦检索服务。"""

    def __init__(
        self,
        providers: Optional[Sequence[SearchProvider]] = None,
        fetcher: Any = None,
        deduplicator: Any = None,
        ranker: Any = None,
        classifier: Any = None,
    ):
        self._providers = list(providers) if providers is not None else None
        self._fetcher = fetcher
        self._dedup = deduplicator
        self._ranker = ranker
        self._classify = classifier or source_registry.classify

    # ── 构造 ────────────────────────────────────────────────

    @classmethod
    def default(cls, providers: Optional[Sequence[SearchProvider]] = None) -> "SearchService":
        """装配默认实现（去重与重排模块缺失时自动降级，不影响检索本身）。"""
        dedup = ranker = None
        try:
            from . import dedup as _dedup_mod
            dedup = _dedup_mod.Deduplicator()
        except Exception as exc:                  # pragma: no cover - 尚未实现时
            _LOG.debug("去重模块不可用，跳过去重: %s", exc)
        try:
            from . import rank as _rank_mod
            ranker = _rank_mod.Ranker()
        except Exception as exc:                  # pragma: no cover
            _LOG.debug("重排模块不可用，保持原序: %s", exc)
        return cls(providers=providers, deduplicator=dedup, ranker=ranker)

    # ── 检索 ────────────────────────────────────────────────

    def providers(self, names: Sequence[str] = ()) -> List[SearchProvider]:
        """按优先级返回待查询的 provider（小的先查）。"""
        if self._providers is not None:
            if not names:
                chosen = list(self._providers)
            else:
                wanted = {str(n).lower() for n in names}
                chosen = [p for p in self._providers if p.name.lower() in wanted]
        else:
            chosen = available_providers(names)
        return sorted(chosen, key=lambda p: getattr(p, "priority", 100))

    def search(self, query: Union[str, SearchQuery],
               limit: Optional[int] = None) -> SearchResponse:
        """执行一次联邦检索。"""
        q = SearchQuery(text=str(query)) if isinstance(query, str) else query
        if limit is not None:
            q = SearchQuery(text=q.text, limit=int(limit), domains=q.domains,
                            exclude_domains=q.exclude_domains, year=q.year,
                            time_range=q.time_range, providers=q.providers)

        active = self.providers(q.providers)
        if not active:
            return SearchResponse(
                query=q.text,
                providers_failed=(("*", "没有可用的检索源（未安装可选依赖或被网络策略拦截）"),),
                year=q.year,
            )

        collected: List[SearchResult] = []
        used: List[str] = []
        failed: List[Tuple[str, str]] = []

        for provider in active:
            try:
                raw = provider.search(q.text, limit=max(q.limit, DEFAULT_LIMIT),
                                      time_range=q.time_range)
            except ProviderError as exc:
                failed.append((provider.name, str(exc)))
                continue
            except Exception as exc:              # pragma: no cover - 实现内部错误
                failed.append((provider.name, f"未预期异常: {exc}"))
                continue

            # [防「200 但内容是垃圾」] 逐条过相关性守门；全部不相关时按失败处理。
            # 实测 Bing 对裸 urllib 请求会返回 200 + 完全无关的内容（软性反爬），
            # 结构正常、正则匹配得到 10 条「结果」——不加这道守门就会被当成素材。
            if raw:
                relevant, dropped = relevance.filter_relevant(raw, q.text)
                if not relevant:
                    failed.append((provider.name,
                                   relevance.anti_bot_reason(len(raw), 0)))
                    _LOG.warning("provider %s 的结果与查询无关，已丢弃 %d 条",
                                 provider.name, len(raw))
                    continue
                if dropped:
                    _LOG.info("provider %s 丢弃 %d 条无关结果", provider.name, dropped)
                raw = relevant

            used.append(provider.name)
            collected.extend(self._annotate(raw))

        candidates = len(collected)
        filtered = [r for r in collected if self._passes_domain_filter(r, q)]

        kept, removed = self._dedup_results(filtered, q)
        ranked = self._rank_results(kept, q)
        results = tuple(ranked[: q.limit])

        return SearchResponse(
            query=q.text, results=results, providers_used=tuple(used),
            providers_failed=tuple(failed), candidates=candidates,
            duplicates=removed, queries_run=1, year=q.year,
        )

    def search_planned(self, text: str, *, limit: int = 10,
                       year: Optional[int] = None, school: str = "",
                       major: str = "", domains: Sequence[str] = (),
                       providers: Sequence[str] = (),
                       max_queries: int = 4) -> SearchResponse:
        """按查询规划做多路检索，再合并去重后返回。

        单条查询的召回取决于运气：实测「南方医科大学 085409 招生」能命中研究生
        招生网，但用户原句「南方医科大学085409今年招多少人」经常只拿到培训机构的
        二手解读。多路的意义在于覆盖**不同来源类别**（官方站内 / 研招网 / 简章 /
        专业目录 / 资料），而不是把同一句话换几种说法。

        :param max_queries: 查询路数上限。每路都会真的发请求，默认 4 路。
        """
        from .rewrite import plan_queries

        plan = plan_queries(text, year=year, school=school, major=major,
                            limit=max(2, int(max_queries)), domains=domains,
                            providers=providers)
        responses = [self.search(q) for q in plan.queries]

        from .models import merge_responses

        merged = merge_responses(responses, query=text)
        # 跨查询去重：多路之间必然有重复（同一篇简章可能被多条查询命中）
        base_query = SearchQuery(text=text, limit=limit, year=year or None)
        kept, removed = self._dedup_results(merged.results, base_query)
        ranked = self._rank_results(kept, base_query)
        return replace(merged, results=tuple(ranked[:limit]),
                       duplicates=merged.duplicates + removed,
                       year=year or merged.year)

    # ── 抓取（证据） ────────────────────────────────────────

    def fetch(self, url: str, *, extract_title: bool = True) -> Document:
        """抓取指定 URL 的正文（这是证据，不是线索）。"""
        url = str(url or "").strip()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not url.startswith(("http://", "https://")):
            return Document(url=url, retrieved_at=now,
                            meta={"error": "仅支持 http(s) URL"})

        if url.lower().endswith(".pdf"):
            return self._fetch_pdf(url, now)

        try:
            fetcher = self._get_fetcher()
            res = fetcher.fetch(url)
            if not getattr(res, "is_valid", False):
                return Document(url=url, retrieved_at=now,
                                meta={"error": "抓取失败或页面为空"})
            content = res.content or ""
            title = ""
            if extract_title:
                m = _TITLE_RE.search(content)
                title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
            return Document(url=url, title=title, content=content,
                            content_type="html", retrieved_at=now)
        except Exception as exc:
            _LOG.warning("抓取失败: %s -> %s", url, exc)
            return Document(url=url, retrieved_at=now, meta={"error": str(exc)})

    def inspect(self, url: str, pattern: str, *, context: int = 80,
                limit: int = 8) -> List[str]:
        """打开网页并找出与 `pattern` 相关的片段。

        比「把整页正文丢给模型」更省上下文，也更不容易让页面里的无关内容
        干扰判断 —— 考研官网动辄几万字，真正有用的往往就那几行。
        """
        doc = self.fetch(url)
        if not doc.ok:
            return []
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return []
        hits: List[str] = []
        for line in re.split(r"[\r\n。；;]+", doc.content):
            text = re.sub(r"\s+", " ", line).strip()
            if not text:
                continue
            m = rx.search(text)
            if not m:
                continue
            start = max(0, m.start() - context)
            end = min(len(text), m.end() + context)
            hits.append(text[start:end])
            if len(hits) >= limit:
                break
        return hits

    # ── 内部 ────────────────────────────────────────────────

    def _get_fetcher(self):
        if self._fetcher is not None:
            return self._fetcher
        try:
            from intelligence.fetcher import HTTPFetcher
        except ImportError:                        # pragma: no cover
            from tools.intelligence.fetcher import HTTPFetcher  # type: ignore
        self._fetcher = HTTPFetcher()
        return self._fetcher

    def _annotate(self, results: Sequence[SearchResult]) -> List[SearchResult]:
        """补齐来源类型与权威分（provider 可以不填，但上层必须拿得到）。"""
        out: List[SearchResult] = []
        for r in results:
            stype, authority = self._classify(r.domain or r.url)
            if r.source_type in ("", "unknown") or r.authority <= 0:
                r = SearchResult(
                    title=r.title, url=r.url, snippet=r.snippet, engine=r.engine,
                    domain=r.domain,
                    source_type=r.source_type if r.source_type != "unknown" else stype,
                    published_at=r.published_at,
                    authority=r.authority if r.authority > 0 else authority,
                    score=r.score, extra=dict(r.extra),
                )
            out.append(r)
        return out

    def _passes_domain_filter(self, result: SearchResult, q: SearchQuery) -> bool:
        host = result.domain or domain_of(result.url)
        if q.domains and not any(_domain_matches(host, d) for d in q.domains):
            return False
        if q.exclude_domains and any(_domain_matches(host, d) for d in q.exclude_domains):
            return False
        return True

    def _dedup_results(self, results: Sequence[SearchResult],
                       q: SearchQuery) -> Tuple[List[SearchResult], int]:
        if self._dedup is None:
            return list(results), 0
        try:
            kept, removed = self._dedup.dedup(list(results))
            return list(kept), int(removed)
        except Exception as exc:                   # pragma: no cover
            _LOG.warning("去重失败，按原样返回: %s", exc)
            return list(results), 0

    def _rank_results(self, results: Sequence[SearchResult],
                      q: SearchQuery) -> List[SearchResult]:
        if self._ranker is None:
            return sorted(results, key=lambda r: -r.authority)
        try:
            return list(self._ranker.rank(list(results), q))
        except Exception as exc:                   # pragma: no cover
            _LOG.warning("重排失败，退回按权威度排序: %s", exc)
            return sorted(results, key=lambda r: -r.authority)

    def _fetch_pdf(self, url: str, now: str) -> Document:
        """PDF 走可选抽取器；未安装时如实说明，不假装拿到了正文。"""
        try:
            try:
                from skills import pdf_extractor
            except ImportError:                    # pragma: no cover
                from tools.skills import pdf_extractor  # type: ignore
            text = pdf_extractor.extract_pdf_text(url) if hasattr(
                pdf_extractor, "extract_pdf_text") else ""
            if text:
                return Document(url=url, content=str(text),
                                content_type="pdf", retrieved_at=now)
        except Exception as exc:
            _LOG.info("PDF 抽取不可用: %s -> %s", url, exc)
        return Document(url=url, content_type="pdf", retrieved_at=now,
                        meta={"error": "PDF 正文未抽取（缺 pypdf 或该 PDF 需解密），"
                                       "请将文件放入本地参考资料目录后用 ky ingest 入库"})


def quick_search(text: str, limit: int = 5,
                 domains: Sequence[str] = ()) -> SearchResponse:
    """便捷入口：一行发起检索（供 CLI / Agent 工具调用）。"""
    return SearchService.default().search(
        SearchQuery(text=text, limit=limit, domains=tuple(domains)))


def format_results(response: SearchResponse, *, show_scores: bool = False) -> str:
    """把结果渲染成给模型/用户看的文本（含出处与来源类型）。"""
    if not response.has_results:
        lines = [f"【检索】{response.query}", response.summary_line()]
        for name, why in response.providers_failed:
            lines.append(f"  - 失败源 {name}: {why}")
        lines.append("  未找到结果：可能是资料确实不存在，也可能是检索源未覆盖 —— "
                     "详见上面的失败源列表。")
        return "\n".join(lines)

    out = [f"【检索】{response.query}", response.summary_line(), ""]
    for i, r in enumerate(response.results, 1):
        tag = "官方" if r.is_official else r.source_type
        score = f" score={r.score:.2f}" if show_scores else ""
        out.append(f"[{i}] {r.title}  <{tag}{score}>")
        out.append(f"    {r.url}")
        if r.snippet:
            out.append(f"    {r.snippet[:200]}")
    if response.providers_failed:
        out.append("")
        out.append("未参与的检索源：" + "；".join(
            f"{n}({w})" for n, w in response.providers_failed))
    return "\n".join(out)


__all__ = ["DEFAULT_LIMIT", "SearchService", "format_results", "quick_search"]
