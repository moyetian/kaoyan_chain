# -*- coding: utf-8 -*-
"""[审计 2026-09-30] 出站收敛（P1-7）/ 内联 JSON 转义（P1-10）/ 看板读取缓存（PF-6）回归。

覆盖：
1. P1-7 出站收敛：
   * doctor 模型探活必须经模块级 ``safe_urlopen``（裸 ``urllib.request.urlopen`` 被禁用）；
   * ``_http.get_text`` 对云元数据地址（169.254.169.254）fail-closed 拒绝且不建连，
     且发起请求的接缝就是 ``safe_urlopen``（自定义 SSL context 语义保留）；
   * notify webhook 推送经 ``safe_urlopen``，指向元数据地址时如实返回失败；
   * ``SafeRedirectHandler`` 302 跨主机跳转剥离 ``Authorization``（同主机保留）——
     防恶意 base_url 用 302 收割 API Key。
2. P1-10 内联 JSON 转义：``json_inline_escape`` 一次性覆盖五个序列
   （``&``/``<``/``>``/U+2028/U+2029），``<``→``\\u003c`` 天然覆盖 ``</script>``；
   ``str.translate`` 单遍映射，``\\u0026`` 不得被二次转义成 ``\\u005cu0026``。
3. PF-6 看板读取缓存：单次 ``build()`` 内同一源文件只读一次，缓存不跨构建复用。

约定：**零真实外网** —— SSRF 用例只指向回环/链路本地（校验在建连前拒绝），
其余出站调用全部以 monkeypatch 打桩（不得依赖 DNS 可解析）。
"""

from __future__ import annotations

