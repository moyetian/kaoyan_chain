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

[P3 修复] 本版修掉四个已核实的缺陷：
  1. 高亮选择器写错：原写 `ListItem.--highlight`（双横线），而 textual 的
     `ListItem.watch_highlighted` 实际 set_class 的是**单横线** `-highlight`
     （见 textual/widgets/_list_item.py）→ 该规则是死选择器，选中态一直回落
     textual 默认蓝 #0178D4。改为 `.-highlight` 后立即生效。
  2. 未接自家主题：CSS 只有 9 行、不 `register_theme`，与 Web/GUI 的紫色体系
     不一致。现由 `tools/theme` 的语义 token 编译出 textual 主题并默认启用。
  3. 菜单分组被丢弃：`MENU_GROUPS` 的三组标题原本在遍历时被 `_title` 吞掉，
     界面平铺。现把组标题渲染为分组头（仍是同一个 ListView 的 11 个条目，
     组标题是条目首行 —— 见 `ActionItem` 的注释）。
  4. 数字键只绑 1-9，菜单里的 `10` 与 `0` 无快捷键可达。现补齐 `0`；
     `10` 用「1 再按 0」的两击序列（textual 的 BINDINGS 只认单键名，
     `Binding("10", ...)` 永远不会被触发 —— 终端不会一次送来两个字符）。
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
from textual.theme import Theme as TextualTheme
from textual.widgets import Footer, Header, Label, ListItem, ListView, RichLog, Static

from rich.markup import escape as _rich_escape

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - 取决于运行方式
    import tui_navigator
except ImportError:  # pragma: no cover
    from tools import tui_navigator  # type: ignore

#: textual 主题名（与 tools/theme 的 token 同源，Web/GUI/CLI 共用这套 token）
THEME_NAME = "ky"

#: 数字键「1 再按 0」的等待窗口（秒）。窗口内没有第二击就按单键 1 处理。
DIGIT_PREFIX_TIMEOUT = 0.35

#: token 缺失时的兜底（取自 tools/theme 的 dark 预设，保证任何情况下 CSS 可解析）
_FALLBACK = {
    "acc": "#a78bfa", "acc-hover": "#c4b5fd", "acc-press": "#8b5cf6",
    "acc-sub": "#2e1065", "on-acc": "#1e1b4b",
    "fg": "#f8fafc", "mut": "#94a3b8", "line": "#1e293b",
    "bg": "#090d16", "surf": "#111827", "surf2": "#1e293b", "surf3": "#334155",
    "ok": "#34d399", "warn": "#fbbf24", "bad": "#f87171",
}


def escape_markup(text: object) -> str:
    """把任意文本转成 Rich markup 的**字面量**（``[`` → ``\\[``）。

    [P2-8 修复·markup 注入] 日志区 ``RichLog(markup=True)`` 与概要区 ``Static``
    都按富文本解析写入内容，而其中的文本来自**后端 stdout / LLM 回复 / 学员配置**
    —— 完全不受本模块控制：

    * 正常标记会被误解析：后端打印 ``[red]x[/red]`` 时用户看到的是"x"而非原文；
    * 畸形标记会直接抛 ``MarkupError``（如 ``[/unclosed]``、``[不是标签]``），
      异常从 ``log.write()`` 冒出 → 整个 TUI 崩掉。

    故：**本模块自造的装饰性标记照常写，任何外部文本写入前必须过本函数**。
    """
    return _rich_escape(str(text))


def load_theme_tokens(workspace_root: Optional[Path] = None) -> dict:
    """读取工作区主题 token（``ui_theme.json`` 优先，缺失则内置默认预设）。

    失败时返回空字典 —— 主题是外观层，取不到就退回 `_FALLBACK` 的内置色值，
    不该让终端中枢起不来。
    """
    root = Path(workspace_root) if workspace_root else ROOT
    try:
        try:
            from theme import load_theme
        except ImportError:  # pragma: no cover
            from tools.theme import load_theme  # type: ignore

        return dict(load_theme(root).tokens)
    except Exception:                                  # pragma: no cover - 纯外观降级
        return {}


def _hex(tokens: dict, key: str) -> str:
    value = tokens.get(key)
    if isinstance(value, str) and value.startswith("#"):
        return value
    return _FALLBACK.get(key, "#ffffff")


