# -*- coding: utf-8 -*-
"""P2 组件族：左侧导航 rail（KYNavRail / KYNavItem / KYNavToolItem）

[为什么改成 rail]
改造前是「顶部 10 张等权重功能卡 2×5 平铺」+「4 个页签」两套互不相干的导航：
页面与动作同级（无主次）、10 张卡无分组、长尾功能占满首屏。现在导航收敛到
左侧 rail 的两组：

  * ``视图`` 组 —— 4 个页面（原 QTabWidget 的 4 个页签），紧凑行，
    激活态 = 「左侧 3px 强调条 + acc-sub 底」，**不用巨型高亮块**；
  * ``工具`` 组 —— 原 10 张功能卡的动作，点击仍走 ``win._on_card_clicked``。

[样式铁律] 组件内部不写任何内联样式，全部走 objectName + 主题 QSS。

[为什么工具项仍是卡片尺寸] 既有回归测试（``tests/test_qss_and_gui_ergonomics.py``）
对 ``win.feature_cards`` 断言「10 项、每项 ≥180×80、互不重叠」，且该测试不在本轮
允许修改的范围内 —— 故工具项沿用 FunctionCard 的尺寸与 ``_icon_label`` 契约，
只是视觉改为 rail 内的分组卡片。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

try:  # pragma: no cover - 取决于运行方式
    from gui.widgets.function_card import FunctionCard
    from gui.widgets.icons import render_icon
except ImportError:  # pragma: no cover
    from tools.gui.widgets.function_card import FunctionCard  # type: ignore
    from tools.gui.widgets.icons import render_icon  # type: ignore

#: rail 固定宽度：需容纳 ≥190px 宽的工具卡片（既有几何断言）
RAIL_WIDTH = 252

#: 视图项 / 工具项的图标像素尺寸
VIEW_ICON = 18
TOOL_ICON = 20


class KYNavItem(QPushButton):
    """视图导航项：紧凑行（图标 + 文字）。

    激活态交给 QSS 的 ``#NavItem:checked`` 选择器（左侧 3px 强调条 + acc-sub 底），
    互斥由 ``autoExclusive`` 保证 —— 键盘（Tab/Space）同样可达。
    """

    def __init__(self, icon_key: str, text: str, parent=None):
        super().__init__(text, parent)
        self._icon_key = icon_key
        self.setObjectName("NavItem")
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(38)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setIconSize(QSize(VIEW_ICON, VIEW_ICON))
        self.setToolTip(text)

    def refresh_icon(self, color: str) -> None:
        """按主题色重渲染 SVG 图标（切主题时由 rail 统一调用）。"""
        pm = render_icon(self._icon_key, VIEW_ICON, color)
        if pm is not None and not pm.isNull():
            self.setIcon(QIcon(pm))


class KYNavToolItem(FunctionCard):
    """工具项：图标 + 标题 + 说明的动作卡（沿用 FunctionCard 的键盘可达性）。"""

    def __init__(self, icon: str, title: str, desc: str, alias: str, parent=None):
        super().__init__(icon, title, desc, alias, parent)
        self.setObjectName("NavToolItem")
        self._title_label.setObjectName("NavToolTitle")
        self._desc_label.setObjectName("NavToolDesc")


class KYNavRail(QFrame):
    """左侧分组导航栏：``视图`` 组切页、``工具`` 组触发动作。"""

    #: 视图项被点击 → 页面索引
    view_clicked = Signal(int)
    #: 工具项被点击 → 功能别名（与原功能卡一致）
    tool_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(RAIL_WIDTH)

        self.view_items: list[KYNavItem] = []
        self.tool_items: list[KYNavToolItem] = []
        self.group_titles: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        brand = QLabel("导航")
        brand.setObjectName("NavBrand")
        self.palette_btn = QPushButton("Ctrl+K")
        self.palette_btn.setObjectName("NavPaletteBtn")
        self.palette_btn.setCursor(Qt.PointingHandCursor)
        self.palette_btn.setFocusPolicy(Qt.StrongFocus)
        self.palette_btn.setToolTip("打开命令面板（Ctrl+K）：搜索全部页面与工具动作")
        head.addWidget(brand)
        head.addStretch(1)
        head.addWidget(self.palette_btn)
        root.addLayout(head)

        scroll = QScrollArea()
        # 既有几何断言按此名查找承载功能卡片的可滚动区域
        scroll.setObjectName("CardScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName("NavRailCanvas")
        self._canvas_layout = QVBoxLayout(canvas)
        self._canvas_layout.setContentsMargins(0, 0, 4, 0)
        self._canvas_layout.setSpacing(6)
        self._canvas_layout.addStretch(1)
        scroll.setWidget(canvas)
        root.addWidget(scroll, stretch=1)

    # ── 组装 ────────────────────────────────────────────────
    def _append(self, widget: QWidget) -> None:
        """把控件插到末尾（末尾恒有一个 stretch 占位）。"""
        self._canvas_layout.insertWidget(self._canvas_layout.count() - 1, widget)

    def add_group_title(self, title: str) -> QLabel:
        """加一个分组标题（如「视图」/「工具」）。"""
        label = QLabel(title)
        label.setObjectName("NavGroupTitle")
        self.group_titles.append(title)
        self._append(label)
        return label

    def add_view_item(self, icon_key: str, text: str) -> KYNavItem:
        """加一个视图项（页面切换），返回该项供调用方连线。"""
        item = KYNavItem(icon_key, text, self)
        index = len(self.view_items)
        item.clicked.connect(lambda _checked=False, i=index: self.view_clicked.emit(i))
        self.view_items.append(item)
        self._append(item)
        return item

    def add_tool_item(self, icon: str, title: str, desc: str, alias: str) -> KYNavToolItem:
        """加一个工具项（动作触发），返回该项供调用方连线。"""
        item = KYNavToolItem(icon, title, desc, alias, self)
        item.clicked.connect(lambda emitted_alias: self.tool_clicked.emit(emitted_alias))
        self.tool_items.append(item)
        self._append(item)
        return item

    # ── 状态 ────────────────────────────────────────────────
    def set_active_index(self, index: int) -> None:
        """同步激活态（页面索引由 QTabWidget.currentChanged 驱动）。"""
        for i, item in enumerate(self.view_items):
            item.setChecked(i == index)

    def active_index(self) -> int:
        for i, item in enumerate(self.view_items):
            if item.isChecked():
                return i
        return -1

    def refresh_icons(self, color: str) -> None:
        """按当前主题主色重渲染全部图标（明暗切换时调用）。"""
        for item in self.view_items:
            item.refresh_icon(color)
        for item in self.tool_items:
            item.refresh_icon(color)


__all__ = ["KYNavItem", "KYNavRail", "KYNavToolItem", "RAIL_WIDTH", "TOOL_ICON", "VIEW_ICON"]
