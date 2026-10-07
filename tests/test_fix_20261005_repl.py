# -*- coding: utf-8 -*-
"""2026-10-05 REPL/CLI 体验修复批次（F1-F11）回归测试。

覆盖 tools/cli/repl/{loop,renderer}.py 的 11 项已核实缺陷：

* F1  AGENTS.md 8 个中文口令全部缺失（终端中枢/导航/考纲Diff/切片入库/
      研招证据/双校对标/招生监控/高校侦察），输入后静默坠入 LLM 计费对话；
* F2  ``/watch <校名>`` 静默吞输入（无任何输出，也不添加监控）；
* F3  AgentRunner 模式 ``/2 /save /4 /5`` 全失效（不写本地 history、无工具栏）；
* F4  首启向导吞首句输入（非 TTY 也提问；非 y 输入被向导消费掉）；
* F5  ``/menu`` 进 TUI 前触发日终收尾（提前写完成率 / IM 推送「今日收工」）；
* F6  ``/clear`` 在 AgentRunner 模式无效（AI 记忆仍在）；
* F7  裸输入「打卡」坠入 LLM（缺用法提示）；
* F8  ``/today`` 无任务时提示跑 ``/plan`` 向导（应为「[科目]报到」）；
* F9  ``/relieve`` 不支持 ``--off`` / ``--keep-style``；``/exit`` 无续聊提示；
* F10 网页伴侣启动失败仍显示「已就绪」（None 被兜底成 8088）；
* F11 指令大盘缺项（/menu /subject /save /submit /gui /wechat /bridge /clawbot）。

全部用例只读 / 打桩：不联网、不落盘、不触碰真实 ky_config.json / 今日任务 /
会话日志；基础桩统一由 ``_drive_repl`` 铺设。
"""
from __future__ import annotations

import builtins
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


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
    """确定性错题本桩：默认全部空转，归档调用记入 state。"""

    def __init__(self, state):
        self.state = state

    def get_due_reviews(self, *a, **k):
        return []

    def generate_blind_quiz(self, *a, **k):
        return ""

    def log_error_record(self, **kw):
        self.state["archived"].append(kw)
        return "SENTINEL_ARCHIVED"

    def __getattr__(self, name):
        return lambda *a, **k: None


def _patch_importable(monkeypatch, module_names, attr, value):
    """在可能双导入的模块对象上打桩同一属性；返回打桩成功数。"""
    patched = 0
    for name in module_names:
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, value, raising=True)
            patched += 1
    return patched


def _stub_tui_loop(monkeypatch, recorder) -> int:
    """把 tui_navigator.run_tui_loop 打桩为 recorder（覆盖双导入的两个模块名）。"""
    return _patch_importable(monkeypatch, ("tools.tui_navigator", "tui_navigator"),
                             "run_tui_loop", recorder)


