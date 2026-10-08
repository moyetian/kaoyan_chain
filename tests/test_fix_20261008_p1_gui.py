# -*- coding: utf-8 -*-
"""P1 修复回归（2026-10-08）：GUI UI/UX 六条 + K7 /diff 命名与落盘

覆盖缺陷与回归点：
  G1 错题盲盒卷面不可见 → 生成后卷面必须同步上屏聊天区（error_info 契约保留、
     仍默认折叠）
  G2 探测线程零收尾 → 设置中心/向导 closeEvent 停线程 + 强引用池出池 + 重入
     保护（对照 wechat_search_dialog 三档兜底）
  G3 向导占位文本回填 → 空配置不回填占位文本；校验拦截 school/major/pro 占位
     值（与 is_unconfigured 同名单，防「建档成功」却永远未配置）
  G4 单行输入框 → ChatInput（QPlainTextEdit）Enter 发送 / Shift+Enter 换行、
     1~6 行自适应
  G5 错误文案 → humanize_error 中文映射 + 去双重 [×] 前缀 + GUI 内恢复指引
  K7 /diff → REPL 与 GUI 接 infer_diff_naming（公共课考纲不再张冠李戴成志愿
     名）；REPL 落盘改显式 --save

运行（必须带 basetemp）：
  py -X utf8 -m pytest tests/test_fix_20261008_p1_gui.py -q \
      -p no:cacheprovider --basetemp=C:/tmp/pbt_p1_g

[隔离] 全部用例在 tmp_path 沙箱构造窗口/向导；不联网；不写真实工作区。
[中性化] 校名/专业一律用合成名（示例大学 / 甲合成大学 / 合成专业），
不与任何真实考生身份关联。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 用例")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

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
    """校验警告等模态框在离屏测试中一律直通（不阻塞、不改断言）。"""
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


def _wait_until(cond, qt_app, ms: int = 3000) -> bool:
    """轮询条件（驱动事件循环），超时返回最后一次结果。"""
    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        qt_app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class _InterruptibleWorker(QThread):
    """响应 requestInterruption 的替身线程（证明收尾是「真停」而非躺平）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entered = False

    def run(self):
        self.entered = True
        while not self.isInterruptionRequested():
            time.sleep(0.01)


class _FakeSettingsWin:
    """SettingsDialog 需要的最小宿主（workspace_root 指向沙箱）。"""

    class _Theme:
        name = "classic_dark"

        def color(self, key):
            return None

        def is_dark(self):
            return True

    _theme = _Theme()

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def _sync_theme_button(self):
        pass

    def _refresh_card_icons(self):
        pass

    def _load_config(self):
        pass

    def _refresh_all(self):
        pass


# ══════════════════════════════════════════════════════════════
# G1 · 错题盲盒卷面可见
# ══════════════════════════════════════════════════════════════

def test_g1_error_quiz_paper_lands_in_chat(win, qt_app, monkeypatch):
    """组卷完成后：卷面必须出现在聊天区；error_info 契约保留且仍默认折叠。"""
    import tools.gui.workers.intel_worker as iw_mod

    holders = {}

    class _SpyIntelWorker(QObject):
        log_signal = Signal(str)
        finished_signal = Signal(str, str)
        error_signal = Signal(str)
        finished = Signal()

        def __init__(self, *a, **k):
            super().__init__()
            holders["w"] = self

        def start(self):
            pass

    monkeypatch.setattr(iw_mod, "IntelTaskWorker", _SpyIntelWorker)
    monkeypatch.setattr(mw_mod.QInputDialog, "getItem",
                        lambda *a, **k: ("专业课", True))

    win._generate_error_quiz()
    assert "w" in holders, "前置：组卷 worker 未创建"

    paper = ("【错题盲盒自测卷】已生成！\n"
             "1. 合成题干甲 [2分]\n2. 合成题干乙 [3分]\n"
             "> 自测卷已落盘: C:/tmp/fake/quiz.md")
    holders["w"].finished_signal.emit(paper, "C:/tmp/fake/quiz.md")
    qt_app.processEvents()

    assert "合成题干甲" in win.chat_display.toPlainText(), \
        "卷面未上屏聊天区（考生看不到题目）"
    assert "合成题干甲" in win.error_info.toPlainText(), \
        "error_info 既有契约被破坏（_generate_error_quiz 仍应写入）"
    assert win.error_info.isHidden(), "原始档案应仍默认折叠"


# ══════════════════════════════════════════════════════════════
# G2 · 探测线程收尾
# ══════════════════════════════════════════════════════════════

