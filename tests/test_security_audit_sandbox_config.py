# -*- coding: utf-8 -*-
"""2026-09-30 审查报告 · P1-1（沙箱部分）/ P1-3 / P1-9 回归测试。

缺陷现场
--------
* **P1-1** ``Sandbox.resolve_safe_path`` 对 ``allowed_extra_paths`` 用字符串前缀
  ``str(resolved).lower().startswith(str(extra).lower())`` 判定目录归属（无分隔符
  边界）：授权 ``base/refs`` 后，兄弟目录 ``base/refs-secret/victim.md`` 前缀相同
  即被一并放行（已实测可读写越界）。
* **P1-3** ``ky_config.json`` 位于工作区根且 agent 的 ``write_file`` 可写工作区
  任意位置 —— 可改写 ``mcp_servers`` / ``agent.allowed_extra_paths`` /
  ``permission_mode`` 自扩权限；``mcp_client`` 又对配置里的 ``command`` 直接
  ``Popen`` 无校验，「写脚本进工作区 + 改配置」即任意代码执行。
* **P1-9** ``config_guard.py`` / ``ky_io.py`` 在 ``os.name == "nt"`` 时直接
  ``return``，含明文 api_key 的 ``ky_config.json`` 与 config_backup 沿用 NTFS
  默认权限（同机其他用户可读）；GUI ``settings.py`` 两处写配置未传
  ``sensitive=True``。

测试约定
--------
* 全部用 ``tmp_path`` 造真实文件，不碰真实仓库；夹具词中性化。
* 不伪造全局 ``os.name`` / ``os.chmod``（``tests/test_ci_matrix_regressions.py``
  有元测试钉住）：Windows 分支的 icacls 调用抽成 ``_harden_windows_acl`` 后直接
  对其注入 ``subprocess`` 替身；需要另一种平台语义时只向**被测模块**注入 os 替身
  （与 ``test_cli_security_hardening._PosixOsShim`` 同款）。
* 每条守卫配阳性/阴性对照：证明断言确实指向本次修复的判定逻辑。
"""

import getpass
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import config_guard  # noqa: E402
from tools import ky_io  # noqa: E402
from tools.agent import mcp_client as mcp_mod  # noqa: E402
from tools.agent import sandbox as sandbox_mod  # noqa: E402
from tools.agent.mcp_client import MCPClientManager, MCPProcessClient  # noqa: E402
from tools.agent.permissions import PermissionManager  # noqa: E402
from tools.agent.sandbox import Sandbox, SecurityException  # noqa: E402
from tools.agent.tools_impl import ToolRegistry  # noqa: E402

#: 越界文件内容哨兵：出现在任何"被拒绝"的返回值里即为泄漏。
VICTIM_SENTINEL = "P1_1_VICTIM_SENTINEL_4c1e"
#: ky_config.json 内容哨兵：被改写后不再等于它即为失败。
CONFIG_SENTINEL = '{"api_key": "sk-placeholder", "study_plan": {"school": "示例农业大学"}}\n'


@pytest.fixture(autouse=True)
def _isolate_process_wide_state(monkeypatch):
    """沙箱的授权目录 / 读取汇总 / 写入污染集都是**进程级**全局（见 sandbox.py
    模块级说明）：逐条用例换成全新对象，既不依赖也不污染同进程里的其他测试。"""
    monkeypatch.setattr(sandbox_mod, "_SESSION_AUTHORIZED_READ_DIRS", set())
    monkeypatch.setattr(sandbox_mod, "_SESSION_EXTERNAL_READS", [])
    monkeypatch.setattr(sandbox_mod, "_SESSION_WRITTEN_FILES", set())


