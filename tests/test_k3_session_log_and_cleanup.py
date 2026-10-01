# -*- coding: utf-8 -*-
"""K3 批次回归 · session_log 降级重放 + 小项清理。

本文件锁定（不得回退）：
1. **session_log（有界内存队列 + 可恢复重放）**：
   - 首次写失败不抛、``append`` 仍返回事件 id；警告只打一次（含缓存条数）；
   - 降级期事件进有界队列（``maxlen=512``）；恢复后按序补写
     （读回 JSONL 断言条数与顺序）；
   - ``close()`` best-effort 最后 flush 一次，且不凭空创建从未建立的文件；
2. **knowledge_store**：连接后 ``PRAGMA journal_mode`` 为 ``wal``；
3. **material_ingestion**：未知科目 ``raise ValueError``（信息含支持科目键，
   ingest 入口转为可读失败结果）；已知科目目录缺失时打印 ``[warn]`` 回落提示；
   题卡元信息区带「科目归属」标记；
4. **fsrs_scheduler**：模块可导入、``compute_next_interval`` 行为不变、
   ``FSRSScheduler`` 死类不再存在；
5. **school_scout.find_school_in_db**：详情库命中返回结构不变、未收录返回 None。

全程合成数据 + ``tmp_path`` 隔离，绝不写真实工作区 / 真实 ``.memory``。
"""

import builtins
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tools.fsrs_scheduler as fsrs_mod  # noqa: E402
from tools.agent.session_log import (  # noqa: E402
    EVENT_ASSISTANT,
    EVENT_USER,
    SessionLog,
    load_events,
)
from tools.fsrs_scheduler import compute_next_interval, make_scheduler  # noqa: E402
from tools.search.knowledge_store import KnowledgeStore  # noqa: E402
from tools.skills.material_ingestion import (  # noqa: E402
    MaterialIngestionPipeline,
    QuestionChunk,
)
from tools.skills.school_db import TARGET_SCHOOLS_DB  # noqa: E402
from tools.skills.school_scout import find_school_in_db  # noqa: E402


def _boom_open(*args, **kwargs):
    """模拟磁盘满 / 权限不足的 open。"""
    raise OSError(28, "No space left on device")


# ── 1. session_log：首次写失败不抛 + 警告只一次 ─────────────────────────


def test_first_write_failure_does_not_raise_and_warns_once(tmp_path, monkeypatch, capsys):
    log = SessionLog(workspace_root=tmp_path, session_id="k3-first-fail")
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", _boom_open)
    try:
        eid = log.append(EVENT_USER, {"content": "样本事件一"})
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    assert isinstance(eid, str) and eid, "写失败也必须返回事件 id（调用方不必分支）"
    assert log._degraded is True
    assert len(log._pending) == 1

    err = capsys.readouterr().err
    assert err.count("[warn] 会话日志写入失败") == 1, "写入失败警告必须只打一次"
    assert "已缓存 1 条" in err, "警告应含待补写条数"

    # 首次写就失败、文件从未建立 → close 不凭空创建日志文件（懒创建契约）
    log.close()
    assert not log.path.exists()


# ── 2. session_log：恢复后按序补写（每 20 次降级 append 自动重放一次） ──


def test_recovery_replays_pending_in_order(tmp_path, monkeypatch):
    log = SessionLog(workspace_root=tmp_path, session_id="k3-replay")
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", _boom_open)
    try:
        ids = [log.append(EVENT_USER, {"content": f"样本事件{i:02d}"})
               for i in range(1, 20)]      # 第 1 次失败 + 18 次降级入队
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert log._degraded is True
    assert len(log._pending) == 19

    # 第 20 次降级 append 触发重放：此时磁盘已恢复，按序补写全部缓存
    ids.append(log.append(EVENT_ASSISTANT, {"content": "样本事件20"}))
    assert log._degraded is False, "重放成功后必须解除降级"
    assert len(log._pending) == 0

    events = load_events(log.path)
    assert [e["payload"]["content"] for e in events] == \
        [f"样本事件{i:02d}" for i in range(1, 21)], "补写必须按序、一条不丢"
    assert [e["id"] for e in events] == ids, "落盘事件 id 必须与 append 返回值一致"
    log.close()


# ── 3. session_log：队列有界（maxlen=512） ──────────────────────────────


def test_pending_queue_is_bounded(tmp_path, monkeypatch):
    log = SessionLog(workspace_root=tmp_path, session_id="k3-bounded")
    monkeypatch.setattr(builtins, "open", _boom_open)
    for i in range(1, 521):
        log.append(EVENT_USER, {"content": f"样本{i}"})

    assert log._pending.maxlen == 512, "待补写队列必须有界（防内存无界膨胀）"
    assert len(log._pending) == 512
    # 只保留最近 512 条：最早 8 条被挤出，队首应为第 9 条
    assert '"content": "样本9"' in log._pending[0]
    log.close()


# ── 4. session_log：close best-effort flush（文件已存在时） ─────────────


def test_close_flushes_pending_when_file_exists(tmp_path, monkeypatch):
    # 先成功落盘一次，建立会话文件
    log1 = SessionLog(workspace_root=tmp_path, session_id="k3-close-flush")
    log1.append(EVENT_USER, {"content": "样本事件一"})
    log1.close()
    assert log1.path.exists()

    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", _boom_open)
    log2 = SessionLog(workspace_root=tmp_path, session_id="k3-close-flush")
    try:
        log2.append(EVENT_ASSISTANT, {"content": "样本事件二"})
        log2.append(EVENT_USER, {"content": "样本事件三"})
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    assert log2._degraded is True and len(log2._pending) == 2

    log2.close()          # close 时 best-effort 重放一次
    assert log2._degraded is False
    log2.close()          # 幂等：重复 close 不得重复补写

    events = load_events(log1.path)
    assert [e["payload"]["content"] for e in events] == \
        ["样本事件一", "样本事件二", "样本事件三"]


