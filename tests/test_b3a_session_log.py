# -*- coding: utf-8 -*-
"""B3a 批次回归 · 会话持久化：AgentEvent schema / append-only JSONL / resume 重建。

本文件锁定（不得回退）：
1. **截断容错**：JSONL 最后一行是半截 JSON（进程崩溃现场）→ ``load_events``
   丢弃残行，正常返回前面的事件（断言恢复事件数与内容）；
2. **resume 上下文 == 实时上下文**：同一段事件流，``rebuild_history`` 与
   实时维护（12 条截断 + compact 摘要插入）逐条相等；并在真实 ``AgentRunner``
   上端到端复核（含真实压缩触发）；
3. **未知事件不崩**：注入未知 type 与多余字段 → 加载 / 重建正常，未知事件被保留；
4. **schema 校验**：缺字段 / 类型错误被 ``validate_event`` 报出；
5. **append-only**：两次 append 后行数增加、前面内容字节不变；
6. **写失败降级**：日志目录被占位 / ``open`` 抛 OSError → 不抛异常，
   对话路径照常返回；
7. **SessionStart 只触发一次**：同一 AgentRunner 连续 run 两次，钩子计数为 1；
8. ``close()`` 幂等，只写一条 ``session_end``；tool 事件带 parent 串链。

全程离线：AgentRunner 路径一律 monkeypatch ``_call_llm``（及工具执行），
不发起任何网络请求。夹具使用合成占位串，不含任何真实身份信息。
"""

import builtins
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.session_log import (  # noqa: E402
    SessionLog,
    SCHEMA_VERSION,
    RESUME_TAIL_MESSAGES,
    TOOL_RESULT_MAX_CHARS,
    KNOWN_EVENT_TYPES,
    EVENT_SESSION_START,
    EVENT_USER,
    EVENT_ASSISTANT,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_COMPACT,
    EVENT_SESSION_END,
    compose_history,
    load_events,
    new_event,
    rebuild_history,
    validate_event,
)

SUMMARY_TEXT = "【历史上下文压缩摘要 (Context Compaction)】:\n[学员目标]\n- 样本目标"


# ── 辅助 ────────────────────────────────────────────────────────────────


def _evt(etype, payload, *, eid, parent=None, ts="2026-01-01T00:00:00.000", **extra):
    """手工造一条事件（含可选多余字段，用于兼容性测试）。"""
    evt = {
        "id": eid,
        "ts": ts,
        "type": etype,
        "parent": parent,
        "payload": payload,
        "schema_version": SCHEMA_VERSION,
    }
    evt.update(extra)
    return evt


def _write_jsonl(path, events, *, tail_raw=None, tail_newline=True):
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    if tail_raw is not None:
        text += "\n" + tail_raw
        if tail_newline:
            text += "\n"
    else:
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _make_runner(tmp_path, monkeypatch, replies, *, api_key="sk-test-fake"):
    """构造离线 AgentRunner：``_call_llm`` 按序返回固定回复，绝不联网。"""
    from tools.agent.loop import AgentRunner

    replies = list(replies)
    state = {"i": 0}

    def fake_call_llm(self, messages):
        idx = min(state["i"], len(replies) - 1)
        state["i"] += 1
        return {"choices": [{"message": {"content": replies[idx], "tool_calls": []}}]}

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    cfg = {"api_key": api_key, "model": "样本模型", "active_subject": "pol"}
    return AgentRunner(config=cfg, workspace_root=tmp_path, permission_mode="auto", quiet=True)


# ── 1. 截断容错 ─────────────────────────────────────────────────────────


def test_truncated_last_line_is_dropped(tmp_path):
    """最后一行是半截 JSON → 丢弃残行，前面事件照常恢复。"""
    events = [
        _evt(EVENT_SESSION_START, {"active_subject": "pol", "user_input": "样本开场"}, eid="e1"),
        _evt(EVENT_USER, {"content": "样本问题一"}, eid="e2"),
        _evt(EVENT_ASSISTANT, {"content": "样本回答一"}, eid="e3"),
    ]
    half = '{"id": "e4", "ts": "2026-01-01T00:00:03.000", "type": "user", "payload": {"content": "半截'
    p = _write_jsonl(tmp_path / "s.jsonl", events, tail_raw=half, tail_newline=False)

    got = load_events(p)
    assert len(got) == 3, f"残行应被丢弃，实际恢复 {len(got)} 条"
    assert [e["type"] for e in got] == [EVENT_SESSION_START, EVENT_USER, EVENT_ASSISTANT]
    assert got[1]["payload"]["content"] == "样本问题一"
    # 重建也必须正常
    assert rebuild_history(got) == [
        {"role": "user", "content": "样本问题一"},
        {"role": "assistant", "content": "样本回答一"},
    ]


