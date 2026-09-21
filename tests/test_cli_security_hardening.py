# -*- coding: utf-8 -*-
"""CLI 层安全加固回归测试。

覆盖本轮审查修复：
  * P0-3  网关静态页不再无条件放行（鉴权矩阵 (a)(b)(c)(d)）
  * P1-8  start_background_live_server 显式 token 生效
  * P1-5  凭证/Webhook 不再明文回显（_mask_secret）
  * P1-4  .gitignore 覆盖 ky_config.corrupted.bak；_align_to_umask(sensitive=True) 收紧为 0600
  * 低危  _wants_command_help 不吞正文里的字面 help；未知子命令退出码非 0；build_parser 已删除
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tools import ky_io
from tools.cli import config as cfgmod
from tools.cli import dispatch as dispatch_mod
from tools.cli.commands import system as system_mod
from tools.cli.gateway import (
    _detect_lan_ip,
    _token_matches,
    create_gateway_handler,
    start_background_live_server,
)

LAN_CLIENT = "192.168.1.77"
LOOPBACK = "127.0.0.1"
STATIC_PATHS = ("/", "/index.html", "/live")
DATA_PATHS = ("/api/live", "/v1/models")
LIVE = Path(__file__).resolve().parent.parent / "docs" / "live.html"
WEBHOOK_PATH = "/webhook"


class _FakeReq:
    """最小化的 handler 实例替身，用于直调 _is_authorized。"""

    def __init__(self, path, client, headers=None):
        self.path = path
        self.client_address = (client, 55555)
        self.headers = headers or {}


def _authorized(token, path, client, headers=None):
    handler_cls = create_gateway_handler(token=token)
    req = _FakeReq(path, client, headers)
    # 把真实 handler 的实例方法绑到替身上，等价于一个完整 handler 实例
    for name in ("_token_ok", "_is_loopback", "_webhook_authorized"):
        setattr(req, name, getattr(handler_cls, name).__get__(req, handler_cls))
    return handler_cls._is_authorized(req)


# ── P0-3 鉴权矩阵 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", STATIC_PATHS)
def test_a_static_page_with_token_from_anywhere(path):
    """(a) 带正确 token 的请求，回环与局域网都能访问静态页。"""
    for client in (LOOPBACK, LAN_CLIENT):
        assert _authorized("SECRET", path, client,
                           {"X-KY-Token": "SECRET"}) is True
        assert _authorized("SECRET", path, client,
                           {"Authorization": "Bearer SECRET"}) is True


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_a_static_page_via_query_token(path):
    """(a) ?token= 查询参数同样可用（手机浏览器直接打开）。"""
    assert _authorized("SECRET", f"{path}?token=SECRET", LAN_CLIENT) is True


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_b_static_page_no_token_from_lan_denied(path):
    """(b) 未配置 token 且非回环：静态页必须被拒。"""
    assert _authorized("", path, LAN_CLIENT) is False


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_b2_static_page_token_configured_but_missing_denied(path):
    """(b) 已配置 token 但请求不带 token：静态页也必须被拒（局域网/回环一致）。"""
    for client in (LOOPBACK, LAN_CLIENT):
        assert _authorized("SECRET", path, client) is False


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_c_static_page_no_token_loopback_allowed(path):
    """(c) 未配置 token 且回环：静态页仍可访问（本地开发便利）。"""
    assert _authorized("", path, LOOPBACK) is True


def test_d_data_endpoints_behavior_unchanged():
    """(d) /api/live、/v1/models 现有行为不变。"""
    for path in DATA_PATHS:
        # 无 token：仅回环放行
        assert _authorized("", path, LOOPBACK) is True
        assert _authorized("", path, LAN_CLIENT) is False
        # 有 token：必须带 token，回环也不豁免
        assert _authorized("SECRET", path, LOOPBACK) is False
        assert _authorized("SECRET", path, LAN_CLIENT) is False
        assert _authorized("SECRET", path, LOOPBACK, {"X-KY-Token": "SECRET"}) is True
        assert _authorized("SECRET", path, LAN_CLIENT, {"X-KY-Token": "SECRET"}) is True


def test_d_wrong_token_denied():
    """错误 token 一律拒绝（恒定时间比较函数本身也要正确）。"""
    assert _token_matches("WRONG", "SECRET") is False
    assert _token_matches("", "SECRET") is False
    assert _token_matches("SECRET", "SECRET") is True
    assert _authorized("SECRET", "/api/live", LOOPBACK, {"X-KY-Token": "WRONG"}) is False


def test_negative_control_old_whitelist_would_leak():
    """阴性对照：修复前的「静态页无条件放行」逻辑在本用例下会失败。

    旧实现等价于：path in ("/live","/","/index.html") -> True（无视 token/来源）。
    若有人回退该修复，下面的 `old_behavior` 断言仍为 True，而新实现断言会失败。
    """
    def old_behavior(path, client):
        return path in STATIC_PATHS

    assert old_behavior("/index.html", LAN_CLIENT) is True          # 旧逻辑：裸奔
    assert _authorized("", "/index.html", LAN_CLIENT) is False       # 新逻辑：拒绝


# ── P0-3 HTTP 级验证 ──────────────────────────────────────────────────────

def _serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_static_page_requires_token_when_configured():
    server = _serve(create_gateway_handler(token="SECRET"))
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _get(f"{base}/index.html")[0] == 401
        assert _get(f"{base}/index.html?token=SECRET")[0] == 200
        assert _get(f"{base}/api/live")[0] == 401
        assert _get(f"{base}/api/live", {"X-KY-Token": "SECRET"})[0] == 200
    finally:
        server.shutdown()
        server.server_close()


# ── P1-8 后台网关 token 生效 ──────────────────────────────────────────────

def test_start_background_live_server_accepts_and_enforces_token():
    port = start_background_live_server(18400, host="127.0.0.1", token="BGTOKEN")
    assert port is not None
    base = f"http://127.0.0.1:{port}"
    assert _get(f"{base}/api/live")[0] == 401          # 无 token 被拒
    assert _get(f"{base}/api/live", {"X-KY-Token": "BGTOKEN"})[0] == 200


def test_start_background_live_server_signature_has_token():
    import inspect
    sig = inspect.signature(start_background_live_server)
    assert "token" in sig.parameters


# ── P1-5 打码 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret", [
    "sk-abcdefghijklmnop",
    "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=AAAA-SUPER-SECRET-KEY",
    "SEC0123456789abcdef",
])
def test_mask_secret_hides_middle(secret):
    masked = cfgmod._mask_secret(secret)
    assert masked.startswith(secret[:6])
    assert masked.endswith(secret[-4:])
    assert "***" in masked
    # 中段绝不出现在打码结果里
    assert secret[6:-4] not in masked
    assert secret not in masked


def test_mask_secret_short_and_empty():
    assert cfgmod._mask_secret("") == "未设置"
    assert cfgmod._mask_secret("sk-SHORT") == "已设置"       # 长度不足 -> 不回显
    assert cfgmod._mask_secret(None) == "未设置"


def test_show_config_never_echoes_credentials():
    cfg = {
        "api_key": "sk-SHORT",           # 长度 <=12，此前会原样打印
        "webhooks": {
            "wechat": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SUPER-SECRET-KEY",
            "dingtalk": "https://oapi.dingtalk.com/robot/send?access_token=DING-SECRET",
            "dingtalk_secret": "SEC0123456789abcdef",
            "feishu": "https://open.feishu.cn/open-apis/bot/v2/hook/FEISHU-SECRET",
            "qq_onebot": "http://127.0.0.1:3000",
        },
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        cfgmod.show_config(cfg)
    out = buf.getvalue()
    for leaked in ("SUPER-SECRET-KEY", "DING-SECRET", "FEISHU-SECRET", "sk-SHORT"):
        assert leaked not in out, f"明文凭证泄露: {leaked}"
    assert "已设置(长度8)" in out


# ── P1-4 备份文件忽略与权限 ───────────────────────────────────────────────

def test_gitignore_covers_corrupted_backup():
    text = Path(".gitignore").read_text(encoding="utf-8")
    assert "ky_config.corrupted.bak" in text
    assert "*.corrupted.bak" in text


def test_align_to_umask_sensitive_is_0600(monkeypatch):
    """POSIX 下 sensitive=True 收紧为 0600（Windows 由 ACL 管理，跳过）。"""
    recorded = {}
    monkeypatch.setattr(ky_io.os, "name", "posix")
    monkeypatch.setattr(ky_io.os, "chmod", lambda p, m: recorded.__setitem__("mode", m))

    ky_io._align_to_umask(Path("x"), sensitive=True)
    assert recorded["mode"] == 0o600

    recorded.clear()
    monkeypatch.setattr(ky_io, "_current_umask", lambda: 0o022)
    ky_io._align_to_umask(Path("x"))                  # 非敏感保持 umask 默认
    assert recorded["mode"] == 0o644


def test_atomic_write_text_accepts_sensitive_kwarg(tmp_path):
    p = tmp_path / "cfg.json"
    ky_io.atomic_write_text(p, "{}", sensitive=True)
    assert p.read_text(encoding="utf-8") == "{}"


def test_unchanged_content_still_tightens_sensitive_perms(tmp_path, monkeypatch):
    """内容未变而走「跳过写入」捷径时，也必须补齐 0600 权限收紧。

    缺陷现场：``atomic_write_text`` 在「目标已存在且内容与待写文本完全一致」时
    直接 ``return target``，而这一步发生在 ``_align_to_umask(...)`` **之前**。
    于是一个旧版本以 umask 0644 创建的 ``ky_config.json``，若本次写入内容与磁盘
    完全一致（恰是「升级加固」的典型情形），0600 收紧被永久跳过，权限停在 0644。

    这里记录 ``_align_to_umask`` 的调用（0600 的映射本身由
    ``test_align_to_umask_sensitive_is_0600`` 覆盖），避开在 Windows 上伪造
    ``os.name='posix'`` 会连带破坏 ``pathlib`` 的坑。
    """
    p = tmp_path / "cfg.json"
    p.write_text("{}", encoding="utf-8")          # 与下面要写的完全一致

    calls = []
    monkeypatch.setattr(
        ky_io, "_align_to_umask",
        lambda path, mode=0o666, sensitive=False: calls.append(
            (Path(path), sensitive)))

    ky_io.atomic_write_text(p, "{}", sensitive=True)

    assert calls, "内容未变时跳过了权限对齐（0600 收紧失效）"
    assert calls[-1][1] is True, "sensitive=True 未透传给权限对齐"
    assert p.read_text(encoding="utf-8") == "{}"


# ── 低危 dispatch ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("args,expected", [
    (["exam"], False),
    (["exam", "--help"], True),
    (["exam", "-h"], True),
    (["exam", "--count=3", "--help"], True),
    (["exam", "--help", "--count=3"], True),
    (["exam", "help"], False),          # 字面 help 是参数，不是帮助请求
    (["exam", "help", "me"], False),
    (["exam", "foo", "help"], False),
])
def test_wants_command_help_positions(args, expected):
    assert dispatch_mod._wants_command_help(args) is expected


def test_commands_unknown_subcommand_returns_nonzero():
    assert system_mod._cmd_commands(["commands", "zzz_notacommand"]) == 1


def test_commands_known_subcommand_returns_zero():
    assert system_mod._cmd_commands(["commands"]) == 0


def test_build_parser_removed():
    assert not hasattr(dispatch_mod, "build_parser")


def test_detect_lan_ip_returns_string():
    assert isinstance(_detect_lan_ip(), str)


# ══════════════════════════════════════════════════════════════════════════
# 本轮审查修复：① 手机端实时页 401  ② 非 ASCII token  ③ /webhook 豁免
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _no_ambient_webhook_secret(monkeypatch):
    """默认清掉环境里的 KY_WEBHOOK_TOKEN，避免本机环境让用例随机变红。"""
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)


# ── ① docs/live.html 必须携带 token 凭据 ─────────────────────────────────

_TOKEN_BEGIN = "// [KY-TOKEN-LAYER-BEGIN]"
_TOKEN_END = "// [KY-TOKEN-LAYER-END]"


def _token_layer_source() -> str:
    text = LIVE.read_text(encoding="utf-8")
    assert _TOKEN_BEGIN in text and _TOKEN_END in text, "live.html 缺少 token 层标记"
    start = text.index(_TOKEN_BEGIN)
    end = text.index(_TOKEN_END, start)
    layer = text[start:end]
    assert "function authHeaders" in layer and "KY_TOKEN" in layer, layer[:200]
    return layer


def _run_token_layer(search: str) -> dict:
    """在 node 里执行 live.html 的 token 层（抽取真实代码，非复制）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 Node.js，跳过 live.html token 层单测")
    script = (
        "global.window = { location: { search: %s } };\n" % json.dumps(search)
    ) + _token_layer_source() + (
        "process.stdout.write(JSON.stringify({"
        " token: KY_TOKEN,"
        " plain: authHeaders(),"
        " withExtra: authHeaders({'Content-Type': 'application/json'})"
        "}));"
    )
    res = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, f"node 执行失败: {res.stderr}"
    return json.loads(res.stdout)


