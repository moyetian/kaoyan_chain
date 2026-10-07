# -*- coding: utf-8 -*-
"""导航 rail 视图：把 KYNavRail 挂到主窗口，并给出命令面板条目表

rail 分组（用户拍板）：
  * ``视图`` —— 原 4 个页签（私教对话 / 今日任务 / 错题本 / 研招情报）
  * ``工具`` —— ``function_cards.CARD_ITEMS`` 的功能卡动作（[2026-10-06] 由 10 条
    增至 12 条：新增 ``ky rag`` 本地知识库检索与 ``ky index`` 建索引的 GUI 入口，
    此前二者在命令面板完全搜不到）

命令面板分组（W13-7）：``PALETTE_ENTRIES`` 的 ``group`` 字段按
``日常 / 自测 / 情报 / 系统`` 四桶分桶（面板按桶渲染标题行，见
``widgets/command_palette.py``）；桶顺序即 ``PALETTE_GROUP_ORDER``。

[契约] 工具项同时登记为 ``win.feature_cards`` / ``win._feature_buttons``：
既有测试与自检脚本按这两个名字取「全部功能卡控件」（2026-10-06 起 12 个：
新增 rag/index）并读取 ``_icon_label``。
"""

from __future__ import annotations

try:  # pragma: no cover - 取决于运行方式
    from gui import theme_apply
    from gui.views.function_cards import CARD_ITEMS
    from gui.widgets.command_palette import (
        CLI_MAIN_COMMAND_COUNT, TOOL_PREFIX, VIEW_PREFIX, CommandPalette, PaletteEntry,
    )
    from gui.widgets.nav_rail import KYNavRail
except ImportError:  # pragma: no cover
    from tools.gui import theme_apply  # type: ignore
    from tools.gui.views.function_cards import CARD_ITEMS  # type: ignore
    from tools.gui.widgets.command_palette import (  # type: ignore
        CLI_MAIN_COMMAND_COUNT, TOOL_PREFIX, VIEW_PREFIX, CommandPalette, PaletteEntry,
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

#: 工具别名 → 分组桶（必须覆盖全部 12 条 ``CARD_ITEMS``，audit 测试对账）
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
    # [2026-10-06] 本地检索/建索引归「系统」：两者都是对本地知识库的操作，
    # 与同为本地库操作的「切片入库/看板更新」同桶，而非内容型情报。
    "rag": "系统",            # 本地知识库检索
    "index": "系统",          # 本地知识库建索引
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
#: 覆盖全部 12 条 ``CARD_ITEMS`` 别名（audit 测试对账）。
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
    # [2026-10-06] 仿真探查实测「rag」「index」「索引」「检索」四词全 0 命中，
    # 故把 CLI 名与口语词都收进词表；「检索」此前只命中 variant/wechat_search，
    # 现在会额外命中 rag（更贴近「查本地知识库」的语义）。
    "rag": "工具 rag 检索 搜索 查知识库 本地知识库 知识点",
    "index": "工具 index 索引 建索引 切片入库 知识库",
}


def _grouped_entries() -> tuple:
    """16 条面板条目按四桶稳定排序（同桶连续 → 标题行每组只出现一次）。"""
    entries = (
        [PaletteEntry(f"{VIEW_PREFIX}{i}", title, VIEW_GROUPS[i], "切换到该页面",
                      VIEW_KEYWORDS[icon])
         for i, (icon, title) in enumerate(NAV_VIEWS)]
        + [PaletteEntry(f"{TOOL_PREFIX}{alias}", title, TOOL_GROUPS[alias], desc,
                        TOOL_KEYWORDS[alias])
           for _icon, title, desc, alias in CARD_ITEMS]
    )
    return tuple(sorted(entries, key=lambda e: PALETTE_GROUP_ORDER.index(e.group)))


#: 命令面板条目（4 个页面 + 12 个工具动作，按四桶分组）
PALETTE_ENTRIES = _grouped_entries()


def _collapsed_from_settings() -> bool:
    """读 QSettings 记忆的 rail 折叠态（缺失 / 异常 / 任意后端差异一律安全解析）。

    ``QSettings`` 在 bool 的存回类型上各平台后端不完全一致（bool / "true" /
    1），故统一按字符串归一化判断，而非直接 ``bool(raw)``（后者对 "false" 会
    错误地判为 True）。
    """
    raw = theme_apply.read_pref(theme_apply.KEY_RAIL_COLLAPSED, False)
    return str(raw).strip().lower() in ("1", "true", "yes")


