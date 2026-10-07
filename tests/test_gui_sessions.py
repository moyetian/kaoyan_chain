# -*- coding: utf-8 -*-
"""GUI 会话管理回归（修复④）：显式 session_id / 历史对话框 / 恢复 / 新建。

覆盖：
  a) AgentWorker 显式 session_id（恢复语义入口）优先于进程内共享 id；
  b) SessionDialog：列表渲染 / 删除（确认后文件消失 + 列表刷新）/ 恢复选择；
  c) MainWindow._apply_session_restore：消息回放 + 截断提示 + 恢复提示；
  d) MainWindow._on_new_session：共享 id 置空 + 清屏 + 提示；
  e) _on_open_sessions：worker 在跑时拒绝；选中恢复端到端（打桩对话框 exec）；
  f) chat_tab 的「历史 / 新建」按钮存在且接线正确。

运行（本机铁律，必须带 basetemp）：
  py -m pytest tests/test_gui_sessions.py -p no:cacheprovider --basetemp=.pytest_tmp/fixsess -q

隔离说明：每个用例保存/还原 ``AgentWorker._shared_session_id``（类级状态，
防污染其它 GUI 测试）；工作区一律用 tmp_path。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 会话管理用例")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from tools.agent.session_log import (  # noqa: E402
    EVENT_ASSISTANT, EVENT_USER, SessionLog,
)
from tools.gui.workers.agent_worker import AgentWorker  # noqa: E402


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst
    inst.setStyleSheet("")


@pytest.fixture(autouse=True)
def _restore_shared_session_id():
    """隔离 AgentWorker 类级共享 id（进程级状态，防污染其它测试）。"""
    saved = AgentWorker._shared_session_id
    yield
    AgentWorker._shared_session_id = saved


@pytest.fixture()
def win(app, tmp_path, monkeypatch):
    from tools.gui import main_window as mw

    # 临时工作区未配置 → 禁用「自动弹引导向导」定时器（同 test_fix_20261005 惯例）
    monkeypatch.setattr(mw.services, "is_unconfigured",
                        lambda *a, **k: False, raising=False)
    w = mw.MainWindow(workspace_root=tmp_path)
    yield w
    w.close()
    w.deleteLater()
    app.processEvents()


def _write_session(ws, messages, session_id=None):
    """在临时工作区造一段会话日志（至少一条消息），返回 session_id。"""
    log = SessionLog(workspace_root=ws, session_id=session_id)
    sid = log.session_id
    for role, content in messages:
        etype = EVENT_USER if role == "user" else EVENT_ASSISTANT
        log.append(etype, {"content": content})
    log.close()
    return sid


def _session_file(ws, sid):
    return Path(ws) / ".memory" / "sessions" / f"{sid}.jsonl"


def _session_dir(ws):
    return Path(ws) / ".memory" / "sessions"


# ── a) AgentWorker 显式 session_id ──────────────────────────────


def test_agent_worker_explicit_session_id_wins(app, tmp_path):
    """显式 session_id = resume 入口：原样透传，不生成新 id。"""
    worker = AgentWorker({}, "你好", workspace_root=tmp_path,
                         session_id="20260101-000000-abcdef")
    assert worker._session_id == "20260101-000000-abcdef"


def test_agent_worker_falls_back_to_shared_id(app, tmp_path):
    """未显式传入时沿用进程内共享 id 的既有逻辑。"""
    worker = AgentWorker({}, "你好", workspace_root=tmp_path)
    assert worker._session_id, "未显式传入时应生成/复用共享 id"
    assert worker._session_id == AgentWorker._shared_session_id


# ── b) SessionDialog：列表 / 删除 / 恢复 ────────────────────────


def test_session_dialog_lists_deletes_and_restores(app, tmp_path, monkeypatch):
    sid_a = _write_session(tmp_path, [("user", "第一段会话"), ("assistant", "回答一")],
                           session_id="20260101-000000-aaaaaa")
    sid_b = _write_session(tmp_path, [("user", "第二段会话")],
                           session_id="20260101-000001-bbbbbb")
    from tools.gui.widgets.session_dialog import EMPTY_HINT, SessionDialog

    dlg = SessionDialog(tmp_path)
    try:
        assert dlg.table.rowCount() == 2, "两段会话应显示两行"
        assert dlg.table.item(0, 0).text() != EMPTY_HINT
        assert dlg.selected_session_id() is None, "未选中时不得有选择"

        # 删除：确认后文件消失、列表刷新为 1 行
        target_row = next(i for i in range(2)
                          if dlg.table.item(i, 0).text() == sid_b[:15])
        dlg.table.selectRow(target_row)
        assert dlg.selected_session_id() == sid_b
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.Yes)
        dlg.delete_btn.click()
        assert dlg.table.rowCount() == 1, "删除后列表应刷新"
        assert not _session_file(tmp_path, sid_b).exists(), "会话文件必须被删除"

        # 恢复：选中剩余行 → 记录选择并 accept
        dlg.table.selectRow(0)
        assert dlg.selected_session_id() == sid_a
        dlg.restore_btn.click()
        assert dlg.chosen_session_id() == sid_a
    finally:
        dlg.deleteLater()


def test_session_dialog_empty_state(app, tmp_path):
    from tools.gui.widgets.session_dialog import EMPTY_HINT, SessionDialog

    dlg = SessionDialog(tmp_path)
    try:
        assert dlg.table.rowCount() == 1, "空列表应显示一行提示"
        assert dlg.table.item(0, 0).text() == EMPTY_HINT
        assert dlg.selected_session_id() is None
        assert dlg.chosen_session_id() is None
    finally:
        dlg.deleteLater()


def test_session_dialog_prune_keeps_recent(app, tmp_path, monkeypatch):
    """清理旧会话：21 段 → 保留最近 20 段（确认后执行）。"""
    from tools.gui.widgets.session_dialog import PRUNE_KEEP, SessionDialog

    assert PRUNE_KEEP == 20, "保留口径 = 最近 20 段"
    for i in range(PRUNE_KEEP + 1):
        _write_session(tmp_path, [("user", f"会话 {i:02d}")],
                       session_id=f"20260101-{i:06d}-aaaaaa")

    dlg = SessionDialog(tmp_path)
    try:
        assert dlg.table.rowCount() == PRUNE_KEEP + 1
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.Yes)
        dlg.prune_btn.click()
        assert dlg.table.rowCount() == PRUNE_KEEP, "清理后应只保留最近 20 段"
        remaining = list(_session_dir(tmp_path).glob("*.jsonl"))
        assert len(remaining) == PRUNE_KEEP
    finally:
        dlg.deleteLater()


# ── c) _apply_session_restore：回放 / 截断 ──────────────────────


def test_apply_session_restore_replays_messages(win, tmp_path):
    sid = _write_session(tmp_path, [
        ("user", "请解释一下均值不等式"),
        ("assistant", "均值不等式说的是……"),
    ], session_id="20260101-120000-abcdef")

    win._apply_session_restore(sid)
    text = win.chat_display.toPlainText()
    assert "请解释一下均值不等式" in text
    assert "均值不等式说的是……" in text
    assert "已恢复会话" in text
    assert sid[:15] in text


def test_apply_session_restore_truncates_old_messages(win, tmp_path):
    from tools.gui.main_window import RESTORE_TAIL_MESSAGES

    messages = []
    for i in range(40):
        messages.append(("user", f"问题{i:02d}"))
        messages.append(("assistant", f"回答{i:02d}"))
    sid = _write_session(tmp_path, messages, session_id="20260101-130000-abcdef")
    assert len(messages) == 80

    win._apply_session_restore(sid)
    text = win.chat_display.toPlainText()
    omitted = 80 - RESTORE_TAIL_MESSAGES
    assert f"更早的 {omitted} 条消息已省略" in text, "超长会话必须有截断提示"
    assert "问题00" not in text, "超出尾部的旧消息不应上屏"
    assert "问题39" in text and "回答39" in text, "尾部消息必须保留"
    assert len(win.chat_display.bubbles) == RESTORE_TAIL_MESSAGES + 2


def test_apply_session_restore_missing_log_shows_hint_only(win):
    """日志不存在（或读失败）时只上恢复提示，绝不抛异常。"""
    win._apply_session_restore("20260101-999999-nolog0")
    text = win.chat_display.toPlainText()
    assert "已恢复会话" in text


# ── d) _on_new_session ─────────────────────────────────────────


def test_on_new_session_resets_shared_id_and_clears(win):
    AgentWorker._shared_session_id = "20260101-000000-abcdef"
    win.chat_display.add_user_message("旧会话内容标记XYZ")

    win._on_new_session()
    assert AgentWorker._shared_session_id is None, "新建后共享 id 必须置空"
    text = win.chat_display.toPlainText()
    assert "已开始新对话" in text
    assert "旧会话内容标记XYZ" not in text, "新建会话必须清空聊天页"
    assert len(win.chat_display.bubbles) == 1


# ── e) _on_open_sessions：拒绝 / 端到端恢复 ─────────────────────


def test_open_sessions_refused_while_worker_running(win, monkeypatch):
    """worker 在跑时打开历史应被拒绝（不得弹对话框）。"""
    from tools.gui.widgets import session_dialog as sd

    opened = []
    monkeypatch.setattr(sd.SessionDialog, "exec",
                        lambda self: opened.append(True) or 0)

    class _BusyWorker:
        def isRunning(self):  # noqa: N802
            return True

    win.agent_worker = _BusyWorker()
    try:
        win._on_open_sessions()
        assert "仍在回答中" in win.chat_display.toPlainText()
        assert not opened, "busy 时应直接拒绝，不得打开历史对话框"

        win._on_new_session()
        assert "仍在回答中" in win.chat_display.toPlainText()
    finally:
        win.agent_worker = None


def test_open_sessions_restores_chosen_session(win, tmp_path, monkeypatch):
    """端到端：选中恢复 → 共享 id 切到所选会话 + 消息回放到聊天页。"""
    sid = _write_session(tmp_path, [("user", "历史提问"), ("assistant", "历史解答")],
                         session_id="20260101-140000-abcdef")
    from tools.gui.widgets import session_dialog as sd

    monkeypatch.setattr(sd.SessionDialog, "exec", lambda self: 1)
    monkeypatch.setattr(sd.SessionDialog, "chosen_session_id", lambda self: sid)

    win._on_open_sessions()
    assert AgentWorker._shared_session_id == sid, "恢复后后续消息必须复用所选会话"
    text = win.chat_display.toPlainText()
    assert "历史提问" in text and "历史解答" in text
    assert "已恢复会话" in text

    # 链路闭环：恢复后新建的 worker（= 用户下一条消息）复用所选会话 id，
    # AgentRunner 构造时据此自动重建 history（loop.py._restore_history_from_log）
    worker = AgentWorker({}, "继续问", workspace_root=tmp_path)
    assert worker._session_id == sid


# ── f) chat_tab 按钮 ────────────────────────────────────────────


def test_chat_tab_has_session_buttons(win, monkeypatch):
    from tools.gui.widgets import session_dialog as sd

    btns = win.findChildren(QPushButton, "SessionBtn")
    assert len(btns) == 2, "对话页应有「历史 / 新建」两个会话按钮"
    texts = [b.text() for b in btns]
    assert any("历史" in t for t in texts)
    assert any("新建" in t for t in texts)
    for b in btns:
        assert b.cursor().shape() == Qt.PointingHandCursor

    # 「历史」→ 打开会话对话框；「新建」→ 清屏并提示
    opened = []
    monkeypatch.setattr(sd.SessionDialog, "exec",
                        lambda self: opened.append(True) or 0)
    next(b for b in btns if "历史" in b.text()).click()
    assert opened, "「历史」按钮应打开会话对话框"

    next(b for b in btns if "新建" in b.text()).click()
    assert "已开始新对话" in win.chat_display.toPlainText()
