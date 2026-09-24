# -*- coding: utf-8 -*-
"""P0/P1/P2 回归 · ``tools/agent`` 与抓取层的沙箱 / SSRF / 体积上限加固。

缺陷现场（均为实测复现，见各测试 docstring）
--------------------------------------------
1. **run_command 沙箱绕过**：``tools/agent/tools_impl.py`` 的 ``run_command``
   只对程序名做白名单、对参数做高危正则，**位置参数里的路径从未过沙箱** ——
   ``cat /etc/passwd``、``cat C:/Users/x/.ssh/id_rsa``、``cat ../../outside.txt``
   都能读到工作区外任意文件（连 ``resolve_safe_path`` 明确拦截的 ``.json`` 也读到了）。
2. **记忆路径穿越**：``tools/agent/memory.py`` 的 ``get_file_path`` 在 scope 未命中
   白名单时回落成 ``project_memory_dir / f"{scope}.md"``，``scope="../../pwn"``
   可写到工作区外；且 ``read`` 是 Level 0，``--permission=safe`` 也放行。
3. **笔记锁闸门被绕过**：``assert_writable`` 只接在 ``error_logger`` 一条路径上，
   ``write_file`` / ``edit_file`` 未调用 —— 学员标注 ``locked: true`` 的笔记
   可被 LLM 静默覆盖。
4. **SSRF**：``fetch_url`` 的字符串黑名单挡不住 ``2130706433`` / ``127.1`` /
   ``[::ffff:127.0.0.1]`` / ``fd00::`` / ``fe80::``，且 ``urlopen`` 自动跟随
   3xx —— 公网 URL 302 到 ``127.0.0.1`` 即可打到本机（实测读到回环内容）。
5. **解压炸弹**：``gzip.decompress`` / ``zlib.decompress`` / ``brotli.decompress``
   与 ``resp.read()`` 全无上限，30KB 的 gzip 炸弹可膨胀成 31MB。
6. **MCP 响应无 id 校验**：``_send_request`` 只 ``readline()`` 一行就返回，
   超时后残留行被下一次请求消费 —— 实测 id=1 的响应被当成 id=2 的返回值。
7. **fetcher 只解 gzip**：请求头声明 ``Accept-Encoding: gzip, deflate``，
   但 deflate 回包不解压，正文变乱码。

每条守卫都配**阴性对照**：把守卫摘掉后，对应的坏行为必须重新出现，
以此证明断言确实由本次修复支撑（而不是测试本身没走到那条路径）。
"""

import gzip
import ipaddress
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent import tools_impl as tools_impl_mod  # noqa: E402
from tools.agent.memory import MemoryManager  # noqa: E402
from tools.agent.mcp_client import MCPProcessClient  # noqa: E402
from tools.agent.permissions import PermissionManager  # noqa: E402
from tools.agent.sandbox import Sandbox  # noqa: E402
from tools.agent.tools_impl import ToolRegistry  # noqa: E402
from tools import net_guard  # noqa: E402

OUTSIDE_SENTINEL = "SENTINEL_OUTSIDE_9f3a2b"


# ───────────────────────── 公共工具 ─────────────────────────

def _registry(workspace: Path) -> ToolRegistry:
    """构造一个只测沙箱层、不受审批层干扰的注册表。"""
    perm = PermissionManager(mode="auto", workspace_root=workspace)
    perm.force_allow_all = True
    return ToolRegistry(Sandbox(workspace_root=workspace), perm)


@pytest.fixture
def outside_file(tmp_path):
    """在沙箱根之外造一个含哨兵的 ``.txt`` 与 ``.json``。"""
    outer = tmp_path / "outside"
    outer.mkdir()
    txt = outer / "sentinel.txt"
    txt.write_text(OUTSIDE_SENTINEL + "\n", encoding="utf-8")
    js = outer / "secret.json"
    js.write_text('{"k":"SENTINEL_JSON_7788"}', encoding="utf-8")
    return outer, txt, js


