# -*- coding: utf-8 -*-
"""2026-10-09 批次二 C 域（GUI/TUI）修复回归：GUI-H1 + GUI-L1 + TUI-L1

覆盖三项已核实缺陷：
  GUI-H1  关窗线程析构崩溃窗口 —— closeEvent 5s 截止后仍在跑的
          IntelTaskWorker/AgentWorker（无 parent QThread、纯 Python run()）
          登记进模块级强引用池 _ORPHANED_WORKERS，窗口析构不再连带析构
          运行中的 QThread；finished 信号出池；已结束线程不登记。
  GUI-L1  ChatInput 未过滤键盘自动连击 —— 按住 Enter（autorep=True）不得
          连发；正常 Enter 恰好发一次；Shift+Enter 换行语义不变；普通字符
          键长按不被吞（防过度过滤的阴性对照）。
  TUI-L1  display_width 省略号宽度按 1 列计 —— U+2026 在中文终端占 2 列，
          修复后 pad_display / 截断场景对齐到目标宽度；其余 Ambiguous
          字符（'·' 等）不连带漂移（阴性对照）。

[中性化] 全部文本为合成内容，不含任何真实身份。
运行（必须带 basetemp）：
  py -m pytest tests/test_audit_20261009_b2_gui_tui.py -q \
      -p no:cacheprovider --basetemp=C:/Users/29652/AppData/Local/Temp/ky_b2_c
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 用例")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import tools.gui.main_window as mw_mod  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    """离屏 Qt 应用（无真实显示器）。缺平台插件时整组跳过。"""
    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
    except Exception as exc:  # pragma: no cover - 平台插件缺失
        pytest.skip(f"Qt 平台插件不可用，跳过 GUI 用例: {exc}")
    return app


# ══════════════════════════════════════════════════════════════════
# GUI-H1 · 关窗线程析构崩溃窗口（模块级强引用池）
# ══════════════════════════════════════════════════════════════════


class _StuckWorker(QObject):
    """替身：isRunning 恒为真、wait 恒超时（模拟关窗时仍阻塞在网络的 worker）。"""

    finished = Signal()

    def __init__(self, running: bool = True):
        super().__init__()
        self._running = running
        self.cancelled = False
        self.interruption_requested = False
        self.wait_ms: list = []

    def cancel(self):
        self.cancelled = True

    def isRunning(self):  # noqa: N802 - Qt 命名
        return self._running

    def quit(self):  # noqa: N802
        pass

    def wait(self, ms):  # noqa: N802
        self.wait_ms.append(int(ms))
        return False           # 永不结束：模拟 LLM 请求在途

    def requestInterruption(self):  # noqa: N802
        self.interruption_requested = True


def _make_main_window(qt_app, monkeypatch, workspace):
    """构造离屏 MainWindow；禁用「未配置 → 自动弹引导向导」定时器。

    打桩打在 ``mw_mod.services`` —— main_window 绑定的是 ``gui.services``
    （与 ``tools.gui.services`` 不是同一模块对象），打错对象拦不住定时器。
    """
    monkeypatch.setattr(mw_mod.services, "is_unconfigured",
                        lambda *a, **k: False, raising=False)
    return mw_mod.MainWindow(workspace_root=workspace)


def test_gui_h1_running_worker_kept_in_module_pool(qt_app, tmp_path, monkeypatch):
    """closeEvent 后仍运行的 worker 必须被模块级强引用池持有。"""
    mw_mod._ORPHANED_WORKERS.clear()
    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    stuck = _StuckWorker()
    try:
        win._worker_refs.append(stuck)
        win.close()

        assert stuck.cancelled, "关窗必须先 cancel"
        assert stuck.interruption_requested, "关窗应对残留线程请求中断"
        assert stuck in mw_mod._ORPHANED_WORKERS, (
            "关窗后仍在跑的线程未被模块级强引用池持有 —— 窗口析构会连带析构 "
            "运行中的 QThread（QThread: Destroyed while thread is still running）")
        assert getattr(stuck, "_ky_orphan_registered", False), "缺少防重复登记标记"
    finally:
        mw_mod._ORPHANED_WORKERS.discard(stuck)
        win.deleteLater()
        qt_app.processEvents()


def test_gui_h1_finished_signal_removes_from_pool(qt_app, tmp_path, monkeypatch):
    """线程自然结束（finished）后从池中移除，引用不无限累积。"""
    mw_mod._ORPHANED_WORKERS.clear()
    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    stuck = _StuckWorker()
    try:
        win._worker_refs.append(stuck)
        win.close()
        assert stuck in mw_mod._ORPHANED_WORKERS

        stuck.finished.emit()          # 线程随后自然结束
        assert stuck not in mw_mod._ORPHANED_WORKERS, "finished 后未从池中移除"
    finally:
        mw_mod._ORPHANED_WORKERS.discard(stuck)
        win.deleteLater()
        qt_app.processEvents()


def test_gui_h1_finished_worker_not_registered(qt_app, tmp_path, monkeypatch):
    """已结束（isRunning 为假）的 worker 不被登记。"""
    mw_mod._ORPHANED_WORKERS.clear()
    win = _make_main_window(qt_app, monkeypatch, tmp_path)
    done = _StuckWorker(running=False)
    try:
        win._worker_refs.append(done)
        win.close()
        assert done not in mw_mod._ORPHANED_WORKERS, "已结束的线程不应被登记"
        assert not getattr(done, "_ky_orphan_registered", False)
    finally:
        mw_mod._ORPHANED_WORKERS.discard(done)
        win.deleteLater()
        qt_app.processEvents()


# ══════════════════════════════════════════════════════════════════
# GUI-L1 · ChatInput 键盘自动连击过滤
# ══════════════════════════════════════════════════════════════════


def _return_event(autorep: bool, mods=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, mods, "", autorep)


def test_gui_l1_autorepeat_enter_does_not_send(qt_app):
    """按住 Enter 的自动连击事件不得触发发送。"""
    from tools.gui.views.chat_tab import ChatInput

    box = ChatInput()
    try:
        sent = []
        box.send_requested.connect(lambda: sent.append(1))
        box.keyPressEvent(_return_event(autorep=True))
        assert sent == [], "自动连击的 Enter 不应发送"
    finally:
        box.deleteLater()
        qt_app.processEvents()


def test_gui_l1_plain_enter_sends_exactly_once(qt_app):
    """正常 Enter（非连击）恰好发送一次，且不插入换行。"""
    from tools.gui.views.chat_tab import ChatInput

    box = ChatInput()
    try:
        sent = []
        box.send_requested.connect(lambda: sent.append(1))
        box.keyPressEvent(_return_event(autorep=False))
        assert sent == [1], "正常 Enter 应恰好发送一次"
        assert box.toPlainText() == "", "Enter 不应插入换行"
    finally:
        box.deleteLater()
        qt_app.processEvents()


def test_gui_l1_shift_enter_newline_not_send(qt_app):
    """Shift+Enter 换行语义不变（不发送、插入换行）。"""
    from tools.gui.views.chat_tab import ChatInput

    box = ChatInput()
    try:
        sent = []
        box.send_requested.connect(lambda: sent.append(1))
        box.keyPressEvent(_return_event(autorep=False,
                                        mods=Qt.KeyboardModifier.ShiftModifier))
        assert sent == [], "Shift+Enter 不应触发发送"
        assert "\n" in box.toPlainText(), "Shift+Enter 应插入换行"
    finally:
        box.deleteLater()
        qt_app.processEvents()


def test_gui_l1_autorepeat_char_key_still_typed(qt_app):
    """阴性对照：普通字符键长按不被吞（过滤只针对发送键）。"""
    from tools.gui.views.chat_tab import ChatInput

    box = ChatInput()
    try:
        box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A,
                                    Qt.KeyboardModifier.NoModifier, "a", True))
        assert box.toPlainText() == "a", "普通字符键的自动连击不应被吞"
    finally:
        box.deleteLater()
        qt_app.processEvents()


# ══════════════════════════════════════════════════════════════════
# TUI-L1 · 省略号显示宽度（'…' 按 2 列计）
# ══════════════════════════════════════════════════════════════════


def test_tui_l1_ellipsis_display_width_is_two():
    from tui.terminal import display_width

    assert display_width("…") == 2
    assert display_width("a…b") == 4


def test_tui_l1_pad_display_ellipsis_aligned():
    from tui.terminal import display_width, pad_display

    padded = pad_display("…", 4)
    assert padded == "…  "
    assert display_width(padded) == 4


def test_tui_l1_real_truncation_scenario_aligned():
    """真实截断场景：_truncate_display 为省略号按 2 列预留空间，结果不超 max_w，
    pad 后对齐到 target。"""
    import tui_navigator as nav

    truncated = nav._truncate_display("摘要内容" * 20, 29)
    assert "…" in truncated, "该场景应走省略号截断路径"
    assert nav.display_width(truncated) == 28, \
        "截断结果宽度未如实计算（13 字 × 2 列 + 省略号 2 列，且 ≤ max_w=29）"
    line = nav.pad_display(truncated, 40)
    assert nav.display_width(line) == 40, "截断文本 pad 后未对齐到目标宽度"


def test_tui_l1_other_ambiguous_chars_untouched():
    """阴性对照：其余 Ambiguous 字符（'·'）维持原判定，不连带漂移。"""
    from tui.terminal import display_width

    assert display_width("·") == 1
