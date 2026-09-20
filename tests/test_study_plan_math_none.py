# -*- coding: utf-8 -*-
"""文科画像回归：不考数学战术跳过 + 自命题占位示例随科目变化。"""
from tools.study_planner import generate_expert_diagnostic_strategy
from tools.syllabus_manager import _pro_placeholder_example


def _plan(**over):
    base = {"math_key": "math2", "math_name": "数学二 (302)",
            "math_weakness": "极限计算", "eng_weakness": "长难句",
            "pol_weakness": "多选", "pro_weakness": "原著",
            "pro_name": "622 中西哲学史"}
    base.update(over)
    return base


def test_math_none_skips_math_tactics():
    text = generate_expert_diagnostic_strategy(
        _plan(math_key="none", math_name="不考数学",
              math_weakness="无"))
    assert "不考数学" in text
    assert "折半检验法" not in text and "求导验算" not in text
    assert "### 1. 【英语" in text or "### 1. 【eng" in text or text.index("英语") < text.index("政治")


def test_math_present_keeps_math_tactics_numbered():
    text = generate_expert_diagnostic_strategy(_plan())
    assert "折半检验法" in text
    assert "### 1. 【数学二 (302)】" in text
    assert "### 4. 【622 中西哲学史】" in text


def test_placeholder_example_follows_subject():
    assert "数据结构" in _pro_placeholder_example("408 计算机学科专业基础")
    philo = _pro_placeholder_example("622 中西哲学史")
    assert "数据结构" not in philo and "算法" not in philo
    neutral = _pro_placeholder_example("999 某某新设科目")
    assert "数据结构" not in neutral and "算法" not in neutral
