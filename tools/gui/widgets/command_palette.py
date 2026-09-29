# -*- coding: utf-8 -*-
"""P2 组件族：Ctrl+K 命令面板（Raycast 模式）

[为什么需要]
导航收敛到 rail 后仍有 14 个条目（4 页面 + 10 工具），逐个用眼睛找太慢。
命令面板把「全部导航项 + 全部工具动作」放进一个可搜索列表：输入即过滤，
↑↓ 选择，Enter 执行，Esc 关闭。

[职责边界] 面板只负责「搜索 + 选中 + 发信号」，不知道任何业务：
执行动作由窗口订阅 :attr:`CommandPalette.activated` 完成（key 前缀区分
``view:`` / ``tool:``）。这样面板可以在离屏测试里独立驱动，不触发任何后端。

[分组标题行 · W13-7] 结果列表按 ``PaletteEntry.group`` 插入**不可选中**的标题行
（``Qt.NoItemFlags`` + 空 key）：标题行只是视觉分桶，不参与 ``visible_keys()``
计数契约，键盘流（初始选中 / ↑↓）与 Enter 执行都显式跳过它——否则 Enter 会对
标题行发出 ``"None"``。标题行的文字用 ``#PaletteGroupHeader`` 的 QLabel 承载。

[空结果提示行 · W13 验收修复 R3-1] 查询无匹配时插入一行**不可选中**的灰字提示
（:data:`EMPTY_HINT_TEXT`，``#PaletteEmptyHint``）：修复前空结果是一片空白，
用户不知道是"没这个词"还是"面板坏了"。提示行复用标题行机制（空 key、不参与
计数契约），并给出「错题 / 组卷 / 看板」三个可用示例词。

[口语别名 · W13 验收修复 R3-2] ``PaletteEntry.keywords`` 收纳口语词表（如
「考情」→研招情报、「刷题」→靶向组卷），并入 :meth:`PaletteEntry.haystack`
参与子串匹配。

[样式铁律] 全部走 objectName + 主题 QSS，组件内部不写内联样式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
)

#: 面板条目标签前缀（窗口按前缀分发）
VIEW_PREFIX = "view:"
TOOL_PREFIX = "tool:"

#: 分组标题行的 UserRole 值：空 key（``visible_keys()`` 过滤；执行前二次拦截）
GROUP_HEADER_KEY = ""

#: 空结果提示文案（无匹配时插入的不可选中提示行；测试引用本常量）
EMPTY_HINT_TEXT = "未找到匹配命令，试试：错题 / 组卷 / 看板"

#: ``#PaletteList::item`` 的上下 padding 之和（8+8）。``setItemWidget`` 的控件几何
#: = item 矩形的**文本子区域**，会被该 padding 扣除——标题行高度必须补回这一份，
#: 否则 12px 字号只剩 10px 可用高度（实测纵向裁切）。改动该 padding 需同步本值。
_LIST_ITEM_PADDING_V = 16


@dataclass(frozen=True)
class PaletteEntry:
    """一条可搜索条目：``key`` 供窗口分发，其余字段参与展示与过滤。

    ``keywords``（W13 验收修复 R3-2）收纳口语别名（空格分隔），必须放**最后**
    并带默认值，保持既有位置参数 ``(key, title, group, hint)`` 兼容。
    """

    key: str
    title: str
    group: str = ""
    hint: str = ""
    keywords: str = ""

    def haystack(self) -> str:
        """参与子串匹配的文本（标题 + 分组 + 说明 + 口语别名）。"""
        return f"{self.title} {self.group} {self.hint} {self.keywords}".lower()


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

        self.hint = QLabel("↑↓ 选择 · Enter 执行 · Esc 关闭 · 完整 42 条命令见 ky commands")
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
        """按查询词重建结果列表：按分组插入标题行，并默认选中首个可执行条目。

        分组标题行不可选中、不带可执行 key（见 :data:`GROUP_HEADER_KEY`），
        因此 ``setCurrentRow(0)`` 必须跳到**首个可执行行**而不是第一行。
        无匹配时插入一行不可选中的提示（:data:`EMPTY_HINT_TEXT`）——
        空列表零提示会让用户分不清"没这个词"还是"面板坏了"。
        """
        self.list.clear()
        matched = self.matches(text)
        if not matched:
            self._add_empty_hint()
            return
        last_group = ""
        for entry in matched:
            if entry.group and entry.group != last_group:
                self._add_group_header(entry.group)
                last_group = entry.group
            self._add_entry(entry)
        first = self._next_selectable_row(0, 1)
        if first >= 0:
            self.list.setCurrentRow(first)

    def _add_label_row(self, text: str, object_name: str) -> None:
        """插入不可选中的纯文字行（分组标题 / 空结果提示共用机制）。

        行高 = 标签自身高度 + 被 item padding 扣掉的文本子区域内缩（防裁切）。
        """
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)                  # 不可选中 / 不可点击
        item.setData(Qt.UserRole, GROUP_HEADER_KEY)    # 空 key：计数与执行都不参与
        self.list.addItem(item)
        label = QLabel(text)
        label.setObjectName(object_name)
        self.list.setItemWidget(item, label)
        label.ensurePolished()
        item.setSizeHint(QSize(0, label.sizeHint().height() + _LIST_ITEM_PADDING_V))

    def _add_group_header(self, title: str) -> None:
        """插入不可选中的分组标题行（文字走 QLabel + objectName，零内联样式）。"""
        self._add_label_row(title, "PaletteGroupHeader")

    def _add_empty_hint(self) -> None:
        """插入不可选中的空结果提示行（灰字，样式见 ``#PaletteEmptyHint``）。"""
        self._add_label_row(EMPTY_HINT_TEXT, "PaletteEmptyHint")

    def _add_entry(self, entry: PaletteEntry) -> None:
        label = f"{entry.title}    {entry.hint}".rstrip()
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, entry.key)
        item.setToolTip(f"[{entry.group}] {entry.title} · {entry.hint}")
        self.list.addItem(item)

    def _next_selectable_row(self, start: int, step: int) -> int:
        """从 ``start`` 起沿 ``step`` 方向找最近的可执行行（跳过标题行）。

        可执行 = 「可选中」且「带非空 key」两个条件同时成立（标题行两个都不满足，
        双保险防未来某一处被单独改坏）。越界返回 -1。
        """
        row = start
        count = self.list.count()
        while 0 <= row < count:
            item = self.list.item(row)
            if (item is not None and item.flags() & Qt.ItemIsSelectable
                    and item.data(Qt.UserRole)):
                return row
            row += step
        return -1

    def visible_keys(self) -> List[str]:
        """当前结果列表里的可执行 key（标题行空 key 不参与；供测试与自检断言）。"""
        keys = (self.list.item(i).data(Qt.UserRole) for i in range(self.list.count()))
        return [str(key) for key in keys if key]

    # ── 执行 ────────────────────────────────────────────────
    def activate_current(self) -> str:
        """执行当前选中项：发 ``activated`` 并关闭面板；无选中/标题行不做任何事。"""
        item = self.list.currentItem()
        if item is None or not item.flags() & Qt.ItemIsSelectable:
            return ""
        key = item.data(Qt.UserRole)
        if not key:
            return ""
        self.activated.emit(str(key))
        self.close()
        return str(key)

    # ── 键盘 ────────────────────────────────────────────────
    def keyPressEvent(self, event):  # noqa: N802 - Qt 命名
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        if key in (Qt.Key_Down, Qt.Key_Up):
            step = 1 if key == Qt.Key_Down else -1
            count = self.list.count()
            if count:
                row = self.list.currentRow()
                start = row + step if row >= 0 else (0 if step > 0 else count - 1)
                target = self._next_selectable_row(start, step)
                if target >= 0:
                    self.list.setCurrentRow(target)
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = [
    "CommandPalette", "EMPTY_HINT_TEXT", "GROUP_HEADER_KEY", "PaletteEntry",
    "TOOL_PREFIX", "VIEW_PREFIX",
]
