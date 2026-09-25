# -*- coding: utf-8 -*-
"""P3 改造回归：TUI（textual 8.x）与 CLI（rich）重设计。

覆盖六项验收：
  ① 高亮选择器必须是 textual 的**单横线** ``-highlight``（防回归写回双横线）
  ② ``ky`` 主题已注册并默认启用（与 tools/theme 的 token 同源）
  ③ 菜单三组标题渲染为分组头（仍是 11 个条目，导航语义不变）
  ④ 数字键 ``0`` 与 ``10`` 可达（``10`` = 「1 再按 0」两击序列）
  ⑤ 状态大盘结构化渲染，不倾倒 Markdown 源码（含阴性对照）
  ⑥ ``rich`` 已进核心依赖（pyproject ``[project] dependencies`` + requirements.txt）

阴性对照（证明断言有区分度，不是恒真）：
  * ①：把 CSS 换回 ``.--highlight`` 死选择器 → 高亮样式断言必红；
  * ⑤：把 ``_clean_md`` 换成恒等 → ``**`` 断言必红。

[夹具中性化] 本文件不出现任何真实院校/专业/考生信息。
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(coro):
    """同步驱动一段协程（与 tests/test_tui.py 同一模式）。"""
    return asyncio.run(coro)


@pytest.fixture()
def tui_app():
    """textual 版 TUI 模块（textual 缺失时跳过调用方测试）。"""
    pytest.importorskip("textual", reason="TUI 需可选依赖：pip install '.[tui]'")
    from tools.tui import app as module
    return module


@pytest.fixture()
def renderer():
    """CLI 渲染层模块（rich 缺失时跳过调用方测试）。"""
    pytest.importorskip("rich", reason="rich 属核心依赖：pip install -r requirements.txt")
    from tools.cli.repl import renderer as module
    return module


# ══════════════════════════════════════════════════════════════
# ① 高亮选择器：单横线 -highlight
# ══════════════════════════════════════════════════════════════

def test_tui_highlight_selector_is_single_dash(tui_app):
    """选择器写 ``.-highlight``；运行时确实命中（而非回落 textual 默认样式）。

    判据取「只有本项目规则才会产生的色值」：
      * 聚焦态文字色 = ``$ky-on-acc``（textual 默认是 auto 87% 的白色）；
      * 非聚焦态背景 = ``$ky-acc-soft``（textual 默认是半透明的 block-cursor 色）。
    仅比较背景色不行 —— textual 默认的 ``$block-cursor-background`` 由 primary
    派生，而 ky 主题的 primary 就是 acc，两者恰好同色（实测）。
    """
    css = tui_app.KaoyanTUI.CSS
    assert "ListItem.-highlight" in css, "高亮选择器缺失（应为单横线 -highlight）"
    assert ".--highlight" not in css, "双横线 --highlight 是死选择器，不得写回"

    theme = tui_app.build_textual_theme()
    on_acc = str(theme.variables["ky-on-acc"]).lower()
    acc_soft = str(theme.variables["ky-acc-soft"]).lower()

    async def scenario():
        app = tui_app.KaoyanTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            menu = app.query_one("#menu")
            await pilot.press("down")
            await pilot.pause()
            child = menu.highlighted_child
            assert child is not None, "↓ 后应有一项处于高亮态"
            assert child.has_class("-highlight"), "textual 的运行时高亮类名应为单横线"
            assert child.styles.color is not None
            assert child.styles.color.hex.lower() == on_acc, (
                f"聚焦态未命中 $ky-on-acc：{child.styles.color!r}")
            # 菜单失焦后走「模糊态」规则（$ky-acc-soft）
            app.query_one("#log").focus()
            await pilot.pause()
            assert child.styles.background is not None
            assert child.styles.background.hex.lower() == acc_soft, (
                f"非聚焦态未命中 $ky-acc-soft：{child.styles.background!r}")

    _run(scenario())


def test_tui_highlight_dead_selector_negative_control(tui_app):
    """阴性对照：选择器换回双横线后，上述「命中」断言必须不再成立。"""
    class DeadSelectorApp(tui_app.KaoyanTUI):
        CSS = tui_app.KaoyanTUI.CSS.replace(".-highlight", ".--highlight")

    on_acc = str(tui_app.build_textual_theme().variables["ky-on-acc"]).lower()

    async def scenario():
        app = DeadSelectorApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            menu = app.query_one("#menu")
            await pilot.press("down")
            await pilot.pause()
            child = menu.highlighted_child
            assert child is not None and child.has_class("-highlight")
            assert child.styles.color is not None
            assert child.styles.color.hex.lower() != on_acc, (
                "死选择器下不应命中我们的规则，否则本组断言没有区分度")

    _run(scenario())


# ══════════════════════════════════════════════════════════════
# ② ky 主题注册并默认启用
# ══════════════════════════════════════════════════════════════

def test_tui_ky_theme_registered_and_active(tui_app):
    """主题由 tools/theme 的 token 编译，注册后默认启用，CSS 变量随之注入。"""
    tokens = tui_app.load_theme_tokens()
    assert tokens, "应能读到主题 token（缺 ui_theme.json 时走内置默认预设）"

    theme = tui_app.build_textual_theme()
    assert theme.name == "ky"
    assert theme.dark is True, "内置默认预设是深色（dark）"
    # textual 8.x 的 Theme 字段是 str（不是 Color），直接比色值字面量
    assert str(theme.primary).lower() == str(tokens["acc"]).lower(), "primary 应对应 acc"
    assert str(theme.error).lower() == str(tokens["bad"]).lower(), "error 应对应 bad"

    async def scenario():
        app = tui_app.KaoyanTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert "ky" in app.available_themes, "on_mount 应注册 ky 主题"
            assert app.theme == "ky", "默认启用的主题应为 ky"
            variables = app.get_css_variables()
            assert variables["primary"].lower() == str(tokens["acc"]).lower()
            assert variables["ky-muted"].lower() == str(tokens["mut"]).lower()
            assert variables["ky-line"].lower() == str(tokens["line"]).lower()

    _run(scenario())


# ══════════════════════════════════════════════════════════════
# ③ 菜单分组标题
# ══════════════════════════════════════════════════════════════

def test_tui_menu_group_titles_rendered(tui_app):
    """三组标题挂在每组首条目上（视觉分组头），其余条目不重复标题。"""
    try:
        import tui_navigator
    except ImportError:  # pragma: no cover
        from tools import tui_navigator  # type: ignore

    group_starts = {}
    index = 0
    for title, _color, items in tui_navigator.MENU_GROUPS:
        group_starts[index] = title
        index += len(items)
    all_titles = list(group_starts.values())
    assert len(group_starts) == 3 and index == 11, "菜单应为 3 组 11 项"

    async def scenario():
        app = tui_app.KaoyanTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            items = list(app.query_one("#menu").children)
            assert len(items) == 11, "分组头不得引入额外条目（导航语义不变）"
            for position, item in enumerate(items):
                text = str(item.children[0].render())
                if position in group_starts:
                    assert group_starts[position] in text, (
                        f"第 {position} 条应带组标题：{text!r}")
                    assert item.has_class("-group-start"), "组首条目应有 -group-start"
                else:
                    assert not any(t in text for t in all_titles), (
                        f"非组首条目不得重复组标题：{text!r}")

    _run(scenario())


# ══════════════════════════════════════════════════════════════
# ④ 数字键 0 与 10 可达
# ══════════════════════════════════════════════════════════════

def test_digit_keys_zero_and_ten_reachable(tui_app):
    """``0`` 单键直达退出；``10`` 走「1 再按 0」两击序列；单按 ``1`` 不被吞掉。

    直接 monkeypatch ``_dispatch`` 记录别名 —— 真实派发会执行后端动作
    （含网络/LLM），测试环境不得触发。
    """
    keys = tui_app.KaoyanTUI.menu_keys()
    assert {"0", "10"} <= keys, "菜单数据里应存在 0 与 10"

    async def scenario():
        app = tui_app.KaoyanTUI()
        calls: list = []
        app._dispatch = lambda alias: calls.append(alias)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("0")
            await pilot.pause()
            assert calls == ["exit"], f"0 应派发退出：{calls}"

        app2 = tui_app.KaoyanTUI()
        calls2: list = []
        app2._dispatch = lambda alias: calls2.append(alias)
        async with app2.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.press("0")
            await pilot.pause()
            assert calls2 == ["wechat_search"], f"1 再按 0 应派发动作 10：{calls2}"

        app3 = tui_app.KaoyanTUI()
        calls3: list = []
        app3._dispatch = lambda alias: calls3.append(alias)
        async with app3.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause(tui_app.DIGIT_PREFIX_TIMEOUT + 0.3)
            assert calls3 == ["today"], f"超时后单键 1 应照常派发：{calls3}"

    _run(scenario())


# ══════════════════════════════════════════════════════════════
# ⑤ 状态大盘：结构化渲染，不倾倒 Markdown 源码
# ══════════════════════════════════════════════════════════════

#: 构造的 AGENTS.md 片段（刻意含 ``**加粗**``、反引号与管道表源码）
_FAKE_AGENTS_MD = (
    "### 一、学员基本盘与总战役目标（用户自定义配置区）\n"
    "\n"
    "- **目标院校**：`测试院校甲`\n"
    "- **报考专业**：`000000 测试专业`\n"
    "- **初试日期**：`2026-12-19`\n"
    "\n"
    "| 科目 | 目标成绩 |\n"
    "| --- | --- |\n"
    "| **科目一：不考数学** | **不考数学** |\n"
    "| **合计** | **370+ 分** |\n"
)


def _seed_agents(tmp_path: Path) -> Path:
    agents = tmp_path / "AGENTS.md"
    agents.write_text(_FAKE_AGENTS_MD, encoding="utf-8")
    return agents


def test_status_summary_renders_structured_without_markdown(
        renderer, monkeypatch, capsys, tmp_path):
    """状态大盘输出不得含 ``**`` / 反引号 / 管道表源码，且值要真的渲染出来。

    阴性对照见下一个用例（把 ``_clean_md`` 换恒等 → 本断言必红）。
    """
    _seed_agents(tmp_path)
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    monkeypatch.setattr(renderer, "load_config", lambda: {})
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    renderer.print_status_summary()
    out = capsys.readouterr().out

    assert "**" not in out, f"Markdown 加粗源码泄漏到终端：{out!r}"
    assert "| **" not in out, f"管道表源码泄漏到终端：{out!r}"
    assert "`" not in out, f"行内代码反引号泄漏到终端：{out!r}"
    assert "测试院校甲" in out, "解析后的值应正常渲染"
    assert "测试专业" in out
    assert "\x1b[" not in out, "非 TTY（capsys）下 rich 应自动去色"


def test_status_summary_negative_control(renderer, monkeypatch, capsys, tmp_path):
    """阴性对照：渲染退回「原样打印」时，上一用例的断言必须变红。

    把去装饰函数 ``_clean_md`` 换成恒等即等价于旧实现的「逐行倾倒源码」，
    此时 ``**`` 必然出现在输出里 —— 证明主断言有区分度。
    """
    _seed_agents(tmp_path)
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    monkeypatch.setattr(renderer, "load_config", lambda: {})
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setattr(renderer, "_clean_md", lambda text: text)

    renderer.print_status_summary()
    out = capsys.readouterr().out

    assert "**" in out, "阴性对照失效：连恒等渲染都没有 **，说明主断言无区分度"


# ══════════════════════════════════════════════════════════════
# ⑥ rich 进核心依赖 + 既有函数签名保留
# ══════════════════════════════════════════════════════════════

def test_rich_declared_as_core_dependency():
    """rich 必须在 pyproject 的核心 dependencies 与 requirements.txt 中显式声明。

    只出现在 ``[project.optional-dependencies]`` 里不算 —— CLI 是产品默认入口，
    不能依赖 ``[tui]`` extra 的传递依赖供给。
    """
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # 不用非贪婪正则切段：注释里出现 ``[tui]`` 时会在那里提前截断（实测踩过）。
    start = pyproject.index("dependencies = [")
    end = pyproject.index("\n]", start)
    core = re.findall(r'"([^"]+)"', pyproject[start:end])
    names = [re.split(r"[<>=!~;\[\s]", spec, 1)[0].strip().lower() for spec in core]
    assert "rich" in names, f"rich 未进核心 dependencies：{core}"

    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^rich\s*[><=!~]", requirements, re.M), \
        "requirements.txt 未声明 rich"


def test_renderer_uses_rich_and_drops_hand_drawn_box():
    """renderer 真的用 rich 渲染；手绘盒线字符只允许留在说明历史缺陷的 docstring 里。"""
    source = (ROOT / "tools" / "cli" / "repl" / "renderer.py").read_text(encoding="utf-8")
    assert "from rich.console import Console" in source
    assert "Panel(" in source and "Table(" in source
    # 模块 docstring 之后的正文不得再有手绘盒线（╭/╰ 是旧欢迎框的写死边框）
    body = source.split('"""', 2)[2]
    assert "╭" not in body and "╰" not in body, "手绘盒线未清理干净"


def test_renderer_public_signatures_preserved(renderer):
    """loop.py / commands 模块与既有测试依赖这些签名，不得改动。"""
    assert list(inspect.signature(renderer.print_welcome).parameters) == \
        ["live_port", "animate"]
    assert list(inspect.signature(renderer.print_status_summary).parameters) == []
    assert list(inspect.signature(renderer.print_today_tasks_summary).parameters) == \
        ["as_json", "show_flash"]
    assert list(inspect.signature(renderer.print_command_palette).parameters) == ["cfg"]
    assert list(inspect.signature(renderer.print_followup_toolbar).parameters) == []
    assert list(inspect.signature(renderer.colorize).parameters) == ["text", "color_code"]
    assert isinstance(renderer.C.BLUE, str), "ANSI 调色板类 C 必须保留"
