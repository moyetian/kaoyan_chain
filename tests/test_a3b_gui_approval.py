# -*- coding: utf-8 -*-
"""A3b 回归 · GUI 审批通道（G13 后半段：Level 4-5 弹窗而不是静默拒绝）

缺陷现场（G13 后半段，A3a 修复后仍存在）
----------------------------------------
桌面端 ``AgentWorker``（``QThread``）以 ``permission_mode="auto"`` +
``runner.run(..., interactive=False)`` 运行。A3a 让 Level 0-3 在 GUI 下自动
执行了，但 **Level 4（网络 fetch_url）与 Level 5（破坏性 delete_file）仍走
headless 通道的 ``deny_all`` 被静默拒绝** —— 用户只看到工具返回
``PermissionDenied: 操作被拦截 (... 非交互环境 ...)``。人明明坐在屏幕前，
却没有任何弹窗可点，也没有任何解释。

修复（A3b）
-----------
1. ``PermissionManager`` / ``AgentRunner`` 新增 ``approval_channel`` 注入点：
   调用方显式提供通道就直接用（GUI 弹窗不依赖 TTY 判定），不传则与 A3a
   行为逐字节一致；
2. ``tools/gui/approval_bridge.py`` 的 ``GuiApproval``：worker 线程建局部
   ``QEventLoop`` + 排队信号把请求投到主线程弹模态框 + 排队信号把答复回填
   worker 线程，**不用 ``processEvents`` 轮询**；超时（``agent.approval_timeout``，
   默认 120 秒）/ 无 ``QApplication`` / 关闭窗口 / 异常 → 一律拒绝；
3. ``tools/agent/approval.py`` 的 ``GatewayApproval``：传输可注入的卡片通道。
   **核实结论**：``tools/cli/gateway.py`` 的 ``/api/ask`` 与
   ``/v1/chat/completions`` 都直连 ``query_llm_reply()``（纯 LLM 问答，没有
   agent 工具循环），网关侧没有消费者 —— 因此**刻意不新增 HTTP 端点**
   （那等于凭空造出一个能授权工具执行的鉴权面），只交付可注入传输的通道。

本文件锁定：注入点语义、GUI 通道的线程安全与 fail-closed 全路径、Level 5
不得出现"本会话信任"、批准后共享信任集回写、GUI worker 真的注入了通道、
网关通道的超时/乱序/重复答复语义，以及"网关无消费者"这条核实结论。
"""

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent import approval as approval_mod  # noqa: E402
from agent.permissions import PermissionManager  # noqa: E402

try:
    from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal, Slot
    PYSIDE_AVAILABLE = True
except ImportError:  # pragma: no cover - 无 PySide6 的环境
    PYSIDE_AVAILABLE = False

GUI_WORKER = ROOT / "tools" / "gui" / "workers" / "agent_worker.py"
GUI_BRIDGE = ROOT / "tools" / "gui" / "approval_bridge.py"
GATEWAY = ROOT / "tools" / "cli" / "gateway.py"


# ────────────────────────── 公共夹具与驱动 ──────────────────────────


class _FakeStdin:
    """最小 stdin 替身：只回答 ``isatty``。"""

    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class _ScriptedChannel:
    """假审批通道：记录调用并返回脚本化结果（用于注入点语义测试）。"""

    def __init__(self, allowed=True, reason="假通道放行", session_allowed_tools=None):
        self.allowed = allowed
        self.reason = reason
        self.calls = []
        self.plan_calls = []
        self.session_allowed_tools = session_allowed_tools if session_allowed_tools is not None else set()

    @property
    def is_interactive(self) -> bool:
        return True

    def request(self, tool_name, level, tool_args):
        self.calls.append((tool_name, level, dict(tool_args or {})))
        return self.allowed, self.reason

    def request_plan(self, tool_name, level, tool_args):
        self.plan_calls.append((tool_name, level, dict(tool_args or {})))
        return self.allowed, self.reason


@pytest.fixture(scope="module")
def qt_app():
    """离屏 Qt 应用（无真实显示器）。缺 PySide6 / 平台插件时整组跳过。"""
    if not PYSIDE_AVAILABLE:
        pytest.skip("未安装 PySide6，跳过 GUI 审批通道用例")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
    except Exception as exc:  # pragma: no cover - 平台插件缺失
        pytest.skip(f"Qt 平台插件不可用，跳过 GUI 审批通道用例: {exc}")
    return app