@pytest.fixture
def extra_layout(tmp_path):
    """授权目录 ``base/refs`` + 前缀相同的兄弟目录 ``base/refs-secret``。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    refs = tmp_path / "base" / "refs"
    refs.mkdir(parents=True)
    secret = tmp_path / "base" / "refs-secret"
    secret.mkdir()
    (secret / "victim.md").write_text(VICTIM_SENTINEL + "\n", encoding="utf-8")
    return ws, refs, secret


def _registry(ws: Path) -> ToolRegistry:
    """只测沙箱层、不受审批层干扰的注册表（与既有测试同款写法）。"""
    perm = PermissionManager(mode="auto", workspace_root=ws)
    perm.force_allow_all = True
    return ToolRegistry(Sandbox(workspace_root=ws), perm)


# ═══════════ ① P1-1：额外授权目录的分隔符边界 ═══════════

def test_extra_path_prefix_sibling_read_and_write_rejected(extra_layout):
    """核心现场：授权 ``base/refs`` 后，``base/refs-secret/victim.md`` 读写均被拒。"""
    ws, refs, secret = extra_layout
    victim = secret / "victim.md"
    sb = Sandbox(workspace_root=ws, allowed_extra_paths=[str(refs)])

    # 对照前提：两者确实构成「字符串前缀」关系 —— 旧实现正是因此放行
    assert str(victim.resolve()).lower().startswith(str(refs.resolve()).lower()), \
        "对照前提不成立：两目录未构成前缀关系"

    with pytest.raises(SecurityException):
        sb.resolve_safe_path(str(victim), read_only=True)
    with pytest.raises(SecurityException):
        sb.resolve_safe_path(str(victim), allow_create=True, read_only=False)
    with pytest.raises(SecurityException):
        sb.resolve_safe_path(str(secret / "new.md"), allow_create=True, read_only=False)

    assert victim.read_text(encoding="utf-8") == VICTIM_SENTINEL + "\n", "越界文件被改动"


def test_extra_path_authorized_dir_itself_and_children_allowed(extra_layout):
    """阳性：授权目录**自身**、目录内文件的读与写照常放行。"""
    ws, refs, secret = extra_layout
    ok = refs / "ok.md"
    ok.write_text("P1_1_OK_7a2b\n", encoding="utf-8")
    sb = Sandbox(workspace_root=ws, allowed_extra_paths=[str(refs)])

    assert sb.resolve_safe_path(str(refs), read_only=True) == refs.resolve()
    assert sb.resolve_safe_path(str(ok), read_only=True) == ok.resolve()
    assert sb.resolve_safe_path(str(refs / "new.md"), allow_create=True,
                                read_only=False) == (refs / "new.md").resolve()


def test_positive_control_authorizing_sibling_dir_unblocks(extra_layout):
    """阳性对照：把 ``refs-secret`` 本身加入授权后同一文件即可读 —— 证明前面的
    拒绝确实来自目录归属判定，而非路径写错 / 其他前置拒绝等旁因。"""
    ws, refs, secret = extra_layout
    victim = secret / "victim.md"
    sb = Sandbox(workspace_root=ws, allowed_extra_paths=[str(secret)])

    assert sb.resolve_safe_path(str(victim), read_only=True) == victim.resolve()


@pytest.mark.skipif(os.name != "nt", reason="大小写不敏感是 Windows 文件系统语义")
def test_extra_path_case_variants_on_windows(extra_layout):
    """Windows：大小写变体命中同一目录（放行授权目录内的变体、仍拒越界兄弟）。"""
    ws, refs, secret = extra_layout
    sb = Sandbox(workspace_root=ws, allowed_extra_paths=[str(refs)])

    variant = Path(str(refs).upper()) / "OK.MD"
    assert sb.resolve_safe_path(str(variant), read_only=True) == (refs / "ok.md").resolve()
    with pytest.raises(SecurityException):
        sb.resolve_safe_path(str(Path(str(secret).upper()) / "VICTIM.MD"), read_only=True)


# ═══════════ ② P1-3a：ky_config.json 写保护 ═══════════

def test_agent_write_to_ky_config_is_blocked(tmp_path):
    """只读放行；非只读（绝对/相对/allow_create 三种形态）一律 SecurityException。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = ws / "ky_config.json"
    cfg.write_text(CONFIG_SENTINEL, encoding="utf-8")
    sb = Sandbox(workspace_root=ws)

    assert sb.resolve_safe_path(str(cfg), read_only=True) == cfg.resolve()

    for kwargs in ({}, {"allow_create": True}):
        with pytest.raises(SecurityException) as ei:
            sb.resolve_safe_path(str(cfg), read_only=False, **kwargs)
        assert "ky_config.json" in str(ei.value), ei.value
        assert "ky config" in str(ei.value), ei.value
    with pytest.raises(SecurityException):
        sb.resolve_safe_path("ky_config.json", read_only=False)

    assert cfg.read_text(encoding="utf-8") == CONFIG_SENTINEL, "配置被改写了"


