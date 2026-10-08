# -*- coding: utf-8 -*-
"""[P1 修复·2026-10-08] TUI/CLI 组回归测试：T1 / T2 / T3 三条缺陷。

* T1  REPL 无 Tab 补全 / 无命令历史 / 无多行输入（``HISTORY_FILE`` 定义后零读写）；
* T2  生成中 Ctrl+C 语义分裂：空闲时优雅退出、生成中裸 traceback 退出（丢会话
      上下文且跳过 session_end 收尾）；
* T3  纯文本终端中枢菜单 [11]/[12] 溢出面板宽度（实测整行 86/90 列 vs 面板 84，
      右边框被推出面板之外）。

全部用例只读 / 打桩：不联网、不写工作区；历史文件写入被重定向到 ``tmp_path``
（``tests/conftest.py`` 对真实 ``ky_history.json`` 有重定向 + tripwire 双防线）。
readline 在 Windows 上不存在，测试不假设其可用（一律注入 fake 或走缺失降级）。
[夹具中性化] 本文件不出现任何真实院校 / 专业 / 考生信息。
"""
from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.cli.repl import loop as repl_loop  # noqa: E402

try:  # 双导入兼容（与 tests/test_tui.py 同模式）
    from tools import tui_navigator
except ImportError:  # pragma: no cover
    import tui_navigator  # type: ignore


# ════════════════════════════════════════════════════════════════
# 公共工具
# ════════════════════════════════════════════════════════════════

_BASE_CFG = {
    "api_key": "",
    "model": "deepseek-chat",
    "active_subject": "pro",
    "onboarding_completed": True,
    "study_plan": {},
}


class _FakeErrorLogger:
    """确定性错题本桩：默认全部空转。"""

    def get_due_reviews(self, *a, **k):
        return []

    def generate_blind_quiz(self, *a, **k):
        return ""

    def log_error_record(self, **kw):
        return ""

    def __getattr__(self, name):
        return lambda *a, **k: None


class _FakeReadline:
    """记录型 readline 桩：与 GNU readline 的最小 API 面一致。"""

    def __init__(self):
        self.completer = None
        self.bindings = []
        self.history_length = None
        self.read_calls = []
        self.write_calls = []
        self.history = []

    def set_completer(self, fn):
        self.completer = fn

    def parse_and_bind(self, s):
        self.bindings.append(s)

    def set_history_length(self, n):
        self.history_length = n

    def read_history_file(self, p):
        self.read_calls.append(str(p))
        if not Path(p).exists():
            raise FileNotFoundError(p)  # 与真实 readline 首运行行为一致
        self.history = Path(p).read_text(encoding="utf-8").splitlines()

    def write_history_file(self, p):
        self.write_calls.append(str(p))
        Path(p).write_text("\n".join(self.history) + "\n", encoding="utf-8")


