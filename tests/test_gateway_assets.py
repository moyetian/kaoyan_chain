# -*- coding: utf-8 -*-
"""网关 /assets/ 静态资源服务回归测试（伴侣页公式乱码修复）。

根因：REPL 本地网关（默认 127.0.0.1:8088）在浏览器访问 /live 时返回
docs/live.html，页面以相对路径引用 KaTeX / marked（assets/vendor/...），
浏览器解析为 /assets/... 请求；此前 do_GET 无 /assets/ 分支，资源全部 404，
公式渲染降级为源码串（用户看到的「公式乱码」）。

覆盖：
  * /assets/ 命中真实第三方库文件（js/css/woff2/svg 的 Content-Type 映射）；
  * 配了 KY_GATEWAY_TOKEN 时静态资源仍可加载（<script>/<link> 无法带 token 头），
    而 /live、/api/live 仍走 token 闸门（P0-3 语义不被破坏）；
  * 穿越攻击（字面 ..、%2e%2e、..%2f、反斜杠）一律 404 且不泄漏 assets 根外文件；
  * 不存在文件 / 目录 / 空相对路径 → 404；
  * 资源根回退顺序：docs/assets 优先，缺失时回退 05-考研看板/docs/assets。

辅助函数（_serve / _base / _get_full / _get）沿用
tests/test_security_audit_gateway.py 的既有模式。
"""

from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request

import pytest

from tools.cli import gateway as gateway_mod
from tools.cli.gateway import clear_live_messages, create_gateway_handler


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


def _get(url, headers=None):
    status, body, _ = _get_full(url, headers)
    return status, body