def test_truncated_line_with_trailing_newline_and_later_lines(tmp_path):
    """残行带换行（崩溃时缓冲区半行）也不得影响后续正常行。"""
    events = [_evt(EVENT_USER, {"content": "样本问题一"}, eid="e1")]
    p = _write_jsonl(tmp_path / "s.jsonl", events,
                     tail_raw='{"id": "bad", "type": "user", "payload": {"content": "半截')
    # 再补一条正常行：残行应被跳过，正常行保留
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_evt(EVENT_ASSISTANT, {"content": "样本回答一"}, eid="e2"),
                            ensure_ascii=False) + "\n")
    got = load_events(p)
    assert [e["type"] for e in got] == [EVENT_USER, EVENT_ASSISTANT]
    assert got[1]["payload"]["content"] == "样本回答一"


def test_load_events_missing_file_returns_empty(tmp_path):
    assert load_events(tmp_path / "不存在.jsonl") == []


# ── 2. resume 上下文 == 实时上下文 ──────────────────────────────────────


def test_rebuild_history_matches_live_tail_and_summary():
    """14 条消息 + 旧/新两条 compact + tool 事件 → 期望 = 最后一条摘要 + 最后 12 条消息。"""
    events = []
    for i in range(1, 8):
        events.append(_evt(EVENT_USER, {"content": f"样本问题{i}"}, eid=f"u{i}"))
        events.append(_evt(EVENT_ASSISTANT, {"content": f"样本回答{i}"}, eid=f"a{i}"))
    # 打散插入：旧摘要（应被新摘要覆盖）、tool 事件（不应进 history）
    events.insert(4, _evt(EVENT_COMPACT, {"summary": "【历史上下文压缩摘要 (Context Compaction)】: 旧"}, eid="c1"))
    events.insert(6, _evt(EVENT_TOOL_CALL, {"tool_call_id": "t1", "name": "read_file", "arguments": {}}, eid="tc1"))
    events.insert(7, _evt(EVENT_TOOL_RESULT, {"tool_call_id": "t1", "name": "read_file",
                                              "content": "样本工具输出", "truncated": False,
                                              "original_chars": 6}, eid="tr1", parent="tc1"))
    events.append(_evt(EVENT_COMPACT, {"summary": SUMMARY_TEXT}, eid="c2"))

    # 写死的期望（逐条）：最后一条 compact 摘要 + 最后 12 条消息（u2..a7）
    expect = [{"role": "system", "content": SUMMARY_TEXT}]
    for i in range(2, 8):
        expect.append({"role": "user", "content": f"样本问题{i}"})
        expect.append({"role": "assistant", "content": f"样本回答{i}"})

    got = rebuild_history(events)
    assert got == expect
    assert len(got) == 1 + RESUME_TAIL_MESSAGES
    # 实时维护（与 loop.py 同一 compose_history 语义）逐条一致
    live_msgs = []
    for i in range(1, 8):
        live_msgs.append({"role": "user", "content": f"样本问题{i}"})
        live_msgs.append({"role": "assistant", "content": f"样本回答{i}"})
    assert got == compose_history(SUMMARY_TEXT, live_msgs, limit=RESUME_TAIL_MESSAGES)


def test_compose_history_edge_limits():
    msgs = [{"role": "user", "content": "样本"}]
    assert compose_history(None, msgs, limit=0) == []
    assert compose_history("摘要", msgs, limit=0) == [{"role": "system", "content": "摘要"}]
    assert compose_history("   ", msgs, limit=12) == msgs
    assert compose_history("摘要", [], limit=12) == [{"role": "system", "content": "摘要"}]


