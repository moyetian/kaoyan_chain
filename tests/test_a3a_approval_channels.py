# -*- coding: utf-8 -*-
"""A3a 回归 · 审批四通道协议（G3 通道抽象 + G13「GUI 写操作全拒」）

缺陷现场（G13，实测）
--------------------
``tools/gui/workers/agent_worker.py`` 给 ``AgentRunner`` 传
``permission_mode="acceptEdits"``（Claude Code 系命名），而
``PermissionManager`` 的模式解析是::

    self.mode = mode.lower() if mode in ("ask", "auto", "safe", "plan") else "ask"

判断用的是**原始大小写**，``"acceptEdits"`` 不在 4 档里 → 静默回退 ``ask``；
GUI 又固定 ``interactive=False`` → ``check_permission`` 的非交互分支把 Level 1+
全部拒绝。**桌面端因此完全写不了文件**（连 ``01-数学/_状态/`` 都写不进），
且没有任何报错，现场只看到工具返回 ``PermissionDenied``。

同一条解析还有第二个方向更危险的缺陷：``--permission=SAFE`` / ``"Auto"`` 这类
**大小写变体**同样被判非法 → 静默降级成 ``ask``，用户以为只读，实际拿到了
「可批准写操作」的权限。

修复（A3a）
-----------
1. 模式解析收敛到单一实现处 ``permissions.normalize_mode``：先 strip+lower，
   再查别名表（``acceptEdits`` → ``auto``），最后校验 4 档，**未知模式抛
   ``ValueError`` 而不是静默回退**；
2. 「怎么问」从权限策略里抽出成 ``agent.approval`` 的审批通道：交互终端走
   ``TtyApproval``（卡片渲染原样迁入），GUI / 网关 / 管道走 ``HeadlessApproval``
   （**绝不阻塞等输入**），策略由 ``agent.headless_write_policy`` 决定，
   **默认 ``deny_all``**，即接入通道前的行为逐字节不变。

本文件锁定：模式解析、四模式 × 非 TTY 的逐字节文案、三档 headless 策略、
G13 回归（GUI 实际传参下写操作必须放行）、headless 通道不得触碰 ``input()``、
plan 模式的计划审计卡片。
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent import approval as approval_mod  # noqa: E402
from agent.permissions import (  # noqa: E402
    MODE_ALIASES,
    VALID_MODES,
    PermissionManager,
    normalize_mode,
)

GUI_WORKER = ROOT / "tools" / "gui" / "workers" / "agent_worker.py"


class _FakeStdin:
    """最小 stdin 替身：只回答 ``isatty``。"""

    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def headless_stdin(monkeypatch):
    """把 stdin 伪装成管道（非 TTY）。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    return _FakeStdin(False)


@pytest.fixture
def tty_stdin(monkeypatch):
    """把 stdin 伪装成真实终端。"""
    fake = _FakeStdin(True)
    monkeypatch.setattr(sys, "stdin", fake)
    return fake


def _feed_input(monkeypatch, answers):
    """按序喂给 ``input()``；用尽后抛断言错误（防实现多问一次）。"""
    it = iter(answers)

    def _fake_input(*_a, **_k):
        try:
            return next(it)
        except StopIteration:  # pragma: no cover - 只有实现回归才会走到
            raise AssertionError("实现调用了比预期更多的 input()")

    monkeypatch.setattr("builtins.input", _fake_input)


# ────────────────────────── 1. 模式解析（单一实现处） ──────────────────────────


@pytest.mark.parametrize("raw,want", [
    ("ask", "ask"),
    ("auto", "auto"),
    ("safe", "safe"),
    ("plan", "plan"),
    ("SAFE", "safe"),
    ("Auto", "auto"),
    ("  plan  ", "plan"),
    ("acceptEdits", "auto"),
    ("ACCEPTEDITS", "auto"),
    ("AcceptEdits", "auto"),
])
def test_normalize_mode_accepts_valid_and_aliases(raw, want):
    assert normalize_mode(raw) == want