def _cat_available() -> bool:
    """本机 ``cat`` 可执行文件是否存在。

    [F5 修复·环境依赖] 两个阴性对照用 ``cat <外部文件>`` 证明「摘掉守卫后
    命令真的能读到文件」。``cat`` 在 Linux/macOS 与 Git for Windows 的
    ``usr\\bin`` 自带，但在**未把 Git usr\\bin 加入 PATH 的 Windows 裸机**
    上根本不存在 —— 此时用例会以 ``WinError 2`` 失败，而这与被测守卫无关。
    无 ``cat`` 时显式跳过（而非失败），并在 CI 矩阵里由 Git 自带的 coreutils
    覆盖该路径。
    """
    return shutil.which("cat") is not None


needs_cat = pytest.mark.skipif(
    not _cat_available(),
    reason="本机 PATH 缺少 cat（Git usr\\bin 未加入 PATH），阴性对照无法执行",
)


# ═════════════════════ ① run_command 沙箱 ═════════════════════

def test_run_command_blocks_path_args_outside_workspace(tmp_path, outside_file):
    """``cat`` 工作区外绝对路径必须被拦（含 resolve_safe_path 会放行的 .txt 豁免）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)
    _outer, txt, js = outside_file

    out = reg.execute_tool("run_command", {"command": f"cat {txt.as_posix()}"}, interactive=False)
    assert OUTSIDE_SENTINEL not in out, f"工作区外 .txt 被读出: {out!r}"
    assert "安全拦截" in out

    out = reg.execute_tool("run_command", {"command": f"cat {js.as_posix()}"}, interactive=False)
    assert "SENTINEL_JSON_7788" not in out
    assert "安全拦截" in out


def test_run_command_blocks_relative_traversal(tmp_path, outside_file):
    """相对路径穿越（``..\\outside\\sentinel.txt``）必须被拦。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)
    _outer, txt, _js = outside_file

    out = reg.execute_tool(
        "run_command",
        {"command": f"cat {Path(os.path.relpath(txt, ws)).as_posix()}"},
        interactive=False)
    assert OUTSIDE_SENTINEL not in out, f"相对穿越读到了工作区外文件: {out!r}"
    assert "安全拦截" in out


def test_run_command_blocks_credential_and_system_paths(tmp_path):
    """``/etc/passwd``、``~/.aws/``、``.ssh`` 私钥必须被拦。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)
    for cmd in ("cat /etc/passwd",
                "cat /etc/hosts",
                "grep -r token ~/.aws/",
                "cat ~/.ssh/id_rsa",
                "cat C:/Users/nobody/.ssh/id_rsa"):
        out = reg.execute_tool("run_command", {"command": cmd}, interactive=False)
        assert "安全拦截" in out, f"未拦截: {cmd} -> {out!r}"


@pytest.mark.parametrize("command", [
    "ls -la",
    "cat a.txt b.txt",
    "head -n 1 a.txt",
    "tail -n 1 a.txt",
    "wc -l a.txt",
    "grep hello a.txt",
    "grep -rn hello sub",
    "git status --short",
    "git diff --stat",
])
def test_run_command_does_not_break_normal_readonly_usage(tmp_path, command):
    """正常只读用法不得被误伤（命令可以失败，但不得被沙箱拦）。"""
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "a.txt").write_text("hello-a\n", encoding="utf-8")
    (ws / "b.txt").write_text("hello-b\n", encoding="utf-8")
    (ws / "sub" / "c.md").write_text("hello-c\n", encoding="utf-8")
    reg = _registry(ws)

    out = reg.execute_tool("run_command", {"command": command}, interactive=False)
    assert "安全拦截" not in out, f"正常用法被误伤: {command} -> {out!r}"


@needs_cat
def test_negative_control_run_command_path_guard_is_what_blocks(
        tmp_path, outside_file, monkeypatch):
    """阴性对照（判定层）：把「像路径」判定摘掉后，工作区外文件必须真的被读到。

    证明上面那些「哨兵未出现」的断言确实由路径沙箱校验支撑，
    而不是命令根本没执行（例如 cat 不存在、路径拼错）。
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)
    _outer, txt, _js = outside_file

    monkeypatch.setattr(tools_impl_mod, "_looks_like_path_token", lambda token: False)

    out = reg.execute_tool("run_command", {"command": f"cat {txt.as_posix()}"}, interactive=False)
    assert OUTSIDE_SENTINEL in out, (
        f"摘掉路径判定后仍未读到工作区外文件，说明断言没指向该守卫: {out!r}")


