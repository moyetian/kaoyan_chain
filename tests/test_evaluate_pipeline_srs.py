# -*- coding: utf-8 -*-
"""evaluate_pipeline --srs 修复回归测试（2026-09-24，规划批次 A1）。

背景（真实缺陷，非推测）：
    旧版 ``_predict_recall_probability`` 对 ``stage_before=0``（未复习卡）直接取
    py-fsrs 的 retrievability —— 而 py-fsrs 对全新 Card 返回 0 是**正确行为**。
    评测端把它解读为"预测必忘"，于是 34 条真实日志（全部 stage0 + good）把
    RMSE 拉到 1.0 / LogLoss 拉到 13.8，评测以"红灯"（退出码 1）结束。

修复后的口径（本测试钉住，不得回退）：
    1. stage0 事件必须被**剔除**（计入 ``excluded["stage0"]``）而不是参与打分；
    2. stage>=1 的预测 p ∈ (0,1)，且随 days_late 单调下降；
    3. due_before 不可解析 / rating 非法 → 计入 excluded 对应分类；
    4. 三条 CLI 路径（达标 0 / 不达标 1 / 样本不足 2）由 fixture 日志驱动，
       供 CI 门禁复用（见规划 A4）。

数据卫生（同批 A1）：
    ``.memory/review_log.jsonl`` 曾积累 36 条同质测试残留（ky_suite 沙箱漏掉
    ``REVIEW_LOG_FILE`` 所致），已归档为 ``srs_log_residue_all_stage0.jsonl``
    并清空活文件；ky_suite 沙箱同步补漏，防止再次污染真实样本池。
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evaluate_pipeline import (  # noqa: E402
    _predict_recall_probability,
    evaluate_srs_benchmark,
    load_review_log,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _event(stage: int, rating: str, due: str, late: int = 0) -> dict:
    return {
        "ts": "2026-10-01 09:00:00",
        "subject": "math",
        "title": "示例错题",
        "stage_before": stage,
        "rating": rating,
        "due_before": due,
        "days_late": late,
    }


# ── 1. stage0 剔除（本次修复的核心回归点） ──────────────────────────────────

def test_stage0_excluded_not_scored():
    """34 条 stage0 不得再产出 RMSE=1.0 的红灯，而是明确"不可评测"。"""
    events = [_event(0, "good", "2026-10-01") for _ in range(34)]
    res = evaluate_srs_benchmark(events)
    assert res["evaluable"] is False
    assert res["samples"] == 0
    assert res["excluded"].get("stage0") == 34
    assert "stage0" in res["reason"], "不可评测原因必须点名 stage0 口径"


def test_predict_stage0_returns_reason():
    """函数层双保险：即使调用方忘剔除，stage0 也返回 (None, 'stage0')。"""
    p, reason = _predict_recall_probability(0, "2026-10-01", 0)
    assert p is None
    assert reason == "stage0"


# ── 2. stage>=1 的预测曲线 ─────────────────────────────────────────────────

def test_stage1_prediction_in_unit_interval():
    p, reason = _predict_recall_probability(1, "2026-10-01", 0)
    assert reason == ""
    assert 0.0 < p < 1.0
    assert p > 0.5, "stage1 按时复测的保留率应显著高于 0.5"


def test_prediction_monotonic_with_late_days():
    """迟延越久，预测的可回想概率必须单调下降。"""
    probs = []
    for late in (0, 1, 3, 7, 14, 60):
        p, reason = _predict_recall_probability(1, "2026-10-01", late)
        assert reason == ""
        probs.append(p)
    assert all(probs[i] > probs[i + 1] for i in range(len(probs) - 1)), probs


# ── 3. 非法输入的分类计数 ──────────────────────────────────────────────────

def test_invalid_due_excluded():
    events = [_event(1, "good", ""), _event(1, "good", "not-a-date")]
    res = evaluate_srs_benchmark(events)
    assert res["excluded"].get("invalid_due") == 2
    assert res["samples"] == 0


def test_invalid_rating_excluded():
    res = evaluate_srs_benchmark([_event(1, "unknown", "2026-10-01")])
    assert res["excluded"].get("invalid_rating") == 1


def test_due_before_timezone_suffix_tolerated():
    """due_before 带多余空白/时间后缀时按前 10 位解析（error_logger 写入口径）。"""
    p, reason = _predict_recall_probability(1, " 2026-10-01 ", 0)
    assert reason == "" and p is not None


# ── 4. fixture 三路径（CI 门禁将复用同一组文件） ────────────────────────────

def test_fixture_ok_passes():
    res = evaluate_srs_benchmark(load_review_log(FIXTURES / "srs_log_ok.jsonl"))
    assert res["evaluable"] is True
    assert res["passed"] is True
    assert res["rmse"] <= 0.35 and res["logloss"] <= 0.65


def test_fixture_fail_not_passed():
    res = evaluate_srs_benchmark(load_review_log(FIXTURES / "srs_log_fail.jsonl"))
    assert res["evaluable"] is True
    assert res["passed"] is False
    assert res["rmse"] > 0.35


def test_fixture_insufficient_not_evaluable():
    res = evaluate_srs_benchmark(load_review_log(FIXTURES / "srs_log_insufficient.jsonl"))
    assert res["evaluable"] is False
    assert res["samples"] == 5
    assert res["excluded"].get("stage0") == 10


@pytest.mark.parametrize(
    "name,expected_exit",
    [
        ("srs_log_ok.jsonl", 0),
        ("srs_log_fail.jsonl", 1),
        ("srs_log_insufficient.jsonl", 2),
    ],
)
def test_cli_exit_codes(name, expected_exit):
    """端到端：退出码语义 0/1/2 必须稳定（CI 门禁的判定依据）。"""
    r = subprocess.run(
        [sys.executable, "tools/evaluate_pipeline.py", "--srs",
         "--log", str(FIXTURES / name)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120,
    )
    assert r.returncode == expected_exit, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"


def test_cli_insufficient_reports_excluded_detail():
    """不可评测输出必须打印 excluded 明细（用户能看懂为什么测不了）。"""
    r = subprocess.run(
        [sys.executable, "tools/evaluate_pipeline.py", "--srs",
         "--log", str(FIXTURES / "srs_log_insufficient.jsonl")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120,
    )
    assert "已排除样本" in r.stdout
    assert "stage0" in r.stdout


# ── 5. 真实残留归档回归（.memory 清空前的 36 条同质记录快照） ────────────────

def test_real_residue_archive_all_stage0_not_evaluable():
    """归档的真实测试残留（36 条全 stage0）必须走"不可评测"，不得再现红灯。

    该 fixture 是 ``.memory/review_log.jsonl`` 于 2026-09-24 清空前的原始快照：
    ky_suite 沙箱只改了 ``error_logger.ROOT``，漏掉导入期固化的
    ``REVIEW_LOG_FILE``，导致每次运行追加一条同质 stage0 记录。这些记录
    曾把评测误导为 RMSE=1.0 的红灯 —— 修复后必须全部剔除，如实报"样本不足"。
    """
    log = load_review_log(FIXTURES / "srs_log_residue_all_stage0.jsonl")
    assert len(log) == 36
    res = evaluate_srs_benchmark(log)
    assert res["evaluable"] is False
    assert res["samples"] == 0
    assert res["excluded"].get("stage0") == 36


def test_cli_real_residue_returns_exit_2():
    """端到端：旧版红灯输入（真实残留）现在必须返回退出码 2（样本不足）。"""
    r = subprocess.run(
        [sys.executable, "tools/evaluate_pipeline.py", "--srs",
         "--log", str(FIXTURES / "srs_log_residue_all_stage0.jsonl")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120,
    )
    assert r.returncode == 2, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "stage0" in r.stdout
