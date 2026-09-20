# -*- coding: utf-8 -*-
"""P0 靶向组卷回归：白名单按考点打分取 Top-N（确定性），占位题按科目分支设问。"""
from tools.skills import exam_composer as exam

_SLICE = """# 题库切片

### 【题号 1】名词解释题（满分: 5 分）
- **【题源出处】**：`2021真题`
#### 1. 试题原题
齐物逍遥游庄子核心概念辨析

### 【题号 2】论述题（满分: 15 分）
- **【题源出处】**：`2021真题`
#### 1. 试题原题
白马非马公孙龙名实之辩论述

### 【题号 3】简答题（满分: 10 分）
- **【题源出处】**：`2021真题`
#### 1. 试题原题
泰勒展开余项估计简述
"""


def _setup_ws(tmp_path, monkeypatch):
    monkeypatch.setattr(exam, "ROOT", tmp_path)
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True)
    (ref / "题库切片_2021.md").write_text(_SLICE, encoding="utf-8")


def test_whitelist_sampling_is_targeted_and_deterministic(tmp_path, monkeypatch):
    _setup_ws(tmp_path, monkeypatch)
    first = exam._load_whitelist_cards("pro", need=1, boost_text="白马非马名实之辩")
    second = exam._load_whitelist_cards("pro", need=1, boost_text="白马非马名实之辩")
    assert len(first) == 1
    assert "白马非马" in first[0]["question"]
    assert first[0]["title"] == second[0]["title"]  # 确定性：两次结果一致
    all_cards = exam._load_whitelist_cards("pro", need=3)
    assert [c["title"] for c in all_cards] == sorted(c["title"] for c in all_cards)


def test_synthetic_fallback_is_subject_aware(tmp_path, monkeypatch):
    _setup_ws(tmp_path, monkeypatch)
    paper = exam.compose_exam_paper(subject="pro", count=2, include_weak=False,
                                    save_file=False)
    for item in paper["items"]:
        if item.get("is_synthetic"):
            assert "核心公式" not in item["question"]