def test_live_token_read_from_url_and_sent_as_header():
    """手机端用 ?token=xxx 打开页面时，token 必须进 X-KY-Token 请求头。"""
    d = _run_token_layer("?token=SECRET")
    assert d["token"] == "SECRET"
    assert d["plain"] == {"X-KY-Token": "SECRET"}
    assert d["withExtra"] == {"Content-Type": "application/json", "X-KY-Token": "SECRET"}


@pytest.mark.parametrize("search", ["", "?foo=1", "?token="])
def test_live_without_token_sends_no_credentials(search):
    """未配 token 时行为与修复前完全一致：不带任何凭据。"""
    d = _run_token_layer(search)
    assert d["token"] == ""
    assert d["plain"] == {}
    assert d["withExtra"] == {"Content-Type": "application/json"}


def test_live_three_fetches_all_carry_auth_headers():
    """/api/live、/api/ask、/api/clear 三个调用点都必须带上凭据。"""
    text = LIVE.read_text(encoding="utf-8")
    for call in ("fetch('/api/live'", "fetch('/api/ask'", "fetch('/api/clear'"):
        idx = text.find(call)
        assert idx != -1, f"未找到调用点 {call}"
        assert "authHeaders" in text[idx: idx + 220], f"{call} 未携带 token 凭据"


