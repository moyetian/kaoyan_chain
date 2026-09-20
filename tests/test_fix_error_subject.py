# -*- coding: utf-8 -*-
"""P2 回归：错题归档的科目归一化。

缺陷背景（审查报告 P2）：
    ``log_error_record`` 曾用 ``SUBJECT_DIRS.get(subject, "01-数学")`` 兜底，
    任何无法识别的科目名（含 LLM 传入的中文名）都会被**静默**写进数学错题本，
    且回报「已成功归档」—— 不考数学的文科生错题全堆进 01-数学，
    专业课错题队列永远为空。

修复后约定：
    1. 中文名/别名可正确归一化到 math / eng / pol / pro；
    2. 无法识别时**显式报错**（ValueError），绝不静默回退数学。
"""
import pytest

from tools.skills import error_logger as el


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """把 error_logger.ROOT 指向临时目录，避免污染真实错题本。"""
    monkeypatch.setattr(el, "ROOT", tmp_path)
    for folder in el.SUBJECT_DIRS.values():
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _log(subject, title="探针错题"):
    return el.log_error_record(
        subject=subject,
        title=title,
        error_type="概念漏洞",
        detail="探针详情",
        prescription="探针处方",
    )


def _records_under(root, folder):
    """递归收集某科目目录下当天生成的错题记录文件。"""
    return list((root / folder).rglob("错题记录_*.md"))


@pytest.mark.parametrize(
    "subject,folder",
    [
        ("数学", "01-数学"),
        ("数二", "01-数学"),
        ("数三", "01-数学"),
        ("英语", "02-英语"),
        ("英一", "02-英语"),
        ("政治", "03-思想政治理论"),
        ("思想政治理论", "03-思想政治理论"),
        ("思政", "03-思想政治理论"),
        ("专业课", "04-专业课"),
        ("自命题", "04-专业课"),
        ("408", "04-专业课"),
    ],
)
def test_chinese_subject_names_land_in_correct_folder(sandbox, subject, folder):
    msg = _log(subject)
    assert folder in msg, f"{subject} 未归档到 {folder}: {msg}"
    assert _records_under(sandbox, folder), f"{subject} 未生成错题记录文件"

    # 非数学科目绝不允许串写进数学错题本
    if folder != "01-数学":
        assert not _records_under(sandbox, "01-数学"), (
            f"{subject} 被静默串写进 01-数学 错题本"
        )


def test_english_uses_dedicated_folder(sandbox):
    msg = _log("英语")
    assert "错题与长难句本" in msg


def test_unknown_subject_raises_instead_of_silent_math(sandbox):
    with pytest.raises(ValueError):
        _log("量子力学")

    # 关键断言：报错时数学错题本必须保持干净
    assert not _records_under(sandbox, "01-数学"), "无法识别的科目被静默写进数学错题本"


def test_empty_subject_raises(sandbox):
    with pytest.raises(ValueError):
        _log("")
    assert not _records_under(sandbox, "01-数学")


def test_normalize_subject_aliases():
    assert el.normalize_subject("数二") == "math"
    assert el.normalize_subject("MATH") == "math"
    assert el.normalize_subject("英二") == "eng"
    assert el.normalize_subject("思想政治理论") == "pol"
    assert el.normalize_subject("专业课") == "pro"
    with pytest.raises(ValueError):
        el.normalize_subject("不明科目")