def build_textual_theme(workspace_root: Optional[Path] = None) -> TextualTheme:
    """把 ``tools/theme`` 的语义 token 编译成 textual 主题对象。

    [为什么这样写] textual 8.x 的签名是 ``register_theme(theme: Theme)``（**不是**
    早期的 ``register_theme(name, primary=..., ...)``，见 textual/app.py），
    故这里构造 ``textual.theme.Theme`` 数据类。

    token → 主题字段的映射刻意保持语义：ok/warn/bad 直接对应
    success/warning/error，acc 对应 primary —— 这样 `$success`/`$error` 之类
    的 CSS 变量在四端含义一致。token 里没有对应项的结构色（次要文字/分隔线/
    三级表面）走 ``variables``，以 ``ky-`` 前缀暴露给 CSS（``$ky-muted`` 等）。
    """
    tokens = load_theme_tokens(workspace_root)
    dark = str(tokens.get("mode", "dark")) != "light"

    def c(key: str) -> str:
        return _hex(tokens, key)

    return TextualTheme(
        name=THEME_NAME,
        primary=c("acc"),
        secondary=c("acc-press"),
        accent=c("acc-hover"),
        success=c("ok"),
        warning=c("warn"),
        error=c("bad"),
        foreground=c("fg"),
        background=c("bg"),
        surface=c("surf"),
        panel=c("surf2"),
        dark=dark,
        variables={
            "ky-muted": c("mut"),
            "ky-line": c("line"),
            "ky-surf3": c("surf3"),
            "ky-acc-soft": c("acc-sub"),
            "ky-on-acc": c("on-acc"),
        },
    )


#: 菜单数据（`tui_navigator.MENU_GROUPS`）里的组色是 **ANSI 转义码**（16 色时代
#: 的产物），而 markup 只认颜色名/十六进制：把 `\033[93m` 直接拼进标签会被解析器
#: 当字面量，并连带把相邻的 `[b]` 标签吞掉（实测抛 "closing tag '[/b]' does not
#: match any open tag"）。故在此做一次映射，未知码则不染色（只加粗）。
_ANSI_TO_MARKUP = {
    "\033[91m": "red", "\033[92m": "green", "\033[93m": "yellow",
    "\033[94m": "blue", "\033[95m": "magenta", "\033[96m": "cyan",
    "\033[97m": "white",
}


class ActionItem(ListItem):
    """一条菜单动作（携带别名，供选中时派发）。

    [分组标题为什么不单独成条目] ``ListView`` 的键盘/鼠标导航按 ``children``
    下标走，插入非 ``ListItem`` 的标题条目会让 ↑↓ 停在标题上、Enter 无处派发
    （且既有回归测试断言菜单恰有 11 个条目）。故组标题作为**该组首条目的首行**
    渲染：视觉上仍是分组头，导航语义不变。
    """

    def __init__(self, key: str, name: str, icon: str, desc: str, alias: str,
                 group_title: str = "", group_color: str = ""):
        self.group_title = group_title
        if group_title:
            color = _ANSI_TO_MARKUP.get(group_color, "")
            super().__init__(Label(f"[b]{color}{group_title}[/b]\n[{key}] {icon}  {name}"))
            self.add_class("-group-start")
        else:
            super().__init__(Label(f"[{key}] {icon}  {name}"))
        self.alias = alias
        self.tooltip = desc