@needs_cat
def test_negative_control_workspace_containment_is_what_blocks(
        tmp_path, outside_file, monkeypatch):
    """阴性对照（收口层）：放开「必须在工作区内」的收口后，外部 .txt 会被读到。

    单靠 ``resolve_safe_path`` 不够 —— 命令层额外加的 ``_is_inside_sandbox``
    才是这条断言真正的支撑。本对照必须**只**摘掉这一个变量：

    [B2b] ``resolve_safe_path`` 的「工作区外 + 只读 + 白名单扩展名」豁免已收紧为
    「默认拒绝 + 本会话授权后放行」，因此先走合法授权路径
    （``register_authorized_read_dir``）把该目录登记为本会话已授权 —— 这样沙箱层
    的放行条件与 B2b 之前等价，被摘掉的仍然只有 ``_is_inside_sandbox`` 收口。
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)
    _outer, txt, _js = outside_file
    reg.sandbox.register_authorized_read_dir(txt.parent)   # [B2b] 合法授权路径

    monkeypatch.setattr(tools_impl_mod, "_is_inside_sandbox", lambda sandbox, resolved: True)

    out = reg.execute_tool("run_command", {"command": f"cat {txt.as_posix()}"}, interactive=False)
    assert OUTSIDE_SENTINEL in out, (
        f"放开工作区收口后仍未读到外部文件，说明断言没指向该守卫: {out!r}")


def test_run_command_blocks_path_embedded_in_option(tmp_path, outside_file):
    """``--opt=VALUE`` 形式内嵌的越界路径必须被拦。

    缺陷现场：``_looks_like_path_token`` 对「以 ``-`` 开头」的 token 一律放行，
    于是 ``grep --file=/etc/passwd x``、``wc --files0-from=/etc/passwd`` 这类把
    路径塞进选项 ``=`` 右侧的写法完全绕过了位置参数沙箱。
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)
    _outer, txt, js = outside_file

    for cmd in (f"grep --file={txt.as_posix()} x",
                f"wc --files0-from={txt.as_posix()}",
                f"grep --file={js.as_posix()} x"):
        out = reg.execute_tool("run_command", {"command": cmd}, interactive=False)
        assert "安全拦截" in out, f"选项内嵌路径未被拦: {cmd} -> {out!r}"
        assert OUTSIDE_SENTINEL not in out and "SENTINEL_JSON_7788" not in out


@pytest.mark.parametrize("command", [
    "ls --color=always",
    "grep --color=auto -n hello a.txt",
    "git log --oneline --max-count=5",
    "grep -rn hello sub",
])
def test_run_command_does_not_misjudge_option_values(tmp_path, command):
    """纯选项值（``--color=auto`` / ``--max-count=5``）不得被误判成路径而拦截。"""
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "a.txt").write_text("hello-a\n", encoding="utf-8")
    reg = _registry(ws)

    out = reg.execute_tool("run_command", {"command": command}, interactive=False)
    assert "安全拦截" not in out, f"选项值被误判为路径: {command} -> {out!r}"


def test_negative_control_option_embedded_path_is_what_blocks(
        tmp_path, outside_file, monkeypatch):
    """阴性对照：摘掉 ``--opt=VALUE`` 拆值后，选项内嵌的越界路径不再被拦。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)
    _outer, txt, _js = outside_file

    monkeypatch.setattr(tools_impl_mod, "_split_option_value", lambda token: None)

    out = reg.execute_tool("run_command",
                           {"command": f"grep --file={txt.as_posix()} x"},
                           interactive=False)
    assert "安全拦截" not in out, (
        f"摘掉拆值后仍被拦，说明断言没指向该守卫: {out!r}")


# ═════════════════════ ② 记忆路径穿越 ═════════════════════

def test_memory_scope_whitelist_rejects_traversal(tmp_path):
    """``get_file_path`` 对非白名单 scope 必须拒绝，而不是回落拼路径。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    mm = MemoryManager(workspace_root=ws)

    for scope in ("../../pwn", "..\\..\\win_pwn", "../escape", "unknown", "global/../x"):
        with pytest.raises(ValueError):
            mm.get_file_path(scope)