def test_g2_settings_dialog_close_stops_probe_worker(qt_app, tmp_path, monkeypatch):
    """设置中心：在跑的探查线程 → close() 后线程已停 + 强引用池已清。"""
    from tools.gui.widgets import settings_dialog as sd_mod
    from tools.gui.widgets import onboarding_wizard as ow_mod
    from tools.gui.widgets.settings_dialog import SettingsDialog

    class _StubProbeWorker(QThread):
        finished_signal = Signal(dict)

        def __init__(self, api_key, base_url, parent=None):
            super().__init__(parent)
            self.entered = False

        def run(self):
            self.entered = True
            while not self.isInterruptionRequested():
                time.sleep(0.01)

    monkeypatch.setattr(ow_mod, "ProbeModelsWorker", _StubProbeWorker)

    dlg = SettingsDialog(_FakeSettingsWin(tmp_path))
    try:
        dlg.api_key_edit.setText("sk-test")
        dlg._probe_models()
        w = dlg._probe_worker
        assert isinstance(w, _StubProbeWorker)
        assert w in sd_mod._ACTIVE_WORKERS, "探查线程未登记进强引用池"
        assert _wait_until(lambda: w.entered, qt_app)

        # 重入保护：在跑线程不得被新实例覆盖
        dlg._probe_models()
        assert dlg._probe_worker is w, "重入覆盖了运行中的 QThread"

        dlg.close()
        assert _wait_until(lambda: not w.isRunning(), qt_app), \
            "closeEvent 后探查线程仍在运行"
        assert w not in sd_mod._ACTIVE_WORKERS, "closeEvent 后线程未出池"
        w.wait(1000)
    finally:
        dlg.deleteLater()
        qt_app.processEvents()


def test_g2_wizard_close_stops_conn_worker(qt_app, tmp_path, monkeypatch):
    """向导：在跑的连通性自检线程 → close() 后线程已停 + 强引用池已清。"""
    from tools.gui.widgets import onboarding_wizard as ow_mod

    class _StubConnWorker(QThread):
        finished_signal = Signal(dict)

        def __init__(self, api_key, base_url, model, search_provider, parent=None):
            super().__init__(parent)
            self.entered = False

        def run(self):
            self.entered = True
            while not self.isInterruptionRequested():
                time.sleep(0.01)

    monkeypatch.setattr(ow_mod, "ConnectivityWorker", _StubConnWorker)

    wiz = ow_mod.OnboardingWizard(workspace_root=tmp_path)
    try:
        wiz.api_key_edit.setText("sk-test")
        wiz._run_connectivity_test()
        w = wiz._worker
        assert isinstance(w, _StubConnWorker)
        assert w in ow_mod._ACTIVE_WORKERS, "自检线程未登记进强引用池"
        assert _wait_until(lambda: w.entered, qt_app)

        wiz.close()
        assert _wait_until(lambda: not w.isRunning(), qt_app), \
            "closeEvent 后自检线程仍在运行"
        assert w not in ow_mod._ACTIVE_WORKERS, "closeEvent 后线程未出池"
        w.wait(1000)
    finally:
        wiz.deleteLater()
        qt_app.processEvents()


def test_g2_wizard_probe_reentry_guard(qt_app, tmp_path):
    """向导探查：在跑线程不被重入覆盖。"""
    from tools.gui.widgets import onboarding_wizard as ow_mod

    wiz = ow_mod.OnboardingWizard(workspace_root=tmp_path)
    try:
        w = _InterruptibleWorker()
        wiz._probe_worker = w
        w.start()
        assert _wait_until(lambda: w.entered, qt_app)
        wiz.api_key_edit.setText("sk-test")
        wiz._probe_upstream_models()
        assert wiz._probe_worker is w, "重入覆盖了运行中的 QThread"
        wiz.close()
        assert _wait_until(lambda: not w.isRunning(), qt_app)
        w.wait(1000)
    finally:
        wiz.deleteLater()
        qt_app.processEvents()


# ══════════════════════════════════════════════════════════════
# G3 · 向导占位文本
# ══════════════════════════════════════════════════════════════