def _gui_approval_cls():
    try:
        from gui.approval_bridge import GuiApproval
    except ImportError:  # pragma: no cover - 双导入路径兼容
        from tools.gui.approval_bridge import GuiApproval
    return GuiApproval


if PYSIDE_AVAILABLE:
    class _ProbeWorker(QThread):
        """在真实 worker 线程里调用通道，复现 AgentWorker 的调用现场。"""

        done = Signal(object)

        def __init__(self, channel, tool_name, level, tool_args, is_plan):
            super().__init__()
            self.channel = channel
            self.tool_name = tool_name
            self.level = level
            self.tool_args = tool_args
            self.is_plan = is_plan

        def run(self):
            try:
                if self.is_plan:
                    res = self.channel.request_plan(self.tool_name, self.level, self.tool_args)
                else:
                    res = self.channel.request(self.tool_name, self.level, self.tool_args)
            except BaseException as exc:  # pragma: no cover - 只有回归才会走到
                res = ("EXCEPTION", repr(exc))
            self.done.emit(res)

    class _Collector(QObject):
        """主线程侧的收集器（排队接收，等价于 GUI 主窗口的槽）。"""

        def __init__(self, loop):
            super().__init__()
            self.loop = loop
            self.result = None
            self.received = False

        @Slot(object)
        def on_done(self, res):
            self.result = res
            self.received = True
            self.loop.quit()

    class _DialogClicker(QObject):
        """主线程里找到当前对话框并点击指定按钮（离屏模拟真人点击）。"""

        def __init__(self, channel, button_text, parent=None):
            super().__init__(parent)
            self.channel = channel
            self.button_text = button_text
            self.clicks = 0
            self._deadline = 0.0
            self._timer = QTimer(self)
            self._timer.setInterval(30)
            self._timer.timeout.connect(self._try_click)

        def start(self, max_ms=6000):
            self._deadline = time.time() + max_ms / 1000.0
            self._timer.start()

        def _try_click(self):
            from PySide6.QtWidgets import QPushButton
            dlg = next(iter(getattr(self.channel, "_active_dialogs", {}).values()), None)
            if dlg is not None:
                for btn in dlg.findChildren(QPushButton):
                    if self.button_text in btn.text():
                        btn.click()
                        self.clicks += 1
                        self._timer.stop()
                        return
            if time.time() > self._deadline:
                self._timer.stop()


def _drive_request(channel, tool_name="fetch_url", level=4, tool_args=None,
                   is_plan=False, guard_ms=8000):
    """worker 线程发请求 + 主线程跑事件循环收结果（确定性，无显示器依赖）。"""
    loop = QEventLoop()
    collector = _Collector(loop)
    worker = _ProbeWorker(channel, tool_name, level,
                          tool_args if tool_args is not None else {"url": "https://example.test/x"},
                          is_plan)
    worker.done.connect(collector.on_done)
    QTimer.singleShot(guard_ms, loop.quit)
    worker.start()
    loop.exec()
    worker.wait(3000)
    assert collector.received, "worker 未在保护时间内返回（通道疑似阻塞）"
    return collector.result


# ═══════════════════ 1. 注入点：显式通道 > TTY/headless 判定 ═══════════════════


def test_explicit_channel_is_used_regardless_of_tty(monkeypatch):
    """注入通道后，即便 stdin 是 TTY 也不得走 TTY 卡片（GUI 不依赖 TTY 判定）。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))

    def _boom(*_a, **_k):  # pragma: no cover - 只有回归才会触发
        raise AssertionError("注入通道后仍去读终端输入")

    monkeypatch.setattr("builtins.input", _boom)
    ch = _ScriptedChannel(allowed=True, reason="GUI 弹窗批准")
    pm = PermissionManager(mode="ask", workspace_root=ROOT, approval_channel=ch)
    assert pm.approval_channel is ch
    ok, reason = pm.check_permission("fetch_url", 4, {"url": "https://example.test"},
                                     interactive=True)
    assert (ok, reason) == (True, "GUI 弹窗批准")
    assert ch.calls == [("fetch_url", 4, {"url": "https://example.test"})]


def test_explicit_channel_also_used_when_non_interactive(monkeypatch):
    """GUI 的 ``interactive=False`` 不得再拦住显式注入的通道（G13 现场）。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    ch = _ScriptedChannel(allowed=True, reason="GUI 弹窗批准")
    pm = PermissionManager(mode="auto", workspace_root=ROOT, approval_channel=ch)
    # auto 模式 Level 0-3 在策略层直接放行，走不到通道
    assert pm.check_permission("write_file", 1, {"path": "x.md"}, interactive=False)[0] is True
    assert ch.calls == []
    # Level 4-5 必须落到注入通道，而不是 headless 的静默拒绝
    assert pm.check_permission("fetch_url", 4, {}, interactive=False) == (True, "GUI 弹窗批准")
    assert pm.check_permission("delete_file", 5, {"path": "x.md"}, interactive=False) == (True, "GUI 弹窗批准")


