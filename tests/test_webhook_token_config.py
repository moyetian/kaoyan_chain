# -*- coding: utf-8 -*-
"""`/webhook` 专用回调密钥接入 `ky_config.json` 与配置向导的回归测试。

背景：`/webhook`（钉钉/飞书/QQ OneBot 回调入口）此前被网关 token 一视同仁拦住，
修复后引入独立密钥，但该密钥只能靠环境变量 `KY_WEBHOOK_TOKEN`，重启终端即失效。
本次把它的来源补齐为「显式参数 > 环境变量 > ky_config.json 的 webhook_token」，
并在配置向导与配置清单里以掩码方式呈现。

隔离约定：
  * 真实 `ky_config.json` 由 `tests/conftest.py` 重定向 + 绊线保护；
  * 本文件再显式把写盘目标指到 `tmp_path`，并默认屏蔽环境变量与配置文件来源，
    避免本机环境让用例随机变红。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from tools.cli import config as cfgmod
from tools.cli import gateway as gateway_mod
from tools.cli import shared as shared_mod
from tools.cli.gateway import create_gateway_handler, resolve_webhook_secret

LAN_CLIENT = "192.168.1.77"
LOOPBACK = "127.0.0.1"
WEBHOOK_PATH = "/webhook"

#: 占位密钥（绝不用真实凭证；长度刻意 > 12 以便验证部分打码分支）
CFG_SECRET = "CFG-WEBHOOK-SECRET-0001"
ENV_SECRET = "ENV-WEBHOOK-SECRET-0002"


@pytest.fixture(autouse=True)
def _hermetic_secret_sources(monkeypatch):
    """默认：无环境变量、配置文件为空 —— 每个用例只显式打开它要验证的那一个来源。"""
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)
    monkeypatch.setattr(gateway_mod, "load_config", lambda: {}, raising=False)


class _FakeReq:
    """最小化 handler 实例替身，用于直调 `_is_authorized`。"""

    def __init__(self, path, client, headers=None):
        self.path = path
        self.client_address = (client, 55555)
        self.headers = headers or {}


def _authorized(token, path, client, headers=None, webhook_token=""):
    handler_cls = create_gateway_handler(token=token, webhook_token=webhook_token)
    req = _FakeReq(path, client, headers)
    for name in ("_token_ok", "_is_loopback", "_webhook_authorized"):
        setattr(req, name, getattr(handler_cls, name).__get__(req, handler_cls))
    return handler_cls._is_authorized(req)


def _serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _post(url, headers=None, body=b""):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ── ① 配置文件里的 webhook_token 必须生效 ─────────────────────────────────

def test_config_webhook_token_is_enforced_on_webhook(monkeypatch):
    """ky_config.json 设了 webhook_token 后，/webhook 按「配了就得带」执行。"""
    monkeypatch.setattr(gateway_mod, "load_config", lambda: {"webhook_token": CFG_SECRET})
    assert _authorized("SECRET", f"{WEBHOOK_PATH}?token={CFG_SECRET}", LAN_CLIENT) is True
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT, {"X-KY-Token": CFG_SECRET}) is True
    # 配了就必须带：回环也不例外，网关 token 也不算数
    assert _authorized("SECRET", WEBHOOK_PATH, LOOPBACK) is False
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT) is False
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT, {"X-KY-Token": "SECRET"}) is False


def test_config_webhook_token_does_not_leak_to_data_endpoints(monkeypatch):
    """配置来源的密钥同样只对 /webhook 生效，不得放行数据端点。"""
    monkeypatch.setattr(gateway_mod, "load_config", lambda: {"webhook_token": CFG_SECRET})
    assert _authorized("SECRET", "/api/live", LOOPBACK, {"X-KY-Token": CFG_SECRET}) is False
    assert _authorized("SECRET", "/v1/models", LAN_CLIENT, {"X-KY-Token": CFG_SECRET}) is False


def test_http_webhook_accepts_config_secret(monkeypatch):
    """HTTP 级端到端：空 body 的 /webhook（不触发 LLM）按配置密钥鉴权。"""
    monkeypatch.setattr(gateway_mod, "load_config", lambda: {"webhook_token": CFG_SECRET})
    server = _serve(create_gateway_handler(token="SECRET"))
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _post(f"{base}{WEBHOOK_PATH}")[0] == 401
        assert _post(f"{base}{WEBHOOK_PATH}?token={CFG_SECRET}")[0] == 200
        assert _post(f"{base}{WEBHOOK_PATH}", {"X-KY-Token": CFG_SECRET})[0] == 200
    finally:
        server.shutdown()
        server.server_close()


# ── ② 取值优先级：显式参数 > 环境变量 > ky_config.json ────────────────────

def test_env_var_overrides_config(monkeypatch):
    monkeypatch.setenv("KY_WEBHOOK_TOKEN", ENV_SECRET)
    monkeypatch.setattr(gateway_mod, "load_config", lambda: {"webhook_token": CFG_SECRET})
    assert _authorized("SECRET", f"{WEBHOOK_PATH}?token={ENV_SECRET}", LAN_CLIENT) is True
    assert _authorized("SECRET", f"{WEBHOOK_PATH}?token={CFG_SECRET}", LAN_CLIENT) is False


def test_explicit_param_overrides_env_and_config(monkeypatch):
    monkeypatch.setenv("KY_WEBHOOK_TOKEN", ENV_SECRET)
    monkeypatch.setattr(gateway_mod, "load_config", lambda: {"webhook_token": CFG_SECRET})
    explicit = "EXPLICIT-WEBHOOK-SECRET"
    assert _authorized("SECRET", f"{WEBHOOK_PATH}?token={explicit}", LAN_CLIENT,
                       webhook_token=explicit) is True
    assert _authorized("SECRET", f"{WEBHOOK_PATH}?token={ENV_SECRET}", LAN_CLIENT,
                       webhook_token=explicit) is False


def test_resolve_webhook_secret_priority_and_whitespace(monkeypatch):
    cfg = {"webhook_token": f"  {CFG_SECRET}  "}
    assert resolve_webhook_secret("", cfg) == CFG_SECRET          # 配置来源去空白
    assert resolve_webhook_secret("  ", cfg) == CFG_SECRET        # 空白显式参数视为未给
    monkeypatch.setenv("KY_WEBHOOK_TOKEN", f" {ENV_SECRET} ")
    assert resolve_webhook_secret("", cfg) == ENV_SECRET          # 环境变量优先于配置
    assert resolve_webhook_secret("EXPLICIT-X", cfg) == "EXPLICIT-X"  # 显式参数最高
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)
    assert resolve_webhook_secret("", {}) == ""
    assert resolve_webhook_secret("", None) == ""                 # 无配置时不炸


# ── ③ 掩码：绝不回显明文 ─────────────────────────────────────────────────

@pytest.mark.parametrize("secret", [CFG_SECRET, "whk_0123456789abcdef", "x" * 40])
def test_mask_secret_never_leaks_webhook_token(secret):
    masked = cfgmod._mask_secret(secret)
    assert secret not in masked
    assert masked != secret


@pytest.mark.parametrize("secret", ["", "short", "elevenchars", "twelve-chars"])
def test_mask_secret_short_webhook_token_is_never_echoed(secret):
    """短密钥（含 11~12 字符边界）一律只报「已设置」，不得部分回显。"""
    masked = cfgmod._mask_secret(secret)
    if not secret:
        assert masked == "未设置"
    else:
        assert masked == "已设置"
        assert secret not in masked


def test_show_config_masks_webhook_token(capsys):
    cfgmod.show_config({"api_key": "", "webhooks": {}, "webhook_token": CFG_SECRET})
    out = capsys.readouterr().out
    assert CFG_SECRET not in out
    assert "回调密钥" in out


# ── ④ 未配置任何密钥：既有回环语义不变 ───────────────────────────────────

def test_no_secret_configured_keeps_loopback_only_semantics():
    assert _authorized("", WEBHOOK_PATH, LOOPBACK) is True
    assert _authorized("", WEBHOOK_PATH, LAN_CLIENT) is False
    assert _authorized("SECRET", WEBHOOK_PATH, LOOPBACK) is True      # 网关 token 下回环仍放行
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT) is False


# ── ⑤ 向导：写入 ky_config.json（目标路径已被重定向到 tmp_path） ─────────

def test_wizard_persists_webhook_token(tmp_path, monkeypatch):
    target = tmp_path / "ky_config.json"
    monkeypatch.setattr(shared_mod, "CONFIG_FILE", target, raising=False)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", target, raising=False)
    answers = iter(["8", CFG_SECRET, "0"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))

    cfgmod.configure_webhooks({})

    assert target.parent == tmp_path
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["webhook_token"] == CFG_SECRET


def test_wizard_blank_keeps_existing_and_dash_clears(tmp_path, monkeypatch):
    target = tmp_path / "ky_config.json"
    monkeypatch.setattr(shared_mod, "CONFIG_FILE", target, raising=False)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", target, raising=False)

    # 留空 = 跳过，保持现有
    answers = iter(["8", "", "0"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    cfgmod.configure_webhooks({"webhook_token": CFG_SECRET})
    assert json.loads(target.read_text(encoding="utf-8"))["webhook_token"] == CFG_SECRET

    # 输入 - = 清空
    answers = iter(["8", "-", "0"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    cfgmod.configure_webhooks({"webhook_token": CFG_SECRET})
    assert json.loads(target.read_text(encoding="utf-8"))["webhook_token"] == ""


# ── ⑥ GUI 设置保存不得顺带抹掉该字段（历史事故：配置被整份覆盖） ──────────

def test_gui_save_settings_preserves_webhook_token(tmp_path):
    from tools.gui.services.settings import save_settings

    path = tmp_path / "ky_config.json"
    path.write_text(json.dumps({"webhook_token": CFG_SECRET, "study_plan": {"school": "占位院校"}},
                               ensure_ascii=False), encoding="utf-8")
    save_settings(path, api_key="", base_url="https://example.invalid/v1",
                  model="m", school="占位院校", major="占位专业",
                  exam_date="2026-12-19", style="温和启发·减负鼓励型 (Encouraging Mentor)")
    assert json.loads(path.read_text(encoding="utf-8"))["webhook_token"] == CFG_SECRET
