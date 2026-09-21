# -*- coding: utf-8 -*-
"""收尾修复回归 · ``fetcher.fetch`` 静默 TLS 降级 / ``DEFAULT_CONFIG.webhook_token``。

缺陷现场
--------
1. **同一语义两套实现**：上一轮已把 ``tools/search/providers/_http.py`` 的
   「证书错误即静默换 ``CERT_NONE`` 重试」改成「默认不降级，仅调用方显式
   ``allow_insecure_ssl=True`` 才降级，且打 WARNING + 暴露 ``ssl_verified=False``」，
   但 ``tools/intelligence/fetcher.py`` 的 ``HTTPFetcher.fetch`` 里还留着**另一套
   独立的自动降级**：``attempt == 0`` 命中 SSL 错误就切到
   ``_FALLBACK_UNVERIFIED_SSL_CONTEXT`` 并 ``continue``，**没有任何调用方开关**，
   只在事后用 ``ssl_verified=False`` / ``access_status="UNVERIFIED_SSL"`` 留痕。
   即「证书无效的站点照样被抓，且默认路径就降级」。

2. **``webhook_token`` 未进 ``DEFAULT_CONFIG``**：该键已接入 ``ky_config.json``
   顶层与 ``resolve_webhook_secret``，但 ``tools/cli/shared.py`` 的默认配置里没有，
   于是「全新安装 / 配置损坏回退默认」时该键缺席。

隔离约定
--------
* 不发起任何真实网络请求：``safe_urlopen`` / ``assert_url_safe`` 全部 monkeypatch；
* 不写真实 ``ky_config.json``：``CONFIG_FILE`` 显式指向 ``tmp_path``；
* 测试数据一律中性占位（``*.example.test``、``PLACEHOLDER-*``），无真实身份。
"""

from __future__ import annotations

import logging
import ssl
import urllib.error

import pytest

from tools.cli import shared as shared_mod
from tools.intelligence import fetcher as fetcher_mod

CERT_URL = "https://self-signed.example.test/notice/1.html"
OK_URL = "https://yjs.example.test/a.html"

#: 占位密钥（绝不用真实凭证）
PLACEHOLDER_TOKEN = "PLACEHOLDER-WEBHOOK-TOKEN-0001"


def _fake_response(body: bytes, headers: dict):
    class _Resp:
        status = 200

        def __init__(self):
            self.headers = headers

        def read(self, n=None):
            return body if n is None else body[:n]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Resp()


class _SslProbe:
    """严格 context 抛证书错误；未验证 context 命中则返回正文并记账。"""

    def __init__(self, body: bytes = "<html><body>正文</body></html>".encode("utf-8")):
        self.body = body
        self.contexts = []
        self.insecure_hits = 0

    def __call__(self, req, timeout=6, context=None):
        self.contexts.append(context)
        if context is not None and getattr(context, "check_hostname", True):
            raise urllib.error.URLError(ssl.SSLError("certificate verify failed"))
        self.insecure_hits += 1
        return _fake_response(self.body, {"Content-Type": "text/html; charset=utf-8"})


@pytest.fixture
def no_ssrf_guard(monkeypatch):
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", lambda url: url)


# ═════════════════ ① fetcher 默认不得静默降级 ═════════════════

def test_fetch_cert_error_fails_honestly_by_default(monkeypatch, no_ssrf_guard):
    """[语义反转] 默认（不传开关）证书错误必须**如实失败**，且绝不发起未验证连接。

    旧行为：证书错误后自动换 ``CERT_NONE`` 重试，返回 ``is_valid=True`` +
    ``access_status="UNVERIFIED_SSL"`` 的正文。
    """
    probe = _SslProbe()
    monkeypatch.setattr(fetcher_mod, "safe_urlopen", probe)

    res = fetcher_mod.HTTPFetcher().fetch(CERT_URL)

    assert res.is_valid is False, "证书无效却拿到了正文（静默降级仍在）"
    assert res.content == ""
    assert res.access_status == "TLS_CERT_ERROR", (
        f"失败原因不可辨识（可能被静默降级掩盖）: {res.access_status}")
    assert probe.insecure_hits == 0, "默认路径下仍发起了未验证连接（自动降级未删除）"
    assert len(probe.contexts) == 1, "默认路径不应有第二次请求"


def test_fetch_reports_unverified_only_when_explicitly_opted_in(monkeypatch, caplog, no_ssrf_guard):
    """显式 ``allow_insecure_ssl=True`` 才降级，且必须暴露 ``ssl_verified=False`` 并留日志。"""
    probe = _SslProbe()
    monkeypatch.setattr(fetcher_mod, "safe_urlopen", probe)

    with caplog.at_level(logging.WARNING):
        res = fetcher_mod.HTTPFetcher().fetch(CERT_URL, allow_insecure_ssl=True)

    assert res.is_valid is True and res.status_code == 200
    assert res.access_status == "UNVERIFIED_SSL"
    assert res.ssl_verified is False, "未验证 TLS 状态没有暴露给调用方"
    assert probe.insecure_hits == 1
    assert any("未验证" in r.getMessage() for r in caplog.records), "降级未留日志"