def test_aliases_are_a_subset_of_documented_semantics():
    """别名表只能映射到 4 档之内 —— 防止将来别名指向一个不存在的模式。"""
    assert set(MODE_ALIASES.values()) <= set(VALID_MODES)
    assert MODE_ALIASES["acceptedits"] == "auto"


@pytest.mark.parametrize("raw", ["bogus", "", "  ", "yolo", "acceptedits2", "askk"])
def test_unknown_mode_raises_instead_of_silent_fallback(raw):
    """未知模式必须显式报错。

    旧实现静默回退 ``ask``：调用方以为自己拿到了别的模式，实际拿到的是
    「每个写操作都要用户点 y」的默认模式 —— GUI 场景下等价于全拒。
    """
    with pytest.raises(ValueError) as ei:
        normalize_mode(raw)
    assert "未知权限模式" in str(ei.value)
    for m in VALID_MODES:
        assert m in str(ei.value), "报错文案应列出全部合法值，便于用户自查"


def test_permission_manager_rejects_unknown_mode():
    """构造期就要拦下，而不是等到第一次写操作才炸。"""
    with pytest.raises(ValueError):
        PermissionManager(mode="bogus")
    with pytest.raises(ValueError):
        PermissionManager(mode="SAFE".lower() + "typo")


# ────────────── 2. 非 TTY + deny_all：与接入通道前逐字节一致 ──────────────


@pytest.mark.parametrize("mode,level,tool,want_ok,want_reason", [
    # ask：非交互一律拒绝（理由文案含模式名与工具名）
    ("ask", 1, "write_file", False,
     "当前模式 (ask) 下非交互环境无法请求用户审批写操作 [write_file]，"
     "请在交互终端运行或指定 --permission=auto"),
    ("ask", 3, "run_command", False,
     "当前模式 (ask) 下非交互环境无法请求用户审批写操作 [run_command]，"
     "请在交互终端运行或指定 --permission=auto"),
    ("ask", 5, "delete_file", False,
     "当前模式 (ask) 下非交互环境无法请求用户审批写操作 [delete_file]，"
     "请在交互终端运行或指定 --permission=auto"),
    # plan：非交互拒绝文案与 ask 不同（点明"需出具并确认行动计划"）
    ("plan", 1, "write_file", False,
     "Plan 模式 (--permission=plan) 下非交互环境禁止自动执行写操作 "
     "[write_file]，需出具并确认行动计划"),
    ("plan", 5, "delete_file", False,
     "Plan 模式 (--permission=plan) 下非交互环境禁止自动执行写操作 "
     "[delete_file]，需出具并确认行动计划"),
    # safe：严格只读
    ("safe", 1, "write_file", False,
     "当前处于严格安全模式 (--permission=safe)，已拒绝执行非只读操作 [write_file]"),
    # auto：Level 0-3 自动放行，Level 4 起回落 headless 拒绝
    ("auto", 1, "write_file", True, "全自动模式 (--permission=auto)，已自动执行 [write_file]"),
    ("auto", 2, "python_eval", True, "全自动模式 (--permission=auto)，已自动执行 [python_eval]"),
    ("auto", 3, "run_command", True, "全自动模式 (--permission=auto)，已自动执行 [run_command]"),
    ("auto", 4, "fetch_url", False,
     "当前模式 (auto) 下非交互环境无法请求用户审批写操作 [fetch_url]，"
     "请在交互终端运行或指定 --permission=auto"),
    ("auto", 5, "delete_file", False,
     "当前模式 (auto) 下非交互环境无法请求用户审批写操作 [delete_file]，"
     "请在交互终端运行或指定 --permission=auto"),
])
def test_headless_deny_all_is_byte_identical(headless_stdin, mode, level, tool,
                                              want_ok, want_reason):
    pm = PermissionManager(mode=mode, workspace_root=ROOT)
    ok, reason = pm.check_permission(tool, level, {"path": "x.md"}, interactive=True)
    assert (ok, reason) == (want_ok, want_reason)


def test_read_only_always_passes_in_every_mode(headless_stdin):
    """Level 0 只读在任何模式、任何环境都必须零摩擦放行。"""
    for mode in VALID_MODES:
        pm = PermissionManager(mode=mode, workspace_root=ROOT)
        assert pm.check_permission("read_file", 0, {"path": "AGENTS.md"},
                                   interactive=False) == (True, "只读安全操作，自动放行")


