# -*- coding: utf-8 -*-
"""HTTPFetcher 瞬时故障指数退避重试测试（AWS full-jitter 口径）

[背景·为什么有这些用例] R3 全矩阵仿真（3 考生 × 6 环节 × 3 端 = 54 格）实测到
研招网抓取频现 ``RemoteDisconnected``，而改动前 HTTP 层**只尝试一次**（``max_attempts
= 2 if allow_insecure_ssl else 1``）——一次瞬时抖动即判 ``BLOCKED``，同一院校在不同
轮次给出「完整研报」与「UNVERIFIED 降级」两种结果，能力波动远超 15%。本文件钉住
修复后的语义，重点是**不该重试的一律不重试**（证书/403/404）与**对外枚举值不变**
（下游 watcher / scout_engine / wechat_searcher 零改动）。

用例分组：
  A. 可重试的瞬时故障 → 退避重试（传输层 / 5xx / 429 尊重 Retry-After）
  B. 不可重试的永久错误 → 一次即返回（证书 / 403 / 404）
  C. 对外契约不变（BLOCKED/TIMEOUT 语义、异常文本不外泄、opt-in 回归）
  D. 退避公式本身（full jitter 上界随尝试递增、不真实 sleep、可退回旧口径）
"""
from __future__ import annotations

import socket
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.intelligence import fetcher as fetcher_mod  # noqa: E402

URL = "https://yz.example.edu.cn/kyxm.html"
OK_BODY = ("<html><body>" + "招生简章正文内容。" * 60 + "</body></html>").encode("utf-8")


class _FakeResp:
    def __init__(self, body: bytes = OK_BODY, status: int = 200, headers=None):
        self.status = status
        self.headers = dict(headers or {})
        self._body = body

    def read(self, n: int = -1) -> bytes:
        return self._body[:n] if n and n > 0 else self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture()
def no_sleep(monkeypatch):
    """退避不真实等待：记录每次 sleep 时长供断言。"""
    slept: list[float] = []
    monkeypatch.setattr(fetcher_mod.time, "sleep", lambda s: slept.append(float(s)))
    return slept


@pytest.fixture()
def allow_all_urls(monkeypatch):
    monkeypatch.setattr(fetcher_mod, "assert_url_safe", lambda *_a, **_k: None)


def _patch_open(monkeypatch, outcomes: list):
    """按 outcomes 依次返回响应或抛出异常；记录调用次数。"""
    calls: list = []

    def _open(_req, **_kw):
        calls.append(1)
        item = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(fetcher_mod, "safe_urlopen", _open)
    return calls


def _urLError(reason):
    return urllib.error.URLError(reason)


def _http_error(code: int, headers=None):
    return urllib.error.HTTPError(URL, code, "err", headers or {}, None)


# ── A. 可重试的瞬时故障 ────────────────────────────────────────────────

def test_remote_disconnected_then_success(monkeypatch, allow_all_urls, no_sleep):
    """研招网典型故障：首次 RemoteDisconnected、退避后成功 → OK（修复前直接失败）。"""
    calls = _patch_open(monkeypatch, [
        _urLError(ConnectionResetError(
            "Remote end closed connection without response")),
        _FakeResp(),
    ])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert res.access_status == "OK" and res.is_valid is True
    assert len(calls) == 2, "瞬时故障应重试一次"
    assert len(no_sleep) == 1 and no_sleep[0] >= 0.0


def test_connection_reset_exhausted_keeps_blocked_semantics(monkeypatch, allow_all_urls, no_sleep):
    """瞬时故障耗尽重试 → 仍返回 BLOCKED（对外枚举值不变，下游零影响）。"""
    calls = _patch_open(monkeypatch, [
        _urLError(ConnectionResetError("Connection reset by peer"))])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert res.access_status == "BLOCKED" and res.is_valid is False
    assert len(calls) == 1 + fetcher_mod._DEFAULT_RETRY_ATTEMPTS
    assert len(no_sleep) == fetcher_mod._DEFAULT_RETRY_ATTEMPTS


def test_timeout_exhausted_keeps_timeout_semantics(monkeypatch, allow_all_urls, no_sleep):
    """超时耗尽 → 仍返回 TIMEOUT（与改动前一致）。"""
    _patch_open(monkeypatch, [_urLError(socket.timeout("timed out"))])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert res.access_status == "TIMEOUT"


