# -*- coding: utf-8 -*-
"""B2a 回归 · 「写脚本再执行」闸门（脚本执行收紧 + 会话污染规则）。

缺陷现场
--------
``run_command`` 的程序白名单与 python 收紧（禁 ``-c`` / ``-m pip``、仅 ``.py``）
挡住了直接 RCE，但挡不住**两步走**：agent 先用 ``write_file`` 在工作区内落一个
``evil.py``，再 ``python evil.py`` —— 路径校验（"必须在工作区内"）全部通过，
等于绕开审批执行任意代码。这是权限模型里唯一一条这样的路径。

修复（两道闸门，均在 ``run_command`` 内、既有校验之后）
------------------------------------------------------
① **脚本路径 allowlist（硬闸门）**：仅 ``tools/`` / ``tests/`` / ``rust_ext/``
   等受控目录下的脚本可执行；越界一律拒绝，不走审批通道。
② **会话污染规则**：本会话被 ``write_file`` / ``edit_file`` 写过或改过的文件，
   执行前必须经审批通道**显式批准** —— ``auto`` 模式不得自动放行；headless
   （无人在场）一律拒绝，文案可辨识。污染集合是**进程级**的（GUI 每条消息新建
   runner，实例级集合会被清空），见 ``test_pollution_is_shared_across_registries_in_process``。

授权记忆**不落盘**：批准只在本次调用内生效，工作区不产生任何授权状态文件
（见 ``test_approval_leaves_no_auth_state_file_in_workspace``）。

测试策略（为什么用「执行探针」而不是直接调本机 python）
----------------------------------------------------
本机 ``python`` 可能是 Microsoft Store 的占位 stub（实测 exit=49、零输出），
直接调用无法执行脚本 —— 那么"哨兵文件不存在"会因为**脚本根本跑不起来**而假通过。
因此除一条真实子进程用例（本机 python 可用时才跑）外，本文件统一安装
:class:`_ExecProbe`：把命令里的 ``python``/``python3`` 换成**当前测试解释器**
``sys.executable``，其余参数原样透传。于是：

* 放行时脚本是**真实执行**的（哨兵由脚本自己创建、哨兵串出现在工具返回里）；
* 拦截时探针根本不被调用（``probe.calls == []``）+ 哨兵不存在，双证据。

每条守卫都配**阴性对照**：把守卫摘掉后，同一个脚本必须真的被执行
（``test_negative_control_*``），以此证明断言确实由本次修复支撑。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent import sandbox as sandbox_mod  # noqa: E402
from tools.agent import tools_impl as tools_impl_mod  # noqa: E402
from tools.agent.permissions import PermissionLevel, PermissionManager  # noqa: E402
from tools.agent.sandbox import Sandbox  # noqa: E402
from tools.agent.tools_impl import (  # noqa: E402
    _SCRIPT_EXEC_ALLOWED_PREFIXES,
    ToolRegistry,
)

#: 脚本执行后会落下的哨兵文件（相对工作区，脚本自身创建）。
SENTINEL_FILE = "__b2a_executed_sentinel__.txt"
#: 脚本执行后打印的哨兵串（应出现在 run_command 的返回里）。
SENTINEL_STDOUT = "B2A_EXECUTED_MARKER_5c1f"

#: 待执行脚本的源码：创建哨兵文件 + 打印哨兵串。
EVIL_SOURCE = (
    "from pathlib import Path\n"
    f"Path({SENTINEL_FILE!r}).write_text('EXECUTED', encoding='utf-8')\n"
    f"print({SENTINEL_STDOUT!r})\n"
)


# ───────────────────────── 公共工具 ─────────────────────────

@pytest.fixture
def ws(tmp_path):
    """独立工作区（含受控目录 tools/，与真实仓库完全隔离）。"""
    root = tmp_path / "ws"
    (root / "tools").mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def _isolate_process_wide_pollution(monkeypatch):
    """污染集合是**进程级**全局（GUI 每条消息新建 runner，见 sandbox.py 说明）：
    逐条用例换成全新集合，既不依赖也不污染同进程里的其他测试。"""
    monkeypatch.setattr(sandbox_mod, "_SESSION_WRITTEN_FILES", set())


class _ExecProbe:
    """执行探针：替换 ``tools_impl`` 的 ``subprocess.run``（见模块 docstring）。

    记录每次 argv，并把 ``python``/``python3`` 替换为 ``sys.executable`` 后
    **真实执行** —— 脚本的副作用（哨兵文件 / 打印）由脚本自己产生。
    """

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        prog = os.path.basename(argv[0]).lower()
        if prog.endswith(".exe"):
            prog = prog[:-4]
        if prog in ("python", "python3"):
            argv[0] = sys.executable
        return subprocess.run(argv, **kwargs)


class _SubprocessProxy:
    """只覆盖 ``run`` 的 subprocess 代理，避免污染进程内其他调用方。"""

    def __init__(self, real, run):
        self._real = real
        self.run = run

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def probe(monkeypatch):
    """安装执行探针（仅替换 ``tools_impl`` 命名空间里的 subprocess.run）。"""
    p = _ExecProbe()
    monkeypatch.setattr(tools_impl_mod, "subprocess", _SubprocessProxy(subprocess, p))
    return p


class _FakeApprovalChannel:
    """假审批通道（A3a 通道协议的测试替身）：记录请求，按预设答复返回。"""

    def __init__(self, approve=True, reason="用户批准单次执行"):
        self.is_interactive = True
        self.session_allowed_tools = set()
        self.requests = []
        self._approve = approve
        self._reason = reason

    def request(self, tool_name, level, tool_args):
        self.requests.append((tool_name, level, dict(tool_args)))
        return (self._approve, self._reason)

    def request_plan(self, tool_name, level, tool_args):
        return self.request(tool_name, level, tool_args)


class _FakeStdin:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


def _feed_input(monkeypatch, answers):
    """按序喂给 ``input()``；用尽后抛断言错误（防实现多问一次）。"""
    it = iter(answers)

    def _fake_input(*_a, **_k):
        try:
            return next(it)
        except StopIteration:  # pragma: no cover - 只有实现回归才会走到
            raise AssertionError("实现调用了比预期更多的 input()")

    monkeypatch.setattr("builtins.input", _fake_input)


def _registry(ws, *, mode="auto", approval_channel=None):
    perm = PermissionManager(mode=mode, workspace_root=ws,
                             approval_channel=approval_channel)
    return ToolRegistry(Sandbox(workspace_root=ws), perm)


def _plant_polluted_script(reg, ws, rel_path, source=EVIL_SOURCE):
    """直接落盘一个脚本并登记为「本会话写入」。

    绕开写工具自身的权限门禁，让 ask / plan / safe 模式的用例只聚焦**执行闸门**
    （这些模式下 write_file 本身就会被拒，见 test_headless_other_modes_also_deny）。
    """
    p = ws / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    reg.sandbox.register_written_file(p)
    return p


def _write_script(reg, rel_path, source=EVIL_SOURCE):
    """走真实写工具落脚本（auto 模式下 write_file 可用）。"""
    out = reg.execute_tool("write_file", {"path": rel_path, "content": source},
                           interactive=False)
    assert "Success" in out, f"写脚本失败: {out}"
    return out


def _snapshot(root: Path) -> dict:
    """工作区字节指纹（忽略解释器自动生成的 __pycache__ / .pyc）。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".pyc"):
                continue
            p = Path(dirpath) / fn
            out[str(p.relative_to(root)).replace("\\", "/")] = p.read_bytes()
    return out


