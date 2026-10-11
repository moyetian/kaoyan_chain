# -*- coding: utf-8 -*-
"""R12 修复回归：``--no-llm`` 题卡的【考点来源】标注必须如实。

来源：第四轮三方审查报告（2026-10-11）G4-4 —— ``ky ingest --no-llm`` 实测
产出的题卡仍标注「已补全（LLM 采分点分析 / 材料正文关键词）」，但该次运行
从未调用过 LLM（``--no-llm`` 显式跳过；未配置 LLM 时同样跳过）。标注失实，
考生会误以为考点经过 LLM 分析。

修复：``QuestionChunk.points_from_llm`` 三态（True=LLM 补全填入 /
False=材料正文提取（表格列 / 题面关键词 / Rust 分块）/ None=未知-直接构造
的调用方）→ ``points_source()`` 细分为 llm/material/fallback 三档，
【考点来源】行按真实来源取文案；None 保持历史取值与文案逐字节不变。

覆盖：
  1. ``--no-llm`` 完整 ingest：题面关键词命中的题 → 标注「材料正文提取」，
     且不得出现「已补全」（核心红线）
  2. 表格切片路径（考点列）同样如实标注
  3. LLM 补全成功路径仍标「已补全」（防过度矫正）
  4. None（直接构造调用方）历史契约不变：取值仍为 ``POINTS_SOURCE_LLM``
  5. 阴性：材料提取标签不得带「本地兜底」标记；兜底仍带
  6. ``--no-llm`` 两轮 ingest 幂等：来源行取值稳定、复用既有切片

全部用例隔离在 ``tmp_path``：不落盘真实工作区、不联网、不触碰真实
ky_config.json；LLM 一律打桩（``tools.llm_client`` 模块对象）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.skills.material_ingestion import (  # noqa: E402
    MaterialIngestionPipeline, QuestionChunk,
    POINTS_SOURCE_LLM, POINTS_SOURCE_MATERIAL, POINTS_SOURCE_FALLBACK,
    POINTS_FALLBACK_MARK, POINTS_SOURCE_LINE_PREFIX,
)


def _llm_json(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


def _patch_llm(monkeypatch, *, configured=True, chat=None):
    """打桩 tools.llm_client 的 is_llm_configured / chat_completion。"""
    import tools.llm_client as llm_mod
    monkeypatch.setattr(llm_mod, "is_llm_configured", lambda *a, **k: configured)
    if chat is not None:
        monkeypatch.setattr(llm_mod, "chat_completion", chat)


def _pipe(tmp_path) -> MaterialIngestionPipeline:
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    p._force_python = True  # 固定纯 Python 路径，排除 Rust 扩展有无的环境差异
    return p


def _points_line(card: str) -> str:
    for ln in card.splitlines():
        if "【考查考点】" in ln:
            return ln.strip()
    raise AssertionError(f"题卡缺【考查考点】行:\n{card}")


def _source_line(card: str) -> str:
    for ln in card.splitlines():
        if ln.strip().startswith(POINTS_SOURCE_LINE_PREFIX):
            return ln.strip()
    raise AssertionError(f"题卡缺【考点来源】行:\n{card}")


# ═════════════════ 1. --no-llm 路径如实标注 ═════════════════

# 题面命中关键词表（「马原」），切片时即填入 points —— 与 LLM 无关
_NO_LLM_DOC = "1. 简述马原的基本原理与当代价值。\n"


def test_no_llm_keyword_points_labeled_material(tmp_path):
    """[红线] --no-llm 下题面关键词命中的考点 → 标「材料正文提取」，非「已补全」。"""
    p = _pipe(tmp_path)
    res = p.ingest_text(_NO_LLM_DOC, subject="pro", source_name="R12无LLM",
                        llm_enrich=False, target_path=tmp_path / "out_no_llm.md")
    assert res["success"] is True
    body = Path(res["target_path"]).read_text(encoding="utf-8")
    src = _source_line(body)
    assert "材料正文提取" in src, src
    # 核心红线：从未调用过 LLM，不得出现「已补全」
    assert "已补全" not in src, src
    # 材料提取不是本地推断兜底，不得误标降级
    assert POINTS_FALLBACK_MARK not in src, src
    assert POINTS_FALLBACK_MARK not in _points_line(body)


def test_no_llm_parse_single_block_sets_material_source(tmp_path):
    """单块解析（关键词命中）产出的 chunk 必须带 points_from_llm=False。"""
    p = _pipe(tmp_path)
    c = p._parse_single_block("1. 简述马原的基本原理。（10 分）", 1, "测试材料")
    assert c.points == ["马原"]
    assert c.points_from_llm is False
    assert p.points_source(c) == POINTS_SOURCE_MATERIAL


def test_table_points_labeled_material(tmp_path):
    """表格切片路径（考点列）同样如实标注。"""
    doc = (
        "| 题号 | 题目 | 答案 | 考点 |\n"
        "| --- | --- | --- | --- |\n"
        "| 1 | 简述实践是检验真理的唯一标准 | 见教材第三章 | 马克思主义基本原理 |\n"
    )
    p = _pipe(tmp_path)
    # 单元级：表格 chunk 的来源标记与标签
    tc = p._extract_table_chunks(doc, "测试源")
    assert len(tc) == 1
    c = tc[0]
    assert c.points == ["马克思主义基本原理"]
    assert c.points_from_llm is False
    assert p.points_source(c) == POINTS_SOURCE_MATERIAL
    card = p.format_question_card(c, llm_enrich=False)
    src = _source_line(card)
    assert "材料正文提取" in src, src
    assert "已补全" not in src, src
    # 端到端：完整 ingest 产物中出现「材料正文提取」
    # （表格头会被文本切片产出一张 essay 碎片卡 —— 既有行为，与本修复无关，
    #  故不做"仅此一种标签"的整体断言）
    res = p.ingest_text(doc, subject="pro", source_name="R12表格",
                        llm_enrich=False, target_path=tmp_path / "out_table.md")
    assert res["success"] is True
    body = Path(res["target_path"]).read_text(encoding="utf-8")
    assert "材料正文提取" in body


# ═════════════════ 2. LLM 补全成功仍标「已补全」 ═════════════════

def test_llm_enriched_points_still_labeled_llm(tmp_path, monkeypatch):
    """防过度矫正：LLM 真实补全填入的考点必须仍标「已补全」。"""
    _patch_llm(monkeypatch, configured=True, chat=lambda *a, **k: _llm_json(
        rubric=["[+3分] 步骤一"], points=["研究对象"], analysis="简明解析"))
    p = _pipe(tmp_path)
    res = p.ingest_text("1. 简述教育心理学的研究对象。\n", subject="pro",
                        source_name="R12有LLM", target_path=tmp_path / "out_llm.md")
    assert res["success"] is True
    body = Path(res["target_path"]).read_text(encoding="utf-8")
    src = _source_line(body)
    assert "已补全" in src, src
    assert "材料正文提取" not in src, src


def test_enrich_sets_points_from_llm_flag(tmp_path, monkeypatch):
    """enrich_rubric_with_llm 成功填入 points 时置 points_from_llm=True。"""
    _patch_llm(monkeypatch, configured=True, chat=lambda *a, **k: _llm_json(
        rubric=["[+3分] 步骤一"], points=["研究对象"], analysis="解析"))
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10,
                      stem="简述教育心理学的研究对象。")
    assert c.points_from_llm is None
    ok = p.enrich_rubric_with_llm(c)
    assert ok is True
    assert c.points == ["研究对象"]
    assert c.points_from_llm is True
    assert p.points_source(c) == POINTS_SOURCE_LLM


def test_enrich_failure_does_not_set_flag(tmp_path, monkeypatch):
    """阴性：补全失败不得把来源标记为 LLM（points 仍空 → 走兜底档）。"""
    def _boom(*a, **k):
        raise TimeoutError("simulated timeout")

    _patch_llm(monkeypatch, configured=True, chat=_boom)
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10,
                      stem="简述教育心理学的研究对象。")
    ok = p.enrich_rubric_with_llm(c)
    assert ok is False
    assert c.points == []
    assert c.points_from_llm is None  # 未被误置 True
    assert p.points_source(c) == POINTS_SOURCE_FALLBACK


# ═════════════════ 3. None 历史契约不变 ═════════════════

def test_none_source_keeps_legacy_llm_label(tmp_path):
    """直接构造调用方（points_from_llm=None）历史契约不变（既有消费者/测试依赖）。"""
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10, stem="题干",
                      points=["教育心理学"])
    assert c.points_from_llm is None
    assert p.points_source(c) == POINTS_SOURCE_LLM
    card = p.format_question_card(c, llm_enrich=False)
    assert "已补全" in _source_line(card)
    assert "材料正文提取" not in _source_line(card)


# ═════════════════ 4. 阴性对照：三档标签互不串扰 ═════════════════

def test_material_label_is_not_fallback_marked(tmp_path):
    """材料提取 ≠ 本地兜底：标签不得互串；空 points 仍走兜底档。"""
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10, stem="题干",
                      points=["马原"], points_from_llm=False)
    card = p.format_question_card(c, llm_enrich=False)
    assert POINTS_FALLBACK_MARK not in _points_line(card)
    assert POINTS_FALLBACK_MARK not in _source_line(card)
    # 对照：空 points 仍标兜底（既有契约不回归）
    c2 = QuestionChunk(number=2, q_type="essay", score=10, stem="请论述下列问题。")
    card2 = p.format_question_card(c2, llm_enrich=False)
    assert POINTS_FALLBACK_MARK in _points_line(card2)
    assert POINTS_FALLBACK_MARK in _source_line(card2)


def test_three_way_labels_are_distinct(tmp_path):
    """三档取值互异且都有对应文案（防未来把 material 折回 llm 的静默回退）。"""
    from tools.skills.material_ingestion import _POINTS_SOURCE_LABEL
    assert len({POINTS_SOURCE_LLM, POINTS_SOURCE_MATERIAL,
                POINTS_SOURCE_FALLBACK}) == 3
    for key in (POINTS_SOURCE_LLM, POINTS_SOURCE_MATERIAL, POINTS_SOURCE_FALLBACK):
        assert key in _POINTS_SOURCE_LABEL, key


# ═════════════════ 5. --no-llm 两轮 ingest 幂等 ═════════════════

def test_double_ingest_no_llm_idempotent(tmp_path):
    """同一材料两轮 --no-llm 入库：第二轮复用既有切片，来源行取值稳定。"""
    p = _pipe(tmp_path)
    r1 = p.ingest_text(_NO_LLM_DOC, subject="pro", source_name="R12幂等",
                       llm_enrich=False)
    r2 = p.ingest_text(_NO_LLM_DOC, subject="pro", source_name="R12幂等",
                       llm_enrich=False)
    assert r1["success"] is True and r1.get("reused_existing") is not True
    assert r2["success"] is True
    assert r2.get("reused_existing") is True, "第二轮未复用既有切片（指纹漂移）"
    files = list((tmp_path / "04-专业课" / "参考资料").glob("题库切片_R12幂等_*.md"))
    assert len(files) == 1, [f.name for f in files]
    body = files[0].read_text(encoding="utf-8")
    assert "材料正文提取" in body
    assert "已补全" not in body
