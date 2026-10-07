# -*- coding: utf-8 -*-
"""仿真修复批次 W2（检索/入库层）回归测试：F1 / F5 / F9 / F12。

来源：三考生×六环节×三端仿真（18/18 格）发现的 13 项缺陷中 W2 组 4 项：

  F1  切片器不识别「分析论述题」分段 → 段内题目继承上一分段题型/分值
      （material_ingestion.py 三处词表放宽为 ``(?:分析)?论述题``）
  F5  采分点补全静默失效（timeout=10s 必超时 + ``except: pass`` 吞错）
      → enrich 返回 bool、timeout=90、ingest 统计成功/失败数
  F9  变式检索把考纲条目当「真实题目」命中（BM25 一命中即报 N 道）
      → 文件名过滤 + ``_looks_like_question_block`` 题性判定
  F12 幂等去重对含 LLM 补全的切片失效（两次补全输出不同 → 指纹不同）
      → ``_ingest_fingerprint`` 归一化 enrich 尾部段与考点行

全部用例隔离在 ``tmp_path``：不落盘真实工作区、不联网、不触碰真实
ky_config.json；LLM 一律打桩（``tools.llm_client`` 模块对象）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.skills.material_ingestion import (  # noqa: E402
    MaterialIngestionPipeline, QuestionChunk,
)


def _llm_json(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


def _patch_llm(monkeypatch, *, configured=True, chat=None):
    """打桩 tools.llm_client 的 is_llm_configured / chat_completion。

    两个模块内调用点都走 ``from tools.llm_client import ...`` 的函数内导入，
    因此打模块属性即可生效（与 tests/test_multimode_and_llm_injection.py 同法）。
    """
    import tools.llm_client as llm_mod
    monkeypatch.setattr(llm_mod, "is_llm_configured", lambda *a, **k: configured)
    if chat is not None:
        monkeypatch.setattr(llm_mod, "chat_completion", chat)


# ═════════════════════════ F1：分析论述题分段 ═════════════════════════

_F1_DOC = """## 四、分析论述题（每题 30 分，共 60 分）

1. 结合材料，分析教育公平与社会流动之间的内在关系。

