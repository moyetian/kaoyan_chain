# -*- coding: utf-8 -*-
"""[P1 修复·2026-10-08] 「检索/微信」组 S1-S5 回归测试（六领域审查第三批）

[这批测试要拦住的回归]

* **S1** 软性反爬（HTTP 200 + 全垃圾）守门丢弃后**不触发冷却**：Bing 每轮白试
  （对照 ProviderError 路径有冷却）。修法：丢弃分支落账 ``note_failure``
  （阈值型——全垃圾是启发式判定，一次误判不该让源停摆 10 分钟；连续两轮才
  冷却）；且 ``mark_healthy`` 必须移到守门**之后**，否则失败计数每轮被清零、
  阈值冷却永远攒不够。
* **S2** ``try_half_open`` 死代码：半开探测从未启用，冷却期满全量放行（惊群）。
  修法：``SearchService`` 在真正发请求前消费半开名额——冷却期满只放行一次
  探测；缓存命中不浪费名额；探测失败（含未预期异常）强制续冷、不得悬空。
* **S3** 搜狗 ``published_at`` 输出裸 Unix 时间戳（如 1617193814），违反
  ``SearchResult`` 的「ISO 日期或空串」契约；``time_range`` 参数被完全忽略。
  修法：转本地时区 ISO 日期（与 skills 层同口径）+ 客户端时间过滤。
* **S4** 「结构改版」与「被限流」共用文案同一处置：确定性故障被反复冷却惩罚
  （冷却越推越高）。修法：改版独立文案 → 阈值型 ``note_failure``；反爬/限流
  维持 ``mark_blocked`` 立即冷却。
* **S5** Bing 单端点单 UA（对照 DDG 双端点+随机 UA）。修法：随机 UA 池 +
  ``cn.bing.com`` 备用端点（主端点网络异常/反爬时自动换端点）。

全程离线：``get_text`` 打桩、时钟注入、``health`` 落盘指向 ``tmp_path``。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.http_backoff import CooldownPolicy  # noqa: E402
from tools.search import health  # noqa: E402
from tools.search.models import SearchQuery, SearchResult  # noqa: E402
from tools.search.providers import bing as bing_mod  # noqa: E402
from tools.search.providers import sogou as sogou_mod  # noqa: E402
from tools.search.providers._http import USER_AGENTS  # noqa: E402
from tools.search.providers.base import ProviderError, SearchProvider  # noqa: E402
from tools.search.service import SearchService  # noqa: E402


class _Clock:
    """可手工推进的时钟（避免测试真等）。"""

    def __init__(self, t: float = 1000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


@pytest.fixture(autouse=True)
def _isolate_health(tmp_path):
    """冷却状态落盘指向 tmp：不碰真实工作区 ``.memory/``，用例间互不污染。"""
    health.set_persistence(True, path=tmp_path / "cooldown.json")
    health.reset()
    yield
    health.reset()
    health.set_persistence(False, path=None)


# ── 合成夹具（身份中性化） ──────────────────────────────────────────────

#: 软性反爬垃圾：结构正常（正则能匹配到「结果」）但与查询毫无关系
_JUNK = [SearchResult(title="YouTube TV Help",
                      url="https://support.google.com/youtubetv/?hl=en",
                      snippet="Watch YouTube TV on supported devices.")]
_QUERY = "马克思主义 基本原理"


class _JunkProvider(SearchProvider):
    """返回 200+全垃圾（模拟软性反爬），并记录探测中的第二次消费尝试。"""

    name = "junk-src"
    priority = 5

    def __init__(self):
        self.calls = 0
        self.concurrent_probe = None

    def search(self, query, *, limit=10, time_range=None):
        self.calls += 1
        # 探测进行中：第二个消费者再消费半开名额 —— 必须被拒（防惊群）
        self.concurrent_probe = health.try_half_open(self.name)
        return list(_JUNK)


class _OkProvider(SearchProvider):
    """返回与查询相关的结果（守门通过）。"""

    name = "ok-src"
    priority = 5

    def __init__(self):
        self.calls = 0

    def search(self, query, *, limit=10, time_range=None):
        self.calls += 1
        return [SearchResult(title="马克思主义基本原理复习要点",
                             url="https://yz.example.edu.cn/marx",
                             snippet="基本原理与概念框架")]


class _FakeCache:
    def __init__(self):
        self.entries = {}

    def get(self, provider, query, limit, time_range, allow_stale=False):
        return self.entries.get((provider, query))

    def put(self, provider, query, limit, results, time_range):
        self.entries[(provider, query)] = list(results)


def _svc(provider):
    return SearchService(providers=[provider])


# ══════════════════════════════════════════════════════════════════════
# S1：软性反爬丢弃 → 阈值型冷却（不立即；连续两轮才冷却）
# ══════════════════════════════════════════════════════════════════════


def test_s1_junk_discard_is_failure_but_not_immediate_cooldown():
    """一轮全垃圾：如实记为失败源，但**不**立即冷却（阈值型首轮不触发）。"""
    svc = _svc(_JunkProvider())
    resp = svc.search(SearchQuery(text=_QUERY, limit=5))

    failed = dict(resp.providers_failed)
    assert "junk-src" in failed and "无关" in failed["junk-src"]
    assert not health.is_cooling("junk-src"), (
        "软性反爬是启发式判定，一次误判不该让源停摆 10 分钟（R3 教训）")


def test_s1_two_junk_rounds_enter_cooldown():
    """核心修复：连续两轮全垃圾 → 进冷却（修复前永远不冷却，Bing 每轮白试）。

    本用例同时钉住「mark_healthy 移到守门之后」：若有人把 mark_healthy 挪回
    守门之前，失败计数每轮被清零，第二条断言必红。
    """
    provider = _JunkProvider()
    svc = _svc(provider)

    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert not health.is_cooling("junk-src")
    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert health.is_cooling("junk-src"), (
        "连续两轮 200+全垃圾必须进冷却（修复前丢弃分支不落账=永不冷却）")


def test_s1_healthy_result_clears_failure_count():
    """守门通过 → 失败计数清零（note_failure 一次后不应残留）。"""
    health.note_failure("ok-src", "疑似抖动")
    assert health.policy().failures("ok-src") == 1

    svc = _svc(_OkProvider())
    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert health.policy().failures("ok-src") == 0, "守门通过必须清零失败计数"


def test_s1_empty_result_is_healthy_not_failure():
    """真实无结果（空列表）是正常结果：必须清零失败计数、不进冷却。"""

    class _Empty(SearchProvider):
        name = "empty-src"
        priority = 5

        def search(self, query, *, limit=10, time_range=None):
            return []

    health.note_failure("empty-src", "疑似抖动")
    _svc(_Empty()).search(SearchQuery(text=_QUERY, limit=5))
    assert health.policy().failures("empty-src") == 0
    assert not health.is_cooling("empty-src")


# ══════════════════════════════════════════════════════════════════════
# S2：冷却期满只放行一次半开探测
# ══════════════════════════════════════════════════════════════════════


def _probe_policy(monkeypatch, clock, base=60.0):
    pol = CooldownPolicy(base_seconds=base, max_seconds=3600.0,
                         failure_threshold=2, monotonic=clock, time_source=clock)
    monkeypatch.setattr(health, "policy", lambda: pol)
    return pol


def test_s2_cooldown_expiry_releases_exactly_one_probe(monkeypatch):
    """冷却期满：恰好放行一次探测；探测进行中第二次消费被拒（防惊群）。

    修复前 ``try_half_open`` 零调用：期满后过滤链全量放行，并发请求同时涌向
    刚解冻的源。
    """
    clk = _Clock()
    pol = _probe_policy(monkeypatch, clk)
    pol.force_cooldown("junk-src")

    provider = _JunkProvider()
    svc = _svc(provider)
    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 0, "冷却期内不得发请求"

    clk.advance(61)                          # 冷却期满
    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 1, "期满应恰好放行一次探测"
    assert provider.concurrent_probe is False, (
        "半开在途时第二次消费必须被拒（否则并发下等于冷却从未生效）")


def test_s2_probe_failure_reenters_cooldown(monkeypatch):
    """探测失败（垃圾）→ 强制续冷，下一轮不再放行（名额不得悬空）。"""
    clk = _Clock()
    pol = _probe_policy(monkeypatch, clk)
    pol.force_cooldown("junk-src")

    provider = _JunkProvider()
    svc = _svc(provider)
    clk.advance(61)
    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 1
    assert health.is_cooling("junk-src"), "探测失败必须强制续冷"

    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 1, "续冷期间不得再放行"


def test_s2_probe_success_recovers_source(monkeypatch):
    """探测成功 → 清零恢复，后续轮次正常放行（冷却不得把源永久停摆）。"""
    clk = _Clock()
    pol = _probe_policy(monkeypatch, clk)
    pol.force_cooldown("ok-src")

    provider = _OkProvider()
    svc = _svc(provider)
    clk.advance(61)
    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 1
    assert not health.is_cooling("ok-src"), "探测成功后冷却应解除"

    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 2, "恢复后的源应正常参与每一轮"


def test_s2_cache_hit_does_not_consume_probe_slot(monkeypatch):
    """缓存命中不需要探测：不得浪费半开名额（否则源会静默死锁）。"""
    clk = _Clock()
    pol = _probe_policy(monkeypatch, clk)
    pol.force_cooldown("junk-src")

    provider = _JunkProvider()
    svc = _svc(provider)
    svc._cache = _FakeCache()
    svc._cache.entries[("junk-src", _QUERY)] = list(_JUNK)

    clk.advance(61)
    resp = svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 0, "缓存命中不应发请求"
    assert any("缓存" in u for u in resp.providers_used)

    del svc._cache.entries[("junk-src", _QUERY)]
    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 1, "名额未被浪费，清缓存后仍应放行一次探测"


def test_s2_unexpected_exception_does_not_strand_half_open(monkeypatch):
    """探测抛未预期异常 → 也须落账续冷，不得让半开名额悬空（静默死锁）。"""

    class _Boom(SearchProvider):
        name = "boom-src"
        priority = 5

        def __init__(self):
            self.calls = 0

        def search(self, query, *, limit=10, time_range=None):
            self.calls += 1
            raise RuntimeError("内部实现错误（模拟）")

    clk = _Clock()
    pol = _probe_policy(monkeypatch, clk)
    pol.force_cooldown("boom-src")

    provider = _Boom()
    svc = _svc(provider)
    clk.advance(61)
    resp = svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 1
    assert any("未预期异常" in w for _, w in resp.providers_failed)
    assert health.is_cooling("boom-src"), "未预期异常也必须续冷（防名额悬空）"

    svc.search(SearchQuery(text=_QUERY, limit=5))
    assert provider.calls == 1, "续冷期间不得再放行"


# ══════════════════════════════════════════════════════════════════════
# S3：搜狗 published_at 转 ISO + time_range 客户端过滤
# ══════════════════════════════════════════════════════════════════════

_OLD_TS = 1451577600                       # 2016-01-01（确定超期）
_NEW_TS = int(time.time()) - 86400         # 1 天前（各窗口内）


def _sogou_html(include_old=True, include_new=True, include_nodate=False):
    blocks = []
    if include_old:
        blocks.append(f"""
  <li><div class="txt-box">
    <h3><a href="/link?url=old&amp;type=2">示例旧文标题</a></h3>
    <p class="txt-info">旧文摘要。</p>
    <div class="s-p"><a class="account">旧账号</a>
      <span class="s2">timeConvert('{_OLD_TS}')</span></div>
  </div></li>""")
    if include_new:
        blocks.append(f"""
  <li><div class="txt-box">
    <h3><a href="/link?url=new&amp;type=2">示例新文标题</a></h3>
    <p class="txt-info">新文摘要。</p>
    <div class="s-p"><a class="account">新账号</a>
      <span class="s2">timeConvert('{_NEW_TS}')</span></div>
  </div></li>""")
    if include_nodate:
        blocks.append("""
  <li><div class="txt-box">
    <h3><a href="/link?url=nodate&amp;type=2">无日期标题</a></h3>
    <p class="txt-info">无日期摘要。</p>
  </div></li>""")
    return '<ul class="news-list">' + "".join(blocks) + "</ul>"


@pytest.fixture()
def patch_sogou_text(monkeypatch):
    def _apply(html_text):
        monkeypatch.setattr(sogou_mod, "get_text", lambda *a, **k: html_text)
    return _apply


def test_s3_published_at_is_iso_date(patch_sogou_text):
    """裸 Unix 时间戳 → ISO 日期（本地时区，与 skills 层同口径）。"""
    patch_sogou_text(_sogou_html())
    results = sogou_mod.SogouWeixinProvider().search("示例查询", limit=5)

    exp_old = datetime.fromtimestamp(_OLD_TS).strftime("%Y-%m-%d")
    exp_new = datetime.fromtimestamp(_NEW_TS).strftime("%Y-%m-%d")
    assert [r.published_at for r in results] == [exp_old, exp_new]
    assert all(not r.published_at.isdigit() for r in results), (
        "不得再输出裸 Unix 时间戳（违反 models.py 的 ISO 日期契约）")


def test_s3_time_range_filters_old_results(patch_sogou_text):
    """time_range=year：超期结果被客户端过滤（此前参数被完全忽略）。"""
    patch_sogou_text(_sogou_html())
    results = sogou_mod.SogouWeixinProvider().search(
        "示例查询", limit=5, time_range="year")

    exp_new = datetime.fromtimestamp(_NEW_TS).strftime("%Y-%m-%d")
    assert [r.published_at for r in results] == [exp_new], (
        "2016 年的旧文必须被 time_range 过滤，不得挤占 limit 名额")


def test_s3_time_range_keeps_items_without_date(patch_sogou_text):
    """无日期条目保留：无法证明其超期，误杀会损失召回（召回优先口径）。"""
    patch_sogou_text(_sogou_html(include_old=False, include_nodate=True))
    results = sogou_mod.SogouWeixinProvider().search(
        "示例查询", limit=5, time_range="week")

    assert len(results) == 2, "无日期条目必须保留"
    assert results[-1].published_at == ""


def test_s3_invalid_timestamp_yields_empty(patch_sogou_text):
    """越界/非法时间戳 → 空串（不得转出 1970 年幻觉日期）。"""
    html_text = """