def test_explicit_interactive_false_same_as_pipe(headless_stdin):
    """``interactive=False`` 与「stdin 不是 TTY」两条路都必须走 headless。"""
    pm = PermissionManager(mode="ask", workspace_root=ROOT)
    want = ("当前模式 (ask) 下非交互环境无法请求用户审批写操作 [write_file]，"
            "请在交互终端运行或指定 --permission=auto")
    assert pm.check_permission("write_file", 1, {}, interactive=False) == (False, want)
    assert pm.check_permission("write_file", 1, {}, interactive=True) == (False, want)


def test_default_policy_is_deny_all():
    """默认策略必须是 deny_all —— 配置缺失时失败方向只能是"更严"。"""
    assert approval_mod.DEFAULT_HEADLESS_POLICY == "deny_all"
    pm = PermissionManager(mode="ask", workspace_root=ROOT)
    assert pm.headless_policy == "deny_all"
    assert pm.headless_allow_tools == ()


# ────────────────────── 3. headless 三档策略 ──────────────────────


def test_headless_allow_list_policy(headless_stdin):
    pm = PermissionManager(
        mode="ask", workspace_root=ROOT,
        config={"agent": {"headless_write_policy": "allow_list",
                          "headless_allow_tools": ["write_file"]}},
    )
    assert pm.check_permission("write_file", 1, {"path": "x.md"},
                               interactive=False)[0] is True
    assert pm.check_permission("edit_file", 1, {"path": "x.md"},
                               interactive=False)[0] is False
    # 白名单只对写操作生效，Level 3 Shell 不因点名而放行（策略只覆盖写入类）
    assert pm.check_permission("run_command", 3, {},
                               interactive=False)[0] is False


def test_headless_auto_within_workspace_policy(tmp_path):
    pm = PermissionManager(
        mode="ask", workspace_root=tmp_path,
        config={"agent": {"headless_write_policy": "auto_within_workspace"}},
    )
    inside = tmp_path / "01-数学" / "_状态"
    inside.mkdir(parents=True, exist_ok=True)

    ok, reason = pm.check_permission("write_file", 1, {"path": "01-数学/_状态/x.md"},
                                     interactive=False)
    assert ok is True and "工作区内" in reason

    # 越界写入（相对路径逃逸）必须拒绝
    ok, reason = pm.check_permission("write_file", 1, {"path": "../../outside.txt"},
                                     interactive=False)
    assert ok is False and "越界" in reason

    # 绝对路径指向工作区外也必须拒绝
    ok, _ = pm.check_permission("write_file", 1, {"path": str(tmp_path.parent / "o.txt")},
                                interactive=False)
    assert ok is False

    # 取不到目标路径 → 拒绝（不能猜）
    assert pm.check_permission("write_file", 1, {}, interactive=False)[0] is False
    # 非写入级别（Shell / 网络 / 破坏性）不在此策略覆盖范围 → 拒绝
    assert pm.check_permission("run_command", 3, {"path": "x"}, interactive=False)[0] is False
    assert pm.check_permission("delete_file", 5, {"path": "x"}, interactive=False)[0] is False


@pytest.mark.parametrize("bad", ["yolo", "", "DENY_ALL ", 123, None, ["deny_all"]])
def test_invalid_headless_policy_falls_back_to_deny_all(headless_stdin, bad):
    """配置写错时只能更严：任何非法值一律回落 deny_all。"""
    pm = PermissionManager(mode="ask", workspace_root=ROOT,
                           config={"agent": {"headless_write_policy": bad}})
    assert pm.headless_policy == "deny_all"
    assert pm.check_permission("write_file", 1, {"path": "x.md"},
                               interactive=False)[0] is False


def test_headless_config_shapes_are_tolerated():
    """``config`` 可能是 None / 非 dict / 缺 agent 段 —— 都不得崩。"""
    for cfg in (None, {}, [], "x", {"agent": None}, {"agent": "x"}):
        pm = PermissionManager(mode="ask", workspace_root=ROOT, config=cfg)
        assert pm.headless_policy == "deny_all"


