# -*- coding: utf-8 -*-
"""文科自命题切片适配回归：名词解释/简答/论述题型识别 + 答案区误切."""
from tools.skills.material_ingestion import (
    MaterialIngestionPipeline, _detect_wenke_type,
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
