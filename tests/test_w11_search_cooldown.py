# -*- coding: utf-8 -*-
"""[W11 全源冷却话术] 区分「源暂时不可用」与「资料不存在」。

背景（KaoYanBench core50 w10 实测）
-----------------------------------
UNI-005 在重载全量 run 下 bing/ddg/sogou-weixin 在任务早期即被反爬标记
（600s 冷却），叠加引擎直抓拦截 → 该题检索能力归零；而 web_search 全冷却时
返回的却是泛化的「未找到结果」，模型无法区分「资料不存在」与「源暂时不可用」，
在死路上烧步数。本批给「全源冷却」一个结构化判据 + 专属降级话术：
  * SearchResponse.providers_cooling（结构化标记）+ all_failed_cooling 判据；
  * SearchReport.verdict 新增 "cooling" 分支（与 not_searched / not_found 分开）；
  * format_results 全冷却分支：明确「勿立即重试」并指出去哪（工作区资料）。

全程离线：假 provider + 冷却表操作，不发起网络请求。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.search import health  # noqa: E402
from tools.search.models import SearchResponse, merge_responses  # noqa: E402
from tools.search.providers.base import SearchProvider  # noqa: E402
from tools.search.report import build_report  # noqa: E402
from tools.search.service import SearchService, format_results  # noqa: E402
from tools.search.models import SearchQuery  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cooldown():
    health.reset()
    yield
    health.reset()


class _Probe(SearchProvider):
    name = "probe"
    priority = 5

    def search(self, query, *, limit=10, time_range=None):
        return []


# ── 1. health 文案语义（remaining 是剩余秒数，不是「之前」） ────────────


def test_cooldown_reason_semantics():
    health.mark_blocked("ddg")
    reason = health.cooldown_reason("ddg")
    assert "冷却中" in reason
    assert "秒后自动恢复" in reason
    assert "秒前" not in reason, "remaining 是剩余秒数，旧文案「N 秒前」语义反了"


# ── 2. SearchResponse 结构化判据 ────────────────────────────────────────


def test_all_failed_cooling_true_for_full_cooldown():
    resp = SearchResponse(
        query="q",
        providers_failed=(("bing", "冷却中（约 300 秒后自动恢复）"),),
        providers_cooling=(("bing", "冷却中（约 300 秒后自动恢复）"),),
    )
    assert resp.all_failed_cooling


def test_all_failed_cooling_false_for_generic_failure():
    resp = SearchResponse(query="q",
                          providers_failed=(("bing", "连接超时"),))
    assert not resp.all_failed_cooling


def test_all_failed_cooling_false_for_mixed_failure():
    """一个冷却 + 一个一般失败 → 不是「全冷却」（重试仍可能有源可用）。"""
    resp = SearchResponse(
        query="q",
        providers_failed=(("bing", "冷却中（约 300 秒后自动恢复）"),
                          ("ddg", "连接超时")),
        providers_cooling=(("bing", "冷却中（约 300 秒后自动恢复）"),),
    )
    assert not resp.all_failed_cooling


def test_all_failed_cooling_false_when_results_exist():
    from tools.search.models import SearchResult
    resp = SearchResponse(
        query="q",
        results=(SearchResult(title="t", url="https://a.edu.cn/x"),),
        providers_used=("bing",),
        providers_failed=(("ddg", "冷却中"),),
        providers_cooling=(("ddg", "冷却中"),),
    )
    assert not resp.all_failed_cooling


# ── 3. service：全冷却的端到端路径（假 provider + 冷却表） ──────────────


def test_service_full_cooldown_structured_and_message():
    health.mark_blocked("probe", "返回反爬验证页")
    svc = SearchService(providers=[_Probe()])

    resp = svc.search(SearchQuery(text="复试线"))

    assert not resp.has_results
    assert resp.all_failed_cooling, "唯一源冷却中 → 应为全冷却"
    assert dict(resp.providers_cooling)["probe"].startswith("冷却中")

    out = format_results(resp)
    assert "反爬冷却期" in out
    assert "请勿立即重试" in out
    assert "不代表资料不存在" in out
    assert "未找到结果" not in out, "全冷却不应再输出笼统的「未找到结果」"


def test_service_generic_empty_keeps_old_message():
    """源正常返回但为空 → 保持既有「未找到结果」文案（不误报冷却）。"""
    svc = SearchService(providers=[_Probe()])
    resp = svc.search(SearchQuery(text="q"))

    assert not resp.has_results
    assert not resp.all_failed_cooling
    out = format_results(resp)
    assert "未找到结果" in out
    assert "反爬冷却期" not in out


def test_service_no_provider_keeps_old_message():
    """零可用源（非冷却）→ 旧文案不变（回归 test_search_core 既有断言）。

    注意必须显式传 ``providers=[]``：``providers=None`` 会走
    ``available_providers()`` 用真实源发起网络请求（测试不得联网）。
    """
    resp = SearchService(providers=[]).search(SearchQuery(text="q"))
    assert not resp.all_failed_cooling
    assert "未找到结果" in format_results(resp)


# ── 4. report：verdict 三态区分 ────────────────────────────────────────


def test_report_verdict_cooling():
    resp = SearchResponse(
        query="q",
        providers_failed=(("bing", "冷却中（约 300 秒后自动恢复；此前被反爬/失败拦截，暂不再请求）"),),
        providers_cooling=(("bing", "冷却中（约 300 秒后自动恢复；此前被反爬/失败拦截，暂不再请求）"),),
    )
    rep = build_report(resp)

    assert rep.verdict == "cooling"
    assert rep.all_sources_cooling
    text = rep.verdict_text()
    assert "冷却" in text and "请勿立即重试" in text
    md = rep.to_markdown()
    assert "冷却" in md


def test_report_verdict_not_searched_for_generic_failure():
    resp = SearchResponse(query="q", providers_failed=(("bing", "连接超时"),))
    rep = build_report(resp)
    assert rep.verdict == "not_searched", "一般失败不得误报为冷却"
    assert "未能完成检索" in rep.verdict_text()


def test_report_verdict_not_found_when_sources_ok():
    resp = SearchResponse(query="q", providers_used=("bing",))
    rep = build_report(resp)
    assert rep.verdict == "not_found"
    assert "没有匹配结果" in rep.verdict_text()


def test_report_source_status_carries_cooling_flag():
    resp = SearchResponse(
        query="q",
        providers_failed=(("bing", "冷却中"), ("ddg", "连接超时")),
        providers_cooling=(("bing", "冷却中"),),
    )
    rep = build_report(resp)
    by_name = {s.name: s for s in rep.sources}
    assert by_name["bing"].cooling is True
    assert by_name["ddg"].cooling is False
    assert by_name["bing"].to_dict()["cooling"] is True


# ── 5. 多路合并（search_planned 路径）保留冷却标记 ─────────────────────


def test_merge_responses_carries_cooling():
    r1 = SearchResponse(query="a",
                        providers_failed=(("bing", "冷却中"),),
                        providers_cooling=(("bing", "冷却中"),))
    r2 = SearchResponse(query="b",
                        providers_failed=(("ddg", "冷却中"),),
                        providers_cooling=(("ddg", "冷却中"),))
    merged = merge_responses([r1, r2], query="q")

    assert set(dict(merged.providers_cooling)) == {"bing", "ddg"}
    assert merged.all_failed_cooling


def test_to_dict_includes_cooling():
    resp = SearchResponse(query="q",
                          providers_failed=(("bing", "冷却中"),),
                          providers_cooling=(("bing", "冷却中"),))
    d = resp.to_dict()
    assert d["providers_cooling"] == [{"provider": "bing", "reason": "冷却中"}]