def test_agent_runner_resume_equals_live_history(tmp_path, monkeypatch):
    """端到端：真实 AgentRunner 跑 3 轮，事件流重建结果与实时 history 逐条相等。"""
    questions = ("样本问题一", "样本问题二", "样本问题三")
    answers = ("样本回答一", "样本回答二", "样本回答三")
    runner = _make_runner(tmp_path, monkeypatch, list(answers))
    for q, a in zip(questions, answers):
        assert runner.run(q) == a

    events = load_events(runner._session_log.path)
    assert [e["type"] for e in events].count(EVENT_USER) == 3
    assert [e["type"] for e in events].count(EVENT_ASSISTANT) == 3

    assert runner.history == [
        {"role": "user", "content": "样本问题一"},
        {"role": "assistant", "content": "样本回答一"},
        {"role": "user", "content": "样本问题二"},
        {"role": "assistant", "content": "样本回答二"},
        {"role": "user", "content": "样本问题三"},
        {"role": "assistant", "content": "样本回答三"},
    ]
    assert rebuild_history(events) == runner.history
    runner.close()


def test_agent_runner_compact_event_and_resume_with_summary(tmp_path, monkeypatch):
    """端到端：强制触发压缩 → compact 事件落盘、摘要进 history、resume 仍逐条相等。"""
    runner = _make_runner(tmp_path, monkeypatch, ["样本回答"] * 6)
    runner.context_engine.compact_watermark = 0   # 消息数 > 6 即压缩（确定性触发）
    for i in range(1, 6):
        runner.run(f"样本问题{i}")

    events = load_events(runner._session_log.path)
    compacts = [e for e in events if e["type"] == EVENT_COMPACT]
    assert compacts, "应至少发生一次压缩并写入 compact 事件"
    for c in compacts:
        assert isinstance(c["payload"]["summary"], str) and c["payload"]["summary"]
        assert isinstance(c["payload"]["before_messages"], int)
        assert isinstance(c["payload"]["after_messages"], int)

    assert runner._history_summary is not None
    assert runner.history[0]["role"] == "system"
    assert "Context Compaction" in runner.history[0]["content"]
    assert runner.history[0]["content"] == compacts[-1]["payload"]["summary"]
    # 核心断言：resume 上下文 == 实时上下文
    assert rebuild_history(events) == runner.history
    runner.close()


# ── 3. 未知事件不崩 ─────────────────────────────────────────────────────


def test_unknown_event_type_and_extra_fields_are_tolerated(tmp_path):
    events = [
        _evt(EVENT_USER, {"content": "样本问题一"}, eid="e1"),
        _evt("future_event_999", {"whatever": [1, 2, 3]}, eid="e2",
             future_extra_field="新版本才认识的字段"),
        _evt(EVENT_ASSISTANT, {"content": "样本回答一"}, eid="e3"),
    ]
    p = _write_jsonl(tmp_path / "s.jsonl", events)

    got = load_events(p)
    assert len(got) == 3
    assert got[1]["type"] == "future_event_999"
    assert got[1]["future_extra_field"] == "新版本才认识的字段"   # 多余字段原样保留
    assert rebuild_history(got) == [
        {"role": "user", "content": "样本问题一"},
        {"role": "assistant", "content": "样本回答一"},
    ]
    # validate_event 把未知 type 报为提示性问题（但加载侧不据此过滤）
    probs = validate_event(got[1])
    assert any("future_event_999" in s for s in probs)


