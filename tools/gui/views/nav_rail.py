# -*- coding: utf-8 -*-
"""导航 rail 视图：把 KYNavRail 挂到主窗口，并给出命令面板条目表

分组（用户拍板）：
  * ``视图`` —— 原 4 个页签（私教对话 / 今日任务 / 错题本 / 研招情报）
  * ``工具`` —— 原 10 张功能卡的动作（清单唯一来源：``function_cards.CARD_ITEMS``）

[契约] 工具项同时登记为 ``win.feature_cards`` / ``win._feature_buttons``：
既有测试与自检脚本按这两个名字取「10 个功能卡控件」并读取 ``_icon_label``。
"""

from __future__ import annotations

try:  # pragma: no cover - 取决于运行方式
    from gui.views.function_cards import CARD_ITEMS
    from gui.widgets.command_palette import (
        TOOL_PREFIX, VIEW_PREFIX, CommandPalette, PaletteEntry,
    )
    from gui.widgets.nav_rail import KYNavRail
except ImportError:  # pragma: no cover
    from tools.gui.views.function_cards import CARD_ITEMS  # type: ignore
    from tools.gui.widgets.command_palette import (  # type: ignore
        TOOL_PREFIX, VIEW_PREFIX, CommandPalette, PaletteEntry,
    )
    from tools.gui.widgets.nav_rail import KYNavRail  # type: ignore

#: 视图组：(图标 key, 页面标题)——标题是页签文案的唯一来源
NAV_VIEWS = (
    ("chat", "私教对话"),
    ("today", "今日任务"),
    ("book", "错题本"),
    ("landmark", "研招情报"),
)

#: 命令面板条目（4 个页面 + 10 个工具动作）
PALETTE_ENTRIES = tuple(
    [PaletteEntry(f"{VIEW_PREFIX}{i}", title, "视图", "切换到该页面")
     for i, (_icon, title) in enumerate(NAV_VIEWS)]
    + [PaletteEntry(f"{TOOL_PREFIX}{alias}", title, "工具", desc)
       for _icon, title, desc, alias in CARD_ITEMS]
)


def build(win) -> KYNavRail:
    """构建 rail：视图组（切页）+ 工具组（触发动作），并回填既有契约名。"""
    rail = KYNavRail()
    rail.add_group_title("视图")
    for icon_key, title in NAV_VIEWS:
        rail.add_view_item(icon_key, title)
    rail.add_group_title("工具")
    for icon, title, desc, alias in CARD_ITEMS:
        rail.add_tool_item(icon, title, desc, alias)

    rail.view_clicked.connect(win._on_nav_view_clicked)
    rail.tool_clicked.connect(win._on_card_clicked)
    rail.palette_btn.clicked.connect(win._open_command_palette)

    color = win._theme.color("acc") if hasattr(win, "_theme") else ""
    rail.refresh_icons(color)

    # 既有契约：功能卡列表（测试用 _feature_buttons[0]._icon_label 取图标）
    win.feature_cards = list(rail.tool_items)
    win._feature_buttons = list(rail.tool_items)
    return rail


def build_palette(win) -> CommandPalette:
    """构建命令面板（惰性创建后由窗口长期复用）。"""
    palette = CommandPalette(PALETTE_ENTRIES, win)
    palette.activated.connect(win._on_palette_activated)
    return palette


__all__ = ["NAV_VIEWS", "PALETTE_ENTRIES", "build", "build_palette"]
