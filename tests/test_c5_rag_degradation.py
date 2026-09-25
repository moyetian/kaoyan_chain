# -*- coding: utf-8 -*-
"""C5 回归测试：RAG 显式降级 + 用户可达检索入口。

验收口径（升级规划 C5）：
    1. 无模型时检索**可用**且有**提示**；
    2. 有模型路径不变。

背景（改造前的事实）：
    ``tools/search/hybrid.py`` 的混合检索链路**没有任何用户可达入口** ——
    唯一调用方 ``cli_integration.py`` 自身也是孤儿模块。因此「向量不可用时
    自动降级」这条路径既没人走也没人看见：``_vector_search`` 把一切异常吞成
    ``[]``，调用方无法区分「模型缺失」与「真的没搜到」。本文件锁定修复后的
    三条性质：降级**可诊断**、原因**如实**、入口**可达**。
"""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import hybrid  # noqa: E402
from tools.search import knowledge_store as ks_mod  # noqa: E402
from tools.search.knowledge_store import Chunk  # noqa: E402


# ─────────────────────────── 测试替身 ───────────────────────────

class _FakeConn:
    """只实现 ``_lexical_search`` 用到的 ``SELECT id, text FROM chunks``。"""

    def __init__(self, chunks):
        self._chunks = chunks

    def execute(self, sql, params=()):
        if "FROM chunks" in sql:
            return [(cid, c.text) for cid, c in self._chunks.items()]
        raise AssertionError(f"未预期的 SQL: {sql}")


class _FakeStore:
    """最小知识库替身：可控 has_vector / 片段表 / 向量命中。"""

    def __init__(self, has_vector=False, chunks=None, vector_hits=None):
        self.has_vector = has_vector
        self._chunks = chunks or {}
        self._vector_hits = vector_hits or []
        self.conn = _FakeConn(self._chunks)

    def get_chunk(self, chunk_id):
        return self._chunks.get(chunk_id)

    def search_by_vector(self, query_embedding=None, top_k=20, source_filter=None):
        return list(self._vector_hits[:top_k])


def _chunk(cid: str, text: str, source: str = "04-专业课/马原.md") -> Chunk:
    return Chunk(id=cid, text=text, source=source, metadata={})


def _patch_store(monkeypatch, store):
    monkeypatch.setattr(ks_mod, "get_knowledge_store", lambda: store)


CHUNKS = {
    "c1": _chunk("c1", "剩余价值是雇佣工人所创造的被资本家无偿占有的超过劳动力价值的价值"),
    "c2": _chunk("c2", "矛盾的普遍性与特殊性是共性与个性的关系"),
    "c3": _chunk("c3", "实践是检验真理的唯一标准", source="04-专业课/毛中特.md"),
}


# ─────────────── 1. 无模型时：检索可用 + 有提示 ───────────────

def test_no_vector_extension_is_reported_degraded(monkeypatch):
    """sqlite-vec 未加载 → degraded=True，且原因点名到具体扩展。"""
    _patch_store(monkeypatch, _FakeStore(has_vector=False, chunks=CHUNKS))

    outcome = hybrid.search_with_diagnostics("剩余价值", top_k=5)

    assert outcome.degraded is True
    assert "sqlite-vec" in outcome.degrade_reason, "降级原因必须点名真实缺件，不能只说『已降级』"


def test_search_still_usable_when_degraded(monkeypatch):
    """验收第 1 条前半句：无向量时检索**可用**（词法分支照常出结果）。"""
    _patch_store(monkeypatch, _FakeStore(has_vector=False, chunks=CHUNKS))

    outcome = hybrid.search_with_diagnostics("剩余价值", top_k=5)

    assert outcome.lexical_count >= 1, "纯词法分支必须仍能召回"
    assert [r.chunk_id for r in outcome.results][0] == "c1", "TF 命中最高者应排第一"


def test_degrade_flag_stamped_on_every_result(monkeypatch):
    """每条结果都带降级标记（供只拿到 List[SearchResult] 的调用方感知）。"""
    _patch_store(monkeypatch, _FakeStore(has_vector=False, chunks=CHUNKS))

    results = hybrid.search("剩余价值", top_k=5)

    assert results, "前置条件：应有词法命中"
    assert all(r.degraded for r in results)
    assert all(r.degrade_reason for r in results)


