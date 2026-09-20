# -*- coding: utf-8 -*-
"""TUI（终端中枢）回归测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 依赖缺失时必须在导入 tools.tui.app 之前跳过整个模块。
pytest.importorskip("textual", reason="需安装可选依赖：pip install '.[tui]'")

from tools.tui import app as tui_app  # noqa: E402
from tools.tui import terminal  # noqa: E402

try:  # pragma: no cover - 取决于运行方式
    import tui_navigator
except ImportError:  # pragma: no cover
    from tools import tui_navigator  # type: ignore


def test_display_width_cjk_and_emoji():
    assert terminal.display_width("abc") == 3
    assert terminal.display_width("中文") == 4
    assert terminal.display_width("🎯") == 2


def test_display_width_ignores_ansi():
    colored = "\033[96m中文\033[0m"
    assert terminal.display_width(colored) == 4


def test_pad_display_aligns_right_border():
    padded = terminal.pad_display("中文", 10)
    assert terminal.display_width(padded) == 10


def test_panel_width_clamped(monkeypatch):
    monkeypatch.setattr(terminal, "terminal_columns", lambda fallback=84: 30)
    assert terminal.panel_width() == terminal.MIN_WIDTH
    monkeypatch.setattr(terminal, "terminal_columns", lambda fallback=84: 500)
    assert terminal.panel_width() == terminal.MAX_WIDTH
    monkeypatch.setattr(terminal, "terminal_columns", lambda fallback=84: 100)
    assert terminal.panel_width() == 100


def test_no_color_env_disables_colors(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert terminal.colors_disabled() is True
    assert tui_navigator.colorize("x", "\033[96m") == "x"


def test_colorize_without_color_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("KY_NO_COLOR", raising=False)
    monkeypatch.setattr(terminal, "is_tty", lambda: True)
    assert terminal.colors_disabled() is False


def test_enable_windows_vt_returns_bool():
    assert isinstance(terminal.enable_windows_vt(), bool)


def test_intel_ribbon_performs_no_network_calls(monkeypatch):
    calls = []

    class SpyWatcher:
        def __init__(self, *args, **kwargs):
            calls.append("__init__")

        def add_watch(self, *args, **kwargs):
            calls.append("add_watch")
            return {"success": False}

        def check_updates(self, *args, **kwargs):
            calls.append("check_updates")
            return []

    try:
        import intelligence.watcher as watcher_mod
    except ImportError:  # pragma: no cover
        from tools.intelligence import watcher as watcher_mod  # type: ignore

    monkeypatch.setattr(watcher_mod, "AdmissionWatcher", SpyWatcher)
    ribbon = tui_navigator.get_intel_ribbon()
    assert calls == []
    assert isinstance(ribbon, list)


def test_render_header_is_pure_and_repeatable(monkeypatch):
    calls = []

    class SpyWatcher:
        def __init__(self, *args, **kwargs):
            calls.append("__init__")

        def add_watch(self, *args, **kwargs):
            calls.append("add_watch")
            return {"success": False}

    try:
        import intelligence.watcher as watcher_mod
    except ImportError:  # pragma: no cover
        from tools.intelligence import watcher as watcher_mod  # type: ignore

    monkeypatch.setattr(watcher_mod, "AdmissionWatcher", SpyWatcher)
    first = tui_navigator.render_header()
    second = tui_navigator.render_header()
    assert calls == []
    assert first == second


def test_render_contracts_preserved():
    header = tui_navigator.render_header()
    menu = tui_navigator.render_menu()
    assert "考研学习链" in header and "倒计时" in header
    assert "[1]" in menu and "今日任务" in menu
    assert "[4]" in menu and "[8]" in menu
    assert tui_navigator.execute_action("0", interactive=False) is False


def test_panel_width_used_by_renderers(monkeypatch):
    monkeypatch.setattr(tui_navigator, "panel_width", lambda *args, **kwargs: 70)
    narrow = tui_navigator.render_header()
    monkeypatch.setattr(tui_navigator, "panel_width", lambda *args, **kwargs: 100)
    wide = tui_navigator.render_header()
    assert narrow != wide


def test_should_use_textual_respects_legacy_env(monkeypatch):
    monkeypatch.setenv("KY_TUI_LEGACY", "1")
    assert tui_navigator.should_use_textual() is False
    monkeypatch.delenv("KY_TUI_LEGACY", raising=False)
    monkeypatch.setattr(tui_navigator, "_is_tty", lambda: False)
    assert tui_navigator.should_use_textual() is False


def _run(coro):
    return asyncio.run(coro)


def test_textual_app_boots_with_menu_and_summary():
    async def scenario():
        app = tui_app.KaoyanTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            menu = app.query_one("#menu")
            summary = str(app.query_one("#summary").render())
            assert len(menu.children) == 11
            assert "倒计时" in summary
            assert "今日打卡" in summary

    _run(scenario())


def test_textual_app_keyboard_and_digit_dispatch():
    async def scenario():
        app = tui_app.KaoyanTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            menu = app.query_one("#menu")
            await pilot.press("down")
            await pilot.press("down")
            assert menu.highlighted_child is not None

            log = app.query_one("#log")
            before = len(log.lines)
            await pilot.press("enter")
            await pilot.pause(1.2)
            assert len(log.lines) > before

            before_digit = len(log.lines)
            await pilot.press("1")
            await pilot.pause(1.2)
            assert len(log.lines) > before_digit

            await pilot.press("q")
            await pilot.pause(0.2)

    _run(scenario())


def test_textual_menu_covers_all_action_aliases():
    aliases = [
        alias
        for _title, _color, items in tui_navigator.MENU_GROUPS
        for _key, _name, _icon, _desc, alias in items
    ]
    known = {alias for _key, _name, _desc, alias in tui_navigator.MENU_OPTIONS}
    assert set(aliases) == known
    assert "exit" in aliases and "today" in aliases
