# -*- coding: utf-8 -*-
"""tests/test_fetcher_browser.py —— BrowserPluginManager 阶段 0 安全加固（S1–S9）回归。

被测：``tools/intelligence/fetcher.py`` 的 ``BrowserPluginManager.fetch_with_browser``
（2026-09-28 重写，依据 ``browser_acquisition_integration_plan.md`` v2 与红队核实记录）。

覆盖清单
--------
* 闸门三态：``KY_BROWSER_ACQUISITION`` 默认 off；on/1/true/yes 才开；off 时零副作用。
* S7 两级探测：库缺失 → ``BROWSER_NOT_INSTALLED``；内核探测链
  msedge → chrome → ms-playwright 缓存（None）→ ``UNAVAILABLE``；聚合 ``is_available``。
* S1 入口 SSRF：真实 ``assert_url_safe`` 对回环/内网/云元数据 IP 字面量即拒，
  拒绝时不得启动浏览器。
* S2 route 全量拦截：非 http(s) fail-closed；逐 host 复检 + 会话级缓存去重；
  任意异常 → abort；WS 双保险与 SW/WebRTC/下载面封堵。
* S3 try/finally：成功与异常路径都必须 ``browser.close()``。
* S4 会话预算：deadline 已过 → ``TIMEOUT`` 且不启动；goto/default 超时压缩为剩余预算。
* S5 体积上限：正文 2MB 截断 + ``TRUNCATION_MARKER`` + ``truncated=True``；嗅探 ≤100 条。
* S6 真实状态码透传；验证码/反爬特征 → ``BLOCKED``（绝不报 OK）。
* S9 异常详情只进日志，``access_status`` 仅枚举值、绝不外泄异常文本。

隔离约定
--------
* **全 mock**：向 ``sys.modules`` 注入假的 ``playwright`` / ``playwright.sync_api``
  模块（FakeStack 模拟 chromium/browser/context/page/route 全链路），不启动任何真实
  浏览器、不发起任何真实网络请求；CI 未安装 playwright 也能全绿。
* **不依赖宿主机**：探测链用 ``Path.exists`` / ``Path.glob`` / ``shutil.which`` 的
  受控替身驱动，无论本机是否装 Edge 结果一致。
* **SSRF 用例**：需要真实 ``assert_url_safe`` 的用例只用 IP 字面量 / ``localhost``
  （``getaddrinfo`` 对字面量不做 DNS 查询），其余用例把 ``assert_url_safe`` 换成
  受控替身（默认放行 / 计数 / 拒绝）。
* 测试数据一律中性占位（``*.example.test``、``SECRET-TOKEN-*``）。
"""

from __future__ import annotations

import shutil
import sys
import time
import types
import urllib.parse
from pathlib import Path

import pytest

from tools.intelligence import fetcher as fetcher_mod

_CLS = fetcher_mod.BrowserPluginManager
_GATE = _CLS.GATE_ENV
_URL = "https://news.example.test/notice/1.html"


def _allow_all(url, pin=None):
    """受控放行替身：不解析 DNS、不判定网段。"""
    return url


# ─────────────────────────────────────────────────────────────────────────────
# FakePlaywright 栈
# ─────────────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status: int = 200, url: str = ""):
        self.status = status
        self.url = url


class _FakeRequest:
    def __init__(self, url):
        self.url = url


class _ExplodingRequest:
    """``request.url`` 读取即抛 —— 验证 S2 的 fail-closed 兜底。"""

    @property
    def url(self):
        raise RuntimeError("request.url 不可读")


class _FakeRoute:
    def __init__(self):
        self.continued = 0
        self.aborted = 0

    def continue_(self):
        self.continued += 1

    def abort(self):
        self.aborted += 1