# ────────────────────── 4. G13 回归：GUI 写操作 ──────────────────────


def _gui_permission_mode() -> str:
    """从 GUI worker 源码里取出它实际传给 AgentRunner 的模式名。"""
    src = GUI_WORKER.read_text(encoding="utf-8")
    m = re.search(r'permission_mode\s*=\s*"([^"]*)"', src)
    assert m, "agent_worker.py 里找不到 permission_mode= 实参，测试需同步更新"
    return m.group(1)


def test_gui_worker_passes_a_recognized_mode():
    """GUI 传的模式名必须是规范化后有效的（旧值 acceptEdits 曾静默降级）。"""
    mode = _gui_permission_mode()
    assert normalize_mode(mode) == "auto", (
        f"GUI 传的 permission_mode={mode!r} 规范化后不是 auto；"
        "桌面端需要 Level 0-3 自动执行（Level 4-5 交由 headless 策略）"
    )


def test_gui_worker_mode_is_canonical_not_alias():
    """[G13] 传参对齐：GUI 应直接传规范名，而不是依赖别名表。

    别名保留是为了兼容外部调用方；自家调用点依赖别名会让「谁在用什么语义」
    变得不可 grep。
    """
    assert _gui_permission_mode() in VALID_MODES


def test_g13_gui_write_is_no_longer_denied(tmp_path):
    """G13 现场复现：GUI 的 (模式, interactive=False) 组合下写操作必须放行。"""
    pm = PermissionManager(mode=_gui_permission_mode(), workspace_root=tmp_path)
    ok, reason = pm.check_permission(
        "write_file", 1, {"path": "01-数学/_状态/今日任务.md"}, interactive=False)
    assert ok is True, f"GUI 写操作仍被拒（G13 回归）: {reason}"


def test_g13_alias_alone_also_works(tmp_path):
    """即便外部调用方仍传旧别名，也必须落到 auto 语义而不是被静默降级。"""
    pm = PermissionManager(mode="acceptEdits", workspace_root=tmp_path)
    assert pm.mode == "auto"
    assert pm.check_permission("write_file", 1, {"path": "x.md"},
                               interactive=False)[0] is True


def test_g13_gui_still_cannot_delete_or_fetch(tmp_path, headless_stdin):
    """修复不得把高危操作一起放开：Level 4-5 在 GUI 下仍应被拦。"""
    pm = PermissionManager(mode=_gui_permission_mode(), workspace_root=tmp_path)
    for level, tool in ((4, "fetch_url"), (5, "delete_file")):
        ok, reason = pm.check_permission(tool, level, {}, interactive=False)
        assert ok is False, f"Level {level} 被误放行: {tool}"
        assert "非交互环境" in reason


def test_gui_worker_agent_runner_wires_config_into_permissions(tmp_path, headless_stdin):
    """端到端：AgentRunner 把 config 一路传到 PermissionManager 的 headless 策略。

    GUI 场景（``interactive=False``）下学员可通过
    ``ky config set agent.headless_write_policy auto_within_workspace`` 让
    桌面端能落盘状态文件，而无需把模式放宽到 auto。
    """
    try:
        from agent.loop import AgentRunner
    except ImportError:  # pragma: no cover
        from tools.agent.loop import AgentRunner

    runner = AgentRunner(
        config={"agent": {"headless_write_policy": "auto_within_workspace"}},
        workspace_root=tmp_path,
        permission_mode=_gui_permission_mode(),
        quiet=True,
    )
    assert runner.permissions.mode == "auto"
    assert runner.permissions.headless_policy == "auto_within_workspace"
    # auto 模式下 Level 1 在策略层就自动放行了（走不到通道），故用同一份 config
    # 另建一个 ask 模式的 manager，验证配置确实能一路驱动通道决策。
    pm_ask = PermissionManager(mode="ask", workspace_root=tmp_path,
                               config={"agent": {"headless_write_policy": "auto_within_workspace"}})
    ok, reason = pm_ask.check_permission("write_file", 1, {"path": "_状态/x.md"},
                                         interactive=False)
    assert ok is True and "工作区内" in reason


