# -*- coding: utf-8 -*-
"""GUI 会话管理对话框（历史列表 / 恢复 / 删除 / 清理）——修复④

[为什么单独成文件] 会话管理是独立交互（列表 + 动作按钮），塞进主窗口只会
继续撑大 main_window；模式参照 ``wechat_search_dialog`` 的 QDialog 惯例，
但本对话框**纯本地文件操作、无网络线程**，不需要其线程收尾设施。

[口径] 用户提的「归档对话」按「删除 + 清理旧会话」实现（fork 暂不做）：
「删除」删单段会话；「清理旧会话」保留最近 :data:`PRUNE_KEEP` 段、删除更旧的，
先 dry-run 报数量再确认。

[双导入] gui.* / tools.gui.*、agent.session_log / tools.agent.session_log。
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from agent.session_log import list_sessions, prune_sessions, remove_session
except ImportError:  # pragma: no cover
    from tools.agent.session_log import (  # type: ignore
        list_sessions, prune_sessions, remove_session,
    )

#: 「清理旧会话」保留的最近会话数（与 CLI ``ky session prune`` 默认口径一致）
PRUNE_KEEP = 20
#: 会话 id 在列表中的显示截断长度
SID_DISPLAY_LEN = 15
#: 首条消息在列表中的显示截断长度
FIRST_TEXT_LEN = 30

#: 空列表提示行文案
EMPTY_HINT = "暂无历史会话"


class SessionDialog(QDialog):
    """历史会话列表：恢复 / 删除 / 清理旧会话。

    ``chosen_session_id()`` 仅在「恢复会话」确认后非空；``selected_session_id()``
    返回当前选中行（删除与恢复共用），未选中 / 空列表返回 None。
    """

    def __init__(self, workspace_root: Any = None, parent=None):
        super().__init__(parent)
        self.workspace_root = workspace_root
        self._sessions: list = []
        self._chosen_session_id: Optional[str] = None
        self.setWindowTitle("历史会话")
        self.setMinimumSize(760, 460)
        self._init_ui()
        self._refresh()

    # ── UI ──────────────────────────────────────────────────
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        hint = QLabel("选择一段历史会话点「恢复会话」继续对话；"
                      f"「清理旧会话」只保留最近 {PRUNE_KEEP} 段。")
        hint.setObjectName("SessionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["会话", "创建时间", "事件数", "消息数", "首条消息"])
        header = self.table.horizontalHeader()
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.restore_btn = QPushButton("恢复会话")
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.clicked.connect(self._on_restore)
        btn_row.addWidget(self.restore_btn)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("SecondaryBtn")
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.delete_btn)

        self.prune_btn = QPushButton("清理旧会话…")
        self.prune_btn.setObjectName("SecondaryBtn")
        self.prune_btn.setCursor(Qt.PointingHandCursor)
        self.prune_btn.clicked.connect(self._on_prune)
        btn_row.addWidget(self.prune_btn)

        btn_row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("SecondaryBtn")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ── 数据 ────────────────────────────────────────────────
    def _refresh(self) -> None:
        self._sessions = list_sessions(self.workspace_root)
        self.table.clearSpans()
        self.table.setRowCount(0)
        if not self._sessions:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem(EMPTY_HINT))
            self.table.setSpan(0, 0, 1, self.table.columnCount())
            return
        self.table.setRowCount(len(self._sessions))
        for row, item in enumerate(self._sessions):
            sid = str(item.get("session_id") or "")
            created = str(item.get("created_at") or "")[:19]
            first = str(item.get("first_user_text") or "")
            first = first.replace("\r", " ").replace("\n", " ")
            self.table.setItem(row, 0, QTableWidgetItem(sid[:SID_DISPLAY_LEN]))
            self.table.setItem(row, 1, QTableWidgetItem(created))
            self.table.setItem(row, 2, QTableWidgetItem(str(item.get("event_count") or 0)))
            self.table.setItem(row, 3, QTableWidgetItem(str(item.get("message_count") or 0)))
            self.table.setItem(row, 4, QTableWidgetItem(first[:FIRST_TEXT_LEN]))

    # ── 选择 ────────────────────────────────────────────────
    def selected_session_id(self) -> Optional[str]:
        """当前选中行的 session_id；未选中 / 空列表返回 None。"""
        row = self.table.currentRow()
        if row < 0 or row >= len(self._sessions):
            return None
        return str(self._sessions[row].get("session_id") or "") or None

    def chosen_session_id(self) -> Optional[str]:
        """「恢复会话」确认后的选择；未恢复返回 None。"""
        return self._chosen_session_id

    # ── 动作 ────────────────────────────────────────────────
    def _on_restore(self) -> None:
        sid = self.selected_session_id()
        if sid is None:
            QMessageBox.information(self, "提示", "请先选择一段历史会话。")
            return
        self._chosen_session_id = sid
        self.accept()

    def _on_delete(self) -> None:
        sid = self.selected_session_id()
        if sid is None:
            QMessageBox.information(self, "提示", "请先选择要删除的会话。")
            return
        ok = QMessageBox.question(
            self, "删除会话",
            f"确定删除会话 {sid[:SID_DISPLAY_LEN]}？此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        remove_session(self.workspace_root, sid)
        self._refresh()

    def _on_prune(self) -> None:
        sessions = list_sessions(self.workspace_root)
        extra = len(sessions) - PRUNE_KEEP
        if extra <= 0:
            QMessageBox.information(
                self, "清理旧会话",
                f"当前共 {len(sessions)} 段会话，未超过保留上限"
                f"（{PRUNE_KEEP} 段），无需清理。")
            return
        ok = QMessageBox.question(
            self, "清理旧会话",
            f"将删除最旧的 {extra} 段会话（保留最近 {PRUNE_KEEP} 段），确定继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        prune_sessions(self.workspace_root, keep=PRUNE_KEEP)
        self._refresh()


__all__ = ["SessionDialog", "PRUNE_KEEP", "SID_DISPLAY_LEN", "EMPTY_HINT"]
