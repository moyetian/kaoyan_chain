# -*- coding: utf-8 -*-
"""K1 判卷口径一致性回归测试。

覆盖：
  * 三通道全缺密钥 → 明确拒绝判分（success=False），不产生任何错题回写；
  * ``total_score`` 与 ``pass_rate`` 同分母（待复核题不计入分母），
    并新增显式 ``max_score`` / ``graded_max`` 键；
  * 全卷待复核 → 不出具任何通过/不通过结论；
  * 渲染前内部一致性校验：越界只告警不抛异常（整卷报告绝不丢失）；
  * CLI 失败分支读取 ``msg`` 键（旧实现只读 ``message``，真实原因被吞）；
  * 开放题部分分按 match_level 二值化：部分分不落总分、转人工复核。

全部使用合成数据（示例题 / 示例作答），不含任何真实身份信息。
"""
import json
from types import SimpleNamespace

from tools.skills import exam_composer as exam
from tools.skills import exam_grading


def _paper(keys):
    """按真实链路形态构造内嵌密钥试卷（与 test_exam_submission_safety 同款夹具）。"""
    return "<!-- EXAM_ANSWER_KEYS: " + json.dumps(keys) + " -->"


def _fake_logger(writes):
    """记录一切回写调用的假错题本（断言"零回写"用）。"""
    return SimpleNamespace(
        scan_error_records=lambda subject=None: [],
        log_error_record=lambda **kw: writes.append(kw) or "ok",
        mark_error_status=lambda **kw: writes.append(kw) or (False, "无既有记录"))


# ───────────── 拒绝路径：三通道全缺密钥 ─────────────

def test_all_key_channels_missing_rejects_grading(monkeypatch):
    """密钥不可读时必须显式拒绝：success=False + msg 非空 + 0 分 + 零回写。

    此前该路径零测试覆盖；若回归为「继续判分」，学员会看到 0 分报告并
    误以为全部答错，同时错题被误归档。
    """
    writes = []
    monkeypatch.setattr(exam, "error_logger", _fake_logger(writes))
    # 内联文本：无 EXAM_PAPER_ID（中央密钥库通道）、非文件（伴随密钥通道）、
    # 无 EXAM_ANSWER_KEYS 注释（内嵌密钥通道）→ 三通道全部落空。
    result = exam.grade_exam_paper("这是一份没有任何密钥通道的示例试卷内联文本。", "1. A")
    assert result["success"] is False
    assert result["msg"], "拒绝判分必须给出非空原因"
    assert result["score"] == 0
    assert result["updated_records"] == []
    assert writes == [], "密钥缺失的拒绝路径不得产生任何错题回写"


# ───────────── 口径一致性：total_score 与 pass_rate 同分母 ─────────────

def test_total_score_shares_denominator_with_pass_rate(monkeypatch):
    """1 道待复核 + 1 道满分（各 5 分）：
    总分口径须与通过率一致 —— score==graded_max==5、max_score==10、
    total_score==graded_max（而非旧口径的全卷满分 10）。
    """
    writes = []
    monkeypatch.setattr(exam, "error_logger", _fake_logger(writes))
    keys = [
        {"id": 1, "title": "示例题一", "question": "示例题干一",
         "standard_answer": "A", "score": 5},
        {"id": 2, "title": "示例题二", "question": "示例题干二", "score": 5},
    ]
    result = exam.grade_exam_paper(_paper(keys), "1. A\n2. 示例作答内容",
                                   auto_advance=False)
    assert result["success"]
    assert result["score"] == 5
    assert result["graded_max"] == 5
    assert result["max_score"] == 10
    assert result["total_score"] == result["graded_max"] == 5
    assert result["pass_rate"] == 100
    assert len(result["need_review"]) == 1
    assert result["consistency_warnings"] == [], result["report"]


def test_all_pending_review_reports_no_pass_conclusion(monkeypatch):
    """全卷待人工复核：graded_max==0，报告不得出现任何通过/不通过结论。"""
    writes = []
    monkeypatch.setattr(exam, "error_logger", _fake_logger(writes))
    keys = [{"id": n, "title": f"示例题{n}", "question": f"示例题干{n}", "score": 5}
            for n in (1, 2)]
    result = exam.grade_exam_paper(_paper(keys), "1. 示例作答甲\n2. 示例作答乙",
                                   auto_advance=False)
    assert result["success"]
    assert result["graded_max"] == 0
    assert result["total_score"] == 0
    assert result["pass_rate"] == 0
    assert "未生成通过率结论" in result["report"]
    assert "🎉 评价" not in result["report"]
    assert "⚠️ 评价" not in result["report"]
    assert writes == []


