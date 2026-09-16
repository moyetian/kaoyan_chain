# -*- coding: utf-8 -*-
"""
TUI（终端中枢）回归测试

覆盖本轮修复的三类真实缺陷：
  1. **渲染路径里发网络请求** —— get_intel_ribbon() 旧实现会在未配置监控时
     就地 new AdmissionWatcher 并 add_watch()（真实网络请求），而它被
     render_header() 每轮调用 → 每渲染一次主菜单发一次请求。
  2. **宽度写死 84** —— 与终端实际列数无关，窄终端折行、宽终端留白。
  3. **ANSI 在旧版 Windows 控制台成乱码** —— 仅 os.system("color") 不足以
     启用虚拟终端处理。

textual 界面的交互用 textual 自带的 headless pilot 驱动（无需真实终端）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tui import terminal  # noqa: E402
from tools.tui import app as tui_app  # noqa: E402

try:  # pragma: no cover - 与项目其它模块一致的双路径导入
    import tui_navigator
except ImportError:  # pragma: no cover
    from tools import tui_navigator  # type: ignore


# ── 终端能力 ────────────────────────────────────────────────────

def test_display_width_cjk_and_emoji():
    assert terminal.display_width("abc") == 3
    assert terminal.display_width("中文") == 4          # 全角按 2 计
    assert terminal.display_width("🎯") == 2            # Emoji 按 2 计


def test_display_width_ignores_ansi():
    colored = "\033[96m中文\033[0m"
    assert terminal.display_width(colored) == 4, "ANSI 转义不应计入可见宽度"


def test_pad_display_aligns_right_border():
    padded = terminal.pad_display("中文", 10)
    assert terminal.display_width(padded) == 10


def test_panel_width_clamped(monkeypatch):
    monkeypatch.setattr(terminal, "terminal_columns", lambda fallback=84: 30)
    assert terminal.panel_width() == terminal.MIN_WIDTH, "过窄终端应夹到下限"
    monkeypatch.setattr(terminal, "terminal_columns", lambda fallback=84: 500)
    assert terminal.panel_width() == terminal.MAX_WIDTH, "超宽终端应夹到上限"
    monkeypatch.setattr(terminal, "terminal_columns", lambda fallback=84: 100)
    assert terminal.panel_width() == 100, "常规宽度应原样采用"


def test_no_color_env_disables_colors(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert terminal.colors_disabled() is True
    assert tui_navigator.colorize("x", "\033[96m") == "x", "NO_COLOR 下不应输出转义序列"


def test_colorize_without_color_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("KY_NO_COLOR", raising=False)
    monkeypatch.setattr(terminal, "is_tty", lambda: True)
    # 注意：colorize 走的是 tui_navigator 里导入的引用，这里直接验证终端层语义
    assert terminal.colors_disabled() is False


def test_enable_windows_vt_returns_bool():
    assert isinstance(terminal.enable_windows_vt(), bool)


# ── 渲染路径不得触网（本轮核心修复） ────────────────────────────

def test_intel_ribbon_performs_no_network_calls(monkeypatch):
    """get_intel_ribbon 只能读本地数据，绝不能注册监控/发请求。

    回归用例：旧实现会在未配置监控且有目标院校时调用
    AdmissionWatcher().add_watch()，而本函数位于主菜单渲染路径上。
    """
    calls: list[str] = []

    class SpyWatcher:
        def __init__(self, *a, **k):
            calls.append("__init__")

        def add_watch(self, *a, **k):
            calls.append("add_watch")
            return {"success": False}

        def check_updates(self, *a, **k):
            calls.append("check_updates")
            return []

    try:
        import intelligence.watcher as watcher_mod
    except ImportError:  # pragma: no cover
        from tools.intelligence import watcher as watcher_mod  # type: ignore

    monkeypatch.setattr(watcher_mod, "AdmissionWatcher", SpyWatcher)
    ribbon = tui_navigator.get_intel_ribbon()

    assert calls == [], f"渲染情报条时发生了网络相关调用: {calls}"
    assert isinstance(ribbon, list)


def test_render_header_is_pure_and_repeatable(monkeypatch):
    """重复渲染不应产生副作用（旧实现每次都会尝试注册监控）。"""
    calls: list[str] = []

    class SpyWatcher:
        def __init__(self, *a, **k):
            calls.append("__init__")

        def add_watch(self, *a, **k):
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
    assert first == second, "同一状态下两次渲染结果应一致（无隐藏副作用）"


# ── 既有对外契约 ────────────────────────────────────────────────

def test_render_contracts_preserved():
    header = tui_navigator.render_header()
    menu = tui_navigator.render_menu()
    assert "考研学习链" in header and "倒计时" in header
    assert "[1]" in menu and "今日任务" in menu
    assert "[4]" in menu and "[8]" in menu
    assert tui_navigator.execute_action("0", interactive=False) is False


def test_panel_width_used_by_renderers(monkeypatch):
    """渲染宽度必须跟随终端，而不是写死 84。"""
    monkeypatch.setattr(tui_navigator, "panel_width", lambda *a, **k: 70)
    narrow = tui_navigator.render_header()
    monkeypatch.setattr(tui_navigator, "panel_width", lambda *a, **k: 100)
    wide = tui_navigator.render_header()
    assert narrow != wide, "不同终端宽度应产出不同宽度的面板"


def test_should_use_textual_respects_legacy_env(monkeypatch):
    monkeypatch.setenv("KY_TUI_LEGACY", "1")
    assert tui_navigator.should_use_textual() is False
    monkeypatch.delenv("KY_TUI_LEGACY", raising=False)
    monkeypatch.setattr(tui_navigator, "_is_tty", lambda: False)
    assert tui_navigator.should_use_textual() is False, "非 TTY 必须回落纯文本"


# ── textual 界面（headless pilot） ──────────────────────────────

pytest.importorskip("textual", reason="未安装 textual，跳过图形化 TUI 测试")


def _run(coro):
    return asyncio.run(coro)


def test_textual_app_boots_with_menu_and_summary():
    async def scenario():
        app = tui_app.KaoyanTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            menu = app.query_one("#menu")
            summary = str(app.query_one("#summary").render())
            assert len(menu.children) == 11, "菜单应含 10 个动作 + 1 个退出"
            assert "倒计时" in summary, "左侧概要应展示倒计时（数据来自共享状态层）"
            assert "今日打卡" in summary

    _run(scenario())


def test_textual_app_keyboard_and_digit_dispatch():
    """方向键移动 + Enter 执行 + 数字快捷键 + q 退出（键鼠双控的核心路径）。"""
    async def scenario():
        app = tui_app.KaoyanTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            menu = app.query_one("#menu")
            await pilot.press("down")
            await pilot.press("down")
            assert menu.highlighted_child is not None, "方向键应产生高亮选中项"

            log = app.query_one("#log")
            before = len(log.lines)
            await pilot.press("enter")
            await pilot.pause(1.2)
            assert len(log.lines) > before, "Enter 应执行动作并写入日志"

            before_digit = len(log.lines)
            await pilot.press("1")
            await pilot.pause(1.2)
            assert len(log.lines) > before_digit, "数字快捷键应能直接执行动作"

            await pilot.press("q")
            await pilot.pause(0.2)

    _run(scenario())


def test_textual_menu_covers_all_action_aliases():
    """菜单里的每个别名都应能被 execute_action 识别（防菜单与分发漂移）。"""
    aliases = [alias for _t, _c, items in tui_navigator.MENU_GROUPS
               for _k, _n, _i, _d, alias in items]
    known = {alias for _k, _n, _d, alias in tui_navigator.MENU_OPTIONS}
    assert set(aliases) == known
    assert "exit" in aliases and "today" in aliases