def _drive_repl(monkeypatch, inputs, cfg=None, stdin_tty=False, observe=None):
    """在打桩环境下驱动一次 run_repl，返回观测 state。

    基础桩保证：不读真实配置、不启端口、不进真实 Agent、不写工作区、
    不联网；``observe(monkeypatch, repl_loop, state)`` 供用例追加观测桩。
    """
    from tools.cli.repl import loop as repl_loop

    state = {"llm_calls": [], "toolbar_calls": [], "saved_configs": [],
             "archived": [], "runners": []}

    _cfg = dict(_BASE_CFG)
    if cfg:
        _cfg.update(cfg)

    monkeypatch.setattr(repl_loop, "load_config", lambda: dict(_cfg))
    monkeypatch.setattr(repl_loop, "save_config",
                        lambda c: state["saved_configs"].append(dict(c)))
    monkeypatch.setattr(repl_loop, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "start_background_live_server", lambda *a, **k: 8088)
    monkeypatch.setattr(repl_loop, "AgentRunner", None)
    monkeypatch.setattr(repl_loop, "_stdin_is_tty", lambda: stdin_tty)
    monkeypatch.setattr(repl_loop, "append_live_message", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "stream_chat",
                        lambda *a, **k: (state["llm_calls"].append(1), "SENTINEL_LLM")[1])
    monkeypatch.setattr(repl_loop, "print_followup_toolbar",
                        lambda: state["toolbar_calls"].append(1))
    monkeypatch.setattr(repl_loop, "build_system_prompt", lambda s: "SYS")
    monkeypatch.setattr(repl_loop, "infer_subject_from_text", lambda t, cur: cur)
    monkeypatch.setattr(repl_loop, "_count_error_records", lambda s: 0)
    monkeypatch.setattr(repl_loop, "_warn_if_mistake_not_archived", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "format_subject_hint", lambda a, b: "")
    monkeypatch.setattr(repl_loop, "error_logger", _FakeErrorLogger(state))
    # 报到分支会生成「今日任务」文件：打桩为 no-op，避免写真实工作区
    _patch_importable(monkeypatch, ("tools.study_planner", "study_planner"),
                      "ensure_subject_today_task", lambda *a, **k: None)

    if observe:
        observe(monkeypatch, repl_loop, state)

    queue = list(inputs)

    def _fake_input(prompt=""):
        if queue:
            return queue.pop(0)
        raise EOFError

    monkeypatch.setattr(builtins, "input", _fake_input)

    repl_loop.run_repl(permission_mode="ask")
    return state


# ════════════════════════════════════════════════════════════════
# F1 · AGENTS.md 中文口令 8 个
# ════════════════════════════════════════════════════════════════

_CN_CMD_CASES = [
    ("终端中枢", "menu"),
    ("导航", "menu"),
    ("考纲Diff", "diff"),
    ("考纲diff", "diff"),      # 大小写归一
    ("切片入库", "ingest"),
    ("研招证据", "admission"),
    ("双校对标", "compare"),
    ("招生监控", "watch"),
    ("高校侦察", "scout"),
]


@pytest.mark.parametrize("phrase,kind", _CN_CMD_CASES)
def test_f1_chinese_commands_route_to_local_branch(monkeypatch, capsys, phrase, kind):
    """8 个中文口令必须命中对应本地分支，绝不坠入 LLM 计费对话。"""
    marks = {"diff": [], "ingest": [], "tui": []}

    def observe(mp, repl_loop, state):
        if kind == "menu":
            assert _stub_tui_loop(mp, lambda: marks["tui"].append(1)) >= 1, \
                "tui_navigator 未定位，测试前置条件不成立"
        elif kind == "diff":
            _patch_importable(mp, ("tools.cli.commands.intel", "cli.commands.intel"),
                              "run_syllabus_diff", lambda arg: marks["diff"].append(arg))
        elif kind == "ingest":
            _patch_importable(mp, ("tools.cli.commands.material", "cli.commands.material"),
                              "run_material_ingest", lambda arg: marks["ingest"].append(arg))
        elif kind == "watch":
            class _EmptyWatcher:
                def list_watched(self):
                    return []

            mp.setattr(repl_loop, "intelligence",
                       types.SimpleNamespace(AdmissionWatcher=_EmptyWatcher))

    state = _drive_repl(monkeypatch, [phrase], observe=observe)
    out = capsys.readouterr().out

    assert state["llm_calls"] == [], \
        f"中文口令「{phrase}」坠入了 LLM 对话（应走本地分支）"
    if kind == "menu":
        assert marks["tui"] == [1], "「终端中枢/导航」未进入 /menu（TUI）分支"
        assert "TUI 终端中枢" in out, f"缺少进入 TUI 提示：{out[-300:]}"
    elif kind == "diff":
        assert marks["diff"] == [""], "「考纲Diff」未走 /diff 分支"
    elif kind == "ingest":
        assert marks["ingest"] == [""], "「切片入库」未走 /ingest 分支"
    elif kind == "admission":
        assert "用法: /admission" in out, "「研招证据」未走 /admission 分支"
    elif kind == "compare":
        assert "用法: /compare" in out, "「双校对标」未走 /compare 分支"
    elif kind == "watch":
        assert "监控高校" in out, "「招生监控」未走 /watch 分支"
    elif kind == "scout":
        assert "用法: /scout" in out, "「高校侦察」未走 /scout 分支"