# ──────────── 5. 通道不得阻塞：网关/GUI 的"超时回落 deny" ────────────


def test_headless_channel_never_touches_input(monkeypatch, headless_stdin):
    """headless 通道**绝不**读输入：无人在场时读输入＝挂死。

    网关（HTTP / IM 卡片）与 GUI 都走这条路；真正的网关超时回落 deny 在 A3b
    的 ``GatewayApproval`` 里，此处先钉住"不阻塞"这条底线。
    """

    def _boom(*_a, **_k):  # pragma: no cover - 只有回归才会触发
        raise AssertionError("headless 通道调用了 input() —— 会阻塞一个不存在的用户")

    monkeypatch.setattr("builtins.input", _boom)
    pm = PermissionManager(mode="ask", workspace_root=ROOT)
    for level, tool in ((1, "write_file"), (3, "run_command"), (5, "delete_file")):
        ok, _ = pm.check_permission(tool, level, {"path": "x.md"}, interactive=False)
        assert ok is False


def test_closed_stdin_is_treated_as_headless(monkeypatch):
    """stdin 被关闭（isatty 抛异常）时也必须安全降级，而不是把异常抛给调用方。"""

    class _BrokenStdin:
        def isatty(self):
            raise OSError("stdin closed")

    monkeypatch.setattr(sys, "stdin", _BrokenStdin())
    pm = PermissionManager(mode="ask", workspace_root=ROOT)
    ok, reason = pm.check_permission("write_file", 1, {"path": "x.md"}, interactive=True)
    assert ok is False and "非交互环境" in reason