def _on_rail_toggle(rail: KYNavRail) -> None:
    """NavToggle 点击：切换折叠态并持久化（写「切换后」的状态）。"""
    collapsed = rail.is_expanded()          # 当前展开 → 本次点击即折叠
    rail.set_expanded(not collapsed)
    theme_apply.write_pref(theme_apply.KEY_RAIL_COLLAPSED, collapsed)


# ════════════════════════════════════════════════════════════════
# [W13-7 · CLI 主命令覆盖边界声明] GUI 可执行面 vs ``ky`` CLI 主命令全集
# ════════════════════════════════════════════════════════════════
# GUI（rail「工具」组 + Ctrl+K 命令面板）的可执行面 = ``CARD_ITEMS`` 的 12 个别名，
# 它们是 ``tui_navigator.execute_action`` 支持的 13 个别名的子集（不含 ``exit``：
# GUI 关闭走窗口自身）。本批**不承诺** CLI 主命令全部在 GUI 可达——完整
# 命令请见 ``ky commands``。下面把「可达 / 不可达」显式列全，audit 测试与 CLI
# 注册表逐一对账（防未来新增命令时静默漏声明）。
# 总数常量 ``CLI_MAIN_COMMAND_COUNT`` 的单一真源在 ``widgets/command_palette.py``
# （面板提示文案与 rail 审计共用），此处仅 re-export 供既有导入路径使用。

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
    # [2026-10-06] rag / index 由「不可达」移入「可达」：两者在 CLI 注册表里
    # 的规范名就是别名本身（``ky rag`` / ``ky index``），且已接上 GUI 动作分发
    # （见 main_window._on_card_clicked 与 gui.services.rag_search/build_index）。
    "rag": "rag",
    "index": "index",
}

#: GUI 可执行的动作别名（唯一来源：``CARD_ITEMS``）
GUI_ACTION_ALIASES = tuple(alias for _icon, _title, _desc, alias in CARD_ITEMS)

#: 「不经 TUI 分发器、直接走 GUI 服务层」的动作别名。
#:
#: [为什么需要显式声明] 其余 10 个别名都由 ``gui.services.run_action_capture`` →
#: ``tui_navigator.execute_action`` 执行（单实现）。但 ``rag`` / ``index`` 需要
#: GUI 侧先收集入参（检索词）或干脆换一套执行器（建索引走 build_index 而非
#: execute_action），硬塞进 TUI 会为了两条命令改动 TUI 菜单——而 TUI 菜单编号
#: 契约（1-12）刚因另一批次扩容，不宜再动。此处把差异显式登记，audit 测试按
#: 「TUI 分发集 ∪ 服务层直调集 = 全部 GUI 别名」三方对账，而不是靠放宽断言掩盖。
GUI_SERVICE_ONLY_ALIASES = frozenset({"rag", "index"})

#: GUI 可达的 CLI 主命令（12 个）
GUI_REACHABLE_COMMANDS = frozenset(GUI_ACTION_TO_COMMAND.values())

#: GUI 无分发路径的 CLI 主命令（46 − 12 = 34 个）——完整命令见 ``ky commands``
GUI_UNREACHABLE_COMMANDS = frozenset({
    "version", "help", "commands", "config", "doctor", "status", "subject",
    "plan", "done", "map", "calc", "exam-submit", "review", "diagnose",
    "admission", "mount", "key", "notify", "rollback", "memory", "fatigue",
    "relieve", "style", "clawbot", "gui", "menu", "bridge", "serve", "view",
    "session", "gain", "tools", "audit", "grade-regress",
    # [2026-10-06] 新增 ``ky budget``（输出预算档位）—— 纯 CLI 侧配置入口，
    # GUI 暂未接入（考生可先用命令切换），故列入不可达。
    "budget",
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
    # 双态折叠：NavToggle 点击切换并写 QSettings；构建时按 QSettings 恢复上次状态
    rail.toggle_btn.clicked.connect(lambda _checked=False: _on_rail_toggle(rail))
    if _collapsed_from_settings():
        rail.set_expanded(False)

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
    "GUI_SERVICE_ONLY_ALIASES",
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