def test_live_token_never_written_to_dom():
    """token 只允许进请求头，不得落到 DOM / 存储（防泄漏）。"""
    text = LIVE.read_text(encoding="utf-8")
    assert "textContent = KY_TOKEN" not in text
    assert "innerHTML = KY_TOKEN" not in text
    for line in text.splitlines():
        if "KY_TOKEN" in line and "localStorage" in line:
            pytest.fail(f"token 被写入 localStorage: {line.strip()}")


def test_http_live_page_with_token_can_reach_api():
    """端到端：配 token 后，带 ?token= 的页面请求能拿到 /api/live（不再必然 401）。"""
    server = _serve(create_gateway_handler(token="SECRET"))
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _get(f"{base}/api/live")[0] == 401
        assert _get(f"{base}/api/live", {"X-KY-Token": "SECRET"})[0] == 200
        assert _get(f"{base}/api/live?token=SECRET")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


# ── ② 非 ASCII token 不得让请求处理崩溃 ──────────────────────────────────

def test_token_matches_supports_non_ascii():
    """中文 token 必须能正确比较（此前 hmac.compare_digest 抛 TypeError）。"""
    assert _token_matches("我的密钥", "我的密钥") is True
    assert _token_matches("我的密钥", "你的密钥") is False
    assert _token_matches("我的密钥", "SECRET") is False
    assert _token_matches("SECRET", "SECRET") is True
    # 空值早退逻辑保留
    assert _token_matches("", "我的密钥") is False
    assert _token_matches("我的密钥", "") is False


