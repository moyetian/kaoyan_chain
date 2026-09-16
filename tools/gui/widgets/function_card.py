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
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

try:  # pragma: no cover - 取决于运行方式
    from gui.widgets.icons import render_icon
except ImportError:  # pragma: no cover
    from tools.gui.widgets.icons import render_icon  # type: ignore


class FunctionCard(QFrame):
    """功能模块卡片：可点击、可键盘激活。"""

    clicked = Signal(str)      # 发射功能别名

    def __init__(self, icon: str, title: str, desc: str, alias: str, parent=None):
        super().__init__(parent)
        self.alias = alias
        self._icon_key = icon           # SVG 模板 key（见 widgets/icons.py）
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

        # 标题行：SVG 图标 + 文字（横向排列），替代改造前的 emoji 字符拼接。
        # emoji 跨系统字形差异大（彩色/单色、宽高不一），SVG 矢量渲染保证一致。
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)

        self._icon_label = QLabel()
        self._icon_label.setObjectName("CardIcon")
        self._icon_label.setFixedSize(22, 22)
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")

        title_row.addWidget(self._icon_label)
        title_row.addWidget(title_label)
        title_row.addStretch()

        desc_label = QLabel(desc)
        desc_label.setObjectName("CardDesc")
        desc_label.setWordWrap(True)

        layout.addLayout(title_row)
        layout.addWidget(desc_label)

    # ── 交互 ────────────────────────────────────────────────
    def refresh_icon(self, color: str) -> None:
        """用主题色重新渲染 SVG 图标（切主题时由 MainWindow 调用）。"""
        pm = render_icon(self._icon_key, 20, color)
        if not pm.isNull():
            self._icon_label.setPixmap(pm)

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
