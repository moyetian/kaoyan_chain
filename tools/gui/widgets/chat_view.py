# -*- coding: utf-8 -*-
"""P2 组件族：对话页消息视图（ChatView / ChatBubble）

[为什么换掉 QTextEdit]
改造前的对话页是「一个占满的 QTextEdit + 占位符文字」：所有内容都是同一种
等宽文本块，用户与私教的发言无法区分，空状态只有一句占位符、没有任何引导。
现在改为气泡视图：

  * 用户消息 → 右侧气泡（``#UserBubble``），私教回复 → 左侧气泡（``#AgentBubble``），
    工具/系统播报 → 虚线系统气泡（``#SystemBubble``）；
  * 空状态给出 3~4 个**可点击示例提示词**（点击只填入输入框，不直接发送，
    避免误触发起计费调用）。

[契约兼容] ``win.chat_display`` 仍是本视图，保留 ``append()`` / ``toPlainText()``
两个既有调用点（上传提示、向导热更新通知、动作回显），并新增
``add_user_message`` / ``add_agent_message`` / ``append_agent_chunk`` 三个流式接口。

[样式铁律] 全部走 objectName + 主题 QSS，组件内部不写内联样式。
"""

from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

#: 空状态示例提示词：(按钮文字, 实际填入输入框的口令)
EXAMPLE_PROMPTS: Tuple[Tuple[str, str], ...] = (
    ("拆解长难句", "英语报到"),
    ("帽子词秒杀自测", "政治报到"),
    ("批改作业 · 错因归因", "交作业"),
    ("查漏：薄弱点雷达", "查漏"),
)

#: 气泡种类 → objectName
_BUBBLE_NAMES = {"user": "UserBubble", "agent": "AgentBubble", "system": "SystemBubble"}

#: 气泡种类 → 元信息（发言者）文字
_BUBBLE_META = {"user": "你", "agent": "私教"}


class ChatBubble(QFrame):
    """一条消息气泡：可选发言者元信息 + 正文（私教侧按 Markdown 渲染）。"""

    def __init__(self, text: str = "", kind: str = "system", parent=None):
        super().__init__(parent)
        self.kind = kind
        #: 是否处于流式写入中（决定后续 chunk 追加到本条还是新起一条）
        self.streaming = False
        self.setObjectName(_BUBBLE_NAMES.get(kind, "SystemBubble"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        meta_text = _BUBBLE_META.get(kind, "")
        if meta_text:
            meta = QLabel(meta_text)
            meta.setObjectName("BubbleMeta")
            layout.addWidget(meta)

        self._label = QLabel(text)
        self._label.setObjectName("BubbleText")
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if kind == "agent":
            # 私教回复按 Markdown 渲染，避免把 **加粗** 之类的源码直接漏给用户
            self._label.setTextFormat(Qt.TextFormat.MarkdownText)
        layout.addWidget(self._label)

    def text(self) -> str:
        return self._label.text()

    def append_text(self, chunk: str) -> None:
        self._label.setText(self._label.text() + chunk)


class ChatView(QScrollArea):
    """对话消息视图：气泡列表 + 空状态引导（替代原来的只读 QTextEdit）。"""

    #: 空状态示例被点击（携带应填入输入框的口令）
    example_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ChatView")
        self.setWidgetResizable(True)
        self.setMinimumHeight(220)

        canvas = QWidget()
        canvas.setObjectName("ChatCanvas")
        self._canvas = canvas
        self._layout = QVBoxLayout(canvas)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(10)

        self.bubbles: List[ChatBubble] = []
        self.example_pills: List[QPushButton] = []
        self.empty_state = self._build_empty_state()
        self._layout.addWidget(self.empty_state)
        self._layout.addStretch(1)
        self.setWidget(canvas)

    # ── 空状态 ──────────────────────────────────────────────
    def _build_empty_state(self) -> QFrame:
        box = QFrame()
        box.setObjectName("ChatEmpty")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(6)

        title = QLabel("考研全科专属私教已就绪")
        title.setObjectName("ChatEmptyTitle")
        hint = QLabel("输入口令开始辅导（如：英语报到 / 交作业），或点下面的示例快速进入状态：")
        hint.setObjectName("ChatEmptyHint")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        for label, prompt in EXAMPLE_PROMPTS:
            pill = QPushButton(label)
            pill.setObjectName("ExamplePill")
            pill.setCursor(Qt.PointingHandCursor)
            pill.setToolTip(f"点击填入输入框：{prompt}")
            pill.clicked.connect(lambda _checked=False, p=prompt: self.example_clicked.emit(p))
            self.example_pills.append(pill)
            row.addWidget(pill)
        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _hide_empty_state(self) -> None:
        if self.empty_state.isVisible():
            self.empty_state.setVisible(False)

    # ── 消息接口 ────────────────────────────────────────────
    def append(self, text: str) -> None:
        """系统/工具播报气泡（兼容改造前的 ``chat_display.append(...)`` 调用点）。"""
        self.add_bubble(text, kind="system")

    def add_user_message(self, text: str) -> ChatBubble:
        return self.add_bubble(text, kind="user")

    def add_agent_message(self, text: str) -> ChatBubble:
        bubble = self.add_bubble(text, kind="agent")
        bubble.streaming = False
        return bubble

    def finish_agent_message(self) -> None:
        """封口当前流式气泡：后续 chunk 会另起一条（收尾时调用）。"""
        for bubble in self.bubbles:
            bubble.streaming = False

    def append_agent_chunk(self, chunk: str) -> ChatBubble:
        """流式片段：续写当前私教气泡；没有正在流式写入的气泡则新起一条。"""
        last = self.bubbles[-1] if self.bubbles else None
        if last is not None and last.kind == "agent" and last.streaming:
            last.append_text(chunk)
            self._scroll_to_bottom()
            return last
        bubble = self.add_bubble(chunk, kind="agent")
        bubble.streaming = True
        return bubble

    def add_bubble(self, text: str, kind: str = "system") -> ChatBubble:
        """新建一条气泡：用户右对齐、私教与系统左对齐。"""
        for bubble in self.bubbles:
            bubble.streaming = False
        self._hide_empty_state()

        bubble = ChatBubble(text, kind, self._canvas)
        row = QWidget(self._canvas)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        if kind == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble, 4)
        elif kind == "agent":
            row_layout.addWidget(bubble, 4)
            row_layout.addStretch(1)
        else:
            row_layout.addWidget(bubble)

        # 插到末尾的 stretch 之前，保持消息时序
        self._layout.insertWidget(self._layout.count() - 1, row)
        self.bubbles.append(bubble)
        self._scroll_to_bottom()
        return bubble

    # ── 只读导出（兼容既有测试的 toPlainText() 断言） ────────
    def toPlainText(self) -> str:  # noqa: N802 - 对齐 QTextEdit 命名
        return "\n".join(bubble.text() for bubble in self.bubbles)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))


__all__ = ["ChatBubble", "ChatView", "EXAMPLE_PROMPTS"]