2. 论述教育目的的价值取向，并联系实际谈一谈你的看法。
"""


def test_f1_analysis_discuss_section_recognized(tmp_path):
    """「分析论述题」分段必须被识别：2 题 → discuss、满分 30（分段声明）。"""
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    chunks = p.chunk_text(_F1_DOC, default_source="F1回归")
    assert len(chunks) == 2, [c.stem for c in chunks]
    assert all(c.q_type == "discuss" for c in chunks), [c.q_type for c in chunks]
    assert all(c.score == 30 for c in chunks), [c.score for c in chunks]


def test_f1_plain_discuss_section_unchanged(tmp_path):
    """阴性对照：原「论述题」分段行为不变（修复不得回归既有识别）。"""
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    chunks = p.chunk_text(_F1_DOC.replace("分析论述题", "论述题"), default_source="F1阴性")
    assert len(chunks) == 2, [c.stem for c in chunks]
    assert all(c.q_type == "discuss" for c in chunks), [c.q_type for c in chunks]
    assert all(c.score == 30 for c in chunks), [c.score for c in chunks]


# ═════════════════════════ F5：采分点补全静默失效 ═════════════════════════

_F5_DOC = "1. 简述教育心理学的研究对象。\n\n2. 简述教育心理学的发展历程。\n"


def test_f5_timeout_reports_failure_count(tmp_path, monkeypatch, capsys):
    """chat_completion 超时（TimeoutError）→ 补全结果行如实报告失败题数。"""
    def _boom(*a, **k):
        raise TimeoutError("simulated timeout")

    _patch_llm(monkeypatch, configured=True, chat=_boom)
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    res = p.ingest_text(_F5_DOC, subject="pro", source_name="F5超时",
                        target_path=tmp_path / "out_f5_timeout.md")
    out = capsys.readouterr().out
    assert res["success"] is True
    assert "成功 0 / 失败 2 道" in out, out
    assert "失败题保留本地兜底考点" in out


def test_f5_success_counts_and_timeout_90(tmp_path, monkeypatch, capsys):
    """合法 JSON → 成功计数；调用参数 timeout 必须为 90.0（10s 必超时）。"""
    seen = {}
    payload = _llm_json(rubric=["[+3分] 步骤一"], points=["研究对象"], analysis="简明解析")

    def _fake(prompt, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return payload

    _patch_llm(monkeypatch, configured=True, chat=_fake)
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    res = p.ingest_text(_F5_DOC, subject="pro", source_name="F5成功",
                        target_path=tmp_path / "out_f5_ok.md")
    out = capsys.readouterr().out
    assert "成功 2 / 失败 0 道" in out, out
    assert seen.get("timeout") == 90.0, seen
    assert all(c.rubric == ["[+3分] 步骤一"] for c in res["chunks"])
    assert all(c.points == ["研究对象"] for c in res["chunks"])


def test_f5_enrich_returns_bool_contract(tmp_path, monkeypatch):
    """enrich_rubric_with_llm 返回语义：无需补全/成功→True；未配置/异常/不可解析/未填充→False。"""
    p = MaterialIngestionPipeline(workspace_root=tmp_path)

    def _mk():
        return QuestionChunk(number=1, q_type="short", score=10, stem="简述教育心理学的研究对象。")

    _patch_llm(monkeypatch, configured=True)
    # 无需补全 → True
    full = QuestionChunk(number=1, q_type="short", score=10, stem="x",
                         rubric=["[+2分] 步骤"], points=["考点"], analysis="解析")
    assert p.enrich_rubric_with_llm(full) is True

    # 未配置 LLM → False
    _patch_llm(monkeypatch, configured=False)
    assert p.enrich_rubric_with_llm(_mk()) is False

    _patch_llm(monkeypatch, configured=True, chat=lambda *a, **k: "")
    assert p.enrich_rubric_with_llm(_mk()) is False          # 响应为空

    _patch_llm(monkeypatch, configured=True, chat=lambda *a, **k: "这不是 JSON")
    assert p.enrich_rubric_with_llm(_mk()) is False          # JSON 不可解析

    _patch_llm(monkeypatch, configured=True,
               chat=lambda *a, **k: _llm_json(rubric=[], points=[], analysis=""))
    assert p.enrich_rubric_with_llm(_mk()) is False          # 未填充任何字段

    _patch_llm(monkeypatch, configured=True,
               chat=lambda *a, **k: _llm_json(rubric=["[+5分] 步骤一"], points=["研究对象"],
                                              analysis="解析"))
    c = _mk()
    assert p.enrich_rubric_with_llm(c) is True               # 成功填充
    assert c.rubric == ["[+5分] 步骤一"] and c.points == ["研究对象"] and c.analysis == "解析"


def test_f5_fallback_points_data_structure(tmp_path):
    """考点兜底词表增补：题干含「邻接矩阵」等 → 推断为「数据结构」。"""
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    for stem in ("给定图的邻接矩阵，求最小生成树。",
                 "简述 Dijkstra 最短路径算法的适用条件。",
                 "分析哈希冲突的常用消解方法。"):
        c = QuestionChunk(number=1, q_type="essay", score=10, stem=stem)
        assert p._fallback_points(c) == ["数据结构"], stem


# ═════════════════════════ F9：变式检索考纲误报 ═════════════════════════

_SYLLABUS_TEXT = (
    "### 1. 教育心理学概述\n\n"
    "- 掌握：教育心理学的研究对象与学科性质\n"
    "- 理解：教育心理学的发展历程与主要流派\n"
    "- 了解：教育心理学在教育实践中的应用\n"
)

_REAL_QUESTION_TEXT = (
    "### 1. 简述教育心理学的研究对象。\n\n"
    "### 2. 试述教育心理学的发展历程及其对教学的启示。\n"
)


def _setup_retriever(tmp_path, monkeypatch):
    from tools.skills import variant_retriever as vr
    monkeypatch.setattr(vr, "ROOT", tmp_path)
    monkeypatch.setattr(vr, "error_logger", None)   # 不触碰真实错题本
    _patch_llm(monkeypatch, configured=False)        # 合成路径不得联网
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True)
    return vr, ref


def test_f9_hits_real_question_file_not_syllabus(tmp_path, monkeypatch):
    """考纲 + 真题样本并存：命中来自真题样本，考纲文件不产生任何命中。"""
    vr, ref = _setup_retriever(tmp_path, monkeypatch)
    (ref / "311考纲_2026.md").write_text(_SYLLABUS_TEXT, encoding="utf-8")
    (ref / "311真题样本.md").write_text(_REAL_QUESTION_TEXT, encoding="utf-8")

    rep = vr.search_real_variant(subject="pro", keyword="教育心理学")
    assert rep["is_real_source"] is True, rep
    assert rep["variants"], "真题样本未被命中"
    assert all(h["source_name"] == "311真题样本.md" for h in rep["variants"]), rep["variants"]
    assert all(h["source_type"] == "real_file" for h in rep["variants"])


def test_f9_only_syllabus_is_not_real_source(tmp_path, monkeypatch):
    """仅放考纲 → 不得标 real；走既有自拟变式诚实路径。"""
    vr, ref = _setup_retriever(tmp_path, monkeypatch)
    (ref / "311考纲_2026.md").write_text(_SYLLABUS_TEXT, encoding="utf-8")

    rep = vr.search_real_variant(subject="pro", keyword="教育心理学")
    assert rep["is_real_source"] is False, rep
    assert "自拟变式" in rep["source_status"], rep["source_status"]


def test_f9_syllabus_filename_filtered_even_with_question_like_content(tmp_path, monkeypatch):
    """文件名含「考纲」→ 整文件跳过（内容即使像题目也不得命中）。"""
    vr, ref = _setup_retriever(tmp_path, monkeypatch)
    (ref / "311考纲_2026.md").write_text(_REAL_QUESTION_TEXT, encoding="utf-8")

    rep = vr.search_real_variant(subject="pro", keyword="教育心理学")
    assert rep["is_real_source"] is False, rep


def test_f9_looks_like_question_block_unit():
    """题性判定单测：掌握条目/纯章节 → 非题；设问/选项/分值/英文设问 → 题。"""
    from tools.skills.variant_retriever import _looks_like_question_block as lqb

    assert lqb(_SYLLABUS_TEXT) is False
    assert lqb("### 1. 教育心理学概述\n\n本章介绍教育心理学的学科性质。") is False
    assert lqb("") is False
    assert lqb("### 1. 简述教育心理学的研究对象。") is True          # 设问词
    assert lqb("1. 计算下列定积分。\nA. 1\nB. 2\nC. 3\nD. 4") is True  # 选项行
    assert lqb("1. 结合材料回答问题。\n（30 分）") is True               # 分值声明
    assert lqb("Which of the following statements is correct") is True  # 英文设问
    assert lqb("教育公平的实现路径是什么？") is True                     # 问号


# ═════════════════════════ F12：幂等去重对 LLM 补全失效 ═════════════════════════

_F12_DOC = "1. 简述教育心理学的研究对象及其学科性质。\n"


@pytest.mark.parametrize("mode", ["different", "same", "failing"])
def test_f12_idempotent_despite_llm_enrichment(tmp_path, monkeypatch, capsys, mode):
    """同源文本两次 ingest：两次补全输出不同/相同/失败均只落一个切片文件。

    修复前「补全成功」两次输出不同 → 指纹不一致 → 生成两份同源切片（去重失效）；
    归一化后 enrich 尾部段与考点行不参与指纹，三种模式第二次都走复用路径。
    """
    state = {"n": 0}
    base = {"rubric": ["[+2分] 步骤一"], "points": ["考点一"], "analysis": "解析一"}
    alt = {"rubric": ["[+3分] 步骤二"], "points": ["考点二"], "analysis": "解析二"}

    def _fake(prompt, **kwargs):
        state["n"] += 1
        if mode == "failing":
            raise TimeoutError("simulated timeout")
        data = alt if (mode == "different" and state["n"] > 1) else base
        return _llm_json(**data)

    _patch_llm(monkeypatch, configured=True, chat=_fake)
    p = MaterialIngestionPipeline(workspace_root=tmp_path)

    r1 = p.ingest_text(_F12_DOC, subject="pro", source_name="F12同源")
    r2 = p.ingest_text(_F12_DOC, subject="pro", source_name="F12同源")
    capsys.readouterr()

    assert r1["success"] is True and r1.get("reused_existing") is not True
    assert r2["success"] is True, r2
    assert r2.get("reused_existing") is True, r2
    assert state["n"] == 2, "第二次 ingest 未真实触发补全，去重判定失去意义"

    files = list((tmp_path / "04-专业课" / "参考资料").glob("题库切片_F12同源_*.md"))
    assert len(files) == 1, [f.name for f in files]
    assert Path(r2["target_path"]) == files[0]
