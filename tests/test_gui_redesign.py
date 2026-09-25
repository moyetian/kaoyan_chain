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
    from gui.widgets.nav_rail import KYNavRail
except ImportError:  # pragma: no cover
    from tools.gui.widgets.nav_rail import KYNavRail  # type: ignore


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst
    inst.setStyleSheet("")


@pytest.fixture()
def win(app):
    w = MainWindow()
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
    """rail 分「视图」「工具」两组，条目与原 4 页签 / 10 卡一一对应。"""
    rail = win.nav_rail
    assert rail.group_titles == ["视图", "工具"], (
        f"rail 应有「视图/工具」两组，实际 {rail.group_titles}")

    # 视图组 = 原 4 个页签（标题与 QTabWidget 页签文案同源）
    assert [item.text() for item in rail.view_items] == list(TAB_TITLES)
    assert len(rail.view_items) == 4

    # 工具组 = 原 10 张功能卡的动作，别名与清单完全一致（行为不变）
    assert len(rail.tool_items) == 10
    assert [item.alias for item in rail.tool_items] == [alias for *_x, alias in CARD_ITEMS]

    # 契约：_feature_buttons / feature_cards 仍指向 10 个可读图标的工具项
    assert len(win._feature_buttons) == 10
    for i, item in enumerate(rail.tool_items):
        assert win._feature_buttons[i] is item
        assert win.feature_cards[i] is item
        assert item._icon_label is not None


# ── ② 激活态非巨型 ──────────────────────────────────────────────

def test_nav_active_state_is_slim_accent_bar_not_giant_highlight(win):
    """激活态 = 左侧 3px 强调条 + acc-sub 底；结构上是紧凑行而非巨型高亮块。"""
    rail = win.nav_rail

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
                         "#UserBubble", "#AgentBubble", "#KYCard", "#StatValue",
                         "#ExamplePill"):
            assert selector in qss, f"预设 {preset} 缺少 {selector} 样式块"
        assert "{{" not in qss and "}}" not in qss, f"预设 {preset} 存在未替换占位符"