class FakeStack:
    """模拟 ``sync_playwright()`` 全链路；所有层都指回自身，便于集中记录。

    对应关系::

        sync_playwright()  -> self（context manager，__enter__ 返回 self）
        p.chromium         -> self（property）
        chromium.launch()  -> self（记录 launch_kwargs）
        browser.new_context()/close()
        context.route()/route_web_socket()/add_init_script()/new_page()
        page.set_default_timeout()/on()/goto()/content()
    """

    def __init__(self, html: str = "<html><body>ok</body></html>", status: int = 200,
                 goto_error: Exception | None = None,
                 launch_error: Exception | None = None,
                 support_ws_route: bool = True,
                 responses_to_emit=None):
        self._html = html
        self._status = status
        self.goto_error = goto_error
        self.launch_error = launch_error
        self.responses_to_emit = list(responses_to_emit or [])
        if not support_ws_route:
            self.route_web_socket = None  # type: ignore[assignment]  # 模拟旧版无此 API

        # 记录面
        self.sync_calls = 0
        self.playwright_entered = False
        self.playwright_exited = False
        self.launch_kwargs = None
        self.context_kwargs = None
        self.init_scripts = []
        self.route_handlers = []
        self.ws_route_handlers = []
        self.response_handlers = []
        self.goto_urls = []
        self.goto_timeouts = []
        self.goto_wait_until = None
        self.default_timeout_ms = None
        self.browser_closed = False
        # [P0] 跳转还原模拟：page.url / 链接提取 / 重定向映射
        self.url = ""
        self.link_hrefs = []
        self.redirect_map = {}
        self.eval_calls = []

    # ---- context manager ----

    def sync_playwright(self):
        self.sync_calls += 1
        return self

    def __enter__(self):
        self.playwright_entered = True
        return self

    def __exit__(self, *exc):
        self.playwright_exited = True
        return False

    # ---- chromium / browser ----

    @property
    def chromium(self):
        return self

    def launch(self, **kwargs):
        self.launch_kwargs = dict(kwargs)
        if self.launch_error is not None:
            raise self.launch_error
        return self

    def new_context(self, **kwargs):
        self.context_kwargs = dict(kwargs)
        return self

    def close(self):
        self.browser_closed = True

    # ---- context ----

    def route(self, pattern, handler):
        self.route_handlers.append((pattern, handler))

    def route_web_socket(self, pattern, handler):  # noqa: D401 - 模拟 API
        self.ws_route_handlers.append((pattern, handler))

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def new_page(self):
        return self

    # ---- page ----

    def set_default_timeout(self, ms):
        self.default_timeout_ms = ms

    def on(self, event, handler):
        if event == "response":
            self.response_handlers.append(handler)

    def goto(self, url, timeout=None, wait_until=None):
        self.goto_urls.append(url)
        self.goto_timeouts.append(timeout)
        self.goto_wait_until = wait_until
        if self.goto_error is not None:
            raise self.goto_error
        # [P0] 模拟重定向：最终 URL 由 redirect_map 决定（未配置则等于原址）
        self.url = self.redirect_map.get(url, url)
        for u in self.responses_to_emit:
            self.emit_response(u)
        return _FakeResponse(self._status, url)

    def eval_on_selector_all(self, selector, expression):
        """[P0] 模拟链接提取（返回预设的 link_hrefs）。"""
        self.eval_calls.append(selector)
        return list(self.link_hrefs)

    def content(self):
        return self._html

    # ---- 测试辅助 ----

    def emit_response(self, url):
        for handler in self.response_handlers:
            handler(_FakeResponse(200, url))

    def call_route(self, request):
        """手动触发已注册的 S2 route handler（字符串自动包成 _FakeRequest）。"""
        assert self.route_handlers, "route handler 尚未安装（fetch 未执行到接线处）"
        if isinstance(request, str):
            request = _FakeRequest(request)
        route = _FakeRoute()
        self.route_handlers[-1][1](route, request)
        return route


# ─────────────────────────────────────────────────────────────────────────────
# 安装辅助
# ─────────────────────────────────────────────────────────────────────────────

def _install_fake_playwright(monkeypatch, stack: FakeStack) -> None:
    """把假的 playwright 包注入 ``sys.modules``，使函数内 from-import 命中。"""
    pkg = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = stack.sync_playwright
    pkg.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)


def _force_channel(monkeypatch, channel: str = "msedge") -> None:
    """把内核探测固定为给定值，避免用例依赖宿主机是否装 Edge。"""
    monkeypatch.setattr(_CLS, "_detect_channel", staticmethod(lambda: channel))


