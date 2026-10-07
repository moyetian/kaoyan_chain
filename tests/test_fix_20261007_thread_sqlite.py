# -*- coding: utf-8 -*-
"""R4 #207 修复回归：GUI 本地检索跨线程 SQLite 失败 + 「检索异常」不得伪装成「知识库为空」。

背景（R4 全矩阵仿真，三副本复现）：
- ``knowledge_store.py`` 全局单例 + ``sqlite3.connect()`` 默认把连接绑定到创建
  线程；GUI 的「建索引」与「本地检索」各自在独立 QThread 里执行 → 跨线程复用
  同一连接 → ``ProgrammingError: SQLite objects created in a thread can only
  be used in that same thread``；
- ``hybrid.py`` 词法分支把该异常吞成 warning → 返回空列表 → 界面提示
  「知识库是空的」，把「检索坏了」伪装成「没搜到」。

修复：连接改为**线程本地**（每线程各自持有连接）+ 词法/向量分支失败显式上报
（``LexicalUnavailable`` / ``search_by_vector`` 不再吞查询异常 /
``SearchOutcome.lexical_failed`` 供渲染层区分「没搜到」与「没搜成」）。

阴性对照（修复前跑本文件）：
- test_cross_thread_search_after_singleton_created_in_another_thread → 0 命中
- test_gui_rag_service_from_worker_thread_reports_hits → 输出「知识库是空的」
- test_lexical_failure_is_reported_not_silent → degrade_reason 为空/无关
- test_search_by_vector_propagates_query_failure → 不抛（返回 []）
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

import pytest

import tools.search.hybrid as hybrid
import tools.search.knowledge_store as ks_mod


def _chunks():
    return [
        ks_mod.Chunk(
            id="c1",
            text="教育学原理：教育的本质是有目的地培养人的社会活动",
            source="04-专业课/教育学.md",
            metadata={},
        ),
        ks_mod.Chunk(
            id="c2",
            text="马克思主义基本原理：实践是检验真理的唯一标准",
            source="04-专业课/马原.md",
            metadata={},
        ),
    ]


def _isolate_db(tmp_path, monkeypatch):
    """把单例与默认库路径都指到 tmp（防止串扰真实工作区）。"""
    db = tmp_path / "data" / "knowledge" / "embeddings.db"
    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks_mod, "_STORE", None)
    return db


# ─────────── 1. 跨线程（GUI 时序：索引线程建单例 → 检索线程用） ───────────

def test_cross_thread_search_after_singleton_created_in_another_thread(
        tmp_path, monkeypatch):
    """索引 worker 建单例 → 检索 worker 必须能命中（修复前跨线程 0 条）。"""
    _isolate_db(tmp_path, monkeypatch)

    errors = []
    hits = []

    def _index_worker():
        try:
            store = ks_mod.get_knowledge_store()
            for c in _chunks():
                assert store.add_chunk(c) is True
        except Exception as e:  # pragma: no cover - 修复前也不抛（异常被吞）
            errors.append(("index", repr(e)))

    def _search_worker():
        try:
            outcome = hybrid.search_with_diagnostics("教育学", top_k=5)
            hits.extend(outcome.results)
        except Exception as e:  # pragma: no cover
            errors.append(("search", repr(e)))

    t1 = threading.Thread(target=_index_worker)
    t1.start()
    t1.join()
    t2 = threading.Thread(target=_search_worker)
    t2.start()
    t2.join()

    assert not errors, f"线程内异常: {errors}"
    assert [h.chunk_id for h in hits][:1] == ["c1"], (
        "跨线程检索必须命中（修复前此处 0 条：连接绑定在索引线程）")


def test_store_conn_is_thread_local(tmp_path, monkeypatch):
    """同一 store 对象：本线程内复用同一连接；跨线程各自持有不同连接。"""
    _isolate_db(tmp_path, monkeypatch)
    store = ks_mod.get_knowledge_store()

    assert store.conn is store.conn, "同一线程内连接必须复用（不得每次新建）"

    seen = {}

    def _other_thread():
        seen["conn"] = store.conn

    t = threading.Thread(target=_other_thread)
    t.start()
    t.join()

    assert seen["conn"] is not store.conn, (
        "跨线程必须使用各自独立的连接（修复前是同一个连接对象）")


# ─────────── 2. GUI 服务路径（QThread 里调 services.rag_search） ───────────

def test_gui_rag_service_from_worker_thread_reports_hits(tmp_path, monkeypatch):
    """GUI「本地检索」动作在 worker 线程执行时必须搜到内容。

    修复前：worker 线程跨线程复用主线程连接 → 0 条 + 误导提示「知识库是空的」。
    """
    _isolate_db(tmp_path, monkeypatch)

    # 主线程先建库入库（等价 GUI 先点「建索引」）
    store = ks_mod.get_knowledge_store()
    for c in _chunks():
        assert store.add_chunk(c) is True

    from tools.gui.services.actions import rag_search

    out_box = []

    def _worker():
        out_box.append(rag_search("教育学", top_k=5))

    t = threading.Thread(target=_worker)
    t.start()
    t.join()

    out = out_box[0]
    assert "教育的本质" in out, f"检索必须命中；实际输出:\n{out}"
    assert "知识库是空的" not in out, "命中时不得出现「知识库是空的」提示"


def test_intel_worker_qthread_rag_search_end_to_end(tmp_path, monkeypatch):
    """Qt 层端到端：真实 IntelTaskWorker（QThread）执行 rag_search 必须命中。

    R4 #207 的现场就是这个类：GUI「本地检索」卡片的动作在它的 run() 里、
    于独立 QThread 中执行。修复前跨线程复用主线程连接 → 0 条。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover - 无 Qt 环境
        pytest.skip("PySide6 不可用")
    app = QApplication.instance() or QApplication([])

    _isolate_db(tmp_path, monkeypatch)
    store = ks_mod.get_knowledge_store()   # 主线程先建单例（等价先点「建索引」）
    for c in _chunks():
        assert store.add_chunk(c) is True

    from tools.gui.workers.intel_worker import IntelTaskWorker

    worker = IntelTaskWorker("rag_search", tmp_path, {"query": "教育学"})
    outs, errs = [], []
    worker.finished_signal.connect(lambda out, saved: outs.append(out))
    worker.error_signal.connect(errs.append)

    worker.start()
    assert worker.wait(30000), "worker 未在 30s 内结束"
    # 排空跨线程排队信号（QThread → 主线程为 QueuedConnection）
    deadline = time.monotonic() + 3.0
    while not outs and not errs and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert not errs, f"worker 报错: {errs}"
    assert outs, "worker 未产出结果"
    assert "教育的本质" in outs[0], f"QThread 检索必须命中；实际输出:\n{outs[0]}"


