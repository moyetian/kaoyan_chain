# -*- coding: utf-8 -*-
"""变式按题号切块 + 考纲 Diff 修订说明过滤回归。"""
from tools.skills.variant_retriever import _extract_question_block
from tools.intelligence.syllabus_diff import SyllabusDiffGenerator

_DOC = """# 2021真题回忆版

一名词解释
1. 齐物
庄子齐物论核心概念，要求掌握。
2. 白马非马
公孙龙名实之辩，要求掌握。

二简答题
1. 简述实践标准理由。
理由有三，要求理解。
"""


def test_variant_cuts_single_question_block():
    idx = _DOC.find("白马非马")
    block = _extract_question_block(_DOC, idx, len("白马非马"))
    assert "白马非马" in block
    assert "简述实践" not in block  # 不吞下一题
    assert "齐物" not in block  # 不吞上一题（按题号切分）


def test_variant_falls_back_when_no_markers():
    content = "里士多德；要求「掌握」。康德范畴表要求熟悉。"
    idx = content.find("康德")
    block = _extract_question_block(content, idx, len("康德"))
    assert "康德" in block and len(block) <= 1200


def test_diff_ignores_revision_notes():
    gen = SyllabusDiffGenerator()
    new_md = """# 04-专业课大纲
## 第一章 存在论
- **掌握**：存在与本质
## 三、2027届调整
- 新增：现代新儒家
- 剔除：无。
"""
    pts = gen.parse_syllabus(new_md)
    texts = [p.text for p in pts]
    assert any("存在与本质" in t for t in texts)
    assert not any("现代新儒家" in t for t in texts)
    assert not any(t.strip() in ("无", "无。") for t in texts)
