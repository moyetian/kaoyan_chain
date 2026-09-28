# -*- coding: utf-8 -*-
"""tests/test_p1_two_tier_fetch.py —— [P1] 两级采集通用化回归（整合方案 v2 §5 阶段 2）。

被测
----
* ``tools/intelligence/fetcher.py``
  * :func:`fetch_with_fallback`（HTTP 快路径 → 浏览器升级）
  * :meth:`BrowserPluginManager.status`（聚合可用状态）
  * :meth:`BrowserPluginManager._classify_resource` 与 ``resources`` 元信息登记
* ``tools/agent/tools_impl.py``
  * ``fetch_url`` 的 ``mode=auto|http|browser``
  * ``download_file`` 工具（扩展名白名单 / 路径约束 / 落盘登记）

不变式（本文件重点钉住）
--------------------
1. **默认零新增开销**：``mode="http"`` 与 HTTP 成功时绝不触碰浏览器。
2. **升级只在真受阻时**：仅 ``BROWSER_REQUIRED`` / ``HTTP_403`` 才升级；
   超时/证书错误等升级无用，不许白跑。
3. **升级失败不丢原结果**：浏览器失败时保留 HTTP 结果，原因进 ``escalation``。
4. **闸门优先**：``KY_BROWSER_ACQUISITION`` 未开时连尝试都不做（``BROWSER_DISABLED``）。
5. **资源嗅探只登记元信息**：``resources`` 里绝不出现 body。
6. **下载落盘受沙箱约束**：白名单扩展名 + 必须落在 ``data/downloads/`` 内。

隔离约定：全 mock，不发起真实网络请求、不启动真实浏览器；
测试数据一律中性占位（``*.example.test``）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tools.intelligence import fetcher as fetcher_mod
from tools.intelligence.fetcher import (
    BrowserPluginManager,
    FetchResult,
    HTTPFetcher,
)

_CLS = BrowserPluginManager
_GATE = _CLS.GATE_ENV
_URL = "https://news.example.test/notice/1.html"


def _allow_all(url, pin=None):
    """受控放行替身：不解析 DNS、不判定网段。"""
    return url


def _http_result(status: str = "OK", code: int = 200, body: str = "<html>ok</html>",
                 valid: bool = True) -> FetchResult:
    return FetchResult(url=_URL, status_code=code, content=body, is_valid=valid,
                       access_status=status, headers={}, tier="http")


def _browser_result(status: str = "OK", body: str = "<html>rendered</html>",
                    valid: bool = True) -> FetchResult:
    return FetchResult(url=_URL, status_code=200 if valid else 0, content=body,
                       is_valid=valid, access_status=status, headers={},
                       tier="browser")


# ─────────────────────────────────────────────────────────────────────────────
# 精简 FakePlaywright 栈（只实现 fetch_with_browser 需要的面）
# ─────────────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status=200, url="", headers=None):
        self.status = status
        self.url = url
        self.headers = headers or {}


class FakeStack:
    """模拟 ``sync_playwright()`` 链路；自身充当 p / chromium / browser / context / page。"""

    def __init__(self, html="<html><body>ok</body></html>", status=200,
                 responses=None):
        self._html = html
        self._status = status
        self.responses_to_emit = list(responses or [])
        self.browser_closed = False
        self.goto_urls = []
        self.response_handlers = []
        self.url = _URL

    # ---- playwright 上下文 ----
    def sync_playwright(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def chromium(self):
        return self

    # ---- browser / context / page ----
    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self

    def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self

    def new_page(self):
        return self

    def route(self, pattern, handler):
        return None

    def route_web_socket(self, pattern, handler):
        return None

    def add_init_script(self, script):
        return None

    def set_default_timeout(self, ms):
        self.default_timeout_ms = ms

    def on(self, event, handler):
        if event == "response":
            self.response_handlers.append(handler)

    def goto(self, url, **kwargs):
        self.goto_urls.append(url)
        for resp in self.responses_to_emit:
            for h in self.response_handlers:
                h(resp)
        return _FakeResponse(status=self._status, url=url)

    def content(self):
        return self._html

    def close(self):
        self.browser_closed = True


@pytest.fixture
def browser_ready(monkeypatch):
    """把浏览器层设为「可用」：闸门开 + 库在 + 内核在 + SSRF 放行。"""
    monkeypatch.setenv(_GATE, "on")
    monkeypatch.setattr(_CLS, "_has_library", staticmethod(lambda: True))
    monkeypatch.setattr(_CLS, "_detect_channel", staticmethod(lambda: "msedge"))
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)
    return None


@pytest.fixture
def fake_playwright(monkeypatch):
    """向 sys.modules 注入假 playwright；返回工厂（用指定栈替换）。"""
    installed = {}

    def _install(stack: FakeStack):
        pw = types.ModuleType("playwright")
        api = types.ModuleType("playwright.sync_api")
        api.sync_playwright = stack.sync_playwright
        pw.sync_api = api
        monkeypatch.setitem(sys.modules, "playwright", pw)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
        installed["stack"] = stack
        return stack

    return _install


# ─────────────────────────────────────────────────────────────────────────────
# 一、mode 三态与升级触发条件
# ─────────────────────────────────────────────────────────────────────────────

def test_mode_http_never_touches_browser(monkeypatch, browser_ready, fake_playwright):
    """mode=http：即使 HTTP 403 且浏览器可用，也绝不启动浏览器（默认零开销）。"""
    seen = {"browser": 0, "http": 0}

    def fake_http(self, url, **kw):
        seen["http"] += 1
        return _http_result("HTTP_403", code=403, valid=False)

    def fake_browser(url, **kw):
        seen["browser"] += 1
        return _browser_result()

    monkeypatch.setattr(HTTPFetcher, "fetch", fake_http)
    monkeypatch.setattr(_CLS, "fetch_with_browser", staticmethod(fake_browser))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="http")
    assert res.access_status == "HTTP_403"
    assert res.tier == "http"
    assert res.escalation is None
    assert seen == {"browser": 0, "http": 1}


def test_mode_browser_skips_http(monkeypatch, browser_ready, fake_playwright):
    """mode=browser：不白跑一次 HTTP。"""
    seen = {"http": 0}

    monkeypatch.setattr(HTTPFetcher, "fetch", lambda self, url, **kw: (
        seen.__setitem__("http", seen["http"] + 1), _http_result())[1])
    stack = fake_playwright(FakeStack(html="<html>rendered</html>"))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="browser")
    assert seen["http"] == 0
    assert res.tier == "browser" and res.is_valid
    assert stack.browser_closed is True  # S3


def test_auto_escalates_on_403(monkeypatch, browser_ready, fake_playwright):
    """403 + 闸门开 + 浏览器可用 → 升级成功，结果为浏览器层。"""
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("HTTP_403", code=403, valid=False))
    fake_playwright(FakeStack(html="<html>rendered</html>"))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.tier == "browser"
    assert res.is_valid and res.access_status == "OK"
    assert res.escalation == "OK"
    assert res.content == "<html>rendered</html>"


def test_auto_escalates_on_browser_required(monkeypatch, browser_ready, fake_playwright):
    """SPA 空壳（BROWSER_REQUIRED）同样是升级触发条件。"""
    monkeypatch.setattr(HTTPFetcher, "fetch", lambda self, url, **kw: _http_result(
        "BROWSER_REQUIRED", body='<div id="app"></div>', valid=False))
    fake_playwright(FakeStack(html="<html>spa</html>"))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.tier == "browser" and res.escalation == "OK"


def test_auto_no_escalation_when_http_ok(monkeypatch, browser_ready, fake_playwright):
    """HTTP 已经拿到有效内容 → 不升级（升级只用于救「拿不到」的场景）。"""
    seen = {"browser": 0}
    monkeypatch.setattr(HTTPFetcher, "fetch", lambda self, url, **kw: _http_result())
    monkeypatch.setattr(_CLS, "fetch_with_browser",
                        staticmethod(lambda url, **kw: (seen.__setitem__(
                            "browser", seen["browser"] + 1), _browser_result())[1]))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.tier == "http" and res.escalation is None
    assert seen["browser"] == 0


def test_auto_no_escalation_on_timeout(monkeypatch, browser_ready, fake_playwright):
    """TIMEOUT 不在可升级清单内 —— 换浏览器也大概率解不了，不白跑。"""
    seen = {"browser": 0}
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("TIMEOUT", code=0, valid=False))
    monkeypatch.setattr(_CLS, "fetch_with_browser",
                        staticmethod(lambda url, **kw: (seen.__setitem__(
                            "browser", seen["browser"] + 1), _browser_result())[1]))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.access_status == "TIMEOUT" and res.escalation is None
    assert seen["browser"] == 0


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode 仅支持"):
        fetcher_mod.fetch_with_fallback(_URL, mode="turbo")


# ─────────────────────────────────────────────────────────────────────────────
# 二、升级不可用 / 失败时的降级口径
# ─────────────────────────────────────────────────────────────────────────────

def test_gate_off_records_disabled_and_keeps_http(monkeypatch):
    """闸门默认 off：连尝试都不做，如实记 BROWSER_DISABLED，保留 HTTP 原结果。"""
    monkeypatch.delenv(_GATE, raising=False)
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("HTTP_403", code=403, valid=False))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.tier == "http"
    assert res.access_status == "HTTP_403"
    assert res.escalation == "BROWSER_DISABLED"
    assert res.content == "<html>ok</html>"


def test_library_missing_records_not_installed(monkeypatch):
    monkeypatch.setenv(_GATE, "on")
    monkeypatch.setattr(_CLS, "_has_library", staticmethod(lambda: False))
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("HTTP_403", code=403, valid=False))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.escalation == "BROWSER_NOT_INSTALLED"


def test_no_kernel_records_unavailable(monkeypatch):
    monkeypatch.setenv(_GATE, "on")
    monkeypatch.setattr(_CLS, "_has_library", staticmethod(lambda: True))
    monkeypatch.setattr(_CLS, "_detect_channel", staticmethod(lambda: "UNAVAILABLE"))
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("HTTP_403", code=403, valid=False))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.escalation == "BROWSER_UNAVAILABLE"


def test_upgrade_failure_keeps_http_result(monkeypatch, browser_ready, fake_playwright):
    """浏览器层被反爬挡住 → 保留 HTTP 原结果（不丢），原因进 escalation。"""
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("HTTP_403", code=403, valid=False))
    fake_playwright(FakeStack(html="<html>antispider 请输入验证码</html>"))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.tier == "http"
    assert res.access_status == "HTTP_403"      # 原结果未被升级失败覆盖
    assert res.escalation == "BLOCKED"
    assert res.content == "<html>ok</html>"


def test_status_matrix(monkeypatch):
    """status() 四态必须与闸门 / 库 / 内核的组合一一对应。"""
    monkeypatch.delenv(_GATE, raising=False)
    assert _CLS.status() == "DISABLED"

    monkeypatch.setenv(_GATE, "on")
    monkeypatch.setattr(_CLS, "_has_library", staticmethod(lambda: False))
    assert _CLS.status() == "NOT_INSTALLED"

    monkeypatch.setattr(_CLS, "_has_library", staticmethod(lambda: True))
    monkeypatch.setattr(_CLS, "_detect_channel", staticmethod(lambda: "UNAVAILABLE"))
    assert _CLS.status() == "UNAVAILABLE"

    monkeypatch.setattr(_CLS, "_detect_channel", staticmethod(lambda: "msedge"))
    assert _CLS.status() == "READY"


# ─────────────────────────────────────────────────────────────────────────────
# 三、资源分类嗅探（JSON / Media / PDF / Doc，只登记元信息）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,ctype,expect", [
    ("https://yz.example.test/a.pdf", "", "pdf"),
    ("https://yz.example.test/x", "application/pdf; charset=binary", "pdf"),
    ("https://yz.example.test/m3", "video/mp4", "media"),
    ("https://yz.example.test/clip.m3u8", "", "media"),
    ("https://yz.example.test/api/list", "application/json", "json"),
    ("https://yz.example.test/data.json?t=1", "", "json"),
    ("https://yz.example.test/目录.xlsx", "", "doc"),
    ("https://yz.example.test/index.html", "text/html", None),
    ("https://yz.example.test/logo.png", "image/png", None),
])
def test_classify_resource(url, ctype, expect):
    assert _CLS._classify_resource(url, ctype) == expect


def test_resources_registered_without_body(monkeypatch, browser_ready, fake_playwright):
    """resources 只含 url / kind / content_type —— 绝不带 body。"""
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("HTTP_403", code=403, valid=False))
    stack = FakeStack(html="<html>ok</html>", responses=[
        _FakeResponse(200, "https://yz.example.test/2027简章.pdf", {"content-type": "application/pdf"}),
        _FakeResponse(200, "https://yz.example.test/api/list", {"content-type": "application/json"}),
        _FakeResponse(200, "https://yz.example.test/v.mp4", {"content-type": "video/mp4"}),
        _FakeResponse(200, "https://yz.example.test/style.css", {"content-type": "text/css"}),
    ])
    fake_playwright(stack)

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    kinds = [r["kind"] for r in (res.resources or [])]
    assert kinds == ["pdf", "json", "media"]
    for item in res.resources or []:
        assert set(item) == {"url", "kind", "content_type"}
        assert "body" not in item and "content" not in item


def test_resources_none_when_nothing_classified(monkeypatch, browser_ready, fake_playwright):
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("HTTP_403", code=403, valid=False))
    fake_playwright(FakeStack(html="<html>ok</html>"))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert res.resources is None


def test_resources_capped(monkeypatch, browser_ready, fake_playwright):
    """嗅探条数有上限（S5 同款），防止大页面把 resources 撑爆。"""
    monkeypatch.setattr(HTTPFetcher, "fetch",
                        lambda self, url, **kw: _http_result("HTTP_403", code=403, valid=False))
    many = [_FakeResponse(200, f"https://yz.example.test/f{i}.pdf", {"content-type": "application/pdf"})
            for i in range(_CLS.MAX_CAPTURED_RESOURCES + 20)]
    fake_playwright(FakeStack(html="<html>ok</html>", responses=many))

    res = fetcher_mod.fetch_with_fallback(_URL, mode="auto")
    assert len(res.resources or []) == _CLS.MAX_CAPTURED_RESOURCES


# ─────────────────────────────────────────────────────────────────────────────
# 四、工具层：fetch_url(mode) 与 download_file
# ─────────────────────────────────────────────────────────────────────────────

def _tool(name, tmp_path):
    from tools.agent.permissions import PermissionManager
    from tools.agent.sandbox import Sandbox
    from tools.agent.tools_impl import ToolRegistry

    reg = ToolRegistry(Sandbox(workspace_root=tmp_path),
                       PermissionManager(mode="auto", workspace_root=tmp_path))
    return reg.tools[name].func


def _patch_both_fetchers(monkeypatch, attr, value):
    """工具层实际跑的是 ``intelligence.fetcher``，而本文件导入的是
    ``tools.intelligence.fetcher`` —— 双导入路径下这是**两个模块对象**，
    只 patch 一个会静默失效。这里两处都打补丁。"""
    import importlib

    names = []
    for name in ("tools.intelligence.fetcher", "intelligence.fetcher"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, attr, value)
        names.append(name)
    assert names, f"未能 patch 任何 fetcher 模块的 {attr}"
    return names


def test_tool_fetch_url_mode_validation(tmp_path):
    fn = _tool("fetch_url", tmp_path)
    assert fn("https://news.example.test/a", mode="turbo") == "Error: mode 仅支持 auto / http / browser"
    assert fn("file:///c:/x") == "Error: 仅支持 http:// 或 https:// 协议"


def test_tool_fetch_url_success_and_failure(tmp_path, monkeypatch):
    fn = _tool("fetch_url", tmp_path)
    calls = []

    def fake(url, **kw):
        calls.append((url, kw.get("mode")))
        if kw.get("mode") == "browser":
            return _browser_result(status="BLOCKED", valid=False)
        return _http_result(body="<html><script>x</script><p>正文 内容</p></html>")

    _patch_both_fetchers(monkeypatch, "fetch_with_fallback", fake)
    out = fn("https://news.example.test/a", mode="http")
    assert "正文 内容" in out and "<script>" not in out
    assert calls == [("https://news.example.test/a", "http")]

    bad = fn("https://news.example.test/a", mode="browser")
    assert bad.startswith("Error 访问网页失败")
    assert "tier=browser" in bad and "升级尝试=BLOCKED" in bad


def test_tool_fetch_url_failure_hints_gate(tmp_path, monkeypatch):
    """HTTP 403 且浏览器闸门未开 → 错误信息里给出可操作的开启方式。"""
    fn = _tool("fetch_url", tmp_path)
    res = _http_result("HTTP_403", code=403, body="", valid=False)
    res.escalation = "BROWSER_DISABLED"
    _patch_both_fetchers(monkeypatch, "fetch_with_fallback", lambda url, **kw: res)

    out = fn("https://news.example.test/a")
    assert out.startswith("Error 访问网页失败")
    assert "KY_BROWSER_ACQUISITION=on" in out


def test_tool_fetch_url_reports_resource_hint(tmp_path, monkeypatch):
    fn = _tool("fetch_url", tmp_path)
    res = _http_result(body="<p>正文</p>")
    res.resources = [
        {"url": "https://yz.example.test/简章.pdf", "kind": "pdf", "content_type": "application/pdf"},
        {"url": "https://yz.example.test/v.mp4", "kind": "media", "content_type": "video/mp4"},
    ]
    _patch_both_fetchers(monkeypatch, "fetch_with_fallback", lambda url, **kw: res)

    out = fn("https://news.example.test/a")
    assert "正文" in out
    assert "[页面资源线索]" in out and "pdf×1" in out and "media×1" in out


def test_tool_download_file_rejects_bad_names(tmp_path):
    fn = _tool("download_file", tmp_path)
    url = "https://yz.example.test/简章.pdf"
    assert fn("file:///c:/x.pdf").startswith("Error: 仅支持")
    assert fn("https://yz.example.test/a.exe").startswith("Error: 仅允许落盘文档类文件")
    assert "路径穿越" in fn(url, filename="../evil.pdf")
    assert "不带盘符" in fn(url, filename="C:/tmp/evil.pdf")


@pytest.mark.parametrize("url", [
    "https://media.example.test/clip.mp4",
    "https://media.example.test/live.m3u8",
    "https://media.example.test/audio.mp3",
])
def test_tool_download_file_never_saves_media(tmp_path, url):
    """[P2 硬约束] 短视频/音频只做「元数据 + 链接」，媒体下载默认关闭：
    白名单里没有媒体后缀，download_file 必须一律拒。"""
    fn = _tool("download_file", tmp_path)
    out = fn(url)
    assert out.startswith("Error: 仅允许落盘文档类文件")
    assert not (tmp_path / "data" / "downloads").exists(), "被拒的媒体不得留下任何目录/文件"


def test_tool_download_file_lands_in_downloads(tmp_path, monkeypatch):
    """成功路径：落盘在 data/downloads/ 内并返回相对路径 + 字节数。"""
    fn = _tool("download_file", tmp_path)
    written = {}

    def fake_download(self, url, dest_path, max_bytes=15 * 1024 * 1024):
        from pathlib import Path as _P
        p = _P(dest_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4 fake")
        written["path"] = p
        return True

    def _patch(mod):
        monkeypatch.setattr(mod.HTTPFetcher, "download_file", fake_download)

    import importlib
    patched = 0
    for name in ("tools.intelligence.fetcher", "intelligence.fetcher"):
        try:
            _patch(importlib.import_module(name))
            patched += 1
        except ImportError:
            continue
    assert patched >= 1

    out = fn("https://yz.example.test/简章.pdf")
    assert out.startswith("已下载: data/downloads/")
    assert "字节" in out
    dest = written["path"]
    assert dest.exists()
    assert dest.parent == (tmp_path / "data" / "downloads").resolve()