@pytest.mark.skipif(os.name != "nt", reason="大小写不敏感是 Windows 文件系统语义")
def test_ky_config_case_variant_blocked_on_windows(tmp_path):
    """Windows：``KY_CONFIG.JSON`` 指向同一文件，同样拦截。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ky_config.json").write_text(CONFIG_SENTINEL, encoding="utf-8")
    sb = Sandbox(workspace_root=ws)

    with pytest.raises(SecurityException):
        sb.resolve_safe_path(str(ws / "KY_CONFIG.JSON"), read_only=False)


def test_ky_config_bak_and_nested_names_unaffected(tmp_path):
    """边界防误伤：``ky_config.json.bak`` 与子目录里的同名文件不是运行配置本体。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    bak = ws / "ky_config.json.bak"
    bak.write_text("{}", encoding="utf-8")
    sb = Sandbox(workspace_root=ws)

    assert sb.resolve_safe_path(str(bak), read_only=False) == bak.resolve()

    nested = ws / "05-考研看板" / "ky_config.json"
    assert sb.resolve_safe_path(str(nested), allow_create=True,
                                read_only=False) == nested.resolve()


def test_write_file_tool_cannot_overwrite_ky_config(tmp_path):
    """端到端：agent 的 write_file 写 ky_config.json 被拦，read_file 仍可用。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = ws / "ky_config.json"
    cfg.write_text(CONFIG_SENTINEL, encoding="utf-8")
    reg = _registry(ws)

    out = reg.execute_tool(
        "write_file",
        {"path": "ky_config.json", "content": "{}", "overwrite": True},
        interactive=False)

    assert "SecurityError" in out, out
    assert "ky_config.json" in out, out
    assert cfg.read_text(encoding="utf-8") == CONFIG_SENTINEL, "配置被 agent 改写"

    read_out = reg.execute_tool("read_file", {"path": "ky_config.json"},
                                interactive=False)
    assert "sk-placeholder" in read_out, read_out


# ═══════════ ③ P1-3b：MCP command 指向工作区内的拒绝 ═══════════

def test_mcp_absolute_command_inside_workspace_rejected(tmp_path):
    """配置里 command 指向工作区内脚本 → 启动被拒且原因可辨识（不进 clients）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    script = ws / "tools" / "evil_server.py"
    script.parent.mkdir()
    script.write_text("print('x')\n", encoding="utf-8")

    mgr = MCPClientManager(workspace_root=ws)
    mgr.load_from_config({"sample_local": {"command": str(script), "args": []}},
                         start_timeout=1)

    assert mgr.clients == {}, "工作区内的命令不得被启动"
    reason = mgr.failed.get("sample_local", "")
    assert "工作区内" in reason and "mcp_servers" in reason, reason
    health = mgr.mcp_health()
    assert health["dead"] == 1, health


def test_mcp_relative_command_inside_workspace_rejected(tmp_path):
    """相对路径 command（按子进程 cwd=工作区 解析）同样被拒。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "evil_server.py").write_text("print('x')\n", encoding="utf-8")

    mgr = MCPClientManager(workspace_root=ws)
    mgr.load_from_config({"sample_rel": {"command": "tools/../evil_server.py", "args": []}},
                         start_timeout=1)

    assert mgr.clients == {}
    assert "工作区内" in mgr.failed.get("sample_rel", ""), mgr.failed


def test_mcp_absolute_system_command_outside_workspace_not_blocked(tmp_path):
    """工作区外的系统级绝对路径命令不受本校验影响（放行，交给 Popen 尝试）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    client = MCPProcessClient(name="s", command=sys.executable, args=[],
                              workspace_root=ws)

    assert client._workspace_local_command_error() == ""


class _FakeProc:
    """假子进程：stdin/stdout 立刻 EOF，避免测试真实启动进程。"""

    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()

    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


