# -*- coding: utf-8 -*-
"""P2 GUI 改造验收：导航 rail / 组件族 / 命令面板 / 气泡 / 错题卡

覆盖外部评审方案 P2 的 6 项要求：

  ① 导航架构：10 卡平铺 + 4 页签 → 左侧 rail（视图组 + 工具组）+ 隐藏 tabBar
  ② 激活态非巨型：``#NavItem:checked`` = 左侧 3px 强调条 + acc-sub 底，紧凑行
  ③ Ctrl+K 命令面板：可打开 / 可过滤 / 回车执行（Raycast 模式）
  ④ 任务页科目名不再被 ``setFixedWidth(200)`` 硬切
  ⑤ 对话页空状态给出 3~4 个可点击示例（点击只填入输入框）
  ⑥ 错题本卡片化（KYCard 列表），同时保留 ``error_info`` 文本契约

另含：离屏冒烟（构造 MainWindow → 点击全部 rail 视图项 → 分发全部工具项 →
开合命令面板）、既有契约回归（``tabs`` / ``tab_widget`` / ``_feature_buttons`` /
``task_progress_bars`` / ``task_count_labels`` / 主题 / 头部刷新）、以及 3 处
**可执行阴性对照**（见各用例 docstring）。

W13-7 追加：命令面板四桶分组标题行（不可选中 / 计数契约不破 / 键盘流跳过）+
「CLI 主命令覆盖边界声明」审计（面板可执行别名 ⊆ ``MENU_OPTIONS``，差集与 CLI 注册表对账）。

W13 验收修复追加：命令面板空结果提示行（R3-1：无匹配不再是零提示空白列表）+
口语搜索别名（R3-2：``PaletteEntry.keywords``，「考情 / 刷题 / 报到」等词可命中）。

本文件不含任何真实身份串（tests/ 对导出脱敏免疫，见
``test_privacy_identity_rules.test_tests_dir_is_immune_to_py_sanitization``）。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 改造测试")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QProgressBar, QTabWidget, QWidget,
)

#: ``QWIDGETSIZE_MAX``：Qt 中「不限制尺寸」的哨兵值（PySide6 未导出该常量）
QWIDGETSIZE_MAX = 16777215

from tools.gui import services  # noqa: E402
from tools.gui.main_window import TAB_TITLES, MainWindow  # noqa: E402
from tools.gui.views.function_cards import CARD_ITEMS  # noqa: E402
from tools.gui.widgets.chat_view import EXAMPLE_PROMPTS  # noqa: E402
from tools.theme import build_theme, render_qss  # noqa: E402

# 双导入路径（``gui.*`` / ``tools.gui.*``）会让同名类成为两个对象：main_window
# 优先走 ``gui.*``，测试必须从**同一条路径**取类，否则 isinstance 会假红。
try:  # pragma: no cover - 取决于运行方式
    from gui.widgets.nav_rail import KYNavRail, RAIL_COLLAPSED_WIDTH, RAIL_WIDTH
except ImportError:  # pragma: no cover
    from tools.gui.widgets.nav_rail import (  # type: ignore
        KYNavRail, RAIL_COLLAPSED_WIDTH, RAIL_WIDTH,
    )

try:  # pragma: no cover - 取决于运行方式
    from gui.views import nav_rail as nav_rail_view
except ImportError:  # pragma: no cover
    from tools.gui.views import nav_rail as nav_rail_view  # type: ignore

try:  # pragma: no cover - 取决于运行方式
    from gui.widgets.command_palette import (
        EMPTY_HINT_TEXT, CommandPalette, PaletteEntry,
    )
except ImportError:  # pragma: no cover
    from tools.gui.widgets.command_palette import (  # type: ignore
        EMPTY_HINT_TEXT, CommandPalette, PaletteEntry,
    )


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst
    inst.setStyleSheet("")


@pytest.fixture()
def win(app, tmp_path, monkeypatch):
    # [tripwire 修复 2026-10-07] 隔离姿势同 test_gui_smoke：workspace_root=
    # tmp_path 防改写真实「今日任务.md」；is_unconfigured 必须打在
    # **_mw.services**（main_window 实际引用的对象，双导入下可能与
    # ``tools.gui.services`` 不是同一个模块）——否则空 tmp 工作区判未配置 →
    # singleShot(150ms) 弹建档向导 → 离屏下 wizard.exec() 永久阻塞。
    import tools.gui.main_window as _mw

    monkeypatch.setattr(_mw.services, "is_unconfigured",
                        lambda *a, **k: False, raising=False)
    w = MainWindow(workspace_root=tmp_path)
    w.show()
    w.adjustSize()
    app.processEvents()
    yield w
    w.close()


# ── 工具函数 ────────────────────────────────────────────────────

def _qss_block(qss: str, selector: str) -> str:
    """取渲染后 QSS 中某个选择器的规则体（找不到即失败）。"""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
    assert match is not None, f"QSS 编译产物缺少选择器块 {selector}"
    return match.group(1)


def _truncation_risk(label: QLabel) -> bool:
    """标签是否被钉死宽度到放不下自身文本（即 ``setFixedWidth(200)`` 那类截断）。"""
    if label.maximumWidth() >= QWIDGETSIZE_MAX:
        return False
    return label.maximumWidth() < label.fontMetrics().horizontalAdvance(label.text())


def _bubble_side(bubble) -> str:
    """气泡在其所在行的对齐方向：行首是 stretch → 靠右，否则靠左。"""
    layout = bubble.parentWidget().layout()
    first = layout.itemAt(0) if layout is not None and layout.count() else None
    return "right" if first is not None and first.spacerItem() is not None else "left"


def _card_title_of(card) -> str:
    """取 KYCard 的标题文本（经 objectName 查找，不碰私有属性）。"""
    title = card.findChild(QLabel, "KYCardTitle")
    assert title is not None, "KYCard 应含标题标签"
    return title.text()


# ── ① rail 存在且分组 ───────────────────────────────────────────

def test_nav_rail_replaces_tiled_cards_and_visible_tabs(win):
    """导航收敛为左侧 rail：10 卡平铺 + 4 页签两套并列导航都消失。"""
    rail = win.nav_rail
    assert isinstance(rail, KYNavRail)
    assert rail.isVisible(), "rail 应可见（导航唯一入口）"
    # 原 QTabWidget 保留为页面容器，但 tabBar 隐藏 —— 页签不再与 rail 并列
    assert win.tabs.tabBar().isHidden(), "原生页签栏必须隐藏，导航交给 rail"


def test_nav_rail_has_two_groups_with_expected_items(win):
    """rail 分「视图」「工具」两组，条目与原 4 页签 / 功能卡清单一一对应。"""
    rail = win.nav_rail
    # [阶段 D 前置] 折叠态下视图项文字被清空（原文在 tooltip 里）——本用例
    # 断言展开态文案，先显式展开（断言语义不变）。
    rail.set_expanded(True)
    assert rail.group_titles == ["视图", "工具"], (
        f"rail 应有「视图/工具」两组，实际 {rail.group_titles}")

    # 视图组 = 原 4 个页签（标题与 QTabWidget 页签文案同源）
    assert [item.text() for item in rail.view_items] == list(TAB_TITLES)
    assert len(rail.view_items) == 4

    # 工具组 = 功能卡清单的动作，别名与清单完全一致（行为不变）
    # [2026-10-06] 10 → 12：新增 rag（本地检索）/ index（建索引）两个 GUI 入口。
    assert len(rail.tool_items) == len(CARD_ITEMS) == 12
    assert [item.alias for item in rail.tool_items] == [alias for *_x, alias in CARD_ITEMS]

    # 契约：_feature_buttons / feature_cards 仍指向全部可读图标的工具项
    assert len(win._feature_buttons) == len(CARD_ITEMS)
    for i, item in enumerate(rail.tool_items):
        assert win._feature_buttons[i] is item
        assert win.feature_cards[i] is item
        assert item._icon_label is not None


# ── ② 激活态非巨型 ──────────────────────────────────────────────

def test_nav_active_state_is_slim_accent_bar_not_giant_highlight(win):
    """激活态 = 左侧 3px 强调条 + acc-sub 底；结构上是紧凑行而非巨型高亮块。"""
    rail = win.nav_rail
    # [阶段 D 前置] 本用例断言展开态几何（252px 完整栏），先显式展开。
    rail.set_expanded(True)

    # 结构：紧凑行（远小于页面高度）。下限 34px 对应 #NavItem 的 QSS min-height
    # （30px）+ 上下 padding（4+4）—— 点击目标不得被全局 QPushButton 规则压回 22px。
    for item in rail.view_items:
        assert 34 <= item.height() <= 48, (
            f"视图项 {item.text()!r} 高度 {item.height()}px 不是紧凑行（巨型高亮？）")
    assert rail.width() <= 320, f"rail 宽度 {rail.width()}px 过宽（应只占侧边一栏）"

    # 互斥激活：任意时刻恰好一项选中
    rail.set_active_index(2)
    checked = [i for i, item in enumerate(rail.view_items) if item.isChecked()]
    assert checked == [2], f"激活态应唯一，实际 {checked}"
    assert rail.active_index() == 2

    # 样式：从主题编译产物取 #NavItem:checked，断言强调条 + 低饱和底色
    theme = win._theme
    block = _qss_block(render_qss(theme), "#NavItem:checked")
    assert "border-left: 3px solid" in block, f"激活态缺少 3px 左侧强调条: {block!r}"
    assert f"background-color: {theme.color('acc-sub')}" in block, (
        f"激活态底色应为 acc-sub（低饱和），实际 {block!r}")
    assert f"background-color: {theme.color('acc')};" not in block, (
        "激活态不得使用主色实心块（巨型高亮）")
    # 非巨型：字号不超过正文字阶，padding 不放大
    sizes = [float(v) for v in re.findall(r"font-size:\s*([\d.]+)px", block)]
    assert all(size <= 16 for size in sizes), f"激活态字号 {sizes} 过大（巨型高亮）"
    pads = [float(v) for v in re.findall(r"padding:\s*([\d.]+)px", block)]
    assert all(pad <= 8 for pad in pads), f"激活态 padding {pads} 过大（巨型高亮）"


# ── ③ 命令面板（Ctrl+K） ────────────────────────────────────────

def test_command_palette_opens_filters_and_executes(win, app, monkeypatch):
    """命令面板：Ctrl+K 打开 → 输入过滤 → 回车执行（视图切页 / 工具分发）。"""
    from PySide6.QtGui import QKeySequence

    assert win._palette_shortcut.key() == QKeySequence("Ctrl+K"), "Ctrl+K 快捷键未注册"

    win._open_command_palette()
    app.processEvents()
    palette = win._palette
    assert palette is not None and palette.isVisible(), "命令面板应能打开"
    assert len(palette.visible_keys()) == len(TAB_TITLES) + len(CARD_ITEMS), \
        "空查询应列出全部导航项与工具动作"

    # 过滤 → 执行「错题本」页
    palette.refresh("错题")
    assert "view:2" in palette.visible_keys()
    win.tabs.setCurrentIndex(0)
    assert palette.activate_current() == "view:2"
    assert win.tabs.currentIndex() == 2, "命令面板执行后应切到错题本页"

    # 过滤 → 执行「看板更新」工具（替换分发端，避免真起 worker）
    dispatched: list = []
    monkeypatch.setattr(win, "_on_card_clicked", lambda alias: dispatched.append(alias))
    win._open_command_palette()
    palette.refresh("看板")
    assert palette.visible_keys() == ["tool:build"]
    assert palette.activate_current() == "tool:build"
    assert dispatched == ["build"], "工具条目应走既有动作分发（行为不变）"


def test_command_palette_no_match_lists_nothing_negative_control(win, app):
    """阴性对照：无命中查询不得退化成「列出全部」，也不得误执行任何条目。

    检测器若把过滤写成「无结果时回退全量」，本用例变红。
    """
    win._open_command_palette()
    app.processEvents()
    palette = win._palette
    palette.refresh("zzz-不存在的条目-zzz")
    assert palette.visible_keys() == [], "无命中时必须为空列表"

    fired: list = []
    palette.activated.connect(fired.append)
    assert palette.activate_current() == "", "无选中项时不得执行"
    assert fired == [], "无选中项时不得发 activated 信号"
    palette.close()


# ── ④ 任务页标签不截断 ──────────────────────────────────────────

def test_task_labels_are_not_width_clamped(win):
    """④ 科目标签不再 ``setFixedWidth(200)``；完整名称同时进 tooltip。"""
    labels = win.findChildren(QLabel, "TaskLabel")
    assert labels, "任务页应有科目标签"
    for label in labels:
        assert not _truncation_risk(label), (
            f"科目标签 {label.text()!r} 被钉死在 {label.maximumWidth()}px，长名会被硬切")
        assert label.toolTip() == label.text(), "完整科目名必须进 tooltip（截断时的兜底）"

    # 进度条与计数标签契约名保留，且与共享状态层同源
    assert win.task_progress_bars and win.task_count_labels
    for subject in services.subject_progress(win.workspace_root):
        bar = win.task_progress_bars[subject.key]
        pct = win.task_count_labels[subject.key]
        assert isinstance(bar, QProgressBar) and bar.value() == subject.pct
        assert bar.minimumWidth() >= 120, "长科目名下进度条不得被挤没"
        assert pct.text() == subject.summary_text


def test_truncation_detector_flags_fixed_width_200_negative_control(win):
    """阴性对照：截断检测器必须能识别改造前的 ``setFixedWidth(200)`` 写法。

    否则上面那条「无固定宽」断言可能只是检测器失灵导致的假绿。
    使用合成长名（中性化），不引用任何真实科目名。
    """
    synthetic = "模拟超长科目名 1234567890 用于验证截断检测器 ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    probe = QLabel(synthetic)
    assert not _truncation_risk(probe), "未限宽标签不应被判为截断风险"
    assert probe.fontMetrics().horizontalAdvance(synthetic) > 200, \
        "探针前提：该合成长名在 200px 内放不下"
    probe.setFixedWidth(200)                       # 复现改造前的写法
    assert _truncation_risk(probe), "setFixedWidth(200) 必须被判为截断风险（检测器失灵？）"
    probe.deleteLater()


# ── ⑤ 对话页空状态示例 ──────────────────────────────────────────

def test_chat_empty_state_examples_fill_input_without_sending(win, app):
    """⑤ 空状态给出 3~4 个可点击示例；点击只填入输入框。

    阴性对照：点击示例**不得**直接发起私教调用（真实计费 LLM）或产生气泡。
    """
    view = win.chat_display
    assert 3 <= len(view.example_pills) <= 4, (
        f"空状态示例应为 3~4 个，实际 {len(view.example_pills)}")
    assert view.empty_state.isVisible(), "初始应显示空状态引导"

    assert win.input_box.text() == ""
    view.example_pills[0].click()
    app.processEvents()
    assert win.input_box.text() == EXAMPLE_PROMPTS[0][1], "点击示例应把口令填入输入框"
    assert win.agent_worker is None, "点击示例不得发起私教工作线程（避免误触计费调用）"
    assert view.bubbles == [], "点击示例不得直接产生对话气泡"

    view.add_user_message("你好")
    assert not view.empty_state.isVisible(), "产生消息后空状态应隐藏"


def test_chat_bubbles_distinguish_sides_and_kinds(win):
    """消息气泡：用户右 / 私教左 / 系统居中，且各走自己的 QSS 选择器。"""
    view = win.chat_display
    user = view.add_user_message("问题")
    agent = view.add_agent_message("回答")
    view.append("系统播报")                       # 兼容接口：无返回值
    system = view.bubbles[-1]

    assert user.objectName() == "UserBubble"
    assert agent.objectName() == "AgentBubble"
    assert system.objectName() == "SystemBubble"
    assert _bubble_side(user) == "right", "用户消息应右对齐"
    assert _bubble_side(agent) == "left", "私教消息应左对齐"

    # 流式续写：同一气泡内追加，收尾后另起一条
    chunk = view.append_agent_chunk("片段一")
    view.append_agent_chunk("片段二")
    assert chunk.text() == "片段一片段二", "流式片段应续写当前气泡"
    view.finish_agent_message()
    view.append_agent_chunk("新段")
    assert len(view.bubbles) == 5, "收尾后的片段应另起新气泡"
    # 契约：toPlainText 仍可导出全部文本（向导热更新通知等调用点依赖）
    assert "问题" in view.toPlainText() and "回答" in view.toPlainText()


# ── ⑥ 错题本卡片化 ──────────────────────────────────────────────

def test_error_tab_renders_cards_and_keeps_text_contract(win):
    """⑥ 错题本以 KYCard 列表为主视图；``error_info`` 文本契约原样保留。"""
    records = services.error_queue_cards(win.workspace_root)
    win._refresh_error_tab()
    assert len(win.error_cards) == len(records), "卡片数应与结构化错题记录一一对应"

    for card in win.error_cards:
        assert card.objectName() == "KYCard"
        chips = {child.objectName() for child in card.findChildren(QLabel)}
        assert "CardChip" in chips, "每张错题卡必须有科目 chip"

    if records:
        first_title = records[0].get("question") or records[0].get("title")
        assert _card_title_of(win.error_cards[0]) == first_title, "卡片标题应取题干/标题"
    else:  # pragma: no cover - 取决于工作区数据
        assert win.findChild(QLabel, "EmptyHint") is not None, "无错题时应给出空态提示"

    # [CI 修复·阴性对照] 数据未变的二次渲染不得把已渲染内容从布局里摘掉：旧实现
    # 先 _clear_layout 再按指纹提前返回 —— 命中短路时卡片/空态提示全部被摘除且
    # 不重建，视图变空白（CI 三平台实测红；有数据的真机二次刷新同样整页空白）。
    win._refresh_error_tab()
    if records:
        assert win.error_cards[0].parent() is not None, "二次渲染后错题卡被摘出视图"
    else:
        assert win.findChild(QLabel, "EmptyHint") is not None, "二次渲染后空态提示被摘掉"

    # 契约：error_info 仍是 QTextEdit，toPlainText 含「错题」；卡片为主、原文默认折叠
    assert "错题" in win.error_info.toPlainText()
    assert win.error_info.isHidden(), "原始档案默认折叠（卡片视图为主）"
    win.error_raw_btn.setChecked(True)
    assert not win.error_info.isHidden(), "「原始档案」开关应能展开 Markdown 兜底视图"
    win.error_raw_btn.setChecked(False)
    assert win.error_info.isHidden()


# ── 组件族样式铁律 + 契约回归 + 冒烟 ────────────────────────────

def test_p2_components_have_no_inline_stylesheet(win, app):
    """项目铁律：P2 组件族内部不写任何内联样式（全部走 objectName + 主题 QSS）。"""
    roots = [win.nav_rail, win.chat_display, win.error_cards_container]
    roots.extend(win.task_stat_tiles.values())
    roots.extend(win.error_cards)
    offenders = []
    for root in roots:
        for widget in [root] + root.findChildren(QWidget):
            if widget.styleSheet():
                offenders.append((widget.objectName() or type(widget).__name__,
                                  widget.styleSheet()[:60]))
    assert not offenders, f"P2 组件存在内联样式: {offenders}"

    # 命令面板同样零内联样式
    win._open_command_palette()
    app.processEvents()
    palette = win._palette
    assert not [w for w in [palette] + palette.findChildren(QWidget) if w.styleSheet()]
    palette.close()

    # 统计块（大数字 + caption）已消费新 token：字号来自主题而非控件
    tile = win.task_stat_tiles["progress"]
    assert tile.value_text().endswith("%")
    assert tile.caption_text(), "统计块应有 caption"


def test_legacy_contract_names_are_preserved(win):
    """既有契约名保持存在且语义不变（9 个测试文件依赖）。"""
    # 页面容器
    assert isinstance(win.tabs, QTabWidget) and isinstance(win.tab_widget, QTabWidget)
    assert win.tabs is win.tab_widget
    assert win.tabs.count() == 4 and win.tabs.tabBar().count() == 4
    assert [win.tabs.tabText(i) for i in range(4)] == list(TAB_TITLES)

    # 头部 / 主题
    assert win.countdown_label.text().startswith("初试倒计时:")
    win._sync_header_text()
    win._on_timer_tick()
    assert win._theme is not None and callable(win._toggle_theme)

    # 数据链路
    win._refresh_error_tab()
    win._refresh_intel_tab()
    assert "研招" in win.intel_display.toPlainText()


def test_smoke_switch_all_rail_items_and_open_palette(win, app):
    """冒烟：点击全部 rail 视图项切页、分发全部工具项、开合命令面板 —— 不抛异常。"""
    rail = win.nav_rail

    for index, item in enumerate(rail.view_items):
        item.click()
        app.processEvents()
        assert win.tabs.currentIndex() == index, f"点击 {item.text()!r} 未切到页 {index}"
        assert item.isChecked(), f"{item.text()!r} 点击后未进入激活态"

    # 工具项：全部 10 项可分发到既有动作（替换分发端，避免真起 worker / 弹窗）
    dispatched: list = []
    try:
        rail.tool_clicked.disconnect()
    except RuntimeError:
        pytest.fail("rail.tool_clicked 未连接到窗口：工具项点击不会触发任何动作")
    rail.tool_clicked.connect(dispatched.append)
    try:
        for item in rail.tool_items:
            item.clicked.emit(item.alias)
        assert dispatched == [item.alias for item in rail.tool_items]
    finally:
        rail.tool_clicked.disconnect()
        rail.tool_clicked.connect(win._on_card_clicked)     # 还原真实连线

    win._open_command_palette()
    app.processEvents()
    assert win._palette is not None and win._palette.isVisible()
    win._palette.close()
    app.processEvents()


def test_tool_item_real_dispatch_end_to_end(win, app):
    """端到端：点击「今日任务」工具项，走**真实**分发链（不经替身）切页并刷新进度。

    该别名不弹对话框、不起 worker，是 10 个工具项里唯一可安全真实执行的；
    其余 9 项在冒烟用例里以替换分发端的方式验证可分发。
    """
    item = next(it for it in win.nav_rail.tool_items if it.alias == "today")
    win.tabs.setCurrentIndex(0)
    win.task_progress_bars[next(iter(win.task_progress_bars))].setValue(0)

    item.clicked.emit(item.alias)                  # 真实链路 → win._on_card_clicked
    app.processEvents()
    assert win.tabs.currentIndex() == 1, "「今日任务」工具项应切到今日任务页"
    for subject in services.subject_progress(win.workspace_root):
        assert win.task_progress_bars[subject.key].value() == subject.pct, \
            "工具项动作应刷新今日进度（行为与改造前一致）"


def test_qss_renders_with_p2_selectors_for_all_presets():
    """QSS 模板（含 P2 新组件块）在全部内置预设下都能编译，且消费了新 token。"""
    from tools.theme import PRESET_ORDER

    for preset in PRESET_ORDER:
        qss = render_qss(build_theme(preset))
        for selector in ("#NavRail", "#NavItem:checked", "#NavToolItem", "#PaletteDialog",
                         "#PaletteGroupHeader", "#UserBubble", "#AgentBubble", "#KYCard",
                         "#StatValue", "#ExamplePill"):
            assert selector in qss, f"预设 {preset} 缺少 {selector} 样式块"
        assert "{{" not in qss and "}}" not in qss, f"预设 {preset} 存在未替换占位符"


# ── ⑦ W13-7：命令面板分组标题行 + CLI 主命令覆盖边界 ──────────────────

def _palette_header_rows(palette) -> list:
    """当前面板列表里的分组标题行行号（判据：无 UserRole key）。"""
    return [row for row in range(palette.list.count())
            if not palette.list.item(row).data(Qt.UserRole)]


def test_palette_entries_grouped_into_four_buckets():
    """W13-7：面板条目按 日常/自测/情报/系统 四桶分桶，且同桶连续。"""
    entries = nav_rail_view.PALETTE_ENTRIES
    assert len(entries) == len(TAB_TITLES) + len(CARD_ITEMS) == 16, \
        "面板条目数必须与「4 视图 + 12 工具」契约一致"

    groups = [entry.group for entry in entries]
    assert set(groups) == set(nav_rail_view.PALETTE_GROUP_ORDER) == {"日常", "自测", "情报", "系统"}
    # 同桶连续（面板按 group 切换插入标题行，乱序会让同一标题行重复出现）
    assert groups == sorted(groups, key=nav_rail_view.PALETTE_GROUP_ORDER.index)
    for bucket in nav_rail_view.PALETTE_GROUP_ORDER:
        assert groups.count(bucket) >= 2, f"分组桶 {bucket} 覆盖不足"

    # 工具条目的分桶表覆盖全部 12 个别名（漏一个会 KeyError，这里给出显式断言）
    tool_aliases = {alias for *_x, alias in CARD_ITEMS}
    assert tool_aliases == set(nav_rail_view.TOOL_GROUPS)
    assert set(nav_rail_view.TOOL_KEYWORDS) == tool_aliases, \
        "口语词表必须覆盖全部工具别名（含 2026-10-06 新增的 rag/index）"
    assert {entry.key for entry in entries} == (
        {f"view:{i}" for i in range(len(TAB_TITLES))}
        | {f"tool:{alias}" for alias in tool_aliases})


def test_palette_group_headers_render_and_are_not_selectable(win, app):
    """W13-7：空查询渲染 4 个分组标题行；不可选中、无 key、不破坏计数契约。

    阴性对照：标题行 flags 改为可选中 → 本用例的 ``ItemIsSelectable`` 断言必红。
    """
    win._open_command_palette()
    app.processEvents()
    palette = win._palette

    # 计数契约原样保留（标题行不参与 visible_keys）
    assert len(palette.visible_keys()) == len(TAB_TITLES) + len(CARD_ITEMS)

    header_rows = _palette_header_rows(palette)
    assert len(header_rows) == len(nav_rail_view.PALETTE_GROUP_ORDER) == 4, \
        f"空查询应渲染 4 个分组标题行，实际 {header_rows}"
    labels = [palette.list.itemWidget(palette.list.item(row)) for row in header_rows]
    assert [label.text() for label in labels] == list(nav_rail_view.PALETTE_GROUP_ORDER)
    for row, label in zip(header_rows, labels):
        item = palette.list.item(row)
        assert label is not None and label.objectName() == "PaletteGroupHeader"
        assert not (item.flags() & Qt.ItemIsSelectable), "标题行不得可选中"
        assert not item.data(Qt.UserRole), "标题行不得携带可执行 key"
        # 防裁切（实测缺陷）：itemWidget 的几何 = item 文本子区域，会被
        # #PaletteList::item 的上下 padding 扣除——行高必须补回这一份。
        assert label.height() >= label.sizeHint().height(), "标题行高度不足，文字被纵向裁切"

    # 初始选中 = 首个可执行行（不是 row 0 的标题行）
    current = palette.list.currentRow()
    assert current not in header_rows and current >= 0, "初始选中不得落在标题行"
    assert palette.list.currentItem().data(Qt.UserRole) == palette.visible_keys()[0]

    # 面板提示告知「完整命令见 ky commands」，条数与声明常量同源
    assert "ky commands" in palette.hint.text()
    assert str(nav_rail_view.CLI_MAIN_COMMAND_COUNT) in palette.hint.text()
    palette.close()


def test_palette_keyboard_skips_group_headers(win, app):
    """W13-7：↑↓ 只落在可执行行；即使被程序化选到标题行，Enter 也不发 "None"。"""
    from PySide6.QtGui import QKeyEvent

    win._open_command_palette()
    app.processEvents()
    palette = win._palette
    palette.refresh("")

    total = palette.list.count()
    header_rows = set(_palette_header_rows(palette))
    assert header_rows, "空查询下应存在分组标题行"

    def press(key):
        QApplication.sendEvent(palette, QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier))

    # 一路 ↓：全程不落在标题行，最终停在最后一行（末条目）
    for _ in range(total + 2):
        assert palette.list.currentRow() not in header_rows, "↓ 不得停在标题行"
        press(Qt.Key_Down)
    assert palette.list.currentRow() == total - 1

    # 一路 ↑：回到首个可执行行后不再前移（标题行挡住）
    first_entry = min(row for row in range(total) if row not in header_rows)
    for _ in range(total + 2):
        assert palette.list.currentRow() not in header_rows, "↑ 不得停在标题行"
        press(Qt.Key_Up)
    assert palette.list.currentRow() == first_entry

    # 防御：程序化选到标题行时，Enter 不得发出 "None" 或任何 key
    fired: list = []
    palette.activated.connect(fired.append)
    palette.list.setCurrentRow(min(header_rows))
    assert palette.activate_current() == "", "标题行不得被执行"
    assert fired == [], "标题行不得发出 activated 信号"


def test_w13_palette_command_coverage_boundary_audit():
    """W13-7 · CLI 主命令覆盖边界审计：面板可执行别名 ⊆ MENU_OPTIONS；差集与 CLI 对账。

    本批**不承诺**全部 CLI 主命令 GUI 可达——可达 / 不可达是显式声明
    （``GUI_REACHABLE_COMMANDS`` / ``GUI_UNREACHABLE_COMMANDS``），此处与
    ``ky`` CLI 注册表逐一对账，防未来新增命令时静默漏声明。
    """
    from tools import tui_navigator
    from tools.cli import dispatch

    # ① 面板可执行别名 = TUI 分发集 ∪ 服务层直调集（两者都必须 ⊆ GUI 别名）
    menu_aliases = {alias for _key, _name, _desc, alias in tui_navigator.MENU_OPTIONS}
    gui_aliases = set(nav_rail_view.GUI_ACTION_ALIASES)
    assert gui_aliases == {alias for *_x, alias in CARD_ITEMS}
    # [2026-10-06] rag / index 走 gui.services 而非 TUI execute_action（需 GUI
    # 侧先收检索词 / 换执行器），故不再要求全部别名落在 MENU_OPTIONS，改为
    # 「除显式登记的服务层直调集外，都必须能被 TUI 分发」——比原来的 ⊆ 更严：
    # 多登记一个别名、或把能走 TUI 的别名错登记成服务层直调，都会红。
    svc_only = set(nav_rail_view.GUI_SERVICE_ONLY_ALIASES)
    assert svc_only and svc_only <= gui_aliases, \
        "GUI_SERVICE_ONLY_ALIASES 必须是 GUI 别名的真子集"
    assert gui_aliases - svc_only <= menu_aliases, (
        f"TUI 分发别名必须全部落在 MENU_OPTIONS："
        f"{sorted((gui_aliases - svc_only) - menu_aliases)}")

    # ② CLI 主命令全集：可达 / 不可达声明与 CLI 注册表逐一对账
    dispatch._init_all_commands()
    registered = {cmd.name for cmd in dispatch.list_commands()}
    # [2026-10-05 同步] 新增 ``ky index`` 后 44 → 45（不可达 34 → 35）
    # [2026-10-06 同步] 新增 ``ky grade-regress`` 后 45 → 46（不可达 35 → 36）
    # [2026-10-06 补 GUI 入口] rag / index 由不可达移入可达：不可达 36 → 34
    # [2026-10-06 新增 ky budget] 46 → 47（不可达 34 → 35）
    assert len(registered) == nav_rail_view.CLI_MAIN_COMMAND_COUNT == 47
    assert nav_rail_view.GUI_REACHABLE_COMMANDS.isdisjoint(nav_rail_view.GUI_UNREACHABLE_COMMANDS)
    assert nav_rail_view.GUI_REACHABLE_COMMANDS | nav_rail_view.GUI_UNREACHABLE_COMMANDS == registered, (
        "覆盖边界声明与 CLI 注册表漂移：请同步 nav_rail.GUI_REACHABLE/UNREACHABLE_COMMANDS")
    assert len(nav_rail_view.GUI_REACHABLE_COMMANDS) == 12
    assert len(nav_rail_view.GUI_UNREACHABLE_COMMANDS) == 35
    assert {"rag", "index"} <= nav_rail_view.GUI_REACHABLE_COMMANDS, (
        "rag / index 必须已从不可达移入可达（2026-10-06 补 GUI 入口）")
    assert not ({"rag", "index"} & nav_rail_view.GUI_UNREACHABLE_COMMANDS)

    # ③ 每个 GUI 动作别名都映射到一个真实注册的主命令
    assert set(nav_rail_view.GUI_ACTION_TO_COMMAND) == gui_aliases
    assert set(nav_rail_view.GUI_ACTION_TO_COMMAND.values()) <= registered
    assert nav_rail_view.GUI_REACHABLE_COMMANDS == set(nav_rail_view.GUI_ACTION_TO_COMMAND.values())
    # 能在 CLI 注册表直接解析的别名（如 compose→exam）必须解析到声明的规范名；
    # diff / wechat_search 由 CLI 分发层与 TUI 分发器承接，注册表查不到属预期。
    resolved = {alias: dispatch.get_command(alias)
                for alias in nav_rail_view.GUI_ACTION_TO_COMMAND}
    for alias, cmd in resolved.items():
        if cmd is not None:
            assert cmd.name == nav_rail_view.GUI_ACTION_TO_COMMAND[alias], (
                f"别名 {alias} 解析到 {cmd.name}，与声明的 "
                f"{nav_rail_view.GUI_ACTION_TO_COMMAND[alias]} 不一致")
    assert {alias for alias, cmd in resolved.items() if cmd is None} == {
        "diff", "wechat_search"}, "预期只有这两个别名不在 CLI 注册表直查面"


# ── ⑧ W13 验收修复：空结果提示行（R3-1）+ 口语搜索别名（R3-2） ──

@pytest.fixture()
def palette(app):
    """独立驱动命令面板（模块契约：面板不触发任何后端，可离屏独立驱动）。"""
    p = CommandPalette(nav_rail_view.PALETTE_ENTRIES)
    p.show()
    app.processEvents()
    yield p
    p.close()


def _palette_hint_label(palette):
    """空结果提示行的 QLabel（objectName 判据）；不存在返回 None。"""
    for row in range(palette.list.count()):
        label = palette.list.itemWidget(palette.list.item(row))
        if label is not None and label.objectName() == "PaletteEmptyHint":
            return label
    return None


def test_palette_empty_result_shows_hint_row(palette):
    """R3-1（F5）：无匹配查询不再是零提示空白列表，而是插入不可选中的提示行。

    阴性对照：回退 ``refresh()`` 里的 ``_add_empty_hint`` 调用 → 提示行断言必红。
    """
    palette.refresh("zzz-不存在的条目-zzz")

    # 计数契约不破：提示行无 key，不参与 visible_keys（W13-7）
    assert palette.visible_keys() == [], "无命中时可见 key 必须为空列表"
    assert palette.list.count() == 1, "空结果应恰好渲染一行提示"
    assert palette.list.currentRow() == -1, "空结果不得默认选中任何行"

    label = _palette_hint_label(palette)
    assert label is not None, "空结果必须给出提示行（修复前是一片空白）"
    assert label.text() == EMPTY_HINT_TEXT, "提示文案必须与导出常量同源"
    assert all(word in label.text() for word in ("错题", "组卷", "看板")), \
        "提示应给出可用示例词（错题 / 组卷 / 看板）"

    item = palette.list.item(0)
    assert not (item.flags() & Qt.ItemIsSelectable), "提示行不得可选中"
    assert not item.data(Qt.UserRole), "提示行不得携带可执行 key"
    assert label.height() >= label.sizeHint().height(), "提示行高度不足，文字被纵向裁切"

    # 防御：提示行不可被执行（与分组标题行同机制）
    fired: list = []
    palette.activated.connect(fired.append)
    palette.list.setCurrentRow(0)
    assert palette.activate_current() == "", "提示行不得被执行"
    assert fired == [], "提示行不得发出 activated 信号"

    # 恢复有结果查询 → 提示行消失（不残留）
    palette.refresh("错题")
    assert _palette_hint_label(palette) is None, "有匹配结果时不得残留提示行"


def test_palette_hint_absent_for_empty_and_matching_queries(palette):
    """R3-1 边界：空查询（全量契约）与有匹配查询都**不**出现提示行。"""
    palette.refresh("")
    assert len(palette.visible_keys()) == len(TAB_TITLES) + len(CARD_ITEMS) == 16, \
        "空查询仍是全量 16 条（W13-7 契约；2026-10-06 起 4 视图 + 12 工具）"
    assert _palette_hint_label(palette) is None, "空查询是合法空态（全量列表），不得显示提示行"

    palette.refresh("看板")
    assert palette.visible_keys() == ["tool:build"]
    assert _palette_hint_label(palette) is None, "有匹配时不得显示提示行"


#: R3-2 词表抽检（任务书拍板的映射词子集）：每个词至少命中 1 条
ORAL_QUERY_SAMPLES = ("考情", "工具", "试卷", "刷题", "考试", "报到", "查漏", "计时", "倒计时")


@pytest.mark.parametrize("query", ORAL_QUERY_SAMPLES)
def test_palette_oral_keywords_hit_entries(palette, query):
    """R3-2（F7）：口语词至少命中 1 条面板条目（修复前这些词全部 0 命中）。

    阴性对照：回退 keywords 并入 ``haystack()`` → 本参数化用例整体变红。
    """
    palette.refresh(query)
    assert palette.visible_keys(), f"口语词 {query!r} 应至少命中 1 条，实际 0 条"
    assert _palette_hint_label(palette) is None, "有命中时不得显示空结果提示"


def test_palette_tool_keyword_hits_all_tools(palette):
    """R3-2：「工具」统一命中全部工具条目（视图条目不含该词）。"""
    palette.refresh("工具")
    keys = palette.visible_keys()
    assert len(keys) == len(CARD_ITEMS) == 12, f"「工具」应命中全部 12 条工具，实际 {keys}"
    assert set(keys) == {f"tool:{alias}" for *_x, alias in CARD_ITEMS}


@pytest.mark.parametrize("query", ("交作业", "设置", "主题", "退出", "帮助", "报告", "诊断"))
def test_palette_unmapped_words_stay_unmatched(palette, query):
    """R3-2 反向约束：无 GUI 对应动作的词不得被映射（搜到也执行不了＝误导）。

    任务书显式拍板：交作业 / 设置 / 主题 / 退出 / 帮助 / 报告 / 诊断 不映射。
    """
    palette.refresh(query)
    assert palette.visible_keys() == [], f"{query!r} 无对应 GUI 动作，不得命中条目"
    assert _palette_hint_label(palette) is not None, "无命中应显示提示行"


def test_palette_keywords_field_contract():
    """R3-2：``keywords`` 是末位可选字段（位置参数兼容），词表与条目清单对账。"""
    # 既有 (key, title, group, hint) 四参位置写法不受影响
    legacy = PaletteEntry("k", "标题", "分组", "说明")
    assert legacy.keywords == ""
    assert "说明" in legacy.haystack()

    entry = PaletteEntry("k2", "标题", "分组", "说明", "口语词")
    assert "口语词" in entry.haystack(), "keywords 必须并入匹配文本"

    # 词表覆盖全部条目（漏配会在构建 PALETTE_ENTRIES 时 KeyError，这里显式钉住）
    assert set(nav_rail_view.TOOL_KEYWORDS) == {alias for *_x, alias in CARD_ITEMS}
    assert set(nav_rail_view.VIEW_KEYWORDS) == {
        icon for icon, _title in nav_rail_view.NAV_VIEWS}
    for palette_entry in nav_rail_view.PALETTE_ENTRIES:
        assert palette_entry.keywords, f"面板条目 {palette_entry.key} 缺少口语别名"
    # 「工具」统一词：10 条工具条目全部携带（视图条目不携带）
    for palette_entry in nav_rail_view.PALETTE_ENTRIES:
        has_tool = "工具" in palette_entry.keywords
        assert has_tool == palette_entry.key.startswith(nav_rail_view.TOOL_PREFIX), \
            f"「工具」词只应加在工具条目上：{palette_entry.key}"


def test_palette_empty_hint_qss_compiles_for_all_presets():
    """R3-1：空结果提示样式在全部内置预设下可编译，且消费弱化色主题变量。"""
    from tools.theme import PRESET_ORDER

    for preset in PRESET_ORDER:
        theme = build_theme(preset)
        block = _qss_block(render_qss(theme), "#PaletteEmptyHint")
        assert "transparent" in block, f"预设 {preset} 的提示行背景应为透明"
        assert f"color: {theme.color('mut')}" in block, (
            f"预设 {preset} 的 #PaletteEmptyHint 应使用弱化色 mut")


# ── ⑨ 阶段 D：rail 双态折叠（252px 展开 ↔ 56px 图标栏） ─────────

def test_nav_rail_collapse_roundtrip(win, app):
    """折叠 → 56px 图标栏（文字清空存 tooltip、工具卡区隐藏）；展开全部还原。

    阴性对照：若 set_expanded 只改宽度、不清文字 / 不隐藏工具卡 / 不置
    ``collapsed`` 属性，本用例逐条变红。
    """
    rail = win.nav_rail
    rail.set_expanded(True)
    app.processEvents()
    texts = [item.text() for item in rail.view_items]
    assert texts == list(TAB_TITLES)
    assert all(card.isVisible() for card in rail.tool_items)

    rail.set_expanded(False)
    app.processEvents()
    assert rail.width() == RAIL_COLLAPSED_WIDTH < RAIL_WIDTH, \
        f"折叠态宽度应为 {RAIL_COLLAPSED_WIDTH}px，实际 {rail.width()}px"
    for item, text in zip(rail.view_items, texts):
        assert item.text() == "", "折叠态视图项只留图标（文字清空）"
        assert item.toolTip() == text, "原文必须保留在 tooltip"
        assert item.property("collapsed") is True, \
            "折叠属性未置位（QSS #NavItem[collapsed=\"true\"] 不生效）"
    assert all(not card.isVisible() for card in rail.tool_items), \
        "折叠态 10 个工具卡必须隐藏"
    assert not rail.brand_label.isVisible(), "折叠态品牌文字应隐藏"
    assert not rail.palette_btn.isVisible(), \
        "折叠态命令面板按钮应隐藏（Ctrl+K 快捷键仍可用）"

    rail.set_expanded(True)
    app.processEvents()
    assert rail.width() == RAIL_WIDTH
    assert [item.text() for item in rail.view_items] == texts
    assert all(item.property("collapsed") is False for item in rail.view_items)
    assert all(card.isVisible() for card in rail.tool_items)


def test_nav_rail_toggle_persists_and_restores(win, app):
    """NavToggle 点击切换折叠并写 QSettings；新窗口构建时恢复该状态。

    QSettings 键为 ``ui/rail_collapsed``（主题偏好同源 org/app）。本用例写入
    的值由 conftest 的隔离 fixture 在收尾时清除，不影响其它用例与本机 GUI。
    """
    from tools.gui import theme_apply

    def _collapsed_pref() -> bool:
        raw = theme_apply.read_pref(theme_apply.KEY_RAIL_COLLAPSED)
        return str(raw).strip().lower() in ("1", "true")

    rail = win.nav_rail
    rail.set_expanded(True)
    rail.toggle_btn.click()
    app.processEvents()
    assert not rail.is_expanded(), "点击 NavToggle 应折叠"
    assert _collapsed_pref(), "折叠态应写入 QSettings（ui/rail_collapsed）"

    # 端到端：新窗口构建时按 QSettings 恢复折叠态
    # [tripwire 修复 2026-10-07] 复用 win 的 tmp 工作区（is_unconfigured 已由
    # fixture 打桩），避免裸构造写真实仓库。
    win2 = MainWindow(workspace_root=win.workspace_root)
    win2.show()
    app.processEvents()
    try:
        assert not win2.nav_rail.is_expanded(), "重建窗口应恢复上次的折叠态"
        assert win2.nav_rail.width() == RAIL_COLLAPSED_WIDTH
        assert win2.nav_rail.view_items[0].text() == ""
    finally:
        win2.close()

    rail.toggle_btn.click()
    app.processEvents()
    assert rail.is_expanded(), "再次点击 NavToggle 应展开"
    assert not _collapsed_pref(), "展开态应写回 QSettings"


def test_nav_rail_collapse_survives_theme_toggle(win, app):
    """切主题（重设全局 styleSheet）后折叠态与 ``collapsed`` 属性仍生效。

    ``MainWindow._toggle_theme`` 会重设 QApplication 样式表并对 rail 幂等重放
    折叠态（unpolish/polish）——本用例钉住该行为：切主题不得把 56px 图标栏
    打回文字态。
    """
    rail = win.nav_rail
    rail.set_expanded(False)
    app.processEvents()
    win._toggle_theme()
    app.processEvents()
    try:
        assert not rail.is_expanded()
        assert rail.width() == RAIL_COLLAPSED_WIDTH
        assert all(item.property("collapsed") is True for item in rail.view_items)
        assert all(item.text() == "" for item in rail.view_items)
        assert all(not card.isVisible() for card in rail.tool_items)
    finally:
        win._toggle_theme()          # 切回，避免影响其它用例