def _patch_fs(monkeypatch, *, edge: bool, chrome: bool, cache: bool) -> None:
    """受控文件系统替身：只回答探测链关心的路径。

    * Edge/Chrome 可执行文件是否存在 → ``edge`` / ``chrome`` 参数；
    * ms-playwright 缓存目录是否存在 + 是否含 chromium* → ``cache`` 参数；
    * 其余真实路径仍走原 ``Path.exists`` / ``Path.glob``。
    """
    real_exists = Path.exists
    real_glob = Path.glob

    def fake_exists(self):
        s = str(self).lower().replace("\\", "/")
        if "ms-playwright" in s:
            return cache
        if "msedge" in s or "microsoft-edge" in s:
            return edge
        if "chrome" in s or "chromium" in s:
            return chrome
        return real_exists(self)

    def fake_glob(self, pattern):
        if "ms-playwright" in str(self).lower():
            return iter([self / "chromium-1234"] if cache else [])
        return real_glob(self, pattern)

    def fake_which(name):
        if edge and "edge" in name:
            return "/usr/bin/microsoft-edge"
        if chrome and ("chrome" in name or "chromium" in name):
            return "/usr/bin/google-chrome"
        return None

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "glob", fake_glob)
    monkeypatch.setattr(shutil, "which", fake_which)


# ─────────────────────────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv(_GATE, "on")


@pytest.fixture
def fake_stack(monkeypatch, gate_on):
    """闸门 on + 假 playwright 栈 + 固定 msedge + SSRF 替身默认放行。"""
    stack = FakeStack()
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)
    return stack


# ─────────────────────────────────────────────────────────────────────────────
# 闸门（默认 off，权限下沉到 BrowserPluginManager 自身）
# ─────────────────────────────────────────────────────────────────────────────

def test_gate_default_off(monkeypatch):
    monkeypatch.delenv(_GATE, raising=False)
    assert _CLS.is_enabled() is False


@pytest.mark.parametrize("val,expected", [
    ("on", True), ("1", True), ("true", True), ("yes", True),
    ("ON", True), (" True ", True),
    ("off", False), ("0", False), ("", False), ("random", False),
])
def test_gate_env_values(monkeypatch, val, expected):
    monkeypatch.setenv(_GATE, val)
    assert _CLS.is_enabled() is expected


def test_gate_off_short_circuits(monkeypatch, fake_stack):
    """闸门 off：返回 BROWSER_DISABLED，且 sync_playwright 一次都不被调用。"""
    monkeypatch.delenv(_GATE, raising=False)
    r = _CLS.fetch_with_browser(_URL)
    assert r.access_status == "BROWSER_DISABLED"
    assert r.status_code == 0 and r.content == "" and r.is_valid is False
    assert fake_stack.sync_calls == 0
    assert fake_stack.playwright_entered is False


# ─────────────────────────────────────────────────────────────────────────────
# S7 两级探测
# ─────────────────────────────────────────────────────────────────────────────

def test_not_installed_without_playwright(monkeypatch, gate_on):
    """库级探测失败 → BROWSER_NOT_INSTALLED（None 占位是标准「阻止导入」技巧）。"""
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    assert _CLS._has_library() is False
    r = _CLS.fetch_with_browser(_URL)
    assert r.access_status == "BROWSER_NOT_INSTALLED"


def test_has_library_true_when_importable(fake_stack):
    assert _CLS._has_library() is True


@pytest.mark.parametrize("edge,chrome,cache,expected", [
    (True, True, False, "msedge"),    # 优先级：都装选 Edge
    (False, True, False, "chrome"),
    (False, False, True, None),       # Playwright 自带 chromium 缓存
    (False, False, False, "UNAVAILABLE"),
])
def test_detect_channel_chain(monkeypatch, edge, chrome, cache, expected):
    _patch_fs(monkeypatch, edge=edge, chrome=chrome, cache=cache)
    assert _CLS._detect_channel() == expected


def test_is_available_aggregation(monkeypatch):
    """S7 聚合：库与内核必须同时可用。"""
    monkeypatch.setattr(_CLS, "_has_library", staticmethod(lambda: False))
    monkeypatch.setattr(_CLS, "_detect_channel", staticmethod(lambda: "msedge"))
    assert _CLS.is_available() is False

    monkeypatch.setattr(_CLS, "_has_library", staticmethod(lambda: True))
    monkeypatch.setattr(_CLS, "_detect_channel", staticmethod(lambda: "UNAVAILABLE"))
    assert _CLS.is_available() is False

    monkeypatch.setattr(_CLS, "_detect_channel", staticmethod(lambda: None))
    assert _CLS.is_available() is True


# ─────────────────────────────────────────────────────────────────────────────
# S1 入口 SSRF（真实 assert_url_safe，仅 IP 字面量 / localhost，无 DNS）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://192.168.1.1/",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost/",
])
def test_entry_ssrf_real_impl(monkeypatch, gate_on, url):
    stack = FakeStack()
    _install_fake_playwright(monkeypatch, stack)
    # 不替换 assert_url_safe —— 走真实实现
    r = _CLS.fetch_with_browser(url)
    assert r.access_status == "BLOCKED"
    assert r.status_code == 0 and r.is_valid is False
    assert stack.sync_calls == 0