# ═══════════ ① headless：本会话写入的脚本必须被拒（核心现场） ═══════════

def test_headless_auto_denies_session_written_script(ws, probe):
    """核心现场：auto + headless（默认 deny_all）下，写脚本再执行必须被拒。

    auto 模式对 Level 3 是自动放行的 —— 这条断言正是"auto 不得覆盖污染规则"。
    """
    reg = _registry(ws, mode="auto")
    assert reg.permissions.headless_policy == "deny_all", "headless 默认策略应为 deny_all"
    _write_script(reg, "tools/evil.py")

    out = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                           interactive=False)

    assert "PermissionDenied" in out, out
    assert "本会话写入的脚本" in out, f"缺少可辨识关键词: {out!r}"
    assert "需在交互终端批准" in out, f"缺少可辨识关键词: {out!r}"
    assert probe.calls == [], f"污染脚本仍被交给子进程执行: {probe.calls}"
    assert SENTINEL_STDOUT not in out
    assert not (ws / SENTINEL_FILE).exists(), "脚本被真实执行了（哨兵文件出现）"


@pytest.mark.parametrize("mode", ["ask", "plan", "safe"])
def test_headless_other_modes_also_deny(ws, probe, mode):
    """其余模式在 headless 下同样拒绝（不执行、无哨兵）。

    注：ask / plan / safe 模式下 run_command 在通用权限层就已被拒（headless
    无人可批），本闸门是其后的纵深防御 —— 断言只要求"拒绝且未执行"。
    """
    reg = _registry(ws, mode=mode)
    _plant_polluted_script(reg, ws, "tools/evil.py")

    out = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                           interactive=False)

    assert "PermissionDenied" in out or "安全拦截" in out, out
    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


