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


def test_escape_markup_is_literal_and_idempotent_for_plain_text():
    """[P2-8] escape_markup 只转义 `[`，普通文本原样返回。"""
    assert tui_app.escape_markup("[red]x[/red]") == "\\[red]x\\[/red]"
    assert tui_app.escape_markup("纯文本，无标记") == "纯文本，无标记"
    assert tui_app.escape_markup(123) == "123"


def test_summary_failure_escapes_exception_markup(tmp_path, monkeypatch):
    """[缺陷修复] 状态加载失败时的异常消息必须转义后再交给 Static(markup=True)。

    缺陷现场：``_summary_text`` 里 ``school``/``major``/``style_short`` 都过了
    ``escape_markup``，唯独异常分支 ``f"[red]状态加载失败：{exc}[/red]"`` 的
    ``{exc}`` 没转义。异常消息带 Rich 认得的标记时（小写字母或 ``/`` 开头），
    会被当富文本解析 → 正常标记被吞掉、畸形标记（``[/unclosed]``）直接抛
    ``MarkupError`` 崩掉 TUI。

    注意：Rich 的 markup 正则只认 ``[a-z#/@...]`` 开头的 tag，``[Errno 2]``
    这种大写开头的方括号本来就是字面量 —— 故此处刻意用 ``[b]``/``[/unclosed]``。
    """
    try:
        import state as state_mod
    except ImportError:  # pragma: no cover
        from tools import state as state_mod  # type: ignore

    def _boom(_root):
        raise OSError("[b]状态文件缺失[/unclosed]")

    monkeypatch.setattr(state_mod, "load_dashboard_state", _boom)

    async def scenario():
        app = tui_app.KaoyanTUI(workspace_root=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._refresh_summary()          # 修复前此处会抛 MarkupError
            await pilot.pause()
            rendered = str(app.query_one("#summary").render())
            assert "[b]状态文件缺失[/unclosed]" in rendered, (
                f"异常消息未按字面显示：{rendered!r}")

    _run(scenario())


def test_richlog_renders_backend_output_literally(tmp_path):
    """[P2-8 回归] 后端 stdout / LLM 回复里的 markup 必须按字面显示且不抛异常。

    修复前 ``RichLog(..., markup=True)`` 而写入内容直接来自后端 stdout：
      * ``[red]x[/red]`` 会被当富文本解析 → 用户看到 "x" 而不是原文；
      * 畸形标记 ``[/unclosed]`` 直接抛 ``rich.errors.MarkupError``
        （"closing tag '[/unclosed]' ... doesn't match any open tag"）
        从 ``log.write()`` 冒出 → 整个 TUI 崩掉。

    这里走的是应用真实的写入路径（``_log_write``），而不是绕过它单独测转义函数。

    阴性对照：把 ``tools/tui/app.py`` 里的 ``escape_markup`` 改成恒等
    （``return str(text)``），本用例必须变红 —— ``[/unclosed]`` 那行会抛
    MarkupError，且 ``[red]x[/red]`` 渲染出来只剩 "x"。
    """
    async def scenario():
        app = tui_app.KaoyanTUI(workspace_root=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._log_write("[red]x[/red]")
            app._log_write("[/unclosed]")
            await pilot.pause()
            log = app.query_one("#log")
            rendered = "".join(strip.text for strip in log.lines)
            assert "[red]x[/red]" in rendered, f"未按字面显示，实际：{rendered!r}"
            assert "[/unclosed]" in rendered, f"未按字面显示，实际：{rendered!r}"

    _run(scenario())
