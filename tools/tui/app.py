# -*- coding: utf-8 -*-
"""
textual 版终端交互中枢（键鼠双控 · 无闪烁重绘）

[背景] 改造前 TUI 是 `input()` + `os.system("clear")` 的循环：
  * 无方向键、无高亮选中、无鼠标（README 宣传的「键鼠双控面板」与实现不符）
  * 每轮全屏清屏 → 长时间使用持续闪烁，执行结果要按 Enter 才能留住
  * 主菜单渲染路径里藏着一次网络请求（见 tui_navigator.get_intel_ribbon）
而 pyproject 里声明了 `[tui] extra = textual>=0.40.0` 却全仓零导入 ——
即「幽灵依赖」。本模块把 textual 真正用起来：方向键/鼠标/无闪烁/CSS 主题一次到位。

[与原实现的关系] 动作执行仍复用 `tui_navigator.execute_action`（单一实现，
GUI 也走它），本模块只负责呈现与交互。textual 不可用或非 TTY 时，
`tui_navigator.run_tui_loop()` 会回落到原纯文本循环，功能不缺失。
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView, RichLog, Static

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - 取决于运行方式
    import tui_navigator
except ImportError:  # pragma: no cover
    from tools import tui_navigator  # type: ignore


class ActionItem(ListItem):
    """一条菜单动作（携带别名，供选中时派发）。"""

    def __init__(self, key: str, name: str, icon: str, desc: str, alias: str):
        super().__init__(Label(f"[{key}] {icon}  {name}"))
        self.alias = alias
        self.tooltip = desc


class KaoyanTUI(App):
    """考研学习链终端中枢（textual）。"""

    TITLE = "考研学习链 · 终端全景智能中枢"
    SUB_TITLE = "键鼠双控 · ↑↓ 选择 · Enter 执行 · q 退出"

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #side { width: 46; border: round $accent; padding: 1; }
    #summary { height: auto; padding: 0 1; color: $text-muted; }
    #menu { height: 1fr; }
    #log { border: round $primary; padding: 1; }
    ListItem.--highlight { background: $accent; color: $text; }
    """

    BINDINGS = [
        Binding("q", "quit_app", "退出"),
        Binding("escape", "focus_menu", "回到菜单"),
        Binding("r", "refresh_summary", "刷新"),
        Binding("1", "run_digit('1')", "动作1", show=False),
        Binding("2", "run_digit('2')", "动作2", show=False),
        Binding("3", "run_digit('3')", "动作3", show=False),
        Binding("4", "run_digit('4')", "动作4", show=False),
        Binding("5", "run_digit('5')", "动作5", show=False),
        Binding("6", "run_digit('6')", "动作6", show=False),
        Binding("7", "run_digit('7')", "动作7", show=False),
        Binding("8", "run_digit('8')", "动作8", show=False),
        Binding("9", "run_digit('9')", "动作9", show=False),
    ]

    def __init__(self, workspace_root: Optional[Path] = None):
        super().__init__()
        self.workspace_root = Path(workspace_root) if workspace_root else ROOT

    # ── 组装 ────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="side"):
                yield Static("", id="summary")
                with ListView(id="menu"):
                    for group in tui_navigator.MENU_GROUPS:
                        _title, _color, items = group
                        for key, name, icon, desc, alias in items:
                            yield ActionItem(key, name, icon, desc, alias)
            yield RichLog(id="log", wrap=True, highlight=False, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#log", RichLog).write(
            "[b]欢迎使用考研学习链终端中枢[/b]\n"
            "↑↓ 或鼠标选择动作，Enter 执行；执行输出会保留在右侧日志区。\n"
            "提示：输入序号 1-9 可直接执行，q 退出。\n")
        self._refresh_summary()
        self.query_one("#menu", ListView).focus()

    # ── 数据 ────────────────────────────────────────────────

    def _summary_text(self) -> str:
        """左侧概要：倒计时 / 目标院校 / 今日打卡（全部走共享状态层）。"""
        try:
            try:
                from state import load_dashboard_state
            except ImportError:  # pragma: no cover
                from tools.state import load_dashboard_state  # type: ignore

            st = load_dashboard_state(self.workspace_root)
        except Exception as exc:                     # pragma: no cover
            return f"[red]状态加载失败：{exc}[/red]"

        return (f"[b]⏳ 初试倒计时[/b]  {st.days_left} 天\n"
                f"[b]🏛️ 目标[/b]  {st.school}\n"
                f"[b]📚 专业[/b]  {st.major}\n"
                f"[b]🛡️ 风格[/b]  {st.style_short}\n"
                f"[b]📋 今日打卡[/b]  {st.completed}/{st.total}（{st.rate}%）")

    def _refresh_summary(self) -> None:
        self.query_one("#summary", Static).update(self._summary_text())

    # ── 交互 ────────────────────────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        alias = getattr(item, "alias", "")
        if alias:
            self._dispatch(alias)

    def action_focus_menu(self) -> None:
        self.query_one("#menu", ListView).focus()

    def action_refresh_summary(self) -> None:
        self._refresh_summary()
        self.query_one("#log", RichLog).write("[dim]已刷新左侧概要。[/dim]")

    def action_run_digit(self, digit: str) -> None:
        for _title, _color, items in tui_navigator.MENU_GROUPS:
            for key, _name, _icon, _desc, alias in items:
                if key == digit:
                    self._dispatch(alias)
                    return

    def action_quit_app(self) -> None:
        self.exit()

    # ── 动作执行（放线程里跑，避免长任务冻住界面） ──────────

    def _dispatch(self, alias: str) -> None:
        log = self.query_one("#log", RichLog)
        log.write(f"\n[b cyan]▶[/b cyan] 正在执行：[b]{alias}[/b]")
        self._run_action(alias)

    @work(thread=True)
    def _run_action(self, alias: str) -> None:
        """在线程中执行动作并捕获其标准输出。

        textual 的 worker 允许把阻塞调用丢到后台线程，界面仍可滚动/切换 ——
        这正是改造前「执行时整个终端卡住」的解法。
        """
        if alias in ("exit", "quit", "q"):
            self.call_from_thread(self.exit)
            return

        buf = io.StringIO()
        error: Optional[Exception] = None
        try:
            with contextlib.redirect_stdout(buf):
                keep_running = tui_navigator.execute_action(alias, interactive=False)
        except Exception as exc:                     # pragma: no cover - 后端异常不该炸界面
            error = exc
            keep_running = True

        output = buf.getvalue().strip()

        def _write() -> None:
            log = self.query_one("#log", RichLog)
            if output:
                log.write(output)
            if error is not None:
                log.write(f"[red]执行异常：{error}[/red]")
            else:
                log.write(f"[green]✔ 动作 [{alias}] 执行完毕[/green]")
            self._refresh_summary()
            if not keep_running:
                self.exit()

        self.call_from_thread(_write)


def run_textual_app(workspace_root: Optional[Path] = None) -> int:
    """启动 textual 界面的入口，返回退出码。"""
    KaoyanTUI(workspace_root).run()
    return 0


def textual_available() -> bool:
    """textual 是否可用（导入探测，不触发真正的导入开销）。"""
    try:
        import importlib.util

        return importlib.util.find_spec("textual") is not None
    except Exception:                                # pragma: no cover
        return False


__all__ = ["ActionItem", "KaoyanTUI", "run_textual_app", "textual_available"]
