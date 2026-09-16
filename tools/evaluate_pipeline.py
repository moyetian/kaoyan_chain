# -*- coding: utf-8 -*-
"""
考研学习链 · 离线评测流水线 (Offline Evaluation Pipeline)

提供两项**真实**评测，用于回答"记忆调度是否校准"与"引用是否可溯源"：

  1. ``--srs``   FSRS 校准度评测（RMSE / LogLoss / 预测-实际保留率偏差）
     数据来源：``.memory/review_log.jsonl``（由 error_logger 在每次复测回写时追加）
     预测模型：以该题**复测前**的档位重建卡片，取**实际复测时刻**的可回想概率 p；
              实际结果 y = 1（good/easy 想起）或 0（hard/again 未想起）。

  2. ``--ragas`` 引文忠实度评测（反幻觉闸门的功能评测）
     数据来源：内置**带标注**用例集（正例应通过、反例应被拒绝），
              逐条跑 ``citation_engine.verify_citations`` 并统计判定准确率。

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


def _predict_recall_probability(stage_before: int, due_before: str, days_late: int) -> Optional[float]:
    """预测"在本次复测时刻能想起"的概率。

    做法：用 ``stage_before`` 次 good 复习重建记忆稳定性（与
    ``fsrs_scheduler.compute_next_interval`` 同一套快进逻辑），
    再取该卡片在**实际复测时刻**的可回想概率。
    实际复测时刻 = 计划到期日 + 迟延天数（按时复测即等于计划到期日）。
    """
    try:
        from fsrs import Card, Rating  # noqa: F401
        from fsrs_scheduler import make_scheduler

        sched = make_scheduler()
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        card = Card()
        cursor = base
        for _ in range(max(0, int(stage_before or 0))):
            card, _log = sched.review_card(card, Rating.Good, cursor)
            cursor = card.due
        probe = cursor + timedelta(days=max(0, int(days_late or 0)))
        value = sched.get_card_retrievability(card, probe)
        if value is None:
            return None
        return max(1e-6, min(1 - 1e-6, float(value)))
    except Exception:
        return None


def evaluate_srs_benchmark(
    events: Optional[List[Dict[str, Any]]] = None,
    min_samples: int = MIN_SRS_SAMPLES,
) -> Dict[str, Any]:
    """计算 FSRS 预测的 RMSE / LogLoss 与保留率偏差。

    Returns:
        ``{"evaluable": bool, "reason": str, "samples": int, "rmse": float, ...}``
    """
    events = load_review_log() if events is None else list(events)

    pairs: List[Tuple[float, int]] = []
    for ev in events:
        rating = str(ev.get("rating", "")).strip().lower()
        if rating not in ("again", "hard", "good", "easy"):
            continue  # 缺少评级的事件无法判定实际结果
        p = _predict_recall_probability(
            ev.get("stage_before", 0), str(ev.get("due_before", "")), ev.get("days_late", 0)
        )
        if p is None:
            continue
        y = 1 if rating in ("good", "easy") else 0
        pairs.append((p, y))

    result: Dict[str, Any] = {"evaluable": False, "samples": len(pairs), "reason": ""}
    if len(pairs) < min_samples:
        result["reason"] = (
            f"有效复测样本仅 {len(pairs)} 条，少于可信评测所需的最小样本量 {min_samples} 条。"
            "请先通过 `ky review` 完成若干轮错题复测（每次回写都会追加一条事件到 "
            f"{REVIEW_LOG_FILE.relative_to(ROOT)}），积累后再运行本评测。"
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

#: 内置带标注用例：expected=True 表示**应当**溯源通过
FAITHFULNESS_CASES: List[Dict[str, Any]] = [
    {
        "name": "逐字命中",
        "documents": ["示例院校A2027年硕士研究生招生简章：拟招生 60 人。"],
        "answer": {"answer": "拟招 60 人", "confidence": "high",
                   "citations": [{"cited_text": "拟招生 60 人", "document_index": 0}]},
        "expected": True,
    },
    {
        "name": "空白差异仍应命中",
        "documents": ["初试科目：\n  (302)数学(二)\n  (814)通信原理"],
        "answer": {"answer": "考数二与通信原理", "confidence": "high",
                   "citations": [{"cited_text": "(302)数学(二) (814)通信原理", "document_index": 0}]},
        "expected": True,
    },
    {
        "name": "空引文必须拒绝",
        "documents": ["研招网页面正文"],
        "answer": {"answer": "无据断言", "confidence": "high",
                   "citations": [{"cited_text": "", "document_index": 0}]},
        "expected": False,
    },
    {
        "name": "编造引文必须拒绝",
        "documents": ["示例院校A2027年硕士研究生招生简章：拟招生 60 人。"],
        "answer": {"answer": "拟招 9999 人", "confidence": "high",
                   "citations": [{"cited_text": "拟招生 9999 人", "document_index": 0}]},
        "expected": False,
    },
    {
        "name": "文档下标越界必须拒绝",
        "documents": ["唯一文档"],
        "answer": {"answer": "x", "confidence": "high",
                   "citations": [{"cited_text": "唯一文档", "document_index": 7}]},
        "expected": False,
    },
    {
        "name": "高置信度无引文必须拒绝",
        "documents": ["研招网页面正文"],
        "answer": {"answer": "无据断言", "confidence": "high", "citations": []},
        "expected": False,
    },
    {
        "name": "低置信度无引文允许",
        "documents": ["研招网页面正文"],
        "answer": {"answer": "推测", "confidence": "low", "citations": []},
        "expected": True,
    },
    {
        "name": "非法置信度必须拒绝",
        "documents": ["文档"],
        "answer": {"answer": "x", "confidence": "very-high", "citations": []},
        "expected": False,
    },
]


def evaluate_ragas_faithfulness(cases: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """逐条运行引文闸门，统计判定准确率（忠实度）。

    这是对**反幻觉闸门本身**的功能评测：正例必须放行、反例必须拦下。
    """
    from intelligence.citation_engine import UngroundedCitation, verify_citations

    cases = FAITHFULNESS_CASES if cases is None else list(cases)
    details: List[Dict[str, Any]] = []
    correct = 0
    for case in cases:
        try:
            verify_citations(case["answer"], case["documents"])
            accepted = True
            why = "溯源通过"
        except UngroundedCitation as e:
            accepted = False
            why = str(e)
        ok = (accepted == bool(case["expected"]))
        correct += 1 if ok else 0
        details.append({
            "name": case["name"],
            "expected": bool(case["expected"]),
            "accepted": accepted,
            "correct": ok,
            "detail": why[:120],
        })

    total = len(cases)
    return {
        "evaluable": total > 0,
        "samples": total,
        "correct": correct,
        "faithfulness": round(correct / total, 4) if total else 0.0,
        "passed": correct == total,
        "details": details,
    }


def _print_ragas_result(res: Dict[str, Any]) -> None:
    print("\n=== 引文忠实度评测 (反幻觉闸门) ===")
    if not res["evaluable"]:
        print("  ⚠️ 不可评测：用例集为空")
        return
    for d in res["details"]:
        mark = "✅" if d["correct"] else "❌"
        print(f"  {mark} {d['name']:22} 期望={'通过' if d['expected'] else '拒绝'} "
              f"实际={'通过' if d['accepted'] else '拒绝'}  {d['detail'][:60]}")
    print(f"  判定准确率(忠实度): {res['faithfulness']} ({res['correct']}/{res['samples']})")
    print(f"  结论              : {'✅ 达标' if res['passed'] else '❌ 未达标'}")


# ════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="考研学习链 · 离线评测流水线（真实指标，无数据时明确不可评测）"
    )
    parser.add_argument("--srs", action="store_true", help="运行 FSRS 校准度评测 (RMSE/LogLoss)")
    parser.add_argument("--ragas", action="store_true", help="运行引文忠实度评测（反幻觉闸门）")
    parser.add_argument("--log", type=str, default="", help="[--srs] 指定复测事件日志路径")
    parser.add_argument("--min-samples", type=int, default=MIN_SRS_SAMPLES,
                        help=f"[--srs] 可信评测所需最小样本量 (默认 {MIN_SRS_SAMPLES})")
    args = parser.parse_args()

    if not (args.srs or args.ragas):
        args.srs = True
        args.ragas = True

    not_evaluable = False
    failed = False

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