def test_f1_negative_control_unmapped_phrase_falls_to_llm(monkeypatch, capsys):
    """阴性对照：未映射的中文短语仍走 LLM —— 证明「未坠入 LLM」断言不空转。"""
    state = _drive_repl(monkeypatch, ["随便聊聊天"], observe=None)
    # stream_chat 的真实实现负责把回复流式打印到终端，此处打桩只记录调用；
    # 「坠入 LLM」的判据即该调用发生（F1 各用例反向断言其为空）。
    assert state["llm_calls"] == [1], "helper 的 LLM 打桩未生效，F1 断言会空转"


def test_f1_fetch_alias_registered(monkeypatch, capsys):
    """AGENTS.md 118 行「考纲Diff / /fetch」：/fetch 必须与 /diff 同源。"""
    marks = []

    def observe(mp, repl_loop, state):
        _patch_importable(mp, ("tools.cli.commands.intel", "cli.commands.intel"),
                          "run_syllabus_diff", lambda arg: marks.append(arg))

    state = _drive_repl(monkeypatch, ["/fetch"], observe=observe)
    assert marks == [""], "/fetch 未走 /diff 分支"
    assert state["llm_calls"] == []


# ════════════════════════════════════════════════════════════════
# F2 · /watch <校名> 静默吞输入
# ════════════════════════════════════════════════════════════════

def test_f2_watch_add_school_has_output_and_registers(monkeypatch, capsys):
    """``/watch <校名>`` 必须调用 add_watch、有明确输出且状态含该校。"""
    added = []

    class _FakeWatcher:
        def __init__(self, *a, **k):
            pass

        def list_watched(self):
            return ([{"name": "示例大学", "last_check": "2026-10-05 12:00"}]
                    if added else [])

        def add_watch(self, school):
            added.append(school)
            return {"success": True, "name": school,
                    "url": "https://example.edu.cn/zs",
                    "msg": f"已成功将【{school}】纳入动态招生监控雷达"}

    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "intelligence",
                   types.SimpleNamespace(AdmissionWatcher=_FakeWatcher))

    state = _drive_repl(monkeypatch, ["/watch 示例大学"], observe=observe)
    out = capsys.readouterr().out

    assert added == ["示例大学"], "/watch <校名> 未调用 add_watch（输入被静默吞掉）"
    assert "已成功将【示例大学】纳入动态招生监控雷达" in out, f"缺少成功输出：{out[-300:]}"
    assert "监控页面" in out, "成功输出缺少监控页面信息"
    assert state["llm_calls"] == []


def test_f2_watch_add_failure_still_reports(monkeypatch, capsys):
    """添加失败（校名无法识别）也必须给出原因，不得静默。"""

    class _FakeWatcher:
        def __init__(self, *a, **k):
            pass

        def list_watched(self):
            return []

        def add_watch(self, school):
            return {"success": False, "msg": f"未能识别高校【{school}】，请核对校名或代码"}

    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "intelligence",
                   types.SimpleNamespace(AdmissionWatcher=_FakeWatcher))

    state = _drive_repl(monkeypatch, ["/watch 不存在大学"], observe=observe)
    out = capsys.readouterr().out
    assert "未能识别高校【不存在大学】" in out, f"失败路径无输出：{out[-300:]}"
    assert state["llm_calls"] == []


def test_f2_watch_empty_arg_still_lists(monkeypatch, capsys):
    """空参 ``/watch`` 回归保底：仍走监控列表分支。"""
    class _FakeWatcher:
        def __init__(self, *a, **k):
            pass

        def list_watched(self):
            return []

    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "intelligence",
                   types.SimpleNamespace(AdmissionWatcher=_FakeWatcher))

    _drive_repl(monkeypatch, ["/watch"], observe=observe)
    out = capsys.readouterr().out
    assert "监控高校" in out, "空参 /watch 未列出监控列表"


