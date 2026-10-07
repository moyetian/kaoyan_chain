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