def test_g3_no_placeholder_prefill_and_blocking(qt_app, tmp_path):
    """空配置：不回填占位文本；占位值被校验拦截；真实值正常放行。"""
    from tools.gui.widgets.onboarding_wizard import OnboardingWizard

    wiz = OnboardingWizard(workspace_root=tmp_path)
    try:
        assert wiz.school_edit.text() == "", "空配置不应回填「目标院校」占位文本"
        assert wiz.major_edit.text() == ""
        assert wiz.school_edit.placeholderText(), "示例应保留在 placeholder 中"

        # 占位值拦截（school）
        wiz.school_edit.setText("目标院校")
        wiz.major_edit.setText("030100 合成专业")
        assert wiz._validate_current_step() is False, "「目标院校」占位值未被拦截"

        # 占位值拦截（major）
        wiz.school_edit.setText("示例大学")
        wiz.major_edit.setText("目标专业 (专业代码)")
        assert wiz._validate_current_step() is False, "占位专业未被拦截"

        # 真实值放行
        wiz.major_edit.setText("030100 合成专业")
        assert wiz._validate_current_step() is True

        # step 1：专业课占位文本拦截
        wiz._current_step = 1
        wiz.pro_name_edit.setText("自命题专业课科目")
        assert wiz._validate_current_step() is False, "专业课占位文本未被拦截"
        wiz.pro_name_edit.setText("610 合成专业课一")
        assert wiz._validate_current_step() is True
    finally:
        wiz.close()
        wiz.deleteLater()
        qt_app.processEvents()


def test_g3_existing_config_placeholder_not_refilled(qt_app, tmp_path):
    """已有配置里若是占位符（历史脏数据）→ 视作未填；真实值正常回填。"""
    from tools.gui.widgets.onboarding_wizard import OnboardingWizard

    dirty = tmp_path / "ky_config.json"
    dirty.write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "目标院校", "major": "目标专业 (专业代码)",
                       "pro_name": "自命题专业课科目"},
    }, ensure_ascii=False), encoding="utf-8")
    wiz = OnboardingWizard(workspace_root=tmp_path)
    try:
        assert wiz.school_edit.text() == "", "占位校名被回填"
        assert wiz.major_edit.text() == "", "占位专业被回填"
        assert wiz.pro_name_edit.text() == "", "占位专业课名被回填"
    finally:
        wiz.close()
        wiz.deleteLater()
        qt_app.processEvents()

    real = tmp_path / "ky_config.json"
    real.write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "示例大学", "major": "030100 合成专业",
                       "pro_name": "610 合成专业课一"},
    }, ensure_ascii=False), encoding="utf-8")
    wiz2 = OnboardingWizard(workspace_root=tmp_path)
    try:
        assert wiz2.school_edit.text() == "示例大学"
        assert wiz2.major_edit.text() == "030100 合成专业"
        assert wiz2.pro_name_edit.text() == "610 合成专业课一"
    finally:
        wiz2.close()
        wiz2.deleteLater()
        qt_app.processEvents()


# ══════════════════════════════════════════════════════════════
# G4 · 多行输入框
# ══════════════════════════════════════════════════════════════

def test_g4_chat_input_enter_sends_shift_enter_newline(qt_app):
    """ChatInput：Enter 发送（不换行）、Shift+Enter 换行（不发送）、高度自适应。"""
    from tools.gui.views.chat_tab import ChatInput

    box = ChatInput()
    try:
        sent = []
        box.send_requested.connect(lambda: sent.append(1))

        box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                    Qt.KeyboardModifier.NoModifier))
        assert sent == [1], "Enter 未触发发送"
        assert box.toPlainText() == "", "Enter 不应插入换行"

        box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                    Qt.KeyboardModifier.ShiftModifier))
        assert sent == [1], "Shift+Enter 不应触发发送"
        assert "\n" in box.toPlainText(), "Shift+Enter 应插入换行"

        h1 = box.height()
        box.setPlainText("\n".join(f"第{i}行" for i in range(3)))
        qt_app.processEvents()
        assert box.height() > h1, "3 行内容高度应大于单行"

        box.setPlainText("\n".join(f"第{i}行" for i in range(30)))
        qt_app.processEvents()
        line_h = max(1, box.fontMetrics().lineSpacing())
        assert box.height() <= 6 * line_h + 20, "高度应封顶在 6 行附近"
    finally:
        box.deleteLater()
        qt_app.processEvents()