def test_explicit_channel_used_for_plan_mode(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    ch = _ScriptedChannel(allowed=True, reason="计划已批准")
    pm = PermissionManager(mode="plan", workspace_root=ROOT, approval_channel=ch)
    assert pm.check_permission("write_file", 1, {"path": "x.md"}, interactive=False) == (True, "计划已批准")
    assert ch.plan_calls == [("write_file", 1, {"path": "x.md"})]


def test_no_channel_keeps_a3a_behaviour(monkeypatch):
    """不传通道时必须与 A3a 完全一致（headless 默认 deny_all）。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    pm = PermissionManager(mode="auto", workspace_root=ROOT)
    assert pm.approval_channel is None
    ok, reason = pm.check_permission("delete_file", 5, {"path": "x.md"}, interactive=False)
    assert ok is False and "非交互环境" in reason


def test_manager_shares_channel_session_trust_set():
    """通道自带信任集时，manager 必须共享同一个 set 对象（否则"信任"不生效）。"""
    shared = set()
    ch = _ScriptedChannel(session_allowed_tools=shared)
    pm = PermissionManager(mode="ask", workspace_root=ROOT, approval_channel=ch)
    assert pm.session_allowed_tools is shared
    shared.add("fetch_url")
    assert pm.check_permission("fetch_url", 4, {}, interactive=False) == (True, "会话已永久信任此工具")


def test_agent_runner_passes_channel_through(tmp_path, monkeypatch):
    """AgentRunner 必须把 approval_channel 一路透传到 PermissionManager。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    try:
        from agent.loop import AgentRunner
    except ImportError:  # pragma: no cover
        from tools.agent.loop import AgentRunner

    ch = _ScriptedChannel(allowed=True, reason="GUI 弹窗批准")
    runner = AgentRunner(config={}, workspace_root=tmp_path, permission_mode="auto",
                         approval_channel=ch, quiet=True)
    assert runner.permissions.approval_channel is ch
    assert runner.permissions.check_permission("fetch_url", 4, {}, interactive=False) == (True, "GUI 弹窗批准")
    # 不传通道时回落默认（headless deny_all），保持 A3a 行为
    runner2 = AgentRunner(config={}, workspace_root=tmp_path, permission_mode="auto", quiet=True)
    assert runner2.permissions.approval_channel is None
    assert runner2.permissions.check_permission("fetch_url", 4, {}, interactive=False)[0] is False


def test_end_to_end_gui_channel_unblocks_dangerous_tool(tmp_path, monkeypatch):
    """端到端：注入 GUI 通道后 Level 5 不再返回 PermissionDenied，而是走通道。

    阴性对照：同一现场不注入通道 → 仍被 headless 静默拒绝（G13 旧行为）。
    """
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    try:
        from agent.loop import AgentRunner
    except ImportError:  # pragma: no cover
        from tools.agent.loop import AgentRunner

    victim = tmp_path / "待删除讲义.md"
    victim.write_text("旧内容", encoding="utf-8")
    ch = _ScriptedChannel(allowed=True, reason="GUI 弹窗批准")
    runner = AgentRunner(config={}, workspace_root=tmp_path, permission_mode="auto",
                         approval_channel=ch, quiet=True)
    out = runner.tool_registry.execute_tool("delete_file", {"path": "待删除讲义.md"},
                                            interactive=False)
    assert "PermissionDenied" not in out, f"GUI 注入通道后仍被权限层拦截：{out}"
    assert [c[0] for c in ch.calls] == ["delete_file"]
    assert not victim.exists(), "用户批准后工具应真正执行"

    # 阴性对照：不注入通道（A3b 之前的 GUI 现场）→ headless 拒绝，文件保留
    runner2 = AgentRunner(config={}, workspace_root=tmp_path, permission_mode="auto", quiet=True)
    victim2 = tmp_path / "待删除讲义2.md"
    victim2.write_text("旧内容", encoding="utf-8")
    out2 = runner2.tool_registry.execute_tool("delete_file", {"path": "待删除讲义2.md"},
                                             interactive=False)
    assert "PermissionDenied" in out2
    assert victim2.exists()


def test_agent_worker_source_pins_channel_injection():
    """静态钉住：GUI worker 必须注入通道，且模式仍是规范化后的 auto。"""
    src = GUI_WORKER.read_text(encoding="utf-8")
    assert "approval_channel=self._approval_channel" in src, "GUI worker 未把审批通道注入 AgentRunner"
    assert "def _build_approval_channel" in src
    assert 'permission_mode="auto"' in src


def test_agent_worker_injects_gui_channel_behaviorally(qt_app, monkeypatch):
    """行为断言：AgentWorker 构造出的 GuiApproval 真的传给了 AgentRunner。"""
    GuiApproval = _gui_approval_cls()
    from tools.gui.workers.agent_worker import AgentWorker

    captured = {}

    def fake_init(self, *args, **kwargs):
        captured.update(kwargs)
        # [B3b] AgentWorker 现在在 finally 里调用 runner.close()：
        # 把 fake 会话标记为已收尾，让幂等 close() 第一行就短路返回。
        self._closed = True

    def fake_run(self, *args, **kwargs):
        return "【私教】测试回复"

    patched = 0
    for name in ("agent.loop", "tools.agent.loop"):
        try:
            mod = importlib.import_module(name)
        except ImportError:  # pragma: no cover
            continue
        monkeypatch.setattr(mod.AgentRunner, "__init__", fake_init, raising=True)
        monkeypatch.setattr(mod.AgentRunner, "run", fake_run, raising=True)
        patched += 1
    assert patched >= 1

    worker = AgentWorker(config={}, user_input="帮我分析一下这道题")
    worker.run()
    assert isinstance(captured.get("approval_channel"), GuiApproval), (
        f"AgentWorker 未注入 GUI 审批通道：{captured.get('approval_channel')!r}"
    )
    assert captured.get("permission_mode") == "auto"


def test_agent_worker_degrades_gracefully_when_channel_build_fails(qt_app, monkeypatch):
    """通道构造失败必须优雅降级为 None，不得让既有流程变红。"""
    import gui.approval_bridge as bridge
    from tools.gui.workers.agent_worker import AgentWorker

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟 PySide6 不可用")

    monkeypatch.setattr(bridge, "GuiApproval", _boom, raising=True)
    worker = AgentWorker(config={}, user_input="随便问一句")
    assert worker._approval_channel is None


# ═══════════════════ 2. GuiApproval：线程安全与 fail-closed ═══════════════════


def test_gui_approval_approve_and_dialog_runs_on_main_thread(qt_app):
    """批准路径：对话框只在主线程创建，worker 线程拿到答复。"""
    GuiApproval = _gui_approval_cls()
    seen = {}

    def runner(request):
        seen["thread"] = QThread.currentThread()
        seen["request"] = dict(request)
        return {"approved": True, "reason": "用户批准单次执行"}

    ch = GuiApproval(timeout=5.0, dialog_runner=runner)
    res = _drive_request(ch, "fetch_url", 4, {"url": "https://example.test/真题.pdf"})
    assert res == (True, "用户批准单次执行")
    assert seen["thread"] is qt_app.thread(), "对话框必须在 Qt 主线程创建/操作"
    assert QThread.currentThread() is qt_app.thread()
    req = seen["request"]
    assert req["tool_name"] == "fetch_url"
    assert "Level 4" in req["level_desc"]
    assert req["is_danger"] is False and req["allow_remember"] is True
    assert "https://example.test/真题.pdf" in req["args_preview"]


def test_gui_approval_arg_preview_truncates_over_60_chars(qt_app):
    """参数预览 >60 字符截断（与 TTY 卡片同口径）。"""
    GuiApproval = _gui_approval_cls()
    seen = {}
    ch = GuiApproval(timeout=5.0, dialog_runner=lambda req: (seen.update(req), {"approved": False})[1])
    long_url = "https://example.test/" + "a" * 120
    res = _drive_request(ch, "fetch_url", 4, {"url": long_url})
    assert res == (False, "用户拒绝执行该操作")
    preview = seen["args_preview"]
    assert "..." in preview
    assert long_url not in preview


def test_gui_approval_remember_writes_shared_session_set(qt_app):
    """选"本会话信任"后，工具名写进与 PermissionManager 共享的 set。"""
    GuiApproval = _gui_approval_cls()
    shared = set()
    ch = GuiApproval(session_allowed_tools=shared, timeout=5.0,
                     dialog_runner=lambda req: {"approved": True, "remember": True})
    res = _drive_request(ch, "fetch_url", 4, {"url": "https://example.test"})
    assert res == (True, "用户批准本会话永久信任此工具")
    assert shared == {"fetch_url"}

    pm = PermissionManager(mode="ask", workspace_root=ROOT, approval_channel=ch)
    assert pm.session_allowed_tools is shared
    assert pm.check_permission("fetch_url", 4, {}, interactive=False) == (True, "会话已永久信任此工具")


def test_gui_approval_denial_keeps_session_set_clean(qt_app):
    GuiApproval = _gui_approval_cls()
    shared = set()
    ch = GuiApproval(session_allowed_tools=shared, timeout=5.0,
                     dialog_runner=lambda req: {"approved": False, "remember": True})
    res = _drive_request(ch, "delete_file", 5, {"path": "旧讲义.md"})
    assert res == (False, "用户拒绝执行该操作")
    assert shared == set()


def test_gui_approval_timeout_denies_and_never_hangs(qt_app):
    """超时 → 拒绝（fail-closed），理由要能区分"超时"与"用户拒绝"。"""
    GuiApproval = _gui_approval_cls()
    ch = GuiApproval(timeout=0.2, dialog_runner=lambda req: None)  # 模拟无人点击
    t0 = time.time()
    res = _drive_request(ch, "delete_file", 5, {"path": "旧讲义.md"}, guard_ms=6000)
    elapsed = time.time() - t0
    assert res[0] is False
    assert "超时" in res[1] and "已拒绝" in res[1]
    assert "用户拒绝" not in res[1], "超时理由必须与用户主动拒绝区分开"
    assert elapsed < 5.0, f"超时未生效（耗时 {elapsed:.1f}s）"
    assert ch.session_allowed_tools == set()


def test_gui_approval_dialog_exception_fails_closed(qt_app):
    """对话框实现抛异常 → 拒绝，绝不把异常抛给 worker。"""
    GuiApproval = _gui_approval_cls()

    def _boom(_req):
        raise RuntimeError("模拟对话框崩溃")

    ch = GuiApproval(timeout=5.0, dialog_runner=_boom)
    res = _drive_request(ch, "fetch_url", 4, {"url": "https://example.test"})
    assert res == (False, "GUI 审批对话框异常，已拒绝执行该操作")


def test_gui_approval_danger_level_has_no_session_trust(qt_app):
    """Level 5：卡片不提供"本会话信任"，越权回传 remember 也不得写进信任集。"""
    GuiApproval = _gui_approval_cls()
    seen = {}
    shared = set()
    ch = GuiApproval(session_allowed_tools=shared, timeout=5.0,
                     dialog_runner=lambda req: (seen.update(req), {"approved": True, "remember": True})[1])
    res = _drive_request(ch, "delete_file", 5, {"path": "01-数学/_状态/旧文件.md"})
    assert seen["is_danger"] is True
    assert seen["allow_remember"] is False, "Level 5 不得出现'本会话信任'选项"
    assert "Level 5" in seen["level_desc"]
    assert res == (True, "用户批准单次执行")
    assert shared == set(), "Level 5 的 remember 回传被越权接受"


def test_gui_approval_plan_card_includes_checkpoint_hint(qt_app, tmp_path):
    """Plan 模式：弹计划审计框，含"写前已创建 Checkpoint 快照"提示。"""
    GuiApproval = _gui_approval_cls()
    target = tmp_path / "示例讲义.md"
    target.write_text("原始内容", encoding="utf-8")
    seen = {}

    def checkpoint_fn(rel_path):
        assert rel_path == "示例讲义.md"
        return str(tmp_path / ".checkpoint" / "ckpt_20260924" / "示例讲义.md")

    ch = GuiApproval(checkpoint_fn=checkpoint_fn, timeout=5.0,
                     dialog_runner=lambda req: (seen.update(req), {"approved": True})[1])
    res = _drive_request(ch, "write_file", 1, {"path": "示例讲义.md"}, is_plan=True)
    assert res == (True, "用户批准 Plan 模式执行变更")
    assert seen["is_plan"] is True
    assert "Checkpoint 安全快照已创建" in seen["checkpoint_hint"]
    assert "示例讲义.md" in seen["checkpoint_hint"]


def test_gui_approval_plan_denial(qt_app):
    GuiApproval = _gui_approval_cls()
    ch = GuiApproval(timeout=5.0, dialog_runner=lambda req: {"approved": False})
    res = _drive_request(ch, "write_file", 1, {"path": "示例讲义.md"}, is_plan=True)
    assert res == (False, "用户拒绝 Plan 模式该项执行计划")


def test_gui_approval_without_qapplication_denies_fast():
    """无 QApplication（CI / 无头）→ 直接拒绝且不阻塞、不崩。

    该路径必须在**没有** QApplication 的干净进程里验证（本测试进程里
    可能已有离屏 app），因此用子进程。
    """
    if not PYSIDE_AVAILABLE:
        pytest.skip("未安装 PySide6，跳过 GUI 审批通道用例")
    code = "\n".join([
        "import sys",
        f"sys.path.insert(0, {str(ROOT)!r})",
        f"sys.path.insert(0, {str(ROOT / 'tools')!r})",
        "from gui.approval_bridge import GuiApproval",
        "assert __import__('PySide6.QtWidgets', fromlist=['QApplication']).QApplication.instance() is None",
        "ch = GuiApproval(timeout=0.5)",
        "print('RESULT1', ch.request('fetch_url', 4, {'url': 'https://example.test'}))",
        "print('RESULT2', ch.request_plan('write_file', 1, {'path': 'x.md'}))",
    ])
    t0 = time.time()
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=90)
    elapsed = time.time() - t0
    assert proc.returncode == 0, f"无 GUI 环境不得崩溃：\n{proc.stdout}\n{proc.stderr}"
    assert "GUI 审批通道不可用" in proc.stdout
    assert proc.stdout.count("(False,") == 2, proc.stdout
    assert elapsed < 60, "无 GUI 环境疑似阻塞等待输入"


