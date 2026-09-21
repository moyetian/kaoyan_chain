# -*- coding: utf-8 -*-
"""``ky serve --webhook-token=`` 命令行形参的回归测试。

背景：``/webhook`` 专用回调密钥（``KY_WEBHOOK_TOKEN``）已接入
「显式参数 > 环境变量 > ``ky_config.json`` 顶层 ``webhook_token``」，
但 ``ky serve`` 一直没有命令行形参 —— 只能靠向导/环境变量/配置文件。

本文件锁定：
    * 形参存在且**从解析一路传到** ``create_gateway_handler``（不得「解析出来却被
      静默丢弃」—— 本文件历史上 P1-8 是同型缺陷：``start_background_live_server``
      没有 token 形参，``--gateway-token=`` 传进去就没了）；
    * 优先级正确：形参 > 环境变量 > 配置文件；
    * 终端/日志**不回显密钥明文**，且给了形参就不再误报「未配置回调密钥」；
    * 帮助文案同步。

阴性对照：
    * 去掉 ``_cmd_serve`` 里的 ``elif a.startswith("--webhook-token=")`` 分支（或
      不把 ``webhook_token`` 传给 ``run_server``），
      ``test_cmd_serve_forwards_webhook_token`` /
      ``test_dispatch_does_not_swallow_webhook_token`` 变红；
    * 去掉 ``run_server`` 的 ``webhook_token`` 形参（或 ``create_gateway_handler``
      少传一个实参），优先级三条用例变红；
    * 把 ``if not effective_webhook_token`` 改回 ``resolve_webhook_secret(cfg=cfg)``，
      ``test_explicit_flag_suppresses_false_warning`` 变红。
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

from tools.cli import dispatch as dispatch_mod      # noqa: E402
from tools.cli import gateway as gateway_mod        # noqa: E402
from tools.cli.commands import misc as misc_mod     # noqa: E402
from tools.cli.commands import system as system_mod  # noqa: E402

#: 占位密钥（tests/ 禁含真实身份；值本身无意义）
EXPLICIT_SECRET = "PLACEHOLDER-WEBHOOK-EXPLICIT-0001"
ENV_SECRET = "PLACEHOLDER-WEBHOOK-ENV-0002"
CFG_SECRET = "PLACEHOLDER-WEBHOOK-CFG-0003"


class _FakeHTTPServer:
    """替身：不真正监听，``serve_forever`` 立刻返回（run_server 会捕获中断）。"""

    def __init__(self, addr, handler):
        self.server_address = addr
        self.handler = handler

    def serve_forever(self):
        raise KeyboardInterrupt


def _run_server_capture(monkeypatch, cfg, env=None, host="127.0.0.1", **kwargs):
    """跑 ``run_server``，捕获它最终交给 ``create_gateway_handler`` 的实参。"""
    monkeypatch.setattr(gateway_mod, "load_config", lambda: dict(cfg))
    if env is None:
        monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)
    else:
        monkeypatch.setenv("KY_WEBHOOK_TOKEN", env)

    captured = {}

    def _fake_create(token="", webhook_token=""):
        captured["token"] = token
        captured["webhook_token"] = webhook_token
        return object()

    monkeypatch.setattr(gateway_mod, "create_gateway_handler", _fake_create)
    monkeypatch.setattr(gateway_mod, "ThreadingHTTPServer", _FakeHTTPServer)
    gateway_mod.run_server(port=0, host=host, **kwargs)
    return captured


# ── ① 形参必须存在，并一路传到 handler ──────────────────────────────────────

def test_run_server_signature_has_webhook_token():
    """``run_server`` 必须有 ``webhook_token`` 形参（否则形参无处可传）。"""
    assert "webhook_token" in inspect.signature(gateway_mod.run_server).parameters


def test_cmd_serve_forwards_webhook_token(monkeypatch):
    """``_cmd_serve`` 解析出的密钥必须原样交给 ``run_server``（不得被吞掉）。"""
    captured = {}
    monkeypatch.setattr(misc_mod, "run_server", lambda **kw: captured.update(kw))

    misc_mod._cmd_serve(["serve", "9090", "--host=0.0.0.0",
                         "--gateway-token=PLACEHOLDER-GW-TOKEN",
                         f"--webhook-token={EXPLICIT_SECRET}"])

    assert captured["webhook_token"] == EXPLICIT_SECRET, "解析出来的密钥被静默丢弃"
    assert captured["gateway_token"] == "PLACEHOLDER-GW-TOKEN"
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9090


def test_cmd_serve_without_flag_passes_empty(monkeypatch):
    """未给形参时传空串 —— 让环境变量 / 配置文件来源继续生效。"""
    captured = {}
    monkeypatch.setattr(misc_mod, "run_server", lambda **kw: captured.update(kw))
    misc_mod._cmd_serve(["serve"])
    assert captured["webhook_token"] == ""


def test_dispatch_does_not_swallow_webhook_token(monkeypatch):
    """主入口的全局参数过滤不得把 ``--webhook-token=`` 摘掉。"""
    dispatch_mod._init_all_commands()
    captured = {}
    monkeypatch.setattr(misc_mod, "run_server", lambda **kw: captured.update(kw))

    rc = dispatch_mod.main(["serve", f"--webhook-token={EXPLICIT_SECRET}"])

    assert rc == 0
    assert captured.get("webhook_token") == EXPLICIT_SECRET, \
        "dispatch 主入口把 --webhook-token= 吞掉了（serve 收不到）"


# ── ② 优先级：形参 > 环境变量 > ky_config.json ─────────────────────────────

def test_explicit_flag_beats_env_and_config(monkeypatch):
    cap = _run_server_capture(monkeypatch, {"webhook_token": CFG_SECRET},
                              env=ENV_SECRET, gateway_token="",
                              webhook_token=EXPLICIT_SECRET)
    assert cap["webhook_token"] == EXPLICIT_SECRET


def test_env_beats_config_when_no_flag(monkeypatch):
    cap = _run_server_capture(monkeypatch, {"webhook_token": CFG_SECRET},
                              env=ENV_SECRET, gateway_token="", webhook_token="")
    assert cap["webhook_token"] == ENV_SECRET


def test_config_used_when_no_flag_and_no_env(monkeypatch):
    cap = _run_server_capture(monkeypatch, {"webhook_token": CFG_SECRET},
                              env=None, gateway_token="", webhook_token=None)
    assert cap["webhook_token"] == CFG_SECRET


def test_no_source_yields_empty(monkeypatch):
    """三个来源都没有时解析为空 —— 维持「仅回环回调」的既有语义。"""
    cap = _run_server_capture(monkeypatch, {}, env=None,
                              gateway_token="", webhook_token=None)
    assert cap["webhook_token"] == ""


# ── ③ 不回显明文 + 不误报 ──────────────────────────────────────────────────

def test_explicit_flag_suppresses_false_warning(monkeypatch, capsys):
    """给了形参就不该再提示「未配置群机器人回调密钥」，且不得回显密钥。"""
    _run_server_capture(monkeypatch, {}, env=None, host="0.0.0.0",
                        gateway_token="", webhook_token=EXPLICIT_SECRET)
    out = capsys.readouterr().out
    assert "未配置群机器人回调密钥" not in out, \
        "已通过命令行给出密钥却仍报「未配置」（解析结果没有参与判定）"
    assert EXPLICIT_SECRET not in out, "密钥明文被回显到终端"


def test_missing_secret_still_warns(monkeypatch, capsys):
    """反面对照：确实没配时，提示必须照常出现（避免用例空转）。"""
    _run_server_capture(monkeypatch, {}, env=None, host="0.0.0.0",
                        gateway_token="", webhook_token=None)
    out = capsys.readouterr().out
    assert "未配置群机器人回调密钥" in out


def test_help_documents_webhook_token(capsys):
    """帮助文案与命令注册表都要提到该形参。"""
    system_mod._cmd_help(["help"])
    assert "--webhook-token=" in capsys.readouterr().out

    dispatch_mod._init_all_commands()
    cmd = dispatch_mod.get_command("serve")
    assert cmd is not None and "--webhook-token=" in cmd.usage
