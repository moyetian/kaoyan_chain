# -*- coding: utf-8 -*-
"""网络层加固回归 · 重定向 SSRF / DNS 重绑定 / 解压炸弹 / 静默 TLS 降级 / 截断标记。

缺陷现场（均为本轮代码审查实测复现）
------------------------------------
1. **``download_file`` 重定向绕过 SSRF**：``tools/intelligence/fetcher.py`` 的
   ``download_file`` 只对**初始 URL** 调了 ``assert_url_safe``，随后用裸
   ``urllib.request.urlopen`` —— 它默认跟随 3xx 且**不再复检**。公网 URL 302 到
   ``http://127.0.0.1:…/secret`` 即可把回环内容写进本地文件。
2. **``net_guard`` 的 DNS 重绑定（TOCTOU）窗口**：校验用 ``getaddrinfo`` 解析一次，
   ``opener.open`` 建连时**再独立解析一次**。攻击者控制权威 DNS 时，第一次返回公网
   IP（校验通过）、第二次返回 ``127.0.0.1``，校验结果根本没被 pin 到连接上。
3. **``_http.decompress_body`` 无上限解压**：``gzip.decompress`` / ``zlib.decompress``
   无体积上限，且 ``resp.read()`` 不带上限。
4. **``_http.get_text`` 静默 TLS 降级**：证书错误时悄悄换成 ``CERT_NONE`` 重试，
   调用方拿不到任何「未验证」信号。**本轮已删除降级**：默认证书错误如实失败，
   仅当调用方显式 ``allow_insecure_ssl=True`` 才允许未验证重试。
5. **``net_guard._gzip_limited`` 不标记截断**：被 ``resp.read(MAX)`` 截断的压缩流
   不判 ``eof``，返回 ``truncated=False``，调用方分不清「完整」与「半截」。
6. **``fetcher`` 的 ``_truncated`` 死变量**：截断后仍 ``is_valid=True`` 且没有
   ``TRUNCATION_MARKER``。

每条守卫都配**阴性对照**：把守卫摘掉后，对应的坏行为必须重新出现。
所有网络用例都在回环上自建 socket / http.server，**不发真实外网请求**。
"""

import gzip
import ipaddress
import socket
import ssl
import threading
import urllib.error
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    import sys
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import net_guard  # noqa: E402
from tools.intelligence import fetcher as fetcher_mod  # noqa: E402
from tools.search.providers import _http  # noqa: E402

#: 被「校验」认定为公网、可放行的 IP（TEST 文档地址，非真实业务地址）
VALIDATED_PUBLIC_IP = "93.184.216.34"
ENTRY_HOST = "public-entry.test"
LOOPBACK_SENTINEL = "SENTINEL_LOOPBACK_REDIRECT_7c41"


# ───────────────────────── 公共工具 ─────────────────────────

class _Redirector(BaseHTTPRequestHandler):
    """``/`` 返回 302 到本机回环的 ``/secret``；``/secret`` 返回哨兵。"""

    def do_GET(self):  # noqa: N802
        port = self.server.server_address[1]
        if self.path == "/":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/secret")
            self.end_headers()
        elif self.path == "/secret":
            body = LOOPBACK_SENTINEL.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # noqa: D102
        pass


