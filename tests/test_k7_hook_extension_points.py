# -*- coding: utf-8 -*-
"""K7 批次回归 · RunLoop 渐进迁移（扩展点补点 / 拦截计数迁 hook / AfterCompact 复活）。

背景（agent 内核修复方案 K7）：RunLoop 需要一个稳定的扩展点体系，让后续
能力（记忆注入、请求改写、收尾审计）以 hook 方式挂载，而不是继续内联进
loop 主循环。本批五个单元：
  U1 GUI 关窗 SessionEnd（真实会话过 → 恰触发一次）；
  U2 HookEvent 新增 PREPARE_NEXT_TURN/PREPARE_REQUEST/FINISH_TURN/FINISH_RUN
     + trigger（无注册 = 恒等）；
  U3 拦截连续计数迁入 hooks.block_streak_guard（PostToolUse, priority 60），
     经 ctx 契约回传决策，loop 原位消费（消息/事件顺序逐字不变）；
  U4 收尾链仅补扩展点（FINISH_RUN），不搬迁；
  U5 AfterCompact 死事件复活（trigger_after_compact + compact_context 调用）。

本文件锁定（不得回退）：
1. 四个新事件的 trigger 恒等性（无注册时原样返回/静默）、改写契约
   （返回列表=替换、返回 None=不变）、异常隔离（单个 hook 抛错不影响链路）；
2. loop 主循环中扩展点的调用顺序（PNT→PR→FT 每轮、FR 收尾一次）；
3. block_streak_guard 的 ctx 契约：缺键 no-op、连续计数、阈值可配、
   第 3 次升级文案、成功执行重置；
4. GUI 关窗：真实会话过 → SessionEnd 恰一次（幂等）；空窗 → 零触发。

全程离线：AgentRunner 路径 monkeypatch ``_call_llm`` 与 ``execute_tool``；
GUI 段离屏（QT_QPA_PLATFORM=offscreen）且打桩 trigger_session_end（零写盘）。
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.hooks import HookEvent, HookManager  # noqa: E402


def _hm(tmp_path):
    return HookManager(workspace_root=tmp_path)


# ── 1. 扩展点基础契约（U2） ────────────────────────────────────────────


def test_new_events_defined():
    """四个新事件常量存在且命名稳定（外部注册方依赖字面量）。"""
    assert HookEvent.PREPARE_NEXT_TURN == "PrepareNextTurn"
    assert HookEvent.PREPARE_REQUEST == "PrepareRequest"
    assert HookEvent.FINISH_TURN == "FinishTurn"
    assert HookEvent.FINISH_RUN == "FinishRun"


def test_no_registration_is_identity(tmp_path):
    """无注册 = 恒等：改写型返回原引用，通知型静默无异常。"""
    hm = _hm(tmp_path)
    msgs = [{"role": "user", "content": "样本"}]
    assert hm.trigger_prepare_next_turn(msgs, {}) is msgs
    assert hm.trigger_prepare_request(msgs, {}) is msgs
    hm.trigger_finish_turn(msgs, {})
    hm.trigger_finish_run({})
    hm.trigger_after_compact(msgs, {})
    # 未注册的事件列表存在且为空（register_hook 之外不产生副作用）
    assert hm.hooks[HookEvent.PREPARE_NEXT_TURN] == []
    assert hm.hooks[HookEvent.FINISH_RUN] == []


def test_rewrite_contract(tmp_path):
    """改写契约：hook 返回列表 → 替换；返回 None → 保持不变。"""
    hm = _hm(tmp_path)
    injected = {"role": "user", "content": "注入"}

    hm.register_hook(HookEvent.PREPARE_NEXT_TURN,
                     lambda m, c: m + [injected], priority=100)
    msgs = [{"role": "user", "content": "x"}]
    out = hm.trigger_prepare_next_turn(msgs, {})
    assert out is not msgs and out[-1] is injected

    hm.register_hook(HookEvent.PREPARE_REQUEST,
                     lambda m, c: None, priority=100)
    assert hm.trigger_prepare_request(out, {}) is out


def test_hook_exception_isolated(tmp_path):
    """单个 hook 抛异常：被捕获警告，不影响链路与返回值。"""
    hm = _hm(tmp_path)

    def bad(m, c):
        raise RuntimeError("boom")

    seen = []
    hm.register_hook(HookEvent.PREPARE_NEXT_TURN, bad, priority=10)
    hm.register_hook(HookEvent.PREPARE_NEXT_TURN,
                     lambda m, c: seen.append(1) or None, priority=20)
    msgs = [{"role": "user", "content": "x"}]
    out = hm.trigger_prepare_next_turn(msgs, {})   # 不抛
    assert out is msgs
    assert seen == [1], "异常 hook 之后的正常 hook 仍应执行"

    hm.register_hook(HookEvent.FINISH_RUN, bad, priority=10)
    hm.trigger_finish_run({})                      # 不抛


def test_notify_events_reach_hooks(tmp_path):
    """FINISH_TURN / FINISH_RUN / AFTER_COMPACT 通知到注册方（含 messages/context）。"""
    hm = _hm(tmp_path)
    got = []
    hm.register_hook(HookEvent.FINISH_TURN,
                     lambda m, c: got.append(("FT", len(m), c.get("k"))), priority=100)
    hm.register_hook(HookEvent.FINISH_RUN,
                     lambda c: got.append(("FR", c.get("k"))), priority=100)
    hm.register_hook(HookEvent.AFTER_COMPACT,
                     lambda m, c: got.append(("AC", len(m))), priority=100)
    msgs = [{"role": "user", "content": "x"}]
    hm.trigger_finish_turn(msgs, {"k": 1})
    hm.trigger_finish_run({"k": 2})
    hm.trigger_after_compact(msgs, {})
    assert got == [("FT", 1, 1), ("FR", 2), ("AC", 1)]


# ── 2. loop 集成：扩展点调用顺序（U2+U4） ──────────────────────────────


def _scripted_runner(tmp_path, monkeypatch):
    """两轮离线 runner：轮1 工具调用（fake 执行）、轮2 文本收尾。"""
    from tools.agent.loop import AgentRunner

    scripted = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]}}]},
        {"choices": [{"message": {"content": "最终答复", "tool_calls": []}}]},
    ]
    calls = {"i": 0}

    def fake_call_llm(self, messages):
        idx = min(calls["i"], len(scripted) - 1)
        calls["i"] += 1
        return scripted[idx]

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    runner.tool_registry.execute_tool = lambda name, args, interactive=True, call_id="": "样本工具结果"
    return runner


def test_loop_extension_point_order(tmp_path, monkeypatch):
    """扩展点顺序：每轮 PNT→PR→FT；收尾恰一次 FR（且在所有轮次之后）。"""
    runner = _scripted_runner(tmp_path, monkeypatch)
    order = []
    runner.hooks.register_hook(HookEvent.PREPARE_NEXT_TURN,
                               lambda m, c: order.append("PNT"), priority=100)
    runner.hooks.register_hook(HookEvent.PREPARE_REQUEST,
                               lambda m, c: order.append("PR"), priority=100)
    runner.hooks.register_hook(HookEvent.FINISH_TURN,
                               lambda m, c: order.append("FT"), priority=100)
    runner.hooks.register_hook(HookEvent.FINISH_RUN,
                               lambda c: order.append("FR"), priority=100)

    out = runner.run("样本任务", interactive=False)
    assert out == "最终答复"
    assert order == ["PNT", "PR", "FT", "PNT", "PR", "FT", "FR"]
    runner.close()


def test_loop_prepare_request_can_rewrite(tmp_path, monkeypatch):
    """PREPARE_REQUEST 改写真的进入发给 LLM 的 messages。"""
    runner = _scripted_runner(tmp_path, monkeypatch)
    seen = []
    orig = runner._call_llm

    def spy_call_llm(messages):
        seen.append([m.get("content") for m in messages])
        return orig(messages)

    runner._call_llm = spy_call_llm
    runner.hooks.register_hook(
        HookEvent.PREPARE_REQUEST,
        lambda m, c: m + [{"role": "user", "content": "[K7 注入探针]"}], priority=100)
    runner.run("样本任务", interactive=False)
    assert any("[K7 注入探针]" in contents for contents in seen)
    runner.close()


def test_finish_run_context_has_final_answer(tmp_path, monkeypatch):
    """FINISH_RUN 的 context 携带 final_answer（收尾审计 hook 的消费面）。"""
    runner = _scripted_runner(tmp_path, monkeypatch)
    got = {}
    runner.hooks.register_hook(HookEvent.FINISH_RUN,
                               lambda c: got.update(c), priority=100)
    runner.run("样本任务", interactive=False)
    assert got.get("final_answer") == "最终答复"
    assert got.get("user_input") == "样本任务"
    runner.close()


# ── 3. block_streak_guard 的 ctx 契约（U3） ────────────────────────────


_BLOCKED_FETCH = "Error: 已拦截搜索引擎直抓（bing.com）。本环境已禁用搜索引擎直接抓取。"


def test_block_streak_guard_noop_without_contract(tmp_path):
    """ctx 缺 block_streaks / block_streak_decisions 键 → 完全 no-op 不崩。"""
    hm = _hm(tmp_path)
    out = hm.trigger_post_tool_use("fetch_url", {"url": "x"}, _BLOCKED_FETCH, {})
    assert out == _BLOCKED_FETCH, "无契约 ctx 时结果必须原样返回"


def test_block_streak_guard_decisions_via_contract(tmp_path):
    """ctx 契约：连续 3 次拦截 → 前 2 次 nudge、第 3 次升级；决策经列表回传。"""
    hm = _hm(tmp_path)
    ctx = {"block_streaks": {"safety": 0, "search": 0},
           "block_streak_decisions": [], "block_escalate_threshold": 3}
    for _ in range(3):
        hm.trigger_post_tool_use("fetch_url", {}, _BLOCKED_FETCH, ctx)
    decisions = ctx["block_streak_decisions"]
    assert [k for _, k in decisions] == [
        "search_guard_nudge", "search_guard_nudge", "search_guard_escalation"]
    assert "已连续 3 次" in decisions[2][0]
    assert ctx["block_streaks"]["search"] == 3


def test_block_streak_guard_success_resets(tmp_path):
    """成功执行同类工具 → 计数重置（「连续」语义，非历史累计）。"""
    hm = _hm(tmp_path)
    ctx = {"block_streaks": {"safety": 0, "search": 0},
           "block_streak_decisions": [], "block_escalate_threshold": 3}
    hm.trigger_post_tool_use("fetch_url", {}, _BLOCKED_FETCH, ctx)
    hm.trigger_post_tool_use("fetch_url", {}, _BLOCKED_FETCH, ctx)
    hm.trigger_post_tool_use("fetch_url", {}, "正常抓取结果", ctx)   # 成功 → 重置
    hm.trigger_post_tool_use("fetch_url", {}, _BLOCKED_FETCH, ctx)
    kinds = [k for _, k in ctx["block_streak_decisions"]]
    assert kinds == ["search_guard_nudge", "search_guard_nudge", "search_guard_nudge"]
    assert ctx["block_streaks"]["search"] == 1


def test_block_streak_guard_threshold_from_context(tmp_path):
    """升级阈值从 ctx 读取（loop 传 AgentRunner._BLOCK_ESCALATE_THRESHOLD）。"""
    hm = _hm(tmp_path)
    ctx = {"block_streaks": {"safety": 0, "search": 0},
           "block_streak_decisions": [], "block_escalate_threshold": 2}
    hm.trigger_post_tool_use("fetch_url", {}, _BLOCKED_FETCH, ctx)
    hm.trigger_post_tool_use("fetch_url", {}, _BLOCKED_FETCH, ctx)
    kinds = [k for _, k in ctx["block_streak_decisions"]]
    assert kinds == ["search_guard_nudge", "search_guard_escalation"]


# ── 4. GUI 关窗 SessionEnd（U1，离屏） ─────────────────────────────────


def _patch_gui_session_end(monkeypatch):
    """按 GUI 实际导入路径（agent.hooks 优先）打桩 trigger_session_end。

    返回 calls 列表（每次触发 append 一次 context）。双导入防御：GUI 里
    ``from agent.hooks import HookManager`` 先成功，故打桩必须打同一模块对象。
    """
    try:
        from agent.hooks import HookManager as _HM
    except ImportError:  # pragma: no cover
        from tools.agent.hooks import HookManager as _HM
    calls = []
    monkeypatch.setattr(_HM, "trigger_session_end",
                        lambda self, context: calls.append(dict(context)))
    return calls


@pytest.fixture()
def gui_env(monkeypatch):
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 离屏测试")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst


def test_gui_close_triggers_session_end_once(gui_env, monkeypatch):
    """真实会话过 → 关窗触发 SessionEnd 恰一次；再次 close 不重复。"""
    from tools.gui.main_window import MainWindow

    calls = _patch_gui_session_end(monkeypatch)
    win = MainWindow()
    win._mark_agent_session_ran()      # 模拟 AgentWorker.session_ran_signal 槽
    win.close()
    assert len(calls) == 1
    assert "active_subject" in calls[0]
    assert win._session_end_fired is True
    win.close()
    assert len(calls) == 1, "SessionEnd 必须幂等（只触发一次）"


def test_gui_close_skips_session_end_without_session(gui_env, monkeypatch):
    """空窗（从未发过消息）→ 关窗零触发（不写盘、不推送）。"""
    from tools.gui.main_window import MainWindow

    calls = _patch_gui_session_end(monkeypatch)
    win = MainWindow()
    win.close()
    assert calls == []
    assert win._session_end_fired is False


def test_gui_session_end_hook_failure_never_blocks_close(gui_env, monkeypatch):
    """钩子抛异常 → 关窗流程照常完成（静默降级）。"""
    from tools.gui.main_window import MainWindow

    try:
        from agent.hooks import HookManager as _HM
    except ImportError:  # pragma: no cover
        from tools.agent.hooks import HookManager as _HM
    monkeypatch.setattr(_HM, "trigger_session_end",
                        lambda self, context: (_ for _ in ()).throw(RuntimeError("boom")))
    win = MainWindow()
    win._mark_agent_session_ran()
    win.close()                        # 不抛
    assert win._session_end_fired is True