def test_memory_write_cannot_escape_workspace(tmp_path):
    """``write_memory`` 不得把文件写到工作区外。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    mm = MemoryManager(workspace_root=ws)

    with pytest.raises(ValueError):
        mm.write_memory("../../pwn", "PWNED_BY_TRAVERSAL_1234")

    assert not (tmp_path / "pwn.md").exists(), "越界文件被创建了"
    assert not (ws.parent / "pwn.md").exists()


def test_memory_tool_reports_illegal_scope_even_in_safe_mode(tmp_path):
    """``--permission=safe`` 下 ``manage_memory(read, ../../x)`` 也必须拒绝。

    这条正是缺陷报告点出的「read 是 Level 0，safe 模式也放行」。
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    mm = MemoryManager(workspace_root=ws)
    perm = PermissionManager(mode="safe", workspace_root=ws)
    reg = ToolRegistry(Sandbox(workspace_root=ws), perm, memory_manager=mm)

    out = reg.execute_tool("manage_memory", {"action": "read", "scope": "../../pwn"},
                           interactive=False)
    assert "非法记忆作用域" in out, f"越界 scope 未被拒绝: {out!r}"

    # 合法 scope 仍须可用
    out = reg.execute_tool("manage_memory", {"action": "read", "scope": "session"},
                           interactive=False)
    assert "非法记忆作用域" not in out


def test_negative_control_legacy_get_file_path_escapes(tmp_path, monkeypatch):
    """阴性对照：恢复旧实现（回落拼 scope.md）后，越界写必须重新发生。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    mm = MemoryManager(workspace_root=ws)

    def _legacy(scope: str) -> Path:
        norm = scope.lower().strip()
        if norm in mm._files:
            return mm._files[norm]
        return mm.project_memory_dir / f"{norm}.md"

    monkeypatch.setattr(MemoryManager, "get_file_path",
                        lambda self, scope: _legacy(scope))

    assert mm.write_memory("../../pwn", "PWNED_BY_TRAVERSAL_1234") is True
    escaped = (ws / ".memory" / ".." / ".." / "pwn.md").resolve()
    assert escaped.exists(), "旧实现竟然没越界，说明对照前提不成立"
    assert not str(escaped).startswith(str(ws.resolve()))
    escaped.unlink()


# ═════════════════════ ③ 笔记锁闸门 ═════════════════════

LOCKED_NOTE = "---\ntitle: 学员精修笔记\nlocked: true\n---\n# 我的笔记\n原始内容\n"


def test_write_file_refuses_locked_note(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    note = ws / "note_locked.md"
    note.write_text(LOCKED_NOTE, encoding="utf-8")
    reg = _registry(ws)

    out = reg.execute_tool(
        "write_file", {"path": "note_locked.md", "content": "LLM 覆盖内容", "overwrite": True},
        interactive=False)

    assert "已锁定" in out, f"锁定笔记未被拦: {out!r}"
    assert note.read_text(encoding="utf-8") == LOCKED_NOTE


def test_edit_file_refuses_locked_note(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    note = ws / "note_locked.md"
    note.write_text(LOCKED_NOTE, encoding="utf-8")
    reg = _registry(ws)

    out = reg.execute_tool(
        "edit_file", {"path": "note_locked.md", "target_content": "原始内容",
                      "replacement": "HACKED"},
        interactive=False)

    assert "已锁定" in out, f"锁定笔记未被拦: {out!r}"
    assert "HACKED" not in note.read_text(encoding="utf-8")


def test_unlocked_note_and_binary_are_unaffected(tmp_path):
    """未锁定笔记与非 Markdown 文件必须照常可写（闸门不能过度拦截）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)

    assert "Success" in reg.execute_tool(
        "write_file", {"path": "free.md", "content": "ok", "overwrite": True},
        interactive=False)

    (ws / "data.bin").write_text("raw", encoding="utf-8")
    assert "Success" in reg.execute_tool(
        "write_file", {"path": "data.bin", "content": "raw2", "overwrite": True},
        interactive=False)