@pytest.fixture
def redirector():
    """起一个「入口 302 到回环」的本地服务（仅监听 127.0.0.1）。"""
    server = TCPServer(("127.0.0.1", 0), _Redirector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def route_validated_ip_to_loopback(monkeypatch):
    """把「已校验公网 IP」这一跳路由到本地测试服务，保证用例零外网流量。"""
    real_create = socket.create_connection

    def _routed(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host, port = address
        if host == VALIDATED_PUBLIC_IP:
            host = "127.0.0.1"
        return real_create((host, port), timeout, source_address)

    monkeypatch.setattr(socket, "create_connection", _routed)


def _fake_response(body: bytes, headers: dict, status: int = 200):
    class _Resp:
        def __init__(self):
            self.status = status
            self.headers = headers

        def read(self, n=-1):  # noqa: D102
            if n is None or n < 0:
                return body
            return body[:n]

        def __enter__(self):  # noqa: D102
            return self

        def __exit__(self, *a):  # noqa: D102
            return False

    return _Resp()


# ═════════════════ ① download_file 重定向绕过 SSRF ═════════════════

def test_download_file_blocks_redirect_to_loopback(
        tmp_path, redirector, route_validated_ip_to_loopback):
    """入口是公网 IP 字面量，302 到 ``127.0.0.1`` —— 回环内容不得落盘。"""
    dest = tmp_path / "out.bin"
    url = f"http://{VALIDATED_PUBLIC_IP}:{redirector}/"

    assert fetcher_mod.HTTPFetcher().download_file(url, dest) is False
    assert not dest.exists(), "重定向到回环的内容被写进了本地文件"


def test_negative_control_bare_urlopen_follows_redirect_to_loopback(
        tmp_path, redirector, route_validated_ip_to_loopback, monkeypatch):
    """阴性对照：把 ``download_file`` 打回旧的裸 ``urlopen``，回环内容必须真的落盘。"""
    dest = tmp_path / "out.bin"
    url = f"http://{VALIDATED_PUBLIC_IP}:{redirector}/"

    monkeypatch.setattr(
        fetcher_mod, "safe_urlopen",
        lambda req, timeout=12, context=None: urllib.request.urlopen(
            req, timeout=timeout, context=context))

    assert fetcher_mod.HTTPFetcher().download_file(url, dest) is True
    assert LOOPBACK_SENTINEL.encode("utf-8") in dest.read_bytes(), (
        "摘掉 safe_urlopen 后仍未读到回环内容，说明对照前提不成立")


def test_download_file_keeps_15mb_cap_and_64kb_chunks(tmp_path, monkeypatch):
    """修法不得破坏原有语义：15MB 上限与 64KB 分块读取必须保留。"""
    seen_reads = []

    class _BigResp:
        status = 200
        headers = {}

        def __init__(self):
            self._left = 20 * 1024 * 1024

        def read(self, n=-1):  # noqa: D102
            seen_reads.append(n)
            if self._left <= 0:
                return b""
            take = min(n if n and n > 0 else 65536, self._left, 65536)
            self._left -= take
            return b"x" * take

        def __enter__(self):  # noqa: D102
            return self

        def __exit__(self, *a):  # noqa: D102
            return False

    monkeypatch.setattr(fetcher_mod, "assert_url_safe", lambda url: url)
    monkeypatch.setattr(fetcher_mod, "safe_urlopen",
                        lambda req, timeout=12, context=None: _BigResp())

    assert fetcher_mod.HTTPFetcher().download_file(
        "http://example.edu.cn/big.pdf", tmp_path / "big.pdf") is False
    assert seen_reads and all(n == 64 * 1024 for n in seen_reads), seen_reads


# ═════════════════ ② DNS 重绑定：把校验过的 IP pin 到连接上 ═════════════════

def test_safe_urlopen_pins_connection_to_validated_ip(monkeypatch):
    """建连目标必须是**校验时**解析出的 IP，而不是建连时二次解析的结果。

    这里刻意让两次解析给出不同答案（校验 -> 公网 IP，建连 -> 回环），
    正是 DNS 重绑定 / 重定向到内网的真实形态。
    """
    recorded = []
    real_gai = socket.getaddrinfo

    def _spy_create(address, timeout=None, source_address=None):
        recorded.append(address)
        raise OSError("stop before any real connect")

    def _rebinding_gai(host, port, *args, **kwargs):
        if host == ENTRY_HOST:
            host = "127.0.0.1"          # 二次解析被攻击者换成回环
        return real_gai(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", _spy_create)
    monkeypatch.setattr(socket, "getaddrinfo", _rebinding_gai)
    monkeypatch.setattr(net_guard, "resolve_host_ips",
                        lambda host: [ipaddress.ip_address(VALIDATED_PUBLIC_IP)])

    req = urllib.request.Request(f"http://{ENTRY_HOST}:8443/")
    with pytest.raises(urllib.error.URLError):
        net_guard.safe_urlopen(req, timeout=2)

    assert recorded, "连接根本没发起，断言没有指向建连那一跳"
    assert recorded[0][0] == VALIDATED_PUBLIC_IP, (
        f"连接目标是二次解析结果而不是已校验 IP: {recorded[0]}")


def test_negative_control_without_pin_connection_uses_unvalidated_hostname(monkeypatch):
    """阴性对照：摘掉 do_open 的 pin 包装后，连接目标变回未经校验的主机名。"""
    recorded = []
    real_gai = socket.getaddrinfo

    def _spy_create(address, timeout=None, source_address=None):
        recorded.append(address)
        raise OSError("stop before any real connect")

    monkeypatch.setattr(socket, "create_connection", _spy_create)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **k: real_gai(
                            "127.0.0.1" if host == ENTRY_HOST else host, port, *a, **k))
    monkeypatch.setattr(net_guard, "resolve_host_ips",
                        lambda host: [ipaddress.ip_address(VALIDATED_PUBLIC_IP)])
    monkeypatch.setattr(net_guard._PinnedConnectionHandler, "do_open",
                        urllib.request.AbstractHTTPHandler.do_open)

    req = urllib.request.Request(f"http://{ENTRY_HOST}:8443/")
    with pytest.raises(urllib.error.URLError):
        net_guard.safe_urlopen(req, timeout=2)

    assert recorded and recorded[0][0] == ENTRY_HOST, (
        f"摘掉 pin 后连接目标仍被钉死，说明断言没指向该守卫: {recorded}")


def test_redirect_target_is_pinned_too(monkeypatch):
    """重定向目标同样要进 pin 表，否则跳转那一跳又回到二次解析。"""
    monkeypatch.setattr(net_guard, "resolve_host_ips",
                        lambda host: [ipaddress.ip_address(VALIDATED_PUBLIC_IP)])
    registry = net_guard.PinRegistry()
    handler = net_guard.SafeRedirectHandler(pin=registry)

    handler.redirect_request(
        urllib.request.Request("http://entry.test/"), None, 302, "Found", {},
        "http://second-hop.test/next")

    assert registry.lookup("second-hop.test") == VALIDATED_PUBLIC_IP
    assert registry.lookup("second-hop.test:8080") == VALIDATED_PUBLIC_IP


def test_assert_url_safe_without_pin_keeps_old_contract():
    """``assert_url_safe`` 的旧调用方式（不传 pin）必须继续可用。"""
    assert net_guard.assert_url_safe("https://93.184.216.34/") == "https://93.184.216.34/"
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe("http://127.0.0.1/")


# ═════════════════ ③ _http 解压炸弹与响应体上限 ═════════════════

def test_decompress_body_caps_decompression_bomb(monkeypatch):
    """8MB 级 gzip 炸弹必须被上限截断，而不是无上限膨胀。"""
    cap = 64 * 1024
    monkeypatch.setattr(_http, "MAX_DECOMPRESSED_BYTES", cap)
    bomb = gzip.compress(b"\x00" * (8 * 1024 * 1024), compresslevel=9)
    assert len(bomb) < cap, "炸弹压缩后应远小于解压后体积"

    out = _http.decompress_body(bomb, {"Content-Encoding": "gzip"})
    assert len(out) == cap, f"未被上限截断，实际 {len(out)} 字节"


def test_negative_control_unbounded_gzip_expands_fully():
    """阴性对照：无上限的 ``gzip.decompress`` 会把同一个炸弹完整解出来。"""
    payload = b"\x00" * (8 * 1024 * 1024)
    bomb = gzip.compress(payload, compresslevel=9)
    assert len(gzip.decompress(bomb)) == len(payload)


def test_get_text_caps_response_read_size(monkeypatch):
    """``get_text`` 读响应体必须带上限，不得裸 ``resp.read()``。"""
    seen = {}

    class _Resp:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self, n=-1):  # noqa: D102
            seen["n"] = n
            return "<html><body>研招简章</body></html>".encode("utf-8")

        def __enter__(self):  # noqa: D102
            return self

        def __exit__(self, *a):  # noqa: D102
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=10, context=None: _Resp())
    text = _http.get_text("https://yz.example.edu.cn/notice.html")

    assert "研招简章" in text
    assert seen.get("n") == _http.MAX_HTTP_RESPONSE_BYTES, (
        f"读取未带上限: n={seen.get('n')}")