# ─────────── 3. 「检索失败」必须显式上报，不得静默成空结果 ───────────

class _BoomConn:
    """execute 即抛跨线程 ProgrammingError 的连接替身。"""

    def execute(self, *a, **kw):
        raise sqlite3.ProgrammingError("SQLite objects created in a thread can only be used in that same thread")


class _BoomStore:
    """词法遍历必失败的知识库替身（has_vector=True 走真实分支判定）。"""

    has_vector = True
    conn = _BoomConn()

    def search_by_vector(self, **kw):  # pragma: no cover - 本用例走不到
        return []

    def get_chunk(self, cid):  # pragma: no cover
        return None


def test_lexical_failure_is_reported_not_silent(tmp_path, monkeypatch):
    """词法分支失败必须进 degrade_reason（修复前：吞异常 → 空结果无诊断）。"""
    # [2026-10-07 跨环境修复] hybrid 只读守卫（DEFAULT_DB_PATH 不存在 → 提前
    # 返回「本地知识库尚未建立」）会在 mock 之前拦截：主仓库恰有真实库故绿，
    # 副本/CI 无库必红。建一个空库放行守卫（与下方 L226 用例同款）。
    db = tmp_path / "kb.db"
    ks_mod.KnowledgeStore(db).close()
    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks_mod, "get_knowledge_store", lambda: _BoomStore())

    outcome = hybrid.search_with_diagnostics("教育学", top_k=5, enable_vector=False)

    assert outcome.results == []
    assert outcome.degraded is True, "词法失败必须标记降级"
    assert "词法检索失败" in outcome.degrade_reason
    assert "ProgrammingError" in outcome.degrade_reason, (
        "原因须携带真实异常类型，不能只说『已降级』")
    # 阴性对照：词法已失败时不得再声称「本次为纯词法检索」
    assert "本次为纯词法检索" not in outcome.degrade_reason
    assert outcome.lexical_failed is True


