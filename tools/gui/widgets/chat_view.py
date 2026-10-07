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

[公式渲染] Qt 原生 MarkdownText 不支持 LaTeX，``$$\frac{a}{b}$$`` 之类的源码会
直接裸露给考生。私教气泡现复用终端同源美化器 ``prettify_latex_for_terminal``
（``tools/skills/latex_beautifier.py``）把公式转成 Unicode 排版后再 setText，
保证 GUI / TUI / CLI 三端公式观感一致；美化失败一律降级为原文（绝不吞消息）。

[思考链折叠] ``append_step`` 把私教动作/工具步骤收进默认收起的
``StepBubble``（一行「🧠 思考过程 · N 步」，点击展开），避免长任务的几十条
步骤把对话页占满；``finish_step_group`` 在答案开始/收尾时封口。

[样式铁律] 全部走 objectName + 主题 QSS，组件内部不写内联样式。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

try:  # pragma: no cover - 取决于运行方式
    from skills.latex_beautifier import prettify_latex_for_terminal
except ImportError:  # pragma: no cover
    try:
        from tools.skills.latex_beautifier import prettify_latex_for_terminal
    except ImportError:  # pragma: no cover - 兜底：美化器不可用时保持原文渲染
        prettify_latex_for_terminal = None  # type: ignore[assignment]

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
    """一条消息气泡：可选发言者元信息 + 正文（私教侧按 Markdown 渲染）。

    私教气泡的正文经 LaTeX 美化后再 setText：``_raw_text`` 始终保存原始
    文本（流式累积 / 兜底降级都基于它），``text()`` 返回上屏的显示文本。
    """

    def __init__(self, text: str = "", kind: str = "system", parent=None):
        super().__init__(parent)
        self.kind = kind
        #: 是否处于流式写入中（决定后续 chunk 追加到本条还是新起一条）
        self.streaming = False
        #: 原始文本（未美化）；显示文本由 _render() 派生
        self._raw_text = text
        self.setObjectName(_BUBBLE_NAMES.get(kind, "SystemBubble"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        meta_text = _BUBBLE_META.get(kind, "")
        if meta_text:
            meta = QLabel(meta_text)
            meta.setObjectName("BubbleMeta")
            layout.addWidget(meta)

        self._label = QLabel()
        self._label.setObjectName("BubbleText")
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if kind == "agent":
            # 私教回复按 Markdown 渲染，避免把 **加粗** 之类的源码直接漏给用户
            self._label.setTextFormat(Qt.TextFormat.MarkdownText)
        layout.addWidget(self._label)
        self._render()

    def _display_text(self) -> str:
        """由 ``_raw_text`` 派生显示文本：私教侧做公式美化，其余原样。"""
        if self.kind != "agent" or prettify_latex_for_terminal is None:
            return self._raw_text
        try:
            rendered = prettify_latex_for_terminal(self._raw_text)
        except Exception:  # noqa: BLE001 - 美化失败必须降级为原文，绝不吞消息
            return self._raw_text
        return rendered if rendered is not None else self._raw_text

    def _render(self) -> None:
        self._label.setText(self._display_text())

    def text(self) -> str:
        return self._label.text()

    def append_text(self, chunk: str) -> None:
        self._raw_text += chunk
        self._render()


class StepBubble(ChatBubble):
    """思考链折叠块：默认收起的一行「🧠 思考过程 · N 步」，点击展开步骤正文。

    与 ChatBubble 同族（可放入 ``ChatView.bubbles``），但 ``streaming`` 恒为
    False —— 不参与 ``append_agent_chunk`` 的流式续写；``text()`` 返回步骤
    正文，保证 ``toPlainText()`` 仍能导出全部步骤文本。
    """

    def __init__(self, parent=None):
        QFrame.__init__(self, parent)  # 跳过 ChatBubble 的单一正文骨架
        self.kind = "steps"
        self.streaming = False
        self.setObjectName("StepGroup")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 6)
        layout.setSpacing(2)

        self._steps: List[str] = []

        self._toggle = QToolButton()
        self._toggle.setObjectName("StepToggle")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setToolTip("展开 / 收起思考过程")
        self._toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self._toggle)

        self._body = QLabel()
        self._body.setObjectName("StepBody")
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._body.setVisible(False)  # 默认折叠
        layout.addWidget(self._body)

        self._refresh_header()

    def _on_toggled(self, checked: bool) -> None:
        self._body.setVisible(checked)
        self._refresh_header()

    def _refresh_header(self) -> None:
        arrow = "▾" if self._toggle.isChecked() else "▸"
        self._toggle.setText(f"{arrow} 🧠 思考过程 · {len(self._steps)} 步")

    def add_step(self, text: str) -> None:
        self._steps.append(text)
        self._body.setText("\n".join(self._steps))
        self._refresh_header()

    def text(self) -> str:
        return "\n".join(self._steps)


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
        #: 当前未封口的思考折叠块（append_step 续写它；finish_step_group 置空）
        self._open_step_group: Optional[StepBubble] = None
        self.empty_state = self._build_empty_state()
        # 空状态整体垂直居中：内容上下各一个伸缩项（伸缩因子相同才会均分空间，
        # 只给下方 addStretch 会让空状态贴顶）。首个气泡出现时收起上方伸缩
        # （见 _hide_empty_state），让消息列表恢复顶部对齐。
        self._layout.addStretch(1)
        self._top_stretch = self._layout.itemAt(0).spacerItem()
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

        icon = QLabel("💬")
        icon.setObjectName("ChatEmptyIcon")
        layout.addWidget(icon)

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
        # 收起上方伸缩：Fixed 策略使 min=max=0（Minimum 策略下该项仍会分走空间），
        # 消息从顶部开始排布
        self._top_stretch.changeSize(
            0, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ── 消息接口 ────────────────────────────────────────────
    def append(self, text: str) -> None:
        """系统/工具播报气泡（兼容改造前的 ``chat_display.append(...)`` 调用点）。"""
        self.add_bubble(text, kind="system")

    def add_user_message(self, text: str) -> ChatBubble:
        # 用户新发言 = 上一轮彻底结束：先封口思考折叠块，防停止/丢回复场景下
        # 新一轮步骤续进上一轮未封口的块里（步骤分组边界必须跟消息对齐）
        self.finish_step_group()
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

    # ── 思考链折叠 ──────────────────────────────────────────
    def append_step(self, text: str) -> Optional[StepBubble]:
        """把一条思考链步骤追加进当前折叠块；没有（或已封口）则新起一块。"""
        if not text:
            return self._open_step_group
        group = self._open_step_group
        if group is None:
            group = StepBubble(self._canvas)
            self._hide_empty_state()
            self._insert_row(group, "steps")
            self.bubbles.append(group)
            self._open_step_group = group
        group.add_step(text)
        self._scroll_to_bottom()
        return group

    def finish_step_group(self) -> None:
        """封口当前思考折叠块：后续 step 新起一块。

        幂等且零成本 —— ``_on_agent_chunk`` 每个流式片段都会调用它
        （答案开始/收尾即封口），不能做任何布局遍历。
        """
        self._open_step_group = None

    def clear(self) -> None:
        """清空全部消息与折叠块，恢复初始空状态（「新建/恢复会话」前置接口）。"""
        self.finish_step_group()
        for bubble in list(self.bubbles):
            row = bubble.parentWidget()
            if row is not None and row is not self._canvas:
                self._layout.removeWidget(row)
                row.deleteLater()
        self.bubbles.clear()
        # 还原上方伸缩项（_hide_empty_state 曾把它压成 Fixed），空状态恢复垂直居中
        self._top_stretch.changeSize(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.empty_state.setVisible(True)

    def _insert_row(self, widget: QWidget, kind: str) -> None:
        """把 widget 包进一行并插到消息末尾：用户右对齐，其余左对齐。"""
        row = QWidget(self._canvas)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        if kind == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(widget, 4)
        elif kind == "agent":
            row_layout.addWidget(widget, 4)
            row_layout.addStretch(1)
        else:
            row_layout.addWidget(widget)

        # 插到末尾的 stretch 之前，保持消息时序
        self._layout.insertWidget(self._layout.count() - 1, row)

    def add_bubble(self, text: str, kind: str = "system") -> ChatBubble:
        """新建一条气泡：用户右对齐、私教与系统左对齐。"""
        for bubble in self.bubbles:
            bubble.streaming = False
        self._hide_empty_state()

        bubble = ChatBubble(text, kind, self._canvas)
        self._insert_row(bubble, kind)
        self.bubbles.append(bubble)
        self._scroll_to_bottom()
        return bubble

    # ── 只读导出（兼容既有测试的 toPlainText() 断言） ────────
    def toPlainText(self) -> str:  # noqa: N802 - 对齐 QTextEdit 命名
        return "\n".join(bubble.text() for bubble in self.bubbles)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))


__all__ = ["ChatBubble", "ChatView", "StepBubble", "EXAMPLE_PROMPTS"]