def test_read_capped_tolerates_response_without_size_arg():
    """只实现无参 ``read()`` 的轻量响应对象不得因此崩溃（退化为整读）。"""
    class _Legacy:
        def read(self):  # noqa: D102
            return b"legacy-body"

    assert _http._read_capped(_Legacy(), 8) == b"legacy-body"


# ═════════════════ ④ TLS 降级必须由调用方显式 opt-in ═════════════════

class _InsecureSslProbe:
    """第一次（严格 context）抛证书错误；未验证 context 命中则返回正文并记账。"""

    def __init__(self):
        self.contexts = []
        self.insecure_hits = 0

    def __call__(self, req, timeout=10, context=None):
        self.contexts.append(context)
        if context is not None and getattr(context, "check_hostname", True):
            raise urllib.error.URLError(ssl.SSLError("certificate verify failed"))
        self.insecure_hits += 1
        return _fake_response(
            "<html><body>高校自签名站点正文</body></html>".encode("utf-8"),
            {"Content-Type": "text/html; charset=utf-8"})


def test_cert_error_fails_honestly_by_default(monkeypatch):
    """[语义反转] 默认（不传开关）证书错误必须**如实失败**，原因可辨识、绝不降级。

    旧行为：证书错误后静默换 ``CERT_NONE`` 重试并返回正文。现降级已删除，
    默认即「如实失败」，且不得发起任何未验证连接。
    """
    probe = _InsecureSslProbe()
    monkeypatch.setattr(urllib.request, "urlopen", probe)
    status = {}

    with pytest.raises(_http.ProviderError) as excinfo:
        _http.get_text("https://self-signed.example.edu.cn/a", ssl_status=status)

    assert "TLS 证书校验失败" in str(excinfo.value), "失败原因不可辨识（可能被静默降级掩盖）"
    assert probe.insecure_hits == 0, "默认路径下仍发起了未验证连接（静默降级未删除）"
    assert len(probe.contexts) == 1, "默认路径不应有第二次请求"