# ───────────── 内部一致性校验：越界只告警，不丢报告 ─────────────

def test_consistency_check_flags_out_of_range_without_crashing(monkeypatch):
    """monkeypatch 单题满分口径制造越界（graded_max > max_score）：
    不得抛异常，consistency_warnings 非空且报告含告警行。
    """
    def fake_full(card):
        # 待复核题（id=2）的满分被制造为负值 → excluded_full<0 → graded_max>max_score
        return -5.0 if int(card.get("id")) == 2 else 5.0

    monkeypatch.setattr(exam_grading, "_item_full_score", fake_full)
    keys = [
        {"id": 1, "title": "示例题一", "question": "示例题干一",
         "standard_answer": "A", "score": 5},
        {"id": 2, "title": "示例题二", "question": "示例题干二", "score": 5},
    ]
    result = exam.grade_exam_paper(_paper(keys), "1. A\n2. 示例作答内容",
                                   auto_advance=False)
    assert result["success"], "校验失败绝不允许丢失整卷报告"
    assert result["consistency_warnings"], result["report"]
    assert "内部一致性告警" in result["report"]


# ───────────── CLI 失败分支：读取 msg 键 ─────────────

def test_cli_failure_surfaces_msg_key(monkeypatch, capsys):
    """失败路径写的是 "msg" 键：stdout 必须显示真实原因，而非兜底文案。"""
    from tools.cli.commands import study

    monkeypatch.setattr(exam, "grade_exam_paper",
                        lambda *a, **kw: {"success": False, "msg": "样本失败原因"})
    study._cmd_exam_submit(["exam-submit", "示例内联试卷文本", "1. A"])
    assert "样本失败原因" in capsys.readouterr().out

    # 旧键 "message" 仍兼容
    monkeypatch.setattr(exam, "grade_exam_paper",
                        lambda *a, **kw: {"success": False, "message": "旧键原因"})
    study._cmd_exam_submit(["exam-submit", "示例内联试卷文本", "1. A"])
    assert "旧键原因" in capsys.readouterr().out

    # 两键全缺 → 兜底文案
    monkeypatch.setattr(exam, "grade_exam_paper",
                        lambda *a, **kw: {"success": False})
    study._cmd_exam_submit(["exam-submit", "示例内联试卷文本", "1. A"])
    assert "未识别到有效作答" in capsys.readouterr().out


# ───────────── 开放题部分分取舍（设计取舍钉住） ─────────────

def test_open_partial_credit_binarized_to_review(monkeypatch):
    """开放题判分引擎返回 match_level=1（0~10 部分分）时：
    该题 0 分、进待复核、不落总分 —— 部分分故意不折算（保守设计）。
    """
    calls = []

    def fake_grade(**kw):
        calls.append(kw)
        return SimpleNamespace(match_level=1, reason="部分分（0-10 得 4 分）：要点不全")

    from tools.skills import open_grader as open_grader_mod
    targets = {id(open_grader_mod): open_grader_mod}
    try:  # 兼容双路径导入（tools/ 被加入 sys.path 时 skills.* 是独立副本）
        import skills.open_grader as alias_mod  # noqa: WPS433
        targets[id(alias_mod)] = alias_mod
    except ImportError:
        pass
    for mod in targets.values():
        monkeypatch.setattr(mod, "grade_open_question", fake_grade)

    writes = []
    monkeypatch.setattr(exam, "error_logger", _fake_logger(writes))
    keys = [{"id": 1, "title": "示例开放题", "question": "示例论述题干",
             "grading_mode": "open", "score": 5}]
    result = exam.grade_exam_paper(_paper(keys), "1. 示例论述作答内容")
    assert calls, "未走到开放题判分引擎"
    assert result["success"]
    assert result["score"] == 0, "部分分不得折算进总分（保守取舍）"
    assert result["graded_max"] == 0
    assert len(result["need_review"]) == 1
    assert "部分分" in result["report"]
    assert writes == [], "待复核（部分分）不得产生错题回写"