def test_non_ascii_token_request_returns_401_not_crash():
    """配了中文 token 时，带非空 token 的请求必须正常返回 401/200 而非掐断连接。

    注意 HTTP 头值只能是 latin-1，故「正确的中文 token」只能走 ``?token=``
    （浏览器/客户端会做百分号编码）；ASCII 的错误 token 走请求头，正是修复前
    触发 ``TypeError`` 崩连接的那条路径。
    """
    server = _serve(create_gateway_handler(token="我的密钥"))
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _get(f"{base}/api/live", {"X-KY-Token": "wrong"})[0] == 401
        assert _get(f"{base}/api/live", {"Authorization": "Bearer nope"})[0] == 401
        quoted = urllib.parse.quote("我的密钥", safe="")
        assert _get(f"{base}/api/live?token={quoted}")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


# ── ③ /webhook 豁免（第三方回调无法携带网关 token） ──────────────────────

def test_webhook_loopback_allowed_when_gateway_token_set(monkeypatch):
    """配了网关 token 时，回环来源的 /webhook 仍放行（本地 NapCat 直连）。"""
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)
    assert _authorized("SECRET", WEBHOOK_PATH, LOOPBACK) is True


def test_webhook_lan_denied_without_credentials(monkeypatch):
    """配了网关 token 但未配专用密钥：局域网裸奔必须被挡（不得退化）。"""
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT) is False
    # 携带网关 token 的远程回调放行（自建穿透统一凭据场景）
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT,
                       {"X-KY-Token": "SECRET"}) is True