# ════════════════════════════════════════════════════════════════
# F3 · AgentRunner 模式 /2 /save /4 /5
# ════════════════════════════════════════════════════════════════

def _make_fake_agent_runner(state, reply="SENTINEL_AGENT_REPLY"):
    """构造记录型 AgentRunner 桩（run 同步写自身 history，模拟真实实现）。"""

    class _FakeAgentRunner:
        def __init__(self, **kw):
            self.config = dict(kw.get("config") or {})
            self.history = []
            self.history_after_run = 0
            self.closed = False
            self._session_id = kw.get("session_id") or "20261005-120000-ab"
            self._session_log = None
            self.hooks = types.SimpleNamespace(
                trigger_session_end=lambda ctx: None)
            state["runners"].append(self)

        def set_subject(self, subject):
            self.config["active_subject"] = subject

        def run(self, text, interactive=True):
            self.history.append({"role": "user", "content": text})
            self.history.append({"role": "assistant", "content": reply})
            self.history_after_run = len(self.history)
            return reply

        def close(self):
            self.closed = True

    return _FakeAgentRunner


def test_f3_agent_runner_mode_save_works(monkeypatch, capsys):
    """Agent 模式：回复后必须同步 history + 工具栏，/save 不再「暂无可归档」。"""
    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "AgentRunner",
                   _make_fake_agent_runner(state))

    state = _drive_repl(
        monkeypatch, ["请讲解一道题", "/save"],
        cfg={"api_key": "sk-test"}, observe=observe)
    out = capsys.readouterr().out

    assert state["runners"], "fake AgentRunner 未被构造，测试前置条件不成立"
    assert state["runners"][0].history_after_run == 2, \
        "AgentRunner.run 未按真实语义写入自身 history"
    assert state["toolbar_calls"], "Agent 模式回复后未打印跟随工具栏（/2 /4 /5 指引缺失）"
    assert state["archived"], "/save 未触发错题归档（history 未同步）"
    assert "SENTINEL_ARCHIVED" in out, "/save 归档结果未落地输出"
    assert "暂无可归档" not in out, "/save 仍提示无上下文（history 同步失效）"


def test_f3_permission_denied_does_not_write_history(monkeypatch, capsys):
    """PermissionDeniedError 分支 continue 时不得写 history、不得打工具栏。"""
    class PermissionDeniedError(Exception):
        pass

    class _DeniedRunner:
        def __init__(self, **kw):
            self.config = dict(kw.get("config") or {})
            self.history = []
            self.hooks = types.SimpleNamespace(trigger_session_end=lambda ctx: None)

        def set_subject(self, subject):
            pass

        def run(self, text, interactive=True):
            raise PermissionDeniedError("safe mode")

        def close(self):
            pass

    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "AgentRunner", _DeniedRunner)

    state = _drive_repl(
        monkeypatch, ["写一个文件"], cfg={"api_key": "sk-test"}, observe=observe)
    out = capsys.readouterr().out
    assert "[✘ 已拒绝]" in out
    assert state["toolbar_calls"] == [], "被拒回合不应打印工具栏"
    assert state["llm_calls"] == [], "被拒回合不得回退到 stream_chat"


# ════════════════════════════════════════════════════════════════
# F4 · 首启向导吞首句输入
# ════════════════════════════════════════════════════════════════

def _wizard_observers(monkeypatch, wizard_calls):
    """在双导入的 study_planner 上打桩向导，返回观察器。"""
    def observe(mp, repl_loop, state):
        _patch_importable(mp, ("study_planner", "tools.study_planner"),
                          "run_study_plan_wizard",
                          lambda **k: wizard_calls.append(k))
    return observe


