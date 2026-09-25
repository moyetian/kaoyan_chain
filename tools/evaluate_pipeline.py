# -*- coding: utf-8 -*-
"""
考研学习链 · 离线评测流水线 (Offline Evaluation Pipeline)

提供四项**真实**评测，用于回答"记忆调度是否校准""引用是否可溯源"
"考纲红线是否拦得住"与"开放题判分是否与预标一致"：

  1. ``--srs``   FSRS 校准度评测（RMSE / LogLoss / 预测-实际保留率偏差）
     数据来源：``.memory/review_log.jsonl``（由 error_logger 在每次复测回写时追加）
     预测模型：以该题**复测前**的档位重建卡片，并把重建链的时间轴平移对齐到
              日志记录的计划到期日，取**实际复测时刻**（到期日 + 迟延天数）的
              可回想概率 p；实际结果 y = 1（good/easy 想起）或 0（hard/again 未想起）。
     有效样本口径：``stage_before >= 1`` 且 ``due_before`` 可解析 —— **未复习卡
              （stage_before=0）不在 FSRS 可校准域**（py-fsrs 对全新 Card 的
              retrievability 返回 0 是正确行为，评测端不得解读为"预测必忘"），
              其余事件计入 excluded 并附原因，不参与指标计算。

  2. ``--ragas`` 引文忠实度评测（C2：反幻觉闸门的功能评测）
     数据来源：``tests/benchmarks/citation_faithfulness.jsonl``（108 条，覆盖
              无据引用 / 伪造 URL / 过期数据 / 跨校混淆 / 二手源冒充 五类），
              按 kind 分发到 ``citation_engine.verify_citations`` /
              ``evidence_engine.build_evidence`` / ``resolve_conflicts`` /
              ``DocumentExtractor`` 四个被测对象。双指标：拦截侧必须 **100%**
              （漏放一条编造引用即红线失守）、放行侧 ≥98%（误拦容错）。

  3. ``--syllabus`` 考纲守卫评测（C1：考纲红线拦截的功能评测）
     数据来源：``tests/benchmarks/syllabus_guard.jsonl``（math1/2/3/396 × 正/负
              样本，否定/笔记语境 ≥30%），逐条跑 ``agent.hooks`` 的
              ``syllabus_guard_hook`` 并统计"该拦的拦、该放的放"准确率
              （达标阈值 98%）。完全离线、确定性。

  4. ``--grading`` 开放题判分评测（C4 pilot：与 AI 预标的一致率）
     数据来源：``tests/benchmarks/grading_pilot.jsonl``（30 份 AI 预标：专业课 12 /
              政治 10 / 英语 8）+ ``grading_snapshots.jsonl``（真实 LLM 响应快照，
              由 ``--grading-live`` 一次性采集后冻结）。三指标：采分点命中一致率 /
              总分 MAE / 错因一致率。**命名诚实化**：AI 预标下测的是"与预标一致率"
              （AI 对 AI），不是人工金标准准确率；正式 MAE / Kappa 目标待人工标注
              量上来后再定。**不进 CI 门禁**（元测试钉住）——pilot 阶段尚无正式
              达标阈值，快照回放只用于本地回归与基线观测。回放完全离线、零成本。

退出码约定（便于 CI 判定）
  0 = 已完成评测且指标达标；1 = 已完成评测但指标未达标；2 = **样本不足，无法评测**。

重要约定：**样本不足时绝不编造指标**。历史版本会打印
``✓ FSRS algorithm metrics: RMSE=0.042 (Mocked)`` 这类硬编码分数，
其格式与真实评测完全一致，极易被误当作"已验证"。本版已彻底移除该行为。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
for _p in (str(ROOT), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REVIEW_LOG_FILE = ROOT / ".memory" / "review_log.jsonl"

#: 低于该样本量视为"不足以给出可信结论"
MIN_SRS_SAMPLES = 20

#: 校准达标阈值（RMSE 越小越好；LogLoss 越低越好）
RMSE_TARGET = 0.35
LOGLOSS_TARGET = 0.65


# ════════════════════════════════════════════════════════════════
# 1) FSRS 校准度评测
# ════════════════════════════════════════════════════════════════

def load_review_log(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """读取复测事件日志；文件不存在或行损坏时跳过坏行而非整体失败。"""
    target = Path(path) if path else REVIEW_LOG_FILE
    if not target.exists():
        return []
    events: List[Dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except json.JSONDecodeError:
            continue
    return events


def _predict_recall_probability(
    stage_before: int, due_before: str, days_late: int
) -> Tuple[Optional[float], str]:
    """预测"在本次复测时刻能想起"的概率；返回 ``(p, reason)``。

    重建方式：用 ``stage_before`` 次 good 复习快进记忆稳定性（与
    ``fsrs_scheduler.compute_next_interval`` 同一套快进逻辑），并把重建链的
    时间轴**平移对齐到日志记录的计划到期日**（``due_before``），使
    "上次复习 → 计划到期"的间隔与真实调度一致；再取卡片在
    "计划到期日 + 迟延天数"时刻的可回想概率。

    不可用样本返回 ``(None, reason)``：

    - ``"stage0"``：``stage_before == 0`` 的未复习卡不在可校准域（历史版本
      曾把 py-fsrs 对全新卡的 retrievability=0 当成"预测必忘"，导致 RMSE=1.0）。
    - ``"invalid_due"``：``due_before`` 缺失或不可解析，无法对齐时间轴。
    - ``"predict_failed"``：重建或取值失败（依赖缺失、参数异常等）。
    """
    try:
        stage = max(0, int(stage_before or 0))
    except (TypeError, ValueError):
        return None, "predict_failed"
    if stage < 1:
        return None, "stage0"

    due_text = str(due_before or "").strip()[:10]
    if not due_text:
        return None, "invalid_due"
    try:
        target_due = datetime.strptime(due_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None, "invalid_due"

    try:
        from fsrs import Card, Rating
        from fsrs_scheduler import make_scheduler

        sched = make_scheduler()
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def _fast_forward(origin: datetime):
            card = Card()
            cursor = origin
            for _ in range(stage):
                card, _log = sched.review_card(card, Rating.Good, cursor)
                cursor = card.due
            return card, cursor

        _card, natural_due = _fast_forward(base)
        # 时间轴平移：让重建链的"下次计划到期日"对齐到日志记录的 due_before。
        shift = target_due - natural_due
        card, _cursor = _fast_forward(base + shift)

        probe = target_due + timedelta(days=max(0, int(days_late or 0)))
        value = sched.get_card_retrievability(card, probe)
        if value is None:
            return None, "predict_failed"
        return max(1e-6, min(1 - 1e-6, float(value))), ""
    except Exception:
        return None, "predict_failed"


def evaluate_srs_benchmark(
    events: Optional[List[Dict[str, Any]]] = None,
    min_samples: int = MIN_SRS_SAMPLES,
) -> Dict[str, Any]:
    """计算 FSRS 预测的 RMSE / LogLoss 与保留率偏差。

    有效样本口径：``rating`` 合法、``stage_before >= 1``、``due_before`` 可解析。
    其余事件计入 ``excluded``（附原因分类），不参与指标计算。

    Returns:
        ``{"evaluable": bool, "reason": str, "samples": int, "excluded": {...}, ...}``
    """
    events = load_review_log() if events is None else list(events)

    pairs: List[Tuple[float, int]] = []
    excluded: Dict[str, int] = {}

    for ev in events:
        rating = str(ev.get("rating", "")).strip().lower()
        if rating not in ("again", "hard", "good", "easy"):
            excluded["invalid_rating"] = excluded.get("invalid_rating", 0) + 1
            continue  # 缺少评级的事件无法判定实际结果
        p, reason = _predict_recall_probability(
            ev.get("stage_before", 0), str(ev.get("due_before", "")), ev.get("days_late", 0)
        )
        if p is None:
            key = reason or "predict_failed"
            excluded[key] = excluded.get(key, 0) + 1
            continue
        y = 1 if rating in ("good", "easy") else 0
        pairs.append((p, y))

    result: Dict[str, Any] = {
        "evaluable": False,
        "samples": len(pairs),
        "total_events": len(events),
        "excluded": excluded,
        "reason": "",
    }
    if len(pairs) < min_samples:
        detail = "、".join(f"{k}×{v}" for k, v in sorted(excluded.items())) or "无"
        result["reason"] = (
            f"有效复测样本仅 {len(pairs)} 条（原始事件 {len(events)} 条，已排除：{detail}）。"
            "未复习卡（stage_before=0）不在 FSRS 可校准域，不计入有效样本；"
            "请先通过 `ky review` 对同一错题完成若干轮复测（达 stage≥1），"
            "每次回写都会追加一条事件到 "
            f"{REVIEW_LOG_FILE.relative_to(ROOT)}，"
            f"有效样本达到 {min_samples} 条后再运行本评测。"
        )
        return result

    n = len(pairs)
    se = sum((p - y) ** 2 for p, y in pairs) / n
    ll = 0.0
    for p, y in pairs:
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    ll /= n
    mean_p = sum(p for p, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n

    result.update({
        "evaluable": True,
        "rmse": round(math.sqrt(se), 4),
        "logloss": round(ll, 4),
        "mean_predicted_retention": round(mean_p, 4),
        "observed_retention": round(mean_y, 4),
        "calibration_gap": round(mean_p - mean_y, 4),
    })
    result["passed"] = result["rmse"] <= RMSE_TARGET and result["logloss"] <= LOGLOSS_TARGET
    return result


def _print_srs_result(res: Dict[str, Any]) -> None:
    print("\n=== FSRS 校准度评测 (srs-benchmark 口径) ===")
    excluded = res.get("excluded") or {}
    if excluded:
        detail = "、".join(f"{k}×{v}" for k, v in sorted(excluded.items()))
        print(f"  已排除样本         : {detail}")
    if not res["evaluable"]:
        print(f"  ⚠️ 不可评测：{res['reason']}")
        return
    print(f"  有效样本数         : {res['samples']}")
    print(f"  RMSE（越小越好）    : {res['rmse']}   (目标 ≤ {RMSE_TARGET})")
    print(f"  LogLoss（越小越好） : {res['logloss']} (目标 ≤ {LOGLOSS_TARGET})")
    print(f"  预测保留率          : {res['mean_predicted_retention']}")
    print(f"  实际保留率          : {res['observed_retention']}")
    print(f"  校准偏差(预测-实际)  : {res['calibration_gap']}")
    print(f"  结论                : {'✅ 达标' if res.get('passed') else '❌ 未达标'}")


# ════════════════════════════════════════════════════════════════
# 2) 引文忠实度评测（反幻觉闸门）
# ════════════════════════════════════════════════════════════════

#: C2 评测集与双阈值（规划口径：伪造引用 100% 拦截；正常引用 ≥98% 放行）
CITATION_BENCH_FILE = ROOT / "tests" / "benchmarks" / "citation_faithfulness.jsonl"
CITATION_NEG_THRESHOLD = 1.0    # 拦截侧：漏放一条编造引用即红线失守
CITATION_POS_THRESHOLD = 0.98   # 放行侧：允许 2% 误拦容错


def evaluate_ragas_faithfulness(cases_path: Optional[Path] = None) -> Dict[str, Any]:
    """逐条运行反幻觉闸门，**分别**统计“该拦的拦、该放的放”判定准确率。

    数据：``tests/benchmarks/citation_faithfulness.jsonl``（C2：108 条，覆盖
    无据引用 / 伪造 URL / 过期数据 / 跨校混淆 / 二手源冒充 五类）。被测对象
    与 kind 分发见 :mod:`benchmarks.citation_judge`。

    为什么分两个指标而不是一个总准确率：反幻觉闸门两个方向的容错要求不对称 ——
    漏放一条编造引用（false negative）是红线失守，误拦一条正常引用只是体验损失。
    混成一个数字会掩盖“拿误拦率换漏放率”这类劣化。

    与 --syllabus 同款语义：完全离线、确定性、无网络、无 LLM。
    """
    try:
        from benchmarks.citation_judge import build_judge
        from benchmarks.runner import EXIT_FAIL, EXIT_INSUFFICIENT, EXIT_OK
        from benchmarks.runner import exit_code, load_cases, run_benchmark
    except ImportError:  # 包内导入（tools.evaluate_pipeline）路径
        from tools.benchmarks.citation_judge import build_judge
        from tools.benchmarks.runner import EXIT_FAIL, EXIT_INSUFFICIENT, EXIT_OK
        from tools.benchmarks.runner import exit_code, load_cases, run_benchmark

    path = Path(cases_path) if cases_path else CITATION_BENCH_FILE
    if not path.exists():
        return {"evaluable": False, "samples": 0, "correct": 0, "faithfulness": 0.0,
                "passed": False, "negative": {}, "positive": {},
                "reason": f"评测集不存在: {path}"}

    cases, invalid = load_cases(path)
    judge = build_judge()
    block_cases = [c for c in cases if c.get("expect_block") is True]
    allow_cases = [c for c in cases if c.get("expect_block") is not True]

    block_report = run_benchmark(block_cases, judge, name="引文忠实度·拦截侧", invalid=invalid)
    allow_report = run_benchmark(allow_cases, judge, name="引文忠实度·放行侧", invalid=invalid)

    block_code = exit_code(block_report, threshold=CITATION_NEG_THRESHOLD)
    allow_code = exit_code(allow_report, threshold=CITATION_POS_THRESHOLD)
    if EXIT_INSUFFICIENT in (block_code, allow_code):
        overall = EXIT_INSUFFICIENT
    elif EXIT_FAIL in (block_code, allow_code):
        overall = EXIT_FAIL
    else:
        overall = EXIT_OK

    total = block_report.total + allow_report.total
    correct = block_report.passed + allow_report.passed
    if invalid:
        reason = f"存在 {len(invalid)} 条无效样本（数据损坏）"
    elif not total:
        reason = "评测集为空"
    else:
        reason = ""
    return {
        "evaluable": total > 0 and not invalid,
        "samples": total,
        "correct": correct,
        "faithfulness": round(correct / total, 4) if total else 0.0,
        "passed": overall == EXIT_OK,
        "exit_code": overall,
        "negative": block_report.to_dict(),
        "positive": allow_report.to_dict(),
        "invalid": [{"id": cid, "reason": r} for cid, r in invalid],
        "reason": reason,
    }


def _print_ragas_result(res: Dict[str, Any]) -> None:
    print("\n=== 引文忠实度评测 (反幻觉闸门 · C2) ===")
    if not res["evaluable"]:
        print(f"  ⚠️ 不可评测：{res.get('reason') or '样本不足'}")
        return
    neg = res["negative"]
    pos = res["positive"]
    print(f"  拦截侧(该拦的): {neg['passed']}/{neg['total']} 通过  "
          f"漏放 {neg['failed']} 条  （要求 100%）")
    print(f"  放行侧(该放的): {pos['passed']}/{pos['total']} 通过  "
          f"误拦 {pos['failed']} 条  （要求 ≥98%）")
    for label, rep in (("拦截", neg), ("放行", pos)):
        for item in rep["failures"][:10]:
            print(f"    ❌ [{label}] {item['id']}: {item['detail'][:100]}")
    print(f"  总体判定准确率 : {res['faithfulness']:.2%} ({res['correct']}/{res['samples']})")
    print(f"  结论           : {'✅ 达标' if res['passed'] else '❌ 未达标'}")


# ════════════════════════════════════════════════════════════════
# 3) 考纲守卫评测（C1：可证阶段 · 考纲红线拦截的度量）
# ════════════════════════════════════════════════════════════════

#: C1 评测集与达标阈值（规划口径：pass ≥98%）
SYLLABUS_BENCH_FILE = ROOT / "tests" / "benchmarks" / "syllabus_guard.jsonl"
SYLLABUS_PASS_THRESHOLD = 0.98


def evaluate_syllabus_guard(cases_path: Optional[Path] = None) -> Dict[str, Any]:
    """逐条运行考纲守卫 Hook，统计"该拦的拦、该放的放"判定准确率。

    数据：``tests/benchmarks/syllabus_guard.jsonl``（math1/2/3/396 × 正/负样本，
    否定/笔记语境 ≥30%）。评测对象是 ``agent.hooks.syllabus_guard_hook`` ——
    它此前只有 23 个 pytest 用例（D2 短板），本评测把它升级为**可度量资产**。

    与 --ragas 同款语义：完全离线、确定性、无网络、无 LLM。
    """
    try:
        from benchmarks.runner import load_cases, run_benchmark
    except ImportError:  # 包内导入（tools.evaluate_pipeline）路径
        from tools.benchmarks.runner import load_cases, run_benchmark
    from agent.hooks import HookManager

    path = Path(cases_path) if cases_path else SYLLABUS_BENCH_FILE
    if not path.exists():
        return {"evaluable": False, "samples": 0, "correct": 0,
                "accuracy": 0.0, "passed": False, "details": [],
                "reason": f"评测集不存在: {path}"}

    cases, invalid = load_cases(path)
    hm = HookManager(workspace_root=ROOT)

    def judge(case: Dict[str, Any]) -> Tuple[bool, str]:
        subject = str(case.get("subject", "math"))
        ctx = {
            "active_subject": subject,
            "math_key": case.get("math_key") if subject == "math" else None,
            "user_input": case.get("text", ""),
        }
        allow, _reason, _ = hm.trigger_pre_tool_use(
            "generate_exam", {"query": case.get("text", "")}, ctx)
        expect = bool(case.get("expect_allow"))
        ok = allow == expect
        return ok, ("" if ok else f"allow={allow} 期望 {expect}")

    report = run_benchmark(cases, judge, name="syllabus_guard", invalid=invalid)
    return {
        "evaluable": report.total > 0 and not report.invalid,
        "samples": report.total,
        "correct": report.passed,
        "accuracy": round(report.pass_rate, 4),
        "passed": (not report.invalid) and report.pass_rate >= SYLLABUS_PASS_THRESHOLD,
        "invalid": list(report.invalid),
        "failures": list(report.failures),
        "reason": (f"无效样本 {len(report.invalid)} 条" if report.invalid else ""),
    }


def _print_syllabus_result(res: Dict[str, Any]) -> None:
    print("\n=== 考纲守卫评测 (C1 · 考纲红线拦截) ===")
    if not res["evaluable"]:
        print(f"  ⚠️ 不可评测：{res.get('reason') or '用例集为空'}")
        return
    for cid, detail in res["failures"][:20]:
        print(f"  ❌ {cid}: {detail}")
    if len(res["failures"]) > 20:
        print(f"  ... 其余 {len(res['failures']) - 20} 条省略")
    print(f"  判定准确率: {res['accuracy']:.2%} ({res['correct']}/{res['samples']})"
          f"（阈值 {SYLLABUS_PASS_THRESHOLD:.0%}）")
    print(f"  结论      : {'✅ 达标' if res['passed'] else '❌ 未达标'}")


# ════════════════════════════════════════════════════════════════
# 4) 开放题判分评测（C4 pilot：与预标一致率；**不进 CI 门禁**）
# ════════════════════════════════════════════════════════════════

def evaluate_grading_pilot(cases_path: Optional[Path] = None,
                           snapshots_path: Optional[Path] = None) -> Dict[str, Any]:
    """回放 LLM 响应快照，评测开放题判分链路与 AI 预标的一致率。

    数据：``tests/benchmarks/grading_pilot.jsonl``（30 份 AI 预标：专业课 12 /
    政治 10 / 英语 8）+ ``tests/benchmarks/grading_snapshots.jsonl``（真实 LLM
    响应快照，由 ``--grading-live`` 一次性采集后冻结）。三指标：采分点命中
    一致率 / 总分 MAE / 错因一致率。

    **命名诚实化**：AI 预标（用户抽检）下测的是「与预标一致率」，不是人工金标准
    准确率；正式 MAE / Kappa 目标待人工标注量上来后再定。

    **不进 CI**：本评测不接入 ``ci_evaluate_gate.py`` / ``test.yml``（元测试钉住）
    —— pilot 阶段尚无正式达标阈值，快照回放只用于本地回归与基线观测。
    完全离线、确定性、零成本（回放不联网）。
    """
    try:
        from benchmarks.grading_judge import evaluate_grading_pilot as _eval
    except ImportError:  # 包内导入（tools.evaluate_pipeline）路径
        from tools.benchmarks.grading_judge import evaluate_grading_pilot as _eval
    return _eval(cases_path, snapshots_path)


def _print_grading_result(res: Dict[str, Any]) -> None:
    print("\n=== 开放题判分评测 (C4 pilot · 与预标一致率) ===")
    if res.get("disclaimer"):
        print(f"  口径：{res['disclaimer']}")
    if not res["evaluable"]:
        print(f"  ⚠️ 不可评测：{res.get('reason') or '样本不足'}")
        for item in res.get("invalid", [])[:5]:
            print(f"      - {item['id']}: {item['reason'][:100]}")
        return
    m = res["metrics"]
    print(f"  采分点命中一致率: Jaccard {m['hit_jaccard']:.2%}"
          f"（recall {m['hit_recall']:.2%} / precision {m['hit_precision']:.2%}）")
    print(f"  总分 MAE        : {m['total_mae']:.2f} 分（10 分制）")
    print(f"  错因一致率      : {m['mistake_agreement']:.2%}")
    print(f"  诊断            : 经主审仲裁 {m['arbitrated_rate']:.1%} ｜ "
          f"降级/转人工 {m['degraded_rate']:.1%} ｜ 有效样本 {m['samples']}")
    for key, label in (("by_quality", "作答档位"), ("by_subject", "科目")):
        rows = res.get(key) or {}
        if rows:
            print(f"  分层（{label}）:")
            for g, gm in rows.items():
                print(f"    {g:<8} n={gm['samples']:<3} "
                      f"J={gm['hit_jaccard']:.2f} MAE={gm['total_mae']:.2f} "
                      f"错因={gm['mistake_agreement']:.0%}")
    failures = (res.get("report") or {}).get("failures") or []
    if failures:
        print(f"  [!] 与预标偏离较大的样本（前 10 条）：")
        for item in failures[:10]:
            print(f"      - {item['id']}: {item['detail'][:120]}")
    if res.get("invalid"):
        print(f"  [!] 无效样本 {len(res['invalid'])} 条（数据损坏 / 快照缺失）")
    print(f"  结论            : "
          f"{'✅ 达到探索性预警线' if res['passed'] else '❌ 低于探索性预警线'}"
          f"（{res['alert_threshold']:.0%}，**探索性、非正式达标线**）")


def _run_grading_capture() -> int:
    """``--grading-live``：真实调用 LLM 采集判分快照（**一次性计费**）。

    增量采集（已有快照的题目跳过，可中断续跑），采集完成后由调用方立即回放。
    """
    try:
        from benchmarks.grading_judge import CASES_FILE, SNAPSHOTS_FILE, capture_snapshots
        from benchmarks.runner import load_cases
    except ImportError:  # 包内导入路径
        from tools.benchmarks.grading_judge import (  # type: ignore
            CASES_FILE, SNAPSHOTS_FILE, capture_snapshots)
        from tools.benchmarks.runner import load_cases  # type: ignore

    if not CASES_FILE.exists():
        print(f"  ❌ 评测集不存在: {CASES_FILE}")
        return 1
    try:
        data = json.loads((ROOT / "ky_config.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ❌ 无法读取 ky_config.json: {e}")
        return 1
    base_url = str(data.get("base_url") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    model = str(data.get("model") or "").strip()
    if not (base_url and api_key and model):
        print("  ❌ ky_config.json 未配置 base_url / api_key / model，无法采集")
        return 1

    cases, _invalid = load_cases(CASES_FILE)
    print(f"  数据源: {CASES_FILE}（{len(cases)} 份）")
    print(f"  模型  : {model} @ {base_url}")
    print("  说明  : 增量采集（已有快照的题目跳过）；每题约 4 次调用，**会产生计费**")
    stats = capture_snapshots(cases, SNAPSHOTS_FILE, base_url=base_url,
                              api_key=api_key, model=model,
                              progress=lambda msg: print("  " + msg))
    print(f"  采集完成: 新增 {stats['captured']} / 跳过 {stats['skipped']} / "
          f"契约失败 {len(stats['invalid_cases'])} / 全失败 {len(stats['failed_cases'])}"
          f"（快照总计 {stats['total_snapshots']} 份）")
    if stats["failed_cases"]:
        print(f"  [!] 全调用失败的题（可删掉其快照行后重采）：{stats['failed_cases']}")
    return 0


# ════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="考研学习链 · 离线评测流水线（真实指标，无数据时明确不可评测）"
    )
    parser.add_argument("--srs", action="store_true", help="运行 FSRS 校准度评测 (RMSE/LogLoss)")
    parser.add_argument("--ragas", action="store_true", help="运行引文忠实度评测（反幻觉闸门）")
    parser.add_argument("--syllabus", action="store_true",
                        help="运行考纲守卫评测（C1：math1/2/3/396 红线拦截准确率）")
    parser.add_argument("--grading", action="store_true",
                        help="运行开放题判分评测（C4 pilot：快照回放，零网络零成本；不进 CI）")
    parser.add_argument("--grading-live", action="store_true",
                        help="[计费] 真实调用 LLM 采集判分快照（一次性），随后立即回放评测")
    parser.add_argument("--log", type=str, default="", help="[--srs] 指定复测事件日志路径")
    parser.add_argument("--min-samples", type=int, default=MIN_SRS_SAMPLES,
                        help=f"[--srs] 可信评测所需最小样本量 (默认 {MIN_SRS_SAMPLES})")
    args = parser.parse_args()

    if not (args.srs or args.ragas or args.syllabus or args.grading or args.grading_live):
        args.srs = True
        args.ragas = True
        args.syllabus = True

    not_evaluable = False
    failed = False

    if args.grading_live:
        if _run_grading_capture() != 0:
            failed = True
        args.grading = True   # 采集后立即回放

    if args.srs:
        res = evaluate_srs_benchmark(
            load_review_log(Path(args.log)) if args.log else None,
            min_samples=args.min_samples,
        )
        _print_srs_result(res)
        if not res["evaluable"]:
            not_evaluable = True
        elif not res.get("passed"):
            failed = True

    if args.ragas:
        res = evaluate_ragas_faithfulness()
        _print_ragas_result(res)
        if not res["evaluable"]:
            not_evaluable = True
        elif not res.get("passed"):
            failed = True

    if args.syllabus:
        res = evaluate_syllabus_guard()
        _print_syllabus_result(res)
        if not res["evaluable"]:
            not_evaluable = True
        elif not res.get("passed"):
            failed = True

    if args.grading:
        res = evaluate_grading_pilot()
        _print_grading_result(res)
        if not res["evaluable"]:
            not_evaluable = True
        elif not res.get("passed"):
            failed = True

    print()
    if failed:
        print("评测完成：存在未达标项（退出码 1）")
        return 1
    if not_evaluable:
        print("部分评测因样本不足无法给出结论（退出码 2）—— 本流水线不会编造指标。")
        return 2
    print("全部评测达标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