def test_webhook_dedicated_secret_is_enforced(monkeypatch):
    """配了 KY_WEBHOOK_TOKEN 后：必须携带该密钥（可写在回调 URL 的 ?token=）。"""
    monkeypatch.setenv("KY_WEBHOOK_TOKEN", "WHSECRET")
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT,
                       {"X-KY-Token": "WHSECRET"}) is True
    assert _authorized("SECRET", f"{WEBHOOK_PATH}?token=WHSECRET", LAN_CLIENT) is True
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT) is False
    assert _authorized("SECRET", WEBHOOK_PATH, LOOPBACK) is False       # 配了就得带
    assert _authorized("SECRET", WEBHOOK_PATH, LAN_CLIENT,
                       {"X-KY-Token": "SECRET"}) is False               # 网关 token 不算


def test_webhook_no_token_configured_loopback_only(monkeypatch):
    """两者都没配：维持既有行为，仅回环放行。"""
    monkeypatch.delenv("KY_WEBHOOK_TOKEN", raising=False)
    assert _authorized("", WEBHOOK_PATH, LOOPBACK) is True
    assert _authorized("", WEBHOOK_PATH, LAN_CLIENT) is False


def test_webhook_secret_does_not_leak_to_data_endpoints(monkeypatch):
    """webhook 密钥只对 /webhook 生效，不得顺带放行 /api/live 等数据端点。"""
    monkeypatch.setenv("KY_WEBHOOK_TOKEN", "WHSECRET")
    assert _authorized("SECRET", "/api/live", LOOPBACK, {"X-KY-Token": "WHSECRET"}) is False
    assert _authorized("SECRET", "/api/live", LAN_CLIENT, {"X-KY-Token": "WHSECRET"}) is False
    assert _authorized("SECRET", "/v1/models", LOOPBACK) is False


def _post(url, headers=None, body=b""):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_webhook_endpoint_accepts_dedicated_secret(monkeypatch):
    """HTTP 级：空 body 的 /webhook（不会触发 LLM）按新规则鉴权。"""
    monkeypatch.setenv("KY_WEBHOOK_TOKEN", "WHSECRET")
    server = _serve(create_gateway_handler(token="SECRET"))
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _post(f"{base}{WEBHOOK_PATH}")[0] == 401
        assert _post(f"{base}{WEBHOOK_PATH}?token=WHSECRET")[0] == 200
        assert _post(f"{base}{WEBHOOK_PATH}",
                     {"X-KY-Token": "WHSECRET"})[0] == 200
    finally:
        server.shutdown()
        server.server_close()


# ── ④ Windows 下 npx 必须解析成真实可执行文件 ────────────────────────────

def test_clawbot_uses_resolved_npx_path(monkeypatch):
    """去 shell=True 后必须用 shutil.which 解析 npx，否则 WinError 2 不可用。"""
    resolved = r"C:\nvm4w\nodejs\npx.CMD"
    monkeypatch.setattr(cfgmod.shutil, "which", lambda name: resolved if name == "npx" else None)
    recorded = {}

    def fake_run(args, *a, **k):
        recorded["args"] = args
        recorded["shell"] = k.get("shell", False)

    monkeypatch.setattr(cfgmod.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

    with redirect_stdout(io.StringIO()):
        cfgmod.run_wechat_clawbot_install()

    assert recorded["args"][0] == resolved
    assert list(recorded["args"][1:]) == ["-y", cfgmod.WECHAT_CLAWBOT_CLI_PKG, "install"]
    assert recorded["shell"] is False


def test_clawbot_missing_npx_reports_clearly(monkeypatch):
    """解析不到 npx 时给出清晰提示，且不得尝试执行子进程。"""
    monkeypatch.setattr(cfgmod.shutil, "which", lambda name: None)
    monkeypatch.setattr(cfgmod.subprocess, "run",
                        lambda *a, **k: pytest.fail("解析失败时不应调用 subprocess"))
    buf = io.StringIO()
    with redirect_stdout(buf):
        cfgmod.run_wechat_clawbot_install()
    assert "未检测到 npx" in buf.getvalue()

