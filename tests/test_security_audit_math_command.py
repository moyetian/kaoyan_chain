# -*- coding: utf-8 -*-
"""2026-09-30 安全审计修复回归 —— math_verifier 安全解析（P0-1）与
run_command 闸门收口（P0-2 / P1-4 / P1-5）。

背景（审计报告 2026-09-30）：
* **P0-1**：``sp.sympify`` 内部用 eval 执行输入字符串 —— ``verify_math`` 曾是
  一条零审批 RCE 通道（Level 0 标注 + auto/safe 均无条件放行）。修复：白名单
  ``parse_expr`` 解析（``_safe_sympify``）+ 工具提级到 ``SHELL_EXEC`` +
  权限层 safe 判定前移。
* **P0-2**：``pytest`` 在白名单内却不进 B2a 两道闸门 ——
  ``write_file tests/test_x.py`` → ``pytest tests/test_x.py`` 可零审批执行。
* **P1-4**：``git -c alias.x=!cmd`` / ``core.fsmonitor`` 可执行任意程序。
* **P1-5**：``timeout`` 由模型参数控制且无上限；输出全量进内存后才截断。

测试风格与 ``tests/test_b2a_script_exec_gate.py`` 一致：拦截断言 = 文案可辨识
+ 执行探针零调用（双证据）；每条守卫配阴性对照。
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
from tools.agent.tools_impl import ToolRegistry, _is_inside_sandbox  # noqa: E402
from tools.skills import math_verifier as math_verifier_mod  # noqa: E402


# ───────────────────────── 公共工具 ─────────────────────────

@pytest.fixture(autouse=True)
def _isolate_process_wide_pollution(monkeypatch):
    """污染集合是进程级全局：逐条用例换成全新集合，互不污染。"""
    monkeypatch.setattr(sandbox_mod, "_SESSION_WRITTEN_FILES", set())


@pytest.fixture
def ws(tmp_path):
    """独立工作区（含受控目录 tools/ 与 tests/，与真实仓库隔离）。"""
    root = tmp_path / "ws"
    (root / "tools").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    return root


def _registry(ws, *, mode="auto", approval_channel=None):
    perm = PermissionManager(mode=mode, workspace_root=ws,
                             approval_channel=approval_channel)
    return ToolRegistry(Sandbox(workspace_root=ws), perm)


class _RecordingRun:
    """记录型 subprocess.run 替身：不真实执行，仅记录 argv 与 kwargs。"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.kwargs = []
        self._rc = returncode
        self._stdout = stdout
        self._stderr = stderr

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs.append(dict(kwargs))
        return subprocess.CompletedProcess(argv, self._rc,
                                           stdout=self._stdout, stderr=self._stderr)


class _SubprocessProxy:
    """只覆盖 run 的 subprocess 代理（照抄 b2a 模式，避免污染全局）。"""

    def __init__(self, real, run):
        self._real = real
        self.run = run

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def run_probe(monkeypatch):
    """安装记录型执行探针（仅替换 tools_impl 命名空间里的 subprocess）。"""
    p = _RecordingRun()
    monkeypatch.setattr(tools_impl_mod, "subprocess", _SubprocessProxy(subprocess, p))
    return p


# ══════════════════ P0-1 · math_verifier 安全解析 ══════════════════

RCE_PAYLOADS = [
    "__import__('os').popen('echo x').read()",
    "().__class__.__bases__[0].__subclasses__()",
    'eval("1+1")',
    'open("/etc/passwd").read()',
    'getattr(1, "real")',
    "globals()",
]


@pytest.mark.parametrize("payload", RCE_PAYLOADS)
def test_safe_sympify_rejects_rce_payloads(payload):
    """阴性对照：全部代码执行/内省载荷必须被拒绝。"""
    with pytest.raises(Exception):
        math_verifier_mod._safe_sympify(payload)


@pytest.mark.parametrize("expr", [
    "x**2 + sin(x)", "3.5", "x*y", "pi", "sqrt(2)", "2**n",
    "sin(cos(x))", "exp(-x)*x", "log(x)/x",
])
def test_safe_sympify_accepts_math_expressions(expr):
    """阳性对照：正常数学表达式照常解析。"""
    assert math_verifier_mod._safe_sympify(expr) is not None


