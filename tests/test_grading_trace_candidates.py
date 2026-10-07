# -*- coding: utf-8 -*-
"""D3 元测试：判卷 traces 分歧候选提取（``tools/benchmarks/grading_trace_candidates.py``）。

被测脚本把判分链路留痕（``data/grading/traces/*.jsonl``）中
``divergence`` 高或 ``arbitration.flipped`` 的行，转成评测集同构的
**半成品候选**（expected 留空，人工确认后并入
``tests/benchmarks/grading_pilot.jsonl``）。本文件按四层锁定：

  1. 筛选口径：``divergence >= 阈值``（含等于）或 ``flipped is True``；
     缺失 / 非法 divergence 不误判，flipped 非布尔不误判；
  2. 输出契约：id 编号（按 ts 排序后编号）、评测集同构字段、``_trace``
     溯源块、多文件合并后按 ts 升序；
  3. 容错与统计：坏行（半截 JSON）跳过并计数不崩，stderr 统计口径
     ``扫描 N 行 / 候选 M 条 / 坏行 K 条``；
  4. CLI：``--out`` 写文件（stdout 不放候选）、缺省 stdout 输出、
     空目录 / 目录不存在 → 明确提示且退出码 0、默认目录相对工作区根
     （打桩验证，不碰真实工作区）。

隔离约定（硬约束）：所有用例都显式传 ``--traces-dir`` 到 ``tmp_path``，
或对 ``resolve_workspace_root`` 打桩 —— **禁止无参调用真实解析**，
绝不扫描真实工作区的 ``data/grading/traces``。测试数据全部中性化
（subject 用 pro/pol，题目为「测试题」类文字，无任何真实身份信息）。
"""

import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmarks import grading_trace_candidates as gtc  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 夹具构造（全部落在 tmp_path 内，绝不触碰真实工作区）
# ════════════════════════════════════════════════════════════════

def _trace_line(
    ts: str,
    *,
    divergence: Optional[float] = None,
    flipped: bool = False,
    question: str = "测试题",
    subject: str = "pro",
    student_answer: str = "测试作答",
    prompt_version: str = "v1",
) -> str:
    """构造一行 traces 记录（中性化数据；结构对齐 D3 契约）。"""
    obj = {
        "question_id": "q-测试",
        "paper_id": "p-测试",
        "prompt_version": prompt_version,
        "subject": subject,
        "stage_rubric": {
            "count": 4,
            "hit_point_ids": [1, 2],
            "derived_from_question": True,
            "model": "mock",
        },
        "stage_review": [{"reviewer": "a", "score": 8, "mistake_type": "无"}],
        "divergence": divergence,
        "arbitration": {"used": flipped, "flipped": flipped, "final_score": 7},
        "final": {"score": 7, "match_level": 2, "degraded": False},
        "question": question,
        "student_answer": student_answer,
        "ts": ts,
    }
    return json.dumps(obj, ensure_ascii=False)


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# 第 1 层 + 第 2 层 + 第 3 层：筛选口径 / 输出契约 / 容错统计
# ════════════════════════════════════════════════════════════════

def test_筛选字段排序与坏行容错(tmp_path):
    """3 有效行（高 divergence / flipped / 都不满足）+ 1 坏行，跨两个文件。"""
    d = tmp_path / "traces"
    d.mkdir()
    _write(
        d / "2026-10-06.jsonl",
        _trace_line("2026-10-06T12:00:00", divergence=3.5, question="分歧题"),
        _trace_line("2026-10-06T09:00:00", divergence=0.5, question="普通题"),
        '{"question_id": "broken", "divergence": 9.9',  # 半截 JSON
    )
    _write(
        d / "2026-10-05.jsonl",
        _trace_line("2026-10-06T10:00:00", divergence=0.0, flipped=True,
                    question="翻盘题"),
    )

    candidates, stats = gtc.collect_candidates(d, min_divergence=2.0)

    assert stats.scanned == 4
    assert stats.bad == 1
    assert stats.candidates == 2
    # 按 ts 升序编号：翻盘题(10:00) 在 分歧题(12:00) 之前
    assert [c["id"] for c in candidates] == ["cand-01", "cand-02"]
    assert [c["question"] for c in candidates] == ["翻盘题", "分歧题"]

    first, second = candidates
    # 评测集同构字段（expected 留空，人工确认后填）
    assert first["subject"] == "pro"
    assert first["student_answer"] == "测试作答"
    assert first["reference_answer"] == ""
    assert first["key_points"] == []
    assert first["expected"] == {"hit_points": [], "total": 0, "mistake_type": ""}
    # _trace 溯源块
    assert first["_trace"] == {
        "source_file": "2026-10-05.jsonl",
        "ts": "2026-10-06T10:00:00",
        "divergence": 0.0,
        "flipped": True,
        "prompt_version": "v1",
    }
    assert second["_trace"]["source_file"] == "2026-10-06.jsonl"
    assert second["_trace"]["divergence"] == 3.5
    assert second["_trace"]["flipped"] is False


