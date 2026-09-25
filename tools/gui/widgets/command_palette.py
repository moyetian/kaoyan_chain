# -*- coding: utf-8 -*-
"""P2 组件族：Ctrl+K 命令面板（Raycast 模式）

[为什么需要]
导航收敛到 rail 后仍有 14 个条目（4 页面 + 10 工具），逐个用眼睛找太慢。
命令面板把「全部导航项 + 全部工具动作」放进一个可搜索列表：输入即过滤，
↑↓ 选择，Enter 执行，Esc 关闭。

[职责边界] 面板只负责「搜索 + 选中 + 发信号」，不知道任何业务：
执行动作由窗口订阅 :attr:`CommandPalette.activated` 完成（key 前缀区分
``view:`` / ``tool:``）。这样面板可以在离屏测试里独立驱动，不触发任何后端。

[样式铁律] 全部走 objectName + 主题 QSS，组件内部不写内联样式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
)

#: 面板条目标签前缀（窗口按前缀分发）
VIEW_PREFIX = "view:"
TOOL_PREFIX = "tool:"


@dataclass(frozen=True)
class PaletteEntry:
    """一条可搜索条目：``key`` 供窗口分发，其余字段参与展示与过滤。"""

    key: str
    title: str
    group: str = ""
    hint: str = ""

    def haystack(self) -> str:
        """参与子串匹配的文本（标题 + 分组 + 说明）。"""
        return f"{self.title} {self.group} {self.hint}".lower()


class CommandPalette(QDialog):
    """命令面板对话框：搜索框 + 结果列表 + 键盘操作提示。"""

    #: 用户确认执行某条条目（携带 ``PaletteEntry.key``）
    activated = Signal(str)

    def __init__(self, entries: Iterable[PaletteEntry], parent=None):
        super().__init__(parent)
        self.setObjectName("PaletteDialog")
        self.setWindowTitle("命令面板")
        self.setModal(True)
        self.resize(560, 420)

        self.entries: Tuple[PaletteEntry, ...] = tuple(entries)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.input = QLineEdit()
        self.input.setObjectName("PaletteInput")
        self.input.setPlaceholderText("搜索页面或工具动作（如：错题 / 组卷 / 看板 / 侦察）…")
        self.input.textChanged.connect(self.refresh)
        self.input.returnPressed.connect(self.activate_current)
        layout.addWidget(self.input)

        self.list = QListWidget()
        self.list.setObjectName("PaletteList")
        self.list.setUniformItemSizes(False)
        self.list.itemActivated.connect(lambda _item: self.activate_current())
        self.list.itemClicked.connect(lambda _item: self.activate_current())
        layout.addWidget(self.list, stretch=1)

        self.hint = QLabel("↑↓ 选择 · Enter 执行 · Esc 关闭")
        self.hint.setObjectName("PaletteHint")
        layout.addWidget(self.hint)

        self.refresh("")

    # ── 过滤 ────────────────────────────────────────────────
    def matches(self, text: str) -> List[PaletteEntry]:
        """子串匹配（空查询 = 全部条目，与 Raycast 的空态一致）。"""
        query = (text or "").strip().lower()
        if not query:
            return list(self.entries)
        return [e for e in self.entries if query in e.haystack()]

    def refresh(self, text: str = "") -> None:
        """按查询词重建结果列表，并默认选中第一条。"""
        self.list.clear()
        for entry in self.matches(text):
            label = f"{entry.title}    {entry.hint}".rstrip()
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry.key)
            item.setToolTip(f"[{entry.group}] {entry.title} · {entry.hint}")
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def visible_keys(self) -> List[str]:
        """当前结果列表里的 key（供测试与自检断言）。"""
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    # ── 执行 ────────────────────────────────────────────────
    def activate_current(self) -> str:
        """执行当前选中项：发 ``activated`` 并关闭面板；无结果时不做任何事。"""
        item = self.list.currentItem()
        if item is None:
            return ""
        key = str(item.data(Qt.UserRole))
        self.activated.emit(key)
        self.close()
        return key

    # ── 键盘 ────────────────────────────────────────────────
    def keyPressEvent(self, event):  # noqa: N802 - Qt 命名
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        if key in (Qt.Key_Down, Qt.Key_Up):
            row = self.list.currentRow()
            step = 1 if key == Qt.Key_Down else -1
            count = self.list.count()
            if count:
                self.list.setCurrentRow(max(0, min(count - 1, row + step)))
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = ["CommandPalette", "PaletteEntry", "TOOL_PREFIX", "VIEW_PREFIX"]
