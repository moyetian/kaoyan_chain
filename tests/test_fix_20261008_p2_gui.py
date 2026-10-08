# -*- coding: utf-8 -*-
"""P2 打磨回归（2026-10-08）：GUI 领域五条

对应六领域深度审查 §3 GUI 六条；其中条目 1（关窗 5s 截止后无 detach 兜底）
经核实为 P1 批 G2 已修复（settings_dialog / onboarding_wizard 的 closeEvent
已含 ``_detach_probe_worker`` 三档兜底），本文件不设用例。其余五条：

  P2-2 设置中心「应用」切主题后对话框自身内联样式必须重刷；设置中心与向导
       的内联样式色值必须全部来自当前主题色板（写死深色 teal/emerald 常量
       在浅色/自定义主题下不跟随 —— 且此前不在「零内联」扫描范围内）
  P2-3 「刷新今日进度」按钮补 SecondaryBtn 对象名（同页签区按钮族一致）
  P2-4 工具动作重入守卫：同名动作未完成时连点不并发起第二个 worker
  P2-5 流式滚动只在视图贴近底部时跟随（向上翻阅不被拽回）
  P2-6 停止/发送按钮随流式状态联动（生成中停止可点 / 发送置灰）

运行（必须带 basetemp）：
  py -X utf8 -m pytest tests/test_fix_20261008_p2_gui.py -q \
      -p no:cacheprovider --basetemp=D:/测试/ky_p2_gui_bt

[隔离] 全部用例在 tmp_path 沙箱构造窗口/对话框；不联网；不写真实工作区
（QSettings 的 L2 五键用后即清）。
[中性化] 校名/专业一律用合成名（示例大学 / 合成专业），不与任何真实考生
身份关联。
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

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 用例")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QMessageBox, QPushButton, QWidget,
)

import tools.gui.main_window as mw_mod  # noqa: E402


# ══════════════════════════════════════════════════════════════
# 夹具与工具
# ══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    app.setStyleSheet("")


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """模态框在离屏测试中一律直通（不阻塞、不改断言）。"""
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda *a, **k: QMessageBox.StandardButton.Ok)


@pytest.fixture()
def win(qt_app, tmp_path, monkeypatch):
    """离屏 MainWindow（workspace_root=tmp_path，绝不碰真实工作区）。

    打桩必须打在 ``mw_mod.services`` —— main_window 绑定的是 ``gui.services``
    （与 ``tools.gui.services`` 不是同一模块对象），打错对象拦不住
    「未配置 → 150ms 后弹引导向导」定时器。
    """
    monkeypatch.setattr(mw_mod.services, "is_unconfigured",
                        lambda *a, **k: False, raising=False)
    window = mw_mod.MainWindow(workspace_root=tmp_path)
    yield window
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def _clean_prefs():
    """清空 QSettings 的 L2 五键（本机残留会污染主题断言）。"""
    from tools.gui import theme_apply

    s = theme_apply._settings()
    for k in ("ui/preset", "ui/acc", "ui/radius", "ui/density", "ui/font_scale"):
        s.setValue(k, "")


def _pump_until(cond, qt_app, ms: int = 3000) -> bool:
    """轮询条件（驱动事件循环）；离屏下布局/滚动条 range 更新是异步的。"""
    import time

    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        qt_app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _settle(qt_app, ms: int = 120) -> None:
    """固定时长驱动事件循环：排空 pending 的 0 定时器与布局请求。"""
    import time

    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


# ── 内联样式色值扫描（P2-2） ────────────────────────────────────

_HEX6_RE = re.compile(r"#([0-9a-fA-F]{6})(?![0-9a-fA-F])")
_RGBA_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")


def _rgbs_in(css: str) -> set:
    """从一段 QSS 里提取全部颜色（hex 与 rgb/rgba 的 rgb 分量）。"""
    out = set()
    for m in _HEX6_RE.finditer(css):
        h = m.group(1)
        out.add((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    for m in _RGBA_RE.finditer(css):
        out.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return out


def _palette_rgbs(theme) -> set:
    """主题色板 = 全部 token 值中出现的颜色（含派生 token 如 acc-hover/glass）。"""
    out = set()
    for value in theme.tokens.values():
        out |= _rgbs_in(str(value))
    return out


def _style_offenders(root: QWidget, palette: set) -> list:
    """收集 root 及全部子控件里「不在色板内」的样式色值。"""
    offenders = []
    for w in [root] + root.findChildren(QWidget):
        css = w.styleSheet()
        if not css:
            continue
        bad = _rgbs_in(css) - palette
        if bad:
            name = f"{type(root).__name__}/{w.objectName() or type(w).__name__}"
            offenders.append((name, sorted(bad), css[:90]))
    return offenders


# ══════════════════════════════════════════════════════════════
# P2-2 · 对话框内联样式与主题
# ══════════════════════════════════════════════════════════════

def test_p2_settings_dialog_restyles_after_theme_apply(win, qt_app):
    """「应用」切主题后，对话框自身内联样式必须跟随新主题重刷。

    修复前：4 处内联样式只在构造时生成 —— 在「界面外观」页切到浅色并点
    「应用」后，主窗口主题已变，对话框按钮/标签仍是旧主题色（widget 内联
    样式优先级高于全局 QSS，不会被新主题压过去）。

    阴性对照：删掉 ``_apply()`` 末尾的 ``self._restyle()``，本用例变红。
    """
    from tools.gui import theme_apply
    from tools.gui.widgets.settings_dialog import SettingsDialog
    from tools.theme import build_theme

    _clean_prefs()
    theme_apply.write_pref(theme_apply.KEY_PRESET, "dark")
    win._theme = build_theme("dark")
    try:
        dlg = SettingsDialog(win)
        try:
            dark_css = dlg.btn_open_console.styleSheet()
            assert "#2dd4bf" in dark_css, f"初始应为深色主色: {dark_css}"

            idx = dlg.preset_combo.findData("light")
            assert idx >= 0, "前置：预设下拉应含 light"
            dlg.preset_combo.setCurrentIndex(idx)
            assert dlg._apply() is True
            qt_app.processEvents()

            light_css = dlg.btn_open_console.styleSheet()
            assert light_css != dark_css, "「应用」切主题后对话框内联样式未重刷"
            assert "#0f766e" in light_css, f"重刷后应为浅色主色: {light_css}"
            assert "#2dd4bf" not in light_css, "旧主题色残留"
            # 向导入口按钮与连通标签同属 _restyle 覆盖范围
            assert "#0f766e" in dlg.wizard_btn.styleSheet()
            assert "#64748b" in dlg.test_lbl.styleSheet(), \
                f"测试标签未随主题重刷: {dlg.test_lbl.styleSheet()}"
        finally:
            dlg.deleteLater()
            qt_app.processEvents()
    finally:
        _clean_prefs()


def test_p2_dialog_inline_colors_follow_theme_palette(win, qt_app, tmp_path, monkeypatch):
    """设置中心与向导的内联样式色值必须全部来自当前主题色板。

    修复前两文件内写死深色主题常量（rgba(45,212,191,…)、#5eead4、#34d399、
    #cbd5e1 等）：浅色主题 / 自定义主色下卡片底色、步骤指示器、状态色不跟随。
    本用例在 **light** 主题下构造两个对话框（写死色是深色系，浅色色板里
    必然找不到），扫描全部子控件 styleSheet 的色值是否 ⊆ light 主题色板。

    阴性对照：把任意一处 ``_rgba(c['acc'], …)`` 改回写死
    ``rgba(45, 212, 191, …)``，本用例变红。
    """
    from tools.gui import theme_apply
    from tools.gui.widgets import onboarding_wizard as ow_mod
    from tools.gui.widgets.settings_dialog import SettingsDialog
    from tools.theme import build_theme

    light = build_theme("light")
    palette = _palette_rgbs(light)
    assert (45, 212, 191) not in palette, \
        "前置：浅色色板不应含深色 teal（判据成立）"

    # 向导取色走 resolve_theme(workspace_root)（非窗口对象），打桩为浅色主题
    monkeypatch.setattr(theme_apply, "resolve_theme", lambda *a, **k: light)

    _clean_prefs()
    win._theme = light
    offenders = []
    try:
        dlg = SettingsDialog(win)
        try:
            # 覆盖连通测试结果态（🟢/🔴 色为运行时设置）
            dlg._on_test_finished({"llm_ok": True, "llm_detail": "合成连通正常"})
            offenders += _style_offenders(dlg, palette)
        finally:
            dlg.deleteLater()
            qt_app.processEvents()

        wiz = ow_mod.OnboardingWizard(workspace_root=tmp_path)
        try:
            # 覆盖「已完成步骤」分支（focus-ring 派生色）
            wiz._current_step = 2
            wiz._update_step_view()
            wiz._on_connectivity_finished({
                "llm_ok": True, "llm_detail": "合成端点可用",
                "search_ok": False, "search_detail": "合成检索超时"})
            offenders += _style_offenders(wiz, palette)
        finally:
            wiz.close()
            wiz.deleteLater()
            qt_app.processEvents()
    finally:
        _clean_prefs()

    assert not offenders, (
        "对话框内联样式含非当前主题色板色值（写死色）：\n"
        + "\n".join(f"  {n}: {bad}  <<  {css}" for n, bad, css in offenders))


# ══════════════════════════════════════════════════════════════
# P2-3 · 「刷新今日进度」按钮样式类
# ══════════════════════════════════════════════════════════════

def test_p2_refresh_today_button_is_secondary(win):
    """「刷新今日进度」按钮必须带 SecondaryBtn 对象名（同按钮族一致）。"""
    btns = [b for b in win.findChildren(QPushButton)
            if b.text() == "刷新今日进度"]
    assert btns, "前置：未找到「刷新今日进度」按钮"
    assert btns[0].objectName() == "SecondaryBtn", \
        f"按钮未走 #SecondaryBtn 主题样式: {btns[0].objectName()!r}"


# ══════════════════════════════════════════════════════════════
# P2-4 · 工具动作重入守卫
# ══════════════════════════════════════════════════════════════

def test_p2_tool_action_reentry_guard(win, qt_app, monkeypatch):
    """同名工具动作未完成时连点 → 不并发起第二个 worker；完成后可再触发。

    修复前 ``_run_action_to_display`` / ``_run_index_action`` 等每次调用都
    新建 IntelTaskWorker：连点卡片会重复联网/重复计费，落盘类动作还会互相
    覆盖。

    阴性对照：删掉 ``_run_action_to_display`` 开头的 ``_tool_action_busy``
    检查，本用例变红（会启动 2 个 worker）。
    """
    import tools.gui.workers.intel_worker as iw_mod

    created = []

    class _SpyIntelWorker(QObject):
        log_signal = Signal(str)
        finished_signal = Signal(str, str)
        error_signal = Signal(str)
        finished = Signal()

        def __init__(self, *a, **k):
            super().__init__()
            created.append(self)

        def start(self):
            pass

    monkeypatch.setattr(iw_mod, "IntelTaskWorker", _SpyIntelWorker)

    # 1) 卡片动作（watch 走 _run_action_to_display）
    win._run_action_to_display("watch", win.intel_display)
    assert len(created) == 1, "首次触发未创建 worker"
    win._run_action_to_display("watch", win.intel_display)
    assert len(created) == 1, "连点并发起了第二个 worker（无重入守卫）"
    assert "仍在执行中" in win.chat_display.toPlainText(), "缺少「正在执行」提示"

    # 线程结束（finished）→ 出集 → 可再次触发
    created[0].finished.emit()
    win._run_action_to_display("watch", win.intel_display)
    assert len(created) == 2, "线程结束后守卫未释放（死锁）"

    # 2) 无对话框动作（建索引）
    before = len(created)
    win._run_index_action()
    win._run_index_action()
    assert len(created) == before + 1, "建索引未受重入守卫保护"

    # 清理：把在跑 worker 全部出集（避免影响同文件其它用例）
    for w in list(created):
        w.finished.emit()


# ══════════════════════════════════════════════════════════════
# P2-5 · 流式滚动只在底部附近跟随
# ══════════════════════════════════════════════════════════════

def test_p2_chat_scroll_follows_only_near_bottom(qt_app):
    """向上翻阅时流式片段不拽回底部；贴底时仍自动跟随。

    修复前 ``ChatView._scroll_to_bottom`` 无条件 ``setValue(maximum())``：
    长回答流式续写会把正在翻阅历史的视图不断拽回。

    阴性对照：删掉 ``_scroll_to_bottom`` 里的
    ``bar.maximum() - bar.value() > threshold`` 早退分支，本用例第一条断言
    变红。
    """
    from tools.gui.widgets.chat_view import ChatView

    view = ChatView()
    try:
        view.resize(420, 260)
        view.show()
        qt_app.processEvents()

        for _ in range(25):
            view.add_bubble("合成内容段落" * 30, kind="system")
        bar = view.verticalScrollBar()
        assert _pump_until(lambda: bar.maximum() > 0, qt_app), "前置：内容应可滚动"
        _settle(qt_app)  # 排空填充阶段排队的跟随滚动任务（0 定时器）

        # 建立确定性基线：置于底部
        bar.setValue(bar.maximum())

        # 用户向上翻阅 → 流式片段不得拽回
        # （阴性对照：删掉 _scroll_to_bottom 的早退分支，本断言变红）
        bar.setValue(0)
        view.append_agent_chunk("新的流式片段")
        _settle(qt_app, 150)
        assert bar.value() == 0, "向上翻阅时被流式内容拽回底部"

        # 回到贴底 → 恢复自动跟随
        bar.setValue(bar.maximum())
        view.append_agent_chunk("又一段流式片段")
        assert _pump_until(lambda: bar.value() == bar.maximum(), qt_app, ms=800), (
            "贴底时未自动跟随新内容"
        )
    finally:
        view.close()
        view.deleteLater()
        qt_app.processEvents()


# ══════════════════════════════════════════════════════════════
# P2-6 · 停止/发送按钮状态联动
# ══════════════════════════════════════════════════════════════

def test_p2_send_stop_buttons_track_streaming(win, qt_app, monkeypatch):
    """按钮随流式状态联动：空闲（停止灰/发送亮）→ 生成中（停止亮/发送灰）
    → 停止或自然收尾后复原。

    修复前两按钮状态恒定：空闲时「停止」可点（点了才提示）、生成中「发送」
    可点（点了才被守卫拒绝）。

    阴性对照：删掉 ``_on_send_message`` 里的 ``_set_agent_ui_running(True)``，
    本用例生成中断言变红。
    """
    import tools.gui.workers.agent_worker as aw_mod

    created = []

    class _SpyAgentWorker(QObject):
        chunk_signal = Signal(str)
        step_signal = Signal(str)
        finished_signal = Signal(str)
        finished = Signal()
        session_ran_signal = Signal()

        def __init__(self, config, user_input, timeout=None,
                     workspace_root=None, session_allowed_tools=None):
            super().__init__()
            self.user_input = user_input
            created.append(self)

        def start(self):
            pass

        def isRunning(self):  # noqa: N802 - Qt 命名
            return True

        def cancel(self):
            pass

    monkeypatch.setattr(aw_mod, "AgentWorker", _SpyAgentWorker)

    # 初始：空闲态
    assert win.stop_btn.isEnabled() is False, "空闲时「停止」应置灰"
    assert win.send_btn.isEnabled() is True

    # 发送 → 生成中
    win.input_box.setPlainText("合成问题：请解释合成概念")
    win._on_send_message()
    qt_app.processEvents()
    assert len(created) == 1
    assert win.stop_btn.isEnabled() is True, "生成中「停止」应可点"
    assert win.send_btn.isEnabled() is False, "生成中「发送」应置灰"

    # 点停止 → 立即恢复可发送
    win._on_stop_agent()
    assert win.stop_btn.isEnabled() is False, "停止后「停止」应复位置灰"
    assert win.send_btn.isEnabled() is True, "停止后「发送」应恢复"

    # 再发一条 → 线程自然结束（finished）→ 复位
    win.input_box.setPlainText("合成追问：再补充一点")
    win._on_send_message()
    assert win.stop_btn.isEnabled() is True
    created[-1].finished.emit()
    qt_app.processEvents()
    assert win.stop_btn.isEnabled() is False, "收尾后「停止」未复位"
    assert win.send_btn.isEnabled() is True, "收尾后「发送」未恢复"