def test_gui_approval_real_dialog_approve_button_click(qt_app):
    """端到端：离屏真实对话框，点「批准本次执行」→ 放行。"""
    GuiApproval = _gui_approval_cls()
    ch = GuiApproval(timeout=8.0)  # 真实默认对话框
    clicker = _DialogClicker(ch, "批准本次执行")
    clicker.start(max_ms=6000)
    res = _drive_request(ch, "fetch_url", 4, {"url": "https://example.test"}, guard_ms=9000)
    assert clicker.clicks == 1, "未能在真实对话框中找到并点击批准按钮"
    assert res == (True, "用户批准单次执行")


def test_gui_approval_real_dialog_reject_button_click(qt_app):
    """端到端：点「拒绝执行（默认）」→ 拒绝。"""
    GuiApproval = _gui_approval_cls()
    ch = GuiApproval(timeout=8.0)
    clicker = _DialogClicker(ch, "拒绝执行")
    clicker.start(max_ms=6000)
    res = _drive_request(ch, "delete_file", 5, {"path": "旧讲义.md"}, guard_ms=9000)
    assert clicker.clicks == 1
    assert res == (False, "用户拒绝执行该操作")


def test_gui_approval_timeout_closes_leftover_dialog(qt_app):
    """超时后必须关掉残留对话框（否则用户事后点批准会造成"以为执行了"的错觉）。"""
    GuiApproval = _gui_approval_cls()
    ch = GuiApproval(timeout=0.2)  # 真实对话框，无人点击
    res = _drive_request(ch, "fetch_url", 4, {"url": "https://example.test"}, guard_ms=6000)
    assert res[0] is False and "超时" in res[1]
    # 主线程处理"作废"排队事件，确认没有遗留模态框（用嵌套事件循环等待，
    # 不做 processEvents 轮询）
    deadline = time.time() + 5.0
    while ch._active_dialogs and time.time() < deadline:
        wait_loop = QEventLoop()
        QTimer.singleShot(50, wait_loop.quit)
        wait_loop.exec()
    assert ch._active_dialogs == {}, "超时后残留了未关闭的对话框"


