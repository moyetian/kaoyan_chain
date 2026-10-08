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

from . import dedup, health, relevance, source_registry
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


def _stale_note(result: SearchResult) -> str:
    """[serve-stale] 该结果是否为陈旧缓存兜底；是则给出人类可读的标注。"""
    extra = result.extra or {}
    if not extra.get("stale"):
        return ""
    age = int(extra.get("stale_age_seconds") or 0)
    if age >= 3600:
        return f"陈旧缓存（{age // 3600} 小时前）"
    if age >= 60:
        return f"陈旧缓存（{age // 60} 分钟前）"
    return f"陈旧缓存（{age} 秒前）"


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
        self._cache = None                        # 由 default() 装配（见 _cache_get/_cache_put）
        self._classify = classifier or source_registry.classify

    # ── 构造 ────────────────────────────────────────────────

    @classmethod
    def default(cls, providers: Optional[Sequence[SearchProvider]] = None) -> "SearchService":
        """装配默认实现（去重与重排模块缺失时自动降级，不影响检索本身）。"""
        # 注意局部变量名不要与模块名同名：这里 dedup 是模块，装配结果另用 deduplicator
        deduplicator = None
        ranker = None
        try:
            deduplicator = dedup.Deduplicator()
        except Exception as exc:                  # pragma: no cover
            _LOG.debug("去重模块不可用，跳过去重: %s", exc)

        # 缓存：实测 DDG 限流后，多路检索会成倍放大请求数，缓存是必需项而非优化
        search_cache = None
        try:
            from .cache import SearchCache
            search_cache = SearchCache()
        except Exception as exc:                  # pragma: no cover
            _LOG.debug("检索缓存不可用，本次不缓存: %s", exc)
        try:
            from .rank import Ranker
            ranker = Ranker()
        except Exception as exc:                  # pragma: no cover
            _LOG.debug("重排模块不可用，保持原序: %s", exc)
            ranker = None
        svc = cls(providers=providers, deduplicator=deduplicator, ranker=ranker)
        svc._cache = search_cache
        return svc

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
        # 冷却中的源不再请求（避免把限流越试越严，也避免每轮都产生同样的失败噪音）
        return sorted([p for p in chosen if not health.is_cooling(p.name)],
                      key=lambda p: getattr(p, "priority", 100))

    def cooling_down(self, names: Sequence[str] = ()) -> List[Tuple[str, str]]:
        """返回当前因冷却而跳过的源及其原因。"""
        candidates = self._providers or available_providers(names)
        out: List[Tuple[str, str]] = []
        for provider in candidates:
            if health.is_cooling(provider.name):
                out.append((provider.name, health.cooldown_reason(provider.name)))
        return out

    def search(self, query: Union[str, SearchQuery],
               limit: Optional[int] = None) -> SearchResponse:
        """执行一次联邦检索。"""
        q = SearchQuery(text=str(query)) if isinstance(query, str) else query
        if limit is not None:
            q = SearchQuery(text=q.text, limit=int(limit), domains=q.domains,
                            exclude_domains=q.exclude_domains, year=q.year,
                            time_range=q.time_range, providers=q.providers)

        # 冷却中的源：先算出来（即便它是唯一的源，也要如实报「冷却中」而不是
        # 泛泛地说「没有可用源」—— 两者的处置完全不同：前者等一会儿就好）。
        cooling = self.cooling_down(q.providers)

        active = self.providers(q.providers)
        if not active:
            reasons = cooling or [("*", "没有可用的检索源（未安装可选依赖或被网络策略拦截）")]
            # [serve-stale] 全源冷却 → 先用过期缓存兜底，别让检索能力直接归零。
            # 冷却事实仍如实上报（冷却没解除），但把陈旧结果交给上层并标注陈旧度。
            stale = self._stale_for([n for n, _ in cooling], q)
            if stale:
                return SearchResponse(
                    query=q.text, results=tuple(stale),
                    providers_used=tuple(f"{r.engine or '*'}(缓存·陈旧)" for r in stale),
                    providers_failed=tuple(reasons), providers_cooling=tuple(cooling),
                    candidates=len(stale), queries_run=0, year=q.year)
            return SearchResponse(query=q.text, providers_failed=tuple(reasons),
                                  providers_cooling=tuple(cooling), year=q.year)

        collected: List[SearchResult] = []
        used: List[str] = []
        failed: List[Tuple[str, str]] = []

        failed.extend(cooling)

        for provider in active:
            want = max(q.limit, DEFAULT_LIMIT)
            cached = self._cache_get(provider, q)
            if cached is not None:
                used.append(f"{provider.name}(缓存)")
                collected.extend(self._annotate(cached))
                continue

            # [P1 修复·2026-10-08 S2] 即将真正发请求 → 消费半开探测名额。
            # 此前 try_half_open 全仓零调用（死代码）：冷却期满 is_cooling 返回
            # False，过滤链全量放行（惊群）。现在冷却期满只放行一次探测，探测
            # 结果（成功清零 / 失败续冷）在同一轮内闭环。放在缓存检查**之后**：
            # 缓存命中不需要探测，不该浪费名额。
            if not health.try_half_open(provider.name):
                failed.append((provider.name,
                               health.cooldown_reason(provider.name)
                               or "冷却中（暂不放行，稍后自动恢复）"))
                continue

            try:
                raw = provider.search(q.text, limit=want, time_range=q.time_range)
            except ProviderError as exc:
                failed.append((provider.name, str(exc)))
                self._maybe_cooldown(provider, str(exc))
                # [serve-stale] 该源本轮失败（多半是被反爬）→ 用过期缓存兜底。
                # 失败事实照实留在 providers_failed 里，只是额外给出可用的旧结果。
                stale = self._stale_for([provider.name], q)
                if stale:
                    used.append(f"{provider.name}(缓存·陈旧)")
                    collected.extend(stale)
                continue
            except Exception as exc:              # pragma: no cover - 实现内部错误
                failed.append((provider.name, f"未预期异常: {exc}"))
                # [P1 修复·2026-10-08 S2] 未预期异常也必须落一次失败账：半开探测
                # 在途时名额已被消费，若这里不落账，half_open 标志会悬空——源
                # 既不在冷却也不再被放行（静默死锁）。note_failure 在半开在途时
                # 强制重新冷却（见 CooldownPolicy.record_failure）。
                health.note_failure(provider.name, f"未预期异常: {exc}")
                continue

            # [P1 修复·2026-10-08 S1] 守门**之前**不再无条件 mark_healthy：
            # 旧顺序「先标记健康、再发现全是垃圾」会把软性反爬的失败计数每轮
            # 清零，阈值冷却永远攒不够；且半开探测若拿到 200+垃圾，旧顺序会
            # 直接清掉冷却——探测等于白放。现在只有「守门通过」或「真实无
            # 结果（raw 为空）」才算本轮健康。
            #
            # [防「200 但内容是垃圾」] 逐条过相关性守门；全部不相关时按失败处理。
            # 实测 Bing 对裸 urllib 请求会返回 200 + 完全无关的内容（软性反爬），
            # 结构正常、正则匹配得到 10 条「结果」——不加这道守门就会被当成素材。
            if raw:
                relevant, dropped = relevance.filter_relevant(raw, q.text)
                if not relevant:
                    reason = relevance.anti_bot_reason(len(raw), 0)
                    failed.append((provider.name, reason))
                    # [P1 修复·2026-10-08 S1] 软性反爬（200+全垃圾）丢弃后必须
                    # 落账：此前该分支不触发任何冷却，Bing 每轮白试（对照
                    # ProviderError 路径有冷却）。选择阈值型 note_failure 而非
                    # mark_blocked：全垃圾是启发式判定（冷门查询的兜底推荐、
                    # 页面 A/B 都可能命中），一次误判不该让源停摆 10 分钟；
                    # 连续两轮全垃圾才冷却，与 R3「抵消单次抖动」口径一致。
                    health.note_failure(provider.name, reason)
                    # [doctor 静噪] 全垃圾是反爬常态且已被失败原因记录，不再用
                    # warning 刷屏（此前 ky doctor 每次联网检查都打印"丢弃 N 条"）。
                    _LOG.info("provider %s 的结果与查询无关，已丢弃 %d 条",
                              provider.name, len(raw))
                    stale = self._stale_for([provider.name], q)
                    if stale:
                        used.append(f"{provider.name}(缓存·陈旧)")
                        collected.extend(stale)
                    continue
                if dropped:
                    _LOG.info("provider %s 丢弃 %d 条无关结果", provider.name, dropped)
                raw = relevant

            # 本轮健康（守门通过或真实无结果）→ 清零失败计数
            # （半开探测成功后必须做，否则冷却永不解除）
            health.mark_healthy(provider.name)

            # 只缓存**守门通过**的结果：把反爬垃圾写进缓存会被后续请求原样重放
            self._cache_put(provider, q, raw)
            used.append(provider.name)
            collected.extend(self._annotate(raw))

        candidates = len(collected)
        filtered = [r for r in collected if self._passes_domain_filter(r, q)]

        kept, removed = self._dedup_results(filtered, q)
        ranked = self._rank_results(kept, q)
        results = tuple(ranked[: q.limit])

        return SearchResponse(
            query=q.text, results=results, providers_used=tuple(used),
            providers_failed=tuple(failed), providers_cooling=tuple(cooling),
            candidates=candidates,
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

    def _cache_get(self, provider: SearchProvider, q: SearchQuery, *,
                   allow_stale: bool = False) -> Optional[List[SearchResult]]:
        """读该provider 的缓存；``allow_stale=True`` 时允许返回过期条目（带标注）。"""
        if self._cache is None:
            return None
        try:
            return self._cache.get(provider.name, q.text, max(q.limit, DEFAULT_LIMIT),
                                   q.time_range, allow_stale=allow_stale)
        except TypeError:
            # 旧缓存实现无 ``allow_stale`` 形参（双导入/旧副本场景）→ 退回严格语义。
            return self._cache.get(provider.name, q.text, max(q.limit, DEFAULT_LIMIT),
                                   q.time_range)
        except Exception as exc:                  # pragma: no cover
            _LOG.debug("缓存读取失败（按未命中处理）: %s", exc)
            return None

    def _stale_for(self, names: Sequence[str], q: SearchQuery) -> List[SearchResult]:
        """[serve-stale] 给指定的若干源用过期缓存兜底，返回标注了陈旧度的结果。

        [为什么] R3 全矩阵仿真实测到「全源冷却 → 检索能力归零 → 模型空转」：
        早跑的 CLI 抓了 5 篇，10 分钟内后跑的 GUI 却抓 0 篇。招生简章 / 复试线
        在冷却的这 10 分钟里并**不会变化**，拿一份标了陈旧度的旧结果，远好过
        告诉用户「未找到结果」（后者会让模型在死路上反复重试）。

        每条结果带 ``extra["stale"]=True`` 与 ``extra["stale_age_seconds"]``，
        上层据此在输出里如实标注陈旧度。
        """
        if self._cache is None:
            return []
        out: List[SearchResult] = []
        for name in names:
            provider = self._by_name(name)
            if provider is None:
                continue
            cached = self._cache_get(provider, q, allow_stale=True)
            if cached:
                _LOG.info("源 %s 处于不可用状态，改用陈旧缓存兜底 %d 条（最陈旧 %ds）",
                          name, len(cached),
                          max(int((r.extra or {}).get("stale_age_seconds") or 0)
                              for r in cached))
                out.extend(self._annotate(cached))
        return out

    def _by_name(self, name: str) -> Optional[SearchProvider]:
        candidates = self._providers or available_providers(())
        for p in candidates:
            if p.name.lower() == str(name or "").lower():
                return p
        return None

    def _cache_put(self, provider: SearchProvider, q: SearchQuery,
                   results: Sequence[SearchResult]) -> None:
        if self._cache is None:
            return
        try:
            self._cache.put(provider.name, q.text, max(q.limit, DEFAULT_LIMIT),
                            results, q.time_range)
        except Exception as exc:                  # pragma: no cover
            _LOG.debug("缓存写入失败（忽略）: %s", exc)

    def _get_fetcher(self):
        if self._fetcher is not None:
            return self._fetcher
        try:
            from intelligence.fetcher import HTTPFetcher
        except ImportError:                        # pragma: no cover
            from tools.intelligence.fetcher import HTTPFetcher  # type: ignore
        self._fetcher = HTTPFetcher()
        return self._fetcher

    # [P2-8 修复·重复定义] 此处原先还有一组 _maybe_cooldown / _annotate 定义，
    # 与下面那组同名 —— Python 后定义覆盖先定义，于是上面那版
    # （_annotate 走 source_registry.classify_results）**从未生效**，
    # 属纯死代码，且让「改了上面那版却没生效」成为下次踩坑的陷阱。
    # 现只保留下面这一组（也是实际一直生效的那组）。
    @staticmethod
    def _maybe_cooldown(provider: SearchProvider, reason: str) -> None:
        """按失败原因分流处置：反爬/限流立即冷却；改版/疑似抖动走阈值型。

        [P1 修复·2026-10-08 S4] 此前凡命中标记词的原因一律 mark_blocked
        （立即冷却）——「页面结构未匹配（可能改版）」这类确定性解析故障被反复
        当反爬惩罚：force_cooldown 每次让 fails+1，冷却按指数增长却永远修不好
        页面。现分三档：
          * 改版/结构类文案 → note_failure（阈值型，不立即冷却）。**先判**：
            这类文案常同时含「限流/改版」字样，「结构未匹配」是对页面形态的
            确定性观察，优先级高于对「可能被限流」的猜测；
          * 反爬/验证/限流类 → mark_blocked（确定性判定，首次即冷却 ≥600s）；
          * 其余（网络超时等疑似抖动）→ note_failure（连续两次才冷却）——
            这正是 health.note_failure 预留的语义（原搜索侧从未调用，普通
            超时既不冷却也不落账，半开探测失败时会悬空名额）。
        """
        lowered = str(reason or "").lower()
        if any(marker in lowered for marker in ("改版", "结构未匹配", "结构不匹配")):
            health.note_failure(provider.name, reason)
            return
        if any(marker in lowered for marker in ("反爬", "验证", "captcha", "anomaly",
                                                "频繁", "限流", "blocked")):
            health.mark_blocked(provider.name, reason)
            return
        health.note_failure(provider.name, reason)

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
            # 不静默：直连 SearchService() 会跳过 default() 的装配，去重/重排/缓存
            # 一起失效。这里必须留痕，否则调用方只看到「结果变多」而无从归因。
            _LOG.warning("去重未启用：SearchService 未装配 Deduplicator"
                         "（请用 SearchService.default() 构造）")
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
        # [W11 全源冷却] 检索能力暂时归零 ≠ 资料不存在：给出专属降级话术，
        # 明确「勿立即重试」并指出去哪（工作区资料），避免模型在死路上烧步数。
        if response.all_failed_cooling:
            lines.append("  全部检索源暂处于反爬冷却期（暂时不可用，稍后自动恢复）——"
                         "这不代表资料不存在；请勿立即重试同一查询，"
                         "可先改用工作区资料或稍后再试。")
        else:
            lines.append("  未找到结果：可能是资料确实不存在，也可能是检索源未覆盖 —— "
                         "详见上面的失败源列表。")
        return "\n".join(lines)

    out = [f"【检索】{response.query}", response.summary_line(), ""]
    for i, r in enumerate(response.results, 1):
        tag = "官方" if r.is_official else r.source_type
        score = f" score={r.score:.2f}" if show_scores else ""
        # [serve-stale] 陈旧结果必须**在输出里说清**，否则模型会把10 分钟前的
        # 简章当成刚发布的口径去引用。
        stale = _stale_note(r)
        if stale:
            tag = f"{tag}·{stale}"
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