# ── 5. session_log：跨恢复的警告仍只打一次 ──────────────────────────────


def test_warning_printed_only_once_across_recovery(tmp_path, monkeypatch, capsys):
    log = SessionLog(workspace_root=tmp_path, session_id="k3-warn-once")
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", _boom_open)
    try:
        log.append(EVENT_USER, {"content": "样本一"})
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    for i in range(2, 21):                # 第 20 次降级 append 触发成功重放
        log.append(EVENT_USER, {"content": f"样本{i}"})
    assert log._degraded is False

    # 模拟磁盘再次故障（句柄失效 + open 抛错）→ 不得重复警告
    log._close_handle()
    monkeypatch.setattr(builtins, "open", _boom_open)
    log.append(EVENT_USER, {"content": "样本二一"})
    assert log._degraded is True

    err = capsys.readouterr().err
    assert err.count("[warn] 会话日志写入失败") == 1
    assert err.count("[info] 会话日志写入已恢复") == 1
    log.close()


# ── 6. knowledge_store：WAL 日志模式 ────────────────────────────────────


def test_knowledge_store_enables_wal(tmp_path):
    store = KnowledgeStore(db_path=tmp_path / "kb.db")
    try:
        mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal", f"journal_mode 应为 wal，实际 {mode!r}"
    finally:
        store.conn.close()


# ── 7. material_ingestion：未知科目显式报错 / 目录缺失 warn ─────────────


def test_unknown_subject_raises_with_supported_keys(tmp_path):
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    with pytest.raises(ValueError) as ei:
        p._resolve_subject_dir("物理")
    msg = str(ei.value)
    assert "支持的科目键" in msg
    assert "pro" in msg and "math" in msg and "eng" in msg


def test_unknown_subject_ingest_returns_readable_error(tmp_path):
    """ingest 入口（CLI/REPL/GUI/Agent 工具共用）不得裸崩，返回可读 msg。"""
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    doc = "\n".join(f"{i}. 简述第 {i} 个核心考点及其现实意义。" for i in range(1, 4))
    res = p.ingest_text(doc, subject="物理", source_name="样本来源", llm_enrich=False)
    assert res["success"] is False
    assert "未知科目" in res["msg"] and "支持的科目键" in res["msg"]


def test_known_subject_missing_dir_falls_back_with_warn(tmp_path, capsys):
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    target = p._resolve_subject_dir("eng")          # tmp_path 下无 02-英语
    assert target == tmp_path / "04-专业课"
    out = capsys.readouterr().out
    assert "[warn] 目标目录不存在，已回落 04-专业课" in out


def test_known_subject_existing_dir_no_warn(tmp_path, capsys):
    (tmp_path / "01-数学").mkdir()
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    assert p._resolve_subject_dir("math") == tmp_path / "01-数学"
    assert "[warn]" not in capsys.readouterr().out


def test_question_card_has_subject_marker(tmp_path):
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    chunk = QuestionChunk(number=1, q_type="short", score=10,
                          stem="简述样本考点及其现实意义。", source="样本真题")
    card = p.format_question_card(chunk, subject="pro", llm_enrich=False)
    assert "- **【科目归属】**：`04-专业课`" in card
    # 既有结构不受影响
    assert "#### 1. 试题原题" in card
    assert "简述样本考点及其现实意义。" in card
    # 直接调用传未知键（不走入库门禁）不崩、原样展示
    card2 = p.format_question_card(chunk, subject="物理", llm_enrich=False)
    assert "- **【科目归属】**：`物理`" in card2


# ── 8. fsrs_scheduler：死类删除、纯函数行为不变 ─────────────────────────


def test_fsrs_dead_class_removed():
    assert not hasattr(fsrs_mod, "FSRSScheduler"), "FSRSScheduler 死类应已删除"
    assert "FSRSScheduler" not in fsrs_mod.__all__
    assert callable(make_scheduler)
    assert callable(compute_next_interval)


def test_compute_next_interval_behavior_unchanged():
    today = date(2026, 1, 1)
    # 与模块 docstring 的校准表逐档一致（enable_fuzzing=False → 完全可复现）
    assert compute_next_interval(0, "good", today) == (1, date(2026, 1, 3), 2)
    assert compute_next_interval(0, "again", today) == (0, date(2026, 1, 2), 1)
    stage, due, days = compute_next_interval(3, "easy", today)
    assert (stage, days) == (4, 265)
    assert due == today + timedelta(days=265)
    # 同输入结果完全可复现（不变式 ③）
    assert compute_next_interval(3, "easy", today) == (stage, due, days)


# ── 9. school_scout：find_school_in_db 契约不变 ─────────────────────────


def test_find_school_in_db_hit_structure_unchanged():
    hit = find_school_in_db("华中科技大学")
    assert hit is not None
    assert set(hit.keys()) == {"name", "data"}
    assert hit["name"] == "华中科技大学"
    assert hit["data"] == TARGET_SCHOOLS_DB["华中科技大学"]

    # 简称 / 别名（registry 可解析或库内 alias 命中）→ 收敛到同一库内条目
    alias_hit = find_school_in_db("华科")
    assert alias_hit is not None and alias_hit["name"] == "华中科技大学"

    # 未走精确的模糊子串输入仍命中（既有行为）
    fuzzy_hit = find_school_in_db("华中科技")
    assert fuzzy_hit is not None and fuzzy_hit["name"] == "华中科技大学"


def test_find_school_in_db_unlisted_returns_none():
    assert find_school_in_db("某某不存在的高校XYZ") is None
    assert find_school_in_db("   ") is None
