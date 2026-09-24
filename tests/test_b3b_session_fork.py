# -*- coding: utf-8 -*-
"""B3b 批次回归 · 会话持久化 fork + replay + ky session 命令。

本文件锁定（不得回退）：
1. **fork 不影响原会话**：源文件 sha256 字节不变（fork 只读源）；
2. **安全边界对齐**：``find_safe_fork_index`` 不切断 tool_call/tool_result
   配对（--at 落在 call 与 result 之间时向前对齐，复制前缀里无孤儿 call）；
3. **截断容错**：源文件最后一行半截 JSON → load 丢残行 → fork 复制完整事件；
4. **旧格式迁移**：缺 ``schema_version`` / ``id`` / ``parent`` 的历史事件在
   load / rebuild / fork 全链路不崩；
5. **会话管理**：list_sessions 排序与字段、fork 出的会话带 forked_from、
   remove_session、prune_sessions 的 keep 语义；
6. **CLI 层**：``ky session ls|fork|rm|prune``（prune 默认 dry-run）；
   resume 只 monkeypatch 断言收到 ``resume_session_id``，**不启动真实 REPL**；
7. **AgentRunner 恢复**：指定 session_id 且文件已有事件 → history 自动恢复、
   SessionStart 不重复触发 / session_start 不重复写；
8. **常量单源**：``COMPACT_SUMMARY_PREFIX`` 唯一实现处在 compaction，
   loop.py 不再保留私有副本。

全程离线：AgentRunner 路径一律 monkeypatch ``_call_llm``，不发起任何网络请求；
夹具使用合成占位串，不含任何真实身份信息。
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.session_log import (  # noqa: E402
    SessionLog,
    SCHEMA_VERSION,
    EVENT_SESSION_START,
    EVENT_USER,
    EVENT_ASSISTANT,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_COMPACT,
    find_safe_fork_index,
    fork_session,
    list_sessions,
    load_events,
    prune_sessions,
    rebuild_history,
    remove_session,
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


def _write_jsonl(path, events, *, tail_raw=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    if tail_raw is not None:
        text += "\n" + tail_raw
    else:
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _session_path(ws, sid):
    return Path(ws) / ".memory" / "sessions" / f"{sid}.jsonl"


def _seed_session(ws, sid, events, **kw):
    return _write_jsonl(_session_path(ws, sid), events, **kw)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _session_events(idx, prefix="样本"):
    """3 条事件的最小会话（ts 按 idx 递增，保证排序确定）。"""
    ts = f"2026-01-{idx:02d}T00:00:00.000"
    return [
        _evt(EVENT_SESSION_START, {"active_subject": "pol", "user_input": f"{prefix}问题{idx}"},
             eid=f"s{idx}-1", ts=ts),
        _evt(EVENT_USER, {"content": f"{prefix}问题{idx}"}, eid=f"s{idx}-2", ts=ts),
        _evt(EVENT_ASSISTANT, {"content": f"{prefix}回答{idx}"}, eid=f"s{idx}-3", ts=ts),
    ]


def _basic_events(prefix="样本"):
    """含一对 tool_call/tool_result 的会话：e1 开始 / e2 提问 / e3 call / e4 result / e5 回答。"""
    return [
        _evt(EVENT_SESSION_START, {"active_subject": "pol", "user_input": f"{prefix}问题一"},
             eid="e1", ts="2026-01-01T00:00:01.000"),
        _evt(EVENT_USER, {"content": f"{prefix}问题一"}, eid="e2", ts="2026-01-01T00:00:02.000"),
        _evt(EVENT_TOOL_CALL, {"tool_call_id": "t1", "name": "read_file", "arguments": {}},
             eid="e3", ts="2026-01-01T00:00:03.000"),
        _evt(EVENT_TOOL_RESULT, {"tool_call_id": "t1", "name": "read_file",
                                 "content": "样本工具输出", "truncated": False,
                                 "original_chars": 6},
             eid="e4", parent="e3", ts="2026-01-01T00:00:04.000"),
        _evt(EVENT_ASSISTANT, {"content": f"{prefix}回答一"}, eid="e5", ts="2026-01-01T00:00:05.000"),
    ]


def _make_runner(tmp_path, monkeypatch, replies, *, session_id=None, api_key="sk-test-fake"):
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
    return AgentRunner(config=cfg, workspace_root=tmp_path, permission_mode="auto",
                       quiet=True, session_id=session_id)


def _cli_mod():
    import tools.cli.commands.session as sess_mod
    return sess_mod


def _patch_run_repl(monkeypatch, fake):
    """替换真实 ``run_repl``（不启动 REPL）。"""
    try:
        import tools.cli.repl.loop as repl_mod
    except ImportError:  # pragma: no cover
        pytest.skip("tools.cli.repl.loop 不可导入")
    monkeypatch.setattr(repl_mod, "run_repl", fake)


# ── 1. fork 全量：事件数 / forked_from / 源文件字节不变 ─────────────────


def test_fork_full_copies_all_events(tmp_path):
    sid = "20260101-000000-full01"
    _seed_session(tmp_path, sid, _basic_events())
    new_path, copied, message = fork_session(tmp_path, sid)
    assert new_path is not None and new_path.is_file()
    assert copied == 5
    assert len(load_events(new_path)) == 5
    assert new_path.stem != sid
    assert new_path.stem in message


def test_fork_full_marks_forked_from(tmp_path):
    sid = "20260101-000000-full02"
    _seed_session(tmp_path, sid, _basic_events())
    new_path, _copied, _msg = fork_session(tmp_path, sid)
    events = load_events(new_path)
    assert events[0]["type"] == EVENT_SESSION_START
    assert events[0]["payload"]["forked_from"] == {"session_id": sid, "at_event": 5}
    # 其余事件原样复制（内容与类型序列一致）
    assert [e["type"] for e in events] == [e["type"] for e in _basic_events()]
    assert events[1]["payload"]["content"] == "样本问题一"


def test_fork_does_not_touch_source_bytes(tmp_path):
    sid = "20260101-000000-full03"
    src = _seed_session(tmp_path, sid, _basic_events())
    before = _sha256(src)
    new_path, _copied, _msg = fork_session(tmp_path, sid, at_event=3)
    assert new_path is not None
    assert _sha256(src) == before, "fork 只读源文件，源字节必须逐位不变"
    # 再次全量 fork 也一样
    fork_session(tmp_path, sid)
    assert _sha256(src) == before


# ── 2. --at 与安全边界对齐 ──────────────────────────────────────────────


def test_fork_at_event_count(tmp_path):
    sid = "20260101-000000-at0001"
    _seed_session(tmp_path, sid, _basic_events())
    new_path, copied, _msg = fork_session(tmp_path, sid, at_event=2)
    assert copied == 2
    assert [e["type"] for e in load_events(new_path)] == [EVENT_SESSION_START, EVENT_USER]


def test_fork_at_aligns_before_orphan_tool_call(tmp_path):
    """--at 落在 tool_call 与 tool_result 之间 → 自动对齐到 call 之前。"""
    sid = "20260101-000000-at0002"
    _seed_session(tmp_path, sid, _basic_events())
    new_path, copied, _msg = fork_session(tmp_path, sid, at_event=3)
    assert copied == 2, "前缀 [start,user,call] 含孤儿 call，应对齐到 call 之前"
    events = load_events(new_path)
    assert [e["type"] for e in events] == [EVENT_SESSION_START, EVENT_USER]
    assert not any(e["type"] == EVENT_TOOL_CALL for e in events), "复制前缀里不得有孤儿 tool_call"
    assert not any(e["type"] == EVENT_TOOL_RESULT for e in events)


def test_fork_at_after_tool_pair_keeps_pair(tmp_path):
    """--at 落在 tool_result 之后 → 配对完整，无需回退。"""
    sid = "20260101-000000-at0003"
    _seed_session(tmp_path, sid, _basic_events())
    new_path, copied, _msg = fork_session(tmp_path, sid, at_event=4)
    assert copied == 4
    events = load_events(new_path)
    calls = {e["id"] for e in events if e["type"] == EVENT_TOOL_CALL}
    results = {e["parent"] for e in events if e["type"] == EVENT_TOOL_RESULT}
    assert calls and calls <= results, "call/result 必须配对完整"


def test_fork_at_zero_and_oversized_are_clamped(tmp_path):
    sid = "20260101-000000-at0004"
    _seed_session(tmp_path, sid, _basic_events())
    p1, n1, _m1 = fork_session(tmp_path, sid, at_event=0)
    assert p1 is not None and n1 == 1, "at_event 收敛到 [1, len]"
    p2, n2, _m2 = fork_session(tmp_path, sid, at_event=999)
    assert p2 is not None and n2 == 5


def test_fork_at_invalid_value_returns_reason(tmp_path):
    sid = "20260101-000000-at0005"
    _seed_session(tmp_path, sid, _basic_events())
    path, copied, message = fork_session(tmp_path, sid, at_event="不是数字")
    assert path is None and copied == 0
    assert "整数" in message


def test_fork_at_all_orphan_prefix_returns_reason(tmp_path):
    """tool_call 是首条事件 → 任何 <= 1 的对齐都落到 0 → 没有可安全分叉的位置。"""
    sid = "20260101-000000-at0006"
    _seed_session(tmp_path, sid, [
        _evt(EVENT_TOOL_CALL, {"tool_call_id": "t1", "name": "read_file", "arguments": {}}, eid="c1"),
        _evt(EVENT_TOOL_RESULT, {"tool_call_id": "t1", "name": "read_file", "content": "样本输出",
                                 "truncated": False, "original_chars": 4}, eid="r1", parent="c1"),
    ])
    path, copied, message = fork_session(tmp_path, sid, at_event=1)
    assert path is None and copied == 0
    assert "没有可安全分叉的位置" in message


def test_find_safe_fork_index_variants():
    events = _basic_events()
    assert find_safe_fork_index(events, 5) == 5
    assert find_safe_fork_index(events, 4) == 4
    assert find_safe_fork_index(events, 3) == 2
    assert find_safe_fork_index(events, 2) == 2
    assert find_safe_fork_index(events, 1) == 1
    assert find_safe_fork_index(events, 0) == 0


def test_find_safe_fork_index_bounds_and_types():
    events = _basic_events()
    assert find_safe_fork_index(events, 999) == 5
    assert find_safe_fork_index(events, -3) == 0
    assert find_safe_fork_index(events, None) == 5
    assert find_safe_fork_index([], 3) == 0
    # 多段配对：call1/result1/call2/result2，--at 落在第二段中间 → 回退到 call2 前
    events2 = [
        _evt(EVENT_TOOL_CALL, {"tool_call_id": "a", "name": "read_file", "arguments": {}}, eid="c1"),
        _evt(EVENT_TOOL_RESULT, {"tool_call_id": "a", "name": "read_file", "content": "样本",
                                 "truncated": False, "original_chars": 2}, eid="r1", parent="c1"),
        _evt(EVENT_TOOL_CALL, {"tool_call_id": "b", "name": "read_file", "arguments": {}}, eid="c2"),
        _evt(EVENT_TOOL_RESULT, {"tool_call_id": "b", "name": "read_file", "content": "样本",
                                 "truncated": False, "original_chars": 2}, eid="r2", parent="c2"),
    ]
    assert find_safe_fork_index(events2, 4) == 4
    assert find_safe_fork_index(events2, 3) == 2


# ── 3. fork 后 resume 历史 == 源在 fork 点的历史 ────────────────────────


def test_fork_resume_history_equals_source_prefix(tmp_path):
    sid = "20260101-000000-hist01"
    _seed_session(tmp_path, sid, _basic_events())
    new_path, _copied, _msg = fork_session(tmp_path, sid, at_event=3)   # 对齐到 2
    src_events = load_events(_session_path(tmp_path, sid))
    expect = rebuild_history(src_events[:2])
    assert expect == [{"role": "user", "content": "样本问题一"}]
    assert rebuild_history(load_events(new_path)) == expect


def test_fork_full_history_equals_source(tmp_path):
    sid = "20260101-000000-hist02"
    _seed_session(tmp_path, sid, _basic_events())
    new_path, _copied, _msg = fork_session(tmp_path, sid)
    assert rebuild_history(load_events(new_path)) == \
        rebuild_history(load_events(_session_path(tmp_path, sid)))


# ── 4. 截断 fixture 与旧格式迁移 ────────────────────────────────────────


def test_fork_truncated_source_line_is_dropped(tmp_path):
    """源文件最后一行半截 JSON → load 丢弃残行 → fork 复制完整事件数正确。"""
    sid = "20260101-000000-trunc1"
    _seed_session(tmp_path, sid, _basic_events(),
                  tail_raw='{"id": "x", "type": "user", "payload": {"content": "半截')
    assert len(load_events(_session_path(tmp_path, sid))) == 5
    new_path, copied, _msg = fork_session(tmp_path, sid)
    assert new_path is not None
    assert copied == 5
    assert len(load_events(new_path)) == 5
    assert [e["type"] for e in load_events(new_path)][-1] == EVENT_ASSISTANT


def test_fork_legacy_events_without_schema_fields(tmp_path):
    """缺 schema_version / id / parent 的旧事件：load / rebuild / fork 全链路不崩。"""
    sid = "20260101-000000-legacy"
    legacy = [
        {"ts": "2026-01-01T00:00:01.000", "type": EVENT_SESSION_START,
         "payload": {"active_subject": "pol"}},
        {"ts": "2026-01-01T00:00:02.000", "type": EVENT_USER, "payload": {"content": "样本问题一"}},
        {"ts": "2026-01-01T00:00:03.000", "type": EVENT_ASSISTANT, "payload": {"content": "样本回答一"}},
    ]
    _write_jsonl(_session_path(tmp_path, sid), legacy)
    events = load_events(_session_path(tmp_path, sid))
    assert len(events) == 3
    assert rebuild_history(events) == [
        {"role": "user", "content": "样本问题一"},
        {"role": "assistant", "content": "样本回答一"},
    ]
    new_path, copied, _msg = fork_session(tmp_path, sid)
    assert new_path is not None and copied == 3
    got = load_events(new_path)
    assert got[0]["type"] == EVENT_SESSION_START
    assert got[0]["payload"]["forked_from"]["session_id"] == sid
    assert rebuild_history(got) == rebuild_history(events)


def test_fork_legacy_orphan_call_without_id_aligns_safely(tmp_path):
    """旧格式 tool_call 缺事件 id、但有 payload.tool_call_id → 仍能判定孤儿并
    安全对齐（不崩，且不把孤儿 call 复制进新会话）。"""
    sid = "20260101-000000-legacy2"
    legacy = [
        {"ts": "2026-01-01T00:00:01.000", "type": EVENT_SESSION_START, "payload": {}},
        {"ts": "2026-01-01T00:00:02.000", "type": EVENT_TOOL_CALL,
         "payload": {"tool_call_id": "t1", "name": "read_file"}},
    ]
    _write_jsonl(_session_path(tmp_path, sid), legacy)
    new_path, copied, _msg = fork_session(tmp_path, sid, at_event=2)
    assert new_path is not None and copied == 1
    assert [e["type"] for e in load_events(new_path)] == [EVENT_SESSION_START]


def test_fork_legacy_unidentifiable_call_does_not_block(tmp_path):
    """旧格式 tool_call 连 tool_call_id 都没有 → 配对无法判定，按不阻断处理。"""
    sid = "20260101-000000-legacy3"
    legacy = [
        {"ts": "2026-01-01T00:00:01.000", "type": EVENT_SESSION_START, "payload": {}},
        {"ts": "2026-01-01T00:00:02.000", "type": EVENT_TOOL_CALL, "payload": {"name": "read_file"}},
    ]
    _write_jsonl(_session_path(tmp_path, sid), legacy)
    new_path, copied, _msg = fork_session(tmp_path, sid, at_event=2)
    assert new_path is not None and copied == 2


def test_fork_legacy_source_without_session_start(tmp_path):
    """源首条不是 session_start → 新造一条带 forked_from 的 session_start 置于文件头。"""
    sid = "20260101-000000-nostart"
    _seed_session(tmp_path, sid, [
        _evt(EVENT_USER, {"content": "样本问题一"}, eid="e1"),
        _evt(EVENT_ASSISTANT, {"content": "样本回答一"}, eid="e2"),
    ])
    new_path, copied, _msg = fork_session(tmp_path, sid)
    assert new_path is not None and copied == 3
    got = load_events(new_path)
    assert got[0]["type"] == EVENT_SESSION_START
    assert got[0]["payload"]["forked_from"] == {"session_id": sid, "at_event": 2}
    assert [e["type"] for e in got[1:]] == [EVENT_USER, EVENT_ASSISTANT]


def test_fork_missing_source_returns_reason(tmp_path):
    path, copied, message = fork_session(tmp_path, "不存在的会话")
    assert path is None and copied == 0
    assert "不存在" in message


def test_fork_empty_source_returns_reason(tmp_path):
    sid = "20260101-000000-empty0"
    _write_jsonl(_session_path(tmp_path, sid), [])
    path, copied, message = fork_session(tmp_path, sid)
    assert path is None and copied == 0
    assert "没有可用事件" in message


# ── 5. list_sessions / remove_session / prune_sessions ──────────────────


def test_list_sessions_sorted_with_fields(tmp_path):
    sid_old = "20260101-000000-old000"
    sid_new = "20260102-000000-new000"
    _seed_session(tmp_path, sid_old, _session_events(1))
    _seed_session(tmp_path, sid_new, _session_events(2))
    sessions = list_sessions(tmp_path)
    assert [s["session_id"] for s in sessions] == [sid_new, sid_old]
    top = sessions[0]
    assert top["event_count"] == 3
    assert top["message_count"] == 2
    assert top["first_user_text"] == "样本问题2"
    assert top["created_at"] == "2026-01-02T00:00:00.000"
    assert top["last_ts"] == "2026-01-02T00:00:00.000"
    assert isinstance(top["path"], Path)
    assert top["forked_from"] is None
    assert list_sessions(tmp_path / "不存在") == []


def test_list_sessions_reports_forked_from(tmp_path):
    sid = "20260101-000000-src000"
    _seed_session(tmp_path, sid, _session_events(1))
    new_path, _copied, _msg = fork_session(tmp_path, sid)
    by_id = {s["session_id"]: s for s in list_sessions(tmp_path)}
    assert set(by_id) == {sid, new_path.stem}
    assert by_id[new_path.stem]["forked_from"] == {"session_id": sid, "at_event": 3}


def test_list_sessions_skips_empty_and_bad_files(tmp_path):
    sessions_dir = tmp_path / ".memory" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "empty.jsonl").write_text("", encoding="utf-8")
    (sessions_dir / "bad.jsonl").write_text("这不是 JSON\n", encoding="utf-8")
    _seed_session(tmp_path, "20260101-000000-good00", _session_events(1))
    assert [s["session_id"] for s in list_sessions(tmp_path)] == ["20260101-000000-good00"]


def test_remove_session_existing_and_missing(tmp_path):
    sid = "20260101-000000-rm0000"
    _seed_session(tmp_path, sid, _session_events(1))
    assert remove_session(tmp_path, sid) is True
    assert not _session_path(tmp_path, sid).exists()
    assert remove_session(tmp_path, sid) is False
    assert remove_session(tmp_path, "从未存在") is False


def test_prune_sessions_keeps_recent(tmp_path):
    for i in range(1, 6):
        _seed_session(tmp_path, f"2026010{i}-000000-keep{i:02d}", _session_events(i))
    removed = prune_sessions(tmp_path, keep=2)
    assert len(removed) == 3
    assert [s["session_id"] for s in list_sessions(tmp_path)] == [
        "20260105-000000-keep05", "20260104-000000-keep04"]
    # keep=0 → 全删；keep 超过总数 → 不删
    assert prune_sessions(tmp_path, keep=99) == []
    assert len(prune_sessions(tmp_path, keep=0)) == 2
    assert list_sessions(tmp_path) == []


# ── 6. CLI 层：ky session ───────────────────────────────────────────────


def test_cli_session_ls_lists_ids(tmp_path, monkeypatch, capsys):
    sess_mod = _cli_mod()
    sid = "20260101-120000-aa11bb"
    _seed_session(tmp_path, sid, _basic_events())
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    sess_mod._cmd_session(["session", "ls"])
    out = capsys.readouterr().out
    assert sid[:8] in out
    assert sid[:15] in out, "应显示到 HHMMSS 段，同一天的多个会话才能区分"
    assert "历史会话" in out
    # 同一天不同时间的两个会话必须能区分（前 8 位只有日期）
    _seed_session(tmp_path, "20260101-130000-bb22cc", _session_events(2))
    sess_mod._cmd_session(["session", "ls"])
    out2 = capsys.readouterr().out
    assert "20260101-120000" in out2 and "20260101-130000" in out2
    # 空工作区 → 友好提示
    empty = tmp_path / "empty_ws"
    monkeypatch.setattr(sess_mod, "ROOT", empty)
    sess_mod._cmd_session(["session"])
    assert "暂无历史会话" in capsys.readouterr().out


def test_cli_session_fork_creates_new_file(tmp_path, monkeypatch, capsys):
    sess_mod = _cli_mod()
    sid = "20260101-120000-bb22cc"
    _seed_session(tmp_path, sid, _basic_events())
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    sess_mod._cmd_session(["session", "fork", sid, "--at", "3"])
    out = capsys.readouterr().out
    files = sorted((tmp_path / ".memory" / "sessions").glob("*.jsonl"))
    assert len(files) == 2
    new_id = [f.stem for f in files if f.stem != sid][0]
    assert new_id in out
    assert len(load_events(_session_path(tmp_path, new_id))) == 2   # 对齐到 call 之前


def test_cli_session_rm_deletes_file(tmp_path, monkeypatch, capsys):
    sess_mod = _cli_mod()
    sid = "20260101-120000-cc33dd"
    _seed_session(tmp_path, sid, _basic_events())
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    sess_mod._cmd_session(["session", "rm", sid[:8]])
    assert "已删除会话" in capsys.readouterr().out
    assert not _session_path(tmp_path, sid).exists()


def test_cli_session_prune_dry_run_keeps_files(tmp_path, monkeypatch, capsys):
    sess_mod = _cli_mod()
    for i in range(1, 5):
        _seed_session(tmp_path, f"2026010{i}-000000-dry{i:03d}", _session_events(i))
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    sess_mod._cmd_session(["session", "prune", "--keep", "2"])
    out = capsys.readouterr().out
    assert "dry-run" in out and "--yes" in out
    assert len(list((tmp_path / ".memory" / "sessions").glob("*.jsonl"))) == 4, "dry-run 不得删文件"


def test_cli_session_prune_yes_deletes(tmp_path, monkeypatch, capsys):
    sess_mod = _cli_mod()
    for i in range(1, 5):
        _seed_session(tmp_path, f"2026010{i}-000000-yes{i:03d}", _session_events(i))
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    sess_mod._cmd_session(["session", "prune", "--keep", "2", "--yes"])
    out = capsys.readouterr().out
    assert "已清理" in out
    assert [s["session_id"] for s in list_sessions(tmp_path)] == [
        "20260104-000000-yes004", "20260103-000000-yes003"]


def test_cli_session_resume_invokes_run_repl(tmp_path, monkeypatch, capsys):
    """resume 只 monkeypatch 断言收到 resume_session_id，绝不启动真实 REPL。"""
    sess_mod = _cli_mod()
    sid = "20260101-120000-dd44ee"
    _seed_session(tmp_path, sid, _basic_events())
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)

    captured = {}

    def fake_run_repl(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    _patch_run_repl(monkeypatch, fake_run_repl)
    sess_mod._cmd_session(["session", "resume", sid[:8]])   # 前缀匹配
    assert captured["kwargs"].get("resume_session_id") == sid
    assert captured["args"] == ()
    assert sid in capsys.readouterr().out


def test_cli_session_resume_unknown_and_ambiguous(tmp_path, monkeypatch, capsys):
    sess_mod = _cli_mod()
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    called = {"n": 0}

    def fake_run_repl(*args, **kwargs):   # pragma: no cover - 不应被调用
        called["n"] += 1

    _patch_run_repl(monkeypatch, fake_run_repl)
    sess_mod._cmd_session(["session", "resume", "不存在的id"])
    assert "未找到会话" in capsys.readouterr().out

    _seed_session(tmp_path, "20260101-000000-aa11", _session_events(1))
    _seed_session(tmp_path, "20260101-000000-aa22", _session_events(2))
    sess_mod._cmd_session(["session", "resume", "20260101-000000-aa"])
    assert "前缀不唯一" in capsys.readouterr().out
    assert called["n"] == 0, "解析失败时不得启动 REPL"


def test_cli_session_unknown_subcommand_shows_usage(capsys):
    sess_mod = _cli_mod()
    sess_mod._cmd_session(["session", "what"])
    out = capsys.readouterr().out
    assert "未知 session 子命令" in out and "用法" in out


# ── 7. AgentRunner：resume 恢复 ─────────────────────────────────────────


def test_agent_runner_restores_history_and_summary(tmp_path, monkeypatch):
    sid = "20260101-000000-run001"
    events = [
        _evt(EVENT_SESSION_START, {"active_subject": "pol", "user_input": "样本问题一"},
             eid="e1", ts="2026-01-01T00:00:01.000"),
        _evt(EVENT_USER, {"content": "样本问题一"}, eid="e2", ts="2026-01-01T00:00:02.000"),
        _evt(EVENT_ASSISTANT, {"content": "样本回答一"}, eid="e3", ts="2026-01-01T00:00:03.000"),
        _evt(EVENT_COMPACT, {"summary": SUMMARY_TEXT, "before_messages": 9, "after_messages": 4},
             eid="e4", ts="2026-01-01T00:00:04.000"),
        _evt(EVENT_USER, {"content": "样本问题二"}, eid="e5", ts="2026-01-01T00:00:05.000"),
        _evt(EVENT_ASSISTANT, {"content": "样本回答二"}, eid="e6", ts="2026-01-01T00:00:06.000"),
    ]
    _seed_session(tmp_path, sid, events)

    runner = _make_runner(tmp_path, monkeypatch, ["样本回答三"], session_id=sid)
    assert runner.run("样本问题三") == "样本回答三"

    assert runner.history[0] == {"role": "system", "content": SUMMARY_TEXT}
    assert runner.history[1:] == [
        {"role": "user", "content": "样本问题一"},
        {"role": "assistant", "content": "样本回答一"},
        {"role": "user", "content": "样本问题二"},
        {"role": "assistant", "content": "样本回答二"},
        {"role": "user", "content": "样本问题三"},
        {"role": "assistant", "content": "样本回答三"},
    ]
    got = load_events(_session_path(tmp_path, sid))
    assert [e["type"] for e in got].count(EVENT_SESSION_START) == 1, "resume 不得重复写 session_start"
    assert [e["type"] for e in got].count(EVENT_USER) == 3
    assert rebuild_history(got) == runner.history
    runner.close()


def test_agent_runner_resume_writes_no_events_before_next_run(tmp_path, monkeypatch):
    """仅触发恢复（未 run）时：history 已恢复、事件数不变、session_end 之前字节不变。"""
    sid = "20260101-000000-run002"
    _seed_session(tmp_path, sid, _session_events(1))
    before = _sha256(_session_path(tmp_path, sid))

    runner = _make_runner(tmp_path, monkeypatch, ["样本回答"], session_id=sid)
    runner._ensure_session_log()
    assert runner.history == [
        {"role": "user", "content": "样本问题1"},
        {"role": "assistant", "content": "样本回答1"},
    ]
    assert runner._session_started is True
    assert runner._session_start_logged is True
    assert _sha256(_session_path(tmp_path, sid)) == before
    runner.close()   # close 写 session_end（预期行为），放在字节断言之后


def test_agent_runner_resume_missing_file_starts_fresh(tmp_path, monkeypatch):
    sid = "20260101-000000-run003"
    runner = _make_runner(tmp_path, monkeypatch, ["样本回答一"], session_id=sid)
    assert runner.run("样本问题一") == "样本回答一"
    got = load_events(_session_path(tmp_path, sid))
    assert [e["type"] for e in got][:2] == [EVENT_SESSION_START, EVENT_USER]
    assert [e["type"] for e in got].count(EVENT_SESSION_START) == 1
    runner.close()


def test_agent_runner_without_session_id_unchanged(tmp_path, monkeypatch):
    """不传 session_id（既有行为）：新建会话文件、首 run 写 session_start。"""
    runner = _make_runner(tmp_path, monkeypatch, ["样本回答一"])
    assert runner.run("样本问题一") == "样本回答一"
    assert runner._session_log is not None
    got = load_events(runner._session_log.path)
    assert [e["type"] for e in got].count(EVENT_SESSION_START) == 1
    runner.close()


# ── 8. 常量单源 / 命令注册 / 安全模式 / GUI 复用 ────────────────────────


def test_compact_summary_prefix_single_source():
    from tools.agent import compaction
    from tools.agent import loop as loop_mod

    assert compaction.COMPACT_SUMMARY_PREFIX == "【历史上下文压缩摘要"
    assert loop_mod.COMPACT_SUMMARY_PREFIX is compaction.COMPACT_SUMMARY_PREFIX
    assert not hasattr(loop_mod, "_COMPACT_SUMMARY_PREFIX"), "loop.py 不得保留私有副本"


def test_render_summary_still_starts_with_prefix():
    from tools.agent.compaction import COMPACT_SUMMARY_PREFIX, render_summary

    text = render_summary({"goal": ["样本目标"]})
    assert text.startswith(COMPACT_SUMMARY_PREFIX + " (Context Compaction)】:")
    assert "Context Compaction" in text


def test_session_command_registered_and_safe_mode():
    from tools.cli import dispatch
    from tools.cli.shared import detect_safe_mode_violation

    dispatch._init_all_commands()
    cmd = dispatch.get_command("session")
    assert cmd is not None, "session 命令必须已注册"
    assert cmd.write is True
    # 只读子操作放行；写子操作在 safe 模式被拦
    assert detect_safe_mode_violation(["session", "ls"]) is None
    assert detect_safe_mode_violation(["session"]) is None
    assert detect_safe_mode_violation(["session", "resume", "x"]) is not None
    assert detect_safe_mode_violation(["session", "fork", "x"]) is not None
    assert detect_safe_mode_violation(["session", "rm", "x"]) is not None
    assert detect_safe_mode_violation(["session", "prune"]) is not None


def test_gui_worker_shares_session_id(tmp_path, monkeypatch):
    """同一 GUI 会话的所有消息复用同一个 session_id（不落盘、跨实例稳定）。"""
    pytest.importorskip("PySide6")
    from tools.gui.workers import agent_worker as aw

    monkeypatch.setattr(aw, "ROOT", tmp_path)
    monkeypatch.setattr(aw.AgentWorker, "_shared_session_id", None)
    sid1 = aw.AgentWorker._resolve_session_id()
    sid2 = aw.AgentWorker._resolve_session_id()
    assert sid1 and sid1 == sid2
    assert not (tmp_path / ".memory").exists(), "只生成 id，懒创建不碰磁盘"