def test_阈值含等于且可调(tmp_path):
    """divergence 恰好等于阈值 → 入选；低于阈值 → 落选；调低阈值后入选。"""
    d = tmp_path / "traces"
    d.mkdir()
    _write(
        d / "a.jsonl",
        _trace_line("2026-10-06T08:00:00", divergence=2.0),
        _trace_line("2026-10-06T09:00:00", divergence=1.9),
    )

    candidates, stats = gtc.collect_candidates(d, min_divergence=2.0)
    assert stats.candidates == 1
    assert candidates[0]["_trace"]["divergence"] == 2.0

    candidates2, stats2 = gtc.collect_candidates(d, min_divergence=1.5)
    assert stats2.candidates == 2
    assert [c["_trace"]["divergence"] for c in candidates2] == [2.0, 1.9]


def test_字段缺失或非法不误判(tmp_path):
    """divergence 缺失 / 非数值 / NaN，flipped 非布尔 → 不入选、不算坏行。"""
    d = tmp_path / "traces"
    d.mkdir()
    _write(
        d / "a.jsonl",
        json.dumps({"question": "无 divergence", "ts": "2026-10-06T08:00:00"}),
        json.dumps({"question": "字符串 divergence", "divergence": "9.9",
                    "ts": "2026-10-06T09:00:00"}),
        json.dumps({"question": "flipped 字符串", "divergence": 0.0,
                    "arbitration": {"flipped": "true"},
                    "ts": "2026-10-06T10:00:00"}),
    )

    candidates, stats = gtc.collect_candidates(d, min_divergence=2.0)

    assert candidates == []
    assert stats.scanned == 3
    assert stats.bad == 0  # 合法 JSON 但字段不满足 → 不是坏行


# ════════════════════════════════════════════════════════════════
# 第 4 层：CLI 行为
# ════════════════════════════════════════════════════════════════

def test_缺省stdout输出与统计(tmp_path, capsys):
    """缺省（无 --out）候选打到 stdout；统计行到 stderr，口径完整。"""
    d = tmp_path / "traces"
    d.mkdir()
    _write(
        d / "a.jsonl",
        _trace_line("2026-10-06T08:00:00", divergence=2.5, question="边界题"),
        '{"question_id": "broken", "divergence": 9.9',
    )

    rc = gtc.main(["--traces-dir", str(d)])

    assert rc == 0
    captured = capsys.readouterr()
    rows = [json.loads(x) for x in captured.out.splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["question"] == "边界题"
    assert "扫描 2 行 / 候选 1 条 / 坏行 1 条" in captured.err


def test_out写文件(tmp_path, capsys):
    """--out：候选落盘、stdout 不放候选、stderr 提示写盘位置。"""
    d = tmp_path / "traces"
    d.mkdir()
    _write(d / "a.jsonl", _trace_line("2026-10-06T08:00:00", divergence=2.5))
    out = tmp_path / "sub" / "candidates.jsonl"

    rc = gtc.main(["--traces-dir", str(d), "--out", str(out)])

    assert rc == 0
    assert out.is_file()
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    assert len(rows) == 1
    assert rows[0]["id"] == "cand-01"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "已写入 1 条候选" in captured.err
    assert "扫描 1 行 / 候选 1 条 / 坏行 0 条" in captured.err


def test_空目录零候选退出码零(tmp_path, capsys):
    """空目录：明确提示 + 0 候选 + 退出码 0（不是错误场景）。"""
    d = tmp_path / "empty"
    d.mkdir()

    rc = gtc.main(["--traces-dir", str(d)])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "没有 *.jsonl" in captured.err
    assert "扫描 0 行 / 候选 0 条 / 坏行 0 条" in captured.err


def test_目录不存在提示且退出码零(tmp_path, capsys):
    """目录不存在：明确提示 + 退出码 0（判分链路还没留痕）。"""
    missing = tmp_path / "nope"

    rc = gtc.main(["--traces-dir", str(missing)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "目录不存在" in captured.err
    assert "扫描 0 行 / 候选 0 条 / 坏行 0 条" in captured.err


def test_默认目录相对工作区根(tmp_path, monkeypatch, capsys):
    """无 --traces-dir 时扫描 <工作区根>/data/grading/traces。

    对 ``resolve_workspace_root`` 打桩到 tmp_path —— 不碰真实工作区。
    """
    fake_root = tmp_path / "ws"
    traces = fake_root.joinpath(*gtc.TRACES_DIR_REL)
    traces.mkdir(parents=True)
    _write(traces / "t.jsonl", _trace_line("2026-10-06T08:00:00", divergence=5.0))

    monkeypatch.setattr(gtc, "resolve_workspace_root", lambda start=None: fake_root)

    rc = gtc.main([])

    assert rc == 0
    captured = capsys.readouterr()
    assert "扫描 1 行 / 候选 1 条 / 坏行 0 条" in captured.err
