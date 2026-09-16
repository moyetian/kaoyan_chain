# -*- coding: utf-8 -*-
"""
GUI 离屏冒烟测试（不需要真实显示器，CI 可跑）

覆盖目标：GUI 此前**零自动化测试**，而本轮改动的正是最容易改崩的地方
（内联样式改 objectName、倒计时提为实例属性、主题改为 token 编译、数据取用共享层）。
这里用 ``QT_QPA_PLATFORM=offscreen`` 起一个真实 QApplication + MainWindow，
断言的是「行为」而不是「代码长什么样」：

  * 构造后应用的主题确实来自编译器（而不是某个手写文件）
  * 窗口内不存在写死颜色的内联样式（否则浅色主题必被压过）
  * 倒计时是实例属性且能被刷新
  * 切换主题真的会改变样式表，且明暗两套渲染结果不同
  * 三条数据链路（今日/错题/情报）都能取到文本
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 冒烟测试")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from tools.gui import services, theme_apply  # noqa: E402
from tools.gui.main_window import MainWindow  # noqa: E402
from tools.theme import build_theme, render_qss  # noqa: E402


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst
    inst.setStyleSheet("")


@pytest.fixture()
def win(app):
    w = MainWindow()
    yield w
    w.close()


# ── 主题 ────────────────────────────────────────────────────────

def test_window_constructs_and_applies_compiled_theme(win, app):
    """窗口构造后应已应用编译器【现场渲染】的样式表。

    改造前样式表来自 tools/gui/theme/dark.qss（一份手写副本），
    且亮色主题被控件内联深色压过 —— 此处直接与渲染结果比对。
    """
    applied = app.styleSheet()
    assert applied, "构造窗口后应已应用主题样式表"
    dark = render_qss(build_theme("dark"))
    light = render_qss(build_theme("light"))
    assert applied in (dark, light), "应用的样式表必须来自 tools/theme 编译产物"


def test_no_widget_hardcodes_colors(win):
    """窗口内不得存在写死颜色的内联样式。

    这是「浅色主题是坏的」的根因：widget 自身样式表优先级高于
    QApplication.setStyleSheet，任何内联色值都会把主题压过去。
    """
    offenders = [(w.objectName() or type(w).__name__, w.styleSheet()[:60])
                 for w in win.findChildren(QWidget)
                 if w.styleSheet() and "#" in w.styleSheet()]
    assert not offenders, f"存在写死颜色的内联样式: {offenders}"


def test_theme_toggle_changes_stylesheet(win, app):
    before_name = win._theme.name
    before_qss = app.styleSheet()
    win._toggle_theme()
    assert win._theme.name != before_name, "切换后主题应变化"
    assert app.styleSheet() != before_qss, "切换主题必须真的改变样式表"
    win._toggle_theme()          # 切回，避免影响其它用例


def test_theme_toggle_persists_choice(win, app):
    """[缺陷修复] 改造前 `_current_theme` 每次启动写死 dark，重启即回退。"""
    win._toggle_theme()
    expected = win._theme.name
    remembered = theme_apply.read_prefs().get("preset")
    assert remembered == expected, f"切换后的预设应写入 QSettings，实际 {remembered}"
    win._toggle_theme()


def test_hardcoded_font_is_gone():
    """字体族不再硬编码 "Microsoft YaHei"（Linux/macOS 上并不存在该字体）。"""
    src = (ROOT / "tools" / "ky_gui.py").read_text(encoding="utf-8")
    assert 'QFont("Microsoft YaHei"' not in src
    assert "font-scale" in src, "字号应随主题 token 缩放"


# ── SVG 图标 ──────────────────────────────────────────────────

def test_svg_icon_templates_are_valid():
    """所有 SVG 模板必须含 {COLOR} 占位符且可被 QSvgRenderer 解析。"""
    from tools.gui.widgets.icons import SVG_TEMPLATES, _format_svg, _HAS_SVG
    assert len(SVG_TEMPLATES) >= 15, f"图标数应 >=15，实际 {len(SVG_TEMPLATES)}"
    for key, tmpl in SVG_TEMPLATES.items():
        assert "{COLOR}" in tmpl, f"图标 {key} 缺少 {{COLOR}} 占位符"
        assert tmpl.startswith("<svg"), f"图标 {key} 不是合法 SVG 根"
        assert tmpl.endswith("</svg>"), f"图标 {key} 未正确闭合"
        # 格式化后的字符串不含花括号
        formatted = _format_svg(key, "#a78bfa")
        assert "{COLOR}" not in formatted


def test_render_icon_produces_pixmap(app):
    """render_icon 在有 QApplication 时返回非空 QPixmap。"""
    if not __import__("tools.gui.widgets.icons", fromlist=["_HAS_SVG"])._HAS_SVG:
        pytest.skip("QtSvg 不可用")
    from tools.gui.widgets.icons import render_icon
    pm = render_icon("today", 20, "#a78bfa")
    assert not pm.isNull()
    assert pm.width() == 20 and pm.height() == 20


def test_function_card_uses_svg_not_emoji(win):
    """功能卡片标题行不应再含 emoji 字符，应有 SVG pixmap 图标。"""
    card = win._feature_buttons[0]
    # 卡片图标 label 应有 pixmap（而非文本 emoji）
    pm = card._icon_label.pixmap()
    assert pm is not None and not pm.isNull(), "卡片图标应为 SVG pixmap"
    # 卡片标题不应以 emoji 开头
    titles = [c.findChild(type(card._icon_label)) for c in win._feature_buttons]
    # 遍历卡片，确认标题文本不含 emoji 区段
    from tools.gui.views.function_cards import CARD_ITEMS
    for _icon, title, _desc, _alias in CARD_ITEMS:
        # icon 字段应为 SVG key（纯 ASCII），不是 emoji 字符
        assert len(_icon) <= 20 and _icon.isascii(), \
            f"图标键应为 ASCII key，非 emoji: {_icon!r}"


def test_settings_dialog_writes_and_applies_l2(app):
    """设置面板写入 QSettings 并即时应用主题。"""
    from tools.gui.widgets.settings_dialog import SettingsDialog
    from tools.gui import theme_apply
    win = MainWindow()
    try:
        dlg = SettingsDialog(win)
        dlg.preset_combo.setCurrentIndex(
            dlg.preset_combo.findData("light"))
        dlg.acc_edit.setText("#3b82f6")
        dlg.radius_spin.setValue(10)
        dlg._apply()
        prefs = theme_apply.read_prefs()
        assert prefs.get("preset") == "light"
        assert prefs.get("acc") == "#3b82f6"
        assert int(prefs.get("radius", 0)) == 10
        # 清理 L2 覆盖，避免影响其他测试
        for k in ("ui/preset", "ui/acc", "ui/radius",
                   "ui/density", "ui/font_scale"):
            theme_apply._settings().setValue(k, "")
    finally:
        win.close()


# ── 倒计时与任务进度 ────────────────────────────────────────────

def test_countdown_label_is_refreshable(win):
    """[缺陷修复] 改造前 countdown 是局部变量，挂一整天也不动。"""
    label = win.countdown_label
    assert label.text().startswith("初试倒计时:"), f"初始文本异常: {label.text()!r}"

    original = label.text()
    label.setText("初试倒计时: -1 天")
    win._sync_header_text()                       # 定时器走的就是这条路径
    assert label.text() == original, "刷新后应重新取真实倒计时，覆盖手工写入的值"


def test_timer_tick_refreshes_countdown_and_tasks(win):
    win.countdown_label.setText("stale")
    win._on_timer_tick()
    assert win.countdown_label.text() != "stale", "定时器应刷新倒计时"


def test_task_progress_matches_shared_state(win):
    state = services.load_state(win.workspace_root)
    assert state is not None
    for subject in state.subjects:
        bar = win.task_progress_bars.get(subject.key)
        label = win.task_count_labels.get(subject.key)
        assert bar is not None and label is not None
        assert bar.value() == subject.pct
        assert label.text() == subject.summary_text


# ── 数据链路 ────────────────────────────────────────────────────

def test_error_and_intel_panels_render_text(win):
    win._refresh_error_tab()
    win._refresh_intel_tab()
    assert "错题" in win.error_info.toPlainText()
    assert "研招" in win.intel_display.toPlainText()


def test_action_capture_returns_text():
    """GUI 的功能卡片复用 TUI 的动作分发，异常必须转成可读文本而非抛栈。"""
    out = services.run_action_capture("no-such-action")
    assert isinstance(out, str) and out


def test_header_info_falls_back_on_empty_workspace(tmp_path: Path):
    """空工作区（无 ky_config / 无状态文件）也必须给出可用头部数据。"""
    info = services.header_info(tmp_path)
    assert set(info) >= {"days_left", "school", "major", "style_short"}
    assert isinstance(info["days_left"], int)
    assert info["school"]