def test_g4_multiline_answer_sent_intact(win, qt_app, monkeypatch):
    """多行作答（含换行）经 Enter 发送后，完整文本（不截断）交给 AgentWorker。"""
    import tools.gui.workers.agent_worker as aw_mod

    # [P1 修复·2026-10-08] 经 main_window 绑定的 views 命名空间取类：main_window
    # 优先绑定 ``gui.*`` 模块族，直接 ``from tools.gui.views...`` 拿到的是另一个
    # 模块对象（双导入），isinstance 必假。
    ChatInput = mw_mod.views.chat_tab.ChatInput

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
            return False

        def cancel(self):
            pass

    monkeypatch.setattr(aw_mod, "AgentWorker", _SpyAgentWorker)

    assert isinstance(win.input_box, ChatInput), "输入框应为多行 ChatInput"
    assert not hasattr(win.input_box, "returnPressed"), "不应再是 QLineEdit 语义"

    answer = "第一段：合成论述开头\n第二段：合成论述展开\n第三段：合成结论"
    win.input_box.setPlainText(answer)
    win.input_box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                          Qt.KeyboardModifier.NoModifier))
    qt_app.processEvents()

    assert len(created) == 1, "Enter 未触发发送"
    assert created[0].user_input == answer, "多行文本被截断或改写"
    assert answer in win.chat_display.toPlainText(), "用户消息未上屏"


# ══════════════════════════════════════════════════════════════
# G5 · 错误文案
# ══════════════════════════════════════════════════════════════

def test_g5_humanize_error_mapping():
    """humanize_error：去双层前缀、英文异常中文化、中文报错不改写。"""
    from tools.gui.services.actions import humanize_error

    m = humanize_error("[×] 模块 [scout] 执行异常: ConnectionError: boom")
    assert m.count("[×]") == 1, f"双层 [×] 前缀: {m}"
    assert "ConnectionError" not in m, "英文异常原文直出"
    assert "网络连接失败" in m

    m2 = humanize_error(FileNotFoundError("[Errno 2] No such file or directory: 'a.md'"))
    assert "文件不存在" in m2 and "重新选择文件" in m2 and "No such file" not in m2

    m3 = humanize_error(TimeoutError("timed out"))
    assert "超时" in m3 and "设置中心" in m3

    # 中文报错原样保留（兼容既有桩值「网络不可用」）
    assert humanize_error("[×] 组卷异常: 网络不可用") == "[×] 组卷异常: 网络不可用"


def test_g5_append_error_single_prefix_and_guidance(win):
    """_append_error：单 [×] 前缀 + 中文映射 + GUI 内恢复路径。"""

    class _Disp:
        def __init__(self):
            self.lines = []

        def append(self, text):
            self.lines.append(text)

    d = _Disp()
    win._append_error(d, "本地检索异常", "[×] 本地检索异常: ConnectionError: conn boom")
    line = "".join(d.lines)
    assert line.count("[×]") == 1, f"双层前缀: {line.strip()}"
    assert "网络连接失败" in line and "设置中心" in line

    d2 = _Disp()
    win._append_error(d2, "组卷异常", TimeoutError("read timed out"))
    line2 = "".join(d2.lines)
    assert line2.count("[×]") == 1 and "组卷异常" in line2 and "超时" in line2


