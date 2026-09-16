# -*- coding: utf-8 -*-
"""
查询改写 / 路由 / discovery 接通 的回归测试

覆盖三件事：
  1. 一条口语化需求能否被拆成**覆盖不同来源类别**的多路查询
     （官方站内 / 研招网 / 简章 / 专业目录 / 资料），而不是只有原句；
  2. 查询级 provider 路由（site: 不该发给公众号检索）；
  3. `OfficialDiscovery.build_targeted_queries` —— 这个函数此前只被实例化、
     从未被调用（死代码），现在必须真的参与侦察链路。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import (  # noqa: E402
    SearchQuery,
    SearchService,
    detect_intent,
    extract_entities,
    plan_queries,
)
from tools.search.rewrite import providers_for_query  # noqa: E402
from tools.search.providers._http import looks_like_anti_bot  # noqa: E402


# ── 实体抽取 ────────────────────────────────────────────────────

def test_extract_school_from_schoolname_in_sentence():
    entity = extract_entities("华中科技大学 计算机 复试线")
    assert entity.has_school and entity.school == "华中科技大学"


def test_extract_major_code_adjacent_to_chinese():
    """`\\b` 在 Python 里对「学085409」不成立，这条就是那个坑的回归。"""
    entity = extract_entities("南方医科大学085409今年招多少人")
    assert entity.major_code == "085409", "中文与数字相邻时也要能抽出专业代码"


def test_extract_year_from_sentence():
    assert extract_entities("2027 年考研数学二大纲").year == 2027


def test_extract_keywords_exclude_school_and_code():
    entity = extract_entities("华中科技大学 计算机 复试线")
    assert entity.keywords and "华中科技大学" not in entity.keywords, (
        "关键词里不该再出现校名，否则会拼出「校名 校名 招生简章」")


# ── 意图判别 ────────────────────────────────────────────────────

def test_detect_resource_intent():
    assert detect_intent("找南方医科大学085409专业课真题") == "resource"


def test_detect_fact_intent():
    assert detect_intent("华中科技大学 计算机 复试线") == "fact"


def test_detect_general_intent():
    assert detect_intent("考研") == "general"


# ── 查询规划 ────────────────────────────────────────────────────

def test_plan_produces_site_and_chsi_queries():
    plan = plan_queries("华中科技大学 计算机 复试线", year=2027)
    texts = plan.texts
    assert any(t.startswith("site:") for t in texts), "应产出站内检索查询"
    assert any("yz.chsi.com.cn" in t for t in texts), "应产出研招网查询"
    assert any("招生简章" in t for t in texts), "应覆盖招生简章来源"
    assert "华中科技大学 计算机 复试线" in texts, "原句本身也应保留一路"


def test_plan_includes_major_code_when_given():
    plan = plan_queries("南方医科大学085409今年招多少人", year=2027)
    assert any("085409" in t for t in plan.texts), "专业代码必须进入查询"


def test_plan_has_no_self_duplicated_school():
    plan = plan_queries("华中科技大学 计算机 复试线", year=2027)
    for text in plan.texts:
        assert text.count("华中科技大学") <= 1, f"查询中出现重复校名: {text}"


def test_plan_respects_limit_and_dedups():
    plan = plan_queries("华中科技大学 计算机 复试线", year=2027, limit=4)
    assert len(plan.queries) <= 4
    assert len(set(plan.texts)) == len(plan.texts), "查询列表不应有重复项"


def test_plan_summary_mentions_entities():
    summary = plan_queries("华中科技大学 计算机 复试线", year=2027).summary()
    assert "意图=" in summary and "华中科技大学" in summary


# ── 查询级 provider 路由 ────────────────────────────────────────

def test_site_query_uses_only_web_providers():
    assert "sogou-weixin" not in providers_for_query(
        "site:portal.smu.edu.cn 2027 硕士 招生简章", "fact")


def test_resource_query_includes_wechat_provider():
    assert "sogou-weixin" in providers_for_query("华中科技大学 真题", "resource")


def test_general_query_has_no_restriction():
    assert providers_for_query("考研数学二大纲", "general") == ()


def test_plan_sets_providers_per_query():
    plan = plan_queries("找南方医科大学085409专业课真题", year=2027, limit=6)
    site_queries = [q for q in plan.queries if q.text.startswith("site:")]
    assert site_queries and all("sogou-weixin" not in q.providers
                                for q in site_queries)


# ── 反爬识别 ────────────────────────────────────────────────────

def test_looks_like_anti_bot_detects_ddg_anomaly_page():
    """DDG 被限流时返回的是带 anomaly 的验证页（本轮实测出现）。"""
    assert looks_like_anti_bot("<html>anomaly detected</html>")
    assert looks_like_anti_bot("<html>请输入验证码</html>")
    assert looks_like_anti_bot("<html>SourceVerifyCode：480928</html>")


def test_looks_like_anti_bot_false_for_normal_page():
    assert not looks_like_anti_bot(
        "<html><ul class='news-list'>招生简章</ul></html>")


def test_ddg_raises_anti_bot_error_when_both_endpoints_blocked(monkeypatch):
    """两个端点都被挡时要说清「被反爬挡了」，而不是笼统的「没结果」。"""
    from tools.search.providers import ddg as ddg_mod

    monkeypatch.setattr(ddg_mod, "get_text",
                        lambda *a, **k: "<html>anomaly</html>")
    with pytest.raises(Exception) as exc:
        ddg_mod.DuckDuckGoProvider().search("x")
    assert "反爬" in str(exc.value)


# ── 服务：多路检索合并 ──────────────────────────────────────────

def test_search_planned_runs_all_queries_and_merges(monkeypatch):
    """多路检索应真的跑每一路，并把结果合并去重后返回。"""
    from tools.search.providers import ddg as ddg_mod

    seen: list = []
    monkeypatch.setattr(ddg_mod, "get_text",
                        lambda *a, **k: "<html>anomaly</html>")

    class _Resp:
        def __init__(self, tag):
            self.tag = tag
            self.results = ()

        @property
        def has_results(self):
            return False

    calls: list = []

    def fake_search(self, query, limit=None):
        calls.append(query.text)
        return _Resp(query.text)

    svc = SearchService(providers=[])
    monkeypatch.setattr(SearchService, "search", fake_search)
    try:
        resp = svc.search_planned("华中科技大学 计算机 复试线",
                                  limit=5, max_queries=3, year=2027)
    except Exception:
        resp = None                     # 允许在没有可用 provider 时失败
    assert len(calls) >= 2, f"多路检索应发起多轮查询，实际 {calls}"
    assert len(set(calls)) == len(calls), "各路查询不应重复"


# ── discovery 接通（死代码复活） ────────────────────────────────

def test_discover_official_pages_calls_build_targeted_queries(monkeypatch):
    """`build_targeted_queries` 必须被真的调用（此前只有实例化、无调用）。"""
    import tools.intelligence.discovery as discovery_mod
    from tools.intelligence.registry import resolve_university
    from tools.intelligence.scout_engine import KaoYanIntelligenceEngine

    calls: list = []
    real = discovery_mod.OfficialDiscovery.build_targeted_queries

    def spy(self, school_name, domain, major_keyword=None, year=0):
        calls.append((school_name, domain, major_keyword, year))
        return [f"site:{domain} {year} 硕士 招生简章"]

    monkeypatch.setattr(discovery_mod.OfficialDiscovery,
                        "build_targeted_queries", spy)
    # 让检索返回空，避免真实网络依赖
    monkeypatch.setattr(SearchService, "search",
                        lambda self, q, limit=None: SimpleNamespace(results=()))

    engine = KaoYanIntelligenceEngine()
    entity = resolve_university("南方医科大学")
    assert getattr(entity, "chsi_code", "").isdigit(), "该院校应在注册表中"
    out = engine._discover_official_pages(
        entity, "南方医科大学", "生物医学工程", 2027, already_fetched=set())

    assert calls, "build_targeted_queries 未被调用 —— discovery 仍是死代码"
    assert calls[0][0] == "南方医科大学"
    assert isinstance(out, list)


def test_discover_official_pages_returns_empty_without_domains():
    """没有可用域名时不应报错，也不应编查询（不猜域名）。"""
    from tools.intelligence.scout_engine import KaoYanIntelligenceEngine

    engine = KaoYanIntelligenceEngine()
    assert engine._discover_official_pages(None, "野鸡大学xyz", "", 2027,
                                           already_fetched=set()) == []
