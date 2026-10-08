# -*- coding: utf-8 -*-
"""2026-10-05 GUI / Web 看板体验修复批次回归（F3）

覆盖本批 10 项已核实缺陷（第 10 项并入看板模板用例）+ 1 项收口修复：

  1. GUI 审批「本会话记住」跨消息失效 —— MainWindow 持进程级信任集并透传
     AgentWorker → GuiApproval → PermissionManager（三处共享同一 set 对象）；
  2. GUI 无法中断进行中的回答 —— 停止按钮 + 取消检查回调抛 AgentCancelled
     （AgentWorker 显式捕获）+ closeEvent 全局 5s 等待上限；
  3. 看板倒计时不重建就过期 —— 模板渐进增强 JS 按本地日期重算 hero/title/
     aria-label/sr-only；构建期注入值保持不变；
  4. 手机端指引指向脱敏快照 —— 操作手册/README/更新看板.bat 统一指向
     docs/.local/（公开快照仅作分享）；
  5. 错题盲盒只抽专业课 —— 组卷前可选科目（不考数学时不出现数学）；
  6. 考试日过后倒计时为负 —— build.py clamp 到 0；
  7. 正常空态被标「执行异常」—— 未组卷/用户取消不再进失败通道；
  8. GUI.bat 首次静默 pip 安装 —— 补等待提示；
  9. 缺文件静默回落模板 —— 模板回落仅限脱敏发布模式，真实工作区走空态；
 10. 页签切换 pushState（手机返回键先回上个页签）；
 11. 错题卡长围栏解析 —— dashboard.py 复用 extract_mistake_stem（与写入侧同口径）。

运行（必须带 basetemp）：
  py -m pytest tests/test_fix_20261005_gui_web.py \
      --basetemp=C:/Users/29652/AppData/Local/Temp/ky_pt_f3 -p no:cacheprovider
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
for _p in (str(ROOT), str(ROOT / "tools"), str(DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 看板侧（只读构建，不落盘）
import build as build_mod  # noqa: E402
from web.markdown import read as md_read  # noqa: E402

from tools.action_status import output_has_failure  # noqa: E402
from tools.gui.workers.intel_worker import IntelTaskWorker  # noqa: E402

PYSIDE_AVAILABLE = True
try:
    from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - 无 PySide6 的环境
    PYSIDE_AVAILABLE = False


@pytest.fixture(scope="module")
def qt_app():
    """离屏 Qt 应用（无真实显示器）。缺 PySide6 / 平台插件时整组跳过。"""
    if not PYSIDE_AVAILABLE:
        pytest.skip("未安装 PySide6，跳过 GUI 用例")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
    except Exception as exc:  # pragma: no cover - 平台插件缺失
        pytest.skip(f"Qt 平台插件不可用，跳过 GUI 用例: {exc}")
    return app


def _make_main_window(qt_app, monkeypatch, workspace):
    """构造离屏 MainWindow；禁用「未配置 → 自动弹引导向导」定时器。"""
    from tools.gui import services
    from tools.gui.main_window import MainWindow

    monkeypatch.setattr(services, "is_unconfigured", lambda *a, **k: False, raising=False)
    return MainWindow(workspace_root=workspace)


# ══════════════════════════════════════════════════════════════════
# 1. GUI 审批「本会话记住」跨消息共享
# ══════════════════════════════════════════════════════════════════


if PYSIDE_AVAILABLE:
    class _ApprovalProbe(QThread):
        """在真实 worker 线程里调用审批通道（复现 AgentWorker 的调用现场）。"""

        done = Signal(object)

        def __init__(self, channel, tool_name, level, args):
            super().__init__()
            self.channel = channel
            self.tool_name = tool_name
            self.level = level
            self.args = args

        def run(self):
            try:
                res = self.channel.request(self.tool_name, self.level, self.args)
            except BaseException as exc:  # pragma: no cover - 只有回归才会走到
                res = ("EXC", repr(exc))
            self.done.emit(res)


#: 跨线程 Qt 对象保活（防 CI 段错误，同 test_a3b_gui_approval 的教训）
_KEEPALIVE: list = []


def _drive_approval(channel, tool_name="fetch_url", level=4, args=None, guard_ms=8000):
    """worker 线程发审批请求 + 主线程跑事件循环收结果（确定性）。"""
    loop = QEventLoop()
    result = {}

    class _Collector(QObject):
        def __init__(self):
            super().__init__()

        def on_done(self, res):
            result["res"] = res
            loop.quit()

    collector = _Collector()
    worker = _ApprovalProbe(channel, tool_name, level,
                            args if args is not None else {"url": "https://example.test"})
    worker.done.connect(collector.on_done)
    QTimer.singleShot(guard_ms, loop.quit)
    worker.start()
    loop.exec()
    worker.wait(3000)
    _KEEPALIVE.append((worker, collector, loop))
    assert "res" in result, "审批通道未在保护时间内返回（疑似阻塞）"
    return result["res"]


def test_agent_worker_shares_session_allowed_tools_set(qt_app, tmp_path):
    """AgentWorker 必须把调用方传入的信任集**原样**转给 GuiApproval（不拷贝）。"""
    from tools.gui.workers.agent_worker import AgentWorker

    shared = {"write_file"}  # 预置内容，验证不是新建空集
    worker = AgentWorker(config={}, user_input="你好", workspace_root=tmp_path,
                         session_allowed_tools=shared)
    assert worker._approval_channel is not None
    assert worker._approval_channel.session_allowed_tools is shared

    # 未传时回落独立空集（既有行为不变）
    worker2 = AgentWorker(config={}, user_input="你好", workspace_root=tmp_path)
    assert worker2._approval_channel.session_allowed_tools == set()
    assert worker2._approval_channel.session_allowed_tools is not shared


def test_remember_in_first_message_skips_prompt_in_second(qt_app, tmp_path):
    """缺陷现场：勾选「本会话记住」后，第二条消息不得再弹审批框。

    模拟 MainWindow 持集 → 两条消息各建一个 AgentWorker（共享同一 set）：
      * 第 1 条：用户在弹窗里批准并勾选 remember → 工具名写进共享集；
      * 第 2 条：PermissionManager 直接命中「会话已永久信任」，通道零调用。
    """
    from agent.permissions import PermissionManager
    from tools.gui.workers.agent_worker import AgentWorker

    shared: set = set()
    worker1 = AgentWorker(config={}, user_input="第一次", workspace_root=tmp_path,
                          session_allowed_tools=shared)
    worker2 = AgentWorker(config={}, user_input="第二次", workspace_root=tmp_path,
                          session_allowed_tools=shared)
    assert worker1._approval_channel.session_allowed_tools is shared
    assert worker2._approval_channel.session_allowed_tools is shared

    # 第 1 条消息：真实走通道（离屏注入对话框答复：批准 + 本会话记住）
    ch1 = worker1._approval_channel
    ch1._dialog_runner = lambda req: {"approved": True, "remember": True}
    res1 = _drive_approval(ch1, "fetch_url", 4, {"url": "https://example.test/x"})
    assert res1 == (True, "用户批准本会话永久信任此工具")
    assert shared == {"fetch_url"}, "remember 未写入共享信任集"

    # 第 2 条消息：同集 → 策略层直接放行，通道不得再被调用
    asked = []
    ch2 = worker2._approval_channel
    ch2._dialog_runner = lambda req: (asked.append(req), {"approved": True})[1]
    pm = PermissionManager(mode="ask", workspace_root=tmp_path, approval_channel=ch2)
    assert pm.session_allowed_tools is shared
    assert pm.check_permission("fetch_url", 4, {"url": "https://example.test/y"}) == (
        True, "会话已永久信任此工具")
    assert asked == [], "第二条消息仍弹了审批框（本会话记住未跨消息生效）"


def test_main_window_holds_and_passes_process_session_set(qt_app, tmp_path, monkeypatch):
    """MainWindow 持进程级信任集：连续两条消息的两个 worker 共享同一 set 对象。"""
    import tools.gui.workers.agent_worker as aw_mod

    created = []

    class _SpyAgentWorker(QObject):
        chunk_signal = Signal(str)
        step_signal = Signal(str)
        finished_signal = Signal(str)
        finished = Signal()
        session_ran_signal = Signal()

        def __init__(self, config, user_input, timeout=None,
                     workspace_root=None, session_allowed_tools=None):
            super().__init__()
            self.session_allowed_tools = session_allowed_tools
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

        def isRunning(self):  # noqa: N802 - 对齐 Qt 命名
            return False

        def cancel(self):
            pass

    monkeypatch.setattr(aw_mod, "AgentWorker", _SpyAgentWorker)

    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    try:
        assert isinstance(win._session_allowed_tools, set)
        win.input_box.setPlainText("第一条")
        win._on_send_message()
        win.input_box.setPlainText("第二条")
        win._on_send_message()

        assert len(created) == 2, f"应创建 2 个 worker，实际 {len(created)}"
        assert all(w.started for w in created)
        assert all(w.session_allowed_tools is win._session_allowed_tools for w in created), \
            "AgentWorker 未拿到窗口的进程级信任集（跨消息审批记忆会失效）"
    finally:
        win.close()


# ══════════════════════════════════════════════════════════════════
# 2. GUI 中断进行中的回答
# ══════════════════════════════════════════════════════════════════


class _SlowFakeRunner:
    """慢速 AgentRunner 替身：每步回调一次，回调抛异常即中断。"""

    instances: list = []

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self._closed = False
        self.steps_done = 0
        _SlowFakeRunner.instances.append(self)

    def run(self, user_input, interactive=False):  # noqa: ARG002
        for i in range(200):
            self.step_callback(f"step {i}")
            self.steps_done += 1
            time.sleep(0.001)
        return "假装跑完了"

    def close(self):
        self._closed = True


class _QuietFakeRunner(_SlowFakeRunner):
    """不调用任何回调的 runner：验证「取消 + 正常返回」仍报已取消。"""

    instances: list = []

    def run(self, user_input, interactive=False):  # noqa: ARG002
        self.steps_done += 1
        return "正常返回的回复"


def _patch_runner(monkeypatch, fake_cls):
    import importlib
    patched = 0
    for name in ("agent.loop", "tools.agent.loop"):
        try:
            mod = importlib.import_module(name)
        except ImportError:  # pragma: no cover
            continue
        monkeypatch.setattr(mod, "AgentRunner", fake_cls)
        patched += 1
    assert patched >= 1


def test_cancel_stops_run_at_next_callback_boundary(qt_app, tmp_path, monkeypatch):
    """点停止后，回调包装在下一边界抛 AgentCancelled：run 不再继续、有收尾。

    同步驱动（不真起线程，确定性）：第一条 step 信号到达即调 cancel()，
    模拟慢速 run 的第二步必须抛异常终止；runner.close() 仍被调用。
    """
    from tools.gui.workers.agent_worker import AgentWorker

    _SlowFakeRunner.instances = []
    _patch_runner(monkeypatch, _SlowFakeRunner)

    worker = AgentWorker(config={}, user_input="帮我分析这道题", workspace_root=tmp_path)
    worker.step_signal.connect(lambda _t: worker.cancel())   # 第一个步骤边界即点停止
    replies: list = []
    worker.finished_signal.connect(replies.append)

    worker.run()          # 同步执行；若取消无效会跑满 200 步

    assert replies == ["[已取消]: 已停止本轮回答。"], replies
    fake = _SlowFakeRunner.instances[-1]
    assert fake.steps_done == 1, f"取消后仍在继续执行（steps={fake.steps_done}）"
    assert fake._closed, "取消路径也必须 runner.close() 收尾会话日志"


def test_cancel_before_run_reports_cancelled(qt_app, tmp_path, monkeypatch):
    """先取消再执行：直接走早退路径，报告已取消且不构造 runner。"""
    from tools.gui.workers.agent_worker import AgentWorker

    _QuietFakeRunner.instances = []
    _patch_runner(monkeypatch, _QuietFakeRunner)

    worker = AgentWorker(config={}, user_input="随便问一句", workspace_root=tmp_path)
    worker.cancel()
    replies: list = []
    worker.finished_signal.connect(replies.append)
    worker.run()
    assert replies and replies[0].startswith("[已取消]")


def test_stop_button_exists_and_restores_sendable_state(qt_app, tmp_path, monkeypatch):
    """停止按钮：点击 → cancel() + 立即恢复可发送（agent_worker 置 None）。"""
    import tools.gui.workers.agent_worker as aw_mod

    created = []

    class _SpyAgentWorker(QObject):
        chunk_signal = Signal(str)
        step_signal = Signal(str)
        finished_signal = Signal(str)
        finished = Signal()
        session_ran_signal = Signal()

        def __init__(self, config, user_input, timeout=None,
                     workspace_root=None, session_allowed_tools=None):
            super().__init__()
            created.append(self)
            self.cancelled = False

        def start(self):
            pass

        def isRunning(self):  # noqa: N802
            return True

        def cancel(self):
            self.cancelled = True

    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    try:
        assert hasattr(win, "stop_btn"), "聊天区缺少停止按钮"
        assert win.stop_btn.text() == "停止"

        win.agent_worker = _SpyAgentWorker({}, "x")
        # [P2 修复·2026-10-08 适配] 停止按钮现仅在生成期间可点（状态联动）；
        # 真实流程由 _on_send_message 置位，此处直接构造「流式中途」运行态。
        win._set_agent_ui_running(True)
        win.stop_btn.click()
        fake = win.agent_worker  # click 后已被置 None，取引用经闭包
        assert created[-1].cancelled, "点击停止未调用 worker.cancel()"
        assert win.agent_worker is None, "停止后必须立即允许发送下一条消息"
        assert "已停止本轮回答" in win.chat_display.toPlainText()

        # 停止后再次发送：不得再被「私教仍在思考中」拦住
        monkeypatch.setattr(aw_mod, "AgentWorker", _SpyAgentWorker)
        win.input_box.setPlainText("停止后的新问题")
        win._on_send_message()
        assert len(created) == 2, "停止后无法发送新消息"
    finally:
        win.close()


def test_close_event_uses_bounded_five_second_deadline(qt_app, tmp_path, monkeypatch):
    """closeEvent：cancel + 带超时等待；总等待 ≤5s（放宽但不无限）。"""
    class _StuckWorker:
        def __init__(self):
            self.cancelled = False
            self.quitted = False
            self.wait_ms: list = []

        def cancel(self):
            self.cancelled = True

        def isRunning(self):  # noqa: N802
            return True

        def quit(self):
            self.quitted = True

        def wait(self, ms):  # noqa: N802
            self.wait_ms.append(int(ms))
            return False       # 永不结束：模拟 LLM 请求在途

    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    stuck = _StuckWorker()
    win._worker_refs.append(stuck)
    win.close()

    assert stuck.cancelled, "关窗必须先 cancel"
    assert stuck.quitted
    assert stuck.wait_ms, "关窗必须带超时等待线程"
    assert sum(stuck.wait_ms) <= 5000, f"关窗等待超过 5s 上限: {stuck.wait_ms}"
    assert stuck.wait_ms[0] > 2000, f"等待上限未放宽（旧值 2000ms）: {stuck.wait_ms}"


# ══════════════════════════════════════════════════════════════════
# 3/6/10. 看板：倒计时重算 + clamp + pushState
# ══════════════════════════════════════════════════════════════════


def test_template_has_countdown_recompute_and_history_api():
    """模板必须含：客户端倒计时重算（用注入考期）与 pushState/popstate。"""
    tpl = (DASHBOARD / "web" / "template.html").read_text(encoding="utf-8")
    # 倒计时重算脚本
    assert "倒计时客户端重算" in tpl, "模板缺少倒计时重算脚本"
    assert "'{{EXAM_YEAR}}-{{EXAM_MMDD}}'" in tpl, "重算脚本未消费构建期注入的考期"
    assert "hero-num" in tpl and "querySelector('.hero-num')" in tpl, \
        "重算脚本未更新 hero 大数字"
    # 返回键历史 API
    assert "history.pushState" in tpl, "页签切换未接入 pushState"
    assert "addEventListener('popstate'" in tpl, "缺少 popstate 还原监听"
    assert "noPush" in tpl, "恢复上次页签/popstate 需要 noPush 防返回键死循环"


def test_built_html_keeps_injected_dday_and_recompute_script(monkeypatch):
    """构建期 DDAY1 仍被注入（产物断言不破坏），同时带上重算脚本。"""
    monkeypatch.setattr(build_mod, "snapshot_opt_in", lambda: True)  # 免真实工作区写盘
    html, _data, _warns, _secs = build_mod.build(offline=True)

    days = (build_mod.EXAM_DAY1 - datetime.date.today()).days
    days = max(0, days)
    assert f'<span class="hero-num">{days}</span>' in html, \
        f"构建期倒计时未注入（期望 {days}）"
    assert f"倒计时 {days} 天</title>" in html, "title 未注入构建期倒计时"
    assert "倒计时客户端重算" in html, "产物缺少客户端重算脚本"
    assert "{{DDAY1}}" not in html and "{{EXAM_YEAR}}" not in html


def test_build_clamps_past_exam_date_to_zero(monkeypatch):
    """初试日已过：hero/title 显示 0，不得出现负数（GUI/CLI 同口径）。"""
    monkeypatch.setattr(build_mod, "snapshot_opt_in", lambda: True)
    monkeypatch.setattr(build_mod, "EXAM_DAY1", datetime.date(2020, 12, 19))
    monkeypatch.setattr(build_mod, "EXAM_DATE", datetime.date(2020, 12, 20))
    monkeypatch.setattr(build_mod, "PLAN_START", datetime.date(2020, 1, 1))

    html, _data, _warns, _secs = build_mod.build(offline=True)
    assert '<span class="hero-num">0</span>' in html, "过期考期未 clamp 到 0"
    assert "倒计时 0 天</title>" in html
    assert "倒计时 -" not in html, "产物仍存在负数倒计时"


def test_built_scripts_pass_node_syntax_check(monkeypatch, tmp_path):
    """产物内联脚本必须通过 node --check（本机无 node 时跳过）。"""
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 Node.js，跳过 JS 语法校验")
    monkeypatch.setattr(build_mod, "snapshot_opt_in", lambda: True)
    html, _d, _w, _s = build_mod.build(offline=True)
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert blocks
    for i, block in enumerate(blocks):
        f = tmp_path / f"block_{i}.js"
        f.write_text(block, encoding="utf-8")
        proc = subprocess.run([node, "--check", str(f)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        assert proc.returncode == 0, f"第 {i} 个内联脚本语法错误:\n{proc.stderr}"


# ══════════════════════════════════════════════════════════════════
# 9. 缺文件静默回落模板 → 真实工作区走空态
# ══════════════════════════════════════════════════════════════════


def test_markdown_read_fallback_flag(tmp_path):
    """allow_fallback=False 时绝不读模板；默认（发布/演示）保留回落能力。"""
    real = tmp_path / "薄弱点雷达.md"
    tpl = tmp_path / "薄弱点雷达.template.md"
    tpl.write_text("# 模板雷达\n\n示例内容\n", encoding="utf-8")

    assert md_read(real, allow_fallback=False) is None, \
        "真实工作区缺文件时仍回落到模板（会显示假雷达）"
    assert "模板雷达" in (md_read(real) or ""), "默认回落能力被误删（公开演示需要）"


def test_build_template_fallback_flag_follows_build_mode(monkeypatch):
    """build() 按模式传参：脱敏发布=True（保留演示），本地完整=False（空态）。"""
    import tools.study_planner as sp

    monkeypatch.setattr(sp, "refresh_stale_today_tasks", lambda **kw: {}, raising=False)
    calls: list = []
    real_read = build_mod.read

    def spy(p, **kw):
        calls.append(kw)
        return real_read(p, allow_fallback=False)

    monkeypatch.setattr(build_mod, "read", spy)

    monkeypatch.setattr(build_mod, "snapshot_opt_in", lambda: True)
    build_mod.build(offline=True)
    assert calls and all(kw.get("allow_fallback") is True for kw in calls), \
        "脱敏发布模式丢失模板回落（公开演示页会变空）"

    calls.clear()
    monkeypatch.setattr(build_mod, "snapshot_opt_in", lambda: False)
    build_mod.build(offline=True)
    assert calls and all(kw.get("allow_fallback") is False for kw in calls), \
        "本地完整模式仍允许模板回落（真实工作区会显示假任务/假雷达）"


# ══════════════════════════════════════════════════════════════════
# 5. 错题盲盒可选科目
# ══════════════════════════════════════════════════════════════════


def test_error_quiz_subject_is_selectable(qt_app, tmp_path, monkeypatch):
    """组卷前弹科目选择：选中英语 → worker 收到 subject="eng"（不再固定 pro）。"""
    import tools.gui.workers.intel_worker as iw_mod
    import tools.gui.main_window as mw_mod

    captured = []

    class _SpyIntelWorker(QObject):
        log_signal = Signal(str)
        finished_signal = Signal(str, str)
        error_signal = Signal(str)
        finished = Signal()

        def __init__(self, task_type, workspace_root, params):
            super().__init__()
            captured.append((task_type, dict(params)))

        def start(self):
            pass

        def isRunning(self):  # noqa: N802
            return False

        def cancel(self):
            pass

    monkeypatch.setattr(iw_mod, "IntelTaskWorker", _SpyIntelWorker)
    monkeypatch.setattr(mw_mod.QInputDialog, "getItem",
                        lambda *a, **k: ("英语", True))

    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    try:
        win._generate_error_quiz()
        assert captured == [("error_quiz", {"subject": "eng"})], captured
    finally:
        win.close()


def test_error_quiz_cancel_creates_no_worker(qt_app, tmp_path, monkeypatch):
    """用户在科目选择框点取消 → 不启动组卷，给出可辨识提示。"""
    import tools.gui.workers.intel_worker as iw_mod
    import tools.gui.main_window as mw_mod

    captured = []

    class _SpyIntelWorker(QObject):
        log_signal = Signal(str)
        finished_signal = Signal(str, str)
        error_signal = Signal(str)
        finished = Signal()

        def __init__(self, *a, **k):
            captured.append((a, k))

    monkeypatch.setattr(iw_mod, "IntelTaskWorker", _SpyIntelWorker)
    monkeypatch.setattr(mw_mod.QInputDialog, "getItem", lambda *a, **k: ("", False))

    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    try:
        win._generate_error_quiz()
        assert captured == [], "取消科目选择后仍启动了组卷"
        assert "已取消" in win.chat_display.toPlainText()
    finally:
        win.close()


def test_quiz_subject_options_hide_math_when_disabled(qt_app, tmp_path, monkeypatch):
    """不考数学的备考方案下，科目选项不得出现数学。"""
    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    try:
        win.config = {"study_plan": {"math_key": "none", "math_name": "不考数学"}}
        keys = [k for k, _ in win._quiz_subject_options()]
        assert "math" not in keys, "不考数学仍出现数学选项"
        assert {"pro", "eng", "pol"} <= set(keys)
    finally:
        win.close()


# ══════════════════════════════════════════════════════════════════
# 7. 正常空态不再判「执行异常」
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("text", [
    "[!] 未组卷：本地暂无可用题源（按上方 3 步启动闭环指引操作）。",
    "[!] 双校对标已取消：未指定第一所高校。",
    "[!] 未指定【新考纲】文件路径，已取消本次比对。",
    "[!] 双校对标需要指定第一所高校，未输入已取消。",
    "[i] 切片入库已取消：未选择待切片文件。",
])
def test_normal_empty_states_are_not_failures(text):
    """未组卷 / 用户取消 / 未输入 → 正常空态，不得进失败通道。"""
    assert output_has_failure(text) is False, f"正常空态被误判为失败: {text!r}"


@pytest.mark.parametrize("text", [
    "[×] 组卷异常: boom",
    "[!] 执行过程中发生异常: 连接失败",
    "[!] 双校对标已取消，但执行过程中发生异常: x",   # 混入硬故障词 → 仍判失败
    "\x1b[91m[!] 执行过程中发生异常: 连接失败\x1b[0m",
])
def test_real_failures_still_detected(text):
    """阴性对照：真实故障（含「取消 + 异常」混合行）仍必须判失败。"""
    assert output_has_failure(text) is True, f"真实故障漏判: {text!r}"


def test_intel_worker_routes_empty_state_to_finished(tmp_path):
    """IntelTaskWorker：未组卷输出走 finished_signal，不再显示 [×] 执行异常。"""
    worker = IntelTaskWorker("action", tmp_path, {"alias": "compose"})
    got: list = []
    worker.finished_signal.connect(lambda out, saved: got.append(("ok", out)))
    worker.error_signal.connect(lambda err: got.append(("err", err)))

    worker._emit_result("[!] 未组卷：本地暂无可用题源（按上方 3 步启动闭环指引操作）。")
    assert got and got[0][0] == "ok", f"正常空态被路由到错误通道: {got}"

    got.clear()
    worker._emit_result("[×] 组卷异常: boom")
    assert got and got[0][0] == "err", "真实异常必须走错误通道"


# ══════════════════════════════════════════════════════════════════
# 11. GUI 错题卡长围栏解析（与写入侧同一口径）
# ══════════════════════════════════════════════════════════════════


def test_error_queue_cards_parses_long_fence_stem(tmp_path):
    """题干含 ``` 的错题卡（写侧自动加长围栏）在 GUI 侧不得被截断。

    缺陷现场：dashboard.py 旧实现用固定 3 反引号正则，只能吃到题干
    内容里的第一个 ```，把长围栏卡片的题干截成半截。修复复用
    question_source.extract_mistake_stem（纯函数，与 error_logger 写入侧
    同一口径）。本用例同时给出阴性对照：旧正则在同一载荷上必然截断。
    """
    from tools.gui.services import dashboard as dash_mod

    stem = "题干内容含 ``` 三反引号\n第二行：仍属于题干"
    mistake_dir = tmp_path / "04-专业课" / "错题本"
    mistake_dir.mkdir(parents=True)
    card = (
        "## 📌 [2026-10-05] 长围栏错题\n"
        "- **掌握状态**：`[待复测]` (FSRS 自适应复测中)\n"
        "- **错因分类**：`概念漏洞` (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分)\n"
        "- **题干设问**：\n"
        "````text\n"
        f"{stem}\n"
        "````\n"
        "- **复测节奏**：`stage=0` · 下次到期 `2026-10-06`（距今日 1 天 · FSRS good 评级自适应）\n"
        "---\n"
    )
    (mistake_dir / "错题本.md").write_text(card, encoding="utf-8")

    cards = dash_mod.error_queue_cards(tmp_path)
    assert len(cards) == 1, cards
    assert cards[0]["question"] == stem, \
        f"长围栏题干被截断: {cards[0]['question']!r}"

    # 阴性对照：旧固定 3 反引号正则在同一载荷上必然截断（证明用例有区分度）
    old = dash_mod._first_group(dash_mod._ERROR_QUESTION_RE, card)
    assert old != stem, "旧正则竟与新实现同结果——阴性对照失效"
    assert "题干内容含" in old, f"旧正则未匹配到题干（对照构造有误）: {old!r}"
    assert "第二行" not in old, f"旧正则竟未截断（对照失效）: {old!r}"

    # 兼容红线：普通卡（3 反引号）新旧实现结果一致
    plain = "题干普通一行"
    card3 = card.replace("````text", "```text").replace(f"{stem}\n````", f"{plain}\n```")
    (mistake_dir / "错题本.md").write_text(card3, encoding="utf-8")
    cards3 = dash_mod.error_queue_cards(tmp_path)  # 指纹已变 → 重扫
    assert len(cards3) == 1
    assert cards3[0]["question"] == plain, \
        f"普通 3 反引号卡解析行为改变: {cards3[0]['question']!r}"
    assert dash_mod._first_group(dash_mod._ERROR_QUESTION_RE, card3) == plain


# ══════════════════════════════════════════════════════════════════
# 4/8. 指引文档与 bat 提示
# ══════════════════════════════════════════════════════════════════


def test_manual_and_readmes_point_to_local_full_dashboard():
    """指引统一指向 docs/.local/（完整版），公开快照仅作分享。"""
    manual = (ROOT / "操作手册.md").read_text(encoding="utf-8")
    assert "http://192.168.1.100:8080/.local/" in manual, \
        "操作手册手机端指引未指向本地完整版"
    assert "公开脱敏快照" in manual and "docs/.local/index.html" in manual

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/.local/index.html" in readme, "README 未指引本地完整版"

    dash_readme = (DASHBOARD / "README.md").read_text(encoding="utf-8")
    assert "docs/.local/index.html" in dash_readme, "看板 README 未指引本地完整版"
    assert "http://192.168.1.5:8080/.local/" in dash_readme


def test_bat_hints_are_byte_safe_and_informative():
    """更新看板.bat / GUI.bat：GBK 编码保持 + 新增提示可见。"""
    root_bat = (ROOT / "更新看板.bat").read_bytes()
    text = root_bat.decode("gbk")
    assert "docs\\.local\\index.html" in text, "更新看板.bat 未提示本地完整版路径"
    assert ":8080/.local/" in text, "更新看板.bat 未提示手机端完整版地址"
    assert "考研学习链".encode("gbk") in root_bat, "根 bat 编码被破坏（应为 GBK）"

    gui_bat = (ROOT / "GUI.bat").read_bytes().decode("gbk")
    assert "正在安装图形依赖（约 1-3 分钟，请耐心等待" in gui_bat, \
        "GUI.bat 首次 pip 安装缺少等待提示"
    assert "考研学习链".encode("gbk") in (ROOT / "GUI.bat").read_bytes()

    inner = (DASHBOARD / "更新看板.bat").read_text(encoding="utf-8")
    assert "docs/.local/index.html" in inner, "内层更新看板.bat 未同步提示"