def test_zero_hits_still_carries_diagnosis(monkeypatch):
    """0 条命中时诊断不能丢 —— 这正是 SearchOutcome 存在的理由。"""
    _patch_store(monkeypatch, _FakeStore(has_vector=False, chunks={}))

    outcome = hybrid.search_with_diagnostics("绝不存在的词", top_k=5)

    assert outcome.results == []
    assert outcome.degraded is True
    assert outcome.degrade_reason, "0 条结果时更要说清是『没搜到』还是『降级了』"


def test_lexical_branch_falls_back_to_lexical_search_only(monkeypatch):
    """降级时向量分支根本没被调用（不是调用了再丢掉结果）。"""
    store = _FakeStore(has_vector=False, chunks=CHUNKS)

    def _boom(*a, **kw):
        raise AssertionError("has_vector=False 时不应进入向量分支")

    _patch_store(monkeypatch, store)
    monkeypatch.setattr(hybrid, "_vector_search", _boom)

    hybrid.search_with_diagnostics("剩余价值", top_k=5)   # 不抛即通过


# ─────────────────── 2. 原因如实（不误报） ───────────────────

def test_explicit_disable_reason_does_not_blame_extension():
    """调用方显式关闭向量分支时，不得谎称『扩展未加载』。

    阴性对照：若把原因写死成扩展缺失，本用例会失败。
    """
    outcome = hybrid._run_hybrid(
        query="剩余价值", top_k=5, enable_vector=False,
        forced_reason="",
    )

    assert outcome.degraded is True
    assert "enable_vector=False" in outcome.degrade_reason
    assert "sqlite-vec" not in outcome.degrade_reason


def test_forced_reason_is_reported_verbatim(monkeypatch):
    """外部探明的降级原因原样上报，不被『调用方关闭』这类泛化文案覆盖。"""
    _patch_store(monkeypatch, _FakeStore(has_vector=False, chunks=CHUNKS))

    outcome = hybrid.search_with_diagnostics("剩余价值", top_k=5)

    assert "sqlite-vec" in outcome.degrade_reason
    assert "enable_vector=False" not in outcome.degrade_reason


def test_vector_unavailable_exception_reason_propagates(monkeypatch):
    """向量分支抛 VectorUnavailable → 其消息（人话原因）进入 degrade_reason。"""
    _patch_store(monkeypatch, _FakeStore(has_vector=True, chunks=CHUNKS))

    def _raise(*a, **kw):
        raise hybrid.VectorUnavailable("ONNX 模型缺失 data/models/bge-small-zh-v1.5.onnx")

    monkeypatch.setattr(hybrid, "_vector_search", _raise)

    outcome = hybrid.search_with_diagnostics("剩余价值", top_k=5)

    assert outcome.degraded is True
    assert "ONNX 模型缺失" in outcome.degrade_reason
    assert outcome.lexical_count >= 1, "向量挂了也要保住词法结果（可用性优先）"


def test_unexpected_vector_exception_keeps_type_name(monkeypatch):
    """非预期异常也要带上异常类型，便于定位（不能只写『失败』）。"""
    _patch_store(monkeypatch, _FakeStore(has_vector=True, chunks=CHUNKS))

    def _raise(*a, **kw):
        raise ValueError("维度不匹配")

    monkeypatch.setattr(hybrid, "_vector_search", _raise)

    outcome = hybrid.search_with_diagnostics("剩余价值", top_k=5)

    assert outcome.degraded is True
    assert "ValueError" in outcome.degrade_reason
    assert "维度不匹配" in outcome.degrade_reason


def test_vector_search_raises_when_encode_returns_none(monkeypatch):
    """``encode_text`` 返回 None 时必须抛 VectorUnavailable（不再静默吞成 []）。"""
    from tools.search import vector as vector_mod

    monkeypatch.setattr(vector_mod, "encode_text", lambda text: None)

    with pytest.raises(hybrid.VectorUnavailable) as ei:
        hybrid._vector_search("剩余价值")

    assert "模型" in str(ei.value)


# ─────────────────── 3. 有模型路径不变 ───────────────────