class _FakeSubprocessModule:
    """只替换 ``mcp_client`` 模块内 subprocess 引用的替身（不动全局模块）。"""

    PIPE = subprocess.PIPE
    DEVNULL = subprocess.DEVNULL

    def __init__(self):
        self.calls = []

    def Popen(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return _FakeProc()


def test_mcp_bare_command_name_is_not_blocked_by_workspace_check(monkeypatch, tmp_path):
    """裸命令名（如 python）维持现状放行（mock Popen 验证确实走到启动）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    fake_sub = _FakeSubprocessModule()
    monkeypatch.setattr(mcp_mod, "subprocess", fake_sub)

    client = MCPProcessClient(name="s", command="python", args=[], cwd=ws,
                              workspace_root=ws)
    ok = client.start(timeout=0)

    assert fake_sub.calls, "裸命令名不应被工作区校验拦截（Popen 应被调用）"
    assert fake_sub.calls[0][0] == ["python"]
    assert ok is False, "假进程无有效握手响应，start 应为 False"
    assert "工作区内" not in (client.last_error or ""), client.last_error
    assert "无响应" in client.last_error or "超时" in client.last_error, client.last_error
    client.stop()


def test_mcp_workspace_check_negative_control_legacy_prefix_would_allow(tmp_path):
    """阴性对照（判定语义）：旧「字符串前缀」判定对本场景返回 True，新判定 False。

    证明拒绝不是来自路径不存在 / 盘符等旁因，而是精确的目录归属判定本身。
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    script = ws / "evil.py"
    script.write_text("print('x')\n", encoding="utf-8")
    client = MCPProcessClient(name="s", command=str(script), args=[],
                              workspace_root=ws)

    assert str(script.resolve()).lower().startswith(str(ws).lower())   # 旧逻辑：放行
    assert client._workspace_local_command_error() != ""               # 新逻辑：拒绝


def test_mcp_drive_relative_command_is_rejected(tmp_path):
    """[审计 2026-09-30 P1-3 补] Windows 驱动器相对路径（``C:evil.exe``）不含
    路径分隔符，解析基准是子进程「当前盘的当前目录」——父进程无法精确复现，
    无法可靠归属判定的一律 fail-closed 拒绝。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    client = MCPProcessClient(name="s", command="C:evil.exe", args=[],
                              workspace_root=ws)

    err = client._workspace_local_command_error()
    assert err != "" and "驱动器相对路径" in err, err


def test_mcp_drive_relative_negative_control_bare_name_still_allowed(tmp_path):
    """阴性对照：普通裸命令名（不含分隔符与驱动器前缀）不触发该分支。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    client = MCPProcessClient(name="s", command="npx", args=[],
                              workspace_root=ws)

    assert client._workspace_local_command_error() == ""


# ═══════════ ④ P1-9：权限收紧（Windows ACL / POSIX chmod / 调用点） ═══════════

class _RecordingSubprocess:
    """记录 ``subprocess.run`` 调用并可注入返回值 / 异常的替身。"""

    def __init__(self, returncode=0, exc=None):
        self.calls = []
        self._returncode = returncode
        self._exc = exc

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self._exc is not None:
            raise self._exc
        return subprocess.CompletedProcess(args, self._returncode)


def _expected_user() -> str:
    """与实现同款回落的当前用户名。"""
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def test_harden_windows_acl_builds_icacls_invocation(monkeypatch):
    """Windows 分支：参数形态 = icacls + 移除继承 + 仅授当前用户（文件/目录）。"""
    fake = _RecordingSubprocess()
    monkeypatch.setattr(ky_io, "subprocess", fake)
    user = _expected_user()

    ky_io._harden_windows_acl(Path("X:/secret.json"), is_dir=False)
    args, kwargs = fake.calls[0]
    assert args == ["icacls", str(Path("X:/secret.json")), "/inheritance:r",
                    "/grant:r", f"{user}:F"], args
    assert kwargs.get("capture_output") is True, kwargs
    assert kwargs.get("timeout") == 10, kwargs
    assert kwargs.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0), kwargs

    fake.calls.clear()
    ky_io._harden_windows_acl(Path("X:/secret_dir"), is_dir=True)
    args, _ = fake.calls[0]
    assert args[-1] == f"{user}:(OI)(CI)F", args


def test_harden_windows_acl_swallows_exception_and_nonzero(monkeypatch):
    """失败静默降级：异常与非零返回码都不得抛出（不阻塞主流程）。"""
    fake = _RecordingSubprocess(exc=OSError("icacls 不可用"))
    monkeypatch.setattr(ky_io, "subprocess", fake)
    ky_io._harden_windows_acl(Path("X:/secret.json"))            # 不得抛出

    fake2 = _RecordingSubprocess(exc=subprocess.TimeoutExpired("icacls", 10))
    monkeypatch.setattr(ky_io, "subprocess", fake2)
    ky_io._harden_windows_acl(Path("X:/secret.json"))            # 超时不得抛出

    fake3 = _RecordingSubprocess(returncode=5)
    monkeypatch.setattr(ky_io, "subprocess", fake3)
    ky_io._harden_windows_acl(Path("X:/secret.json"))            # 非零不得抛出
    assert fake3.calls, "非零返回码路径也应确实调用了 icacls"


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位在 Windows 上不适用")
def test_harden_file_permissions_posix_real_chmod(tmp_path):
    """POSIX 分支：真实 chmod —— 文件 0600、目录 0700。"""
    f = tmp_path / "secret.json"
    f.write_text("{}", encoding="utf-8")
    ky_io.harden_file_permissions(f)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600

    d = tmp_path / "secret_dir"
    d.mkdir()
    ky_io.harden_file_permissions(d, is_dir=True)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