class KaoyanTUI(App):
    """考研学习链终端中枢（textual）。"""

    TITLE = "考研学习链 · 终端全景智能中枢"
    SUB_TITLE = "键鼠双控 · ↑↓ 选择 · Enter 执行 · q 退出"

    #: [P3 修复] 选择器用 textual 的**单横线** `-highlight`
    #: （ListItem.watch_highlighted → set_class(value, "-highlight")）。
    #: 双横线是死选择器：选中态会静默回落到 textual 默认蓝。
    #: 模糊态（菜单未聚焦）与聚焦态各写一条，后者优先级与 textual 默认规则相同
    #: 但位于应用样式表之后，故生效。
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #side {
        width: 46;
        border: round $primary;
        padding: 1 1;
        background: $surface;
    }
    #summary { height: auto; padding: 0 0 1 0; color: $ky-muted; }
    #menu { height: 1fr; background: transparent; }
    #log { border: round $primary; padding: 0 1; background: $surface; }
    ListView > ListItem { padding: 0 1; }
    ListView > ListItem.-group-start {
        border-top: solid $ky-line;
        padding-top: 1;
    }
    ListView > ListItem.-highlight {
        background: $ky-acc-soft;
        color: $foreground;
    }
    ListView:focus > ListItem.-highlight {
        background: $primary;
        color: $ky-on-acc;
        text-style: bold;
    }
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
        Binding("0", "run_digit('0')", "退出", show=False),
    ]

    def __init__(self, workspace_root: Optional[Path] = None):
        super().__init__()
        self.workspace_root = Path(workspace_root) if workspace_root else ROOT
        #: 编译一次主题对象：on_mount 注册它，CSS 变量默认值也取自它
        self._textual_theme = build_textual_theme(self.workspace_root)
        #: 「1 再按 0」序列的挂起位（见 action_run_digit）
        self._pending_digit = ""

    # ── 组装 ────────────────────────────────────────────────

    def get_theme_variable_defaults(self) -> dict:
        """声明 ``ky-*`` 自定义 CSS 变量的默认值。

        [为什么必须显式声明] textual 在 ``on_mount``（我们注册主题的地方）**之前**
        就要解析应用 CSS；此时 ``$ky-muted`` 尚未由主题注入，会被判为
        "reference to undefined variable" 并导致整个样式表失效（实测）。
        这里先给出默认值，主题注册后由 ``Theme.variables`` 覆盖 ——
        ``App.get_css_variables`` 的合并顺序是 ``{**默认值, **主题变量}``。
        """
        theme = getattr(self, "_textual_theme", None)
        if theme is None:                              # pragma: no cover - 构造期兜底
            theme = build_textual_theme(getattr(self, "workspace_root", None))
        return dict(theme.variables)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="side"):
                yield Static("", id="summary")
                with ListView(id="menu"):
                    for title, color, items in tui_navigator.MENU_GROUPS:
                        for idx, (key, name, icon, desc, alias) in enumerate(items):
                            # 组标题只挂在该组首条目上，其余条目 group_title 为空
                            yield ActionItem(key, name, icon, desc, alias,
                                             group_title=title if idx == 0 else "",
                                             group_color=color)
            yield RichLog(id="log", wrap=True, highlight=False, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        # 主题必须在设置 self.theme 之前注册（否则 _validate_theme 抛错）
        self.register_theme(self._textual_theme)
        self.theme = THEME_NAME
        self.query_one("#log", RichLog).write(
            "[b]欢迎使用考研学习链终端中枢[/b]\n"
            "↑↓ 或鼠标选择动作，Enter 执行；执行输出会保留在右侧日志区。\n"
            f"快捷键：[b]1-9[/b] 直达动作；[b]10[/b] = 先按 1 再按 0；"
            f"[b]0[/b] = 退出；[b]r[/b] 刷新；[b]q[/b] 退出。\n")
        self._refresh_summary()
        self.query_one("#menu", ListView).focus()

    # ── 数据 ────────────────────────────────────────────────

    def _progress_bar(self, done: int, total: int, width: int = 10) -> str:
        """纯文本进度条（用主题色包在 markup 里由调用方着色）。"""
        if total <= 0:
            return "░" * width
        filled = max(0, min(width, round(width * done / total)))
        return "█" * filled + "░" * (width - filled)

    def _summary_text(self) -> str:
        """左侧概要：倒计时 / 目标院校 / 今日打卡（全部走共享状态层）。

        [P3] 升级为「状态色点 + 进度条」：倒计时用 warn 色、打卡进度用 ok 色，
        次要说明用 mut 色 —— 颜色全部取自 tools/theme 的 token，与 Web/GUI 同源。
        """
        tokens = load_theme_tokens(self.workspace_root)
        acc, ok, warn, mut = (_hex(tokens, "acc"), _hex(tokens, "ok"),
                              _hex(tokens, "warn"), _hex(tokens, "mut"))
        try:
            try:
                from state import load_dashboard_state
            except ImportError:  # pragma: no cover
                from tools.state import load_dashboard_state  # type: ignore

            st = load_dashboard_state(self.workspace_root)
        except Exception as exc:                     # pragma: no cover
            # [缺陷修复·markup 注入] 异常消息是最典型的不可信文本：
            # OSError 带 `[Errno 2]`、KeyError 带 `[key]`，未转义时会被
            # Static(markup=True) 当富文本解析 → MarkupError 崩掉整个 TUI。
            # 同函数内的 school/major/style_short 早已过 escape_markup，
            # 唯独这里漏了。
            return f"[red]状态加载失败：{escape_markup(exc)}[/red]"

        bar = self._progress_bar(st.completed, st.total)
        return (f"[{acc}]⏳[/] [b]初试倒计时[/b]  [{warn}]{st.days_left}[/] 天\n"
                f"[{acc}]🏛️[/] [b]目标[/b]  {escape_markup(st.school)}\n"
                f"[{acc}]📚[/] [b]专业[/b]  {escape_markup(st.major)}\n"
                f"[{acc}]🛡️[/] [b]风格[/b]  {escape_markup(st.style_short)}\n"
                f"[{acc}]📋[/] [b]今日打卡[/b]  [{ok}]{bar}[/] "
                f"{st.completed}/{st.total} [{mut}]({st.rate}%)[/]")

    def _refresh_summary(self) -> None:
        self.query_one("#summary", Static).update(self._summary_text())

    def _log_write(self, text: str) -> None:
        """把**外部文本**（后端 stdout / 异常信息）按字面写入日志区。"""
        self.query_one("#log", RichLog).write(escape_markup(text))

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

    #: 菜单里存在的数字键（含两位数 10）
    @staticmethod
    def menu_keys() -> set:
        return {key for _title, _color, items in tui_navigator.MENU_GROUPS
                for key, _name, _icon, _desc, _alias in items}

    def action_run_digit(self, digit: str) -> None:
        """数字键派发。

        [为什么需要两击序列] 菜单键里有 ``10``，而 textual 的 BINDINGS 只能绑
        **单键名**（终端一次只送来一个字符，`Binding("10", ...)` 永不触发）。
        故 ``1`` 先挂起一小段时间：窗口内再按 ``0`` → 派发 ``10``；
        窗口内按别的数字 → 先把 ``1`` 补跑掉再处理新键；窗口内无第二击 → 派发 ``1``。
        """
        if self._pending_digit == "1":
            self._pending_digit = ""
            if digit == "0":
                self._dispatch_menu_key("10")
                return
            self._dispatch_menu_key("1")        # 补跑被挂起的单键
        if digit == "1" and "10" in self.menu_keys():
            self._pending_digit = "1"
            self.set_timer(DIGIT_PREFIX_TIMEOUT, self._flush_pending_digit)
            return
        self._dispatch_menu_key(digit)

    def _flush_pending_digit(self) -> None:
        """超时未等到第二击：把挂起的单键按原样派发。"""
        digit, self._pending_digit = self._pending_digit, ""
        if digit and self.is_running:
            self._dispatch_menu_key(digit)

    def _dispatch_menu_key(self, key: str) -> None:
        for _title, _color, items in tui_navigator.MENU_GROUPS:
            for menu_key, _name, _icon, _desc, alias in items:
                if menu_key == key:
                    self._dispatch(alias)
                    return

    def action_quit_app(self) -> None:
        self.exit()

    # ── 动作执行（放线程里跑，避免长任务冻住界面） ──────────

    def _dispatch(self, alias: str) -> None:
        log = self.query_one("#log", RichLog)
        log.write(f"\n[b cyan]▶[/b cyan] 正在执行：[b]{escape_markup(alias)}[/b]")
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
                # 后端 stdout 是最典型的不可信输入（可能是任意模块/LLM 打印的
                # `[red]` / `[/unclosed]`）—— 必须转义，否则误解析或抛 MarkupError。
                self._log_write(output)
            if error is not None:
                log.write(f"[red]执行异常：{escape_markup(error)}[/red]")
            else:
                log.write(f"[green]✔ 动作 [{escape_markup(alias)}] 执行完毕[/green]")
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


__all__ = ["ActionItem", "KaoyanTUI", "THEME_NAME", "build_textual_theme",
           "escape_markup", "load_theme_tokens", "run_textual_app",
           "textual_available"]