def test_f4_non_tty_skips_wizard_and_keeps_input(monkeypatch, capsys):
    """非 TTY：跳过向导提问（不消费输入、不写 declined 标记），首句指令照常执行。"""
    wizard_calls = []
    state = _drive_repl(
        monkeypatch, ["数学报到"],
        cfg={"onboarding_completed": False}, stdin_tty=False,
        observe=_wizard_observers(monkeypatch, wizard_calls))
    out = capsys.readouterr().out

    assert wizard_calls == [], "非 TTY 下不应启动向导"
    assert not any(c.get("onboarding_declined") for c in state["saved_configs"]), \
        "非 TTY 下不应写入 onboarding_declined"
    assert "私教报到就绪" in out, "首句「数学报到」被向导吞掉，未作为指令执行"


def test_f4_tty_non_yes_input_becomes_first_command(monkeypatch, capsys):
    """TTY + 非 y 输入：视为 declined 并把该输入作为首条指令执行。"""
    wizard_calls = []
    state = _drive_repl(
        monkeypatch, ["数学报到"],
        cfg={"onboarding_completed": False}, stdin_tty=True,
        observe=_wizard_observers(monkeypatch, wizard_calls))
    out = capsys.readouterr().out

    assert wizard_calls == [], "非 y 输入不应启动向导"
    assert any(c.get("onboarding_declined") is True for c in state["saved_configs"]), \
        "拒绝向导必须落 onboarding_declined 标记"
    assert "已跳过向导" in out, "缺少跳过提示"
    assert "私教报到就绪" in out, "首条输入未被作为指令执行（向导吞句回归）"


def test_f4_tty_yes_starts_wizard(monkeypatch, capsys):
    """TTY + y：向导被调用。"""
    wizard_calls = []
    _drive_repl(
        monkeypatch, ["y"],
        cfg={"onboarding_completed": False}, stdin_tty=True,
        observe=_wizard_observers(monkeypatch, wizard_calls))
    assert len(wizard_calls) == 1, "输入 y 后向导未被调用"


def test_f4_tty_explicit_no_is_not_sent_to_llm(monkeypatch, capsys):
    """TTY + n：只记 declined，不得把「n」当提问发给 LLM。"""
    wizard_calls = []
    state = _drive_repl(
        monkeypatch, ["n"],
        cfg={"onboarding_completed": False}, stdin_tty=True,
        observe=_wizard_observers(monkeypatch, wizard_calls))
    assert wizard_calls == []
    assert any(c.get("onboarding_declined") is True for c in state["saved_configs"])
    assert state["llm_calls"] == [], "明确的拒绝「n」被误发 LLM"


# ════════════════════════════════════════════════════════════════
# F5 · /menu 日终收尾时机
# ════════════════════════════════════════════════════════════════

def test_f5_menu_session_end_only_after_tui(monkeypatch, capsys):
    """/menu：进 TUI 前不得触发 SessionEnd；TUI 退出（真正退出）后才触发。"""
    events = []

    class _FakeRunner:
        def __init__(self, **kw):
            self.config = dict(kw.get("config") or {})
            self.history = []
            self.closed = False
            self._session_id = "20261005-120000-ab"
            self._session_log = None
            self.hooks = types.SimpleNamespace(
                trigger_session_end=lambda ctx: events.append(("session_end", dict(ctx))))

        def set_subject(self, subject):
            pass

        def run(self, text, interactive=True):
            return "unused"

        def close(self):
            self.closed = True

    tui_marks = []

    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "AgentRunner", _FakeRunner)
        assert _stub_tui_loop(mp, lambda: tui_marks.append(len(events))) >= 1

    _drive_repl(monkeypatch, ["/menu"], observe=observe)
    out = capsys.readouterr().out

    assert "TUI 终端中枢" in out, "缺少进入 TUI 的提示行"
    assert tui_marks == [0], "进入 TUI 时 SessionEnd 已被提前触发（日终收尾时序回归）"
    assert len(events) == 1, "TUI 退出（REPL 真正退出）后应恰好触发一次 SessionEnd"
    assert events[0][0] == "session_end"


# ════════════════════════════════════════════════════════════════
# F6 · /clear 同步 AgentRunner 上下文
# ════════════════════════════════════════════════════════════════