class _NtOsShim:
    """只向 ``ky_io`` 暴露 Windows 语义的 ``os`` 替身（其余成员透传真实 ``os``）。

    不能直接改全局 ``os.name``（``tests/test_ci_matrix_regressions.py`` 有元测试
    钉住），故只注入被测模块 —— 与 ``_PosixOsShim`` 同款做法。
    """

    name = "nt"

    def __getattr__(self, item):
        return getattr(os, item)


def test_align_to_umask_nt_sensitive_calls_harden(monkeypatch):
    """ky_io 的 Windows 分支不再直接 return：sensitive=True 时必须走 ACL 收紧。"""
    calls = []
    monkeypatch.setattr(ky_io, "harden_file_permissions",
                        lambda path, is_dir=False: calls.append((str(path), is_dir)))
    monkeypatch.setattr(ky_io, "os", _NtOsShim())

    ky_io._align_to_umask(Path("cfg.json"), sensitive=True)
    assert calls == [("cfg.json", False)], "Windows 下敏感文件必须走 icacls 收紧"

    calls.clear()
    ky_io._align_to_umask(Path("cfg.json"))          # 非敏感：Windows 维持现状
    assert calls == [], "非敏感文件不应被收紧（避免每次写盘都起 icacls）"


def test_config_guard_tighten_snapshot_perms_calls_harden(monkeypatch, tmp_path):
    """config_guard 的 Windows 分支不再直接 return：文件与其父目录都收紧。"""
    calls = []
    monkeypatch.setattr(config_guard, "harden_file_permissions",
                        lambda path, is_dir=False: calls.append((str(path), is_dir)))
    snap = tmp_path / "ky_config.auto.1.json"
    snap.write_text("{}", encoding="utf-8")

    config_guard._tighten_snapshot_perms(snap)
    assert (str(tmp_path), True) in calls, "快照父目录必须收紧（明文密钥所在目录）"
    assert (str(snap), False) in calls, "快照文件本身必须收紧"

    calls.clear()
    d = tmp_path / "backup_dir"
    d.mkdir()
    config_guard._tighten_snapshot_perms(d)
    assert calls == [(str(d), True)], calls


def test_gui_save_settings_marks_config_write_sensitive(monkeypatch, tmp_path):
    """GUI 保存配置必须传 sensitive=True（此前漏传 → 权限收紧被跳过）。"""
    from tools.gui.services import settings as gui_settings

    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = ws / "ky_config.json"
    cfg.write_text(json.dumps({"api_key": "sk-old",
                               "study_plan": {"school": "示例农业大学"}}),
                   encoding="utf-8")

    recorded = []
    monkeypatch.setattr(
        gui_settings, "atomic_write_text",
        lambda path, text, **kw: recorded.append((Path(path), kw)) or Path(path))

    gui_settings.save_settings(
        str(cfg), api_key="sk-new", base_url="https://api.example.com/v1",
        model="sample-model", school="示例农业大学", major="030500 示例专业",
        exam_date="2026-12-19", style="严格把关·保姆提分型 (Strict & Disciplined)")

    assert recorded, "未发生写入"
    path, kw = recorded[0]
    assert path == cfg
    assert kw.get("sensitive") is True, "GUI 保存配置未传 sensitive=True"


def test_gui_save_onboarding_config_marks_config_write_sensitive(monkeypatch, tmp_path):
    """首启向导保存配置同样必须传 sensitive=True（联动写盘全部打桩隔离）。"""
    from tools.gui.services import settings as gui_settings

    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = ws / "ky_config.json"

    recorded = []
    monkeypatch.setattr(
        gui_settings, "atomic_write_text",
        lambda path, text, **kw: recorded.append((Path(path), kw)) or Path(path))
    monkeypatch.setattr(gui_settings, "update_agents_md", lambda *a, **k: None)
    monkeypatch.setattr("tools.study_planner.update_subject_agents",
                        lambda *a, **k: None)
    monkeypatch.setattr("tools.syllabus_manager.apply_syllabus_selection",
                        lambda *a, **k: None)

    class _DummyWatcher:
        def list_watched(self):
            return []

        def remove_watch(self, code):
            pass

        def add_watch(self, school):
            pass

    monkeypatch.setattr("tools.intelligence.watcher.AdmissionWatcher", _DummyWatcher)

    gui_settings.save_onboarding_config(
        str(cfg), {"api_key": "sk-new", "study_plan": {"school": "示例农业大学"}},
        workspace_root=ws)

    assert recorded, "未发生写入"
    path, kw = recorded[0]
    assert path == cfg
    assert kw.get("sensitive") is True, "首启向导保存配置未传 sensitive=True"
