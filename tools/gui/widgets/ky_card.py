# -*- coding: utf-8 -*-
"""P2 组件族：KYCard（通用卡片容器）与 KYStatTile（大数字统计块）

[为什么需要]
改造前窗口里所有信息都是「一整块 QTextEdit 直出 Markdown」——错题本是文本墙、
任务页只有进度条，没有可复用的信息容器。这里补两个最小可复用件：

  * ``KYCard``     —— 圆角描边卡片，承载「标题 + 元信息 + 任意内容」；
  * ``KYStatTile`` —— 大数字 + caption（+ 可选趋势）的统计块。

[样式铁律] 组件内部**不写任何内联样式**：颜色/圆角/字号全部由 objectName
选择器（``#KYCard`` / ``#StatValue`` …）在主题 QSS 里给出，这样浅色主题
才不会被控件自身样式表压过（项目既有回归测试会扫描内联色值）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

#: 统计块趋势文字的语气 → QSS 属性选择器 ``#StatTrend[tone="..."]``
TREND_TONES = ("ok", "warn", "bad")


class KYCard(QFrame):
    """通用卡片容器：可选头部（标题 + 右侧元信息）+ 任意内容区。

    用法::

        card = KYCard()
        card.set_header("2024 真题 · 第 3 题", meta="到期 09-24")
        card.body.addWidget(QLabel("题干摘要…"))
    """

    def __init__(self, parent=None, object_name: str = "KYCard"):
        super().__init__(parent)
        self.setObjectName(object_name)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 10, 12, 10)
        self._root.setSpacing(6)

        self._header = QWidget()
        self._header.setObjectName("KYCardHeader")
        self._header_row = QHBoxLayout(self._header)
        self._header_row.setContentsMargins(0, 0, 0, 0)
        self._header_row.setSpacing(8)

        self._title = QLabel("")
        self._title.setObjectName("KYCardTitle")
        self._title.setWordWrap(True)
        self._meta = QLabel("")
        self._meta.setObjectName("KYCardMeta")
        self._meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._header_row.addWidget(self._title, stretch=1)
        self._header_row.addWidget(self._meta)
        self._root.addWidget(self._header)
        self._header.setVisible(False)

        #: 内容区（调用方往里加控件）
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(4)
        self._root.addLayout(self.body)

    # ── API ─────────────────────────────────────────────────
    def set_header(self, title: str, meta: str = "") -> None:
        """设置卡片头部；title 为空则整行隐藏（避免空白头部占位）。"""
        self._title.setText(title)
        self._meta.setText(meta)
        self._header.setVisible(bool(title or meta))

    def add_widget(self, widget: QWidget) -> None:
        """往内容区追加一个控件。"""
        self.body.addWidget(widget)


class KYStatTile(QFrame):
    """统计块：大数字 + caption（+ 可选趋势文字）。

    数字用 ``#StatValue``（fs-hero 字阶），caption 用 ``#StatCaption``，
    趋势用 ``#StatTrend[tone=...]``——三者都只由主题 QSS 决定外观。
    """

    def __init__(self, caption: str = "", value: str = "0", parent=None):
        super().__init__(parent)
        self.setObjectName("StatTile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        self._value = QLabel(value)
        self._value.setObjectName("StatValue")
        self._caption = QLabel(caption)
        self._caption.setObjectName("StatCaption")
        self._trend = QLabel("")
        self._trend.setObjectName("StatTrend")
        self._trend.setProperty("tone", "ok")
        self._trend.setVisible(False)

        layout.addWidget(self._value)
        layout.addWidget(self._caption)
        layout.addWidget(self._trend)

    # ── API ─────────────────────────────────────────────────
    def set_value(self, value: str) -> None:
        self._value.setText(value)

    def value_text(self) -> str:
        return self._value.text()

    def set_caption(self, caption: str) -> None:
        self._caption.setText(caption)

    def caption_text(self) -> str:
        return self._caption.text()

    def set_trend(self, text: str, tone: str = "ok") -> None:
        """可选趋势文字；``tone`` ∈ :data:`TREND_TONES`（映射到 QSS 属性选择器）。"""
        self._trend.setText(text)
        self._trend.setProperty("tone", tone if tone in TREND_TONES else "ok")
        self._trend.setVisible(bool(text))
        # 动态属性变化后需要重新求值样式（Qt 不会自动重算）
        style = self._trend.style()
        if style is not None:
            style.unpolish(self._trend)
            style.polish(self._trend)


__all__ = ["KYCard", "KYStatTile", "TREND_TONES"]