def test_f6_clear_syncs_agent_history(monkeypatch, capsys):
    """/clear 必须同时清空 AgentRunner.history（否则 AI 仍记得全部对话）。"""
    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "AgentRunner", _make_fake_agent_runner(state))

    state = _drive_repl(
        monkeypatch, ["你好", "/clear", "/save"],
        cfg={"api_key": "sk-test"}, observe=observe)
    out = capsys.readouterr().out

    runner = state["runners"][0]
    assert runner.history_after_run == 2, "前置条件：run 后 agent history 应有 2 条"
    assert runner.history == [], "/clear 未同步清空 AgentRunner.history（AI 记忆仍在）"
    assert "已清空当前会话上下文" in out
    assert "暂无可归档" in out, "本地 history 未被清空（/save 仍能取到旧上下文）"
    assert state["archived"] == []


# ════════════════════════════════════════════════════════════════
# F7 · 裸输入「打卡」
# ════════════════════════════════════════════════════════════════

def test_f7_bare_checkin_shows_usage(monkeypatch, capsys):
    """裸「打卡」必须给用法提示，不得坠入 LLM。"""
    state = _drive_repl(monkeypatch, ["打卡"], observe=None)
    out = capsys.readouterr().out
    assert "用法: 打卡" in out, f"裸打卡无用法提示：{out[-300:]}"
    assert state["llm_calls"] == [], "裸打卡坠入了 LLM"


def test_f7_checkin_with_keyword_still_works(monkeypatch, capsys):
    """阳性对照：带关键词的「打卡 xxx」仍走原打卡逻辑。"""
    calls = []

    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "mark_today_task_done",
                   lambda kw, subj: (calls.append((kw, subj)), (True, "已完成打卡"))[1])

    state = _drive_repl(monkeypatch, ["打卡 英语阅读2篇"], observe=observe)
    out = capsys.readouterr().out
    assert calls == [("英语阅读2篇", "pro")], "带关键词打卡未走原逻辑"
    assert "已完成打卡" in out
    assert state["llm_calls"] == []


# ════════════════════════════════════════════════════════════════
# F8 · /today 无任务提示
# ════════════════════════════════════════════════════════════════

def test_f8_today_missing_task_points_to_checkin(monkeypatch, capsys, tmp_path):
    """无任务文件时必须提示「[科目]报到」，不得再引导去跑 /plan 向导。"""
    from tools.cli.repl import renderer

    _patch_importable(monkeypatch, ("tools.study_planner", "study_planner"),
                      "refresh_stale_today_tasks", lambda *a, **k: [])
    monkeypatch.setattr(renderer, "ROOT", tmp_path)     # 空目录：任务文件不存在
    monkeypatch.setattr(renderer, "load_config", lambda: dict(_BASE_CFG))
    monkeypatch.setattr(renderer, "get_today_tasks_data",
                        lambda: {"subjects": {"pol": {}, "pro": {}}})

    class _NoWatch:
        def list_watched(self):
            return []

    monkeypatch.setattr(renderer, "intelligence",
                        types.SimpleNamespace(AdmissionWatcher=_NoWatch))

    renderer.print_today_tasks_summary()
    out = capsys.readouterr().out

    assert "输入「政治报到」即可生成" in out, f"政治段未指路报到口令：{out[-500:]}"
    assert "输入「专业课报到」即可生成" in out, "专业课段未指路报到口令"
    assert "输入 /plan 一键生成" not in out, "残留 /plan 旧文案"


# ════════════════════════════════════════════════════════════════
# F9 · /relieve --off / --keep-style；/exit 续聊提示
# ════════════════════════════════════════════════════════════════

