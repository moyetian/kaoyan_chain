# -*- coding: utf-8 -*-
"""GUI 聊天页修复批：公式 Unicode 美化 + 思考链折叠 + ``clear()`` 接口

覆盖两项已核实体验缺陷的修复：

  1. 私教气泡 LaTeX 不渲染 —— ``$$\frac{a}{b}$$`` 源码裸露。现复用终端同源
     美化器（``tools/skills/latex_beautifier.py``）转 Unicode 后 setText，
     失败降级原文（绝不吞消息）；
  2. 思考链逐条上屏占满页面 —— 步骤收进默认收起的 ``StepBubble``
     （一行「🧠 思考过程 · N 步」），答案开始/收尾/用户新发言即封口；
  3. ``ChatView.clear()`` —— 清空并恢复空状态（「新建/恢复会话」前置接口）。

[为什么单开文件] 本批主题是「聊天页体验缺陷修复」，与既有
``test_gui_redesign.py``（P2 重设计验收）、``test_gui_smoke.py``（冒烟）、
``test_qss_and_gui_ergonomics.py``（QSS 人体工学）的既定范围不同，混入会
削弱三者各自的回归语义；单开便于回溯本批缺陷。

运行（本机铁律，必须带 basetemp）：
  py -m pytest tests/test_chat_view_formula_steps.py \
      --basetemp=.pytest_tmp/fixchat -p no:cacheprovider -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 聊天页用例")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QToolButton  # noqa: E402

from tools.gui.widgets.chat_view import ChatView  # noqa: E402

#: 公式样例：display 公式（$$...$$）+ 行内上标（$x^2$）。
#: raw string —— ``\frac`` 必须按字面进入被测文本。
LATEX_SAMPLE = r"结果为 $$\frac{a}{b}$$ 且 $x^2$"


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst
    inst.setStyleSheet("")


@pytest.fixture()
def view(app):
    v = ChatView()
    v.resize(480, 640)
    v.show()
    app.processEvents()
    yield v
    v.close()
    v.deleteLater()
    app.processEvents()


def _child(group, cls, name):
    child = group.findChild(cls, name)
    assert child is not None, f"StepBubble 缺少 {name} 子控件"
    return child


def _step_groups(v):
    return [b for b in v.bubbles if b.kind == "steps"]


# ── ① 公式 Unicode 美化 ─────────────────────────────────────────


def test_agent_bubble_renders_latex_as_unicode(view):
    """私教气泡：公式源码不裸露，display/行内两种公式都转 Unicode 排版。"""
    bubble = view.add_agent_message(LATEX_SAMPLE)
    shown = bubble.text()

    assert r"\frac" not in shown, f"LaTeX 源码不应裸露给考生: {shown!r}"
    assert "$$" not in shown, f"公式定界符不应残留: {shown!r}"
    assert "(a / b)" in shown, f"display 公式应转 Unicode 排版: {shown!r}"
    assert "x²" in shown, f"行内上标应转 Unicode: {shown!r}"
    assert shown in view.toPlainText(), "toPlainText 应导出美化后的显示文本"


def test_streaming_chunks_rerender_complete_formula(view):
    """流式半截公式：中间态可短暂为源码，累积完整后最终态必须美化。"""
    view.append_agent_chunk(r"推导：$$\frac")
    view.append_agent_chunk(r"{a}{b}$$ 完毕")
    view.finish_agent_message()

    shown = view.bubbles[-1].text()
    assert "(a / b)" in shown, f"流式收尾后公式应完整美化: {shown!r}"
    assert r"\frac" not in shown


def test_formula_beautify_failure_degrades_to_raw_text(view, monkeypatch):
    """美化器抛异常 / 不可用时降级为原文——绝不因公式渲染吞消息。"""
    import tools.gui.widgets.chat_view as cv

    def _boom(_text):
        raise RuntimeError("beautifier exploded")

    monkeypatch.setattr(cv, "prettify_latex_for_terminal", _boom)
    bubble = view.add_agent_message(LATEX_SAMPLE)
    assert r"\frac" in bubble.text(), "美化异常应降级为原文"

    monkeypatch.setattr(cv, "prettify_latex_for_terminal", None)
    bubble2 = view.add_agent_message(LATEX_SAMPLE)
    assert r"\frac" in bubble2.text(), "美化器不可用应降级为原文"


def test_user_and_system_bubbles_keep_raw_text(view):
    """美化只作用于私教气泡：用户 / 系统气泡原文不动。"""
    user = view.add_user_message(LATEX_SAMPLE)
    assert user.text() == LATEX_SAMPLE

    view.append(LATEX_SAMPLE)
    assert view.bubbles[-1].text() == LATEX_SAMPLE


# ── ② 思考链折叠 ────────────────────────────────────────────────


def test_steps_collapse_into_group_with_live_count(view, app):
    """连续步骤收进同一默认收起的折叠块，标题实时计数、箭头随展开切换。"""
    view.append_step("步骤1")
    view.append_step("步骤2")

    groups = _step_groups(view)
    assert len(groups) == 1, "未封口期间步骤应进同一块"
    group = groups[0]
    toggle = _child(group, QToolButton, "StepToggle")
    body = _child(group, QLabel, "StepBody")

    assert "2 步" in toggle.text(), f"标题应实时计数: {toggle.text()!r}"
    assert "▸" in toggle.text(), "默认收起态箭头应为 ▸"
    assert not toggle.isChecked(), "默认必须收起"
    assert body.isHidden(), "默认收起时步骤正文必须隐藏"
    assert "步骤1" in body.text() and "步骤2" in body.text()

    toggle.setChecked(True)
    app.processEvents()
    assert not body.isHidden(), "展开后正文不得再隐藏"
    assert body.isVisible(), "展开后正文应可见"
    assert "▾" in toggle.text(), "展开态箭头应为 ▾"

    toggle.setChecked(False)
    assert body.isHidden(), "再次点击应收起"


def test_finish_step_group_starts_new_block_and_is_idempotent(view):
    """封口后步骤新起一块；finish 幂等（高频重复调用无副作用）。"""
    view.append_step("A")
    view.finish_step_group()
    view.finish_step_group()
    view.append_step("B")

    groups = _step_groups(view)
    assert len(groups) == 2, "封口后步骤应新起一块"
    assert "1 步" in _child(groups[0], QToolButton, "StepToggle").text()
    assert "1 步" in _child(groups[1], QToolButton, "StepToggle").text()


def test_new_user_message_seals_previous_step_group(view):
    """用户新发言即封口：停止/丢回复场景下新一轮步骤不得续进旧块。"""
    view.append_step("旧轮步骤")
    view.add_user_message("新问题")
    view.append_step("新轮步骤")

    groups = _step_groups(view)
    assert len(groups) == 2
    assert "旧轮步骤" in groups[0].text()
    assert "新轮步骤" in groups[1].text()


def test_steps_do_not_join_streaming_bubble(view):
    """折叠块不参与流式续写：chunk 应新起 agent 气泡而非写进步骤块。"""
    view.append_step("步骤")
    bubble = view.append_agent_chunk("回答")

    assert bubble.kind == "agent"
    assert bubble.streaming
    assert [b.kind for b in view.bubbles] == ["steps", "agent"]


def test_toPlainText_includes_step_bodies_and_bubbles(view):
    """兼容契约：toPlainText 导出全部消息与步骤文本（既有调用点依赖）。"""
    view.add_user_message("问题")
    view.append_step("检索资料")
    view.append_agent_chunk("片段一")
    view.append_agent_chunk("片段二")
    view.finish_agent_message()

    text = view.toPlainText()
    assert "问题" in text
    assert "检索资料" in text, "步骤文本必须仍可导出"
    assert "片段一片段二" in text, "折叠块不得截断流式续写"


# ── ③ clear() ───────────────────────────────────────────────────


def test_clear_removes_everything_and_restores_empty_state(view, app):
    """clear 清空气泡与折叠块、恢复空状态；可反复调用。"""
    view.add_user_message("你好")
    view.append_step("步骤")
    view.append_agent_chunk("回答")
    assert not view.empty_state.isVisible()

    view.clear()
    app.processEvents()
    assert view.bubbles == [], "clear 后不得残留气泡/折叠块"
    assert view.empty_state.isVisible(), "空状态必须恢复显示"
    assert view._open_step_group is None, "clear 应同时封口思考折叠块"

    view.add_user_message("再来")
    view.clear()
    app.processEvents()
    assert view.bubbles == []
    assert view.empty_state.isVisible()


# ── ④ MainWindow 接线 ───────────────────────────────────────────


def test_main_window_wires_steps_and_chunk_boundary(app, tmp_path, monkeypatch):
    """step → 折叠块；chunk 到来即封口；回复收尾再次封口（幂等）。"""
    from tools.gui import services
    from tools.gui.main_window import MainWindow

    monkeypatch.setattr(services, "is_unconfigured",
                        lambda *a, **k: False, raising=False)
    win = MainWindow(workspace_root=tmp_path)
    try:
        win._on_agent_step("① 读取任务")
        win._on_agent_step("② 检索资料")
        groups = _step_groups(win.chat_display)
        assert len(groups) == 1, "连续步骤应进同一折叠块"
        assert "2 步" in _child(groups[0], QToolButton, "StepToggle").text()

        win._on_agent_chunk("答案是 ")
        win._on_agent_step("③ 补充检索")
        groups = _step_groups(win.chat_display)
        assert len(groups) == 2, "答案开始应封口思考块"

        win._on_agent_reply("完整回答")
        assert win.chat_display._open_step_group is None, "收尾应封口"
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()


# ── ⑤ 美化器键前缀回归（\geq→≥q 等吞噬缺陷）─────────────────────


def test_beautifier_long_keys_win_over_prefixes():
    r"""长键必须先于前缀键替换，否则被吞尾（\geq→≥q / \subseteq→⊂eq）。

    这是 TUI / CLI / GUI 三端共享的 ``prettify_latex_for_terminal`` 的回归
    护栏：替换循环已按 key 长度倒序（latex_beautifier.format_math_expr）。
    """
    from tools.skills.latex_beautifier import prettify_latex_for_terminal as pretty

    # 长键：不得残留被吞的尾巴
    assert "≥" in pretty(r"$a \geq b$")
    assert "≥q" not in pretty(r"$a \geq b$")
    assert "≤" in pretty(r"$a \leq b$")
    assert "≤q" not in pretty(r"$a \leq b$")
    assert "≠" in pretty(r"$a \neq b$")
    assert "≠q" not in pretty(r"$a \neq b$")
    assert "⊆" in pretty(r"$A \subseteq B$")
    assert "⊂eq" not in pretty(r"$A \subseteq B$")

    # 短键仍正常（倒序排序不得引入新吞噬）
    assert "≥" in pretty(r"$x \ge 1$")
    assert "≤" in pretty(r"$x \le 1$")
    assert "≠" in pretty(r"$x \ne y$")
    assert "⊂" in pretty(r"$A \subset B$")

    # 易被前缀影响的符号组回归
    assert "∞" in pretty(r"$n \to \infty$")
    assert "∈" in pretty(r"$x \in A$")
    assert "∉" in pretty(r"$x \notin A$")


def test_agent_bubble_renders_geq_without_trailing_q(view):
    r"""GUI 端到端：均值不等式公式里的 \geq 不得显示成 ≥q。"""
    bubble = view.add_agent_message(r"由均值不等式 $$\frac{a+b}{2} \geq \sqrt{ab}$$")
    shown = bubble.text()
    assert "≥" in shown, f"\\geq 应转 ≥: {shown!r}"
    assert "≥q" not in shown, f"\\geq 被 \\ge 吞尾: {shown!r}"
    assert "√(ab)" in shown


# ── ⑥ 美化器命令边界保护（\left→≤ft( 等未映射长命令被啃）──────────


def test_beautifier_command_boundary_protects_unmapped_commands():
    r"""未映射长命令不得被短键啃前缀：\left( → ≤ft(（GUI 放大可见）。

    倒序替换只解决「映射键×映射键」；「映射键×未映射长命令」（\le vs \left）
    必须靠命令边界前瞻 (?![a-zA-Z])（latex_beautifier._replace_commands）。
    """
    from tools.skills.latex_beautifier import prettify_latex_for_terminal as pretty

    out = pretty(r"$\left( \frac{a}{b} \right)$")
    assert "≤ft" not in out, f"\\left 被 \\le 啃前缀: {out!r}"
    assert "(a / b)" in out, f"\\frac 应正常还原: {out!r}"
    assert "right" not in out, f"\\right 应还原为空（括号保留）: {out!r}"

    out2 = pretty(r"$x \leftarrow y$")
    assert "←" in out2, f"\\leftarrow 应补键为 ←: {out2!r}"
    assert "≤ftarrow" not in out2, f"\\leftarrow 被啃: {out2!r}"

    # 未映射命令保真：短键不得出现在这些命令内部（旧实现会啃出 ≤verline 等）
    assert "≤verline" not in pretty(r"$\overline{x}$")
    assert "≤ft" not in pretty(r"$\mathbf{A}$")
    assert "→p" not in pretty(r"$\top$")

    # 边界回归：完整命令（后随非字母）仍正常命中
    for src, want in ((r"$a \leq b$", "≤"), (r"$a \ge b$", "≥"),
                      (r"$n \to \infty$", "∞"), (r"$x \in A$", "∈"),
                      (r"$a_1, \ldots, a_n$", "…")):
        assert want in pretty(src), f"{src} 应含 {want}"


def test_agent_bubble_left_right_structure_renders(view):
    r"""GUI 端到端：\left( \frac \right) 结构不再显示 ≤ft( 乱码。"""
    bubble = view.add_agent_message(
        r"$$\left( \frac{a+b}{2} \right) \geq \sqrt{ab}$$")
    shown = bubble.text()
    assert "≤ft" not in shown, f"\\left 被啃: {shown!r}"
    assert "(a+b / 2)" in shown, f"\\frac 应正常还原: {shown!r}"
    assert "≥" in shown and "√(ab)" in shown