def test_cert_error_reports_unverified_only_when_explicitly_opted_in(monkeypatch, caplog):
    """显式 ``allow_insecure_ssl=True`` 时才降级，且必须暴露 ``ssl_verified=False`` 并留日志。"""
    probe = _InsecureSslProbe()
    monkeypatch.setattr(urllib.request, "urlopen", probe)
    status = {}

    with caplog.at_level("WARNING"):
        text = _http.get_text("https://self-signed.example.edu.cn/a",
                              allow_insecure_ssl=True, ssl_status=status)

    assert "高校自签名站点正文" in text
    assert status.get("ssl_verified") is False, "未验证 TLS 状态没有暴露给调用方"
    assert any("未验证" in r.message for r in caplog.records), "降级未留日志"


def test_insecure_ssl_fallback_can_be_disabled(monkeypatch):
    """显式 ``allow_insecure_ssl=False`` 时证书错误必须直接报错，不得降级。"""
    probe = _InsecureSslProbe()
    monkeypatch.setattr(urllib.request, "urlopen", probe)
    status = {}

    with pytest.raises(_http.ProviderError):
        _http.get_text("https://self-signed.example.edu.cn/a",
                       allow_insecure_ssl=False, ssl_status=status)

    assert probe.insecure_hits == 0, "禁用降级后仍发起了未验证重试"
    assert len(probe.contexts) == 1, "禁用降级后仍发起了未验证重试"
    assert status.get("ssl_verified") is True


def test_verified_ssl_keeps_status_true(monkeypatch):
    """正常证书链下状态必须是已验证 —— 证明该标记确有区分能力。"""
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=10, context=None: _fake_response(
            b"<html><body>ok</body></html>",
            {"Content-Type": "text/html; charset=utf-8"}))
    status = {}
    _http.get_text("https://yz.example.edu.cn/ok.html", ssl_status=status)
    assert status.get("ssl_verified") is True


# ═════════════════ ⑤ gzip 截断必须被标记 ═════════════════

def test_truncated_gzip_stream_is_marked():
    """被截断的 gzip 流必须返回 ``truncated=True``（旧实现静默返回 False）。"""
    full = gzip.compress(b"kaoyan" * 5000)
    cut = full[: len(full) // 2]

    out, truncated = net_guard.decompress_limited(cut, "gzip")
    assert isinstance(out, bytes)
    assert truncated is True, "半截 gzip 流被当成了完整流"

    out, truncated = net_guard.decompress_limited(full, "gzip")
    assert out == b"kaoyan" * 5000 and truncated is False


def test_negative_control_without_mark_incomplete_truncation_is_lost():
    """阴性对照：关掉 ``mark_incomplete`` 后，同一个半截流的截断状态就丢了。"""
    full = gzip.compress(b"kaoyan" * 5000)
    cut = full[: len(full) // 2]

    _out, truncated = net_guard.zlib_limited(
        cut, 16 + zlib.MAX_WBITS, net_guard.MAX_DECOMPRESSED_BYTES)
    assert truncated is False, "旧语义竟然已经能标记截断，说明对照前提不成立"


# ═════════════════ ⑥ fetcher 截断状态必须如实暴露 ═════════════════

def test_fetcher_marks_truncated_content(monkeypatch):
    """响应被体积上限截断时：``truncated=True`` 且正文尾部带标记。"""
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", lambda url: url)
    monkeypatch.setattr(fetcher_mod, "MAX_HTTP_RESPONSE_BYTES", 32)
    body = b"<html><body>" + b"kaoyan" * 40 + b"</body></html>"
    monkeypatch.setattr(
        fetcher_mod, "safe_urlopen",
        lambda req, timeout=6, context=None: _fake_response(
            body, {"Content-Type": "text/html; charset=utf-8"}))

    res = fetcher_mod.HTTPFetcher().fetch("https://yjs.example.edu.cn/a.html")

    assert res.is_valid is True
    assert res.truncated is True, "截断状态没有暴露"
    assert net_guard.TRUNCATION_MARKER in res.content
    assert res.raw_bytes_len <= 32


def test_fetcher_does_not_mark_complete_content(monkeypatch):
    """完整响应不得被误标 —— 证明 ``truncated`` 确有区分能力。"""
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", lambda url: url)
    monkeypatch.setattr(
        fetcher_mod, "safe_urlopen",
        lambda req, timeout=6, context=None: _fake_response(
            b"<html><body>short</body></html>",
            {"Content-Type": "text/html; charset=utf-8"}))

    res = fetcher_mod.HTTPFetcher().fetch("https://yjs.example.edu.cn/b.html")

    assert res.is_valid is True
    assert res.truncated is False
    assert net_guard.TRUNCATION_MARKER not in res.content