def test_f9_relieve_off_and_keep_style_passthrough(monkeypatch, capsys):
    """``/relieve --off`` 走恢复；``--keep-style`` 透传 keep_style=True。"""
    apply_calls, restore_calls = [], []

    def observe(mp, repl_loop, state):
        _patch_importable(
            mp, ("study_planner", "tools.study_planner"), "restore_relief_mode",
            lambda: (restore_calls.append(1),
                     {"success": True, "new_hours": 6.5,
                      "style": "严格把关·保姆提分型", "message": "已恢复"})[1])
        _patch_importable(
            mp, ("study_planner", "tools.study_planner"), "apply_relief_mode",
            lambda **k: (apply_calls.append(k),
                         {"success": True, "message": "减负模式已启动"})[1])

    state = _drive_repl(
        monkeypatch,
        ["/relieve --off", "/relieve --keep-style", "/relieve"],
        observe=observe)
    out = capsys.readouterr().out

    assert restore_calls == [1], "--off 未调用 restore_relief_mode"
    assert "已退出减负模式" in out, "缺少退出减负的输出"
    assert apply_calls == [{"keep_style": True}, {"keep_style": False}], \
        f"--keep-style 未透传：{apply_calls}"
    assert state["llm_calls"] == []


def test_f9_exit_hints_session_resume(monkeypatch, capsys):
    """/exit 必须提示可用 ky session resume <id> 续聊。"""
    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "AgentRunner", _make_fake_agent_runner(state))

    _drive_repl(monkeypatch, ["/exit"], cfg={"api_key": "sk-test"}, observe=observe)
    out = capsys.readouterr().out
    assert "ky session resume 20261005-120000-ab" in out, \
        f"/exit 缺少续聊提示：{out[-400:]}"
    assert "再见" in out


# ════════════════════════════════════════════════════════════════
# F10 · 网页伴侣启动失败
# ════════════════════════════════════════════════════════════════

def test_f10_gateway_none_no_ready_banner(monkeypatch, capsys):
    """网关返回 None：不得打印「已就绪」，须如实提示未启动。"""
    welcome_kwargs = []

    def observe(mp, repl_loop, state):
        mp.setattr(repl_loop, "start_background_live_server", lambda *a, **k: None)
        mp.setattr(repl_loop, "print_welcome",
                   lambda **k: welcome_kwargs.append(k))

    _drive_repl(monkeypatch, [], observe=observe)
    out = capsys.readouterr().out

    assert welcome_kwargs and welcome_kwargs[0].get("live_port") is None, \
        "print_welcome 未收到真实的 None（被兜底成 8088）"
    assert "已就绪" not in out, "启动失败仍宣称「已就绪」"
    assert "网页伴侣未启动" in out, "缺少未启动提示"


def test_f10_gateway_ok_still_ready(monkeypatch, capsys):
    """阳性对照：网关返回 8088 时照常打印已就绪。"""
    _drive_repl(monkeypatch, [], observe=None)   # 基础桩返回 8088
    out = capsys.readouterr().out
    assert "网页伴侣已就绪" in out


def test_f10_welcome_renders_not_started_for_none(monkeypatch, capsys):
    """print_welcome(live_port=None) 不得渲染 :None/live。"""
    from tools.cli.repl import renderer

    renderer.print_welcome(live_port=None, animate=False)
    out = capsys.readouterr().out
    assert ":None/live" not in out, "渲染了无效地址 :None/live"
    assert "未启动" in out


# ════════════════════════════════════════════════════════════════
# F11 · 指令大盘缺项
# ════════════════════════════════════════════════════════════════

_F11_EXPECTED = ("/menu", "/subject", "/save", "/submit",
                 "/gui", "/wechat", "/bridge", "/clawbot")


def test_f11_palette_lists_all_existing_commands():
    """大盘必须列出 loop.py 中真实存在但此前缺失的 8 个指令。"""
    from tools.cli.repl import renderer

    cmds = [c for _title, rows in renderer._palette_sections(False) for c, _d in rows]
    for name in _F11_EXPECTED:
        assert any(c.startswith(name) for c in cmds), f"指令大盘未列 {name}"


def test_f11_palette_commands_are_real_in_loop_source():
    """双向核对：大盘新增的指令在 loop.py 源码中真实存在（防照抄文档虚构）。"""
    src = (ROOT / "tools" / "cli" / "repl" / "loop.py").read_text(encoding="utf-8")
    for name in _F11_EXPECTED:
        assert f'"{name}"' in src, f"loop.py 中不存在指令 {name}（大盘列了虚构项）"