def test_negative_control_note_lock_guard_is_what_blocks(tmp_path, monkeypatch):
    """阴性对照：摘掉笔记锁守卫后，锁定笔记必须真的被覆盖。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    note = ws / "note_locked.md"
    note.write_text(LOCKED_NOTE, encoding="utf-8")
    reg = _registry(ws)

    monkeypatch.setattr(tools_impl_mod, "_note_lock_error", lambda path: None)

    out = reg.execute_tool(
        "write_file", {"path": "note_locked.md", "content": "HACKED", "overwrite": True},
        interactive=False)

    assert "Success" in out, f"摘掉守卫后写入仍失败，断言没指向该守卫: {out!r}"
    assert note.read_text(encoding="utf-8") == "HACKED"


# ═════════════════════ ④ SSRF ═════════════════════

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://0.0.0.0/",
    "http://2130706433/",              # 127.0.0.1 的十进制整数写法
    "http://0x7f000001/",              # 十六进制写法
    "http://127.1/",                   # 简写
    "http://[::ffff:127.0.0.1]/",      # IPv4-mapped IPv6
    "http://[::1]/",
    "http://[fd00::1]/",               # ULA
    "http://[fe80::1]/",               # 链路本地
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.1/",
    "http://10.0.0.5/",
    "http://172.16.0.1/",
    "ftp://example.com/",
    "file:///etc/passwd",
])
def test_assert_url_safe_rejects_internal_and_non_http(url):
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe(url)


def test_assert_url_safe_allows_public_ip_literal():
    """公网 IP 字面量必须放行（否则等于禁网）。"""
    assert net_guard.assert_url_safe("https://93.184.216.34/") == "https://93.184.216.34/"


def test_negative_control_ip_guard_is_what_blocks(monkeypatch):
    """阴性对照：摘掉 IP 判定后，回环地址必须变成可放行。"""
    monkeypatch.setattr(net_guard, "_ip_is_blocked", lambda ip_obj: False)
    assert net_guard.assert_url_safe("http://127.0.0.1/") == "http://127.0.0.1/"


def test_redirect_handler_rejects_internal_target():
    """重定向目标必须重新校验（单元级）。"""
    handler = net_guard.SafeRedirectHandler()
    req = urllib.request.Request("http://example.com/")
    with pytest.raises(net_guard.UnsafeURLError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/secret")


def test_negative_control_redirect_guard_is_what_blocks(monkeypatch):
    """阴性对照：换成 urllib 默认（不校验）的 redirect_request 后必须放行。"""
    monkeypatch.setattr(net_guard.SafeRedirectHandler, "redirect_request",
                        urllib.request.HTTPRedirectHandler.redirect_request)
    handler = net_guard.SafeRedirectHandler()
    req = urllib.request.Request("http://example.com/")
    new = handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/secret")
    assert new is not None and "127.0.0.1" in new.full_url


class _Redirector(BaseHTTPRequestHandler):
    """``/`` 返回 302 到本机回环的 ``/secret``；``/secret`` 返回哨兵。"""

    loopback_port = 0

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.loopback_port}/secret")
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"REDIRECT_REACHED_LOOPBACK_99zz")

    def log_message(self, *args):  # noqa: D102
        pass


@pytest.fixture
def redirector(monkeypatch):
    """起一个「公网入口 302 到回环」的本地服务，并伪造 DNS 让入口可连通。

    这里刻意让「校验用的解析器」与「真正建连的解析器」给出不同答案，
    正是 DNS 重绑定 / 开放重定向攻击的真实形态。

    [P1-② 修复] 校验通过后，被 pin 到连接上的 IP 就是 ``93.184.216.34``
    （``_fake_resolve`` 的返回值）—— 建连时不再走 ``entry_host`` 的二次解析。
    因此 ``_fake_gai`` 必须把**被 pin 的 IP** 也落到本地测试服务上，
    否则用例会去连真实的 ``93.184.216.34``（既发外网请求、也测不到东西）。
    """
    real_resolve = net_guard.resolve_host_ips
    real_gai = socket.getaddrinfo
    entry_host = "public-entry.test"
    pinned_ip = "93.184.216.34"

    server = TCPServer(("127.0.0.1", 0), _Redirector)
    _Redirector.loopback_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def _fake_resolve(host):
        if net_guard._host_is_ip_literal(host):
            return real_resolve(host)          # 字面量按真实解析 → 127.0.0.1 会被拦
        return [ipaddress.ip_address(pinned_ip)]

    def _fake_gai(host, port, *args, **kwargs):
        if host in (entry_host, pinned_ip):
            host = "127.0.0.1"                 # 让真实 TCP 连到本地服务
        return real_gai(host, port, *args, **kwargs)

    monkeypatch.setattr(net_guard, "resolve_host_ips", _fake_resolve)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_gai)

    try:
        yield f"http://{entry_host}:{_Redirector.loopback_port}/"
    finally:
        server.shutdown()
        server.server_close()


def test_safe_urlopen_blocks_redirect_to_loopback(redirector):
    """端到端：公网入口 302 到 127.0.0.1 必须被拦，不得读到回环内容。"""
    req = urllib.request.Request(redirector, headers={"User-Agent": "test"})
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.safe_urlopen(req, timeout=5)


def test_negative_control_redirect_end_to_end(redirector, monkeypatch):
    """阴性对照：摘掉重定向守卫后，回环内容必须真的被读到。"""
    monkeypatch.setattr(net_guard.SafeRedirectHandler, "redirect_request",
                        urllib.request.HTTPRedirectHandler.redirect_request)
    req = urllib.request.Request(redirector, headers={"User-Agent": "test"})
    with net_guard.safe_urlopen(req, timeout=5) as resp:
        body = resp.read(200).decode("utf-8", "ignore")
    assert "REDIRECT_REACHED_LOOPBACK_99zz" in body, (
        f"摘掉守卫后仍未读到回环内容，说明对照前提不成立: {body!r}")


def test_fetch_url_tool_rejects_ssrf_targets(tmp_path):
    """工具层：``fetch_url`` 对 SSRF 目标返回错误而不是发请求。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = _registry(ws)
    for url in ("http://127.0.0.1/", "http://2130706433/", "http://[::ffff:127.0.0.1]/"):
        out = reg.execute_tool("fetch_url", {"url": url}, interactive=False)
        assert "Error" in out, f"SSRF 目标未被拒: {url} -> {out!r}"