def test_http_503_retries(monkeypatch, allow_all_urls, no_sleep):
    calls = _patch_open(monkeypatch, [_http_error(503), _FakeResp()])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert res.access_status == "OK" and len(calls) == 2


def test_http_429_honors_retry_after(monkeypatch, allow_all_urls, no_sleep):
    """429 优先遵守服务端 Retry-After（反爬站点常用它告知冷却时长）。"""
    _patch_open(monkeypatch, [_http_error(429, {"Retry-After": "3"}), _FakeResp()])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert res.access_status == "OK"
    assert no_sleep and abs(no_sleep[0] - 3.0) < 1e-6


# ── B. 不可重试的永久错误 ──────────────────────────────────────────────

def test_403_never_retries(monkeypatch, allow_all_urls, no_sleep):
    calls = _patch_open(monkeypatch, [_http_error(403)])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert res.access_status == "HTTP_403"
    assert len(calls) == 1, "403 是永久错误，重试只会加深封禁"
    assert not no_sleep


def test_404_never_retries(monkeypatch, allow_all_urls, no_sleep):
    calls = _patch_open(monkeypatch, [_http_error(404)])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert res.access_status == "HTTP_404" and len(calls) == 1


def test_certificate_error_never_retries_by_default(monkeypatch, allow_all_urls, no_sleep):
    """TLS 口径回归：默认不重试也不降级（重试 ≠ 降级）。"""
    calls = _patch_open(monkeypatch, [
        _urLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert res.access_status == "TLS_CERT_ERROR"
    assert len(calls) == 1, "证书错误不属瞬时故障，绝不重试"


# ── C. 对外契约不变 ────────────────────────────────────────────────────

def test_transient_exhausted_does_not_leak_exception_text(monkeypatch, allow_all_urls, no_sleep):
    """S9 铁律：access_status 仅枚举值，异常详情只进日志。"""
    secret = "Remote end closed connection without response / 10.0.0.1:443"
    _patch_open(monkeypatch, [_urLError(ConnectionResetError(secret))])
    res = fetcher_mod.HTTPFetcher(timeout=1).fetch(URL)
    assert secret not in res.access_status
    assert res.access_status in {"BLOCKED", "TIMEOUT"}


def test_retry_attempts_zero_restores_single_attempt(monkeypatch, allow_all_urls, no_sleep):
    """retry_attempts=0 → 退回改动前的「只试一次」口径（可配置性回归）。"""
    calls = _patch_open(monkeypatch, [_urLError(ConnectionResetError("reset"))])
    res = fetcher_mod.HTTPFetcher(timeout=1, retry_attempts=0).fetch(URL)
    assert res.access_status == "BLOCKED" and len(calls) == 1 and not no_sleep


# ── D. 退避公式 ────────────────────────────────────────────────────────

def test_full_jitter_ceiling_grows_and_is_capped():
    """full jitter：sleep ∈ [0, min(cap, base·2^n)]，上界递增且受 cap 约束。"""
    prev_ceiling = 0.0
    for attempt in range(0, 8):
        ceiling = min(fetcher_mod._RETRY_MAX_DELAY,
                      fetcher_mod._RETRY_BASE_DELAY * (2 ** attempt))
        for _ in range(50):
            d = fetcher_mod._full_jitter_delay(attempt)
            assert 0.0 <= d <= ceiling + 1e-9
        assert ceiling >= prev_ceiling
        prev_ceiling = ceiling
    assert prev_ceiling == fetcher_mod._RETRY_MAX_DELAY


def test_transient_classifier_boundaries():
    """瞬时判定边界：传输层故障为真，证书/普通 4xx 词面为假。"""
    for reason in ("Remote end closed connection without response",
                   "Connection reset by peer",
                   "timed out",
                   "Broken pipe",
                   "HTTP 502 Bad Gateway"):
        assert fetcher_mod._is_transient_network_error(reason) is True, reason
    for reason in ("CERTIFICATE_VERIFY_FAILED",
                   "certificate verify failed",
                   "SSL: WRONG_VERSION_NUMBER",
                   "HTTP Error 404: Not Found",
                   "403 Forbidden"):
        assert fetcher_mod._is_transient_network_error(reason) is False, reason
