# -*- coding: utf-8 -*-
"""Milestone M1: QSS 语法与 GUI 人体工学自动化验收测试"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.theme import PRESET_ORDER, build_theme, render_qss, Theme
from tools.theme.compile_qt import TemplateError
from tools.theme.tokens import _as_float

pytest.importorskip("PySide6", reason="PySide6 未安装，跳过 GUI 测试")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QTabBar
from tools.gui.main_window import MainWindow


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


# ── 1. QSS 编译层语法与单位去重校验 ─────────────────────────────

def test_zero_concatenated_units_in_all_builtin_presets():
    """所有内置预设编译输出严禁出现任何 pxpx 或 ptpt 非法连缀。"""
    for key in PRESET_ORDER:
        qss = render_qss(build_theme(key))
        px_matches = re.findall(r'\b\d+(?:\.\d+)?(?:px){2,}', qss, re.IGNORECASE)
        pt_matches = re.findall(r'\b\d+(?:\.\d+)?(?:pt){2,}', qss, re.IGNORECASE)
        space_dups = re.findall(r'\b\d+\s*(?:px|pt)\s+(?:px|pt)\b', qss, re.IGNORECASE)
        assert not px_matches, f"主题 {key} 中存在非法 pxpx: {px_matches}"
        assert not pt_matches, f"主题 {key} 中存在非法 ptpt: {pt_matches}"
        assert not space_dups, f"主题 {key} 中存在重复单位: {space_dups}"


def test_compiler_deduplicates_redundant_units():
    """当 token 或模板传入已带单位的值时，编译器自动消除重复单位。"""
    theme = build_theme("dark", {"radius": "16px"})
    qss = render_qss(theme, "QWidget { border-radius: {{radius}}px; }")
    assert "border-radius: 16px;" in qss
    assert "16pxpx" not in qss


def test_compiler_deduplicates_pt_and_spaced_units():
    """验证 ptpt 与空格连缀单位去重功能。"""
    theme = build_theme("dark")
    qss1 = render_qss(theme, "QWidget { font-size: 14ptpt; }")
    assert "14pt;" in qss1
    qss2 = render_qss(theme, "QWidget { padding: 12px px; }")
    assert "12px;" in qss2


def test_compiler_gate_raises_on_mixed_duplicate_units():
    """验证编译门禁在遇到未授权混合重复单位（如 pxpt）时主动抛出 TemplateError。"""
    theme = build_theme("dark")
    with pytest.raises(TemplateError, match="非法重复单位"):
        render_qss(theme, "QWidget { border: 16pxpt solid red; }")


def test_as_float_sanitizes_unit_strings():
    """验证 _as_float 剥离单位后缀并正确转换浮点数。"""
    assert _as_float("16px", 8.0) == 16.0
    assert _as_float("1.5pt", 1.0) == 1.5
    assert _as_float(" 24 px ", 10.0) == 24.0
    assert _as_float("invalid", 12.0) == 12.0
    assert _as_float(-5, 10.0) == 10.0


def test_font_size_tokens_meet_accessibility_standards():
    """正文字号 >= 13px，次要字号 >= 12px，彻底拒绝 11px 弱视字体。"""
    for key in PRESET_ORDER:
        tokens = build_theme(key).tokens
        base_sz = float(re.sub(r'[^\d.]', '', str(tokens["fs-base"])))
        sm_sz = float(re.sub(r'[^\d.]', '', str(tokens["fs-sm"])))
        assert base_sz >= 13.0, f"主题 {key} 基础字号 {base_sz}px 小于 13px"
        assert sm_sz >= 12.0, f"主题 {key} 次要字号 {sm_sz}px 小于 12px（严禁 11px 弱视字体）"


def test_dark_and_light_presets_contrast_and_typography():
    """深浅主题文字与排版结构在编译产物中均完整生效。"""
    for preset_name in ["dark", "light", "hc"]:
        theme = build_theme(preset_name)
        qss = render_qss(theme)
        assert len(qss) > 500
        assert "font-family:" in qss
        assert "font-size: 13px" in qss or "font-size: 12px" in qss


# ── 2. GUI 视觉组件间距、边距与防重叠几何校验 ───────────────────

def test_header_margins_and_button_dimensions(win):
    """HeaderBar 必须具备足够内外边距，按钮不得贴边挤压。"""
    header = win.findChild(QWidget, "HeaderBar")
    assert header is not None
    margins = header.layout().contentsMargins()
    assert margins.left() >= 16 and margins.right() >= 16
    assert margins.top() >= 8 and margins.bottom() >= 8
    assert header.layout().spacing() >= 12
    assert win.settings_btn.width() >= 70
    assert win.theme_btn.width() >= 70
    assert win.settings_btn.height() >= 28
    assert win.theme_btn.height() >= 28


def test_function_cards_breathing_room_and_no_overlap(win):
    """功能卡片区不得重叠，且必须具备充足网格呼吸间距。"""
    scroll = win.findChild(QWidget, "CardScrollArea")
    assert scroll is not None
    assert scroll.maximumHeight() >= 210

    cards = win.feature_cards
    assert len(cards) == 10
    for c in cards:
        assert c.width() >= 180
        assert c.height() >= 80

    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            r1 = cards[i].geometry()
            r2 = cards[j].geometry()
            assert not r1.intersects(r2), f"卡片 {i} 与卡片 {j} 发生几何重叠: {r1} vs {r2}"


def test_tab_widget_pane_padding_and_tab_elevation(win):
    """TabBar 与内容区必须分层隔离，内容区边距 >= 10px。"""
    assert win.tabs.tabBar().count() == 4
    qss = render_qss(win._theme)
    assert "padding: 12px" in qss or "padding: 10px" in qss
    assert "border-radius:" in qss


def test_tab_widget_layout_and_pane_geometry(win):
    """QTabWidget 存在且 4 个 Tab 标签页具备合理的几何尺寸。"""
    assert win.tabs is not None
    assert win.tabs.count() == 4
    tab_bar = win.tabs.tabBar()
    assert tab_bar.count() == 4
    for i in range(4):
        rect = tab_bar.tabRect(i)
        assert rect.width() > 40
        assert rect.height() >= 24