# ═════════════════════ ⑤ 响应与解压上限 ═════════════════════

def test_gzip_bomb_is_truncated_by_cap():
    """30MB 级 gzip 炸弹必须被截断，而不是无上限解压。"""
    from tools.llm_client import _decompress_response_bytes

    bomb = gzip.compress(b"\x00" * (60 * 1024 * 1024), compresslevel=9)
    assert len(bomb) < 100 * 1024, "炸弹压缩后应远小于解压后体积"

    out = _decompress_response_bytes(bomb, {"Content-Encoding": "gzip"})
    assert net_guard.TRUNCATION_MARKER in out
    assert len(out) <= net_guard.MAX_DECOMPRESSED_BYTES + len(net_guard.TRUNCATION_MARKER) + 8


def test_negative_control_unbounded_decompress_would_expand_fully():
    """阴性对照：无上限的 ``gzip.decompress`` 会把炸弹完整解出来（说明上限确有必要）。"""
    bomb = gzip.compress(b"\x00" * (60 * 1024 * 1024), compresslevel=9)
    full = gzip.decompress(bomb)
    assert len(full) == 60 * 1024 * 1024
    assert len(full) > net_guard.MAX_DECOMPRESSED_BYTES


def test_decompress_limited_passes_through_small_payloads():
    """小载荷必须原样返回（上限不能误伤正常流量）。"""
    text = "考研政治核心考点：唯物辩证法".encode("utf-8")
    out, truncated = net_guard.decompress_limited(gzip.compress(text), "gzip")
    assert out == text and truncated is False

    out, truncated = net_guard.decompress_limited(zlib.compress(text), "deflate")
    assert out == text and truncated is False

    out, truncated = net_guard.decompress_limited(text, "")
    assert out == text and truncated is False