def test_run_math_query_rce_payload_does_not_execute(tmp_path):
    """端到端：攻击载荷被拒且系统命令确实未执行（哨兵不存在）。"""
    sentinel = tmp_path / "rce_sentinel.txt"
    payload = f"diff __import__('os').system('echo pwned > {sentinel.as_posix()}')+1"
    out = math_verifier_mod.run_math_query(payload)
    assert "已拒绝解析" in out, out
    assert not sentinel.exists(), "RCE 载荷被执行了！"


def test_negative_control_raw_sympify_would_execute(tmp_path, monkeypatch):
    """阴性对照：把安全解析换回原生 sympify，同一载荷必须真的执行
    —— 证明哨兵机制有效、且上一条断言确实由本次修复支撑。"""
    import sympy as sp
    monkeypatch.setattr(math_verifier_mod, "_safe_sympify", sp.sympify)
    sentinel = tmp_path / "negctl_sentinel.txt"
    payload = f"diff __import__('os').system('echo pwned > {sentinel.as_posix()}')+1"
    math_verifier_mod.run_math_query(payload)
    assert sentinel.exists(), "阴性对照失效：原生 sympify 未执行载荷"


def test_verify_math_tool_level_is_shell_exec():
    """verify_math 必须提级到 SHELL_EXEC（safe 模式下不再被 Level 0 短路放行）。"""
    reg = ToolRegistry(Sandbox(workspace_root=REPO_ROOT),
                       PermissionManager(mode="auto", workspace_root=REPO_ROOT))
    tools = getattr(reg, "tools", None) or getattr(reg, "_tools", None)
    assert tools is not None, "无法访问工具注册表"
    entry = tools["verify_math"]
    level = entry["level"] if isinstance(entry, dict) else getattr(entry, "level", None)
    if callable(level):
        level = level({})
    assert level == PermissionLevel.SHELL_EXEC, f"verify_math level={level}"


def test_safe_mode_denies_verify_math():
    """safe 模式下 verify_math（Level 3）必须被拒绝。"""
    pm = PermissionManager(mode="safe", workspace_root=REPO_ROOT)
    ok, reason = pm.check_permission("verify_math", PermissionLevel.SHELL_EXEC,
                                     {"expression": "diff x^3"}, interactive=True)
    assert ok is False
    assert "严格安全模式" in reason


def test_safe_mode_still_allows_readonly():
    """回归：safe 模式下 Level 0 只读操作仍零摩擦放行（既有契约）。"""
    pm = PermissionManager(mode="safe", workspace_root=REPO_ROOT)
    assert pm.check_permission("read_file", PermissionLevel.READ_ONLY,
                               {"path": "AGENTS.md"}, interactive=False) == (
        True, "只读安全操作，自动放行")


def test_safe_mode_ignores_session_trust():
    """P1-2：会话信任集不得绕过 safe 模式（顺序修复后 safe 判定优先）。"""
    pm = PermissionManager(mode="safe", workspace_root=REPO_ROOT)
    pm.session_allowed_tools.add("write_file")
    ok, reason = pm.check_permission("write_file", PermissionLevel.SAFE_EDIT,
                                     {"path": "x.md"}, interactive=False)
    assert ok is False
    assert "严格安全模式" in reason


def test_external_read_safe_mode_ignores_session_trust():
    """P1-2：外部读信任集同样不得绕过 safe 模式。"""
    pm = PermissionManager(mode="safe", workspace_root=REPO_ROOT)
    pm.session_allowed_tools.add("read_external_file")
    ok, reason = pm.check_external_read("D:/somewhere/notes.md",
                                        {"path": "D:/somewhere/notes.md"},
                                        interactive=True)
    assert ok is False
    assert "严格安全模式" in reason


# ══════════════════ P0-2 · pytest 纳入脚本闸门 ══════════════════

def _plant_polluted(reg, ws, rel_path, source="print('x')\n"):
    """直接落盘并登记为「本会话写入」（绕开写工具自身的门禁）。"""
    p = ws / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    reg.sandbox.register_written_file(p)
    return p


def test_pytest_gate_blocks_session_written_script(ws, run_probe):
    """核心缺陷回归：本会话写入的测试文件，pytest 执行前必须过闸门
    （headless 无审批 → 拒绝，且探针零调用）。"""
    reg = _registry(ws)
    _plant_polluted(reg, ws, "tests/test_x.py")
    out = reg.execute_tool("run_command", {"command": "pytest tests/test_x.py"},
                           interactive=False)
    assert "PermissionDenied" in out, out
    assert run_probe.calls == [], f"闸门被绕过，命令被执行: {run_probe.calls}"