# ─────────────────────────────────────────────────────────────────────────────
# 正常流程与 S3/S8 接线
# ─────────────────────────────────────────────────────────────────────────────

def test_ok_flow(fake_stack):
    fake_stack._html = "<html><head><title>T</title></head><body>正文</body></html>"
    r = _CLS.fetch_with_browser(_URL)

    assert r.access_status == "OK" and r.is_valid is True
    assert r.status_code == 200
    assert r.truncated is False
    assert r.raw_bytes_len == len(r.content.encode("utf-8"))
    assert r.headers == {} and r.api_captured == []
    assert r.content == fake_stack._html

    # 启动参数与生命周期
    assert fake_stack.launch_kwargs == {"channel": "msedge", "headless": True}
    assert fake_stack.goto_urls == [_URL]
    assert fake_stack.goto_wait_until == "domcontentloaded"
    assert fake_stack.playwright_entered is True
    assert fake_stack.playwright_exited is True
    assert fake_stack.browser_closed is True  # S3


def test_context_hardening(fake_stack):
    """S2/S8 接线：SW 封禁、禁下载、route 注册、WS 双保险、WebRTC 哑化。"""
    _CLS.fetch_with_browser(_URL)

    assert fake_stack.context_kwargs["service_workers"] == "block"
    assert fake_stack.context_kwargs["accept_downloads"] is False
    assert fake_stack.context_kwargs["user_agent"] == fetcher_mod.USER_AGENT

    assert len(fake_stack.route_handlers) == 1
    assert fake_stack.route_handlers[0][0] == "**/*"
    assert len(fake_stack.ws_route_handlers) == 1

    script = " ".join(fake_stack.init_scripts)
    assert "WebSocket" in script and "RTCPeerConnection" in script


def test_ws_route_optional(monkeypatch, gate_on):
    """旧版 Playwright 无 route_web_socket 时静默降级，init script 兜底仍在。"""
    stack = FakeStack(support_ws_route=False)
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    r = _CLS.fetch_with_browser(_URL)
    assert r.access_status == "OK"
    assert stack.ws_route_handlers == []
    assert any("WebSocket" in s for s in stack.init_scripts)


# ─────────────────────────────────────────────────────────────────────────────
# S2 route 全量拦截
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "data:text/html,x",
    "blob:https://x.example.test/abc",
    "ws://x.example.test/socket",
    "wss://x.example.test/socket",
    "file:///etc/passwd",
    "about:blank",
    "chrome://settings",
])
def test_route_non_http_aborted(monkeypatch, fake_stack, url):
    """非 http(s) 一律 fail-closed，且不进入 SSRF 复检（无从判定）。"""
    calls = []
    monkeypatch.setattr(fetcher_mod, "assert_url_safe",
                        lambda u, pin=None: (calls.append(u), u)[1])
    _CLS.fetch_with_browser(_URL)
    entry_calls = list(calls)  # 入口一次

    route = fake_stack.call_route(url)
    assert route.aborted == 1 and route.continued == 0
    assert calls == entry_calls


def test_route_per_host_cache(monkeypatch, fake_stack):
    """逐 host 复检 + 会话级缓存：同 host 只解析一次，新 host 才再查。"""
    calls = []
    monkeypatch.setattr(fetcher_mod, "assert_url_safe",
                        lambda u, pin=None: (calls.append(u), u)[1])
    _CLS.fetch_with_browser(_URL)
    base = len(calls)

    r1 = fake_stack.call_route("https://cdn.example.test/a.js")
    r2 = fake_stack.call_route("https://cdn.example.test/b.js")
    r3 = fake_stack.call_route("https://other.example.test/x.css")

    assert (r1.continued, r2.continued, r3.continued) == (1, 1, 1)
    assert len(calls) - base == 2  # cdn 一次（去重）+ other 一次


def test_route_fail_closed(monkeypatch, fake_stack):
    """复检拒绝 → abort；handler 内部任意异常 → 仍 abort（绝不 continue）。

    替身按 host 判定（而非调用计数），与「入口是否恰好调用一次」解耦。
    """

    def _block_evil(url, pin=None):
        if "evil.example.test" in url:
            raise fetcher_mod.UnsafeURLError("blocked by test")
        return url

    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _block_evil)
    _CLS.fetch_with_browser(_URL)

    r1 = fake_stack.call_route("https://evil.example.test/x")
    assert r1.aborted == 1 and r1.continued == 0

    r2 = fake_stack.call_route(_ExplodingRequest())
    assert r2.aborted == 1 and r2.continued == 0