import importlib
import ipaddress
import json
import socket
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
for _p in (str(ROOT), str(ROOT / "tools"), str(DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import net_guard  # noqa: E402

#: 被「校验」认定为公网、可放行的 IP（TEST 文档地址，非真实业务地址）
VALIDATED_PUBLIC_IP = "93.184.216.34"
METADATA_URL = "http://169.254.169.254/latest/meta-data/"


def _silence_mcp_probe(monkeypatch):
    """把 doctor 的 MCP 探活替换为 no-op（不启动任何外部进程），双导入路径都覆盖。"""
    for name in ("tools.agent.mcp_client", "agent.mcp_client"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(mod.MCPClientManager, "load_from_config",
                            lambda self, *a, **k: None)


# ═════════════════ ① P1-7：doctor 探活经过 safe_urlopen ═════════════════

def test_doctor_probe_uses_safe_urlopen_never_raw(monkeypatch, tmp_path, capsys):
    """doctor 的模型探活必须经模块级 safe_urlopen，且不得再用裸 urlopen 出站。

    阴性对照：摘掉 safe_urlopen 打桩（或让实现退回裸 urlopen）时，
    ``forbidden_raw`` 会让本用例立刻失败 —— 而不是悄悄真实联网。
    """
    from tools import doctor as doctor_mod

    # 最小隔离工作区：只有一份「已配置有效 Key」的配置，驱动探活分支。
    (tmp_path / "ky_config.json").write_text(json.dumps({
        "api_key": "sk-test-outbound-0000000000",
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(doctor_mod, "ROOT", tmp_path)
    _silence_mcp_probe(monkeypatch)

    seen = []

    def fake_safe_urlopen(req, timeout=12, context=None):
        seen.append((getattr(req, "full_url", str(req)), timeout))
        raise OSError("打桩：测试禁止真实网络")

    def forbidden_raw(*_a, **_k):
        raise AssertionError("doctor 不得再用裸 urllib.request.urlopen 出站")

    monkeypatch.setattr(doctor_mod, "safe_urlopen", fake_safe_urlopen)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_raw)

    doctor_mod.run_doctor(return_summary=True)
    capsys.readouterr()

    assert seen, "探活没有经过 safe_urlopen（疑似退回裸 urlopen）"
    assert any("api.example.com" in url for url, _t in seen), seen
    assert any(timeout == 4 for _url, timeout in seen), f"探活超时参数变化: {seen}"


# ═════════════════ ② P1-7：_http.get_text 出站收敛 ═════════════════

def test_get_text_rejects_cloud_metadata_ip_fail_closed(monkeypatch):
    """阴性对照：目标为云元数据地址时必须 fail-closed 拒绝，且**不发起任何连接**。"""
    from tools.search.providers import _http

    def no_connect(*_a, **_k):
        raise AssertionError("SSRF 校验必须在建连之前拒绝")

    monkeypatch.setattr(socket, "create_connection", no_connect)

    with pytest.raises(_http.ProviderError) as excinfo:
        _http.get_text(METADATA_URL)
    assert "安全校验" in str(excinfo.value), str(excinfo.value)


def test_get_text_routes_requests_through_safe_urlopen(monkeypatch):
    """get_text 的发起请求接缝必须是模块级 safe_urlopen（而非裸 urlopen）。"""
    from tools.search.providers import _http

    seen = {}

    class _Resp:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def read(self, n=-1):  # noqa: D102
            return "<html><body>研招简章</body></html>".encode("utf-8")

        def __enter__(self):  # noqa: D102
            return self

        def __exit__(self, *a):  # noqa: D102
            return False

    def fake_safe(req, timeout=10, context=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["context"] = context
        return _Resp()

    monkeypatch.setattr(_http, "safe_urlopen", fake_safe)
    text = _http.get_text("https://yz.example.test/notice.html", timeout=7)

    assert "研招简章" in text
    assert seen["url"].startswith("https://yz.example.test/")
    assert seen["timeout"] == 7
    assert seen["context"] is not None, "自定义 SSL context 语义丢失"


def test_get_text_rejects_loopback_literal_without_request(monkeypatch):
    """回环字面量同样 fail-closed（且不建连）——覆盖 IP 字面量变体。"""
    from tools.search.providers import _http

    def no_connect(*_a, **_k):
        raise AssertionError("回环目标必须在建连前拒绝")

    monkeypatch.setattr(socket, "create_connection", no_connect)
    with pytest.raises(_http.ProviderError):
        _http.get_text("http://127.0.0.1:8080/admin")


# ═════════════════ ③ P1-7：notify webhook 出站收敛 ═════════════════

class _NotifyResp:
    def read(self, _limit=-1):  # noqa: D102
        return b'{"errcode": 0, "errmsg": "ok"}'

    def __enter__(self):  # noqa: D102
        return self

    def __exit__(self, *a):  # noqa: D102
        return False


def test_notify_webhook_routes_through_safe_urlopen(monkeypatch):
    """四个 IM 推送接缝均为模块级 safe_urlopen（以钉钉为样本验证）。"""
    from tools.cli import notify as notify_mod

    seen = {}

    def fake_safe(req, timeout=10, context=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _NotifyResp()

    monkeypatch.setattr(notify_mod, "safe_urlopen", fake_safe)

    ok, _msg = notify_mod.send_to_dingtalk(
        "https://oapi.dingtalk.com/robot/send?access_token=test", "hello")
    assert ok is True
    assert "oapi.dingtalk.com" in seen["url"]
    assert seen["timeout"] == 10


def test_notify_webhook_rejects_metadata_url(monkeypatch):
    """阴性对照：webhook 指向云元数据地址时如实失败（不静默、不建连）。"""
    from tools.cli import notify as notify_mod

    def no_connect(*_a, **_k):
        raise AssertionError("元数据地址必须在建连前拒绝")

    monkeypatch.setattr(socket, "create_connection", no_connect)

    ok, reason = notify_mod.send_to_wechat(METADATA_URL, "hello")
    assert ok is False
    assert "安全" in reason or "拦截" in reason, reason


# ═════════════════ ④ P1-7：302 跨主机跳转剥离 Authorization ═════════════════

def test_safe_redirect_strips_authorization_on_cross_host(monkeypatch):
    """302 跨主机跳转必须剥离 Authorization（同主机跳转保留）。

    与既有 ``test_fix_net_ssrf_pinning.py::test_redirect_target_is_pinned_too``
    （跳转目标进 pin 表）互补：本条锁定「跳转泄漏 Bearer」防护本身。
    """
    monkeypatch.setattr(net_guard, "resolve_host_ips",
                        lambda host: [ipaddress.ip_address(VALIDATED_PUBLIC_IP)])

    handler = net_guard.SafeRedirectHandler()
    req = urllib.request.Request(
        "https://api.example.com/v1/chat/completions",
        headers={"Authorization": "Bearer sk-secret"})

    same_host = handler.redirect_request(
        req, None, 302, "Found", {}, "https://api.example.com/v1/other")
    assert same_host.get_header("Authorization") == "Bearer sk-secret"

    cross_host = handler.redirect_request(
        req, None, 302, "Found", {}, "https://evil.example.net/v1/chat")
    assert cross_host.get_header("Authorization") is None, (
        "跨主机 302 未剥离 Authorization —— API Key 可被恶意 base_url 收割")


def test_redirect_to_metadata_is_blocked():
    """302 指向云元数据地址时，跳转请求本身必须被拒绝（fail-closed）。

    这里刻意**不**打桩 ``resolve_host_ips``：IP 字面量走真实判定路径
    （link-local → 拒绝），证明防护对字面量地址确实生效。
    """
    handler = net_guard.SafeRedirectHandler()
    req = urllib.request.Request("https://api.example.com/v1/chat")

    with pytest.raises(net_guard.UnsafeURLError):
        handler.redirect_request(req, None, 302, "Found", {}, METADATA_URL)


# ═════════════════ ⑤ P1-10：内联 JSON 五序列转义 ═════════════════

def test_json_inline_escape_covers_five_sequences_without_double_escape():
    """`</script>` / `&` / `<` / U+2028 / U+2029 必须一次性全部转义。"""
    import build as build_mod  # noqa: PLC0415 —— 05-考研看板/build.py
    from web.theme_vars import json_inline_escape

    assert build_mod.json_inline_escape is json_inline_escape, (
        "build.py 与 theme_vars 必须共用同一个转义函数（单一真源）")

    src = "x</script><b>&amp;</b>\u2028\u2029"
    out = json_inline_escape(src)

    assert "</script>" not in out, "裸 </script> 可提前闭合内联脚本块"
    assert "<" not in out and ">" not in out and "&" not in out
    assert "\u2028" not in out and "\u2029" not in out, "裸行终止符会让 JS 解析器断行"
    assert "\\u003c" in out and "\\u003e" in out and "\\u0026" in out
    assert "\\u2028" in out and "\\u2029" in out
    # 单遍 translate 映射：替换产物不得被二次转义（\u0026 → \u005cu0026 是典型坏形态）
    assert "\\u005c" not in out, f"出现二次转义: {out!r}"


def test_json_inline_escape_preserves_json_semantics():
    """转义只是字节层防护：JSON 反解后的语义必须与原文完全一致。"""
    from web.theme_vars import json_inline_escape

    data = {"note": "a<b>&c</script>\u2028d", "n": 1, "cn": "考研"}
    raw = json.dumps(data, ensure_ascii=False)
    assert json.loads(json_inline_escape(raw)) == data


# ═════════════════ ⑥ PF-6：看板构建读取缓存 ═════════════════

def test_sections_share_source_files_precondition():
    """前置事实：SECTIONS 条目数 > 去重后的源文件数（缓存确有收益，非空谈）。"""
    from web.config import SECTIONS  # noqa: PLC0415

    pairs = [(key, rel) for key, items in SECTIONS.items() for rel, *_ in items]
    assert pairs, "SECTIONS 为空，断言前提不成立"
    assert len(pairs) > len(set(pairs)), (
        "SECTIONS 无重复读取，PF-6 前提不成立（数据变化后本断言应同步调整）")


def test_build_reads_each_source_once_and_memo_is_per_build(monkeypatch):
    """单次 build 中同一源文件只读一次；缓存不跨构建复用（防脏读）。"""
    import build as build_mod  # noqa: PLC0415

    reads = []
    real_read = build_mod.read

    def counting_read(path, **kwargs):
        reads.append(str(path))
        return real_read(path, **kwargs)

    monkeypatch.setattr(build_mod, "read", counting_read)

    build_mod.build(offline=True)
    first_round = list(reads)
    assert first_round, "没有任何读取，断言前提不成立"
    dupes = [p for p, n in Counter(first_round).items() if n > 1]
    assert not dupes, f"单次构建内重复读取同一文件: {dupes}"

    # 第二次构建必须重新读文件：缓存放构建局部，不得是全局 lru_cache。
    reads.clear()
    build_mod.build(offline=True)
    assert reads, "第二次构建没有重新读文件（缓存疑似泄漏到构建之外，存在脏读风险）"