@pytest.mark.parametrize("script_arg", [
    "tools/evil.py",
    "./tools/evil.py",
    "tools/../tools/evil.py",
    "tools/./evil.py",
])
def test_path_spellings_cannot_bypass_pollution(ws, probe, script_arg):
    """路径规范化对抗：等价写法不得绕过污染集合匹配。"""
    reg = _registry(ws, mode="auto")
    _write_script(reg, "tools/evil.py")

    out = reg.execute_tool("run_command", {"command": f"python {script_arg}"},
                           interactive=False)

    assert "PermissionDenied" in out and "本会话写入的脚本" in out, out
    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


@pytest.mark.skipif(os.name != "nt", reason="大小写不敏感文件系统仅 Windows")
def test_windows_case_variants_cannot_bypass_pollution(ws, probe):
    """Windows 大小写变体（目录名 / 文件名）不得绕过污染集合匹配。"""
    reg = _registry(ws, mode="auto")
    _write_script(reg, "tools/Evil.py")

    for script_arg in ("python tools/evil.py", "python TOOLS/EVIL.PY"):
        out = reg.execute_tool("run_command", {"command": script_arg}, interactive=False)
        assert "PermissionDenied" in out and "本会话写入的脚本" in out, (script_arg, out)

    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


def test_pollution_is_shared_across_registries_in_process(ws, probe):
    """GUI 场景：每条消息新建 AgentRunner/Sandbox，污染集合必须跨实例共享。

    否则「第 1 条消息写脚本、第 2 条消息执行」就能绕过闸门（agent_worker 每条
    用户消息都新建 runner）。
    """
    reg1 = _registry(ws, mode="auto")
    _write_script(reg1, "tools/evil.py")

    reg2 = _registry(ws, mode="auto")     # 模拟 GUI 的下一条消息：全新 runner
    out = reg2.execute_tool("run_command", {"command": "python tools/evil.py"},
                            interactive=False)

    assert "PermissionDenied" in out and "本会话写入的脚本" in out, out
    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


def test_edit_file_also_pollutes_existing_script(ws, probe):
    """edit_file 改过的既有脚本同样触发污染规则：先跑通 → 改一行 → 被拒。"""
    reg = _registry(ws, mode="auto")
    p = ws / "tools" / "existing.py"
    p.write_text("# v1\n" + EVIL_SOURCE, encoding="utf-8")

    ok = reg.execute_tool("run_command", {"command": "python tools/existing.py"},
                          interactive=False)
    assert SENTINEL_STDOUT in ok, f"未污染脚本本应可执行: {ok!r}"
    assert (ws / SENTINEL_FILE).exists()
    (ws / SENTINEL_FILE).unlink()

    edit = reg.execute_tool("edit_file", {
        "path": "tools/existing.py",
        "target_content": "# v1",
        "replacement": "# v2",
    }, interactive=False)
    assert "Success" in edit, edit

    out = reg.execute_tool("run_command", {"command": "python tools/existing.py"},
                           interactive=False)
    assert "PermissionDenied" in out and "本会话写入的脚本" in out, out
    assert not (ws / SENTINEL_FILE).exists()


# ═══════════ ② allowlist 硬闸门（受控目录之外一律拒绝） ═══════════

def test_allowlist_constant_is_module_level_and_covers_controlled_dirs():
    """allowlist 必须是模块级常量（便于测试引用与审计）。"""
    assert set(_SCRIPT_EXEC_ALLOWED_PREFIXES) >= {"tools/", "tests/", "rust_ext/"}


def test_unpolluted_script_outside_allowlist_is_rejected(ws, probe):
    """allowlist 与污染规则相互独立：没被写过、但在受控目录之外 → 拒绝。"""
    reg = _registry(ws, mode="auto")
    p = ws / "05-考研看板" / "evil.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(EVIL_SOURCE, encoding="utf-8")

    out = reg.execute_tool("run_command", {"command": "python 05-考研看板/evil.py"},
                           interactive=False)

    assert "仅允许执行受控目录" in out, out
    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


