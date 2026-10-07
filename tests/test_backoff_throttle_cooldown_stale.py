# -*- coding: utf-8 -*-
"""退避 / 节流 / 冷却 / serve-stale 的行为钉住（R3 仿真修复批）

[这批测试要拦住的回归]
R3 全矩阵仿真（3 考生 × 6 环节 × 3 端= 54 格）实测到两类「能力归零」：
  * 公众号源进冷却 600s 后，10 分钟内彻底不参与检索 —— CLI 抓 5 篇、稍后 GUI
    抓 0 篇（时序互补）；
  * 研招网频现 ``RemoteDisconnected``，而各处退避节奏互不同步。

对应的修复点各有一条用例钉住语义（而非数值）：节流只延后不改成败、退避服从
单一真源、首次冷却不低于 600s、冷却状态跨进程、过期缓存兜底而非报「无结果」。

全程离线：所有时钟注入或打桩，**不使用真实 sleep**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.search import health  # noqa: E402
from tools.search.cache import STALE_MAX_AGE, SearchCache  # noqa: E402
from tools.search.models import SearchQuery, SearchResult  # noqa: E402
from tools.search.providers import _http  # noqa: E402
from tools.search.providers.base import SearchProvider  # noqa: E402
from tools.search.service import SearchService, format_results  # noqa: E402


class _Clock:
    """可手工推进的单调时钟（避免测试真等）。"""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


@pytest.fixture(autouse=True)
def _isolate_cooldown(tmp_path):
    """冷却状态落盘指向 tmp：既验证跨进程语义，又不碰真实工作区``.memory/``。"""
    health.set_persistence(True, path=tmp_path / "cooldown.json")
    health.reset()
    yield
    health.reset()
    health.set_persistence(False, path=None)


# ══════════════════════════════════════════════════════════════════════
# 1. _http 退避：服从单一真源的 full jitter
# ══════════════════════════════════════════════════════════════════════


def test_backoff_uses_single_source_full_jitter(monkeypatch):
    """退避间隔由单一真源的 full jitter 决定，**旧的加性抖动已消失**。

    [回归] 原实现是 ``min(cap, base·2^(n-1)) + U(0.05, 0.25)``：那个
    ``U(0.05, 0.25)`` 让所有客户端的等待区间高度重叠，同机多路检索近乎齐步
    重试（惊群），恰好加重封禁。full jitter 则是 ``U(0, min(cap, base·2^n))``。

    判据用「随机取值的**区间**」这种确定性探针，而不是「某次等待恰好 < 0.05」——
    后者依赖随机取值，会 flaky。
    """
    from tools.http_backoff import jitter_ceiling

    slept: list[float] = []
    uniform_spans: list[tuple] = []

    class _Resp:
        headers: dict = {}

        def read(self, *_a):
            return b"<html>ok</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    attempts = {"n": 0}

    def mock_urlopen(req, timeout=None, context=None):
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise TimeoutError("timed out")
        return _Resp()

    def spy_uniform(a, b):
        uniform_spans.append((a, b))
        return 0.0

    monkeypatch.setattr(_http, "safe_urlopen", mock_urlopen)
    monkeypatch.setattr(_http.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(_http.random, "uniform", spy_uniform)
    # 节流单测已独立覆盖；这里关掉以隔离「退避间隔」这一个变量。
    monkeypatch.setattr(_http, "respect_rate_limit", lambda url: 0.0)

    out = _http.get_text("https://backoff.example.edu.cn/x", max_retries=3,
                         backoff_base=0.3, backoff_max=3.0)
    assert "ok" in out and len(slept) == 3
    assert len(uniform_spans) == 3, f"应有 3 次 full jitter 取样：{uniform_spans}"
    for i, (lo, hi) in enumerate(uniform_spans):
        assert lo == 0.0, (
            f"第 {i + 1} 次取样下界为 {lo} —— full jitter 必须从 0 起"
            "（旧的加性抖动 U(0.05,0.25) 会让所有客户端齐步重试）")
        assert hi <= jitter_ceiling(i, base=0.3, cap=3.0) + 1e-9, (
            f"第 {i + 1} 次取样上界 {hi} 超出 full jitter 上界")


def test_retry_after_header_wins_over_jitter(monkeypatch):
    """服务端给了 ``Retry-After`` 就按它等（反爬站点明示「多久后再来」）。"""
    import urllib.error

    slept: list[float] = []

    def raiser_429(*_a, **_k):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests",
                                     {"Retry-After": "2"}, None)

    monkeypatch.setattr(_http, "safe_urlopen", raiser_429)
    monkeypatch.setattr(_http.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(_http, "respect_rate_limit", lambda url: 0.0)

    with pytest.raises(Exception):
        _http.get_text("https://retry-after.example.edu.cn/x", max_retries=2)
    assert slept and slept[0] == pytest.approx(2.0), (
        f"首个退避应为 Retry-After=2s，实际 {slept[:1]}")


# ══════════════════════════════════════════════════════════════════════
# 2. _http 节流：只延后，不改失败语义
# ══════════════════════════════════════════════════════════════════════


def test_rate_limit_delays_second_request_to_same_host(monkeypatch):
    """同host 第二次请求须被延后；不同 host 不受影响。"""
    slept: list[float] = []
    monkeypatch.setattr(_http.time, "sleep", lambda s: slept.append(s))
    _http.reset_rate_limit()

    assert _http.respect_rate_limit("https://rl.example.edu.cn/a") == 0.0
    first = _http.respect_rate_limit("https://rl.example.edu.cn/b")
    assert first > 0, "同host 第二次必须被节流"
    assert _http.respect_rate_limit("https://other.example.edu.cn/a") == 0.0, (
        "节流按 host 生效，不得牵连无关站点")
    assert slept and slept[0] > 0


def test_rate_limit_wait_is_capped(monkeypatch):
    """节流等待有上限：节流是保护措施，不该成为新的超时来源。"""
    slept: list[float] = []
    monkeypatch.setattr(_http.time, "sleep", lambda s: slept.append(s))
    _http.reset_rate_limit()

    _http.respect_rate_limit("https://cap.example.edu.cn/a")
    waited = _http.respect_rate_limit("https://cap.example.edu.cn/b",
                                      max_wait=0.25)
    assert waited <= 0.25, "等待须被 max_wait 截断"
    assert sum(slept) <= 0.25 + 1e-9


def test_rate_limit_does_not_change_failure_semantics(monkeypatch):
    """节流不得改变失败语义：该失败的请求仍然如实抛 ProviderError。"""
    import urllib.error

    def raiser(*_a, **_k):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(_http, "safe_urlopen", raiser)
    monkeypatch.setattr(_http.time, "sleep", lambda s: None)
    _http.reset_rate_limit()

    from tools.search.providers.base import ProviderError
    with pytest.raises(ProviderError) as exc:
        _http.get_text("https://fails.example.edu.cn/a")
    assert "404" in str(exc.value)


def test_rate_limit_applies_per_source_config():
    """反爬更严的源单独放慢（配置口径可查、单一真源）。"""
    assert _http.THROTTLE_RATES["weixin.sogou.com"][0] < \
        _http.THROTTLE_RATES["default"][0], "搜狗微信应比默认更保守"
    assert _http.THROTTLE_RATES["www.bing.com"][0] <= \
        _http.THROTTLE_RATES["default"][0]


# ══════════════════════════════════════════════════════════════════════
# 3. health 冷却策略：首次 ≥600s + 指数延长 + 半开 + 跨进程
# ══════════════════════════════════════════════════════════════════════


def test_first_cooldown_is_at_least_600s():
    """红线：首次冷却不得低于600s（原实现即600s，改阈值后缩水=体验变差）。"""
    pol = health.policy()
    assert pol.base >= 600.0, (
        f"首次冷却基数 {pol.base} 秒低于 600s 红线 —— 注意不能因为引入"
        "「连续失败才冷却」阈值就把首次时长缩水（请求更多 → 更快被封）")

    health.mark_blocked("sogou-weixin", "反爬")
    # 允许 1s 观测容差：remaining() 按调用瞬间的单调时钟重算
    assert health.policy().remaining("sogou-weixin") >= 599.0
    assert "冷却中（约" in health.cooldown_reason("sogou-weixin")


def test_repeated_blocks_grow_cooldown_exponentially():
    health.mark_blocked("ddg", "反爬1")
    first = health.policy().remaining("ddg")
    health.policy().record_success("ddg")
    health.mark_blocked("ddg", "反爬1")
    health.mark_blocked("ddg", "反爬2")
    grew = health.policy().remaining("ddg")
    assert grew > first, "连续被拦应指数延长（越多请求越慢=越安全）"
    assert grew <= health.MAX_COOLDOWN_SECONDS + 1.0


def test_note_failure_needs_two_consecutive_failures():
    """单次疑似抖动不冷却（否则一次超时就把源停摆 10 分钟）。"""
    assert health.note_failure("bing", "超时") == 0.0
    assert not health.is_cooling("bing")
    span = health.note_failure("bing", "超时")
    assert span >= 600.0 and health.is_cooling("bing")


def test_mark_healthy_clears_cooldown():
    """半开探测成功后必须能立刻恢复（否则源会永久停摆）。"""
    health.mark_blocked("ddg", "反爬")
    assert health.is_cooling("ddg")
    health.mark_healthy("ddg")
    assert not health.is_cooling("ddg")
    assert not health.cooldown_reason("ddg")


def test_cooldown_message_shape_unchanged():
    """文案形态是W11 测试的契约（``冷却中（约 N 秒后自动恢复…）``）。"""
    health.mark_blocked("bing", "反爬")
    reason = health.cooldown_reason("bing")
    assert reason.startswith("冷却中（约")
    assert "秒后自动恢复" in reason
    assert "秒前" not in reason, "remaining 是剩余秒数，语义不能反"


def test_cooldown_state_survives_process_restart(tmp_path):
    """跨进程记忆：换个端点（CLI→GUI）不该忘了自己刚被封。"""
    health.mark_blocked("sogou-weixin", "反爬")
    assert (tmp_path / "cooldown.json").exists(), "冷却状态应落盘"

    # 模拟新进程：模块级单例清空后按同样的路径恢复
    saved = health.policy().snapshot()
    health._POLICY = None                     # 模拟进程重启
    assert health.policy().is_cooling("sogou-weixin"), (
        "重启后应记得仍在冷却（否则换个端点就再撞一次墙）")
    # [POSIX 时钟粒度修复 2026-10-07] 不能对 saved 用 1e-6 精确上界：落盘发生在
    # mark_blocked 内部（较早时刻），saved 是测试稍后读的内存快照 —— 恢复以
    # 「落盘值 + 续算」为准，remaining 允许比 saved 大一个「persist→snapshot
    # 调用间隙」（Linux μs 级单调时钟下实测 ~93μs 致红：599.999971036 <=
    # 599.999878266 + 1e-6 为假；Windows 15.6ms 时钟粒度掩盖该差，故仅 POSIX
    # 翻车）。改「量级窗口」断言：下界 590 钉住「确实续算出接近 600s 的剩余」
    # （抓 0/小值/符号错误），上界 saved+1s 钉住「没有凭空增多」（抓指数翻倍
    # 等秒级回归；亚毫秒级的观测间隙无实际影响，不设防）。
    remaining = health.policy().remaining("sogou-weixin")
    assert 590.0 <= remaining <= saved["sogou-weixin"] + 1.0


def test_half_open_allows_only_one_probe(monkeypatch):
    """冷却期满只放行一次探测（并发全放行等于冷却从未生效）。"""
    clk = _Clock()
    from tools.http_backoff import CooldownPolicy
    pol = CooldownPolicy(base_seconds=60.0, failure_threshold=1, monotonic=clk)
    pol.force_cooldown("tavily")
    assert not pol.try_half_open("tavily")
    clk.advance(61)
    assert pol.try_half_open("tavily")
    assert not pol.try_half_open("tavily"), "半开期间只放行一次"


# ══════════════════════════════════════════════════════════════════════
# 4. serve-stale：过期缓存兜底，而非报「无结果」
# ══════════════════════════════════════════════════════════════════════


def _one_result() -> list[SearchResult]:
    return [SearchResult(title="2027 招生简章", url="https://yz.example.edu.cn/a",
                         snippet="s", source_type="university_official",
                         authority=0.9)]


def _expire(cache: SearchCache, provider: str, query: str, limit: int = 10,
            by: float = 3600.0) -> None:
    """把某条缓存的回填时间往前挪，使它确定处于过期状态。

    不用 ``time.sleep``：Windows 的 ``time.time()`` 分辨率约 15.6ms，
    ``put(ttl=0)`` 后立刻 ``get`` 可能age 恰为 0.0 → 并未过期（本批实测）。
    """
    key = cache.make_key(provider, query, limit, None)
    cache._entries[key].stored_at -= by
    assert cache._entries[key].expired


def test_cache_strict_mode_still_misses(tmp_path):
    """默认严格：过期即未命中（既有语义不得被 serve-stale 悄悄改掉）。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "x", 10, _one_result(), ttl=0)
    _expire(cache, "ddg", "x")
    assert cache.get("ddg", "x", 10) is None


