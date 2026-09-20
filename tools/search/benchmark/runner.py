# -*- coding: utf-8 -*-
"""
检索质量评测（Benchmark）

数据集：`tools/search/benchmark/dataset.jsonl`，40 条真实考研问法（含 8 条无空格口语
脏输入，如"华科计算机复试线多少分"），覆盖
简章 / 专业目录 / 初试科目 / 招生人数 / 复试线 / 拟录取 / 报录比 / 真题资料 /
学校分析 / 考纲政策十余类。字段见文件内注释。

**两种模式，别混为一谈：**

* ``offline``（默认，CI 可跑）：用固定夹具结果集验证**流水线机械正确性** ——
  查询规划是否覆盖官方站点、相关性守门是否拦住垃圾、去重是否合并变体、排序是否让
  官方当年结果靠前。这是**确定性回归测试**，不是召回率评测。
* ``live``（需 ``KY_LIVE_TEST=1``）：真正联网检索，计算 Recall@5 / MRR / 官方命中率。
  受引擎限流影响，结果会波动，故默认不跑。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .. import rank as rank_mod
from ..dedup import Deduplicator, canonical_url
from ..models import SearchQuery, SearchResponse, SearchResult
from ..relevance import is_relevant, significant_tokens
from ..source_registry import classify_results
from ..rewrite import plan_queries

HERE = Path(__file__).resolve().parent
DATASET_PATH = HERE / "dataset.jsonl"
FIXTURES_PATH = HERE / "fixtures.json"

#: 评测的截断位置
DEFAULT_K = 5


@dataclass
class BenchmarkResult:
    """评测结果。"""

    mode: str
    total: int = 0
    metrics: Dict[str, float] = field(default_factory=dict)
    per_query: List[Dict[str, Any]] = field(default_factory=list)
    dataset_total: int = 0
    missing_fixtures: List[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{k}={v:.2f}" for k, v in self.metrics.items()]
        return f"[{self.mode}] 实测样本 {self.total}/{self.dataset_total or self.total} · " + " · ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode, "total": self.total, "metrics": self.metrics,
                "per_query": self.per_query, "dataset_total": self.dataset_total,
                "missing_fixtures": self.missing_fixtures}


# ── 数据 ────────────────────────────────────────────────────────

def load_dataset(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读取评测数据集（每行一个 JSON 对象）。"""
    target = Path(path) if path else DATASET_PATH
    rows: List[Dict[str, Any]] = []
    with target.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_fixtures(path: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    """读取离线模式的固定结果集：``{查询: [结果 dict, ...]}``。"""
    target = Path(path) if path else FIXTURES_PATH
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _domain_ok(url: str, domains: Sequence[str]) -> bool:
    host = canonical_url(url)
    return any(d in host for d in domains or ())


def _terms_ok(result: SearchResult, terms: Sequence[str]) -> bool:
    hay = f"{result.title} {result.snippet} {result.url}".lower()
    return any(str(t).lower() in hay for t in terms or ())


# ── 离线模式：流水线机械正确性 ──────────────────────────────────

def evaluate_offline(dataset: Optional[List[Dict[str, Any]]] = None,
                    fixtures: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                    k: int = DEFAULT_K) -> BenchmarkResult:
    """离线评测：对每个有夹具的查询，跑「规划 → 守门 → 去重 → 排序」并打分。

    统计四项：
      * ``plan_site_coverage``：规划出的查询里，有多少比例包含一条 **官方站点** 的
        `site:` 查询（衡量查询改写是否真的用上了院校域名）。
      * ``topk_relevant``：Top-K 里「与查询相关」的结果占比。
      * ``topk_official_first``：Top-K 首位是官方来源的比例（事实类需求）。
      * ``dedup_effectiveness``：被合并掉的重复结果占比。
    """
    rows = dataset if dataset is not None else load_dataset()
    fixture_map = fixtures if fixtures is not None else load_fixtures()

    result = BenchmarkResult(mode="offline", dataset_total=len(rows))
    plan_hits = dedup_total = dedup_removed = 0
    topk_relevant = topk_official_first = fact_cases = 0

    for row in rows:
        query_text = row["query"]
        if query_text not in fixture_map:
            result.missing_fixtures.append(query_text)
            continue
        result.total += 1
        domains = row.get("acceptable_domains") or []
        terms = row.get("must_terms") or []
        year = row.get("year")
        intent = row.get("intent") or "general"

        # 1) 查询规划是否覆盖官方站点
        plan = plan_queries(query_text, year=year)
        if any(q.text.startswith("site:") and _site_is_official(q.text, domains)
               for q in plan.queries):
            plan_hits += 1

        # 2) 守门 + 去重 + 排序
        tokens = significant_tokens(query_text)
        raw = [SearchResult.from_raw(item, engine="fixture") for item in fixture_map[query_text]]
        # 夹具结果也要过来源归类，否则 is_official 恒为假、排序指标无意义
        guarded = [r for r in classify_results(raw)
                   if is_relevant(r.title, r.snippet, r.url, tokens)]
        kept, removed = Deduplicator().dedup(guarded)
        dedup_total += len(guarded)
        dedup_removed += removed
        ranked = rank_mod.Ranker().rank(kept, SearchQuery(text=query_text, year=year or None))

        top = ranked[:k]
        if intent == "fact":
            fact_cases += 1
        if top:
            good = sum(1 for r in top if _terms_ok(r, terms))
            topk_relevant += good / len(top)
            if intent == "fact":
                if top[0].is_official:
                    topk_official_first += 1

        result.per_query.append({
            "query": query_text, "plan_queries": len(plan.queries),
            "raw": len(raw), "after_guard": len(guarded), "after_dedup": len(kept),
            "top1_official": bool(top and top[0].is_official),
        })

    n = max(1, result.total)
    result.metrics = {
        "plan_site_coverage": plan_hits / n,
        "topk_relevant": topk_relevant / n,
        "official_first": (topk_official_first / max(1, fact_cases)) if fact_cases else topk_official_first / n,
        "dedup_effectiveness": (dedup_removed / dedup_total) if dedup_total else 0.0,
        "fixture_coverage": result.total / max(1, len(rows)),
    }
    return result


def _site_is_official(query_text: str, domains: Sequence[str]) -> bool:
    return any(d in query_text for d in domains or ())


# ── 联网模式：召回指标 ──────────────────────────────────────────

def evaluate_live(service: Any, dataset: Optional[List[Dict[str, Any]]] = None,
                  k: int = DEFAULT_K,
                  search_fn: Optional[Callable[..., SearchResponse]] = None,
                  max_queries: int = 3) -> BenchmarkResult:
    """联网评测：Recall@{k}、MRR、官方命中率。

    :param search_fn: 可注入检索函数（便于测试）；不传则用 ``service.search_planned``。
    """
    rows = dataset if dataset is not None else load_dataset()
    runner = search_fn or (lambda q, year: service.search_planned(
        q, limit=k, max_queries=max_queries, year=year))

    result = BenchmarkResult(mode="live")
    recall_sum = mrr_sum = official_hits = 0.0

    for row in rows:
        query_text = row["query"]
        domains = row.get("acceptable_domains") or []
        terms = row.get("must_terms") or []
        year = row.get("year")
        try:
            resp = runner(query_text, year)
        except Exception as exc:                    # pragma: no cover
            result.per_query.append({"query": query_text, "error": str(exc)})
            result.total += 1
            continue

        result.total += 1
        hits = 0
        first_rank = 0
        for i, r in enumerate(resp.results[:k], 1):
            scored_ok = _domain_ok(r.url, domains) and _terms_ok(r, terms)
            if scored_ok and not first_rank:
                first_rank = i
            if scored_ok:
                hits += 1
        recall_sum += hits / min(k, max(1, len(resp.results) or k))
        mrr_sum += (1.0 / first_rank) if first_rank else 0.0
        if any(r.is_official for r in resp.results[:k]):
            official_hits += 1
        result.per_query.append({"query": query_text, "hits": hits,
                                 "first_rank": first_rank or None})

    n = max(1, result.total)
    result.metrics = {
        f"recall_at_{k}": recall_sum / n,
        "mrr": mrr_sum / n,
        "official_hit_rate": official_hits / n,
    }
    return result


__all__ = [
    "BenchmarkResult",
    "DATASET_PATH",
    "DEFAULT_K",
    "FIXTURES_PATH",
    "evaluate_live",
    "evaluate_offline",
    "load_dataset",
    "load_fixtures",
]
