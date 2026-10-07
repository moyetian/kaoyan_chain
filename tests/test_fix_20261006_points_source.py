# -*- coding: utf-8 -*-
"""R3 仿真缺口 2 回归：采分点兜底必须有「降级」可见标识。

来源：三考生×六环节×三端全矩阵仿真（54 格）暴露的两条产品能力缺口之二。
``material_ingestion`` 的 ``_fallback_points()`` 有三档来源（LLM 补全 / 题面
学科名推断 / 通用占位「核心综合考点」），但 ``format_question_card()`` 把它们
拼成同一种形态 —— 仿真实测两份切片产物 ``grep -c 降级`` = **0/0**，考生无法
分辨这一题的考点是 LLM 认真分析出来的，还是本地兜底凭空填的。

覆盖：
  1. 兜底路径产物必含标记（【考查考点】行末「（本地兜底）」+【考点来源】行）
  2. LLM 成功路径**不得**含该标记（防把正常结果误标为降级）
  3. 两档兜底（学科名推断 / 通用占位）都须标记
  4. 解析链不受影响：新增行落在元数据区，题干提取 / 题源 ID / 校验和逐字节不变
  5. **幂等**：同一份样本连续跑两次 ingest，第二次不产生新题卡且校验和一致
     （含「LLM 成功 / 兜底」两轮 —— 这正是 F12 踩坑点：题源两行由题干摘要派生，
      若新增行挤进题干段或未纳入指纹归一化，去重与身份都会漂）
  6. 阴性对照：标记不得漏进考点名本身（``【考查考点】`生理学`（本地兜底）``
     里的考点名仍应是干净的 ``生理学``）

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
    POINTS_SOURCE_LLM, POINTS_SOURCE_FALLBACK,
    POINTS_FALLBACK_MARK, POINTS_SOURCE_LINE_PREFIX,
)
from tools.skills import question_source as qs  # noqa: E402


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
    """取题卡的【考查考点】行（找不到直接失败 —— 静默丢行等于没修）。"""
    for ln in card.splitlines():
        if "【考查考点】" in ln:
            return ln.strip()
    raise AssertionError(f"题卡缺【考查考点】行:\n{card}")


def _source_line(card: str) -> str:
    for ln in card.splitlines():
        if ln.strip().startswith(POINTS_SOURCE_LINE_PREFIX):
            return ln.strip()
    raise AssertionError(f"题卡缺【考点来源】行:\n{card}")


# ═════════════════ 1. 兜底路径必含标记 ═════════════════

def test_fallback_points_carry_visible_marker(tmp_path):
    """[红线] 学科名兜底：考点行末须带「（本地兜底）」+ 【考点来源】元信息行。"""
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10,
                      stem="试述傅里叶变换的物理意义与工程应用。")
    assert p._fallback_points(c) == ["信号与系统"]
    card = p.format_question_card(c, llm_enrich=False)
    line = _points_line(card)
    assert POINTS_FALLBACK_MARK in line, line
    assert POINTS_FALLBACK_MARK in _source_line(card)
    # 考点名本身必须保持干净（标记在反引号外，不得混进考点名）
    assert "`信号与系统`" in line, line
    assert "信号与系统（本地兜底）" not in line, line


def test_generic_placeholder_also_marked(tmp_path):
    """通用占位「核心综合考点」信息量最低，却最需要标记 —— 不得漏。"""
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10, stem="请论述下列问题。")
    assert p._fallback_points(c) == ["核心综合考点"]
    card = p.format_question_card(c, llm_enrich=False)
    line = _points_line(card)
    assert POINTS_FALLBACK_MARK in line, line
    assert "`核心综合考点`（本地兜底）" in line, line


def test_points_source_contract(tmp_path):
    """points_source 判据= 渲染时 chunk.points 是否为空（模块级口径的钉住）。"""
    p = _pipe(tmp_path)
    empty = QuestionChunk(number=1, q_type="essay", score=10, stem="题干")
    filled = QuestionChunk(number=1, q_type="essay", score=10, stem="题干",
                           points=["教育心理学"])
    assert p.points_source(empty) == POINTS_SOURCE_FALLBACK
    assert p.points_source(filled) == POINTS_SOURCE_LLM
    # 静态方法：不依赖实例状态
    assert MaterialIngestionPipeline.points_source(empty) == POINTS_SOURCE_FALLBACK


def test_points_source_line_present_on_every_card(tmp_path):
    """每张题卡都必须有【考点来源】行（不是只给兜底题加）。"""
    p = _pipe(tmp_path)
    for stem, pts in (("试述傅里叶变换的物理意义。", []),
                      ("简述教育心理学的研究对象。", ["教育心理学"])):
        c = QuestionChunk(number=1, q_type="essay", score=10, stem=stem, points=pts)
        card = p.format_question_card(c, llm_enrich=False)
        assert _source_line(card), stem


# ═════════════════ 2. LLM 成功路径不得误标 ═════════════════

def test_llm_success_path_has_no_fallback_marker(tmp_path):
    """[红线] LLM 补全成功的题卡**不得**出现降级标记（防误标）。"""
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10, stem="简述教育心理学的研究对象。",
                      points=["教育心理学 · 研究对象"], rubric=["[+3分] 步骤一"],
                      analysis="解析")
    card = p.format_question_card(c, llm_enrich=False)
    line = _points_line(card)
    assert POINTS_FALLBACK_MARK not in line, line
    assert POINTS_FALLBACK_MARK not in _source_line(card)
    # 来源行仍须存在，取值须表明已补全
    assert "已补全" in _source_line(card)


def test_llm_enrich_success_card_not_marked(tmp_path, monkeypatch):
    """走完整补全链（ingest_text 内部）后：成功补全的题卡不得带降级标记。"""
    _patch_llm(monkeypatch, configured=True, chat=lambda *a, **k: _llm_json(
        rubric=["[+3分] 步骤一"], points=["研究对象"], analysis="简明解析"))
    p = _pipe(tmp_path)
    res = p.ingest_text("1. 简述教育心理学的研究对象。\n", subject="pro",
                        source_name="F14补全成功",
                        target_path=tmp_path / "out_ok.md")
    assert res["success"] is True
    body = Path(res["target_path"]).read_text(encoding="utf-8")
    assert POINTS_FALLBACK_MARK not in body, body
    assert POINTS_SOURCE_LINE_PREFIX.strip() in body


def test_llm_enrich_failure_card_is_marked(tmp_path, monkeypatch):
    """补全失败（超时）→ 该题必带降级标记，且统计行如实报失败。"""
    def _boom(*a, **k):
        raise TimeoutError("simulated timeout")

    _patch_llm(monkeypatch, configured=True, chat=_boom)
    p = _pipe(tmp_path)
    res = p.ingest_text("1. 简述教育心理学的研究对象。\n", subject="pro",
                        source_name="F14补全失败",
                        target_path=tmp_path / "out_fail.md")
    assert res["success"] is True
    body = Path(res["target_path"]).read_text(encoding="utf-8")
    assert POINTS_FALLBACK_MARK in body, body


# ═════════════════ 3. 解析链不受影响 ═════════════════

def test_marker_does_not_alter_stem_id_or_checksum(tmp_path):
    """[F12 红线] 新增标记落在元数据区：题干提取 / 题源 ID / 校验和逐字节不变。

    题源 ID 与校验和由 ``extract_card_stem`` 的输出派生。若新增行挤进题干段
    （如被backfill 插到「试题原题」之后），checksum 会随降级标记漂移 →
    组卷侧 fail-closed 判``source_tampered``，整批题被排除。
    """
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10,
                      stem="简述教育心理学的研究对象。",
                      answer="研究对象是心理学所研究的心理现象及其规律。")
    card = p.format_question_card(c, llm_enrich=False)

    stem = qs.extract_card_stem(card)
    assert stem.startswith("简述教育心理学的研究对象。")
    # 标记与来源行都不得混进题干（否则元数据区边界被破坏、身份链漂移）
    assert POINTS_FALLBACK_MARK not in stem
    assert "【考点来源】" not in stem

    src_id, checksum = qs.find_source_id(card), qs.find_checksum(card)
    assert src_id and checksum
    # 身份自洽：ID 前缀是校验和前 12 位
    assert checksum.startswith(src_id.split("-", 1)[1])
    # 反向：按题干重算必须得到同一身份（verify 通过 → 组卷不会判篡改）
    assert qs.source_from_card(card, origin=qs.ORIGIN_WHITELIST).verify(stem)


def test_source_and_checksum_identical_across_fallback_and_llm(tmp_path):
    """同一道题、同一份 enrich 尾部：兜底渲染 vs 有考点渲染 → 身份两行必须一致。

    这是「标记不得污染身份链」的最强形式：**只让points 一个变量翻转**
    （空 → 走兜底并加标记 / 有值 → 不加标记），rubric / analysis / answer
    全部保持相同 —— 此时 ``extract_card_stem`` 的输入逐字节相同，
    题源 ID 与校验和就必须相同。

    注意：这里刻意让两版的 rubric/analysis 一致。已知 F12 规格偏差是
    「题干提取范围含卡尾 §3/§4」，所以 §3/§4 内容不同当然会改变校验和
    （那是既有设计，不是本批引入）；本用例只验「标记本身不改变身份」。
    """
    p = _pipe(tmp_path)
    common = dict(number=1, q_type="essay", score=10,
                  stem="简述教育心理学的研究对象。",
                  answer="研究对象是心理学所研究的心理现象及其规律。",
                  rubric=["[+3分] 步骤一"], analysis="解析")
    card_fb = p.format_question_card(QuestionChunk(points=[], **common),
                                     llm_enrich=False)
    card_llm = p.format_question_card(
        QuestionChunk(points=["信号与系统"], **common), llm_enrich=False)

    assert POINTS_FALLBACK_MARK in card_fb
    assert POINTS_FALLBACK_MARK not in card_llm
    assert qs.extract_card_stem(card_fb) == qs.extract_card_stem(card_llm)
    assert qs.find_source_id(card_fb) == qs.find_source_id(card_llm)
    assert qs.find_checksum(card_fb) == qs.find_checksum(card_llm)


def test_backfill_is_idempotent_with_new_line(tmp_path):
    """backfill 对含【考点来源】行的卡片幂等（不重复注入身份行）。"""
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10, stem="简述教育心理学的研究对象。")
    card = p.format_question_card(c, llm_enrich=False)
    again, changed = qs.backfill_markdown_text(card, kind="whitelist")
    assert changed == 0
    assert again == card


# ═════════════════ 4. 幂等：连续两次 ingest ═════════════════

_IDEM_DOC = "1. 简述教育心理学的研究对象及其学科性质。\n"


@pytest.mark.parametrize("mode", ["fallback", "llm"])
def test_double_ingest_idempotent_and_checksum_stable(tmp_path, monkeypatch, mode):
    """[任务硬要求] 同一份样本连续跑两次 ingest：第二次复用既有切片、校验和一致。

    mode=fallback：两轮都补全失败（带兜底标记）；
    mode=llm：两轮都补全成功（不得带标记）——
    两种模式下都必须只落**一个**切片文件，且文件内题源校验和与题源 ID
    逐字节一致（身份链跨轮稳定）。
    """
    if mode == "fallback":
        def _boom(*a, **k):
            raise TimeoutError("simulated timeout")
        _patch_llm(monkeypatch, configured=True, chat=_boom)
    else:
        _patch_llm(monkeypatch, configured=True, chat=lambda *a, **k: _llm_json(
            rubric=["[+3分] 步骤一"], points=["研究对象"], analysis="简明解析"))

    p = _pipe(tmp_path)
    r1 = p.ingest_text(_IDEM_DOC, subject="pro", source_name="F14幂等")
    r2 = p.ingest_text(_IDEM_DOC, subject="pro", source_name="F14幂等")

    assert r1["success"] is True and r1.get("reused_existing") is not True
    assert r2["success"] is True, r2
    assert r2.get("reused_existing") is True, (
        f"第二轮未复用既有切片（mode={mode}）—— 去重指纹被新增行破坏")

    files = list((tmp_path / "04-专业课" / "参考资料").glob("题库切片_F14幂等_*.md"))
    assert len(files) == 1, [f.name for f in files]
    assert Path(r2["target_path"]) == files[0]

    body = files[0].read_text(encoding="utf-8")
    blocks = qs.split_card_blocks(body)[1:]
    assert blocks
    ids = [qs.find_source_id(b) for b in blocks]
    sums = [qs.find_checksum(b) for b in blocks]
    assert all(ids) and all(sums)
    # 每张卡的身份自洽，且按题干重算 verify 通过（组卷侧不会判篡改）
    for blk, sid, cks in zip(blocks, ids, sums):
        stem = qs.extract_card_stem(blk)
        assert cks.startswith(sid.split("-", 1)[1])
        assert qs.source_from_card(blk, origin=qs.ORIGIN_WHITELIST).verify(stem)

    # 标记口径：仅 fallback 模式带降级标记
    assert (POINTS_FALLBACK_MARK in body) is (mode == "fallback")


@pytest.mark.parametrize("mode", ["fallback_then_llm", "llm_then_fallback"])
def test_idempotent_across_source_flip(tmp_path, monkeypatch, mode):
    """[F12 同类风险] 两轮来源不同（兜底 ↔ LLM）也必须复用同一份切片。

    【考点来源】行的取值会随「LLM 是否成功」翻转 —— 若它没被纳入
    ``_ingest_fingerprint`` 的归一化前缀，指纹就随补全结果漂移，
    「补全成功反而破坏幂等」的 F12 症状会原样复发。
    """
    state = {"fail": mode == "fallback_then_llm"}

    def _maybe_boom(*a, **k):
        if state["fail"]:
            raise TimeoutError("simulated timeout")
        return _llm_json(rubric=["[+3分] 步骤一"], points=["研究对象"], analysis="解析")

    _patch_llm(monkeypatch, configured=True, chat=_maybe_boom)
    p = _pipe(tmp_path)
    r1 = p.ingest_text(_IDEM_DOC, subject="pro", source_name="F14翻转")
    state["fail"] = not state["fail"]
    r2 = p.ingest_text(_IDEM_DOC, subject="pro", source_name="F14翻转")

    assert r1["success"] is True
    assert r2.get("reused_existing") is True, (
        f"来源翻转后未复用（mode={mode}）—— 【考点来源】行未纳入指纹归一化")
    files = list((tmp_path / "04-专业课" / "参考资料").glob("题库切片_F14翻转_*.md"))
    assert len(files) == 1, [f.name for f in files]


# ═════════════════ 5. 阴性对照 ═════════════════

def test_marker_never_leaks_into_points_values(tmp_path):
    """阴性：``_fallback_points`` 返回值不含标记（考点名与展示串解耦）。"""
    p = _pipe(tmp_path)
    for stem in ("试述傅里叶变换的物理意义与工程应用。", "请论述下列问题。"):
        c = QuestionChunk(number=1, q_type="essay", score=10, stem=stem)
        for v in p._fallback_points(c):
            assert POINTS_FALLBACK_MARK not in v, v


def test_fallback_does_not_write_into_chunk_points(tmp_path):
    """阴性：兜底仍只影响展示，不得占住 LLM 补全的判定槽（既有契约不回归）。"""
    p = _pipe(tmp_path)
    c = QuestionChunk(number=1, q_type="essay", score=10, stem="试述傅里叶变换的物理意义。")
    p.format_question_card(c, llm_enrich=False)
    assert c.points == []