# ═══════════════════ 3. GatewayApproval：传输可注入、超时拒绝 ═══════════════════


def test_gateway_has_no_consumer_and_no_new_endpoint():
    """核实结论：网关侧没有 agent 工具循环 → 不新增任何 HTTP 端点。

    若将来网关真的接了 AgentRunner（出现消费者），本用例会变红，提醒先设计
    鉴权面并把 ``GatewayApproval`` 接上去，而不是默默放开工具执行。
    """
    src = GATEWAY.read_text(encoding="utf-8")
    assert "AgentRunner" not in src, "网关出现 agent 工具循环 —— 需先设计审批接线"
    assert "GatewayApproval" not in src

    call_sites = []
    for f in sorted((ROOT / "tools").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover
            continue
        if "AgentRunner(" in text:
            call_sites.append(f.relative_to(ROOT).as_posix())
    assert call_sites == ["tools/cli/repl/loop.py", "tools/gui/workers/agent_worker.py"], (
        f"AgentRunner 消费者清单变了（{call_sites}）—— 新增消费者必须自带审批通道设计"
    )


def test_gateway_timeout_denies_fast():
    cards = []
    ch = approval_mod.GatewayApproval(deliver_card=cards.append, timeout=0.05)
    t0 = time.time()
    ok, reason = ch.request("fetch_url", 4, {"url": "https://example.test"})
    elapsed = time.time() - t0
    assert ok is False and "超时" in reason and "已拒绝" in reason
    assert elapsed < 5.0
    assert len(cards) == 1 and cards[0]["tool_name"] == "fetch_url"
    assert cards[0]["allow_remember"] is True and cards[0]["is_danger"] is False


def test_gateway_missing_transport_denies_immediately():
    ch = approval_mod.GatewayApproval(deliver_card=None, timeout=30.0)
    t0 = time.time()
    ok, reason = ch.request("fetch_url", 4, {})
    assert ok is False and "未配置" in reason
    assert time.time() - t0 < 2.0, "缺传输时必须立刻拒绝，不得阻塞"


def test_gateway_deliver_exception_denies():
    def _boom(_card):
        raise RuntimeError("模拟 IM 推送失败")

    ch = approval_mod.GatewayApproval(deliver_card=_boom, timeout=5.0)
    ok, reason = ch.request("fetch_url", 4, {})
    assert ok is False and "投递失败" in reason


def test_gateway_unknown_request_id_is_rejected():
    """乱序/伪造的答复（未知 id）必须被忽略，不得影响任何在途请求。"""
    ch = approval_mod.GatewayApproval(deliver_card=lambda card: None, timeout=0.05)
    assert ch.submit_reply("不存在的请求id", True) is False
    assert ch.submit_reply("", True) is False


def test_gateway_reply_for_wrong_id_leaves_request_denied():
    """答复乱序（id 对不上）→ 真实请求超时拒绝，而不是被错误放行。"""
    delivered = {}

    def deliver(card):
        delivered["id"] = card["request_id"]
        assert ch.submit_reply("另一个请求的id", True) is False

    ch = approval_mod.GatewayApproval(deliver_card=deliver, timeout=0.05)
    ok, reason = ch.request("delete_file", 5, {"path": "旧讲义.md"})
    assert ok is False and "超时" in reason
    assert delivered["id"] != "另一个请求的id"


def test_gateway_duplicate_reply_only_first_counts():
    """重复答复：只认第一次（先到的"拒绝"不能被后到的"批准"翻案）。"""
    outcome = {}

    def deliver(card):
        rid = card["request_id"]
        outcome["first"] = ch.submit_reply(rid, False, False)
        outcome["second"] = ch.submit_reply(rid, True, True)

    ch = approval_mod.GatewayApproval(deliver_card=deliver, timeout=1.0)
    ok, reason = ch.request("fetch_url", 4, {"url": "https://example.test"})
    assert outcome == {"first": True, "second": False}
    assert (ok, reason) == (False, "用户拒绝执行该操作")
    assert ch.session_allowed_tools == set()


def test_gateway_approve_with_remember_shares_session_set():
    shared = set()

    def deliver(card):
        ch.submit_reply(card["request_id"], True, True)

    ch = approval_mod.GatewayApproval(deliver_card=deliver, timeout=1.0,
                                      session_allowed_tools=shared)
    ok, reason = ch.request("fetch_url", 4, {"url": "https://example.test"})
    assert (ok, reason) == (True, "用户批准本会话永久信任此工具")
    assert shared == {"fetch_url"}

    pm = PermissionManager(mode="ask", workspace_root=ROOT, approval_channel=ch)
    assert pm.session_allowed_tools is shared


def test_gateway_danger_level_never_remembers():
    seen = {}

    def deliver(card):
        seen.update(card)
        ch.submit_reply(card["request_id"], True, True)  # 越权回传 remember

    shared = set()
    ch = approval_mod.GatewayApproval(deliver_card=deliver, timeout=1.0,
                                      session_allowed_tools=shared)
    ok, reason = ch.request("delete_file", 5, {"path": "旧讲义.md"})
    assert seen["is_danger"] is True and seen["allow_remember"] is False
    assert (ok, reason) == (True, "用户批准单次执行")
    assert shared == set()


def test_gateway_plan_card_has_checkpoint_hint(tmp_path):
    target = tmp_path / "示例讲义.md"
    target.write_text("原始内容", encoding="utf-8")
    seen = {}

    def deliver(card):
        seen.update(card)
        ch.submit_reply(card["request_id"], True, False)

    ch = approval_mod.GatewayApproval(
        deliver_card=deliver, timeout=1.0,
        checkpoint_fn=lambda rel: str(tmp_path / ".checkpoint" / "ckpt_1" / "示例讲义.md"))
    ok, reason = ch.request_plan("write_file", 1, {"path": "示例讲义.md"})
    assert (ok, reason) == (True, "用户批准 Plan 模式执行变更")
    assert seen["is_plan"] is True
    assert "Checkpoint 安全快照已创建" in seen["checkpoint_hint"]


def test_gateway_wait_reply_exception_denies():
    def _boom(_rid, _timeout):
        raise RuntimeError("模拟传输层崩溃")

    ch = approval_mod.GatewayApproval(deliver_card=lambda card: None, wait_reply=_boom, timeout=1.0)
    ok, reason = ch.request("fetch_url", 4, {})
    assert ok is False and "超时" in reason


def test_gateway_pending_registry_is_cleaned_up():
    """请求结束后不得在 pending 注册表里留下悬挂条目。"""
    ch = approval_mod.GatewayApproval(deliver_card=lambda card: None, timeout=0.05)
    ch.request("fetch_url", 4, {})
    assert ch._pending == {}


# ────────────────── 4. 超时配置解析（agent.approval_timeout） ──────────────────


@pytest.mark.parametrize("raw,want", [
    (None, approval_mod.DEFAULT_APPROVAL_TIMEOUT),
    (30, 30.0),
    ("45", 45.0),
    ("0", approval_mod.DEFAULT_APPROVAL_TIMEOUT),
    (-5, approval_mod.DEFAULT_APPROVAL_TIMEOUT),
    ("60秒", approval_mod.DEFAULT_APPROVAL_TIMEOUT),
    ([], approval_mod.DEFAULT_APPROVAL_TIMEOUT),
])
def test_resolve_approval_timeout(raw, want):
    cfg = {"agent": {"approval_timeout": raw}}
    assert approval_mod.resolve_approval_timeout(cfg) == want


def test_resolve_approval_timeout_tolerates_bad_config_shapes():
    for cfg in (None, {}, [], "x", {"agent": None}, {"agent": "x"}):
        assert approval_mod.resolve_approval_timeout(cfg) == approval_mod.DEFAULT_APPROVAL_TIMEOUT


def test_default_approval_timeout_is_120s():
    assert approval_mod.DEFAULT_APPROVAL_TIMEOUT == 120.0