def _drive_repl(monkeypatch, inputs, cfg=None, stream=None, observe=None,
                input_factory=None):
    """在打桩环境下驱动一次 run_repl，返回观测 state。

    基础桩保证：不读真实配置、不启端口、不进真实 Agent、不写工作区、不联网；
    ``_readline`` 固定为 None（走缺失降级路径，保证跨平台确定性）。
    """
    state = {"llm_calls": []}
    _cfg = dict(_BASE_CFG)
    if cfg:
        _cfg.update(cfg)

    monkeypatch.setattr(repl_loop, "load_config", lambda: dict(_cfg))
    monkeypatch.setattr(repl_loop, "save_config", lambda c: None)
    monkeypatch.setattr(repl_loop, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "start_background_live_server", lambda *a, **k: 8088)
    monkeypatch.setattr(repl_loop, "AgentRunner", None)
    monkeypatch.setattr(repl_loop, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(repl_loop, "_readline", None)
    monkeypatch.setattr(repl_loop, "append_live_message", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "print_followup_toolbar", lambda: None)
    monkeypatch.setattr(repl_loop, "build_system_prompt", lambda s: "SYS")
    monkeypatch.setattr(repl_loop, "infer_subject_from_text", lambda t, cur: cur)
    monkeypatch.setattr(repl_loop, "_count_error_records", lambda s: 0)
    monkeypatch.setattr(repl_loop, "_warn_if_mistake_not_archived", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "format_subject_hint", lambda a, b: "")
    monkeypatch.setattr(repl_loop, "error_logger", _FakeErrorLogger())
    if stream is None:
        stream = lambda *a, **k: (state["llm_calls"].append(1), "SENTINEL_LLM")[1]
    monkeypatch.setattr(repl_loop, "stream_chat", stream)

    if observe:
        observe(monkeypatch, state)

    if input_factory is None:
        queue = list(inputs)

        def _fake_input(prompt=""):
            if queue:
                return queue.pop(0)
            raise EOFError

        input_factory = _fake_input
    monkeypatch.setattr(builtins, "input", input_factory)

    repl_loop.run_repl(permission_mode="ask")
    return state


# ════════════════════════════════════════════════════════════════
# T1 · Tab 补全 / 命令历史 / 多行续行
# ════════════════════════════════════════════════════════════════

def test_t1_missing_readline_degrades_silently(monkeypatch, tmp_path):
    """readline 缺失（Windows 默认）时 init/persist 静默跳过，绝不崩、不落盘。"""
    hist = tmp_path / "ky_history.json"
    monkeypatch.setattr(repl_loop, "HISTORY_FILE", hist)
    monkeypatch.setattr(repl_loop, "_readline", None)
    monkeypatch.setattr(repl_loop, "_stdin_is_tty", lambda: True)

    repl_loop._init_repl_readline(_BASE_CFG)   # 不应抛
    repl_loop._persist_repl_history()          # 不应抛

    assert not hist.exists(), "readline 缺失时不应写历史文件"


def test_t1_run_repl_full_pass_without_readline(monkeypatch, capsys):
    """集成：readline 缺失下完整驱动一次 REPL（对话 + /exit），全程不崩。"""
    state = _drive_repl(monkeypatch, ["正常提问", "/exit"])
    out = capsys.readouterr().out

    assert state["llm_calls"] == [1], "前置条件：正常提问未走到对话链路"
    assert "再见" in out, "readline 缺失导致 REPL 退出流程异常"


def test_t1_history_read_write_and_candidate_dedup(monkeypatch, tmp_path):
    """历史文件读写（init 读 / persist 写）+ 补全候选从指令大盘派生且去重。"""
    fake = _FakeReadline()
    hist = tmp_path / "ky_history.json"
    monkeypatch.setattr(repl_loop, "_readline", fake)
    monkeypatch.setattr(repl_loop, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(repl_loop, "HISTORY_FILE", hist)
    monkeypatch.setattr(repl_loop, "_REPL_COMPLETION_CACHE", [])

    repl_loop._init_repl_readline(_BASE_CFG)
    assert fake.read_calls == [str(hist)], "init 未读取历史文件"
    assert fake.history_length == 1000, "未设置历史上限（防文件无限膨胀）"
    assert "tab: complete" in fake.bindings, "未注册 Tab 补全绑定"
    assert fake.completer is not None, "未注册补全器"

    cands = repl_loop._REPL_COMPLETION_CACHE
    assert len(cands) == len(set(cands)), "补全候选存在重复"
    assert len(cands) > 30, f"补全候选过少（大盘派生失败）：{len(cands)}"
    assert "/today" in cands and "/admission" in cands, "斜杠主命令未派生"
    assert "查漏" in cands, "中文原生口令未纳入候选"

    fake.history = ["/today", "数学报到"]
    repl_loop._persist_repl_history()
    assert fake.write_calls == [str(hist)], "persist 未写入 HISTORY_FILE"
    assert hist.exists(), "历史文件未落盘"
    assert "/today" in hist.read_text(encoding="utf-8")


def test_t1_non_tty_never_touches_history_file(monkeypatch, tmp_path):
    """非 TTY（管道/自动化）：不读也不写 —— 防用空历史覆盖用户既有文件。"""
    fake = _FakeReadline()
    hist = tmp_path / "ky_history.json"
    monkeypatch.setattr(repl_loop, "_readline", fake)
    monkeypatch.setattr(repl_loop, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(repl_loop, "HISTORY_FILE", hist)

    repl_loop._init_repl_readline(_BASE_CFG)
    repl_loop._persist_repl_history()

    assert fake.read_calls == [] and fake.write_calls == []
    assert not hist.exists()


def test_t1_completer_prefix_matching(monkeypatch):
    """补全器：前缀命中按 state 递增；无命中返回 None（readline 约定）。"""
    monkeypatch.setattr(repl_loop, "_REPL_COMPLETION_CACHE",
                        ["/today", "/to2", "查漏"])
    assert repl_loop._repl_completer("/to", 0) == "/today"
    assert repl_loop._repl_completer("/to", 1) == "/to2"
    assert repl_loop._repl_completer("/to", 2) is None
    assert repl_loop._repl_completer("/zzz", 0) is None
    assert repl_loop._repl_completer("查", 0) == "查漏"


def test_t1_multiline_continuation_joins_lines(monkeypatch):
    """行尾反斜杠续行：多行拼接为一条指令（粘贴长题干场景）。"""
    queue = ["第一行题干\\", "第二行题干"]
    monkeypatch.setattr(builtins, "input", lambda prompt="": queue.pop(0))
    assert repl_loop._read_user_input("> ") == "第一行题干\n第二行题干"


def test_t1_continuation_eof_returns_partial(monkeypatch):
    """续行中 EOF（管道结束）：把已读内容作为最终输入，不丢数据。"""
    queue = ["只有一段\\"]

    def _input(prompt=""):
        if queue:
            return queue.pop(0)
        raise EOFError

    monkeypatch.setattr(builtins, "input", _input)
    assert repl_loop._read_user_input("> ") == "只有一段"


# ════════════════════════════════════════════════════════════════
# T2 · 生成中 Ctrl+C 回主循环（空闲 Ctrl+C 仍优雅退出）
# ════════════════════════════════════════════════════════════════

def test_t2_ctrl_c_during_generation_returns_to_main_loop(monkeypatch, capsys):
    """生成中 Ctrl+C：只中断本次回答、回主循环保留会话（不裸崩退出）。"""
    ki_calls = []

    def _ki_stream(*a, **k):
        ki_calls.append(1)
        raise KeyboardInterrupt

    _drive_repl(monkeypatch, ["正常提问", "/exit"], stream=_ki_stream)
    out = capsys.readouterr().out

    assert ki_calls == [1], "前置条件：中断未注入（断言会空转）"
    assert "已中断本次回答" in out, f"缺少中断收口提示：{out[-300:]}"
    assert "再见" in out, "中断后会话未保留：后续 /exit 未被执行"


def test_t2_normal_reply_has_no_interrupt_banner(monkeypatch, capsys):
    """阴性对照：正常对话不得出现中断提示（证明分支有区分度，非恒真）。"""
    state = _drive_repl(monkeypatch, ["正常提问", "/exit"])
    out = capsys.readouterr().out

    assert state["llm_calls"] == [1], "前置条件：正常提问未走对话链路"
    assert "已中断本次回答" not in out
    assert "再见" in out


def test_t2_idle_ctrl_c_still_graceful_exit(monkeypatch, capsys):
    """空闲时 Ctrl+C 语义不变：优雅退出（打印告别语，不打印中断提示）。"""
    def _ki_input(prompt=""):
        raise KeyboardInterrupt

    _drive_repl(monkeypatch, [], input_factory=_ki_input)
    out = capsys.readouterr().out

    assert "再见" in out, "空闲 Ctrl+C 未优雅退出"
    assert "已中断本次回答" not in out, "空闲退出被误判为「生成中中断」"


def test_t2_interrupt_between_commands_keeps_session(monkeypatch, capsys):
    """连续两次生成中中断：每次都回主循环，会话始终保留。"""
    ki_calls = []

    def _ki_stream(*a, **k):
        ki_calls.append(1)
        raise KeyboardInterrupt

    _drive_repl(monkeypatch, ["提问一", "提问二", "/exit"], stream=_ki_stream)
    out = capsys.readouterr().out

    assert ki_calls == [1, 1], f"预期两次中断注入，实际 {len(ki_calls)} 次"
    assert out.count("已中断本次回答") == 2, "两次中断均应回主循环"
    assert "再见" in out


# ════════════════════════════════════════════════════════════════
# T3 · 菜单溢出面板宽度与截断保护
# ════════════════════════════════════════════════════════════════

def _render_menu_at(monkeypatch, width):
    monkeypatch.setattr(tui_navigator, "panel_width", lambda *a, **k: width)
    return tui_navigator.render_menu()


def test_t3_menu_no_overflow_at_panel_width(monkeypatch):
    """84 宽度下菜单任何一行不得超宽；[11]/[12] 行宽恰为面板宽（边框完整）。"""
    from tui.terminal import display_width

    out = _render_menu_at(monkeypatch, 84)
    bad = [l for l in out.split("\n") if display_width(l) > 84]
    assert bad == [], f"溢出 {len(bad)} 行，首行: {bad[0][:60]!r}"

    hit = [l for l in out.split("\n") if "[11]" in l or "[12]" in l]
    assert len(hit) == 2, f"[11]/[12] 行缺失：{len(hit)}"
    for line in hit:
        assert display_width(line) == 84, f"行宽 {display_width(line)} != 84"


def test_t3_render_box_line_truncates_oversized(monkeypatch):
    """截断保护（治本）：任意超宽输入恒被收进面板宽度，边框对齐。"""
    from tui.terminal import display_width

    r = tui_navigator.render_box_line("x" * 200, 84, "left", pad=0)
    assert display_width(r) == 84
    assert r.startswith("│") and r.endswith("│"), "边框破损"

    r2 = tui_navigator.render_box_line("中文超宽测试" * 30, 84, "left", pad=1)
    assert display_width(r2) == 84
    assert "…" in r2, "截断处未给省略号提示"


def test_t3_truncation_resets_color(monkeypatch):
    """彩色模式下截断处补复位序列，避免色彩泄漏到边框。"""
    from tui.terminal import display_width

    monkeypatch.setattr(tui_navigator.Colors, "RESET", "\033[0m")
    colored = "\033[96m" + "y" * 300 + "\033[0m"
    r = tui_navigator.render_box_line(colored, 84, "left", pad=1)

    assert display_width(r) == 84
    assert "\033[0m" in r, "截断后未补复位序列"


def test_t3_normal_width_text_untouched(monkeypatch):
    """阴性对照：未超宽文本原样保留（截断不误伤正常内容）。"""
    from tui.terminal import display_width

    r = tui_navigator.render_box_line("短文本", 84, "left", pad=1)
    assert "短文本" in r
    assert "…" not in r
    assert display_width(r) == 84


def test_t3_truncate_display_unit():
    """_truncate_display 单元：不超不动 / 精确截断 / 全角边界留省略号。"""
    assert tui_navigator._truncate_display("abc", 5) == "abc"
    assert tui_navigator._truncate_display("abcdef", 3) == "abc"
    assert tui_navigator._truncate_display("中中中", 5) == "中中…"
    assert tui_navigator._truncate_display("中", 0) == ""
