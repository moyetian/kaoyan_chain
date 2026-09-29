# -*- coding: utf-8 -*-
"""导航 rail 视图：把 KYNavRail 挂到主窗口，并给出命令面板条目表

rail 分组（用户拍板）：
  * ``视图`` —— 原 4 个页签（私教对话 / 今日任务 / 错题本 / 研招情报）
  * ``工具`` —— 原 10 张功能卡的动作（清单唯一来源：``function_cards.CARD_ITEMS``）

命令面板分组（W13-7）：``PALETTE_ENTRIES`` 的 ``group`` 字段按
``日常 / 自测 / 情报 / 系统`` 四桶分桶（面板按桶渲染标题行，见
``widgets/command_palette.py``）；桶顺序即 ``PALETTE_GROUP_ORDER``。

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

#: 命令面板四桶（顺序即标题行出现顺序；DESIGN.md 词表同源）
PALETTE_GROUP_ORDER = ("日常", "自测", "情报", "系统")

#: 视图条目（与 ``NAV_VIEWS`` 同序）的分组桶
VIEW_GROUPS = ("日常", "日常", "自测", "情报")

#: 工具别名 → 分组桶（必须覆盖全部 10 条 ``CARD_ITEMS``，audit 测试对账）
TOOL_GROUPS = {
    "today": "日常",          # 任务打卡
    "compose": "自测",        # 靶向组卷
    "variant": "自测",        # 同源变式
    "diff": "情报",           # 考纲Diff
    "ingest": "系统",         # 切片入库
    "scout": "情报",          # 院校侦察
    "compare": "情报",        # 双校对标
    "watch": "情报",          # 简章监控
    "build": "系统",          # 看板更新
    "wechat_search": "情报",  # 公众号检索
}

#: 视图条目的口语别名（W13 验收修复 R3-2）：按 ``NAV_VIEWS`` 的图标 key 索引。
#: 只映射有真实功能的词——「交作业 / 设置 / 主题 / 退出 / 帮助 / 报告 / 诊断」
#: 在 GUI 面板无对应动作，不得映射（否则误导用户搜到执行不了的动作）。
VIEW_KEYWORDS = {
    "chat": "报到 学英语 学政治 私教",
    "today": "打卡 计时 倒计时 进度",
    "book": "错题 查漏 复习 复测",
    "landmark": "考情 考试 院校 研招 情报",
}

#: 工具条目的口语别名（W13 验收修复 R3-2）：统一含「工具」+ 按语义补充，
#: 覆盖全部 10 条 ``CARD_ITEMS`` 别名（audit 测试对账）。
TOOL_KEYWORDS = {
    "today": "工具 打卡 今日",
    "compose": "工具 试卷 刷题 模拟卷 考试",
    "variant": "工具 真题 变式",
    "diff": "工具 考纲 变动",
    "ingest": "工具 入库 试卷 切片",
    "scout": "工具 考情 院校 口碑",
    "compare": "工具 对标 对比",
    "watch": "工具 监控 简章 考情",
    "build": "工具 看板 更新 刷新",
    "wechat_search": "工具 公众号 微信 文章 经验",
}


def _grouped_entries() -> tuple:
    """14 条面板条目按四桶稳定排序（同桶连续 → 标题行每组只出现一次）。"""
    entries = (
        [PaletteEntry(f"{VIEW_PREFIX}{i}", title, VIEW_GROUPS[i], "切换到该页面",
                      VIEW_KEYWORDS[icon])
         for i, (icon, title) in enumerate(NAV_VIEWS)]
        + [PaletteEntry(f"{TOOL_PREFIX}{alias}", title, TOOL_GROUPS[alias], desc,
                        TOOL_KEYWORDS[alias])
           for _icon, title, desc, alias in CARD_ITEMS]
    )
    return tuple(sorted(entries, key=lambda e: PALETTE_GROUP_ORDER.index(e.group)))


#: 命令面板条目（4 个页面 + 10 个工具动作，按四桶分组）
PALETTE_ENTRIES = _grouped_entries()

# ════════════════════════════════════════════════════════════════
# [W13-7 · 42 覆盖边界声明] GUI 可执行面 vs ``ky`` CLI 主命令全集
# ════════════════════════════════════════════════════════════════
# GUI（rail「工具」组 + Ctrl+K 命令面板）的可执行面 = ``CARD_ITEMS`` 的 10 个别名，
# 它们是 ``tui_navigator.execute_action`` 支持的 11 个别名的子集（不含 ``exit``：
# GUI 关闭走窗口自身）。本批**不承诺** 42 个 CLI 主命令全部在 GUI 可达——完整
# 命令请见 ``ky commands``。下面把「可达 / 不可达」显式列全，audit 测试与 CLI
# 注册表逐一对账（防未来新增命令时静默漏声明）。

#: GUI 动作别名 → CLI 主命令规范名（``compose`` 是 ``exam`` 的注册别名；
#: ``diff`` 由 CLI 分发层重写为 ``fetch diff``；``wechat_search`` 由 TUI 分发器承接）
GUI_ACTION_TO_COMMAND = {
    "today": "today",
    "compose": "exam",
    "variant": "variant",
    "diff": "fetch",
    "ingest": "ingest",
    "scout": "scout",
    "compare": "compare",
    "watch": "watch",
    "build": "build",
    "wechat_search": "wechat",
}

#: GUI 可执行的动作别名（唯一来源：``CARD_ITEMS``）
GUI_ACTION_ALIASES = tuple(alias for _icon, _title, _desc, alias in CARD_ITEMS)

#: CLI 主命令总数（``ky commands`` 实测口径，W13 批）
CLI_MAIN_COMMAND_COUNT = 42

#: GUI 可达的 CLI 主命令（10 个）
GUI_REACHABLE_COMMANDS = frozenset(GUI_ACTION_TO_COMMAND.values())

#: GUI 无分发路径的 CLI 主命令（42 − 10 = 32 个）——完整命令见 ``ky commands``
GUI_UNREACHABLE_COMMANDS = frozenset({
    "version", "help", "commands", "config", "doctor", "status", "subject",
    "plan", "done", "map", "calc", "exam-submit", "review", "diagnose",
    "admission", "mount", "key", "notify", "rollback", "memory", "fatigue",
    "relieve", "style", "clawbot", "gui", "menu", "bridge", "serve", "view",
    "session", "rag", "gain",
})


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


__all__ = [
    "CLI_MAIN_COMMAND_COUNT",
    "GUI_ACTION_ALIASES",
    "GUI_ACTION_TO_COMMAND",
    "GUI_REACHABLE_COMMANDS",
    "GUI_UNREACHABLE_COMMANDS",
    "NAV_VIEWS",
    "PALETTE_ENTRIES",
    "PALETTE_GROUP_ORDER",
    "TOOL_GROUPS",
    "TOOL_KEYWORDS",
    "VIEW_GROUPS",
    "VIEW_KEYWORDS",
    "build",
    "build_palette",
]