def test_healthy_vector_path_is_not_degraded(monkeypatch):
    """验收第 2 条：向量可用时行为不变 —— 不降级、双分支都参与融合。"""
    _patch_store(monkeypatch, _FakeStore(
        has_vector=True, chunks=CHUNKS, vector_hits=[("c2", 0.91), ("c1", 0.80)]))

    def _fake_vector(*a, **kw):
        return [("c2", 0.91), ("c1", 0.80)]

    monkeypatch.setattr(hybrid, "_vector_search", _fake_vector)

    outcome = hybrid.search_with_diagnostics("矛盾", top_k=5)

    assert outcome.degraded is False
    assert outcome.degrade_reason == ""
    assert outcome.vector_count == 2
    assert all(r.degraded is False for r in outcome.results)
    # 双分支命中的 c1/c2 都应拿到 vector_rank
    by_id = {r.chunk_id: r for r in outcome.results}
    assert by_id["c2"].vector_rank == 0


def test_hybrid_search_signature_unchanged(monkeypatch):
    """``hybrid_search()`` 仍返回 List[SearchResult]（既有调用方零改动）。"""
    _patch_store(monkeypatch, _FakeStore(has_vector=False, chunks=CHUNKS))

    results = hybrid.hybrid_search("剩余价值", top_k=3)

    assert isinstance(results, list)
    assert all(isinstance(r, hybrid.SearchResult) for r in results)


def test_rrf_score_unchanged():
    """RRF 打分未被本次改造动到（回归护栏）。"""
    assert hybrid.rrf_score([0, 2]) == pytest.approx(1 / 60 + 1 / 62)
    assert hybrid.rrf_score([5, -1]) == pytest.approx(1 / 65)


# ───────────── 4. 真实 SQLite 路径（非替身） ─────────────

def test_real_store_lexical_recall(tmp_path, monkeypatch):
    """真库直连：建库 → 入库 → 词法召回。锁住替身测不出的 SQL 契约。"""
    db = tmp_path / "kb.db"
    store = ks_mod.KnowledgeStore(db)
    for c in CHUNKS.values():
        assert store.add_chunk(c) is True
    _patch_store(monkeypatch, store)

    outcome = hybrid.search_with_diagnostics("剩余价值", top_k=5)

    assert [r.chunk_id for r in outcome.results][:1] == ["c1"]
    assert outcome.results[0].source.endswith("马原.md")
    # 本机没有 sqlite-vec 时必然降级 —— 但不影响上面的召回断言
    assert isinstance(outcome.degraded, bool)


def test_source_filter_is_passed_through(tmp_path, monkeypatch):
    """source_filter 仍透传（[G1 修复] 不得回退）。"""
    db = tmp_path / "kb.db"
    store = ks_mod.KnowledgeStore(db)
    for c in CHUNKS.values():
        store.add_chunk(c)
    _patch_store(monkeypatch, store)

    outcome = hybrid.search_with_diagnostics("实践", top_k=5, source_filter="毛中特")

    assert [r.chunk_id for r in outcome.results] == ["c3"]


# ─────────────────── 5. 入口可达性（CLI 层） ───────────────────

