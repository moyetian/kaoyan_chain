# -*- coding: utf-8 -*-
"""
GUI 离屏冒烟测试（不需要真实显示器，CI 可跑）

覆盖目标：GUI 此前**零自动化测试**，而本轮改动的正是最容易改崩的地方
（内联样式改 objectName、倒计时提为实例属性、主题改为 token 编译、数据取用共享层）。
这里用 ``QT_QPA_PLATFORM=offscreen`` 起一个真实 QApplication + MainWindow，
断言的是「行为」而不是「代码长什么样」：

  * 构造后应用的主题确实来自编译器（而不是某个手写文件）
  * 窗口内不存在写死颜色的内联样式（否则浅色主题必被压过）
  * 倒计时是实例属性且能被刷新
  * 切换主题真的会改变样式表，且明暗两套渲染结果不同
  * 三条数据链路（今日/错题/情报）都能取到文本
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 冒烟测试")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from tools.gui import services, theme_apply  # noqa: E402
from tools.gui.main_window import MainWindow  # noqa: E402
from tools.theme import build_theme, render_qss  # noqa: E402


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst
    inst.setStyleSheet("")


@pytest.fixture()
def win(app):
    w = MainWindow()
    yield w
    w.close()


# ── 主题 ────────────────────────────────────────────────────────

def test_window_constructs_and_applies_compiled_theme(win, app):
    """窗口构造后应已应用编译器【现场渲染】的样式表。

    改造前样式表来自 tools/gui/theme/dark.qss（一份手写副本），
    且亮色主题被控件内联深色压过 —— 此处直接与渲染结果比对。
    """
    applied = app.styleSheet()
    assert applied, "构造窗口后应已应用主题样式表"
    dark = render_qss(build_theme("dark"))
    light = render_qss(build_theme("light"))
    assert applied in (dark, light), "应用的样式表必须来自 tools/theme 编译产物"


def test_no_widget_hardcodes_colors(win):
    """窗口内不得存在写死颜色的内联样式。

    这是「浅色主题是坏的」的根因：widget 自身样式表优先级高于
    QApplication.setStyleSheet，任何内联色值都会把主题压过去。
    """
    offenders = [(w.objectName() or type(w).__name__, w.styleSheet()[:60])
                 for w in win.findChildren(QWidget)
                 if w.styleSheet() and "#" in w.styleSheet()]
    assert not offenders, f"存在写死颜色的内联样式: {offenders}"


def test_theme_toggle_changes_stylesheet(win, app):
    before_name = win._theme.name
    before_qss = app.styleSheet()
    win._toggle_theme()
    assert win._theme.name != before_name, "切换后主题应变化"
    assert app.styleSheet() != before_qss, "切换主题必须真的改变样式表"
    win._toggle_theme()          # 切回，避免影响其它用例


def test_theme_toggle_persists_choice(win, app):
    """[缺陷修复] 改造前 `_current_theme` 每次启动写死 dark，重启即回退。"""
    win._toggle_theme()
    expected = win._theme.name
    remembered = theme_apply.read_prefs().get("preset")
    assert remembered == expected, f"切换后的预设应写入 QSettings，实际 {remembered}"
    win._toggle_theme()


def test_hardcoded_font_is_gone():
    """字体族不再硬编码 "Microsoft YaHei"（Linux/macOS 上并不存在该字体）。"""
    src = (ROOT / "tools" / "ky_gui.py").read_text(encoding="utf-8")
    assert 'QFont("Microsoft YaHei"' not in src
    assert "font-scale" in src, "字号应随主题 token 缩放"


# ── SVG 图标 ──────────────────────────────────────────────────

def test_svg_icon_templates_are_valid():
    """所有 SVG 模板必须含 {COLOR} 占位符且可被 QSvgRenderer 解析。"""
    from tools.gui.widgets.icons import SVG_TEMPLATES, _format_svg, _HAS_SVG
    assert len(SVG_TEMPLATES) >= 15, f"图标数应 >=15，实际 {len(SVG_TEMPLATES)}"
    for key, tmpl in SVG_TEMPLATES.items():
        assert "{COLOR}" in tmpl, f"图标 {key} 缺少 {{COLOR}} 占位符"
        assert tmpl.startswith("<svg"), f"图标 {key} 不是合法 SVG 根"
        assert tmpl.endswith("</svg>"), f"图标 {key} 未正确闭合"
        # 格式化后的字符串不含花括号
        formatted = _format_svg(key, "#a78bfa")
        assert "{COLOR}" not in formatted


def test_render_icon_produces_pixmap(app):
    """render_icon 在有 QApplication 时返回非空 QPixmap。"""
    if not __import__("tools.gui.widgets.icons", fromlist=["_HAS_SVG"])._HAS_SVG:
        pytest.skip("QtSvg 不可用")
    from tools.gui.widgets.icons import render_icon
    pm = render_icon("today", 20, "#a78bfa")
    assert not pm.isNull()
    assert pm.width() == 20 and pm.height() == 20


def test_icon_label_return_annotation_resolves():
    """[P9 回归] ``icon_label`` 的返回注解 ``-> "QLabel"`` 必须能在模块作用域解析。

    修复前 QLabel **只在函数体内局部导入**，模块级没有这个名字 →
    ``typing.get_type_hints(icon_label)`` 抛 ``NameError: name 'QLabel' is not
    defined``（静态检查报 F821），而这是全仓唯一一处此类注解（其余 ``-> "X"``
    里的 X 都能在模块作用域解析）。

    阴性对照：把 ``tools/gui/widgets/icons.py`` 里模块级的 QLabel 受守卫导入去掉，
    本用例必须变红。
    """
    import typing

    from tools.gui.widgets.icons import icon_label

    hints = typing.get_type_hints(icon_label)
    assert hints.get("return") is not None, (
        "返回注解未解析为真实类型（QLabel 不在模块作用域？）")


def test_function_card_uses_svg_not_emoji(win):
    """功能卡片标题行不应再含 emoji 字符，应有 SVG pixmap 图标。"""
    card = win._feature_buttons[0]
    # 卡片图标 label 应有 pixmap（而非文本 emoji）
    pm = card._icon_label.pixmap()
    assert pm is not None and not pm.isNull(), "卡片图标应为 SVG pixmap"
    # 卡片标题不应以 emoji 开头
    titles = [c.findChild(type(card._icon_label)) for c in win._feature_buttons]
    # 遍历卡片，确认标题文本不含 emoji 区段
    from tools.gui.views.function_cards import CARD_ITEMS
    for _icon, title, _desc, _alias in CARD_ITEMS:
        # icon 字段应为 SVG key（纯 ASCII），不是 emoji 字符
        assert len(_icon) <= 20 and _icon.isascii(), \
            f"图标键应为 ASCII key，非 emoji: {_icon!r}"


def test_settings_dialog_writes_and_applies_l2(app, tmp_path):
    """设置面板写入 QSettings 并即时应用主题。

    [P27 隔离修复] ``SettingsDialog`` 的落盘路径取自 ``win.workspace_root``
    （``SettingsDialog.__init__`` 里 ``self.config_file = workspace_root /
    "ky_config.json"``），而 ``MainWindow()`` 默认把 workspace_root 解析为
    **真实仓库根**。因此 ``dlg._apply()`` 会直写开发者真实 ``ky_config.json``
    —— 这正是 19:26 那次覆盖的元凶（写入内容为 DEFAULT_CONFIG 形状 + 被塞进
    api_key/active_subject）。此处必须把 workspace_root 指到 tmp_path，
    并预置一份配置，才能既覆盖保存路径又不动真实文件。
    """
    from tools.gui.widgets.settings_dialog import SettingsDialog
    from tools.gui import theme_apply
    sandbox = tmp_path / "workspace"
    sandbox.mkdir()
    (sandbox / "ky_config.json").write_text(
        json.dumps(
            {
                "api_key": "sk-sandbox",
                "base_url": "https://api.example.test/v1",
                "model": "deepseek-chat",
                "active_subject": "pol",
                "study_plan": {"school": "中国人民大学", "major": "030100 法学"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    win = MainWindow(workspace_root=sandbox)
    try:
        dlg = SettingsDialog(win)
        dlg.preset_combo.setCurrentIndex(
            dlg.preset_combo.findData("light"))
        dlg.acc_edit.setText("#3b82f6")
        dlg.radius_spin.setValue(10)
        dlg._apply()
        prefs = theme_apply.read_prefs()
        assert prefs.get("preset") == "light"
        assert prefs.get("acc") == "#3b82f6"
        assert int(prefs.get("radius", 0)) == 10
        # 清理 L2 覆盖，避免影响其他测试
        for k in ("ui/preset", "ui/acc", "ui/radius",
                   "ui/density", "ui/font_scale"):
            theme_apply._settings().setValue(k, "")
    finally:
        win.close()


# ── 倒计时与任务进度 ────────────────────────────────────────────

def test_countdown_label_is_refreshable(win):
    """[缺陷修复] 改造前 countdown 是局部变量，挂一整天也不动。"""
    label = win.countdown_label
    assert label.text().startswith("初试倒计时:"), f"初始文本异常: {label.text()!r}"

    original = label.text()
    label.setText("初试倒计时: -1 天")
    win._sync_header_text()                       # 定时器走的就是这条路径
    assert label.text() == original, "刷新后应重新取真实倒计时，覆盖手工写入的值"


def test_timer_tick_refreshes_countdown_and_tasks(win):
    win.countdown_label.setText("stale")
    win._on_timer_tick()
    assert win.countdown_label.text() != "stale", "定时器应刷新倒计时"


def test_task_progress_matches_shared_state(win):
    state = services.load_state(win.workspace_root)
    assert state is not None
    for subject in state.subjects:
        bar = win.task_progress_bars.get(subject.key)
        label = win.task_count_labels.get(subject.key)
        assert bar is not None and label is not None
        assert bar.value() == subject.pct
        assert label.text() == subject.summary_text


# ── 数据链路 ────────────────────────────────────────────────────

def test_error_and_intel_panels_render_text(win):
    win._refresh_error_tab()
    win._refresh_intel_tab()
    assert "错题" in win.error_info.toPlainText()
    assert "研招" in win.intel_display.toPlainText()


def test_action_capture_returns_text():
    """GUI 的功能卡片复用 TUI 的动作分发，异常必须转成可读文本而非抛栈。"""
    out = services.run_action_capture("no-such-action")
    assert isinstance(out, str) and out


def test_header_info_falls_back_on_empty_workspace(tmp_path: Path):
    """空工作区（无 ky_config / 无状态文件）也必须给出可用头部数据。"""
    info = services.header_info(tmp_path)
    assert set(info) >= {"days_left", "school", "major", "style_short"}
    assert isinstance(info["days_left"], int)
    assert info["school"]


# ── 微信检索对话框的 Worker 生命周期（P2-8） ─────────────────────

def test_wechat_search_worker_is_parented(app):
    """[P2-8 回归] Worker 必须挂在对话框上。

    修复前 ``WeChatSearchWorker.__init__`` 只调 ``super().__init__()``，
    ``parent()`` 恒为 None：QThread 一旦失去 Python 侧引用就会被 GC 回收，
    而 Qt 侧线程仍在跑 → "QThread: Destroyed while thread is still running"
    直接崩进程。挂 parent 后 Qt 父子关系提供兜底强引用。

    阴性对照：把 ``super().__init__(parent)`` 改回 ``super().__init__()``，
    本用例必须变红。
    """
    from tools.gui.widgets.wechat_search_dialog import (
        WeChatSearchDialog, WeChatSearchWorker)

    dlg = WeChatSearchDialog()
    try:
        worker = WeChatSearchWorker(
            keyword="华中科技大学 计算机 复试线", max_results=3, source="auto",
            fetch_content=False, save=False, school="华中科技大学", parent=dlg)
        assert worker.parent() is dlg, "worker 必须挂在对话框上，否则会被 GC 掉"
        assert not worker.isRunning()
    finally:
        dlg.close()


def test_wechat_search_reentry_keeps_single_worker(app, monkeypatch):
    """[P2-8 回归] 检索未完成时再次触发 _on_search，只能有一个 worker 在跑。

    修复前 ``_on_search`` 只有 ``search_btn.setEnabled(False)`` 没有重入保护，
    而 ``keyword_input.returnPressed`` 与按钮 clicked 都连到本方法 ——
    用户在检索未完成时按回车会重入，直接 new 一个 worker 覆盖 ``self.worker``，
    前一个 QThread 失去引用后被 GC → 崩进程。

    阴性对照：注释掉 ``_on_search`` 开头的
    ``if self.worker is not None and self.worker.isRunning(): return``，
    本用例必须变红（会启动 2 个 worker 且 ``self.worker`` 被覆盖）。
    """
    from PySide6.QtCore import QObject, Signal

    from tools.gui.widgets import wechat_search_dialog as mod

    started: list = []

    class _FakeWorker(QObject):
        """替身：不真起线程、不联网，isRunning() 恒为 True（模拟"检索中"）。"""
        finished_signal = Signal(dict)
        finished = Signal()

        def __init__(self, **kwargs):
            super().__init__(kwargs.get("parent"))
            self.kwargs = kwargs
            started.append(self)

        def start(self) -> None:      # noqa: D401 - 替身不做任何事
            pass

        def isRunning(self) -> bool:  # noqa: N802 - 对齐 Qt 命名
            return True

    monkeypatch.setattr(mod, "WeChatSearchWorker", _FakeWorker)

    dlg = mod.WeChatSearchDialog()
    try:
        dlg.keyword_input.setText("华中科技大学 计算机 复试线")
        dlg._on_search()
        first = dlg.worker
        assert first is not None

        dlg._on_search()      # 重入：必须早退
        assert len(started) == 1, f"重入时应只启动 1 个 worker，实际 {len(started)}"
        assert dlg.worker is first, "self.worker 被重入覆盖了"
        assert dlg._worker_refs == [first], "worker 未进强引用池"

        first.finished.emit()          # 线程结束 → 出池
        assert dlg._worker_refs == [], "线程结束后应出池，避免引用无限累积"
    finally:
        dlg.close()


def test_wechat_search_close_detaches_still_running_thread(app, monkeypatch):
    """[P2-9 回归] 检索进行中关闭对话框必须安全收尾线程（不得析构运行中的 QThread）。

    修复前 worker 是对话框的 QObject 子对象：检索进行中关闭对话框 / 退出应用时，
    主窗口析构会连带销毁仍在跑的 QThread →
    ``QThread: Destroyed while thread is still running`` → 进程 abort
    （离屏实测 exit 127）。

    替身不真起线程、不联网：``wait`` 恒返回 False 模拟「超时仍未结束」。断言
    closeEvent 请求了中断、把线程**摘出对话框**、并登记进模块级在跑池（供
    aboutToQuit 收尾），且关闭立即返回（不卡死 UI）。

    阴性对照：删掉 ``closeEvent``（或 ``_detach_worker`` 里的 ``self.worker = None``
    / ``_ACTIVE_WORKERS.add``），本用例变红。
    """
    import time

    from PySide6.QtCore import QObject, Signal

    from tools.gui.widgets import wechat_search_dialog as mod

    class _StuckWorker(QObject):
        """替身：isRunning 恒 True，wait 恒超时（模拟检索卡在网络上）。"""
        finished_signal = Signal(dict)
        finished = Signal()

        def __init__(self, **kwargs):
            super().__init__(kwargs.get("parent"))
            self.interrupted = False
            self.wait_ms = None

        def start(self) -> None:
            pass

        def isRunning(self) -> bool:      # noqa: N802 - 对齐 Qt 命名
            return True

        def requestInterruption(self) -> None:   # noqa: N802
            self.interrupted = True

        def wait(self, ms) -> bool:       # noqa: N802
            self.wait_ms = ms
            return False                  # 超时：线程仍未结束

    monkeypatch.setattr(mod, "WeChatSearchWorker", _StuckWorker)
    mod._ACTIVE_WORKERS.clear()

    dlg = mod.WeChatSearchDialog()
    w = None
    try:
        dlg.keyword_input.setText("华中科技大学 计算机 复试线")
        dlg._on_search()
        w = dlg.worker
        assert w is not None and w in mod._ACTIVE_WORKERS

        t0 = time.monotonic()
        dlg.close()                       # 修复前：析构运行中的线程 → 崩进程
        assert time.monotonic() - t0 < 2.0, "关闭不得阻塞 UI"

        assert w.interrupted, "关闭时必须请求中断"
        assert isinstance(w.wait_ms, int) and w.wait_ms > 0, "wait 必须带超时"
        assert dlg.worker is None, "超时后必须把线程摘出对话框"
        assert w not in dlg._worker_refs, "摘出的线程不应留在对话框引用池"
        assert w in mod._ACTIVE_WORKERS, "摘出的线程仍须被退出收尾池持有"

        w.finished.emit()                 # 线程最终结束 → 出池
        assert w not in mod._ACTIVE_WORKERS
    finally:
        dlg.close()
        mod._ACTIVE_WORKERS.discard(w)


def test_wechat_search_close_keeps_thread_that_finishes_in_time(app, monkeypatch):
    """线程在超时内自然结束时**不**摘线程（随对话框正常析构）。"""
    from PySide6.QtCore import QObject, Signal

    from tools.gui.widgets import wechat_search_dialog as mod

    class _QuickWorker(QObject):
        finished_signal = Signal(dict)
        finished = Signal()

        def __init__(self, **kwargs):
            super().__init__(kwargs.get("parent"))
            self.waited = False

        def start(self) -> None:
            pass

        def isRunning(self) -> bool:      # noqa: N802
            return True

        def requestInterruption(self) -> None:   # noqa: N802
            pass

        def wait(self, ms) -> bool:       # noqa: N802
            self.waited = True
            return True                   # 在超时内结束

    monkeypatch.setattr(mod, "WeChatSearchWorker", _QuickWorker)
    mod._ACTIVE_WORKERS.clear()

    dlg = mod.WeChatSearchDialog()
    w = None
    try:
        dlg.keyword_input.setText("华中科技大学 计算机 复试线")
        dlg._on_search()
        w = dlg.worker

        dlg.close()
        assert w.waited, "关闭时应带超时等待线程收尾"
        assert dlg.worker is w, "线程已结束则无需摘出对话框"
    finally:
        dlg.close()
        mod._ACTIVE_WORKERS.discard(w)


def test_quit_hook_drains_active_workers(app, monkeypatch):
    """aboutToQuit 收尾钩子：对在跑线程 requestInterruption + **带超时** wait。

    阴性对照：删掉 ``_install_quit_hook`` 里的 connect 或
    ``_wait_for_active_workers`` 里的 wait，本用例变红。
    """
    from tools.gui.widgets import wechat_search_dialog as mod

    calls = {}

    class _W:
        def isRunning(self):
            return True

        def requestInterruption(self):
            calls["interrupted"] = True

        def wait(self, ms):
            calls["ms"] = ms
            return True

    w = _W()
    mod._ACTIVE_WORKERS.add(w)
    try:
        mod._wait_for_active_workers()
        assert calls.get("interrupted") is True
        assert isinstance(calls.get("ms"), int) and calls["ms"] > 0, \
            "退出收尾的 wait 必须带超时，否则会卡死退出流程"
    finally:
        mod._ACTIVE_WORKERS.discard(w)


def test_quit_hook_terminates_thread_that_ignores_interruption(app):
    """线程拒绝中断（wait 恒超时）时必须 terminate 兜底 —— 绝不让运行中的 QThread
    进入析构（Qt 对运行中的 QThread 析构会直接 abort 进程）。

    阴性对照：删掉 ``_stop_worker`` 里的 ``terminate`` 兜底，本用例变红。
    """
    from tools.gui.widgets import wechat_search_dialog as mod

    calls: dict = {}

    class _Stubborn:
        def isRunning(self):
            return True

        def requestInterruption(self):
            calls["interrupted"] = True

        def wait(self, ms):
            calls.setdefault("waits", []).append(ms)
            return False

        def terminate(self):
            calls["terminated"] = True

    w = _Stubborn()
    mod._ACTIVE_WORKERS.add(w)
    try:
        mod._wait_for_active_workers()
        assert calls.get("interrupted") is True
        assert calls.get("terminated") is True, "停不掉时必须 terminate 兜底"
        assert all(ms > 0 for ms in calls.get("waits", [])), "每次 wait 都必须带超时"
    finally:
        mod._ACTIVE_WORKERS.discard(w)


# ── 检索对话框实例持有与退出钩子位置（收尾修复） ────────────────

def test_main_window_installs_search_quit_hook_at_startup(app, monkeypatch):
    """[收尾修复·钩子位置] 退出收尾钩子必须在主窗口**启动时**装好一次。

    修复前只有 ``WeChatSearchDialog._on_search`` 会调 ``_install_quit_hook()``，
    即「真的发起过检索」才装钩子。于是「开着检索对话框（检索进行中）直接退出
    应用」这条路径上 aboutToQuit 没有任何收尾回调，运行中的 QThread 随主窗口
    析构被销毁 → "QThread: Destroyed while thread is still running" → 进程 abort。

    阴性对照：删掉 ``MainWindow.__init__`` 里那次 ``_install_quit_hook()`` 调用，
    本用例变红。
    """
    from tools.gui.widgets import wechat_search_dialog as mod

    calls: list = []
    monkeypatch.setattr(mod, "_install_quit_hook", lambda: calls.append(1))

    w = MainWindow()
    try:
        assert calls == [1], f"主窗口启动应恰好装一次退出钩子，实际 {len(calls)} 次"
    finally:
        w.close()


def test_wechat_search_dialog_instance_is_reused(win, app, monkeypatch):
    """[轻微泄漏修复] 主窗口必须持有并复用同一个检索对话框实例。

    修复前 ``WeChatSearchDialog(self).exec()`` 用临时对象弹窗：exec() 返回后
    Python 引用即失效，但对话框是主窗口的 Qt 子对象 → 每打开一次就留下一个
    隐藏的 QDialog，直到主窗口销毁（轻微泄漏）。

    复用是安全的：每次关闭都走对话框 ``closeEvent`` → ``_shutdown_worker``，
    在跑线程要么已停止、要么被摘出并置 ``self.worker = None``，故再次 ``exec()``
    不会把运行中的 QThread 重新拉进析构路径。

    阴性对照：把 ``_open_wechat_search_dialog`` 改回
    ``WeChatSearchDialog(self).exec()``，本用例变红（创建 2 个实例，
    且 ``win._wechat_dialog`` 为 None）。
    """
    from tools.gui.widgets import wechat_search_dialog as mod

    created: list = []
    real_cls = mod.WeChatSearchDialog

    class _SpyDialog(real_cls):
        def __init__(self, parent=None):
            super().__init__(parent)
            created.append(self)

        def exec(self):                 # 不真进模态事件循环
            return 0

    monkeypatch.setattr(mod, "WeChatSearchDialog", _SpyDialog)

    win._open_wechat_search_dialog()
    win._open_wechat_search_dialog()

    assert len(created) == 1, f"对话框应被复用，实际创建 {len(created)} 个实例"
    assert win._wechat_dialog is created[0], "主窗口必须持有该对话框的引用"
    assert win._wechat_dialog.parent() is win, "对话框应挂在主窗口上"