def test_g5_action_capture_humanizes_exception(monkeypatch):
    """actions.run_action_capture：后端异常不再原文直出。"""
    import importlib

    def _boom(*a, **k):
        raise ConnectionError("upstream boom")

    patched = 0
    for name in ("tui_navigator", "tools.tui_navigator"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        monkeypatch.setattr(mod, "execute_action", _boom, raising=True)
        patched += 1
    assert patched >= 1, "tui_navigator 未能定位，测试前置条件不成立"

    from tools.gui.services.actions import run_action_capture
    out = run_action_capture("scout")
    assert out.startswith("[×]"), out
    assert "ConnectionError" not in out, f"英文异常原文直出: {out}"
    assert "网络连接失败" in out


# ══════════════════════════════════════════════════════════════
# K7 · /diff 命名与落盘
# ══════════════════════════════════════════════════════════════

class _FakeDiffGen:
    def __init__(self):
        self.compares = []
        self.saves = 0

    def _rep(self, kw):
        return {"metrics": {"volatility_percentage": 0.0, "stability_grade": "稳定",
                            "added_count": 0, "removed_count": 0,
                            "modified_count": 0, "unchanged_count": 1,
                            "total_old": 1, "total_new": 1},
                "diff_items": [], "school": kw.get("school"),
                "major": kw.get("major"), "year_new": kw.get("year_new")}

    def compare_texts(self, **kw):
        self.compares.append((kw.get("school"), kw.get("major")))
        return self._rep(kw)

    def compare_files(self, **kw):
        self.compares.append((kw.get("school"), kw.get("major")))
        return self._rep(kw)

    def save_diff_report(self, rep):
        self.saves += 1
        return "fake/考纲变动分析.md"


def _write_english_syllabi(tmp_path: Path):
    (tmp_path / "02-英语" / "参考资料").mkdir(parents=True, exist_ok=True)
    old = tmp_path / "02-英语" / "考试大纲.md"
    new = tmp_path / "02-英语" / "参考资料" / "2027考试大纲.md"
    old.write_text("一、语法：掌握时态\n", encoding="utf-8")
    new.write_text("一、语法：掌握时态与语态\n二、阅读：理解主旨\n", encoding="utf-8")
    return old, new


def test_k7_repl_diff_naming_follows_paths(tmp_path, monkeypatch):
    """REPL /diff：英语考纲命名「全国统考/英语」，不再张冠李戴成 config 志愿。"""
    from types import SimpleNamespace
    from tools.cli.commands import intel as intel_mod

    old, new = _write_english_syllabi(tmp_path)
    gen = _FakeDiffGen()
    fake_intel = SimpleNamespace(get_syllabus_diff_generator=lambda: gen)
    monkeypatch.setattr(intel_mod, "_get_intelligence_module", lambda: fake_intel)
    monkeypatch.setattr(intel_mod, "load_config",
                        lambda: {"study_plan": {"school": "甲合成大学",
                                                "major": "合成马理论"}})

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = intel_mod.run_syllabus_diff(f"--old={old} --new={new}")
    assert ok is True
    assert gen.compares[-1] == ("全国统考", "英语"), \
        f"命名未随实（张冠李戴）: {gen.compares[-1]}"
    out = buf.getvalue()
    assert "甲合成大学" not in out, "播报仍用 config 志愿名"
    assert gen.saves == 0, "未加 --save 却落盘了"
    assert "未指定 --save" in out, "缺少「仅预览」提示"


def test_k7_repl_diff_save_requires_flag(tmp_path, monkeypatch):
    """REPL /diff：显式 --save 才落盘；专业课路径回落 config（阴性对照）。"""
    from types import SimpleNamespace
    from tools.cli.commands import intel as intel_mod

    old, new = _write_english_syllabi(tmp_path)
    gen = _FakeDiffGen()
    fake_intel = SimpleNamespace(get_syllabus_diff_generator=lambda: gen)
    monkeypatch.setattr(intel_mod, "_get_intelligence_module", lambda: fake_intel)
    monkeypatch.setattr(intel_mod, "load_config",
                        lambda: {"study_plan": {"school": "甲合成大学",
                                                "major": "合成马理论"}})

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        intel_mod.run_syllabus_diff(f"--old={old} --new={new} --save")
    assert gen.saves == 1, "--save 时未落盘（或多次落盘）"
    assert "归档至" in buf.getvalue()

    # 阴性对照：专业课路径推导不出 → 回落 config 志愿（推导不越界）
    (tmp_path / "04-专业课").mkdir(parents=True, exist_ok=True)
    pro_old = tmp_path / "04-专业课" / "考试大纲.md"
    pro_new = tmp_path / "04-专业课" / "2027大纲.md"
    pro_old.write_text("考点甲\n", encoding="utf-8")
    pro_new.write_text("考点甲 考点乙\n", encoding="utf-8")
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        intel_mod.run_syllabus_diff(f"--old={pro_old} --new={pro_new}")
    assert gen.compares[-1] == ("甲合成大学", "合成马理论"), \
        f"专业课路径不应被公共课命名覆盖: {gen.compares[-1]}"


def test_k7_gui_diff_naming_follows_paths(tmp_path, monkeypatch):
    """GUI 考纲 Diff：同样接 infer_diff_naming（英语考纲 → 全国统考/英语）。"""
    import importlib
    from tools.gui.services import actions as gui_actions

    old, new = _write_english_syllabi(tmp_path)
    gen = _FakeDiffGen()

    patched = 0
    for name in ("tools.intelligence.syllabus_diff", "intelligence.syllabus_diff"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        monkeypatch.setattr(mod, "get_syllabus_diff_generator", lambda: gen)
        patched += 1
    assert patched >= 1, "syllabus_diff 模块未能定位，测试前置条件不成立"

    text = gui_actions.diff_syllabus(tmp_path, str(old), str(new))
    assert gen.compares and gen.compares[-1] == ("全国统考", "英语"), \
        f"GUI 命名未随实: {gen.compares}"
    assert "动荡率" in text, f"未返回可比对结果: {text[:120]}"
