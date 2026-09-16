# -*- coding: utf-8 -*-
"""
GUI 功能卡片组件 (FunctionCard)

[改造要点]
1. **不再内联 setStyleSheet**。改造前卡片在构造函数里写死深色
   （`background: #1a1e2e` 等），而 Qt 中 widget 自身样式表优先级高于
   `QApplication.setStyleSheet` —— 于是切换浅色主题时这些卡片依然是深底，
   表现为「亮色主题是坏的」。现改为 `setObjectName` + QSS 选择器，
   颜色全部来自主题 token（见 tools/theme）。
2. **键盘可达**。改造前是 QFrame + mousePressEvent 自绘，没有焦点策略也没有
   键盘处理，Tab 永远落不到卡片上、键盘用户完全无法触发。
   现补 `setFocusPolicy(StrongFocus)` 与 Enter/Space 激活；
   可见的焦点环由主题 QSS 的 `*:focus` 规则统一提供。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class FunctionCard(QFrame):
    """功能模块卡片：可点击、可键盘激活。"""

    clicked = Signal(str)      # 发射功能别名

    def __init__(self, icon: str, title: str, desc: str, alias: str, parent=None):
        super().__init__(parent)
        self.alias = alias
        # 样式一律由 objectName 选择器提供（#FunctionCard / #CardTitle / #CardDesc）
        self.setObjectName("FunctionCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(200, 84)
        self.setFocusPolicy(Qt.StrongFocus)          # 键盘可达
        self.setAccessibleName(f"{title}：{desc}")    # 屏幕阅读器可读
        self.setToolTip(f"{title}：{desc}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        title_label = QLabel(f"{icon}  {title}")
        title_label.setObjectName("CardTitle")
        desc_label = QLabel(desc)
        desc_label.setObjectName("CardDesc")
        desc_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(desc_label)

    # ── 交互 ────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._emit_clicked()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        """Enter / Space 激活（与按钮一致的无障碍约定）。"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._emit_clicked()
            event.accept()
            return
        super().keyPressEvent(event)

    def _emit_clicked(self) -> None:
        self.clicked.emit(self.alias)


__all__ = ["FunctionCard"]
