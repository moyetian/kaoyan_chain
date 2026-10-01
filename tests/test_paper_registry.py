from pathlib import Path

from tools.skills.paper_registry import PaperRegistry, paper_id_for_content


def test_paper_id_is_content_addressed_and_normalized(tmp_path: Path):
    first = "题目一\r\n答案  \r\n"
    second = " 题目一\n答案\n"
    assert paper_id_for_content(first) == paper_id_for_content(second)

    registry = PaperRegistry(tmp_path)
    record = registry.register_paper(
        first,
        subject="pro",
        source_name="真题",
        card_path=tmp_path / "card.md",
    )
    assert record["paper_id"].startswith("PAPER-")
    assert registry.find_by_content(second)["paper_id"] == record["paper_id"]
    assert len(registry.list_papers()) == 1


def test_paper_registry_upsert_merges_exam_paths(tmp_path: Path):
    registry = PaperRegistry(tmp_path)
    first = registry.register_paper(
        "同一试卷",
        subject="math",
        metadata={"exam_paper_id": "EXAM-MATH-1"},
    )
    second = registry.register_paper(
        "同一试卷",
        key_path=tmp_path / ".memory" / "exam_keys" / "EXAM-MATH-1.json",
        metadata={"verified": True},
    )
    assert second["paper_id"] == first["paper_id"]
    assert second["metadata"] == {"exam_paper_id": "EXAM-MATH-1", "verified": True}
    assert registry.find_by_metadata("exam_paper_id", "EXAM-MATH-1")[0]["key_path"].endswith(
        "EXAM-MATH-1.json"
    )
