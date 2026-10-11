# -*- coding: utf-8 -*-
"""R12 修复回归：判卷报告的两项如实性 ——
  * G3 防篡改不对称：编辑已保存试卷的题干后交卷判分，此前零告警；
    现固定声明判分口径（按题卡）并逐题对比卷面题干与题卡题干，不一致时提示；
  * G4-5 判卷分母口径澄清：「总得分 8/8、通过率 100%」的分母只含可自动判分
    题目，此前不显式说明，考生易误读为全卷成绩；现摊开分母/待复核/全卷三个
    数字（仅当存在待复核题时输出）。

全部用例使用合成数据（示例题/示例作答）与 tmp_path 文件，不含任何真实身份
信息；LLM 不参与（判卷走选择题精确比对路径）；密钥用内嵌注释通道。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.skills import exam_composer as exam  # noqa: E402


def _paper(keys) -> str:
    """按真实链路形态构造内嵌密钥试卷（与 K1 测试同款夹具）。"""
    return "<!-- EXAM_ANSWER_KEYS: " + json.dumps(keys) + " -->"


def _fake_logger(writes):
    return SimpleNamespace(
        scan_error_records=lambda subject=None: [],
        log_error_record=lambda **kw: writes.append(kw) or "ok",
        mark_error_status=lambda **kw: writes.append(kw) or (False, "无既有记录"))


def _paper_file(tmp_path, qtext: str, keys, *, name="paper.md") -> Path:
    """写一份带真实卷面结构的试卷文件（组卷渲染循环同款格式）。"""
    p = tmp_path / name
    p.write_text(
        "### 📝 第 1 题：示例题一\n"
        "- **考查属性**：`综合考点` ｜ FSRS 档位: `stage=0`\n"
        "- **题目设问与题干**：\n"
        "```text\n" + qtext + "\n```\n"
        "**【学员作答区】**：\n"
        "> (请在此处填写您的推导步骤与最终答案)\n"
        "---\n"
        "<!-- EXAM_ANSWER_KEYS: " + json.dumps(keys) + " -->\n",
        encoding="utf-8")
    return p


_KEYS1 = [{"id": 1, "title": "示例题一", "question": "示例题干一",
           "standard_answer": "A", "score": 5}]


# ═════════════════ G3 判分口径与完整性提示 ═════════════════

def test_paper_question_untouched_no_tamper_notice(tmp_path, monkeypatch):
    """[红线] 卷面未改：口径声明在、完整性提示不在（防误报）。"""
    monkeypatch.setattr(exam, "error_logger", _fake_logger([]))
    pf = _paper_file(tmp_path, "示例题干一", _KEYS1)
    res = exam.grade_exam_paper(str(pf), "1. A", auto_advance=False)
    assert res["success"] is True
    assert "判分口径" in res["report"]
    assert "完整性提示" not in res["report"], res["report"]


def test_paper_question_edited_triggers_notice(tmp_path, monkeypatch):
    """[红线] 卷面题干被改：必须提示（此前零告警），且判分不受影响。"""
    monkeypatch.setattr(exam, "error_logger", _fake_logger([]))
    pf = _paper_file(tmp_path, "被改过的题干内容", _KEYS1)
    res = exam.grade_exam_paper(str(pf), "1. A", auto_advance=False)
    assert res["success"] is True
    assert "完整性提示" in res["report"], res["report"]
    assert "第 1 题" in res["report"]
    # 判分仍按题卡口径执行：标准答案 A 命中 → 得分 5/5
    assert res["score"] == 5


def test_paper_question_prefix_strip_not_flagged(tmp_path, monkeypatch):
    """阴性：组卷渲染侧剥离答案子小节（INGEST-7）后卷面题干是题卡题干前缀 —— 不误报。"""
    monkeypatch.setattr(exam, "error_logger", _fake_logger([]))
    keys = [{"id": 1, "title": "示例题一",
             "question": "示例题干一（完整版，含采分点尾部小节）",
             "standard_answer": "A", "score": 5}]
    pf = _paper_file(tmp_path, "示例题干一", keys)
    res = exam.grade_exam_paper(str(pf), "1. A", auto_advance=False)
    assert "完整性提示" not in res["report"], res["report"]


def test_paper_question_tail_append_triggers_notice(tmp_path, monkeypatch):
    """[红线·E2E 实测补漏] 卷面题干尾部追加文字：必须提示。

    渲染侧只剥离答案子小节、从不追加（exam_composer 渲染防线），故「卷面比
    题卡长」无正当来源。此前一致性判定对称放行前缀关系，把尾部追加这类
    典型编辑形态整类吞掉（E2E S5 实测：追加"（被考生编辑过）"零提示）。
    """
    monkeypatch.setattr(exam, "error_logger", _fake_logger([]))
    keys = [{"id": 1, "title": "示例题一", "question": "示例题干一",
             "standard_answer": "A", "score": 5}]
    pf = _paper_file(tmp_path, "示例题干一（被考生编辑过）", keys)
    res = exam.grade_exam_paper(str(pf), "1. A", auto_advance=False)
    assert res["success"] is True
    assert "完整性提示" in res["report"], res["report"]
    assert "第 1 题" in res["report"]
    # 判分仍按题卡口径执行（编辑卷面不影响判分）
    assert res["score"] == 5


def test_inline_text_no_integrity_check(monkeypatch):
    """内联文本（非文件）无"已保存试卷"可言：口径声明在、完整性检查不触发。"""
    monkeypatch.setattr(exam, "error_logger", _fake_logger([]))
    res = exam.grade_exam_paper(_paper(_KEYS1), "1. A", auto_advance=False)
    assert "判分口径" in res["report"]
    assert "完整性提示" not in res["report"]


def test_extract_paper_question_blocks_unit():
    """提取器最小契约：题号 → 题干；无围栏/无题块 → 不产出。"""
    from tools.skills.exam_grading import _extract_paper_question_blocks
    content = (
        "### 📝 第 1 题：甲\n- **题目设问与题干**：\n```text\n题干甲\n```\n"
        "### 📝 第 2 题：乙\n- **题目设问与题干**：\n````text\n题干含```围栏\n````\n"
    )
    got = _extract_paper_question_blocks(content)
    assert got == {1: "题干甲", 2: "题干含```围栏"}
    assert _extract_paper_question_blocks("") == {}
    assert _extract_paper_question_blocks("### 📝 第 1 题：无题干块") == {}


# ═════════════════ G4-5 分母口径澄清 ═════════════════

def test_denominator_note_when_pending_review(monkeypatch):
    """[红线] 存在待复核题：报告必须摊开 分母/待复核/全卷 三个数字。"""
    monkeypatch.setattr(exam, "error_logger", _fake_logger([]))
    keys = [
        {"id": 1, "title": "示例题一", "question": "示例题干一",
         "standard_answer": "A", "score": 5},
        {"id": 2, "title": "示例题二", "question": "示例题干二", "score": 5},
    ]
    res = exam.grade_exam_paper(_paper(keys), "1. A\n2. 示例作答内容",
                                auto_advance=False)
    assert res["success"]
    report = res["report"]
    assert "口径说明" in report, report
    assert "分母 5 分" in report
    assert "1 题（合计 5 分）待人工复核" in report
    assert "全卷满分 10 分" in report


def test_no_denominator_note_when_all_gradable(monkeypatch):
    """阴性：全部题目可自动判分（分母=全卷）→ 不输出口径说明行（不冗余）。"""
    monkeypatch.setattr(exam, "error_logger", _fake_logger([]))
    keys = [
        {"id": 1, "title": "示例题一", "question": "示例题干一",
         "standard_answer": "A", "score": 5},
        {"id": 2, "title": "示例题二", "question": "示例题干二",
         "standard_answer": "B", "score": 5},
    ]
    res = exam.grade_exam_paper(_paper(keys), "1. A\n2. B", auto_advance=False)
    assert res["success"]
    assert "口径说明" not in res["report"], res["report"]


def test_no_denominator_note_when_all_pending(monkeypatch):
    """全卷待复核（graded_max=0）：由「没有可自动判分的题目」评价行覆盖，不重复输出。"""
    monkeypatch.setattr(exam, "error_logger", _fake_logger([]))
    keys = [{"id": n, "title": f"示例题{n}", "question": f"示例题干{n}", "score": 5}
            for n in (1, 2)]
    res = exam.grade_exam_paper(_paper(keys), "1. 示例作答甲\n2. 示例作答乙",
                                auto_advance=False)
    assert res["success"]
    assert "口径说明" not in res["report"], res["report"]
    assert "没有可自动判分的题目" in res["report"]