def _raw_get(server, raw_path: str):
    """以原始请求行发送 path（不经客户端归一化），返回 (状态码, 响应体)。

    穿越用例必须走原始 socket：urllib 等客户端可能先行归一化 ``..``，
    会掩盖服务端自身的防护是否生效。
    """
    port = server.server_address[1]
    req = (
        f"GET {raw_path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(req)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split(b" ")[1])
    return status, body


# ── ① 真实静态资源可加载（公式乱码根因） ─────────────────────────────────

@pytest.mark.parametrize("rel_path,ctype_part", [
    ("vendor/katex/0.16.9/dist/katex.min.js", "javascript"),
    ("vendor/katex/0.16.9/dist/katex.min.css", "text/css"),
    ("vendor/katex/0.16.9/dist/fonts/KaTeX_AMS-Regular.woff2", "font/woff2"),
    ("vendor/marked/12.0.2/marked.min.js", "javascript"),
    ("readme_system_overview.svg", "image/svg+xml"),
])
def test_asset_served_with_expected_content_type(rel_path, ctype_part):
    """live.html 引用的真实资源可经 /assets/ 加载，Content-Type 正确且带缓存头。"""
    server = _serve(create_gateway_handler())
    try:
        status, body, headers = _get_full(f"{_base(server)}/assets/{rel_path}")
        assert status == 200, rel_path
        assert ctype_part in headers.get("Content-Type", ""), \
            f"{rel_path}: {headers.get('Content-Type')!r}"
        assert body, f"{rel_path} 响应体为空"
        assert headers.get("Cache-Control") == "public, max-age=86400"
    finally:
        server.shutdown()
        server.server_close()


# ── ② 鉴权豁免：静态资源可加载，页面与 API 仍受 token 闸门保护 ────────────

def test_assets_exempt_from_token_but_pages_and_api_still_guarded():
    """配 token 后：/assets/ 无需 token（<script>/<link> 带不了头）；/live 与 /api/live 仍 401。"""
    server = _serve(create_gateway_handler(token="我的密钥"))
    try:
        base = _base(server)
        status, body = _get(f"{base}/assets/vendor/katex/0.16.9/dist/katex.min.js")
        assert status == 200
        assert body
        assert _get(f"{base}/live")[0] == 401
        assert _get(f"{base}/api/live")[0] == 401
    finally:
        server.shutdown()
        server.server_close()


# ── ③ 穿越攻击：一律 404 且不泄漏 assets 根外文件 ────────────────────────

@pytest.mark.parametrize("raw_path", [
    "/assets/../live.html",                 # 字面 ..
    "/assets/%2e%2e/live.html",             # 编码点
    "/assets/..%2fky_config.json",          # 编码斜杠
    "/assets/%2e%2e%2fky_config.json",
    "/assets/..%5Cky_config.json",          # Windows 反斜杠
    "/assets/../ky_config.json",
    "/assets/%2e%2e/ky_config.json",
    "/assets//ky_config.json",              # 双斜杠（rooted 变体）
])
def test_traversal_blocked_with_404(raw_path):
    server = _serve(create_gateway_handler())
    try:
        status, body = _raw_get(server, raw_path)
        assert status == 404, f"{raw_path} → {status}"
        assert b"<!DOCTYPE html>" not in body, f"越界读到 docs/live.html: {raw_path}"
        assert b"api_provider" not in body, f"越界读到 ky_config.json: {raw_path}"
    finally:
        server.shutdown()
        server.server_close()


def test_traversal_targets_exist_outside_assets_root():
    """对照组：穿越目标文件真实存在（否则上面的 404 断言可能是空转）。"""
    root = gateway_mod.ROOT
    live = root / "docs" / "live.html"
    assert live.is_file(), "对照组失效：docs/live.html 不存在"
    assert b"<!DOCTYPE html>" in live.read_bytes()
    cfg = root / "ky_config.json"
    if cfg.is_file():
        assert b"api_provider" in cfg.read_bytes(), "对照组失效：ky_config.json 无特征键"


# ── ④ 不存在文件 / 目录 / 空路径 → 404 ───────────────────────────────────

@pytest.mark.parametrize("path", [
    "/assets/vendor/nonexistent.js",
    "/assets/vendor/katex/0.16.9/dist/",   # 目录（带尾斜杠）
    "/assets/",                             # 空相对路径 → 资源根目录本身
    "/assets/does-not-exist/",
])
def test_missing_or_directory_assets_return_404(path):
    server = _serve(create_gateway_handler())
    try:
        status, body = _get(f"{_base(server)}{path}")
        assert status == 404, f"{path} → {status}"
        assert b"api_provider" not in body
    finally:
        server.shutdown()
        server.server_close()


# ── ⑤ 资源根回退顺序 + 未知后缀 ──────────────────────────────────────────

def test_assets_root_fallback_preference_and_unknown_extension(tmp_path, monkeypatch):
    """docs/assets 优先；缺失时回退 05-考研看板/docs/assets；未知后缀 octet-stream。"""
    # 场景 1：无 docs/assets → 回退 05-考研看板/docs/assets
    fb = tmp_path / "05-考研看板" / "docs" / "assets" / "vendor"
    fb.mkdir(parents=True)
    (fb / "x.js").write_bytes(b"console.log('fallback');")
    (fb / "blob.bin").write_bytes(b"\x00\x01\x02")
    monkeypatch.setattr(gateway_mod, "ROOT", tmp_path)
    server = _serve(create_gateway_handler())
    try:
        base = _base(server)
        status, body, _ = _get_full(f"{base}/assets/vendor/x.js")
        assert (status, body) == (200, b"console.log('fallback');")
        status, body, headers = _get_full(f"{base}/assets/vendor/blob.bin")
        assert status == 200
        assert headers.get("Content-Type", "").startswith("application/octet-stream")
    finally:
        server.shutdown()
        server.server_close()

    # 场景 2：docs/assets 出现后优先于回退目录
    pref = tmp_path / "docs" / "assets" / "vendor"
    pref.mkdir(parents=True)
    (pref / "x.js").write_bytes(b"console.log('primary');")
    server = _serve(create_gateway_handler())
    try:
        status, body = _get(f"{_base(server)}/assets/vendor/x.js")
        assert (status, body) == (200, b"console.log('primary');")
    finally:
        server.shutdown()
        server.server_close()