def test_llm_client_never_reads_unbounded():
    """静态盘点：llm_client / loop / fetcher 的 ``resp.read()`` 都必须带上限。"""
    targets = [
        REPO_ROOT / "tools" / "llm_client.py",
        REPO_ROOT / "tools" / "agent" / "loop.py",
        REPO_ROOT / "tools" / "intelligence" / "fetcher.py",
    ]
    offenders = []
    for py in targets:
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "resp.read()" in stripped:
                offenders.append(f"{py.relative_to(REPO_ROOT)}:{i}: {stripped}")
    assert not offenders, "仍有无上限的 resp.read():\n  " + "\n  ".join(sorted(set(offenders)))


# ═════════════════════ ⑥ MCP id 校验 ═════════════════════

class _FakeStdin:
    def write(self, _s):  # noqa: D102
        pass

    def flush(self):  # noqa: D102
        pass


class _ScriptedStdout:
    """按脚本吐行：第 1 次请求的响应「迟到」，第 2 次请求又吐回 id=1 的旧响应。"""

    def __init__(self, lines):
        self._lines = list(lines)
        self._lock = threading.Lock()

    def readline(self):
        with self._lock:
            if not self._lines:
                time.sleep(0.05)
                return ""
            delay, line = self._lines.pop(0)
        if delay:
            time.sleep(delay)
        return line


class _FakeProc:
    def __init__(self, stdout):
        self.stdin = _FakeStdin()
        self.stdout = stdout


def _client_with_script(lines) -> MCPProcessClient:
    c = MCPProcessClient(name="fake", command="x", args=[])
    c.process = _FakeProc(_ScriptedStdout(lines))
    c.is_initialized = True
    return c


def test_mcp_stale_response_is_discarded():
    """超时后残留的旧响应不得被下一次请求当成自己的返回值。"""
    stale = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"STALE": True}}) + "\n"
    fresh = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"FRESH": True}}) + "\n"
    c = _client_with_script([(2.0, stale), (0.0, stale), (0.0, fresh)])

    assert c._send_request("tools/list", {}, timeout=1) is None      # 第 1 次超时

    resp = c._send_request("tools/call", {"name": "x"}, timeout=5)   # 第 2 次
    assert resp is not None and resp.get("id") == 2, f"id 校验失效: {resp}"
    assert resp["result"] == {"FRESH": True}, f"拿到了串位的旧响应: {resp}"


def test_mcp_notification_without_id_is_ignored():
    """无 id 的通知不得被当成响应返回。"""
    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}) + "\n"
    resp_line = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"OK": True}}) + "\n"
    c = _client_with_script([(0.0, note), (0.0, resp_line)])
    resp = c._send_request("tools/list", {}, timeout=5)
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {"OK": True}}


def test_negative_control_naive_reader_returns_stale_response():
    """阴性对照：旧实现（readline 一行就返回）必然拿到串位的旧响应。"""
    stale = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"STALE": True}}) + "\n"
    fresh = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"FRESH": True}}) + "\n"
    c = _client_with_script([(2.0, stale), (0.0, stale), (0.0, fresh)])

    def _legacy_send(method, params, timeout=15):
        c.msg_id += 1
        import queue as _q
        q: _q.Queue = _q.Queue()

        def _reader():
            q.put(c.process.stdout.readline())

        threading.Thread(target=_reader, daemon=True).start()
        try:
            line = q.get(timeout=timeout)
        except _q.Empty:
            return None
        return json.loads(line.strip())

    assert _legacy_send("tools/list", {}, timeout=1) is None
    resp = _legacy_send("tools/call", {"name": "x"}, timeout=5)
    assert resp.get("id") == 1 and resp["result"] == {"STALE": True}, (
        f"旧实现竟然没串位，说明对照前提不成立: {resp}")