def test_route_real_ssrf_blocks_redirect_hop(monkeypatch, fake_stack):
    """重定向第二跳命中内网 IP 字面量时被真实 assert_url_safe 拦截。

    占位假域名（``*.example.test``）放行，其余走真实实现 —— 与调用计数解耦。
    """
    from tools.net_guard import assert_url_safe as real_assert

    def _allow_fake_domains_else_real(url, pin=None):
        host = urllib.parse.urlsplit(url).hostname or ""
        if host.endswith(".example.test"):
            return url
        return real_assert(url)

    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_fake_domains_else_real)
    _CLS.fetch_with_browser(_URL)

    route = fake_stack.call_route("http://127.0.0.1/secret")
    assert route.aborted == 1 and route.continued == 0


# ─────────────────────────────────────────────────────────────────────────────
# S4 会话预算
# ─────────────────────────────────────────────────────────────────────────────

def test_deadline_expired_before_launch(fake_stack):
    r = _CLS.fetch_with_browser(_URL, deadline=time.monotonic() - 1)
    assert r.access_status == "TIMEOUT"
    assert fake_stack.sync_calls == 0


def test_goto_timeout_compressed(fake_stack):
    """goto/default 超时都压缩为剩余预算（1.5s），而非调用方给的 30s。"""
    r = _CLS.fetch_with_browser(_URL, timeout_sec=30,
                                deadline=time.monotonic() + 1.5)
    assert r.access_status == "OK"
    assert len(fake_stack.goto_timeouts) == 1
    assert 0 < fake_stack.goto_timeouts[0] <= 1500
    assert 0 < fake_stack.default_timeout_ms <= 1500


# ─────────────────────────────────────────────────────────────────────────────
# S5 体积上限与嗅探上限
# ─────────────────────────────────────────────────────────────────────────────

def test_content_truncated(monkeypatch, fake_stack):
    assert _CLS.PAGE_CONTENT_LIMIT == 2 * 1024 * 1024  # 配置常量
    monkeypatch.setattr(_CLS, "PAGE_CONTENT_LIMIT", 1000)

    fake_stack._html = "x" * 1500
    r = _CLS.fetch_with_browser(_URL)

    assert r.truncated is True
    assert r.content.endswith(fetcher_mod.TRUNCATION_MARKER)
    assert len(r.content) == 1000 + len(fetcher_mod.TRUNCATION_MARKER)


def test_api_sniff_cap(monkeypatch, gate_on):
    """嗅探条数硬上限 100；非 API 特征 URL 不入列表。"""
    urls = [f"https://news.example.test/api/item/{i}" for i in range(150)]
    stack = FakeStack(responses_to_emit=urls + ["https://news.example.test/static/a.css"])
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    r = _CLS.fetch_with_browser(_URL)
    assert len(r.api_captured) == 100
    assert all("/api/" in u for u in r.api_captured)


# ─────────────────────────────────────────────────────────────────────────────
# S6 真实状态码与验证码特征
# ─────────────────────────────────────────────────────────────────────────────

def test_status_code_passthrough(monkeypatch, gate_on):
    stack = FakeStack(status=403)
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    r = _CLS.fetch_with_browser(_URL)
    assert r.status_code == 403


@pytest.mark.parametrize("marker", _CLS._BLOCK_MARKERS)
def test_block_markers_detected(monkeypatch, gate_on, marker):
    """验证码/反爬特征页 → BLOCKED（绝不报 OK），状态码仍如实透传。"""
    stack = FakeStack(html=f"<html><body>{marker}</body></html>", status=200)
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    r = _CLS.fetch_with_browser(_URL)
    assert r.access_status == "BLOCKED"
    assert r.is_valid is False
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# S9 异常不外泄 + S3 异常路径关闭
# ─────────────────────────────────────────────────────────────────────────────

def test_error_detail_not_leaked(monkeypatch, gate_on):
    secret = "SECRET-TOKEN-DO-NOT-LEAK"
    stack = FakeStack(goto_error=RuntimeError(secret))
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    r = _CLS.fetch_with_browser(_URL)

    assert r.access_status == "BROWSER_ERROR"
    assert r.is_valid is False and r.status_code == 0
    assert secret not in repr(r)
    assert secret not in r.content
    assert stack.browser_closed is True  # S3 在异常路径同样生效