def test_pytest_gate_negative_control(ws, run_probe, monkeypatch):
    """阴性对照：把会话写入闸门临时放开，同一命令必须走到 subprocess
    —— 证明拦截确实由本闸门支撑。"""
    reg = _registry(ws)
    _plant_polluted(reg, ws, "tests/test_x.py")
    monkeypatch.setattr(reg.permissions, "check_session_script_exec",
                        lambda *a, **k: (True, "阴性对照放行"))
    out = reg.execute_tool("run_command", {"command": "pytest tests/test_x.py"},
                           interactive=False)
    assert "PermissionDenied" not in out, out
    assert len(run_probe.calls) == 1, "放行后应执行一次"


def test_pytest_node_id_syntax_blocked(ws, run_probe):
    """``file.py::test_name`` 的 node id 写法必须同样过闸门。"""
    reg = _registry(ws)
    _plant_polluted(reg, ws, "tests/test_x.py")
    out = reg.execute_tool("run_command",
                           {"command": "pytest tests/test_x.py::test_evil"},
                           interactive=False)
    assert "PermissionDenied" in out, out
    assert run_probe.calls == []


def test_pytest_dir_with_session_written_blocked(ws, run_probe):
    """目录参数：目录内存在本会话写入的 .py 时同样触发闸门。"""
    reg = _registry(ws)
    _plant_polluted(reg, ws, "tests/test_x.py")
    out = reg.execute_tool("run_command", {"command": "pytest tests"}, interactive=False)
    assert "PermissionDenied" in out, out
    assert run_probe.calls == []


def test_bare_pytest_blocked_when_session_written(ws, run_probe):
    """裸 pytest（收集整棵工作区）：会话写入集合含 .py 时触发闸门。"""
    reg = _registry(ws)
    _plant_polluted(reg, ws, "tests/test_x.py")
    out = reg.execute_tool("run_command", {"command": "pytest"}, interactive=False)
    assert "PermissionDenied" in out, out
    assert run_probe.calls == []


def test_pytest_clean_script_passes(ws, run_probe):
    """阳性对照：未写入的受控目录脚本正常放行（走到 subprocess）。"""
    reg = _registry(ws)
    (ws / "tests" / "test_ok.py").write_text("print('ok')\n", encoding="utf-8")
    out = reg.execute_tool("run_command", {"command": "pytest tests/test_ok.py"},
                           interactive=False)
    assert "PermissionDenied" not in out and "安全拦截" not in out, out
    assert len(run_probe.calls) == 1


def test_pytest_info_only_passes(ws, run_probe):
    """``pytest --version`` 不执行任何测试代码，不受闸门影响。"""
    reg = _registry(ws)
    _plant_polluted(reg, ws, "tests/test_x.py")
    out = reg.execute_tool("run_command", {"command": "pytest --version"},
                           interactive=False)
    assert "PermissionDenied" not in out, out
    assert len(run_probe.calls) == 1


@pytest.mark.parametrize("cmd,marker", [
    ("pytest -p evil_plugin tests", "仅允许 `-p no:...`"),
    ("pytest -pevil_plugin tests", "仅允许 `-p no:...`"),
    ("pytest --pyargs tests.test_x", "--pyargs"),
    ("pytest -c evil.ini tests", "禁止指定外部配置文件"),
    ("pytest --override-ini=addopts=-p evil tests", "禁止 --override-ini"),
])
def test_pytest_dangerous_options_blocked(ws, run_probe, cmd, marker):
    """危险选项（插件加载 / 外部配置 / 模块名执行）直接拒绝。"""
    reg = _registry(ws)
    out = reg.execute_tool("run_command", {"command": cmd}, interactive=False)
    assert marker in out, out
    assert run_probe.calls == []


def test_pytest_no_prefix_plugin_option_allowed(ws, run_probe):
    """``-p no:cacheprovider``（禁用插件）是合法用法，不被拦截。"""
    reg = _registry(ws)
    (ws / "tests" / "test_ok.py").write_text("print('ok')\n", encoding="utf-8")
    out = reg.execute_tool(
        "run_command",
        {"command": "pytest -p no:cacheprovider tests/test_ok.py"},
        interactive=False)
    assert "PermissionDenied" not in out and "安全拦截" not in out, out
    assert len(run_probe.calls) == 1


