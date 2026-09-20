import json
from types import SimpleNamespace
import pytest
from tools.skills import exam_composer as exam
from tools.skills.exam_answers import parse_answers
from tools.skills.exam_grading import _unique_mistake_title


def test_mistake_titles_carry_stem_preview_and_do_not_collide():
    t1 = _unique_mistake_title("622 中西哲学史核心必考大纲自测题", "齐物（《庄子》）逍遥游辨析")
    t2 = _unique_mistake_title("622 中西哲学史核心必考大纲自测题", "白马非马（公孙龙）名实之辩")
    assert t1 != t2
    assert "齐物" in t1 and "白马非马" in t2
    # 已带预览的不重复追加（幂等）
    assert _unique_mistake_title(t1, "齐物（《庄子》）逍遥游辨析") == t1


def test_failed_items_with_same_title_get_distinct_mistake_titles(monkeypatch):
    created = []
    monkeypatch.setattr(exam, "error_logger", SimpleNamespace(
        log_error_record=lambda **kw: created.append(kw) or "ok",
        mark_error_status=lambda **kw: (False, "无既有记录")))
    keys = [{"id": 1, "title": "622核心必考大纲自测题", "question": "齐物逍遥游辨析",
             "standard_answer": "A"},
            {"id": 2, "title": "622核心必考大纲自测题", "question": "白马非马名实之辩",
             "standard_answer": "B"}]
    paper = "<!-- EXAM_ANSWER_KEYS: " + json.dumps(keys) + " -->"
    result = exam.grade_exam_paper(paper, "第一题：C\n第二题：D")
    assert result["success"]
    titles = [c["title"] for c in created]
    assert len(titles) == 2 and len(set(titles)) == 2


@pytest.mark.parametrize("text", ["第一题：A\n第二题：B", "（1）A\n【2】B", "1. A 2. B"])
def test_number_formats(text):
    _, answers, error = parse_answers(text, {1, 2})
    assert not error
    assert answers == {1: "A", 2: "B"}


def test_numbers_in_answers_preserved():
    _, answers, error = parse_answers("第十一题：-0.5\n第十二题：f(1)=2", {11, 12})
    assert not error
    assert answers == {11: "-0.5", 12: "f(1)=2"}


def test_unparseable_submission_never_updates_student_records(monkeypatch):
    calls = []
    monkeypatch.setattr(exam, "error_logger", SimpleNamespace(
        log_error_record=lambda **kw: calls.append(kw),
        mark_error_status=lambda **kw: calls.append(kw)))
    keys = [{"id": n, "title": str(n), "question": "完整题干", "standard_answer": "A"}
            for n in (1, 2)]
    paper = "<!-- EXAM_ANSWER_KEYS: " + json.dumps(keys) + " -->"
    result = exam.grade_exam_paper(paper, "这是一段没有题号的完整长文本作答")
    assert not result["success"]
    assert result["updated_records"] == []
    assert calls == []


def test_missing_answers_excluded_and_registered_score_used(monkeypatch):
    keys = [{"id": n, "title": str(n), "question": "完整题干", "standard_answer": "A", "score": 5}
            for n in (1, 2)]
    paper = "<!-- EXAM_ANSWER_KEYS: " + json.dumps(keys) + " -->"
    result = exam.grade_exam_paper(paper, "1. A", auto_advance=False)
    assert result["success"]
    assert result["score"] == 5
    assert result["pass_rate"] == 100
    assert len(result["need_review"]) == 1