def test_mcp_reader_thread_is_singleton_and_stopped():
    """reader 线程必须复用（不随每次请求/超时泄漏）。"""
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"A": 1}}) + "\n"
    c = _client_with_script([(0.0, line)] * 5)
    c._send_request("tools/list", {}, timeout=3)
    t1 = c._reader_thread
    c._send_request("tools/list", {}, timeout=3)
    assert c._reader_thread is t1, "reader 线程被重复创建（旧实现每次请求泄漏一个）"
    c.stop()
    assert c._reader_thread is None


# ═════════════════════ ⑦ fetcher deflate ═════════════════════

def _fake_response(body: bytes, headers: dict):
    class _Resp:
        status = 200

        def __init__(self):
            self.headers = headers

        def read(self, n=-1):  # noqa: D102
            return body

        def __enter__(self):  # noqa: D102
            return self

        def __exit__(self, *a):  # noqa: D102
            return False

    return _Resp()


@pytest.mark.parametrize("encoding,compress", [
    ("gzip", gzip.compress),
    ("deflate", zlib.compress),
])
def test_fetcher_decodes_gzip_and_deflate(monkeypatch, encoding, compress):
    """``HTTPFetcher.fetch`` 必须同时解 gzip 与 deflate（旧实现只解 gzip）。"""
    from tools.intelligence import fetcher as fetcher_mod

    html = "<html><head><title>2027 专业目录</title></head><body>正文</body></html>"
    body = compress(html.encode("utf-8"))

    monkeypatch.setattr(fetcher_mod, "assert_url_safe", lambda url: url)
    monkeypatch.setattr(fetcher_mod, "safe_urlopen",
                        lambda req, timeout=6, context=None: _fake_response(
                            body, {"Content-Encoding": encoding, "Content-Type": "text/html"}))

    res = fetcher_mod.HTTPFetcher().fetch("https://yjs.example.edu.cn/a.html")
    assert res.is_valid and res.status_code == 200
    assert "2027 专业目录" in res.content, (
        f"{encoding} 回包未被正确解压: {res.content[:120]!r}")


def test_negative_control_deflate_would_be_garbage_without_fix(monkeypatch):
    """阴性对照：只解 gzip 的旧逻辑会把 deflate 回包当成乱码正文。"""
    html = "<html><head><title>2027 专业目录</title></head><body>正文</body></html>"
    body = zlib.compress(html.encode("utf-8"))

    # 复刻旧实现：只认 gzip
    def _legacy_decompress(raw, headers):
        if headers.get("Content-Encoding") == "gzip":
            return gzip.decompress(raw)
        return raw

    raw, _ = _legacy_decompress(body, {"Content-Encoding": "deflate"}), None
    assert "2027 专业目录" not in raw.decode("utf-8", errors="ignore"), (
        "旧逻辑竟然能解出正文，说明对照前提不成立")


def test_fetcher_blocks_ssrf_targets(monkeypatch):
    """``HTTPFetcher.fetch`` 对回环/内网目标必须返回 BLOCKED 且不发请求。"""
    from tools.intelligence import fetcher as fetcher_mod

    def _boom(*a, **kw):  # pragma: no cover - 不应被调用
        raise AssertionError("SSRF 目标不应发起真实请求")

    monkeypatch.setattr(fetcher_mod, "safe_urlopen", _boom)

    for url in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/",
                "http://2130706433/", "http://[::ffff:127.0.0.1]/"):
        res = fetcher_mod.HTTPFetcher().fetch(url)
        assert res.is_valid is False and res.access_status == "BLOCKED", (
            f"未拦截 SSRF 目标: {url} -> {res.access_status}")
