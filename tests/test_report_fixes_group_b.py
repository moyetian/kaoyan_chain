# -*- coding: utf-8 -*-
"""审查报告 B 组消缺回归测试（G1 / S1 / B-01）。

对应第三方审查报告条目：
  * G1   网关对外暴露时把 token **明文**拼进 URL 打印（终端录屏 / CI 日志留存）；
         现改为占位提示 + ``_mask_secret`` 打码确认。
  * S1   ``start_background_live_server`` 未透传 ``webhook_token``，``/webhook``
         只能靠环境变量/配置兜底，与 ``run_server`` 路径口径不一致。
  * B-01 ``README.md`` / ``操作手册.md`` 宣称的 ``/submit`` 斜杠指令在代码里不存在，
         现与中文口令「交作业」/「对答案」复用同一处理器 ``build_homework_menu``。

本文件全部为**只读 / 打桩**验证：HTTP server 用替身，绝不真正监听端口，
也不会改动工作区里的 ``ky_config.json``。测试数据一律中性占位。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.cli import gateway as gateway_mod        # noqa: E402
from tools.cli.repl import loop as repl_loop        # noqa: E402

#: 中性占位令牌（tests/ 禁含真实身份；值本身无意义）
PLACEHOLDER_TOKEN = "PLACEHOLDER-ACCESS-TOKEN-0001"
PLACEHOLDER_WEBHOOK = "PLACEHOLDER-WEBHOOK-SECRET-0002"
PLACEHOLDER_ENV = "PLACEHOLDER-WEBHOOK-ENV-0003"
PLACEHOLDER_CFG = "PLACEHOLDER-WEBHOOK-CFG-0004"

LAN_IP = "192.168.1.50"


class _SilentHTTPServer:
    """替身：不绑定任何端口，``serve_forever`` 立即返回（避免残留监听）。"""

    def __init__(self, addr, handler):
        self.server_address = addr
        self.handler = handler

    def serve_forever(self):
        return None

    def server_close(self):
        return None


class _InterruptHTTPServer(_SilentHTTPServer):
    """替身：``serve_forever`` 立刻抛 KeyboardInterrupt（``run_server`` 的正常退出路径）。"""

    def serve_forever(self):
        raise KeyboardInterrupt


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """清掉环境里的网关/回调密钥，避免本机环境让用例随机变红。"""
    monkeypatch.delenv("KY_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)


def _stub_gateway(monkeypatch, server_cls=_SilentHTTPServer, cfg=None, capture=None):
    """把网关的副作用（配置读取 / handler 构造 / 监听 / 网卡探测）全部打桩。"""
    monkeypatch.setattr(gateway_mod, "load_config", lambda: dict(cfg or {}))

    if capture is None:
        monkeypatch.setattr(gateway_mod, "create_gateway_handler", lambda **kw: object())
    else:
        def _fake_create(token="", webhook_token=""):
            capture["token"] = token
            capture["webhook_token"] = webhook_token
            return object()

        monkeypatch.setattr(gateway_mod, "create_gateway_handler", _fake_create)

    monkeypatch.setattr(gateway_mod, "ThreadingHTTPServer", server_cls)
    monkeypatch.setattr(gateway_mod, "_detect_lan_ip", lambda: LAN_IP)


# ── G1：token 不得明文进 stdout ────────────────────────────────────────────

def test_g1_run_server_never_prints_token_plaintext(monkeypatch, capsys):
    """``run_server`` 对外暴露分支：输出不得含 token 明文，但须保留 URL 形状。"""
    _stub_gateway(monkeypatch, server_cls=_InterruptHTTPServer)

    gateway_mod.run_server(port=0, host="0.0.0.0", gateway_token=PLACEHOLDER_TOKEN)

    out = capsys.readouterr().out
    assert PLACEHOLDER_TOKEN not in out, "网关 token 明文被打印到 stdout"
    # 可用性保留：用户仍要知道「手机该怎么访问」
    assert "手机访问" in out
    assert "?token=" in out, "URL 形状提示丢失"
    assert f"http://{LAN_IP}" in out, "局域网 IP 提示丢失"
    # 打码确认：让用户能核对令牌是否配对，但不泄漏完整值
    masked = gateway_mod._mask_secret(PLACEHOLDER_TOKEN)
    assert "***" in masked
    assert masked in out, "缺少打码后的令牌确认信息"


def test_g1_background_server_never_prints_token_plaintext(monkeypatch, capsys):
    """``start_background_live_server`` 对外暴露分支：同口径，不得明文回显。"""
    _stub_gateway(monkeypatch)

    port = gateway_mod.start_background_live_server(
        18088, host="0.0.0.0", token=PLACEHOLDER_TOKEN)
    assert port == 18088

    out = capsys.readouterr().out
    assert PLACEHOLDER_TOKEN not in out, "后台网关路径仍明文打印 token"
    assert "?token=" in out and "手机访问" in out
    assert gateway_mod._mask_secret(PLACEHOLDER_TOKEN) in out


def test_g1_negative_control_old_format_would_leak(monkeypatch, capsys):
    """阴性对照：修复前的 ``?token={effective_token}`` 拼接确实会泄漏明文。

    这里直接复现旧格式的字符串，证明上面的断言不是「恒真」的空转用例。
    """
    old_line = f"    手机访问：http://{LAN_IP}:8088/?token={PLACEHOLDER_TOKEN}\n"
    assert PLACEHOLDER_TOKEN in old_line          # 旧格式：明文可见

    _stub_gateway(monkeypatch, server_cls=_InterruptHTTPServer)
    gateway_mod.run_server(port=0, host="0.0.0.0", gateway_token=PLACEHOLDER_TOKEN)
    out = capsys.readouterr().out
    assert PLACEHOLDER_TOKEN not in out           # 新格式：不可见


# ── S1：后台网关必须透传 webhook_token ─────────────────────────────────────

def test_s1_signature_has_webhook_token():
    sig = inspect.signature(gateway_mod.start_background_live_server)
    assert "webhook_token" in sig.parameters, \
        "start_background_live_server 缺少 webhook_token 形参"


def test_s1_explicit_webhook_token_is_forwarded(monkeypatch):
    """显式传入的回调密钥必须一路传到 ``create_gateway_handler``。"""
    captured = {}
    _stub_gateway(monkeypatch, capture=captured)

    port = gateway_mod.start_background_live_server(
        18089, host="127.0.0.1", token="PLACEHOLDER-GW-TOKEN",
        webhook_token=PLACEHOLDER_WEBHOOK)

    assert port == 18089
    assert captured["token"] == "PLACEHOLDER-GW-TOKEN"
    assert captured["webhook_token"] == PLACEHOLDER_WEBHOOK, \
        "后台网关路径把 webhook_token 静默丢弃了"


def test_s1_env_used_when_no_explicit(monkeypatch):
    """未显式传入时回落到环境变量 ``KY_WEBHOOK_TOKEN``。"""
    captured = {}
    monkeypatch.setenv("KY_WEBHOOK_TOKEN", PLACEHOLDER_ENV)
    _stub_gateway(monkeypatch, capture=captured)

    gateway_mod.start_background_live_server(18090, host="127.0.0.1", token="T")

    assert captured["webhook_token"] == PLACEHOLDER_ENV


def test_s1_config_used_when_no_explicit_no_env(monkeypatch):
    """既无形参也无环境变量时回落到 ``ky_config.json`` 顶层 ``webhook_token``。"""
    captured = {}
    _stub_gateway(monkeypatch, cfg={"webhook_token": PLACEHOLDER_CFG}, capture=captured)

    gateway_mod.start_background_live_server(18091, host="127.0.0.1", token="T")

    assert captured["webhook_token"] == PLACEHOLDER_CFG


def test_s1_no_source_yields_empty(monkeypatch):
    """三个来源都没有时解析为空 —— 维持「仅回环回调」的既有语义。"""
    captured = {}
    _stub_gateway(monkeypatch, cfg={}, capture=captured)

    gateway_mod.start_background_live_server(18092, host="127.0.0.1", token="T")

    assert captured["webhook_token"] == ""


def test_s1_run_repl_signature_forwards_webhook_token(monkeypatch):
    """``run_repl`` 也须透传回调密钥（否则 /view 路径无处可传）。"""
    assert "webhook_token" in inspect.signature(repl_loop.run_repl).parameters

    captured = {}

    def _fake_start(*args, **kwargs):
        captured.update(kwargs)
        return 8088

    monkeypatch.setattr(repl_loop, "start_background_live_server", _fake_start)
    monkeypatch.setattr(repl_loop, "load_config", lambda: {"onboarding_completed": True})
    monkeypatch.setattr(repl_loop, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "AgentRunner", None)

    def _eof(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)

    repl_loop.run_repl(permission_mode="ask", gateway_token="GW",
                       webhook_token=PLACEHOLDER_WEBHOOK)

    assert captured.get("webhook_token") == PLACEHOLDER_WEBHOOK
    assert captured.get("token") == "GW"


def test_s1_dispatch_parses_webhook_token_for_bare_repl(monkeypatch):
    """裸 ``ky --webhook-token=...``（无子命令 → REPL）必须全局解析并透传。

    [S1 修复·口径一致] 主入口此前只全局解析 ``--gateway-token=``；``--webhook-token=``
    会落进 ``filtered_args``，使 ``args[0]`` 变成该选项串并命中「未知参数」分支
    （实测 ``ky --webhook-token=x`` 报未知参数），与 ``ky serve`` / ``ky view`` 口径不一。
    """
    from tools.cli import dispatch as dispatch_mod

    captured = {}

    def _fake_run_repl(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(repl_loop, "run_repl", _fake_run_repl)

    rc = dispatch_mod.main([f"--webhook-token={PLACEHOLDER_WEBHOOK}",
                            f"--gateway-token={PLACEHOLDER_TOKEN}"])

    assert rc == 0, "裸 REPL 路径应正常返回 0（而非落入未知参数分支）"
    assert captured.get("webhook_token") == PLACEHOLDER_WEBHOOK, \
        "裸 ky --webhook-token=... 未透传到 run_repl"
    assert captured.get("gateway_token") == PLACEHOLDER_TOKEN


# ── B-01：``/submit`` 与中文口令走同一处理器 ────────────────────────────────

def _drive_repl_once(monkeypatch, inputs):
    """在打桩环境下驱动一次 run_repl，返回 ``build_homework_menu`` 的调用次数。"""
    cfg = {
        "api_key": "",
        "model": "deepseek-chat",
        "active_subject": "math",
        "onboarding_completed": True,
        "study_plan": {},
    }
    monkeypatch.setattr(repl_loop, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(repl_loop, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "start_background_live_server", lambda *a, **k: 8088)
    monkeypatch.setattr(repl_loop, "AgentRunner", None)

    calls = []
    monkeypatch.setattr(
        repl_loop, "build_homework_menu",
        lambda *a, **k: (calls.append(1), "SENTINEL_HOMEWORK_MENU")[1])

    it = iter(list(inputs))

    def fake_input(*a, **k):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)

    repl_loop.run_repl(permission_mode="ask")
    return calls


def test_b01_submit_slash_uses_homework_menu(monkeypatch, capsys):
    """``/submit`` 必须调用 ``build_homework_menu`` 并输出其内容。"""
    calls = _drive_repl_once(monkeypatch, ["/submit"])
    out = capsys.readouterr().out

    assert calls, "/submit 未调用 build_homework_menu（斜杠指令未注册）"
    assert "SENTINEL_HOMEWORK_MENU" in out, "/submit 的输出未落地"


def test_b01_chinese_alias_same_handler(monkeypatch, capsys):
    """中文口令「交作业」走同一处理器（两者行为一致）。"""
    calls = _drive_repl_once(monkeypatch, ["交作业"])
    out = capsys.readouterr().out

    assert calls, "中文口令「交作业」未调用 build_homework_menu"
    assert "SENTINEL_HOMEWORK_MENU" in out


def test_b01_negative_control_unknown_slash_not_routed(monkeypatch, capsys):
    """阴性对照：未注册的斜杠指令不得触发该处理器（避免断言空转）。"""
    calls = _drive_repl_once(monkeypatch, ["/submit_typo_placeholder"])
    assert calls == [], "未注册指令却触发了 build_homework_menu"
