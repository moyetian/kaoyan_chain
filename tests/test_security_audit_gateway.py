# -*- coding: utf-8 -*-
"""网关安全审查修复回归测试（2026-09-30 审查报告 P0-3 / P1-6 / P1-8）。

覆盖三项修复：
  * P0-3 跨站防护：``_origin_ok`` / ``_cors_header`` —— 跨站浏览器请求（含
    ``Content-Type: text/plain`` simple request 绕过预检的形态）在 do_POST 被 403；
    ACAO 不再回 ``*``，仅精确回显同源 Origin；静态页加 Referrer-Policy。
  * P1-6  /webhook 出站回调改走 ``net_guard.safe_urlopen``（SSRF 旁路封堵）。
  * P1-8  ``BoundedThreadingHTTPServer`` 并发上限 / handler socket 超时 /
    请求体 16 MiB 上限 413 / 群聊后台任务并发闸。

辅助函数（``_serve`` / ``_get`` / ``_post``）沿用
``tests/test_cli_security_hardening.py`` 的既有模式。
"""

from __future__ import annotations

import inspect
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tools.cli import gateway as gateway_mod
from tools.cli.gateway import clear_live_messages, create_gateway_handler
from tools.net_guard import UnsafeURLError


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """清环境密钥 + 清理会话缓存，避免本机环境让用例随机变红。"""
    monkeypatch.delenv("KY_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)
    clear_live_messages()
    yield
    clear_live_messages()


# ── HTTP 辅助 ─────────────────────────────────────────────────────────────

def _serve(handler_cls):
    """用被测的 BoundedThreadingHTTPServer 起服务（顺带覆盖容量内正常路径）。"""
    server = gateway_mod.BoundedThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _base(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def _get_full(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _post_full(url, headers=None, body=b""):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _get(url, headers=None):
    status, body, _ = _get_full(url, headers)
    return status, body


def _post(url, headers=None, body=b""):
    status, body, _ = _post_full(url, headers, body)
    return status, body


# ── P0-3 直调：_origin_ok 判定矩阵 ────────────────────────────────────────

class _FakeReq:
    """最小化 handler 实例替身，用于直调 ``_origin_ok``。"""

    def __init__(self, headers):
        self.headers = headers
        self.client_address = ("127.0.0.1", 55555)


def _origin_ok(headers) -> bool:
    handler_cls = create_gateway_handler()
    req = _FakeReq(headers)
    setattr(req, "_origin_ok", handler_cls._origin_ok.__get__(req, handler_cls))
    return req._origin_ok()


@pytest.mark.parametrize("headers,expected", [
    # 无 Origin：curl / 第三方服务端回调，放行
    ({}, True),
    ({"Host": "127.0.0.1:8088"}, True),
    # 同源（显式端口一致）放行
    ({"Origin": "http://127.0.0.1:8088", "Host": "127.0.0.1:8088"}, True),
    # 同源（默认端口、Host 未显式带端口，兼容反代）放行
    ({"Origin": "https://127.0.0.1", "Host": "127.0.0.1"}, True),
    ({"Origin": "https://127.0.0.1:443", "Host": "127.0.0.1:443"}, True),
    # Host 未显式带端口视为一致（反代）
    ({"Origin": "http://127.0.0.1:8088", "Host": "127.0.0.1"}, True),
    # 跨站主机：拒绝
    ({"Origin": "https://evil.example", "Host": "127.0.0.1:8088"}, False),
    # 端口不一致：拒绝
    ({"Origin": "http://127.0.0.1:9999", "Host": "127.0.0.1:8088"}, False),
    # Origin 默认端口 80 vs Host 显式 8088：拒绝
    ({"Origin": "http://127.0.0.1", "Host": "127.0.0.1:8088"}, False),
    # Origin: null：拒绝
    ({"Origin": "null", "Host": "127.0.0.1:8088"}, False),
    # 非 http/https scheme：拒绝
    ({"Origin": "ftp://127.0.0.1", "Host": "127.0.0.1"}, False),
    # 无 Host 头：拒绝（无法确认同源）
    ({"Origin": "http://127.0.0.1:8088"}, False),
    # Sec-Fetch-Site 纵深防御：即使 Origin 看似同源也拒绝
    ({"Origin": "http://127.0.0.1:8088", "Host": "127.0.0.1:8088",
      "Sec-Fetch-Site": "cross-site"}, False),
    ({"Origin": "https://evil.example", "Host": "127.0.0.1:8088",
      "Sec-Fetch-Site": "cross-site"}, False),
    # 畸形 Origin：拒绝（解析异常 fail-closed）
    ({"Origin": "http://[", "Host": "127.0.0.1:8088"}, False),
    ({"Origin": "http://127.0.0.1:99999", "Host": "127.0.0.1:8088"}, False),
])
def test_origin_ok_matrix(headers, expected):
    assert _origin_ok(headers) is expected


# ── P0-3 HTTP 级：跨站闸门与 ACAO 精确回显 ────────────────────────────────

def test_cross_site_post_denied_403_without_wildcard_acao():
    """① 阴性对照：跨站 Origin 的 POST → 403，且响应无 ACAO（更无通配符）。"""
    server = _serve(create_gateway_handler())
    try:
        status, body, headers = _post_full(
            f"{_base(server)}/api/clear",
            {"Origin": "https://evil.example"})
        assert status == 403
        assert headers.get("Access-Control-Allow-Origin") is None
        assert b"Forbidden" in body
    finally:
        server.shutdown()
        server.server_close()


def test_sec_fetch_site_cross_site_post_denied():
    """② 同源 Origin + Sec-Fetch-Site: cross-site → 403（仅第 3 步能拦的组合）。"""
    server = _serve(create_gateway_handler())
    try:
        base = _base(server)
        status, _, headers = _post_full(
            f"{base}/api/clear",
            {"Origin": base, "Sec-Fetch-Site": "cross-site"})
        assert status == 403
        assert headers.get("Access-Control-Allow-Origin") is None

        status2, _, _ = _post_full(
            f"{base}/api/clear",
            {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        assert status2 == 403
    finally:
        server.shutdown()
        server.server_close()


def test_same_origin_post_allowed_and_origin_echoed():
    """③⑤ 同源 Origin 的 POST 走正常流程，且 ACAO 精确回显该 Origin。"""
    server = _serve(create_gateway_handler())
    try:
        base = _base(server)
        status, body, headers = _post_full(f"{base}/api/clear", {"Origin": base})
        assert status == 200
        assert json.loads(body)["status"] == "cleared"
        assert headers.get("Access-Control-Allow-Origin") == base
        assert headers.get("Vary") == "Origin"
    finally:
        server.shutdown()
        server.server_close()


def test_post_without_origin_unaffected():
    """④ 无 Origin 的 POST（curl / 既有测试风格）不受影响。"""
    server = _serve(create_gateway_handler())
    try:
        status, body = _post(f"{_base(server)}/api/clear")
        assert status == 200
        assert json.loads(body)["status"] == "cleared"
    finally:
        server.shutdown()
        server.server_close()


def test_origin_null_post_denied():
    """⑥ Origin: null → 403。"""
    server = _serve(create_gateway_handler())
    try:
        status, _, headers = _post_full(
            f"{_base(server)}/api/clear", {"Origin": "null"})
        assert status == 403
        assert headers.get("Access-Control-Allow-Origin") is None
    finally:
        server.shutdown()
        server.server_close()


def test_cross_site_get_not_blocked_but_unreadable():
    """do_GET 不主动拒绝（不误伤外部链接导航），但响应不可被跨站脚本读取。"""
    server = _serve(create_gateway_handler())
    try:
        status, _, headers = _get_full(
            f"{_base(server)}/api/live", {"Origin": "https://evil.example"})
        assert status == 200
        assert headers.get("Access-Control-Allow-Origin") is None
    finally:
        server.shutdown()
        server.server_close()


def test_no_wildcard_acao_anywhere_in_source():
    """⑤ 静态断言：源码中不再存在 ``Access-Control-Allow-Origin: *`` 发送点。"""
    src = inspect.getsource(gateway_mod)
    assert '"Access-Control-Allow-Origin", "*"' not in src
    assert "'Access-Control-Allow-Origin', '*'" not in src
    assert '"Access-Control-Allow-Origin", origin' in src, "精确回显落盘缺失"


def test_static_page_sends_referrer_policy():
    """静态页 200 响应带 Referrer-Policy: no-referrer（防 ?token= 经 Referer 外泄）。"""
    server = _serve(create_gateway_handler())
    try:
        status, _, headers = _get_full(f"{_base(server)}/index.html")
        assert status == 200
        assert headers.get("Referrer-Policy") == "no-referrer"
    finally:
        server.shutdown()
        server.server_close()


# ── P1-8：请求体上限 / 并发上限 / socket 超时 ─────────────────────────────

def test_oversized_content_length_gets_413():
    """⑦ 伪造 16 MiB+1 的 Content-Length（不发 body）→ 413，不读入内存。"""
    server = _serve(create_gateway_handler())
    try:
        port = server.server_address[1]
        head = (
            "POST /api/clear HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {gateway_mod.MAX_POST_BODY_BYTES + 1}\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(head.encode("ascii"))
            chunks = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
        raw = b"".join(chunks)
        status_line = raw.split(b"\r\n", 1)[0]
        assert b" 413 " in status_line, raw[:120]
        assert b"Payload Too Large" in raw
    finally:
        server.shutdown()
        server.server_close()


def test_handler_has_socket_timeout():
    """handler 类带 30s socket 超时（防慢连接挂死工作线程）。"""
    assert create_gateway_handler().timeout == 30


def test_bounded_server_class_is_used_by_gateway():
    """⑩ BoundedThreadingHTTPServer 存在，且两个启动入口经它实例化。"""
    import http.server

    assert issubclass(gateway_mod.BoundedThreadingHTTPServer, http.server.ThreadingHTTPServer)
    # 模块级 ThreadingHTTPServer 名指向 bounded 实现（实例化点与测试打桩点共用此名）
    assert gateway_mod.ThreadingHTTPServer is gateway_mod.BoundedThreadingHTTPServer
    assert gateway_mod.BoundedThreadingHTTPServer.max_worker_threads == 32
    assert gateway_mod.BoundedThreadingHTTPServer.request_queue_size == 32
    for fn in (gateway_mod.run_server, gateway_mod.start_background_live_server):
        assert "ThreadingHTTPServer(" in inspect.getsource(fn), fn.__name__


def test_bounded_server_rejects_when_semaphore_exhausted(monkeypatch):
    """并发上限：信号量耗尽时新连接同步关闭，不启动工作线程。"""
    server = gateway_mod.BoundedThreadingHTTPServer(
        ("127.0.0.1", 0), create_gateway_handler())
    try:
        class _ExhaustedSem:
            def acquire(self, blocking=False):
                return False

            def release(self):
                pass

        monkeypatch.setattr(server, "_sem", _ExhaustedSem())
        closed = []
        thread_calls = []
        monkeypatch.setattr(server, "shutdown_request", lambda req: closed.append(req))
        monkeypatch.setattr(server, "process_request_thread",
                            lambda *a: thread_calls.append(a))
        marker = object()
        server.process_request(marker, ("127.0.0.1", 0))
        assert closed == [marker], "超限连接未被关闭"
        assert thread_calls == [], "超限时不应启动工作线程"
    finally:
        server.server_close()


def test_run_webhook_bg_runs_in_background():
    """群聊后台任务正常走线程执行。"""
    done = threading.Event()
    gateway_mod._run_webhook_bg(done.set)
    assert done.wait(timeout=5)


def test_run_webhook_bg_degrades_to_sync_when_exhausted(monkeypatch):
    """后台任务并发闸超限时退化为同步执行（不丢消息）。"""

    class _BusySem:
        def acquire(self, blocking=False):
            return False

        def release(self):
            pass

    monkeypatch.setattr(gateway_mod, "_WEBHOOK_BG_SEM", _BusySem())
    done = []
    gateway_mod._run_webhook_bg(lambda: done.append("ran"))
    assert done == ["ran"], "超限时消息未同步执行"


# ── P1-6：/webhook 出站 SSRF 封堵 ─────────────────────────────────────────

def test_webhook_outbound_uses_safe_urlopen(monkeypatch, capsys):
    """⑧ 出站 URL 来自入站 JSON：必须经 safe_urlopen；被拦截时异常被吞、无崩溃。"""
    called = threading.Event()
    recorded = {}

    def fake_safe_urlopen(req, timeout=12, context=None):
        recorded["url"] = getattr(req, "full_url", str(req))
        called.set()
        raise UnsafeURLError("blocked internal address")

    monkeypatch.setattr(gateway_mod, "safe_urlopen", fake_safe_urlopen)
    monkeypatch.setattr(gateway_mod, "query_llm_reply",
                        lambda msg, cfg: "【占位回复】")

    server = _serve(create_gateway_handler())
    try:
        payload = json.dumps({
            "text": {"content": "占位提问"},
            "sessionWebhook": "http://169.254.169.254/latest/meta-data/",
        }, ensure_ascii=False).encode("utf-8")
        status, _ = _post(f"{_base(server)}/webhook",
                          {"Content-Type": "application/json"}, payload)
        assert status == 200
        assert called.wait(timeout=5), "出站未经过 safe_urlopen（仍走裸 urlopen？）"
        assert recorded["url"].startswith("http://169.254.169.254/")
    finally:
        server.shutdown()
        server.server_close()

    # 等待后台线程把异常吞进失败日志（累积读取，规避竞态）
    out = ""
    deadline = time.time() + 5
    while "钉钉异步发送失败" not in out and time.time() < deadline:
        time.sleep(0.05)
        out += capsys.readouterr().out
    assert "钉钉异步发送失败" in out, "UnsafeURLError 未被吞入失败日志"


def test_gateway_source_has_no_bare_urlopen():
    """静态断言：gateway 源码中不再有裸 ``urllib.request.urlopen(`` 调用。"""
    src = inspect.getsource(gateway_mod)
    assert "urllib.request.urlopen(" not in src
    assert "safe_urlopen(req, timeout=10)" in src


# ── 回归：中文 token ?token= 通路不受 Origin 闸门影响 ─────────────────────

def test_chinese_token_query_param_still_works():
    """⑨ 中文 token 走 ``?token=``（手机端首屏场景）仍可用。"""
    server = _serve(create_gateway_handler(token="我的密钥"))
    try:
        base = _base(server)
        assert _get(f"{base}/api/live", {"X-KY-Token": "wrong"})[0] == 401
        quoted = urllib.parse.quote("我的密钥", safe="")
        assert _get(f"{base}/api/live?token={quoted}")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