def test_launch_error(monkeypatch, gate_on):
    """launch 抛异常：如实 BROWSER_ERROR，不外泄细节，finally 不误伤 None browser。"""
    stack = FakeStack(launch_error=RuntimeError("no browser binary"))
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    r = _CLS.fetch_with_browser(_URL)
    assert r.access_status == "BROWSER_ERROR"
    assert stack.browser_closed is False


# ─────────────────────────────────────────────────────────────────────────────
# [P0] should_stop 中断（GUI 关窗）与 resolve_links 跳转还原
# ─────────────────────────────────────────────────────────────────────────────

def test_should_stop_aborts_before_launch(fake_stack):
    """停止回调为真：ABORTED 且不启动浏览器（检查点在 launch 之前）。"""
    r = _CLS.fetch_with_browser(_URL, should_stop=lambda: True)
    assert r.access_status == "ABORTED"
    assert r.status_code == 0 and r.is_valid is False
    assert fake_stack.sync_calls == 0


def test_should_stop_callback_exception_is_fail_closed(fake_stack):
    """停止回调自身抛异常 → 视作要求停止（fail-closed），绝不带病继续。"""
    def _boom():
        raise RuntimeError("qt object gone")

    r = _CLS.fetch_with_browser(_URL, should_stop=_boom)
    assert r.access_status == "ABORTED"
    assert fake_stack.sync_calls == 0


def test_resolve_none_when_not_requested(fake_stack):
    r = _CLS.fetch_with_browser(_URL)
    assert r.resolved_links is None
    assert fake_stack.eval_calls == []


def test_resolve_links_follows_and_maps(monkeypatch, gate_on):
    """跳转还原：链接逐条在会话内跟随，返回 {原链接: 最终链接} 映射。

    未发生跳转的链接映射到自身（语义：跟随后的最终地址就是它自己）。
    """
    stack = FakeStack()
    stack.link_hrefs = ["https://weixin.sogou.com/link?url=A",
                        "https://weixin.sogou.com/link?url=B"]
    stack.redirect_map = {
        "https://weixin.sogou.com/link?url=A": "https://mp.weixin.qq.com/s/AAA"}
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    r = _CLS.fetch_with_browser(_URL, resolve_links_selector="div.txt-box h3 a",
                                resolve_limit=5)
    assert r.access_status == "OK"
    assert r.resolved_links == {
        "https://weixin.sogou.com/link?url=A": "https://mp.weixin.qq.com/s/AAA",
        "https://weixin.sogou.com/link?url=B": "https://weixin.sogou.com/link?url=B",
    }
    # 主页面 1 次 + 跳转 2 次；选择器透传
    assert len(stack.goto_urls) == 3
    assert stack.eval_calls == ["div.txt-box h3 a"]
    assert stack.browser_closed is True


def test_resolve_links_budget_exhausted(monkeypatch, gate_on):
    """还原子预算为 0：全部记为 None，不发起任何跳转。"""
    stack = FakeStack()
    stack.link_hrefs = ["https://x.example.test/a"]
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    r = _CLS.fetch_with_browser(_URL, resolve_links_selector="a",
                                resolve_budget_sec=0.0)
    assert r.resolved_links == {"https://x.example.test/a": None}
    assert stack.goto_urls == [_URL]


def test_should_stop_mid_resolve(monkeypatch, gate_on):
    """还原循环中途停止：已解析项保留、剩余项为 None，且不再发起跳转。"""
    stack = FakeStack()
    stack.link_hrefs = ["https://a.example.test/1", "https://b.example.test/2"]
    stack.redirect_map = {"https://a.example.test/1": "https://mp.weixin.qq.com/s/1"}
    _install_fake_playwright(monkeypatch, stack)
    _force_channel(monkeypatch)
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", _allow_all)

    # 用「已发生的 goto 次数」驱动停止：主页面 + 第 1 跳完成后即停
    def _stop_after_first_hop():
        return len(stack.goto_urls) >= 2

    r = _CLS.fetch_with_browser(_URL, should_stop=_stop_after_first_hop,
                                resolve_links_selector="a")
    assert r.access_status == "OK"
    assert r.resolved_links == {
        "https://a.example.test/1": "https://mp.weixin.qq.com/s/1",
        "https://b.example.test/2": None,
    }
    assert stack.goto_urls == [_URL, "https://a.example.test/1"]
    assert stack.browser_closed is True