def test_polluted_script_outside_allowlist_is_rejected_by_allowlist(ws, probe):
    """两道闸门叠加时，allowlist 先拒（硬拒绝，不给审批口子）。"""
    reg = _registry(ws, mode="auto")
    _write_script(reg, "05-考研看板/evil.py")

    out = reg.execute_tool("run_command", {"command": "python 05-考研看板/evil.py"},
                           interactive=False)

    assert "仅允许执行受控目录" in out, out
    assert "PermissionDenied" not in out, "越界脚本不得给出可批准的路径"
    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


# ═══════════ ③ 防误伤：受控目录里未被写过的脚本照常执行 ═══════════

@pytest.mark.parametrize("rel", ["tools/ok.py", "tests/ok.py", "rust_ext/ok.py"])
def test_unpolluted_allowlisted_script_still_runs(ws, probe, rel):
    """正常用法行为不变：受控目录里**非本会话写入**的脚本，auto 模式照常执行。"""
    reg = _registry(ws, mode="auto")
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(EVIL_SOURCE, encoding="utf-8")   # 直接落盘，不经写工具 → 不污染

    out = reg.execute_tool("run_command", {"command": f"python {rel}"}, interactive=False)

    assert "PermissionDenied" not in out and "安全拦截" not in out, out
    assert probe.calls, "未污染脚本未被交给子进程执行"
    assert probe.calls[0][1].replace("\\", "/") == rel
    assert SENTINEL_STDOUT in out, out
    assert (ws / SENTINEL_FILE).exists()


def test_existing_python_guards_still_hold(ws, probe):
    """兼容性：既有 python 收紧（-c / -m pip / 工作区外脚本）不得被本次改动放松。"""
    reg = _registry(ws, mode="auto")

    assert "安全拦截" in reg.execute_tool(
        "run_command", {"command": 'python -c "print(1)"'}, interactive=False)
    assert "安全拦截" in reg.execute_tool(
        "run_command", {"command": "python -m pip install evil"}, interactive=False)

    outside = ws.parent / "outside_evil.py"
    outside.write_text(EVIL_SOURCE, encoding="utf-8")
    out = reg.execute_tool("run_command",
                           {"command": f'python "{outside.as_posix()}"'}, interactive=False)
    assert "安全拦截" in out, out

    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


def test_version_flag_not_blocked_by_new_gates(ws, probe):
    """``python --version`` 不执行脚本，不得被新闸门误伤。"""
    reg = _registry(ws, mode="auto")
    out = reg.execute_tool("run_command", {"command": "python --version"}, interactive=False)
    assert "安全拦截" not in out and "PermissionDenied" not in out, out


# ═══════════ ④ 审批通道：假通道 / 真实 TTY 卡片 ═══════════

def test_fake_channel_receives_request_and_approval_executes(ws, probe):
    """批准 → 执行；审批请求带上了脚本路径（tool_args）。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)
    _write_script(reg, "tools/evil.py")

    out = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                           interactive=False)

    assert len(fake.requests) == 1, fake.requests
    tool_name, level, tool_args = fake.requests[0]
    assert tool_name == "run_command"
    assert level == PermissionLevel.DANGEROUS, "污染脚本审批须按高危级别弹卡"
    assert "tools/evil.py" in str(tool_args.get("command", "")), tool_args
    assert SENTINEL_STDOUT in out, out
    assert (ws / SENTINEL_FILE).exists()


def test_fake_channel_denial_blocks_execution(ws, probe):
    """拒绝 → 不执行（哨兵不存在、探针未被调用）。"""
    fake = _FakeApprovalChannel(approve=False, reason="用户拒绝执行该操作")
    reg = _registry(ws, mode="auto", approval_channel=fake)
    _write_script(reg, "tools/evil.py")

    out = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                           interactive=False)

    assert "PermissionDenied" in out, out
    assert "用户拒绝执行该操作" in out, out
    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


def test_tty_card_approves_and_executes(ws, probe, monkeypatch, capsys):
    """真实 TTY 通道：渲染 Level 5 审批卡片，输入 y 后执行。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    _feed_input(monkeypatch, ["y"])
    reg = _registry(ws, mode="auto")
    _write_script(reg, "tools/evil.py")

    out = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                           interactive=True)

    card = capsys.readouterr().out
    assert "🛡️ [权限审批] 智能私教请求调用外部工具:" in card, card
    assert "tools/evil.py" in card, card
    assert "极其谨慎核对" in card, "污染脚本应按 Level 5 高危卡片渲染"
    assert SENTINEL_STDOUT in out, out
    assert (ws / SENTINEL_FILE).exists()