def test_python_gate_still_works(ws, run_probe):
    """回归：python 分支的既有闸门行为不变（写入脚本仍被拦）。"""
    reg = _registry(ws)
    _plant_polluted(reg, ws, "tests/test_x.py")
    out = reg.execute_tool("run_command", {"command": "python tests/test_x.py"},
                           interactive=False)
    assert "PermissionDenied" in out, out
    assert run_probe.calls == []


# ══════════════════ P1-1 · 授权目录边界（tools_impl 侧） ══════════════════

def test_is_inside_sandbox_prefix_boundary(tmp_path):
    """授权 ``base/refs`` 后，``base/refs-secret`` 必须视为越界。"""
    base = tmp_path / "base"
    refs = base / "refs"
    secret = base / "refs-secret"
    refs.mkdir(parents=True)
    secret.mkdir(parents=True)
    sb = Sandbox(workspace_root=tmp_path / "ws", allowed_extra_paths=[refs])
    (tmp_path / "ws").mkdir(exist_ok=True)
    assert _is_inside_sandbox(sb, refs / "ok.md") is True
    assert _is_inside_sandbox(sb, refs) is True
    assert _is_inside_sandbox(sb, secret / "victim.md") is False


# ══════════════════ P1-4 · git 配置注入收口 ══════════════════

@pytest.mark.parametrize("cmd,marker", [
    ("git -c alias.kyprobe=!pwd kyprobe", "以 `!` 开头"),
    ("git -c core.fsmonitor=hook.sh status", "可触发外部程序执行"),
    ("git -c core.sshCommand=evil.exe status", "可触发外部程序执行"),
    # 注：路径形态的选项值会被更早的「位置参数沙箱校验」先拦（防御分层），
    # 这里用裸名值验证专项收口本身生效。
    ("git --upload-pack=evil fetch", "--upload-pack"),
    ("git --template=tpl init", "--template"),
])
def test_git_config_injection_blocked(ws, run_probe, cmd, marker):
    """git 配置注入（alias 执行 shell / fsmonitor / sshCommand 等）必须被拦。"""
    reg = _registry(ws)
    out = reg.execute_tool("run_command", {"command": cmd}, interactive=False)
    assert "安全拦截" in out and marker in out, out
    assert run_probe.calls == []


def test_git_benign_config_allowed(ws, run_probe):
    """阳性对照：无害配置键（user.name）不被拦截。"""
    reg = _registry(ws)
    out = reg.execute_tool("run_command",
                           {"command": "git -c user.name=tester status"},
                           interactive=False)
    assert "安全拦截" not in out, out
    assert len(run_probe.calls) == 1


def test_git_reset_hard_still_blocked(ws, run_probe):
    """回归：既有破坏性子命令黑名单不受影响（更早的 check_command_safety 层先拦）。"""
    reg = _registry(ws)
    out = reg.execute_tool("run_command", {"command": "git reset --hard"}, interactive=False)
    assert ("安全拦截" in out or "沙箱拦截" in out), out
    assert run_probe.calls == []


# ══════════════════ P1-5 · timeout 上限与输出体积上限 ══════════════════

def test_timeout_clamped_to_bounds(ws, run_probe):
    """timeout 参数被 clamp 到 [1, 300]：模型给多大都不得突破。"""
    reg = _registry(ws)
    reg.execute_tool("run_command", {"command": "ls", "timeout": 999999},
                     interactive=False)
    assert run_probe.kwargs[0]["timeout"] == 300
    run_probe.calls.clear()
    run_probe.kwargs.clear()
    reg.execute_tool("run_command", {"command": "ls", "timeout": 0},
                     interactive=False)
    assert run_probe.kwargs[0]["timeout"] == 1


def test_output_capped_at_1mb(ws, monkeypatch):
    """真实执行：2MB 输出的脚本只读回 1MB，返回体带截断标记（内存有界）。"""
    script = ws / "tools" / "big_out.py"
    script.write_text(
        "import sys\nsys.stdout.write('A' * (2 * 1024 * 1024))\n",
        encoding="utf-8")
    reg = _registry(ws)
    real_run = subprocess.run
    calls = []

    def fake_run(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if os.path.basename(argv[0]).lower() in ("python", "python3"):
            argv[0] = sys.executable
        return real_run(argv, **kwargs)

    monkeypatch.setattr(tools_impl_mod, "subprocess", _SubprocessProxy(subprocess, fake_run))
    out = reg.execute_tool("run_command", {"command": "python tools/big_out.py"},
                           interactive=False)
    assert len(calls) == 1, out
    assert "输出超过 1MB 上限" in out, out