def test_structural_bad_lines_are_skipped(tmp_path):
    """非 JSON / 非对象 / 缺 type 的行跳过；payload 非 dict 补空 dict。"""
    lines = [
        json.dumps(_evt(EVENT_USER, {"content": "样本问题一"}, eid="e1"), ensure_ascii=False),
        "这不是 JSON",
        "[1, 2, 3]",
        json.dumps({"id": "x", "ts": "t", "type": "", "payload": {}, "parent": None,
                    "schema_version": 1}, ensure_ascii=False),
        json.dumps({"id": "y", "ts": "t", "type": EVENT_ASSISTANT, "payload": "坏 payload",
                    "parent": None, "schema_version": 1}, ensure_ascii=False),
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    got = load_events(p)
    assert [e["type"] for e in got] == [EVENT_USER, EVENT_ASSISTANT]
    assert got[1]["payload"] == {}


# ── 4. schema 校验 ──────────────────────────────────────────────────────


def test_validate_event_accepts_wellformed_event():
    for etype in sorted(KNOWN_EVENT_TYPES):
        assert validate_event(new_event(etype, {"content": "样本"})) == [], etype


def test_validate_event_reports_missing_and_wrong_types():
    bad = {"ts": 123, "payload": "不是 dict"}
    probs = validate_event(bad)
    for keyword in ("type", "payload", "ts", "parent", "id", "schema_version"):
        assert any(keyword in s for s in probs), f"未报出 {keyword}: {probs}"

    assert validate_event("不是事件") != []

    mixed = {"id": "i", "ts": "2026-01-01T00:00:00.000", "type": EVENT_USER,
             "parent": 5, "payload": {}, "schema_version": "1"}
    probs2 = validate_event(mixed)
    assert any("parent" in s for s in probs2)
    assert any("schema_version" in s for s in probs2)


# ── 5. append-only ──────────────────────────────────────────────────────


def test_append_only_grows_and_preserves_prefix_bytes(tmp_path):
    log = SessionLog(workspace_root=tmp_path, session_id="s-append")
    log.append(EVENT_USER, {"content": "样本第一行"})
    first = log.path.read_bytes()
    assert first.count(b"\n") == 1

    log.append(EVENT_ASSISTANT, {"content": "样本第二行"})
    second = log.path.read_bytes()
    assert second.startswith(first), "追加不得改写既有字节"
    assert second.count(b"\n") == 2
    log.close()

    # 重新打开同一 session 继续追加（同进程顺序写）
    log2 = SessionLog(workspace_root=tmp_path, session_id="s-append")
    log2.append(EVENT_USER, {"content": "样本第三行"})
    log2.close()

    events = load_events(log.path)
    assert [e["type"] for e in events] == [EVENT_USER, EVENT_ASSISTANT, EVENT_USER]
    assert events[2]["payload"]["content"] == "样本第三行"


def test_session_id_default_is_unique(tmp_path):
    a = SessionLog(workspace_root=tmp_path)
    b = SessionLog(workspace_root=tmp_path)
    assert a.session_id != b.session_id
    assert a.path.parent == tmp_path / ".memory" / "sessions"


# ── 6. 写失败降级 ───────────────────────────────────────────────────────


def test_write_failure_degrades_without_raising(tmp_path, monkeypatch):
    # 场景 A：.memory/sessions 被同名文件占位 → 建目录失败
    blocker = tmp_path / ".memory" / "sessions"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("占位文件", encoding="utf-8")

    log = SessionLog(workspace_root=tmp_path, session_id="s-bad")
    eid = log.append(EVENT_USER, {"content": "样本"})
    assert isinstance(eid, str) and eid
    log.append(EVENT_ASSISTANT, {"content": "样本"})   # 降级后继续调用也不抛
    log.close()
    assert log._degraded is True

    # 场景 B：open 抛 OSError（磁盘满 / 权限）
    log2 = SessionLog(workspace_root=tmp_path / "w2", session_id="s-bad2")
    real_open = builtins.open

    def _boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(builtins, "open", _boom)
    try:
        eid2 = log2.append(EVENT_USER, {"content": "样本"})
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert isinstance(eid2, str) and eid2
    log2.close()
    assert not log2.path.exists()


def test_agent_runner_survives_log_write_failure(tmp_path, monkeypatch):
    """日志目录不可写时，对话路径必须照常返回（降级为纯内存）。"""
    blocker = tmp_path / ".memory" / "sessions"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("占位文件", encoding="utf-8")

    runner = _make_runner(tmp_path, monkeypatch, ["样本回答一"])
    reply = runner.run("样本问题一")
    assert reply == "样本回答一"
    assert runner.history == [
        {"role": "user", "content": "样本问题一"},
        {"role": "assistant", "content": "样本回答一"},
    ]
    assert runner._session_log is not None
    assert runner._session_log._degraded is True
    runner.close()


# ── 7. 生命周期：SessionStart 只一次 / close 幂等 / tool 事件串链 ────────


def test_session_start_hook_fires_once_across_runs(tmp_path):
    from tools.agent.hooks import HookEvent
    from tools.agent.loop import AgentRunner

    runner = AgentRunner(config={"api_key": "", "active_subject": "pol"},
                         workspace_root=tmp_path, quiet=True)
    fired = []
    runner.hooks.register_hook(HookEvent.SESSION_START, lambda ctx: fired.append(ctx), priority=1)

    runner.run("样本问题一")   # 无 key：自然返回错误提示，不联网
    runner.run("样本问题二")
    assert len(fired) == 1, f"SessionStart 应只触发一次，实际 {len(fired)} 次"
    # 无 key 的会话不产生日志文件
    assert runner._session_log is None
    assert not (tmp_path / ".memory" / "sessions").exists()


def test_close_is_idempotent_and_writes_session_end(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path, monkeypatch, ["样本回答一"])
    runner.run("样本问题一")
    log_path = runner._session_log.path
    runner.close()
    runner.close()   # 幂等
    events = load_events(log_path)
    assert [e["type"] for e in events].count(EVENT_SESSION_END) == 1
    assert events[-1]["type"] == EVENT_SESSION_END
    assert events[-1]["payload"]["active_subject"] == "pol"


def test_close_without_run_creates_no_file(tmp_path):
    from tools.agent.loop import AgentRunner

    runner = AgentRunner(config={"api_key": "sk-test-fake", "active_subject": "pol"},
                         workspace_root=tmp_path, quiet=True)
    runner.close()
    assert not (tmp_path / ".memory" / "sessions").exists()


def test_tool_events_are_logged_with_parent_chain(tmp_path, monkeypatch):
    from tools.agent.loop import AgentRunner

    responses = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "样本.md"}'}}
        ]}}]},
        {"choices": [{"message": {"content": "样本最终回答", "tool_calls": []}}]},
    ]
    state = {"i": 0}

    def fake_call_llm(self, messages):
        idx = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return responses[idx]

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    runner = AgentRunner(
        config={"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"},
        workspace_root=tmp_path, permission_mode="auto", quiet=True)
    monkeypatch.setattr(runner.tool_registry, "execute_tool",
                        lambda name, args, interactive=True: "样本工具输出")

    assert runner.run("样本问题一") == "样本最终回答"
    events = load_events(runner._session_log.path)
    types = [e["type"] for e in events]
    assert types == [EVENT_SESSION_START, EVENT_USER, EVENT_TOOL_CALL, EVENT_TOOL_RESULT,
                     EVENT_ASSISTANT], types

    call_evt = next(e for e in events if e["type"] == EVENT_TOOL_CALL)
    result_evt = next(e for e in events if e["type"] == EVENT_TOOL_RESULT)
    assert call_evt["payload"]["name"] == "read_file"
    assert call_evt["payload"]["arguments"] == {"path": "样本.md"}
    assert result_evt["parent"] == call_evt["id"], "tool_result 的 parent 必须串到 tool_call 事件 id"
    assert result_evt["payload"]["content"] == "样本工具输出"
    assert result_evt["payload"]["truncated"] is False
    runner.close()


def test_oversized_tool_result_is_truncated_in_log(tmp_path, monkeypatch):
    """超长工具结果截断落盘（标注 truncated），且不破坏 resume 语义。"""
    from tools.agent.loop import AgentRunner

    big_output = "样" * (TOOL_RESULT_MAX_CHARS + 500)
    responses = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}
        ]}}]},
        {"choices": [{"message": {"content": "样本最终回答", "tool_calls": []}}]},
    ]
    state = {"i": 0}

    def fake_call_llm(self, messages):
        idx = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return responses[idx]

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    runner = AgentRunner(
        config={"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"},
        workspace_root=tmp_path, permission_mode="auto", quiet=True)
    monkeypatch.setattr(runner.tool_registry, "execute_tool",
                        lambda name, args, interactive=True: big_output)

    runner.run("样本问题一")
    events = load_events(runner._session_log.path)
    result_evt = next(e for e in events if e["type"] == EVENT_TOOL_RESULT)
    assert len(result_evt["payload"]["content"]) == TOOL_RESULT_MAX_CHARS
    assert result_evt["payload"]["truncated"] is True
    assert result_evt["payload"]["original_chars"] == len(big_output)
    assert rebuild_history(events) == runner.history
    runner.close()