def test_tty_card_denial_blocks_execution(ws, probe, monkeypatch, capsys):
    """真实 TTY 通道：输入 n 后不执行。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    _feed_input(monkeypatch, ["n"])
    reg = _registry(ws, mode="auto")
    _write_script(reg, "tools/evil.py")

    out = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                           interactive=True)
    capsys.readouterr()

    assert "PermissionDenied" in out and "用户拒绝执行该操作" in out, out
    assert probe.calls == []
    assert not (ws / SENTINEL_FILE).exists()


# ═══════════ ⑤ 授权记忆不落盘 ═══════════

def test_approval_leaves_no_auth_state_file_in_workspace(ws, probe):
    """批准后工作区不得多出任何文件（授权状态只存在于进程内存，不落盘）。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)
    before = _snapshot(ws)

    _write_script(reg, "tools/evil.py")
    out = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                           interactive=False)
    assert SENTINEL_STDOUT in out, out

    new_files = set(_snapshot(ws)) - set(before)
    assert new_files == {"tools/evil.py", SENTINEL_FILE}, (
        f"批准流程写出了额外文件（授权状态疑似落盘）: {sorted(new_files)}")


# ═══════════ ⑥ 阴性对照：摘掉守卫后脚本必须真的被执行 ═══════════

def test_negative_control_without_pollution_rule_script_would_execute(ws, probe, monkeypatch):
    """阴性对照（污染规则）：同一个 registry、同一个脚本，只摘掉守卫。

    先证"未摘除时确实被拦"，再证"摘除后确实执行" —— 排除断言因 python 不可用、
    路径写错、权限层拦截等旁因而假通过。
    """
    reg = _registry(ws, mode="auto")
    _write_script(reg, "tools/evil.py")

    denied = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                              interactive=False)
    assert "PermissionDenied" in denied, f"对照前提不成立（守卫未生效）: {denied!r}"

    monkeypatch.setattr(Sandbox, "is_session_written", lambda self, p: False)

    allowed = reg.execute_tool("run_command", {"command": "python tools/evil.py"},
                               interactive=False)
    assert probe.calls, "摘掉污染规则后脚本仍未被执行，说明断言没指向该守卫"
    assert SENTINEL_STDOUT in allowed, allowed
    assert (ws / SENTINEL_FILE).exists()


def test_negative_control_without_allowlist_script_would_execute(ws, probe, monkeypatch):
    """阴性对照（allowlist）：摘掉白名单判定后，非受控目录的未污染脚本会被执行。"""
    reg = _registry(ws, mode="auto")
    p = ws / "05-考研看板" / "evil.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(EVIL_SOURCE, encoding="utf-8")

    denied = reg.execute_tool("run_command", {"command": "python 05-考研看板/evil.py"},
                              interactive=False)
    assert "仅允许执行受控目录" in denied, f"对照前提不成立: {denied!r}"

    monkeypatch.setattr(tools_impl_mod, "_is_script_exec_allowed", lambda *a, **k: True)

    allowed = reg.execute_tool("run_command", {"command": "python 05-考研看板/evil.py"},
                               interactive=False)
    assert probe.calls, "摘掉 allowlist 后脚本仍未被执行"
    assert SENTINEL_STDOUT in allowed, allowed


# ═══════════ ⑦ 真实子进程端到端（本机 python 可用时才跑） ═══════════

def _real_python_available() -> bool:
    """本机 ``python`` 是否是真解释器（Windows 商店占位 stub 会 exit=49 零输出）。"""
    try:
        r = subprocess.run(["python", "--version"], capture_output=True, text=True,
                           timeout=60)
    except Exception:
        return False
    return r.returncode == 0 and "python" in (r.stdout + r.stderr).lower()


@pytest.mark.skipif(not _real_python_available(),
                    reason="本机 python 是商店占位 stub / 不可用，真实执行路径无法验证")
def test_real_execution_of_unpolluted_script(tmp_path):
    """真实子进程端到端：受控目录里未被写过的脚本可真实执行（无探针）。"""
    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True)
    (ws / "tools" / "real_probe.py").write_text(EVIL_SOURCE, encoding="utf-8")

    reg = ToolRegistry(Sandbox(workspace_root=ws),
                       PermissionManager(mode="auto", workspace_root=ws))
    out = reg.execute_tool("run_command", {"command": "python tools/real_probe.py"},
                           interactive=False)

    assert SENTINEL_STDOUT in out, out
    assert (ws / SENTINEL_FILE).exists()
