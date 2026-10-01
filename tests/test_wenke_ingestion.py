# -*- coding: utf-8 -*-
"""文科自命题切片适配回归：名词解释/简答/论述题型识别 + 答案区误切."""
from tools.skills.material_ingestion import (
    MaterialIngestionPipeline, _detect_wenke_type, _strip_md_heading,
)

_WENKE_TEXT = """# 人大622真题2021回忆版

一名词解释（每题5分）
1. 齐物
【答案】庄子齐物论核心概念。
2. 白马非马
【答案】公孙龙名实之辩。

二、简答题（每题10分）
1. 简述实践是检验真理唯一标准的理由。
【答案】理由有三。

三、论述题
1. 论述矛盾普遍性与特殊性的辩证关系。
【答案】略。

### 我的答案
1. 我的理解一。
2. 我的理解二。
"""


def _chunks():
    pipe = MaterialIngestionPipeline()
    return pipe._chunk_text_python(_WENKE_TEXT, "人大622真题2021回忆版")


def test_wenke_type_detection_rules():
    assert _detect_wenke_type("一名词解释")[0] == "term"
    assert _detect_wenke_type("二、简答题")[0] == "short"
    assert _detect_wenke_type("三、论述题")[0] == "discuss"
    assert _detect_wenke_type("三、综合计算题") == (None, 0)


def test_wenke_slice_types_and_scores():
    chunks = _chunks()
    by_stem = {c.stem[:4]: c for c in chunks}
    assert by_stem["齐物"].q_type == "term"
    assert by_stem["齐物"].score == 5
    assert by_stem["白马非马"].q_type == "term"
    assert by_stem["简述实践"].q_type == "short"
    assert by_stem["简述实践"].score == 10
    assert by_stem["论述矛盾"].q_type == "discuss"


def test_answer_section_not_chunked_as_questions():
    chunks = _chunks()
    assert len(chunks) == 4, [c.stem[:8] for c in chunks]
    assert not any("我的理解" in c.stem for c in chunks)


def test_wenke_card_headers_use_wenke_names():
    pipe = MaterialIngestionPipeline()
    chunks = _chunks()
    cards = [pipe.format_question_card(c) for c in chunks]
    assert "名词解释题" in cards[0]
    assert "简答题" in cards[2]
    assert "论述题" in cards[3]


# ───────────── 回归：题干混入 Markdown 标题片段（切片出口统一清洗） ─────────────

def test_strip_md_heading_fragment_and_single_hash_negatives():
    """[回归·题干混入 Markdown 标题] 连续 ≥2 个 # 的标题片段（含与上一题同行的
    粘连形态）必须剥离；单井号的正文（C# / #1）不得误伤。
    """
    # 整行标题：剥离后不留空行
    assert _strip_md_heading("## 综合课（498）\n1. 示例题干正文。") == "1. 示例题干正文。"
    # 与上一题同行的粘连形态（实测缺陷现场：标题并进上一题题干）
    assert (_strip_md_heading("4. 示例案例分析：请分析示例行为性质。## 综合课（498）")
            == "4. 示例案例分析：请分析示例行为性质。")
    # 阴性：单井号不是 Markdown 标题
    assert (_strip_md_heading("示例题干：请用 C# 语言写出示例算法。")
            == "示例题干：请用 C# 语言写出示例算法。")
    assert (_strip_md_heading("示例题干：参见 #1 号文献的表述。")
            == "示例题干：参见 #1 号文献的表述。")


def test_chunk_text_strips_md_heading_from_stem():
    """[回归·全链路] 切片出口统一清洗：题干里混入的「## 综合课（498）」不得随
    切片进入题卡；同一出口对 C# / #1 必须原样保留。
    """
    pipe = MaterialIngestionPipeline()
    pipe._force_python = True  # 固定纯 Python 路径，避免 Rust 扩展有无造成环境差异
    raw = ("1. 示例综合题：请分析示例行为性质。## 综合课（498）\n"
           "2. 示例第二题：请说明示例原理。")
    chunks = pipe.chunk_text(raw, "示例资料")
    assert chunks, "切片不应为空"
    joined = "\n".join(c.stem for c in chunks)
    assert "##" not in joined and "综合课" not in joined and "498" not in joined
    assert "示例综合题" in joined
    # 阴性对照：单井号正文不被误剥
    raw2 = ("1. 示例综合题：请用 C# 语言写出示例算法。\n"
            "2. 示例第二题：参见 #1 号文献的表述。")
    joined2 = "\n".join(c.stem for c in pipe.chunk_text(raw2, "示例资料"))
    assert "C#" in joined2 and "#1" in joined2