def _load_cli():
    """导入 ky_cli 门面（触发命令注册）。"""
    if str(ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools"))
    import importlib
    return importlib.import_module("tools.ky_cli")


def test_rag_command_is_registered():
    """``ky rag`` 必须出现在命令表里（改造前该链路 0 个可达入口）。"""
    cli = _load_cli()
    assert "rag" in cli.COMMAND_ALIASES
    assert cli.COMMAND_ALIASES.get("search") == "rag"


def test_rag_help_mentions_degradation():
    cli = _load_cli()
    cmd = cli.COMMAND_HANDLERS.get("rag")
    assert cmd is not None, "rag 命令必须带 handler"
    spec = next(c for c in cli.COMMAND_SPECS if c.name == "rag")
    assert "降级" in spec.desc


def test_rag_without_db_refuses_and_does_not_create_it(tmp_path, monkeypatch):
    """库不存在时：提示先入库、返回码 2，且**不得**凭空建库（只读语义）。"""
    from tools.cli.commands import search as cmd_search

    missing = tmp_path / "nope" / "embeddings.db"
    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", missing, raising=False)

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cmd_search.run_rag_search("剩余价值")

    out = buf.getvalue()
    assert code == 2
    # 提示必须给出**真实可用**的两步：ky ingest 只归档题卡，建索引是 indexer
    assert "ky ingest" in out
    assert "indexer.py" in out, "不得只写『ky ingest 即可检索』（那样知识库仍是空的）"
    assert not missing.exists(), "只读检索不得代建空库"


def test_rag_with_db_prints_degradation_panel(tmp_path, monkeypatch):
    """库存在但向量不可用：输出里必须出现『已降级』与具体原因。"""
    from tools.cli.commands import search as cmd_search

    db = tmp_path / "kb.db"
    store = ks_mod.KnowledgeStore(db)
    for c in CHUNKS.values():
        store.add_chunk(c)
    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", db, raising=False)
    _patch_store(monkeypatch, store)

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cmd_search.run_rag_search("剩余价值")

    out = buf.getvalue()
    assert code == 0
    assert "已降级" in out
    assert "sqlite-vec" in out
    assert "剩余价值" in out, "应回显查询词"


def test_rag_empty_query_is_usage_error(monkeypatch):
    from tools.cli.commands import search as cmd_search

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cmd_search.run_rag_search("   ")

    assert code == 1
    assert "用法" in buf.getvalue()


# ─────────────── 6. 只读模式白名单（safe mode） ───────────────

def test_rag_allowed_in_safe_mode():
    """``/rag`` 是纯检索，safe 模式下必须放行。"""
    from tools.cli.shared import detect_repl_safe_mode_violation

    assert detect_repl_safe_mode_violation("/rag", "剩余价值") is None
    assert detect_repl_safe_mode_violation("/search", "剩余价值") is None


def test_write_commands_still_blocked_in_safe_mode():
    """阴性对照：白名单加宽不得顺带放行写操作。"""
    from tools.cli.shared import detect_repl_safe_mode_violation

    assert detect_repl_safe_mode_violation("/ingest", "x.md") is not None
    assert detect_repl_safe_mode_violation("/done", "英语") is not None
    assert detect_repl_safe_mode_violation("/style", "2") is not None


# ─────────────── 7. 注释诚实化（静态锁定） ───────────────

def test_no_false_bm25_or_reuse_claims_in_source():
    """源码里不得再出现『BM25-like』或『复用 relevance.py + rank.py』这类不实描述。"""
    src = (ROOT / "tools" / "search" / "hybrid.py").read_text(encoding="utf-8")
    assert "BM25-like" not in src, "词法分支是纯 TF 计数，不是 BM25"
    assert "复用现有的 relevance.py" not in src
    # 更正后的说明必须留下（防止有人再改回去）
    assert "纯 TF 计数" in src


# ─────────────── 8. 片段预览不得泄漏 Markdown 源码 ───────────────

def test_snippet_strips_markdown_source():
    """检索片段是给考生看的，不得把 ``#`` / ``**`` / 表格竖线倒进终端。"""
    from tools.cli.repl.renderer import _md_to_snippet

    raw = ("# 马原核心考点\n\n## 一、剩余价值理论\n"
           "剩余价值是雇佣工人所创造的、被资本家无偿占有的超过劳动力价值的价值。\n"
           "- **共性个性**：矛盾的普遍性与特殊性是共性与个性。\n"
           "| 项目 | 内容 |\n|---|---|\n| 来源 | 教材 |\n")
    snip = _md_to_snippet(raw)

    for bad in ("#", "**", "|", "---"):
        assert bad not in snip, f"片段里残留 Markdown 标记 {bad!r}"
    assert "剩余价值" in snip and "共性个性" in snip, "去标记不得把正文一起删掉"


def test_snippet_truncates_with_ellipsis():
    from tools.cli.repl.renderer import _md_to_snippet

    snip = _md_to_snippet("剩余价值" * 100, limit=40)

    assert len(snip) == 41, "应截到 limit + 省略号"
    assert snip.endswith("…")


def test_rag_output_has_no_markdown_leak(monkeypatch):
    """端到端：``ky rag`` 的渲染输出里不得出现 Markdown 源码标记。"""
    from tools.cli.commands import search as cmd_search

    store = _FakeStore(has_vector=False, chunks={
        "c1": _chunk("c1", "# 标题\n\n## 小节\n- **要点**：矛盾的普遍性与特殊性。"),
    })
    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", Path(__file__), raising=False)
    _patch_store(monkeypatch, store)

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cmd_search.run_rag_search("矛盾")

    assert code == 0
    out = buf.getvalue()
    assert "## 小节" not in out, "不得把 Markdown 标题源码倒给用户"
    assert "**要点**" not in out
