# -*- coding: utf-8 -*-
"""P0-1 修复回归（2026-10-08）：GUI 停止回答后，旧线程迟到流式片段不得污染下一条对话

缺陷：``_on_agent_step`` / ``_on_agent_chunk`` 缺发送者校验。点「停止」把
``agent_worker`` 置 None 并允许立即发新消息，而旧 worker 取消前已 emit 的
chunk/step 是跨线程排队信号，必在停止后被主线程消费 —— 聊天区继续蹦字；
若紧接着发新消息，旧片段还会先建出 streaming 气泡、让新答案续写进同一条
造成内容混排。

修复：两函数在空值检查后、任何副作用之前加与 ``_on_agent_reply`` 同口径的
守卫 —— 发送者非当前活跃 worker 即丢弃；``sender() is None`` 的直接调用路径
保持放行（兼容既有 ``test_chat_view_formula_steps.py`` 的非信号调用）。

[为什么单开文件] 本批主题是「停止后迟到信号污染」，与
``test_chat_view_formula_steps.py``（聊天页公式/折叠/clear）的既定范围不同，
混入会削弱两者的回归语义。

运行（必须带 basetemp）：
  py -X utf8 -m pytest tests/test_fix_20261008_p01_gui_late_chunk.py \
      tests/test_chat_view_formula_steps.py -q -p no:cacheprovider \
      --basetemp=C:/tmp/pbt_p01
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 迟到片段用例")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    """离屏 Qt 应用（无真实显示器）。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    app.setStyleSheet("")


class _FakeWorker(QObject):
    """最小 worker 替身：只提供 AgentWorker 的两个跨线程信号。"""

    chunk = Signal(str)
    step = Signal(str)


@pytest.fixture()
def win(qt_app, tmp_path, monkeypatch):
    """离屏 MainWindow（workspace_root=tmp_path，绝不碰真实工作区）。

    打桩必须打在 ``mw_mod.services`` —— main_window 绑定的是 ``gui.services``
    （与 ``tools.gui.services`` 不是同一模块对象），打错对象拦不住
    「未配置 → 150ms 后弹引导向导」定时器。
    """
    from tools.gui import main_window as mw_mod

    monkeypatch.setattr(mw_mod.services, "is_unconfigured",
                        lambda *a, **k: False, raising=False)
    window = mw_mod.MainWindow(workspace_root=tmp_path)
    yield window
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def _steps(chat):
    return [b for b in chat.bubbles if b.kind == "steps"]


# ── ① 正常路径：当前活跃 worker 的 chunk / step 照常上屏 ─────────


def test_live_worker_chunk_and_step_render(win, qt_app):
    w = _FakeWorker()
    w.chunk.connect(win._on_agent_chunk)
    w.step.connect(win._on_agent_step)
    win.agent_worker = w

    w.step.emit("① 读取任务")
    w.chunk.emit("合成答案正文")
    w.step.emit("② 补充检索")
    qt_app.processEvents()

    assert [b.kind for b in win.chat_display.bubbles] == ["steps", "agent", "steps"], \
        "step → 折叠块、chunk → 私教气泡、答案开始即封口（步骤新起一块）"
    assert "合成答案正文" in win.chat_display.bubbles[1].text()
    assert win._streamed is True
    assert "① 读取任务" in _steps(win.chat_display)[0].text()
    assert "② 补充检索" in _steps(win.chat_display)[1].text()


# ── ② 迟到 chunk 被拦（agent_worker 为 None / 新 worker 两种情况）──


def test_late_chunk_dropped_when_stopped(win, qt_app):
    """点「停止」后 agent_worker=None：旧 worker 迟到 chunk 不得上屏。"""
    old = _FakeWorker()
    old.chunk.connect(win._on_agent_chunk)
    win.agent_worker = None
    win.chat_display.clear()
    win._streamed = False

    old.chunk.emit("迟到片段-甲")
    qt_app.processEvents()

    assert win.chat_display.bubbles == [], "停止后迟到 chunk 不得新建气泡"
    assert "迟到片段-甲" not in win.chat_display.toPlainText()
    assert win._streamed is False, "被拦片段不得污染流式状态机"


def test_late_chunk_dropped_after_new_worker_switched(win, qt_app):
    """已发下一条消息（agent_worker 指向新 worker）：旧 worker 迟到 chunk 不得上屏。"""
    old = _FakeWorker()
    new = _FakeWorker()
    old.chunk.connect(win._on_agent_chunk)
    win.agent_worker = new
    win.chat_display.clear()

    old.chunk.emit("迟到片段-乙")
    qt_app.processEvents()

    assert win.chat_display.bubbles == []
    assert "迟到片段-乙" not in win.chat_display.toPlainText()


def test_late_chunk_does_not_mix_into_new_answer(win, qt_app):
    """核心缺陷场景：新答案已建流式气泡，旧迟到片段不得续写进同一条（混排）。"""
    old = _FakeWorker()
    new = _FakeWorker()
    old.chunk.connect(win._on_agent_chunk)
    new.chunk.connect(win._on_agent_chunk)
    win.chat_display.clear()
    win.agent_worker = new

    new.chunk.emit("新答案正文")
    old.chunk.emit("迟到片段-戊")
    qt_app.processEvents()

    last = win.chat_display.bubbles[-1]
    assert last.kind == "agent"
    assert last.text() == "新答案正文", f"迟到片段混入了新答案气泡: {last.text()!r}"


# ── ③ 迟到 step 被拦 ────────────────────────────────────────────


def test_late_step_dropped(win, qt_app):
    old = _FakeWorker()
    new = _FakeWorker()
    old.step.connect(win._on_agent_step)
    win.agent_worker = new
    win.chat_display.clear()

    old.step.emit("迟到步骤-丙")
    qt_app.processEvents()

    assert win.chat_display._open_step_group is None
    assert _steps(win.chat_display) == [], "迟到 step 不得新建思考折叠块"
    assert "迟到步骤-丙" not in win.chat_display.toPlainText()


# ── ④ sender=None（直接调用）不被拦：兼容既有测试路径 ─────────────


def test_direct_call_without_sender_not_blocked(win, qt_app):
    """非信号调用（sender() is None）保持放行 ——
    test_chat_view_formula_steps.py 直接调 _on_agent_step / _on_agent_chunk。"""
    win.agent_worker = None
    win.chat_display.clear()

    win._on_agent_step("直调步骤-丁")
    win._on_agent_chunk("直调片段-丁")
    qt_app.processEvents()

    assert "直调步骤-丁" in win.chat_display.toPlainText()
    assert "直调片段-丁" in win.chat_display.toPlainText()