def test_select_channel_dispatch(monkeypatch):
    """``select_channel`` 是通道选择的唯一实现处。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    ch = approval_mod.select_channel(interactive=True, mode="ask")
    assert isinstance(ch, approval_mod.HeadlessApproval) and ch.is_interactive is False

    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    ch = approval_mod.select_channel(interactive=True, mode="ask")
    assert isinstance(ch, approval_mod.TtyApproval) and ch.is_interactive is True

    # 调用方显式声明不可交互 → 即便 stdin 是 TTY 也走 headless
    ch = approval_mod.select_channel(interactive=False, mode="ask")
    assert isinstance(ch, approval_mod.HeadlessApproval)


# ────────────────────── 6. TTY 通道：卡片与选择 ──────────────────────


def test_tty_card_approves_on_y(tty_stdin, monkeypatch, capsys):
    _feed_input(monkeypatch, ["y"])
    pm = PermissionManager(mode="ask", workspace_root=ROOT)
    ok, reason = pm.check_permission("write_file", 1, {"path": "x.md"}, interactive=True)
    assert (ok, reason) == (True, "用户批准单次执行")
    out = capsys.readouterr().out
    assert "🛡️ [权限审批] 智能私教请求调用外部工具:" in out
    assert "write_file" in out and "x.md" in out


def test_tty_card_denies_on_n_and_default(tty_stdin, monkeypatch):
    for answer in ("n", "", "whatever"):
        _feed_input(monkeypatch, [answer])
        pm = PermissionManager(mode="ask", workspace_root=ROOT)
        ok, reason = pm.check_permission("write_file", 1, {}, interactive=True)
        assert (ok, reason) == (False, "用户拒绝执行该操作")


def test_tty_remember_choice_shares_session_trust_set(tty_stdin, monkeypatch, capsys):
    """选 [a] 必须回写到 manager 的信任集（通道与策略共享同一个 set 对象）。"""
    _feed_input(monkeypatch, ["a"])
    pm = PermissionManager(mode="ask", workspace_root=ROOT)
    assert pm.check_permission("write_file", 1, {"path": "x.md"}, interactive=True)[0] is True
    capsys.readouterr()
    assert "write_file" in pm.session_allowed_tools
    assert pm.check_permission("write_file", 1, {}, interactive=True) == (True, "会话已永久信任此工具")


def test_tty_danger_card_has_no_remember_option(tty_stdin, monkeypatch, capsys):
    """Level 5 高危卡片不提供 [a] 永久信任：选了 a 也只能按拒绝处理。"""
    _feed_input(monkeypatch, ["a"])
    pm = PermissionManager(mode="ask", workspace_root=ROOT)
    ok, _ = pm.check_permission("delete_file", 5, {"path": "x.md"}, interactive=True)
    out = capsys.readouterr().out
    assert "极其谨慎核对" in out
    assert "[a] 本会话记住并信任此类操作" not in out
    assert ok is False
    assert "delete_file" not in pm.session_allowed_tools


def test_tty_eof_is_denied_not_crashed(tty_stdin, monkeypatch):
    def _eof(*_a, **_k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    pm = PermissionManager(mode="ask", workspace_root=ROOT)
    assert pm.check_permission("write_file", 1, {}, interactive=True) == (False, "用户中断审批")


# ────────────────────── 7. plan 模式的计划审计卡片 ──────────────────────


def test_plan_headless_never_reads_input(monkeypatch, headless_stdin):
    def _boom(*_a, **_k):  # pragma: no cover
        raise AssertionError("plan 模式 headless 路径调用了 input()")

    monkeypatch.setattr("builtins.input", _boom)
    pm = PermissionManager(mode="plan", workspace_root=ROOT)
    ok, reason = pm.check_permission("write_file", 1, {"path": "x.md"}, interactive=False)
    assert ok is False and "需出具并确认行动计划" in reason


def test_plan_headless_ignores_lenient_policy(tmp_path):
    """plan 模式在无人可批准时一律拒绝 —— 不被 headless 宽松策略绕过。"""
    pm = PermissionManager(
        mode="plan", workspace_root=tmp_path,
        config={"agent": {"headless_write_policy": "auto_within_workspace"}})
    ok, reason = pm.check_permission("write_file", 1, {"path": "x.md"}, interactive=False)
    assert ok is False and "Plan 模式" in reason


def test_plan_tty_renders_audit_card_and_makes_checkpoint(tty_stdin, monkeypatch, capsys, tmp_path):
    """TTY + plan：必须渲染计划审计卡片，并在写前创建 Checkpoint 快照。"""
    target = tmp_path / "math_sample.txt"
    target.write_text("原始内容", encoding="utf-8")

    _feed_input(monkeypatch, ["y"])
    pm = PermissionManager(mode="plan", workspace_root=tmp_path)
    ok, reason = pm.check_permission("write_file", 1, {"path": "math_sample.txt"},
                                     interactive=True)
    out = capsys.readouterr().out
    assert "📋 [Plan Mode 行动计划审计]" in out
    assert "Checkpoint 安全快照已创建" in out
    assert (ok, reason) == (True, "用户批准 Plan 模式执行变更")
    assert list((tmp_path / ".checkpoint").glob("ckpt_*/math_sample.txt")), "未创建写前快照"


def test_plan_tty_rejection_leaves_no_execution(tty_stdin, monkeypatch, capsys):
    _feed_input(monkeypatch, ["n"])
    pm = PermissionManager(mode="plan", workspace_root=ROOT)
    ok, reason = pm.check_permission("write_file", 1, {"path": "x.md"}, interactive=True)
    capsys.readouterr()
    assert (ok, reason) == (False, "用户拒绝 Plan 模式该项执行计划")


# ────────────────────── 8. CLI 层：未知模式快速失败 ──────────────────────


def test_cli_rejects_unknown_permission_mode_fast():
    """``ky --permission=bogus`` 必须立刻报错退出，而不是静默按 ask 跑完。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ky_cli.py"), "--permission=bogus"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )
    assert proc.returncode == 3, f"期望退出码 3，实际 {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert "未知权限模式" in (proc.stdout + proc.stderr)


def test_cli_accepts_case_variant_without_silent_downgrade():
    """``--permission=SAFE`` 必须真的进入 safe（旧实现静默降级成 ask）。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ky_cli.py"), "--permission=SAFE", "status"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180,
    )
    assert proc.returncode == 0, f"SAFE 变体应被接受\n{proc.stdout}\n{proc.stderr}"
    assert "未知权限模式" not in (proc.stdout + proc.stderr)
