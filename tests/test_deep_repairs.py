"""回归：护理题源门禁、跨入口科目推断与组卷去重。"""
from __future__ import annotations

import json

from tools.skills import exam_composer, material_ingestion
from tools.cli.commands import study


def test_material_ingest_rejects_incomplete_choice(tmp_path):
    pipe = material_ingestion.MaterialIngestionPipeline(workspace_root=tmp_path)
    raw = (
        "一、单项选择题\n\n"
        "1. 成人心肺复苏时胸外按压深度应为（）\n"
        "A. 3-4cm\nC. 7-8cm\n"
        "【答案】B\n"
    )
    res = pipe.ingest_text(raw, subject="pro", source_name="残缺护理题")
    assert res["success"] is False
    assert res["invalid_choices"] == 1
    assert "A-D" in res["msg"]


def test_compose_deduplicates_same_stem_across_files(tmp_path, monkeypatch):
    monkeypatch.setattr(exam_composer, "ROOT", tmp_path)
    monkeypatch.setattr(exam_composer, "error_logger", None)
    card = {
        "file_name": "a.md", "title": "护理题", "question": "同一道护理题\nA.1\nB.2",
        "origin": exam_composer.ORIGIN_WHITELIST, "standard_answer": "B",
    }
    other = dict(card, file_name="b.md")
    monkeypatch.setattr(exam_composer, "_load_whitelist_cards", lambda *a, **k: [card, other])
    res = exam_composer.compose_exam_paper("pro", count=2, include_weak=False, save_file=False)
    assert res["success"] is True
    assert res["count"] == 1
    assert res["shortfall"] == 1


def test_variant_cli_infers_nursing_keyword(monkeypatch):
    monkeypatch.setattr(study, "load_config", lambda: {"active_subject": "eng"})
    captured = {}

    class FakeRetriever:
        @staticmethod
        def search_real_variant(subject, keyword):
            captured.update(subject=subject, keyword=keyword)
            return {"subject": subject, "subject_name": "308 " + "护理综合", "keyword": keyword,
                    "is_real_source": False, "source_status": "test", "variants": []}

        @staticmethod
        def format_variant_output(_res):
            return "ok"

    monkeypatch.setitem(__import__("sys").modules, "tools.skills.variant_retriever", FakeRetriever)
    # handler imports through tools.skills; use the actual module object instead
    import tools.skills.variant_retriever as vr
    monkeypatch.setattr(vr, "search_real_variant", FakeRetriever.search_real_variant)
    monkeypatch.setattr(vr, "format_variant_output", FakeRetriever.format_variant_output)
    study._cmd_variant(["variant", "护理程序"])
    assert captured == {"subject": "pro", "keyword": "护理程序"}
