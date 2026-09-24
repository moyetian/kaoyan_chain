import json
from types import SimpleNamespace
import pytest
from tools.skills import exam_composer as exam
from tools.skills.exam_answers import parse_answers
from tools.skills.exam_grading import (
    _find_existing_mistake_record,
    _unique_mistake_title,
)


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


# ───────────── F8 回归：重复判负必须幂等，不得重复新建错题 ─────────────

def test_regraded_failure_does_not_duplicate_mistake_record(monkeypatch):
    """同一题整卷重判（回写失败后重跑）不得重复新建错题记录。

    [F8 现场] 旧实现新建前不做判重，同一题第二次判负又新建一条 ——
    FSRS 队列与错因统计被同一题刷屏。现新建前三级判重，命中即跳过。
    """
    created = []
    scan_calls = []

    def fake_scan(subject=None):
        scan_calls.append(subject)
        # 模拟第一次判负后错题本里已有的记录（标题由 _unique_mistake_title 生成，
        # 已带题干预览「 · 齐物逍遥游辨析」，与真实 log_error_record 落盘形态一致）
        return [{"title": _unique_mistake_title("探针题", "齐物逍遥游辨析"),
                 "question": "齐物逍遥游辨析"}]

    fake_logger = SimpleNamespace(
        scan_error_records=fake_scan,
        log_error_record=lambda **kw: created.append(kw) or "ok",
        mark_error_status=lambda **kw: (False, "无既有记录"))
    monkeypatch.setattr(exam, "error_logger", fake_logger)

    keys = [{"id": 1, "title": "探针题", "question": "齐物逍遥游辨析",
             "standard_answer": "A", "error_type": "概念漏洞"}]
    paper = "<!-- EXAM_ANSWER_KEYS: " + json.dumps(keys) + " -->"
    result = exam.grade_exam_paper(paper, "1. B")   # 答错 → 判负
    assert result["success"]
    # 命中既有记录：不得新建
    assert created == [], f"重复判负仍新建了错题: {[c['title'] for c in created]}"
    report = result.get("report", "")
    assert "不重复归档" in report, "命中既有记录时未在回执中说明跳过原因"
    assert scan_calls, "未执行三级判重扫描"


def test_find_existing_mistake_record_three_level_matching(tmp_path, monkeypatch):
    """三级判重口径单测：精确标题 / 题干预览 / 题干指纹。"""
    # 与真实链路同源：错题本里的标题就是 _unique_mistake_title 的产物
    # （标题 + 题干前 12 字预览），故用它构造"已归档"的现场。
    q1 = "齐物逍遥游辨析：庄子与惠子游于濠梁之上。"
    t1 = _unique_mistake_title("探针题", q1)
    records = [
        {"title": t1, "question": q1},
        {"title": "另一题", "question": "白马非马，公孙龙所谓名实之辩也。"},
    ]
    monkeypatch.setattr(exam, "error_logger", SimpleNamespace(
        scan_error_records=lambda subject=None: records))

    # ① 精确标题
    assert _find_existing_mistake_record(
        subject="pro", title=t1, question="别的题干",
        error_logger=exam.error_logger) == t1
    # ② 标题含题干预览（同一题干、标题未带预览时命中标题里的预览片段）
    assert _find_existing_mistake_record(
        subject="pro", title="探针题", question=q1,
        error_logger=exam.error_logger) == t1
    # ③ 题干指纹（跨空白归一化后命中记录正文）
    assert _find_existing_mistake_record(
        subject="pro", title="全新标题",
        question="白马非马，公孙龙所谓名实之辩也。",
        error_logger=exam.error_logger) == "另一题"
    # 未命中
    assert _find_existing_mistake_record(
        subject="pro", title="全新标题", question="毫不相关的新题干内容xyz",
        error_logger=exam.error_logger) is None


def test_find_existing_mistake_record_scan_failure_returns_none(monkeypatch):
    """扫描抛异常时必须返回 None（走新建路径），绝不让闭环断裂。"""
    def _boom(subject=None):
        raise RuntimeError("磁盘故障")

    monkeypatch.setattr(exam, "error_logger", SimpleNamespace(scan_error_records=_boom))
    assert _find_existing_mistake_record(
        subject="pro", title="任意", question="任意") is None
