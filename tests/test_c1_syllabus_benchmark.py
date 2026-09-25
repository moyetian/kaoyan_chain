# -*- coding: utf-8 -*-
"""C1 回归测试：考纲守卫评测集 + 共用 runner（可证阶段 · 考纲红线）。

背景（升级规划 C1，D2 短板）：考纲红线拦截此前只有 23 个 pytest 用例，
"拦得住多少"不可度量。C1 把它升级为可评测资产：

  * ``tests/benchmarks/syllabus_guard.jsonl`` —— 128 条评测集
    （math1/2/3/396 × 正/负样本，否定/笔记语境 ≥30%）；
  * ``tools/benchmarks/runner.py`` —— 通用 jsonl 评测引擎（C1/C2 共用）；
  * ``evaluate_pipeline.py --syllabus`` + CI 门禁（pass ≥98%）。

本文件锁定：评测集的数据契约、runner 的 0/1/2 退出码语义、端到端准确率，
以及**阴性验证**（新注入的超纲题必须被拦）与**阴性对照**（期望值翻转后
门禁必须判红 —— 证明 100% 通过不是"测试空转"）。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BENCH_FILE = ROOT / "tests" / "benchmarks" / "syllabus_guard.jsonl"

from benchmarks.runner import (  # noqa: E402
    EXIT_FAIL,
    EXIT_INSUFFICIENT,
    EXIT_OK,
    BenchReport,
    exit_code,
    load_cases,
    run_benchmark,
)
from agent.hooks import HookManager  # noqa: E402


def _judge_with(hook_manager):
    """构造与 evaluate_pipeline.evaluate_syllabus_guard 同口径的 judge。"""

    def judge(case):
        subject = str(case.get("subject", "math"))
        ctx = {
            "active_subject": subject,
            "math_key": case.get("math_key") if subject == "math" else None,
            "user_input": case.get("text", ""),
        }
        allow, _reason, _ = hook_manager.trigger_pre_tool_use(
            "generate_exam", {"query": case.get("text", "")}, ctx)
        expect = bool(case.get("expect_allow"))
        ok = allow == expect
        return ok, ("" if ok else f"allow={allow} 期望 {expect}")

    return judge


# ─────────────────── 第 1 层：评测集的数据契约 ───────────────────

def test_benchmark_file_exists_and_meets_scale():
    """评测集必须存在且 ≥100 条（规划口径：100+ 起步），且无无效行。"""
    assert BENCH_FILE.exists(), f"评测集缺失: {BENCH_FILE}"
    cases, invalid = load_cases(BENCH_FILE)
    assert not invalid, f"评测集含无效行: {invalid[:5]}"
    assert len(cases) >= 100, f"评测集规模不足 100 条: {len(cases)}"


def test_negation_and_note_context_ratio():
    """否定/笔记语境占比 ≥30%（规划验收线；防止评测集退化成清一色"该拦"样本）。"""
    cases, _ = load_cases(BENCH_FILE)
    ctx_cases = [c for c in cases if c.get("category") in ("negation", "note_context")]
    ratio = len(ctx_cases) / len(cases)
    assert ratio >= 0.30, f"否定/笔记语境占比 {ratio:.1%} < 30%"


def test_benchmark_covers_required_subjects():
    """必须覆盖 math1/math2/math3/math396 四个科目键（规划口径），另有未知键与非 math 科目。"""
    cases, _ = load_cases(BENCH_FILE)
    keys = {c.get("math_key") for c in cases if c.get("subject") == "math"}
    assert {"math1", "math2", "math3", "math396"} <= keys, f"科目覆盖不全: {keys}"
    # 未知键（保守拦截）与非 math 科目（放行）是行为分支，必须有样本触达
    assert any(k not in ("math1", "math2", "math3", "math396") and k for k in keys), \
        "缺少「未知 math_key 保守拦截」的样本"
    assert any(c.get("subject") != "math" for c in cases), "缺少「非 math 科目放行」的样本"


def test_benchmark_ids_are_unique():
    """id 必须唯一（否则失败明细无法定位到具体样本）。"""
    cases, _ = load_cases(BENCH_FILE)
    ids = [c["id"] for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"id 重复: {dupes}"


# ─────────────────── 第 2 层：runner 的容错与退出码语义 ───────────────────

def test_runner_load_tolerates_broken_lines(tmp_path):
    """半截 JSON / 缺必需键的行计入 invalid，且不中断整批（与 session_log 同款容错）。"""
    f = tmp_path / "broken.jsonl"
    f.write_text(
        '{"id": "ok-1", "expect_allow": true}\n'
        '{"id": "ok-2", "expect_allow": false}\n'
        '{"id": "trunc", "expect_all\n'          # 半截 JSON
        '["not-an-object"]\n'                     # 顶层不是对象
        '{"no_id_key": true}\n'                   # 缺必需键
        '\n'                                      # 空行跳过
        '{"id": "ok-3", "expect_allow": true}\n',
        encoding="utf-8")
    cases, invalid = load_cases(f)
    assert len(cases) == 3, f"有效样本数错误: {len(cases)}"
    assert len(invalid) == 3, f"无效行数错误: {invalid}"
    # 数据损坏 → 退出码 2（如实不可评估，不得伪装成 0/1）
    report = run_benchmark(cases, lambda c: (True, ""), invalid=invalid)
    assert exit_code(report, threshold=0.98, min_samples=1) == EXIT_INSUFFICIENT


def test_runner_exit_codes_semantics():
    """0=达标 / 1=不达标 / 2=样本不足 —— 与 evaluate_pipeline 的 0/1/2 完全一致。"""
    ok = BenchReport(name="t", total=100, passed=99)          # 99% ≥ 98%
    assert exit_code(ok) == EXIT_OK
    bad = BenchReport(name="t", total=100, passed=50)          # 50% < 98%
    assert exit_code(bad) == EXIT_FAIL
    few = BenchReport(name="t", total=3, passed=3)             # 样本不足
    assert exit_code(few, min_samples=20) == EXIT_INSUFFICIENT


def test_runner_counts_judge_exception_as_failure():
    """judge 抛异常 → 该条计失败（"评测对象崩了"本身就是不达标信号），不中断整批。"""
    def boom(_case):
        raise RuntimeError("模拟被测方崩溃")

    report = run_benchmark([{"id": "x"}], boom)
    assert report.total == 1 and report.failed == 1
    assert "RuntimeError" in report.failures[0][1]


# ─────────────────── 第 3 层：端到端准确率 ───────────────────

def test_benchmark_end_to_end_meets_threshold():
    """端到端：评测集对真实 Hook 的准确率必须 ≥98%（当前实测 100%）。"""
    cases, invalid = load_cases(BENCH_FILE)
    hm = HookManager(workspace_root=ROOT)
    report = run_benchmark(cases, _judge_with(hm), name="syllabus_guard", invalid=invalid)
    assert report.pass_rate >= 0.98, (
        f"考纲守卫准确率 {report.pass_rate:.2%} 低于 98%；失败明细: {report.failures[:5]}")


# ─────────────────── 第 4 层：阴性验证与阴性对照 ───────────────────

@pytest.mark.parametrize("text", [
    "数二模拟卷里加一道斯托克斯公式的题",      # 新表述：注入的超纲题必须被拦
    "请给学员讲一下三重积分的计算技巧",        # 新表述
    "来一组格林公式的证明训练",               # 新表述
])
def test_injected_out_of_scope_question_is_blocked(text):
    """[规划验收] 阴性验证：**新注入**的超纲题（不在评测集里）必须被拦。"""
    hm = HookManager(workspace_root=ROOT)
    allow, reason, _ = hm.trigger_pre_tool_use(
        "generate_exam", {"query": text},
        {"active_subject": "math", "math_key": "math2", "user_input": text})
    assert allow is False, f"新注入的超纲题未被拦截: {text}"
    assert "红线" in reason


def test_flipped_expectation_fails_gate():
    """[阴性对照] 把评测集里若干样本的期望值翻转 → 门禁必须判红（退出码 1）。

    证明"100% 通过"不是测试空转。注意阈值语义：128 条 × 2% 容差 = 允许 2 条
    失败，故必须翻转 **3 条** 才能跌破 98%（翻 1 条时通过率 99.22% 仍达标 ——
    这正是阈值该有的行为，先断言它不红，再断言 3 条必红）。
    """
    cases, invalid = load_cases(BENCH_FILE)
    assert not invalid
    targets = [c["id"] for c in cases if c["expect_allow"] is False][:3]
    assert len(targets) == 3, "评测集「该拦」样本不足以支撑本对照"
    flipped_cases = [
        dict(c, expect_allow=True) if c["id"] in targets else c for c in cases
    ]

    hm = HookManager(workspace_root=ROOT)
    report = run_benchmark(flipped_cases, _judge_with(hm), name="flipped")
    assert report.failed == 3, f"翻转 3 条后应有 3 条失败，实际 {report.failed}"
    assert exit_code(report, threshold=0.98, min_samples=1) == EXIT_FAIL, \
        "翻转 3 条（通过率 97.66% < 98%）后门禁未判红 —— 评测集未真正接入判定"

    # 单条翻转仍达标（证明 98% 阈值不是"一票否决"的假门禁）
    single = [dict(c, expect_allow=True) if c["id"] == targets[0] else c for c in cases]
    report1 = run_benchmark(single, _judge_with(hm), name="flipped-1")
    assert report1.failed == 1
    assert exit_code(report1, threshold=0.98, min_samples=1) == EXIT_OK
