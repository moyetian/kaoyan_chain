# -*- coding: utf-8 -*-
"""
去重 / 缓存 / 冷却 / 重排 / 年份锁 的回归测试

其中「真实重复场景」的夹具来自本轮联网实测：多路检索「南方医科大学085409招生简章」
时，前 5 条里有 4 条是研招网同一个 `schoolInfo--schId-368409` 页面的不同 URL 变体。
不去重会让模型误以为拿到了多个独立来源。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import (  # noqa: E402
    Deduplicator,
    Ranker,
    SearchCache,
    SearchQuery,
    SearchService,
    canonical_url,
    dedup_urls,
    detect_year,
    year_status,
)
from tools.search import health  # noqa: E402
from tools.search.models import SearchResult  # noqa: E402

_TITLE = "南方医科大学_院校信息_中国研究生招生信息网"
#: 实测出现的同一页面变体（跟踪参数 / www / fragment / 大小写）
REAL_DUP_URLS = [
    "https://yz.chsi.com.cn/sch/schoolInfo--schId-368409.dhtml",
    "https://yz.chsi.com.cn/sch/schoolInfo--schId-368409.dhtml?category=1",
    "http://www.yz.chsi.com.cn/sch/schoolInfo--schId-368409.dhtml?utm_source=wx",
    "https://yz.chsi.com.cn/sch/schoolInfo--schId-368409.dhtml#top",
]


def _results(urls, title=_TITLE, snippet="南方医科大学 招生"):
    return [SearchResult(title=title, url=u, snippet=snippet) for u in urls]


# ── URL 规范化 ─────────────────────────────────────────────────

def test_canonical_url_strips_tracking_and_www():
    assert canonical_url("https://www.Example.com/a/?utm_source=wx&b=2") == \
        "https://example.com/a?b=2"


def test_canonical_url_drops_fragment_and_trailing_slash():
    assert canonical_url("https://a.edu.cn/x/#top") == "https://a.edu.cn/x"
    assert canonical_url("https://a.edu.cn/x/") == "https://a.edu.cn/x"


def test_canonical_url_sorts_params_and_normalizes_scheme():
    """参数顺序、http/https 差异都不应造成「看起来是两条」。"""
    a = canonical_url("https://a.edu.cn/x?b=2&a=1")
    b = canonical_url("https://a.edu.cn/x?a=1&b=2")
    c = canonical_url("http://www.a.edu.cn/x?a=1&b=2&utm_source=wx")
    assert a == b == c == "https://a.edu.cn/x?a=1&b=2"


def test_canonical_url_keeps_content_params():
    """`?id=` 这类会改变内容的参数必须保留（否则会误合并不同页面）。"""
    a = canonical_url("https://a.edu.cn/p?id=1")
    b = canonical_url("https://a.edu.cn/p?id=2")
    assert a != b


# ── 去重 ────────────────────────────────────────────────────────

def test_dedup_merges_real_duplicate_variants():
    kept, removed = Deduplicator().dedup(_results(REAL_DUP_URLS))
    assert len(kept) == 1 and removed == 3, f"保留 {len(kept)} 去掉 {removed}"


def test_dedup_merges_same_title_different_url():
    """同一篇被不同站点转载（URL 不同、标题相同）也该合并。"""
    rs = [
        SearchResult(title=_TITLE, url="https://a.cn/x", snippet="s" * 40),
        SearchResult(title=_TITLE, url="https://b.cn/y", snippet="s" * 40),
    ]
    kept, removed = Deduplicator().dedup(rs)
    assert len(kept) == 1 and removed == 1


def test_dedup_keeps_distinct_results():
    rs = [
        SearchResult(title="2026 招生简章", url="https://a.edu.cn/1", snippet="x"),
        SearchResult(title="2026 复试分数线", url="https://a.edu.cn/2", snippet="y"),
    ]
    kept, removed = Deduplicator().dedup(rs)
    assert len(kept) == 2 and removed == 0


def test_dedup_prefers_higher_authority_variant():
    """同组内保留权威度更高的那条（官方页而非转载页）。"""
    rs = [
        SearchResult(title=_TITLE, url=REAL_DUP_URLS[0], snippet="s", authority=0.4),
        SearchResult(title=_TITLE, url=REAL_DUP_URLS[1], snippet="s", authority=0.98,
                     source_type="national_official"),
    ]
    kept, _ = Deduplicator().dedup(rs)
    assert len(kept) == 1 and kept[0].authority == 0.98


def test_dedup_urls_helper():
    """`dedup_urls` 只做 URL 规范化：`?category=1` 属内容参数，不应被合并。"""
    assert len(dedup_urls(REAL_DUP_URLS + ["https://other.cn/z"])) == 3


# ── 缓存 ────────────────────────────────────────────────────────

def test_cache_roundtrip(tmp_path):
    cache = SearchCache(path=tmp_path / "c.json")
    results = _results(REAL_DUP_URLS[:2])
    cache.put("ddg", "华中科技大学 复试线", 10, results)
    got = cache.get("ddg", "华中科技大学 复试线", 10)
    assert got is not None and len(got) == 2
    assert cache.hits == 1 and cache.misses == 0


def test_cache_is_query_scoped(tmp_path):
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "A 复试线", 10, _results(REAL_DUP_URLS[:1]))
    assert cache.get("ddg", "B 复试线", 10) is None
    assert cache.get("bing", "A 复试线", 10) is None


def test_cache_expires(tmp_path):
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "x", 10, _results(REAL_DUP_URLS[:1]), ttl=0)
    time.sleep(0.01)
    assert cache.get("ddg", "x", 10) is None, "过期条目必须失效"


def test_cache_persists_to_disk(tmp_path):
    path = tmp_path / "c.json"
    first = SearchCache(path=path)
    first.put("ddg", "持久化测试", 10, _results(REAL_DUP_URLS[:1]))
    assert SearchCache(path=path).get("ddg", "持久化测试", 10) is not None


def test_cache_ttl_shorter_for_official_than_resource(tmp_path):
    """官方招生信息更新更频繁，缓存期应短于资料类。"""
    cache = SearchCache(path=tmp_path / "c.json")
    official = [SearchResult(title="2027 招生简章", url="https://gs.hust.edu.cn/a",
                             snippet="s", source_type="graduate_school", authority=0.98)]
    resource = [SearchResult(title="考研数学二真题", url="https://a.cn/p",
                             snippet="s", source_type="unknown", authority=0.4)]
    assert cache.ttl_for(official) < cache.ttl_for(resource)


def test_cache_disabled(tmp_path):
    cache = SearchCache(path=tmp_path / "c.json", enabled=False)
    cache.put("ddg", "x", 10, _results(REAL_DUP_URLS[:1]))
    assert cache.get("ddg", "x", 10) is None


# ── 冷却 ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_cooldown():
    health.reset()
    yield
    health.reset()


def test_cooldown_skips_blocked_provider():
    """被反爬拦截过的源，冷却期内不再请求（避免把限流越试越严）。"""
    health.mark_blocked("ddg", "DuckDuckGo 返回反爬验证页")
    assert health.is_cooling("ddg")
    assert "冷却中" in health.cooldown_reason("ddg")

    svc = SearchService.default()
    assert [p.name for p in svc.providers()] != ["ddg"] or "ddg" not in [p.name for p in svc.providers()]
    assert all(not p.name == "ddg" for p in svc.providers()), "冷却中的源不应参与检索"


def test_service_records_cooling_providers_as_failed():
    health.mark_blocked("ddg", "反爬")
    svc = SearchService.default()
    resp = svc.search(SearchQuery(text="测试 复试线", providers=("ddg",)))
    reasons = dict(resp.providers_failed)
    assert "ddg" in reasons and "冷却" in reasons["ddg"]


def test_service_marks_anti_bot_provider_blocked():
    from tools.search.providers.base import ProviderError, SearchProvider

    class Blocked(SearchProvider):
        name = "blocked-probe"
        priority = 5

        def search(self, query, *, limit=10, time_range=None):
            raise ProviderError("返回反爬验证页（命中特征 anomaly）")

    svc = SearchService(providers=[Blocked()])
    svc.search(SearchQuery(text="x"))
    assert health.is_cooling("blocked-probe"), "反爬失败应让该源进入冷却"


def test_cooldown_expires(monkeypatch):
    monkeypatch.setattr(health, "COOLDOWN_SECONDS", 0)
    health.mark_blocked("ddg")
    time.sleep(0.01)
    assert not health.is_cooling("ddg"), "冷却到期后应自动恢复"


# ── 重排与年份锁 ────────────────────────────────────────────────

def _r(title, url, **kw):
    return SearchResult(title=title, url=url, snippet=kw.pop("snippet", "内容"), **kw)


def test_rank_puts_official_current_year_first():
    """官方 + 当年，应压过公众号 + 三年前（实测里后者曾被排到前面）。"""
    query = SearchQuery(text="华中科技大学 计算机 复试线", year=2026)
    rs = [
        _r("22考研 华中科技大学 计算机 考情汇总", "https://zhuanlan.zhihu.com/p/1",
           source_type="community", authority=0.38, snippet="2022 年数据"),
        _r("华中科技大学2026年硕士研究生复试分数线", "https://gszs.hust.edu.cn/a",
           source_type="graduate_school", authority=0.98, snippet="2026 年分数线"),
    ]
    ranked = Ranker().rank(rs, query)
    assert ranked[0].url.endswith("gszs.hust.edu.cn/a")
    assert ranked[0].score > ranked[1].score


def test_rank_marks_outdated_results():
    query = SearchQuery(text="华中科技大学 复试线", year=2027)
    rs = [_r("2020 复试线", "https://a.cn/1", snippet="2020年 学硕350分")]
    ranked = Ranker().rank(rs, query)
    assert ranked[0].extra["year_status"] == "outdated"
    assert ranked[0].extra["detected_year"] == 2020


def test_year_lock_does_not_delete_historical_results():
    """过期结果要降权，但**不能删** —— 它是判断趋势的唯一线索。"""
    query = SearchQuery(text="华中科技大学 复试线", year=2027)
    rs = [_r("2019 复试线", "https://a.cn/1", snippet="2019年 分数线")]
    assert len(Ranker().rank(rs, query)) == 1


def test_year_status_matrix():
    assert year_status(2027, 2027) == "current"
    assert year_status(2026, 2027) == "outdated"
    assert year_status(0, 2027) == "unknown"
    assert year_status(2027, None) == "unknown"
    assert year_status(2028, 2027) == "current"      # 下一年预告不算过期


def test_detect_year_from_timestamp_and_text():
    assert detect_year(_r("x", "u", published_at="1735689600")) == 2025
    assert detect_year(_r("x", "u", published_at="2024-10-08")) == 2024
    assert detect_year(_r("2023年考研汇总", "u")) == 2023


def test_rank_is_explainable():
    """每条结果都要能回答「为什么排在这个位置」。"""
    query = SearchQuery(text="华中科技大学 复试线", year=2026)
    ranked = Ranker().rank([_r("2026 复试线", "https://gszs.hust.edu.cn/a",
                               source_type="graduate_school", authority=0.98)], query)
    breakdown = ranked[0].extra["score_breakdown"]
    for key in ("lexical", "authority", "freshness", "entity", "quality", "total"):
        assert key in breakdown
    assert 0.0 <= breakdown["total"] <= 1.0


def test_rank_is_stable_for_equal_scores():
    """同分时顺序必须稳定（否则刷新一次结果次序就变，用户会以为系统在乱排）。"""
    query = SearchQuery(text="复试线", year=2026)
    rs = [_r("复试线", "https://a.cn/1"), _r("复试线", "https://b.cn/2")]
    first = [r.url for r in Ranker().rank(rs, query)]
    second = [r.url for r in Ranker().rank(list(reversed(rs)), query)]
    assert first == second, "同分结果应按键值稳定排序"


# ── 服务集成：去重 + 重排一起生效 ───────────────────────────────

def test_service_dedups_and_ranks_together():
    from tools.search.providers.base import SearchProvider

    class Stub(SearchProvider):
        name = "stub"
        priority = 1

        def search(self, query, *, limit=10, time_range=None):
            return self.normalize([
                {"title": "华中科技大学 2022 考研考情汇总",
                 "url": "https://zhuanlan.zhihu.com/p/1", "snippet": "2022 复试线 数据"},
                {"title": "华中科技大学 2026 复试分数线",
                 "url": "https://gszs.hust.edu.cn/a", "snippet": "2026 复试线"},
                {"title": "华中科技大学 2026 复试分数线",
                 "url": "https://gszs.hust.edu.cn/a?utm_source=wx", "snippet": "2026 复试线"},
            ])

    # 用 default() 装配：直接 SearchService(providers=...) 走的是显式注入，
    # 不会自动带去重/重排（这是有意的，便于测试隔离）。
    svc = SearchService.default(providers=[Stub()])
    svc._cache = None                      # 关掉缓存，确保真的走了一遍检索
    resp = svc.search(SearchQuery(text="华中科技大学 复试线", limit=5, year=2026))
    assert len(resp.results) == 2, "重复 URL 必须被合并"
    assert resp.duplicates >= 1
    assert resp.results[0].url.endswith("gszs.hust.edu.cn/a"), "官方当年结果应排第一"
    assert resp.results[0].score >= resp.results[1].score
