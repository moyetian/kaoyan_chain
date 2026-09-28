# -*- coding: utf-8 -*-
"""tests/test_wechat_browser_fallback.py —— 微信采集浏览器兜底（P0）回归。

被测：``tools/skills/wechat_searcher.py`` 的 P0 扩展（整合方案 v2 §5 阶段 1）。

覆盖清单
--------
* 触发条件：搜狗 + Bing **双源均 0 条**且闸门允许才启用；任一源有结果不触发。
* 降级三态：闸门 off / 半态（库在浏览器不在）→ 零浏览器调用、行为与基线一致。
* 结果契约：来源通道标注 ``sogou_browser``；失败（BLOCKED）如实入 source_errors。
* 跳转还原回填：``resolved_links`` 按原链接**精确匹配**替换（无索引错位风险）。
* 正文兜底：HTTP 失败后用真实会话渲染；条数上限 ``BROWSER_ARTICLE_LIMIT``。
* 中断：``should_stop`` 传入浏览器链、在正文循环断点退出（GUI 关窗语义）。

隔离：全部 monkeypatch（假 manager + 假双源 + no-op 正文抓取），
不启动浏览器、不发任何网络请求。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.intelligence import fetcher as fetcher_mod  # noqa: E402
from tools.skills import wechat_searcher as ws_mod  # noqa: E402

GATE = "KY_BROWSER_ACQUISITION"

#: 能被 ``_parse_sogou_results`` 解析的最小结果页（两条，链接为搜狗 /link 址）
SOGOU_HTML = (
    "<html><body>"
    '<div class="txt-box">'
    '<h3><a href="https://weixin.sogou.com/link?url=AAA&token=T1">考研经验分享一</a></h3>'
    '<p class="txt-info">摘要一</p>'
    '<a class="account">考研公众号甲</a>'
    "</div>"
    '<div class="txt-box">'
    '<h3><a href="https://weixin.sogou.com/link?url=BBB&token=T2">考研经验分享二</a></h3>'
    '<p class="txt-info">摘要二</p>'
    "</div>"
    "</body></html>"
)


def _ok_result(html="", resolved=None, status="OK"):
    return fetcher_mod.FetchResult(
        url="", status_code=200, content=html, is_valid=(status == "OK"),
        access_status=status, headers={}, resolved_links=resolved)


def _item(title, url, platform="bing"):
    return ws_mod.WeChatArticleItem(title=title, url=url, source_platform=platform)


class FakeManager:
    """假的 BrowserPluginManager：只记录调用，不启动任何浏览器。"""

    def __init__(self, enabled=True, available=True, result=None):
        self.enabled = enabled
        self.available = available
        self.result = result if result is not None else _ok_result("")
        self.calls = []

    def is_enabled(self):
        return self.enabled

    def is_available(self):
        return self.available

    def fetch_with_browser(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.result


def _patch_sources(monkeypatch, sogou=(), bing=(), local=()):
    """类级替换双源与本地缓存（对 engine.search 与 wechat_search 均生效）。"""
    monkeypatch.setattr(ws_mod.WeChatSearchEngine, "_search_sogou",
                        lambda self, kw, n, time_range=None: list(sogou))
    monkeypatch.setattr(ws_mod.WeChatSearchEngine, "_search_bing",
                        lambda self, kw, n, time_range=None: list(bing))
    monkeypatch.setattr(ws_mod.WeChatSearchEngine, "search_local_cache",
                        lambda self, kw, n: list(local))


def _patch_manager(monkeypatch, manager):
    monkeypatch.setattr(ws_mod, "_load_browser_manager", lambda: manager)


# ─────────────────────────────────────────────────────────────────────────────
# 触发条件与降级三态
# ─────────────────────────────────────────────────────────────────────────────

def test_gate_off_no_browser_call(monkeypatch):
    manager = FakeManager(enabled=False)
    _patch_sources(monkeypatch)
    _patch_manager(monkeypatch, manager)
    assert ws_mod.WeChatSearchEngine().search("测试关键词") == []
    assert manager.calls == []


def test_half_state_unavailable_no_call(monkeypatch):
    """半态（库在浏览器不在 / 闸门 on 但内核探测失败）→ 零调用。"""
    manager = FakeManager(available=False)
    _patch_sources(monkeypatch)
    _patch_manager(monkeypatch, manager)
    assert ws_mod.WeChatSearchEngine().search("测试关键词") == []
    assert manager.calls == []


def test_double_failure_triggers_browser_and_tags_platform(monkeypatch):
    manager = FakeManager(result=_ok_result(SOGOU_HTML))
    _patch_sources(monkeypatch)
    _patch_manager(monkeypatch, manager)

    out = ws_mod.WeChatSearchEngine().search("测试关键词")

    assert len(out) == 2
    assert all(i.source_platform == "sogou_browser" for i in out)
    assert manager.calls[0]["url"].startswith("https://weixin.sogou.com/weixin?")
    assert "type=2" in manager.calls[0]["url"]
    assert manager.calls[0]["resolve_links_selector"] == ws_mod._SOGOU_RESULT_LINK_SELECTOR
    assert manager.calls[0]["resolve_budget_sec"] == 10.0


def test_bing_result_suppresses_fallback(monkeypatch):
    manager = FakeManager(result=_ok_result(SOGOU_HTML))
    _patch_sources(monkeypatch, bing=[_item("b1", "https://mp.weixin.qq.com/s/b1")])
    _patch_manager(monkeypatch, manager)

    out = ws_mod.WeChatSearchEngine().search("测试关键词")
    assert manager.calls == []
    assert any(i.source_platform == "bing" for i in out)


def test_sogou_partial_result_suppresses_fallback(monkeypatch):
    manager = FakeManager(result=_ok_result(SOGOU_HTML))
    _patch_sources(monkeypatch,
                   sogou=[_item("s1", "https://weixin.sogou.com/link?url=S1")])
    _patch_manager(monkeypatch, manager)

    ws_mod.WeChatSearchEngine().search("测试关键词")
    assert manager.calls == []


def test_blocked_reports_source_error(monkeypatch):
    """B 型（IP 频率验证码）如实 BLOCKED：不伪造结果、原因入 source_errors。"""
    manager = FakeManager(result=_ok_result("", status="BLOCKED"))
    _patch_sources(monkeypatch)
    _patch_manager(monkeypatch, manager)

    engine = ws_mod.WeChatSearchEngine()
    assert engine.search("测试关键词") == []
    assert any("浏览器兜底" in e for e in engine.last_errors)
    assert any("BLOCKED" in e for e in engine.last_errors)


def test_empty_parse_reports_source_empty(monkeypatch):
    manager = FakeManager(result=_ok_result("<html>no results</html>"))
    _patch_sources(monkeypatch)
    _patch_manager(monkeypatch, manager)

    engine = ws_mod.WeChatSearchEngine()
    assert engine.search("测试关键词") == []
    assert any("解析 0 条" in e for e in engine.last_errors)


def test_link_resolution_backfill(monkeypatch):
    """跳转还原：映射命中的 /link 址替换为真链；未命中的保持原样。"""
    resolved = {"https://weixin.sogou.com/link?url=AAA&token=T1":
                "https://mp.weixin.qq.com/s/REAL"}
    manager = FakeManager(result=_ok_result(SOGOU_HTML, resolved=resolved))
    _patch_sources(monkeypatch)
    _patch_manager(monkeypatch, manager)

    out = ws_mod.WeChatSearchEngine().search("测试关键词")
    urls = {i.url for i in out}
    assert "https://mp.weixin.qq.com/s/REAL" in urls
    assert "https://weixin.sogou.com/link?url=BBB&token=T2" in urls


def test_should_stop_forwarded_to_browser_search(monkeypatch):
    manager = FakeManager(result=_ok_result(SOGOU_HTML))
    _patch_sources(monkeypatch)
    _patch_manager(monkeypatch, manager)

    stop = lambda: False  # noqa: E731
    ws_mod.WeChatSearchEngine().search("测试关键词", should_stop=stop)
    assert manager.calls[0]["should_stop"] is stop


def test_search_positional_compat(monkeypatch):
    """位置参数调用兼容（search(kw, n, source, time_range) 顺序不变）。"""
    manager = FakeManager(enabled=False)
    _patch_sources(monkeypatch)
    _patch_manager(monkeypatch, manager)
    assert ws_mod.WeChatSearchEngine().search("测试关键词", 5, "auto", "year") == []


# ─────────────────────────────────────────────────────────────────────────────
# 正文浏览器兜底
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_article_via_browser_success(monkeypatch):
    body = "正文内容。" * 30
    html = f'<html><div id="js_content">{body}</div><!--end--></html>'
    manager = FakeManager(result=_ok_result(html))
    _patch_manager(monkeypatch, manager)

    fetcher = ws_mod.WeChatArticleFetcher()
    item = ws_mod.WeChatArticleItem(title="t", url="https://mp.weixin.qq.com/s/x")
    fetcher.fetch_article_via_browser(item)

    assert item.fetched is True
    assert "正文内容" in item.content_markdown
    assert len(manager.calls) == 1


def test_fetch_article_via_browser_blocked_keeps_item(monkeypatch):
    manager = FakeManager(result=_ok_result("", status="BLOCKED"))
    _patch_manager(monkeypatch, manager)

    fetcher = ws_mod.WeChatArticleFetcher()
    item = ws_mod.WeChatArticleItem(title="t", url="https://mp.weixin.qq.com/s/x")
    fetcher.fetch_article_via_browser(item)

    assert item.fetched is False
    assert item.summary == ""          # 保持原状，不改写失败标注
    assert item.content_markdown == ""


def test_fetch_article_via_browser_skips_non_http(monkeypatch):
    manager = FakeManager()
    _patch_manager(monkeypatch, manager)

    fetcher = ws_mod.WeChatArticleFetcher()
    item = ws_mod.WeChatArticleItem(title="t", url="file:///tmp/a.md")
    fetcher.fetch_article_via_browser(item)
    assert manager.calls == []


def test_article_fallback_limit_in_pipeline(monkeypatch):
    """正文兜底条数上限：5 条 HTTP 失败只触发 BROWSER_ARTICLE_LIMIT 次浏览器渲染。"""
    manager = FakeManager(result=_ok_result(""))
    items = [_item(f"t{i}", f"https://mp.weixin.qq.com/s/{i}") for i in range(5)]
    _patch_sources(monkeypatch, bing=items)
    _patch_manager(monkeypatch, manager)
    monkeypatch.setattr(ws_mod.WeChatArticleFetcher, "fetch_article",
                        lambda self, item: item)   # HTTP 抓取 no-op（保持未抓取）

    res = ws_mod.wechat_search("测试关键词", max_results=5, fetch_content=True)

    assert res["total"] == 5
    assert len(manager.calls) == ws_mod.BROWSER_ARTICLE_LIMIT


def test_article_fallback_gate_off_no_calls(monkeypatch):
    manager = FakeManager(enabled=False)
    items = [_item("t", "https://mp.weixin.qq.com/s/1")]
    _patch_sources(monkeypatch, bing=items)
    _patch_manager(monkeypatch, manager)
    monkeypatch.setattr(ws_mod.WeChatArticleFetcher, "fetch_article",
                        lambda self, item: item)

    ws_mod.wechat_search("测试关键词", fetch_content=True)
    assert manager.calls == []


def test_should_stop_breaks_article_loop(monkeypatch):
    """GUI 关窗中断：停止回调为真时正文循环第一个检查点即退出。"""
    calls = {"n": 0}

    def _fetch(self, item):
        calls["n"] += 1
        return item

    manager = FakeManager()
    _patch_sources(monkeypatch, bing=[_item("t", "https://mp.weixin.qq.com/s/1")])
    _patch_manager(monkeypatch, manager)
    monkeypatch.setattr(ws_mod.WeChatArticleFetcher, "fetch_article", _fetch)

    res = ws_mod.wechat_search("测试关键词", fetch_content=True,
                               should_stop=lambda: True)
    assert calls["n"] == 0
    assert res["total"] == 1
    assert manager.calls == []


# ─────────────────────────────────────────────────────────────────────────────
# 健康自检文案
# ─────────────────────────────────────────────────────────────────────────────

def test_health_check_mentions_optional_enhancement():
    h = ws_mod.health_check()
    assert h["status"] == "READY"
    assert "可选增强" in h["reason"]