def test_rag_output_distinguishes_error_from_empty(tmp_path, monkeypatch):
    """0 条结果 + 词法失败：输出必须给「检索未完成」而非「知识库是空的」。"""
    db = tmp_path / "kb.db"
    ks_mod.KnowledgeStore(db).close()          # 让 DEFAULT_DB_PATH.exists() 为真
    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks_mod, "get_knowledge_store", lambda: _BoomStore())
    # 向量分支打桩为「干净地 0 命中」，把变量收敛到词法失败本身
    monkeypatch.setattr(hybrid, "_vector_search", lambda *a, **kw: [])

    import io
    from contextlib import redirect_stdout
    from tools.cli.commands.search import run_rag_search

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = run_rag_search("教育学")
    out = buf.getvalue()

    assert code == 0
    assert "词法检索失败" in out, f"失败原因必须可见；实际输出:\n{out}"
    assert "检索未完成" in out, "0 条且词法失败时必须提示『检索未完成』"
    assert "知识库是空的" not in out, (
        "检索失败不得伪装成『知识库是空的』（这正是 #207 的误导文案）")


def test_genuine_no_match_still_gets_ingest_hint(tmp_path, monkeypatch):
    """阳性对照：词法正常但真的没命中，仍应给「先 ingest 再建索引」引导。

    防止把上一条修成「任何 0 命中都不提示建库引导」的过度拦截。
    """
    db = tmp_path / "kb.db"
    store = ks_mod.KnowledgeStore(db)
    for c in _chunks():
        store.add_chunk(c)
    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks_mod, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(hybrid, "_vector_search", lambda *a, **kw: [])

    import io
    from contextlib import redirect_stdout
    from tools.cli.commands.search import run_rag_search

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = run_rag_search("量子力学")
    out = buf.getvalue()

    assert code == 0
    assert "没有匹配的片段" in out
    assert "知识库是空的" in out, "真·未命中时保留建库引导"
    assert "检索未完成" not in out, "正常检索不得误报「检索未完成」"


# ─────────── 4. 向量分支：查询失败不再吞成「0 命中」 ───────────

def test_vector_query_failure_is_reported(tmp_path, monkeypatch):
    """向量查询抛错 → degrade_reason 必须带异常类型（修复前被吞成空列表）。"""
    # [2026-10-07 跨环境修复] 同上一用例：先放行只读守卫，再让 mock 接管。
    db = tmp_path / "kb.db"
    ks_mod.KnowledgeStore(db).close()
    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks_mod, "get_knowledge_store",
                        lambda: _EmptyStore(has_vector=True))
    monkeypatch.setattr(hybrid, "_vector_search",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            sqlite3.OperationalError("no such table: vec_chunks")))

    outcome = hybrid.search_with_diagnostics("教育学", top_k=5)

    assert outcome.degraded is True
    assert "向量检索异常" in outcome.degrade_reason
    assert "OperationalError" in outcome.degrade_reason


class _EmptyStore:
    """词法干净 0 命中、向量可控的知识库替身。"""

    def __init__(self, has_vector=False):
        self.has_vector = has_vector

    class _Conn:
        def execute(self, *a, **kw):
            return iter(())      # 空游标：0 行

    conn = _Conn()

    def search_by_vector(self, **kw):  # pragma: no cover - 被 monkeypatch 覆盖
        return []

    def get_chunk(self, cid):  # pragma: no cover
        return None


def test_search_by_vector_propagates_query_failure(tmp_path):
    """真库：向量查询失败必须抛错（修复前吞掉返回 []，上层无法区分「0 命中」与「查询坏了」）。"""
    store = ks_mod.KnowledgeStore(tmp_path / "kb.db")
    # 模拟扩展已加载但向量表缺失（查询必然失败）
    store.has_vector = True
    with pytest.raises(sqlite3.Error):
        store.search_by_vector([0.0] * 384, top_k=3)


# ─────────── 5. 护栏：单例语义与 API 不回归 ───────────

def test_get_knowledge_store_still_singleton(tmp_path, monkeypatch):
    """``get_knowledge_store()`` 同线程内仍是同一实例（既有调用方零改动）。"""
    _isolate_db(tmp_path, monkeypatch)
    a = ks_mod.get_knowledge_store()
    b = ks_mod.get_knowledge_store()
    assert a is b


def test_hybrid_search_api_unchanged(tmp_path, monkeypatch):
    """``hybrid_search()`` / ``search()`` 签名与返回类型不变（回归护栏）。"""
    _isolate_db(tmp_path, monkeypatch)
    store = ks_mod.get_knowledge_store()
    for c in _chunks():
        store.add_chunk(c)

    results = hybrid.hybrid_search("教育学", top_k=3)
    assert isinstance(results, list)
    assert all(isinstance(r, hybrid.SearchResult) for r in results)
    assert [r.chunk_id for r in results][:1] == ["c1"]
