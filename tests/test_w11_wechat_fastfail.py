# -*- coding: utf-8 -*-
"""[W11 微信源快速失败] 搜狗微信链路参数收紧 + health 冷却接线。

背景（多角色实测）
------------------
compare 卡死场景的 stderr 噪音源：「微信文章检索源异常」每次失败耗时数十秒——
sogou provider 原 timeout=12 × max_retries=3（默认）最坏 ~50s；
wechat_searcher 链路 get_text(timeout=10) + urlopen(timeout=10) 兜底，
双页最坏 100s+ 且无冷却记忆（同进程反复硬试）。

本批：
  * sogou provider：timeout 6 × max_retries 1（最坏 ~13s）；
  * wechat_searcher._search_sogou：冷却表检查（跳过）+ 网络失败/反爬页即时
    标记冷却 + 双页共享冷却（第一页失败后第二页跳过）；
  * wechat_searcher._search_bing：timeout 10→6（兜底源不接冷却，避免链路全空）。

全程离线：mock get_text / urlopen，不发起网络请求。
"""

import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.search import health  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cooldown():
    health.reset()
    yield
    health.reset()


# ── 1. sogou provider 参数收紧 ─────────────────────────────────────────


def test_sogou_provider_uses_fast_params(monkeypatch):
    """get_text 收到 timeout=6 / max_retries=1（原 12 / 3）。"""
    from tools.search.providers import sogou

    seen = {}

    def fake_get_text(url, **kwargs):
        seen.update(kwargs)
        return "<html>没有找到相关的微信公众号文章</html>"

    monkeypatch.setattr(sogou, "get_text", fake_get_text)
    out = sogou.SogouWeixinProvider().search("测试")

    assert out == []
    assert seen.get("timeout") == 6
    assert seen.get("max_retries") == 1


def test_sogou_provider_block_page_single_attempt(monkeypatch):
    """反爬页单次即抛（不进入重试）——ProviderError 且 get_text 只调 1 次。"""
    from tools.search.providers import sogou
    from tools.search.providers.base import ProviderError

    calls = []

    def fake_get_text(url, **kwargs):
        calls.append(url)
        return "<html>SourceVerifyCode 请协助验证</html>"

    monkeypatch.setattr(sogou, "get_text", fake_get_text)

    with pytest.raises(ProviderError):
        sogou.SogouWeixinProvider().search("测试")
    assert len(calls) == 1


# ── 2. wechat_searcher：冷却跳过 ───────────────────────────────────────


def test_searcher_skips_cooling_source(monkeypatch):
    """冷却中的源直接跳过：不发起任何请求 + last_errors 留痕。"""
    from tools.skills import wechat_searcher
    from tools.skills.wechat_searcher import WeChatSearchEngine
    from tools.search.providers import _http

    calls = []
    monkeypatch.setattr(_http, "get_text",
                        lambda url, **kw: calls.append(url) or "<html></html>")
    # 同时兜底 urlopen 也不应被调用
    monkeypatch.setattr(wechat_searcher, "safe_urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("冷却源不得发起请求")))

    health.mark_blocked("sogou-weixin", "此前反爬")
    engine = WeChatSearchEngine()
    out = engine._search_sogou("测试", 5, "year")

    assert out == []
    assert calls == []
    assert engine.last_errors and "冷却" in engine.last_errors[0]


# ── 3. wechat_searcher：失败即时标记 + 双页共享 ────────────────────────


def test_searcher_marks_blocked_on_network_failure(monkeypatch):
    """get_text 与 urlopen 双双失败 → 源进入冷却 + 留痕。"""
    from tools.skills import wechat_searcher
    from tools.skills.wechat_searcher import WeChatSearchEngine
    from tools.search.providers import _http

    monkeypatch.setattr(_http, "get_text",
                        lambda url, **kw: (_ for _ in ()).throw(
                            ConnectionError("模拟网络失败")))

    def boom(*a, **k):
        raise ConnectionError("模拟兜底失败")

    monkeypatch.setattr(wechat_searcher, "safe_urlopen", boom)

    engine = WeChatSearchEngine()
    out = engine._search_sogou("测试", 5, "year")

    assert out == []
    assert health.is_cooling("sogou-weixin")
    assert engine.last_errors


def test_searcher_second_call_skips_after_failure(monkeypatch):
    """首次失败已标记冷却 → 第二次调用不再发请求（双页/多校场景省时）。"""
    from tools.skills import wechat_searcher
    from tools.skills.wechat_searcher import WeChatSearchEngine
    from tools.search.providers import _http

    calls = []

    def fake_get_text(url, **kw):
        calls.append(url)
        raise ConnectionError("模拟网络失败")

    monkeypatch.setattr(_http, "get_text", fake_get_text)
    monkeypatch.setattr(wechat_searcher, "safe_urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ConnectionError("模拟兜底失败")))

    engine = WeChatSearchEngine()
    engine._search_sogou("测试", 5, "year")          # 第一次：失败并标记
    n_after_first = len(calls)
    assert n_after_first >= 1 and health.is_cooling("sogou-weixin")

    engine._search_sogou("测试2", 5, "year")         # 第二次：冷却跳过
    assert len(calls) == n_after_first, "冷却后不得再发起请求"


def test_searcher_marks_blocked_on_block_page(monkeypatch):
    """HTTP 200 但页面是反爬验证页（解析 0 条）→ 标记冷却。"""
    from tools.skills import wechat_searcher
    from tools.skills.wechat_searcher import WeChatSearchEngine
    from tools.search.providers import _http

    monkeypatch.setattr(_http, "get_text",
                        lambda url, **kw: "<html>请协助验证 antispider</html>")

    engine = WeChatSearchEngine()
    out = engine._search_sogou("测试", 5, "year")

    assert out == []
    assert health.is_cooling("sogou-weixin")


def test_searcher_fast_params(monkeypatch):
    """wechat_searcher 的 get_text 收到 timeout=6 / max_retries=1。"""
    from tools.skills.wechat_searcher import WeChatSearchEngine
    from tools.search.providers import _http

    seen = {}

    def fake_get_text(url, **kwargs):
        seen.update(kwargs)
        return "<html>没有找到相关的微信公众号文章</html>"

    monkeypatch.setattr(_http, "get_text", fake_get_text)

    engine = WeChatSearchEngine()
    engine._search_sogou("测试", 5, "three_years")   # 单页，避免二次调用
    assert seen.get("timeout") == 6
    assert seen.get("max_retries") == 1


# ── 4. Bing 兜底 timeout 收紧（不接冷却） ──────────────────────────────


def test_searcher_bing_timeout_fast(monkeypatch):
    from tools.skills import wechat_searcher
    from tools.skills.wechat_searcher import WeChatSearchEngine

    seen = {}

    class _Resp:
        def read(self, _limit=-1):
            return b"<html></html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(wechat_searcher, "safe_urlopen", fake_urlopen)

    engine = WeChatSearchEngine()
    engine._search_bing("测试", 5, "year")

    assert seen.get("timeout") == 6
    assert not health.is_cooling("bing"), "Bing 兜底源不应被标记冷却"