def test_fetch_verified_ssl_keeps_status_true(monkeypatch, no_ssrf_guard):
    """正常证书链下必须是已验证且无降级 —— 证明该标记确有区分能力。"""
    monkeypatch.setattr(
        fetcher_mod, "safe_urlopen",
        lambda req, timeout=6, context=None: _fake_response(
            b"<html><body>ok</body></html>", {"Content-Type": "text/html; charset=utf-8"}))

    res = fetcher_mod.HTTPFetcher().fetch(OK_URL)

    assert res.is_valid is True and res.access_status == "OK"
    assert res.ssl_verified is True


def test_fetch_non_ssl_error_is_not_retried_as_downgrade(monkeypatch, no_ssrf_guard):
    """重试 ≠ 降级：普通网络错误既不加次数、也不换未验证上下文。"""
    calls = []

    def _timeout(req, timeout=6, context=None):
        calls.append(context)
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(fetcher_mod, "safe_urlopen", _timeout)

    res = fetcher_mod.HTTPFetcher().fetch(OK_URL)

    assert res.access_status == "TIMEOUT"
    assert len(calls) == 1, "非证书错误被额外重试（重试与降级耦合未理清）"
    assert all(getattr(c, "check_hostname", True) for c in calls)


def test_negative_control_old_auto_downgrade_would_hit_insecure_ctx(monkeypatch):
    """阴性对照：复刻**旧**实现（``attempt==0`` 证书错误即换 ``CERT_NONE`` 重试），
    证明本文件的探针确实能区分「降级」与「不降级」。

    若把 ``fetch`` 里的修复注释掉，``test_fetch_cert_error_fails_honestly_by_default``
    就会退化成这里演示的形状：``insecure_hits == 1`` 且拿到正文。
    """
    probe = _SslProbe()
    monkeypatch.setattr(fetcher_mod, "safe_urlopen", probe)

    # —— 旧逻辑（已从产品代码删除，仅在本对照中复刻）——
    ssl_ctx = fetcher_mod._DEFAULT_SSL_CONTEXT
    is_fallback_ssl = False
    content = ""
    for attempt in range(2):
        try:
            with fetcher_mod.safe_urlopen(None, timeout=6, context=ssl_ctx) as resp:
                content = resp.read()
                break
        except urllib.error.URLError as e:
            reason_str = str(e.reason).lower()
            if attempt == 0 and (isinstance(e.reason, ssl.SSLError) or "ssl" in reason_str):
                ssl_ctx = fetcher_mod._FALLBACK_UNVERIFIED_SSL_CONTEXT
                is_fallback_ssl = True
                continue
            break

    assert probe.insecure_hits == 1, "对照前提不成立：旧逻辑竟然没有降级"
    assert content, "对照前提不成立：旧逻辑竟然没拿到正文"
    assert is_fallback_ssl is True


# ═════════════════ ② DEFAULT_CONFIG 必须声明 webhook_token ═════════════════

def test_default_config_declares_webhook_token():
    """默认配置必须含 ``webhook_token``（空串），否则配置回退默认时该键缺席。"""
    assert "webhook_token" in shared_mod.DEFAULT_CONFIG
    assert shared_mod.DEFAULT_CONFIG["webhook_token"] == ""


def test_load_config_falls_back_to_empty_webhook_token(tmp_path, monkeypatch):
    """旧配置文件（无该键）合并默认值后必须补出空串，而不是 ``KeyError``/缺键。"""
    target = tmp_path / "ky_config.json"
    target.write_text('{"api_key": "PLACEHOLDER-KEY"}', encoding="utf-8")
    monkeypatch.setattr(shared_mod, "CONFIG_FILE", target)

    cfg = shared_mod.load_config()

    assert cfg.get("webhook_token") == ""


def test_load_config_keeps_configured_webhook_token(tmp_path, monkeypatch):
    """已配置的 ``webhook_token`` 不得被默认值覆盖，且必须能被 resolver 读到。"""
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)  # 环境变量优先级更高，先屏蔽
    target = tmp_path / "ky_config.json"
    target.write_text(
        '{"api_key": "PLACEHOLDER-KEY", "webhook_token": "%s"}' % PLACEHOLDER_TOKEN,
        encoding="utf-8")
    monkeypatch.setattr(shared_mod, "CONFIG_FILE", target)

    cfg = shared_mod.load_config()

    assert cfg.get("webhook_token") == PLACEHOLDER_TOKEN
    from tools.cli.gateway import resolve_webhook_secret
    assert resolve_webhook_secret("", cfg) == PLACEHOLDER_TOKEN