<ul class="news-list">
  <li><div class="txt-box">
    <h3><a href="/link?url=a&amp;type=2">占位标题甲</a></h3>
    <div class="s-p"><span class="s2">timeConvert('999')</span></div>
  </div></li>
  <li><div class="txt-box">
    <h3><a href="/link?url=b&amp;type=2">占位标题乙</a></h3>
    <div class="s-p"><span class="s2">timeConvert('9999999999999999')</span></div>
  </div></li>
</ul>"""
    patch_sogou_text(html_text)
    results = sogou_mod.SogouWeixinProvider().search("示例查询", limit=5)
    assert [r.published_at for r in results] == ["", ""]


# ══════════════════════════════════════════════════════════════════════
# S4：改版（阈值型）vs 反爬/限流（立即冷却）分流
# ══════════════════════════════════════════════════════════════════════


class _Named:
    name = "sogou-weixin"


def test_s4_revision_reason_is_threshold_based():
    """改版类文案：首次不冷却，连续两次才冷却（确定性故障不再被反复惩罚）。"""
    SearchService._maybe_cooldown(_Named(), "页面结构未匹配到文章块（疑似页面改版）")
    assert not health.is_cooling("sogou-weixin"), (
        "改版是确定性解析故障，反复请求不会有收益也不该被冷却惩罚")

    SearchService._maybe_cooldown(_Named(), "页面结构未匹配到文章块（疑似页面改版）")
    assert health.is_cooling("sogou-weixin"), "连续两次改版失败应进入阈值型冷却"


def test_s4_anti_bot_reason_still_immediate():
    """反爬/限流文案：首次即冷却（确定性判定，等第二次只会多挨一次封）。"""
    SearchService._maybe_cooldown(
        _Named(), "搜狗要求验证码/限制访问（反爬），本次跳过该源")
    assert health.is_cooling("sogou-weixin")


def test_s4_plain_network_error_is_threshold_based():
    """普通网络错误（非反爬非改版）：阈值型（note_failure 的预留语义）。"""
    SearchService._maybe_cooldown(_Named(), "Bing 网络请求失败: timed out")
    assert not health.is_cooling("sogou-weixin")
    SearchService._maybe_cooldown(_Named(), "Bing 网络请求失败: timed out")
    assert health.is_cooling("sogou-weixin")


def test_s4_sogou_revision_message_is_split(patch_sogou_text):
    """sogou 改版分支文案不含「限流」字样（与反爬文案彻底拆分）。"""
    patch_sogou_text("<html>页面结构变了，没有任何 txt-box</html>")
    with pytest.raises(ProviderError) as exc:
        sogou_mod.SogouWeixinProvider().search("示例查询")

    msg = str(exc.value)
    assert "改版" in msg
    assert "限流" not in msg, "改版与限流必须走独立文案/独立处置"


# ══════════════════════════════════════════════════════════════════════
# S5：Bing 随机 UA 池 + cn.bing.com 备用端点
# ══════════════════════════════════════════════════════════════════════

_BING_HTML = """
<li class="b_algo"><h2><a href="https://yz.example.edu.cn/a">示例结果标题</a></h2>
<div class="b_caption"><p>示例摘要。</p></div></li>
"""
_BING_ANTI_BOT = "<html>unusual traffic detected, please verify</html>"


def test_s5_bing_ua_rotates_from_pool(monkeypatch):
    """UA 必须来自随机池（此前是单一固定常量），多次请求能观察到轮换。"""
    captured = []

    def fake_get_text(url, **kw):
        captured.append(kw.get("headers") or {})
        return _BING_HTML

    monkeypatch.setattr(bing_mod, "get_text", fake_get_text)
    bing_mod.BingProvider().search("示例查询")
    assert captured[-1].get("User-Agent") in USER_AGENTS

    seen = set()
    for _ in range(30):
        bing_mod.BingProvider().search("示例查询")
        seen.add(captured[-1].get("User-Agent"))
    assert len(seen) >= 2, (
        f"30 次请求只观察到 {len(seen)} 种 UA —— 随机轮换未生效"
        "（全同的概率约 (1/5)^29，可排除）")


def test_s5_bing_falls_back_to_cn_endpoint_on_network_error(monkeypatch):
    """主端点网络异常 → 自动换 cn.bing.com 再试，而不是整个源失败。"""
    calls = []

    def fake_get_text(url, **kw):
        calls.append(url)
        if "www.bing.com" in url:
            raise RuntimeError("connection reset by peer")
        return _BING_HTML

    monkeypatch.setattr(bing_mod, "get_text", fake_get_text)
    results = bing_mod.BingProvider().search("示例查询")

    assert [u.split("?")[0] for u in calls] == [
        "https://www.bing.com/search", "https://cn.bing.com/search"]
    assert len(results) == 1


def test_s5_bing_falls_back_to_cn_endpoint_on_anti_bot(monkeypatch):
    """主端点返回反爬验证页 → 换端点再试（反爬不再等于整个源判死）。"""
    calls = []

    def fake_get_text(url, **kw):
        calls.append(url)
        if "www.bing.com" in url:
            return _BING_ANTI_BOT
        return _BING_HTML

    monkeypatch.setattr(bing_mod, "get_text", fake_get_text)
    results = bing_mod.BingProvider().search("示例查询")

    assert len(calls) == 2
    assert len(results) == 1


def test_s5_bing_reports_error_when_both_endpoints_fail(monkeypatch):
    """两处端点都失败才如实报错（网络失败文案保留）。"""

    def fake_get_text(url, **kw):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(bing_mod, "get_text", fake_get_text)
    with pytest.raises(ProviderError) as exc:
        bing_mod.BingProvider().search("示例查询")
    assert "网络请求失败" in str(exc.value)


def test_s5_bing_both_anti_bot_reports_anti_bot(monkeypatch):
    """两处端点都返回反爬页 → 报反爬（判定优先级：反爬 > 网络 > 结构）。"""

    def fake_get_text(url, **kw):
        return _BING_ANTI_BOT

    monkeypatch.setattr(bing_mod, "get_text", fake_get_text)
    with pytest.raises(ProviderError) as exc:
        bing_mod.BingProvider().search("示例查询")
    assert "反爬" in str(exc.value)
