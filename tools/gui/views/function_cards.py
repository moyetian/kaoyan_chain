# -*- coding: utf-8 -*-
"""功能卡片区视图：10 个可点击、可键盘激活的模块入口

卡片清单集中在 ``CARD_ITEMS``，与「今日任务 / 靶向组卷 / …」一一对应；
样式与键盘可达性由 ``widgets/function_card.py`` 负责。
"""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QScrollArea, QWidget

try:  # pragma: no cover - 取决于运行方式
    from gui.widgets.function_card import FunctionCard
except ImportError:  # pragma: no cover
    from tools.gui.widgets.function_card import FunctionCard  # type: ignore

#: 功能卡片清单（SVG 图标 key / 标题 / 说明 / 动作别名）
CARD_ITEMS = (
    ("today", "今日任务", "查看四科任务量与推进打卡", "today"),
    ("compose", "靶向组卷", "按考点与难度智能拼卷演练", "compose"),
    ("variant", "同源变式", "薄弱考点同源变式真题检索", "variant"),
    ("diff", "考纲Diff", "新旧考纲层级对比与动荡率", "diff"),
    ("ingest", "切片入库", "真题/模拟卷结构化切片入库", "ingest"),
    ("scout", "院校侦察", "研招网与社媒实名口碑研报", "scout"),
    ("compare", "双校对标", "双校初复试指标横向对标", "compare"),
    ("watch", "简章监控", "高校研究生院简章变动预警", "watch"),
    ("build", "看板更新", "重新编译掌握度雷达看板", "build"),
    ("wechat_search", "公众号检索", "微信公众号考研文章检索与沉淀", "wechat_search"),
)

#: 每行卡片数
COLUMNS = 5


def build(win) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setObjectName("CardScrollArea")
    scroll.setMinimumHeight(196)
    scroll.setMaximumHeight(224)

    container = QWidget()
    grid = QGridLayout(container)
    grid.setSpacing(12)
    grid.setContentsMargins(8, 8, 8, 8)

    win._feature_buttons = []            # 对外契约名（自检脚本按此计数）
    win.feature_cards = []
    for idx, (icon, title, desc, alias) in enumerate(CARD_ITEMS):
        card = FunctionCard(icon, title, desc, alias, container)
        card.clicked.connect(win._on_card_clicked)
        win._feature_buttons.append(card)
        win.feature_cards.append(card)
        row, col = divmod(idx, COLUMNS)
        grid.addWidget(card, row, col)

    # 用当前主题色渲染图标（切主题时由 win._refresh_card_icons() 重渲染）
    color = win._theme.color("acc") if hasattr(win, "_theme") else ""
    for card in win.feature_cards:
        card.refresh_icon(color)

    scroll.setWidget(container)
    return scroll


__all__ = ["CARD_ITEMS", "COLUMNS", "build"]
