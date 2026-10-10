# -*- coding: utf-8 -*-
"""Regression coverage for 105400 nursing school comparison."""

from tools.intelligence.comparator import SchoolComparator


def test_nursing_compare_keeps_308_and_exposes_structured_subjects():
    result = SchoolComparator().compare(
        "目标院校A",
        "目标院校B",
        "105400 " + "护理",
        quick=True,
    )

    for key in ("info1", "info2"):
        profile = result[key]
        assert profile["subject_codes"] == ["101", "201", "308"]
        assert "301" not in " ".join(profile["majors"])
        assert "8xx" not in " ".join(profile["majors"])
        assert profile["subject_status"] == "candidate_config"

    analysis = result["analysis"]
    assert analysis["subject_codes1"] == analysis["subject_codes2"]
    assert "308" in analysis["subject_diff"]
    assert "301" not in analysis["subject_diff"]
    assert "8xx" not in analysis["subject_diff"]
    assert "308" in result["terminal_report"]
    assert "初试科目（结构化）" in result["markdown_report"]


# ══════════════ COMP-M1：科目集合相同、顺序不同不应误报「有差异」 ══════════════


def test_subjects_equivalence_ignores_order():
    """[COMP-M1 回归] 科目比较必须忽略顺序。

    两校画像的科目**集合相同但排列顺序不同**是完全正常的（来源不同、录入顺序不同），
    旧口径逐位比较 ``codes1 == codes2`` 会误报「科目代码存在差异」，
    把实质一致说成有差异，误导考生做无谓取舍。
    """
    from tools.intelligence.comparator import _subjects_equivalent

    a = [{"code": "101", "name": "思想政治理论"},
         {"code": "201", "name": "外国语"},
         {"code": "308", "name": "护理综合"}]
    b = [{"code": "308", "name": "护理综合"},
         {"code": "101", "name": "思想政治理论"},
         {"code": "201", "name": "外国语"}]
    assert _subjects_equivalent(a, b) is True
    assert _subjects_equivalent(a, list(reversed(a))) is True


def test_subjects_equivalence_still_detects_real_difference():
    """[COMP-M1] 收紧不得收成「什么都算一致」——真差异仍须判不等。"""
    from tools.intelligence.comparator import _subjects_equivalent

    a = [{"code": "101", "name": "思想政治理论"}, {"code": "308", "name": "护理综合"}]
    b = [{"code": "101", "name": "思想政治理论"}, {"code": "201", "name": "外国语"}]
    assert _subjects_equivalent(a, b) is False
    # 顺序无关：把 b 顺序打乱结论不变
    assert _subjects_equivalent(a, list(reversed(b))) is False
    # 同代码但名称不同（如「英语(一)」vs「英语(二)」）不算等价
    c = [{"code": "201", "name": "外国语(一)"}]
    d = [{"code": "201", "name": "外国语(二)"}]
    assert _subjects_equivalent(c, d) is False


def test_nursing_compare_reports_no_difference_when_order_differs(monkeypatch):
    """[COMP-M1 回归·端到端] 两校科目仅顺序不同时，报告应判「一致」而非「有差异」。"""
    from tools.intelligence import comparator as comparator_module

    base_subjects = [
        {"code": "101", "name": "思想政治理论"},
        {"code": "201", "name": "外国语"},
        {"code": "308", "name": "护理综合"},
    ]
    order2 = [base_subjects[2], base_subjects[0], base_subjects[1]]

    def _profile(name, subjects):
        return {
            "name": name,
            "exam_subjects": subjects,
            "subject_status": "candidate_config",
            "subject_source": "测试夹具",
        }

    cmp_ = SchoolComparator()
    diff = cmp_._analyze_differences(
        "甲校", _profile("甲校", list(base_subjects)),
        "乙校", _profile("乙校", order2),
        "105400 护理",
    )
    assert "初试科目一致" in diff["subject_diff"], diff["subject_diff"]
    assert "科目代码存在差异" not in diff["subject_diff"], diff["subject_diff"]
    assert comparator_module is not None


def test_nursing_fallback_replaces_generic_subject_template(monkeypatch):
    from tools.intelligence import comparator as comparator_module

    class _GenericEngine:
        def dynamic_fallback_profile(self, school_name, major_keyword=""):
            return {
                "name": school_name,
                "majors": ["(101)思想政治理论", "(201)外国语", "(301/自命题)业务课一", "(8xx/自命题)业务课二"],
                "catalog_source": "[UNVERIFIED 未核验]",
            }

    monkeypatch.setattr(
        "tools.intelligence.agentic_research.get_research_engine",
        lambda: _GenericEngine(),
    )
    profile = SchoolComparator._fallback_profile("某医科大学", "护理")
    assert [item["code"] for item in profile["exam_subjects"]] == ["101", "201", "308"]
    assert "301" not in " ".join(profile["majors"])
    assert "8xx" not in " ".join(profile["majors"])
    assert profile["subject_status"] == "candidate_config"
