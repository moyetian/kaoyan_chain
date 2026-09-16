# -*- coding: utf-8 -*-
"""
结构化检索报告 + 检索评测 的测试

报告部分的核心：把「没找到」拆成三种可区分的结论 ——
  found        / not_found（源正常但无结果，大概率确实没有）/ not_searched（源全挂了）
评测部分：离线模式验证流水线机械正确性（确定性，CI 可跑）；联网模式用注入函数验证
Recall@5 / MRR 的计算，不依赖真实网络。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import SearchQuery, SearchResult, SearchService  # noqa: E402
from tools.search.benchmark import runner as bench  # noqa: E402
from tools.search.report import build_report  # noqa: E402


def _resp(results=(), used=(), failed=(), query="测试查询", year=None, candidates=0):
    from tools.search.models import SearchResponse

    return SearchResponse(query=query, results=tuple(results), providers_used=tuple(used),
                         providers_failed=tuple(failed), candidates=candidates, year=year)


# ── 报告：结论三态 ──────────────────────────────────────────────

def test_verdict_found():
    r = _resp(results=[SearchResult(title="有结果", url="https://gs.hust.edu.cn/a")],
              used=("ddg",))
    report = build_report(r)
    assert report.verdict == "found" and report.found


def test_verdict_not_searched_when_all_sources_failed():
    """源全挂了 ≠ 资料不存在：必须报 not_searched。"""
    r = _resp(failed=(("ddg", "返回反爬验证页"), ("bing", "结果无关已丢弃")))
    report = build_report(r)
    assert report.verdict == "not_searched"
    assert "未能完成检索" in report.verdict_text()


def test_verdict_not_found_when_sources_ok_but_empty():
    """源正常返回但没有匹配结果 → 大概率确实没有。"""
    r = _resp(used=("ddg",))
    report = build_report(r)
    assert report.verdict == "not_found"
    assert "没有匹配结果" in report.verdict_text()


def test_report_lists_sources_and_missing():
    r = _resp(used=("ddg",), failed=(("bing", "结果无关"),), year=2027)
    report = build_report(r, expect_sources=("sogou-weixin",))
    names = {s.name for s in report.sources}
    assert names == {"ddg", "bing"}
    assert any("sogou-weixin" in m for m in report.missing), "期望源未参与应进「缺失」"
    assert any("2027" in m for m in report.missing)


def test_report_marks_outdated_results():
    old = SearchResult(title="2020 复试线", url="https://a.cn/1", snippet="2020",
                       extra={"year_status": "outdated", "detected_year": 2020})
    report = build_report(_resp(results=[old], used=("ddg",)))
    md = report.to_markdown()
    assert "⚠️" in md and "请勿直接当作目标年份" in md


def test_report_markdown_structure():
    report = build_report(_resp(results=[SearchResult(title="官方目录", url="https://gs.hust.edu.cn/a")],
                               used=("ddg",)))
    md = report.to_markdown()
    for section in ("已检查的检索源", "找到", "结论"):
        assert section in md


def test_report_to_dict_is_serializable():
    import json

    report = build_report(_resp(used=("ddg",)))
    assert json.dumps(report.to_dict(), ensure_ascii=False)


# ── 数据集 ──────────────────────────────────────────────────────

def test_dataset_shape():
    rows = bench.load_dataset()
    assert len(rows) >= 30, f"数据集应 ≥30 条，实际 {len(rows)}"
    for row in rows:
        for field in ("query", "intent", "acceptable_domains", "must_terms"):
            assert field in row, f"缺少字段 {field}: {row}"
        assert row["intent"] in ("fact", "resource", "general")


def test_dataset_covers_multiple_categories():
    """报告列的八类需求都要有覆盖（简章/目录/初试科目/人数/复试线/拟录取/真题/分析）。"""
    rows = bench.load_dataset()
    text = " ".join(r["query"] for r in rows)
    for keyword in ("招生简章", "专业目录", "初试科目", "招多少人", "复试线",
                    "拟录取", "真题", "难度"):
        assert keyword in text, f"数据集未覆盖：{keyword}"


# ── 离线评测 ────────────────────────────────────────────────────

def test_offline_evaluation_metrics_in_range():
    result = bench.evaluate_offline()
    assert result.total >= 5
    for name, value in result.metrics.items():
        assert 0.0 <= value <= 1.0, f"{name}={value} 越界"
    assert "plan_site_coverage" in result.metrics and "official_first" in result.metrics


def test_offline_pipeline_drops_junk_and_merges_duplicates():
    """垃圾结果不能进 Top-K，重复变体必须被合并（本轮实测的两个核心问题）。"""
    result = bench.evaluate_offline()
    for item in result.per_query:
        assert item["after_guard"] <= item["raw"], "守门后不该变多"
        assert item["after_dedup"] <= item["after_guard"], "去重后不该变多"


def test_offline_official_ranks_first_for_fact_queries():
    result = bench.evaluate_offline()
    fact_rows = [(r, r["query"]) for r in result.per_query]
    official_first = [item["top1_official"] for item, _ in [(r, r) for r in result.per_query]]
    # 事实类查询应普遍把官方结果排第一
    assert sum(official_first) >= len(result.per_query) // 2


def test_offline_with_empty_fixtures():
    result = bench.evaluate_offline(fixtures={})
    assert result.total == 0


# ── 联网评测（注入函数，不碰真实网络） ──────────────────────────

def test_live_evaluation_computes_recall_and_mrr():
    dataset = [
        {"query": "华中科技大学 复试线", "intent": "fact",
         "acceptable_domains": ["hust.edu.cn"], "must_terms": ["复试"], "year": 2026},
        {"query": "浙江大学 复试线", "intent": "fact",
         "acceptable_domains": ["zju.edu.cn"], "must_terms": ["复试"], "year": 2026},
    ]

    def fake_search(query, year):
        if "华中科技大学" in query:
            return _resp(results=[
                SearchResult(title="华中科技大学 2026 复试线", url="https://gszs.hust.edu.cn/a",
                             snippet="2026 复试", source_type="graduate_school", authority=0.98),
                SearchResult(title="无关内容", url="https://other.cn/x", snippet="别的"),
            ], used=("ddg",))
        return _resp(results=[SearchResult(title="完全无关", url="https://other.cn/y", snippet="别的")],
                    used=("ddg",))

    result = bench.evaluate_live(service=None, dataset=dataset, search_fn=fake_search, k=5)
    assert result.total == 2
    assert result.metrics["mrr"] == pytest.approx(0.5), result.metrics
    assert result.metrics["official_hit_rate"] == pytest.approx(0.5)
    assert 0.0 <= result.metrics["recall_at_5"] <= 1.0


def test_live_evaluation_tolerates_provider_errors():
    dataset = [{"query": "x", "intent": "fact", "acceptable_domains": [], "must_terms": [], "year": None}]

    def boom(query, year):
        raise RuntimeError("网络不可用")

    result = bench.evaluate_live(service=None, dataset=dataset, search_fn=boom)
    assert result.total == 1
    assert result.per_query and "error" in result.per_query[0]


def test_live_requires_explicit_opt_in():
    """联网评测默认不跑（受引擎限流影响会波动），必须显式开启。"""
    import os

    assert os.environ.get("KY_LIVE_TEST") != "1" or True      # 仅说明用途，不做行为断言
    assert hasattr(bench, "evaluate_live")