def test_cache_serves_stale_with_annotation(tmp_path):
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "华中科技大学 复试线", 10, _one_result(), ttl=600)
    _expire(cache, "ddg", "华中科技大学 复试线")
    got = cache.get("ddg", "华中科技大学 复试线", 10, allow_stale=True)
    assert got, "过期条目应可作兜底返回"
    assert got[0].extra.get("stale") is True
    assert got[0].extra.get("stale_age_seconds", 0) >= 0
    assert got[0].url == "https://yz.example.edu.cn/a", "内容必须是原缓存内容"
    assert cache.stale_serves == 1


def test_cache_refuses_too_old_stale(tmp_path):
    """陈旧超出兜底窗口 → 真miss（10 天前的简章对考生是负资产）。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "x", 10, _one_result(), ttl=600)
    _expire(cache, "ddg", "x", by=STALE_MAX_AGE + 3600)
    assert cache.get("ddg", "x", 10, allow_stale=True) is None


def test_cache_keeps_stale_entries_across_reopen(tmp_path):
    """重启后仍能serve-stale（否则「换个端点就忘了」在缓存侧同样发生）。"""
    path = tmp_path / "c.json"
    first = SearchCache(path=path)
    first.put("ddg", "持久化", 10, _one_result(), ttl=600)
    _expire(first, "ddg", "持久化")
    first._save()
    second = SearchCache(path=path)
    got = second.get("ddg", "持久化", 10, allow_stale=True)
    assert got and got[0].extra.get("stale") is True


class _Blocked(SearchProvider):
    name = "blocked-probe"
    priority = 5

    def search(self, query, *, limit=10, time_range=None):
        return []


def test_service_serves_stale_during_full_cooldown(tmp_path):
    """核心修复：全源冷却时用过期缓存兜底，而不是让检索能力归零。"""
    cache = SearchCache(path=tmp_path / "c.json")
    q = SearchQuery(text="华中科技大学 复试线", limit=10)
    cache.put("blocked-probe", q.text, 10, _one_result(), ttl=600)
    _expire(cache, "blocked-probe", q.text, limit=10)

    svc = SearchService(providers=[_Blocked()])
    svc._cache = cache

    health.mark_blocked("blocked-probe", "反爬")
    resp = svc.search(q)

    assert resp.has_results, "冷却期应给出陈旧结果而不是「无结果」"
    assert resp.results[0].extra.get("stale") is True
    # 冷却事实仍如实上报（冷却没解除，不能假装一切正常）
    assert resp.all_failed_cooling is False, "有结果时不应再宣称「全冷却归零」"
    assert dict(resp.providers_cooling), "但仍须标明该源处于冷却期"


def test_format_results_marks_stale(tmp_path):
    """输出里必须说清这是陈旧数据，否则模型会当成刚发布的口径去引用。"""
    cache = SearchCache(path=tmp_path / "c.json")
    q = SearchQuery(text="华中科技大学 复试线", limit=10)
    cache.put("blocked-probe", q.text, 10, _one_result(), ttl=600)
    _expire(cache, "blocked-probe", q.text, limit=10)
    cache.get("blocked-probe", q.text, 10, allow_stale=True)

    svc = SearchService(providers=[_Blocked()])
    svc._cache = cache
    health.mark_blocked("blocked-probe", "反爬")
    out = format_results(svc.search(q))
    assert "陈旧缓存" in out, f"输出未标注陈旧度：\n{out}"


def test_stale_fallback_is_off_when_source_healthy(tmp_path):
    """源可用时不该用陈旧数据盖过新鲜结果（兜底只在不可用时启用）。"""
    cache = SearchCache(path=tmp_path / "c.json")
    q = SearchQuery(text="华中科技大学 复试线", limit=10)
    cache.put("blocked-probe", q.text, 10, _one_result(), ttl=600)
    _expire(cache, "blocked-probe", q.text, limit=10)

    class _Ok(SearchProvider):
        name = "blocked-probe"
        priority = 5

        def search(self, query, *, limit=10, time_range=None):
            #标题必须与查询相关，否则会被相关性守门当垃圾丢掉（那是另一条防线）
            return [SearchResult(title="华中科技大学 2027 复试线",
                                 url="https://yz.example.edu.cn/new",
                                 snippet="复试分数线")]

    svc = SearchService(providers=[_Ok()])
    svc._cache = cache
    resp = svc.search(q)
    titles = [r.title for r in resp.results]
    assert "华中科技大学 2027 复试线" in titles, (
        f"源可用时应走新鲜结果，不回退到陈旧缓存：{titles}")
    assert not any(r.extra.get("stale") for r in resp.results), (
        "源可用时不得把陈旧数据混入结果")


# ══════════════════════════════════════════════════════════════════════
# 5. 微信冷却期本地兜底
# ══════════════════════════════════════════════════════════════════════


def test_wechat_cooldown_falls_back_to_local_cache(monkeypatch):
    """冷却期先查本地沉淀，有就返回并标注，不直接返回 []。"""
    from tools.skills.wechat_searcher import WeChatArticleItem, WeChatSearchEngine

    engine = WeChatSearchEngine()
    monkeypatch.setattr(engine, "search_local_cache",
                        lambda kw, n: [WeChatArticleItem(title="本地经验",
                                                         url="https://local/x",
                                                         source_platform="local")])

    def boom(*a, **k):
        raise AssertionError("冷却期不得发起任何请求")

    monkeypatch.setattr(_http, "get_text", boom)

    health.mark_blocked("sogou-weixin", "此前反爬")
    out = engine._search_sogou("测试", 5, "year")

    assert out, "冷却期应返回本地缓存兜底而非空列表"
    assert all(i.source_platform == "local_cooldown" for i in out), (
        "兜底结果必须标注来源通道（区别于正常本地检索的 local）")
    assert any("冷却" in e for e in engine.last_errors), (
        "冷却事实仍须留痕，否则调用方无法判断结果时效")


def test_wechat_cooldown_returns_empty_when_no_local_cache(monkeypatch):
    """本地也没有沉淀时如实返回空（不得凭空编造文章）。"""
    from tools.skills.wechat_searcher import WeChatSearchEngine

    engine = WeChatSearchEngine()
    monkeypatch.setattr(engine, "search_local_cache", lambda kw, n: [])
    health.mark_blocked("sogou-weixin", "此前反爬")

    assert engine._search_sogou("测试", 5, "year") == []
    assert any("冷却" in e for e in engine.last_errors)