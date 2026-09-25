# -*- coding: utf-8 -*-
"""C4 开放题判分链路评测（pilot）—— 快照回放，零网络、零成本、可复现。

被测对象：``open_grader.grade_open_question``（多模型评审 + 确定性汇总）。
数据来源：
  * ``tests/benchmarks/grading_pilot.jsonl`` —— 30 份 AI 预标样本
    （专业课 12 / 政治 10 / 英语 8；题目为私教自拟变式，全部中性化）；
  * ``tests/benchmarks/grading_snapshots.jsonl`` —— 真实 LLM 响应快照，
    由 ``--grading-live`` 一次性采集后冻结；回放时零网络、零计费。

三指标（**诚实命名**：AI 预标下测的是「与预标一致率」，不是判分准确率）：
  1. 采分点命中一致率 —— 链路判定的命中集 vs 预标命中集（Jaccard / recall / precision）；
  2. 总分 MAE        —— |链路总分 − 预标总分| 的均值；
  3. 错因一致率       —— 链路 mistake_type == 预标 mistake_type 的比例。

  预标由 AI 生成、用户抽检：在没有人工金标准之前，三指标衡量的是「两个独立
  判分过程的吻合度」，不等于「判分正确率」。正式 MAE / Kappa 目标待人工标注
  量上来后再定（规划 C4 条款）。

口径说明（诚实边界，避免过度解读）：
  * 评测**注入预标采分点**（``key_points=``），因此度量的是「在给定采分点下，
    逐点命中判定与总分聚合与预标的一致性」；采分点自身的生成质量（LLM 从
    标准答案推导 rubric）不在本 pilot 的度量范围内 —— 注入是必要的，否则
    模型自生成 rubric 的 id 无法与预标对齐，「采分点覆盖率」将无从计算。
  * 回放 client 对**快照未命中**的调用返回 None 并记录 miss：评测层把该 case
    判为「快照不完整」（计入 invalid → 退出码 2），绝不把「数据缺失」伪装成
    「模型弃权」。

不进 CI：本评测**不接入** ``ci_evaluate_gate.py`` / ``test.yml``（元测试钉住），
因为 pilot 阶段尚无正式达标阈值，快照回放只用于本地回归与基线观测。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "CALL_KINDS", "SUBJECTS", "QUALITIES", "CASE_REQUIRED_KEYS",
    "MIN_PILOT_SAMPLES", "PILOT_ALERT_THRESHOLD", "HIT_JACCARD_FLOOR",
    "TOTAL_TOLERANCE",
    "classify_call", "call_key", "build_replay_client", "build_capture_client",
    "build_pilot_config", "validate_case", "aggregate_hit_set", "case_metrics",
    "load_snapshots", "build_judge", "evaluate_grading_pilot", "capture_snapshots",
]

try:  # 脚本式路径：``tools/`` 已在 sys.path（evaluate_pipeline 的直接调用）
    from skills import open_grader
except ImportError:  # 包内导入：``tools.benchmarks.grading_judge``
    from tools.skills import open_grader  # type: ignore

try:
    from benchmarks.runner import (
        EXIT_INSUFFICIENT, EXIT_OK,
        exit_code, load_cases, run_benchmark,
    )
except ImportError:  # 包内导入路径
    from tools.benchmarks.runner import (  # type: ignore
        EXIT_INSUFFICIENT, EXIT_OK,
        exit_code, load_cases, run_benchmark,
    )

#: 数据集与快照文件（与 tests/benchmarks/ 下其他评测集同目录）
CASES_FILE = Path(__file__).resolve().parent.parent.parent / "tests" / "benchmarks" / "grading_pilot.jsonl"
SNAPSHOTS_FILE = Path(__file__).resolve().parent.parent.parent / "tests" / "benchmarks" / "grading_snapshots.jsonl"

#: 三类 LLM 调用（与 open_grader 的 rubric / review / judge 提示词一一对应）
CALL_KINDS = ("rubric", "review", "judge")

#: 数据集科目与质量档（分布契约由元测试校验）
SUBJECTS = ("pro", "pol", "eng")
QUALITIES = ("good", "medium", "poor", "blank")

#: 用例必需键（缺失 → runner 记 invalid → 退出码 2）
CASE_REQUIRED_KEYS = ("id", "subject", "question", "reference_answer",
                      "key_points", "student_answer", "expected", "quality")

#: 规划口径：pilot 20~30 份；低于下限视为样本不足（退出码 2）
MIN_PILOT_SAMPLES = 20

#: 单条一致判据的探索性口径（**非正式达标线**）：
#:   * Jaccard ≥ 0.5 —— 命中集「过半一致」；
#:   * |Δ总分| ≤ 2.0 —— 与链路自身的 rubric_tolerance 同值：链路自认自报分与
#:     采分点重算偏差超 2 分即转人工，评测沿用同一容忍度。
HIT_JACCARD_FLOOR = 0.5
TOTAL_TOLERANCE = 2.0

#: pilot 探索性预警线：单条一致率低于此值提示链路可能与预标系统性偏离。
#: 正式 MAE / Kappa 目标待人工标注量上来后再定（规划 C4 条款）。
PILOT_ALERT_THRESHOLD = 0.6

#: 命中票值口径 —— 与 ``open_grader._recompute_total`` 的 fractions 同源
#: （full=1.0 / partial=half=0.5 / none=0.0），保证「命中判定」与链路自身的
#: 总分重算口径一致。
_HIT_FRACTIONS = {"full": 1.0, "partial": 0.5, "half": 0.5, "none": 0.0}

#: 预标总分自洽容差（允许 1 位小数舍入）
_EXPECTED_TOTAL_TOL = 0.06


# ════════════════════════════════════════════════════════════════
# 调用分类与键控（快照读写的唯一事实源）
# ════════════════════════════════════════════════════════════════

def classify_call(messages: Sequence[Dict[str, Any]]) -> str:
    """从 messages 判定 LLM 调用类别（rubric / review / judge）。

    判据取三类提示词的**稳定特征串**（open_grader._rubric_messages /
    _judge_messages / _review_messages）：顺序为 rubric → judge → 兜底 review。
    特征串失效（提示词被改写）会让快照读不到 → 评测层标 invalid，fail-closed
    而不是静默错配。
    """
    content = ""
    for m in reversed(list(messages or [])):
        if isinstance(m, dict) and m.get("role") == "user":
            content = str(m.get("content", "") or "")
            break
    if "生成评分要点" in content:
        return "rubric"
    if "多位阅卷人" in content:
        return "judge"
    return "review"


def call_key(messages: Sequence[Dict[str, Any]],
             endpoint: Optional[Dict[str, Any]]) -> str:
    """快照键：rubric / judge 唯一，review 按端点名区分（A/B/C 三视角）。"""
    kind = classify_call(messages)
    if kind == "review":
        return f"review:{(endpoint or {}).get('name', '?')}"
    return kind


# ════════════════════════════════════════════════════════════════
# 回放 / 采集 client
# ════════════════════════════════════════════════════════════════

def build_replay_client(calls: Dict[str, Any],
                        miss_sink: Optional[List[str]] = None) -> Callable:
    """按快照回放响应，构造 ``llm_client``（签名 ``(messages, endpoint) -> str|None``）。

    三态语义（绝不混淆）：
      * 键命中且 ``ok``      → 返回冻结的响应文本；
      * 键命中但 ``ok=False`` → 返回 None（**忠实复现**采集时的模型失败/空响应）；
      * 键未命中              → 记入 ``miss_sink`` 并返回 None（评测层据此把该
        case 判为「快照不完整」，而不是把数据缺失伪装成模型弃权）。
    """

    def _client(messages, endpoint):  # noqa: ANN001 - 与 open_grader 注入契约一致
        key = call_key(messages, endpoint)
        rec = calls.get(key)
        if not isinstance(rec, dict):
            if miss_sink is not None:
                miss_sink.append(key)
            return None
        if not rec.get("ok"):
            return None
        return rec.get("response")

    return _client


def build_capture_client(calls_sink: Dict[str, Any], *, timeout: float = 45.0) -> Callable:
    """真实调用 LLM 并把响应冻结进 ``calls_sink[key]``（同键覆盖为最后一次）。

    **会产生计费调用**，仅供 ``--grading-live`` 显式采集使用。调用异常时记录
    失败后**原样上抛**，让链路按真实失败处理（重试 / 弃权）——采集端不得替
    链路做决策，否则快照就不再忠实。
    """

    def _client(messages, endpoint):  # noqa: ANN001
        key = call_key(messages, endpoint)
        try:
            resp = open_grader.OpenAICompatClient(endpoint, timeout).chat(messages)
        except Exception as e:  # noqa: BLE001 - 记录后上抛，交链路决策
            calls_sink[key] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
            raise
        ok = bool(resp and str(resp).strip())
        calls_sink[key] = ({"ok": True, "response": resp} if ok
                           else {"ok": False, "error": "模型返回空响应"})
        return resp

    return _client


# ════════════════════════════════════════════════════════════════
# 评测配置
# ════════════════════════════════════════════════════════════════

def build_pilot_config(base_url: str, api_key: str, model: str,
                       **over: Any) -> Dict[str, Any]:
    """构造评测/采集共用的判分配置。

    忠实反映本机现状：单模型 → 同源三视角（严格/步骤/结论）+ ``same_source``
    标记 → 强制主审仲裁（与 ``open_grader.build_ensemble`` 的回退编队一致）。
    回放时 base_url/api_key 传占位值即可（client 已注入，不会真发请求）。
    """
    perspectives = [
        ("A", "严格派：只认明确的公式、定理条件与完整推导，含糊表述不给分", 0.1),
        ("B", "步骤派：重点关注推导链条是否完整、逻辑是否自洽", 0.3),
        ("C", "结论派：先看最终结论是否正确，再回看关键步骤", 0.5),
    ]
    reviewers = [
        {"name": n, "role": "reviewer", "base_url": base_url, "api_key": api_key,
         "model": model, "weight": 1.0, "temperature": t, "perspective": p,
         "same_source": True}
        for n, p, t in perspectives
    ]
    cfg: Dict[str, Any] = {
        "enabled": True,
        "cache_rubric": False,      # 评测注入 key_points，rubric 不触发；显式关闭防跨 case 污染
        "max_retries": 0,           # 回放：快照已是最终结果，重试无意义
        "min_valid_reviews": 2,
        "divergence_threshold": 2.0,
        "pass_threshold": 6.0,
        "gray_zone": 2.0,
        "min_confidence": 0.6,
        "per_call_timeout": 45.0,
        "total_budget": 90.0,
        "reviewers": reviewers,
        "judge": {"name": "judge", "role": "judge", "base_url": base_url,
                  "api_key": api_key, "model": model, "weight": 2.0,
                  "temperature": 0.0, "enabled": True},
    }
    cfg.update(over)
    return cfg


# ════════════════════════════════════════════════════════════════
# 数据契约校验
# ════════════════════════════════════════════════════════════════

def validate_case(case: Dict[str, Any]) -> List[str]:
    """校验单份用例的内部自洽性；返回问题列表（空 = 合法）。

    覆盖：科目/质量档枚举、采分点条数与分值合计（=10）、expected 的命中
    id 合法性、**预标总分与命中完整度自洽**（total == Σ full + 0.5×Σ partial）、
    错因枚举、blank 档作答必须为空。
    """
    problems: List[str] = []
    if case.get("subject") not in SUBJECTS:
        problems.append(f"subject 非法: {case.get('subject')!r}（应为 {list(SUBJECTS)}）")
    if case.get("quality") not in QUALITIES:
        problems.append(f"quality 非法: {case.get('quality')!r}（应为 {list(QUALITIES)}）")
    if not str(case.get("question") or "").strip():
        problems.append("question 为空")

    kps = case.get("key_points")
    kp_scores: Dict[Any, float] = {}
    if not isinstance(kps, list):
        problems.append(f"key_points 应为列表（实际 {type(kps).__name__}）")
    else:
        if not (3 <= len(kps) <= 6):
            problems.append(f"key_points 应为 3~6 条（实际 {len(kps)}）")
        for i, kp in enumerate(kps, 1):
            if not isinstance(kp, dict):
                problems.append(f"key_points[{i}] 不是对象")
                continue
            if not str(kp.get("point") or "").strip():
                problems.append(f"key_points[{i}] 缺 point 描述")
            rid = kp.get("id", i)
            if rid in kp_scores:
                problems.append(f"key_points id 重复: {rid!r}")
            kp_scores[rid] = float(kp.get("score", 0.0) or 0.0)
        total_score = sum(kp_scores.values())
        if abs(total_score - 10.0) > 0.01:
            problems.append(f"key_points 分值合计 {total_score} ≠ 10")

    exp = case.get("expected")
    if not isinstance(exp, dict):
        problems.append("expected 不是对象")
    else:
        hits = exp.get("hit_points")
        exp_total = 0.0
        if not isinstance(hits, list):
            problems.append("expected.hit_points 应为列表")
        else:
            for h in hits:
                if not isinstance(h, dict):
                    problems.append("expected.hit_points 元素不是对象")
                    continue
                rid, level = h.get("id"), str(h.get("level", "") or "")
                if kp_scores and rid not in kp_scores:
                    problems.append(f"expected.hit_points id {rid!r} 不在 key_points 中")
                if level not in ("full", "partial"):
                    problems.append(f"expected.hit_points level 非法: {level!r}（应为 full/partial）")
                if rid in kp_scores:
                    exp_total += kp_scores[rid] * (1.0 if level == "full" else 0.5)
        try:
            declared = float(exp.get("total", 0.0) or 0.0)
        except (TypeError, ValueError):
            declared = -1.0
        if abs(declared - round(exp_total, 1)) > _EXPECTED_TOTAL_TOL:
            problems.append(
                f"expected.total 与命中完整度不自洽: 声明 {declared} ≠ 推算 {round(exp_total, 1)}")
        if str(exp.get("mistake_type", "") or "") not in open_grader.MISTAKE_TYPES:
            problems.append(f"expected.mistake_type 非法: {exp.get('mistake_type')!r}")

    if case.get("quality") == "blank" and str(case.get("student_answer") or "").strip():
        problems.append("blank 档的 student_answer 应为空（测链路的空作答边界）")
    return problems


# ════════════════════════════════════════════════════════════════
# 三指标
# ════════════════════════════════════════════════════════════════

def _entry_weight(entry: Any) -> float:
    try:
        return max(0.0, float(getattr(entry, "weight", 1.0) or 1.0))
    except (TypeError, ValueError):
        return 1.0


def _hit_maps(entry: Any) -> Dict[Any, Dict[str, Any]]:
    """评审的 rubric_hits → ``{id: hit}``（id 缺失的条目与链路口径一致地跳过）。"""
    out: Dict[Any, Dict[str, Any]] = {}
    for h in (getattr(entry, "rubric_hits", None) or []):
        if isinstance(h, dict) and h.get("id") is not None:
            out[h.get("id")] = h
    return out


def aggregate_hit_set(rubric: Optional[List[Dict[str, Any]]],
                      entries: Sequence[Any]) -> set:
    """链路判定的命中集：逐点按「命中票值 × 评审权重」加权聚合，≥0.5 记为命中。

    票值口径与 ``open_grader._recompute_total`` 同源（full=1.0 / partial=half=0.5
    / none=0.0），权重与总分聚合一致（评审 1.0 / 主审 2.0）；阈值 0.5 的含义是
    「加权过半认为答到了该点」——三位评审全 partial（0.5）或一 full 一 partial
    一 none（0.5）都算命中。
    """
    entries = [e for e in entries if e is not None]
    total_w = sum(_entry_weight(e) for e in entries)
    if total_w <= 0 or not rubric:
        return set()
    maps = [(e, _hit_maps(e), _entry_weight(e)) for e in entries]
    hit: set = set()
    for i, rp in enumerate(rubric, 1):
        if not isinstance(rp, dict):
            continue
        rid = rp.get("id", i)
        num = 0.0
        for _e, m, w in maps:
            frac = _HIT_FRACTIONS.get(
                str((m.get(rid) or {}).get("hit", "") or "").strip().lower(), 0.0)
            num += frac * w
        if num / total_w >= 0.5:
            hit.add(rid)
    return hit


def case_metrics(case: Dict[str, Any], result: Any) -> Dict[str, Any]:
    """计算单份用例的三指标明细（含诊断字段）。"""
    expected = case.get("expected") or {}
    exp_set = {h.get("id") for h in (expected.get("hit_points") or [])
               if isinstance(h, dict) and h.get("id") is not None}
    pred_set = aggregate_hit_set(result.rubric, list(result.reviews or []) +
                                 ([result.judge] if result.judge is not None else []))
    inter, union = exp_set & pred_set, exp_set | pred_set
    jaccard = len(inter) / len(union) if union else 1.0
    recall = len(inter) / len(exp_set) if exp_set else 1.0
    precision = len(inter) / len(pred_set) if pred_set else 1.0

    exp_total = float(expected.get("total", 0.0) or 0.0)
    act_total = float(result.score or 0.0)
    diff = abs(act_total - exp_total)

    exp_mt = str(expected.get("mistake_type", "无") or "无")
    act_mt = str(result.mistake_type or "无")
    detail = (f"命中集 J={jaccard:.2f}（预标 {sorted(exp_set)} vs 链路 {sorted(pred_set)}）；"
              f"总分 {act_total} vs 预标 {exp_total}（Δ{diff:.1f}）；"
              f"错因 {act_mt} vs {exp_mt}")
    return {
        "id": str(case.get("id")),
        "subject": case.get("subject"),
        "quality": case.get("quality"),
        "expected_hits": sorted(exp_set),
        "predicted_hits": sorted(pred_set),
        "jaccard": round(jaccard, 4),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "expected_total": exp_total,
        "actual_total": act_total,
        "abs_total_diff": round(diff, 4),
        "expected_mistake": exp_mt,
        "actual_mistake": act_mt,
        "mistake_match": act_mt == exp_mt,
        "match_level": int(getattr(result, "match_level", 1) or 0),
        "arbitrated": bool(getattr(result, "arbitrated", False)),
        "degraded": bool(getattr(result, "degraded", False)),
        "detail": detail,
    }


def _aggregate_metrics(details: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总三指标（均值）与两个链路健康诊断指标。"""
    n = len(details)
    if not n:
        return {"samples": 0, "hit_jaccard": 0.0, "hit_recall": 0.0,
                "hit_precision": 0.0, "total_mae": 0.0, "mistake_agreement": 0.0,
                "degraded_rate": 0.0, "arbitrated_rate": 0.0}
    return {
        "samples": n,
        "hit_jaccard": round(sum(d["jaccard"] for d in details) / n, 4),
        "hit_recall": round(sum(d["recall"] for d in details) / n, 4),
        "hit_precision": round(sum(d["precision"] for d in details) / n, 4),
        "total_mae": round(sum(d["abs_total_diff"] for d in details) / n, 4),
        "mistake_agreement": round(
            sum(1 for d in details if d["mistake_match"]) / n, 4),
        "degraded_rate": round(sum(1 for d in details if d["degraded"]) / n, 4),
        "arbitrated_rate": round(
            sum(1 for d in details if d["arbitrated"]) / n, 4),
    }


def _group_metrics(details: Sequence[Dict[str, Any]],
                   key: str) -> Dict[str, Dict[str, Any]]:
    """按 ``quality`` / ``subject`` 分层汇总（诊断「哪类作答偏得最多」）。"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for d in details:
        groups.setdefault(str(d.get(key, "?")), []).append(d)
    return {g: _aggregate_metrics(items) for g, items in sorted(groups.items())}


# ════════════════════════════════════════════════════════════════
# 快照读写
# ════════════════════════════════════════════════════════════════

def load_snapshots(path: Path) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str]]]:
    """读取快照 jsonl → ``{case_id: 快照对象}``；坏行计入 invalid（不中断整批）。"""
    snaps: Dict[str, Dict[str, Any]] = {}
    invalid: List[Tuple[str, str]] = []
    target = Path(path)
    if not target.exists():
        return snaps, invalid
    for lineno, raw in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            invalid.append((f"line:{lineno}", f"快照 JSON 解析失败: {e.msg}"))
            continue
        if not isinstance(obj, dict) or not str(obj.get("id") or "").strip():
            invalid.append((f"line:{lineno}", "快照缺少 id"))
            continue
        if not isinstance(obj.get("calls"), dict):
            invalid.append((str(obj.get("id")), "快照缺少 calls 对象"))
            continue
        snaps[str(obj["id"])] = obj
    return snaps, invalid


def _write_snapshots(path: Path, snaps: Dict[str, Dict[str, Any]]) -> None:
    """按 id 排序写回快照（确定性输出，便于 diff 与评审）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(snaps[k], ensure_ascii=False) for k in sorted(snaps)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# judge（供 runner）与评测入口
# ════════════════════════════════════════════════════════════════

def build_judge(snapshots: Dict[str, Dict[str, Any]], *,
                metrics_sink: Optional[List[Dict[str, Any]]] = None,
                config: Optional[Dict[str, Any]] = None) -> Callable:
    """返回 ``judge(case) -> (passed, detail)``：回放快照并跑判分链路。

    单条一致判据（探索性）：``Jaccard ≥ HIT_JACCARD_FLOOR`` 且
    ``|Δ总分| ≤ TOTAL_TOLERANCE``。快照未命中的 case 一律判失败并把 miss 记入
    明细，评测层据此计入 invalid（退出码 2）。
    """
    cfg = config or build_pilot_config("http://replay.invalid/v1", "replay", "replay")

    def judge(case: Dict[str, Any]) -> Tuple[bool, str]:
        cid = str(case.get("id"))
        snap = snapshots.get(cid)
        if not isinstance(snap, dict):
            return False, f"缺少快照（需先运行 --grading-live 采集 {cid}）"
        misses: List[str] = []
        client = build_replay_client(snap.get("calls") or {}, miss_sink=misses)
        result = open_grader.grade_open_question(
            question=str(case.get("question") or ""),
            student_answer=str(case.get("student_answer") or ""),
            subject=str(case.get("subject") or "pro"),
            reference_answer=str(case.get("reference_answer") or ""),
            key_points=case.get("key_points"),
            config=cfg,
            llm_client=client,
        )
        m = case_metrics(case, result)
        m["snapshot_misses"] = sorted(set(misses))
        if metrics_sink is not None:
            metrics_sink.append(m)
        if misses:
            return False, (f"快照不完整（未命中 {', '.join(sorted(set(misses)))}）——"
                           f"链路版本与快照采集时不一致或快照被裁剪")
        passed = (m["jaccard"] >= HIT_JACCARD_FLOOR
                  and m["abs_total_diff"] <= TOTAL_TOLERANCE)
        return passed, m["detail"]

    return judge


def evaluate_grading_pilot(cases_path: Optional[Path] = None,
                           snapshots_path: Optional[Path] = None,
                           *, min_samples: int = MIN_PILOT_SAMPLES) -> Dict[str, Any]:
    """回放快照评测判分链路；返回三指标、分层诊断与退出码。

    退出码语义（沿用 runner）：无快照 / 样本不足 / 数据损坏 → 2；
    单条一致率 ≥ ``PILOT_ALERT_THRESHOLD``（探索性预警线）→ 0；否则 → 1。
    """
    cpath = Path(cases_path) if cases_path else CASES_FILE
    spath = Path(snapshots_path) if snapshots_path else SNAPSHOTS_FILE
    if not cpath.exists():
        return _not_evaluable(f"评测集不存在: {cpath}", spath)

    cases, invalid = load_cases(cpath, required_keys=CASE_REQUIRED_KEYS)
    good_cases: List[Dict[str, Any]] = []
    for c in cases:
        problems = validate_case(c)
        if problems:
            invalid.append((str(c.get("id")), "数据契约校验失败：" + "；".join(problems)))
        else:
            good_cases.append(c)

    snapshots, snap_invalid = load_snapshots(spath)
    invalid.extend(snap_invalid)
    if not snapshots:
        return _not_evaluable(
            f"快照不存在或为空: {spath}（需先运行 `python tools/evaluate_pipeline.py "
            f"--grading-live` 采集，一次计费、之后永久回放）", spath)

    runnable: List[Dict[str, Any]] = []
    for c in good_cases:
        if str(c.get("id")) in snapshots:
            runnable.append(c)
        else:
            invalid.append((str(c.get("id")), "缺少快照（需运行 --grading-live 采集该题）"))

    details: List[Dict[str, Any]] = []
    judge = build_judge(snapshots, metrics_sink=details)
    report = run_benchmark(runnable, judge, name="开放题判分 pilot（与预标一致率）",
                           invalid=list(invalid))
    for d in details:
        if d["snapshot_misses"]:
            report.invalid.append(
                (d["id"], f"快照不完整：未命中 {', '.join(d['snapshot_misses'])}"))
    scored = [d for d in details if not d["snapshot_misses"]]

    metrics = _aggregate_metrics(scored)
    code = exit_code(report, threshold=PILOT_ALERT_THRESHOLD, min_samples=min_samples)
    if report.invalid:
        reason = f"存在 {len(report.invalid)} 条无效样本（数据损坏 / 快照缺失）"
    elif report.total < min_samples:
        reason = f"有效样本 {report.total} < {min_samples}（规划下限）"
    else:
        reason = ""
    return {
        "evaluable": bool(scored) and not report.invalid and report.total >= min_samples,
        "samples": report.total,
        "scored_samples": len(scored),
        "metrics": metrics,
        "by_quality": _group_metrics(scored, "quality"),
        "by_subject": _group_metrics(scored, "subject"),
        "passed": code == EXIT_OK,
        "exit_code": code,
        "alert_threshold": PILOT_ALERT_THRESHOLD,
        "report": report.to_dict(),
        "details": scored,
        "invalid": [{"id": cid, "reason": r} for cid, r in report.invalid],
        "reason": reason,
        "disclaimer": ("AI 预标（用户抽检）下的「与预标一致率」，非人工金标准准确率；"
                       "正式 MAE / Kappa 目标待人工标注量上来后再定"),
    }


def _not_evaluable(reason: str, snapshots_path: Path) -> Dict[str, Any]:
    return {
        "evaluable": False, "samples": 0, "scored_samples": 0,
        "metrics": _aggregate_metrics([]), "by_quality": {}, "by_subject": {},
        "passed": False, "exit_code": EXIT_INSUFFICIENT,
        "alert_threshold": PILOT_ALERT_THRESHOLD, "report": None,
        "details": [], "invalid": [], "reason": reason,
        "snapshots_path": str(snapshots_path),
        "disclaimer": "",
    }


# ════════════════════════════════════════════════════════════════
# 采集（--grading-live，**会产生计费调用**）
# ════════════════════════════════════════════════════════════════

def capture_snapshots(cases: Sequence[Dict[str, Any]], snapshots_path: Path,
                      *, base_url: str, api_key: str, model: str,
                      timeout: float = 45.0,
                      progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """真实调用 LLM 采集判分快照（一次性，增量续跑）。

    增量语义：已有快照记录的 case 默认跳过（不重复计费）；想重采某题就先从
    快照文件里删掉它的行。返回统计 ``{captured, skipped, invalid_cases,
    failed_cases, total_snapshots}``。
    """
    path = Path(snapshots_path)
    snaps, _invalid = load_snapshots(path)
    cfg = build_pilot_config(base_url, api_key, model, max_retries=1)
    say = progress or (lambda msg: None)

    captured = skipped = 0
    invalid_cases: List[str] = []
    failed_cases: List[str] = []
    for idx, case in enumerate(cases, 1):
        cid = str(case.get("id"))
        problems = validate_case(case)
        if problems:
            invalid_cases.append(cid)
            say(f"[{idx}/{len(cases)}] {cid} 跳过（契约校验失败：{'；'.join(problems)}）")
            continue
        if cid in snaps:
            skipped += 1
            say(f"[{idx}/{len(cases)}] {cid} 已有快照，跳过")
            continue
        calls: Dict[str, Any] = {}
        say(f"[{idx}/{len(cases)}] {cid} 采集中…")
        try:
            result = open_grader.grade_open_question(
                question=str(case.get("question") or ""),
                student_answer=str(case.get("student_answer") or ""),
                subject=str(case.get("subject") or "pro"),
                reference_answer=str(case.get("reference_answer") or ""),
                key_points=case.get("key_points"),
                config=cfg,
                llm_client=build_capture_client(calls, timeout=timeout),
            )
            ok_calls = sum(1 for v in calls.values() if v.get("ok"))
            say(f"    完成：{ok_calls}/{len(calls)} 次调用成功，"
                f"链路分 {result.score}（{result.match_level}）")
            if not calls:
                # 空作答等边界 case 不发起调用：仍写入快照（calls 为空），
                # 保证「数据集 id ↔ 快照 id」一一对应。
                say("    （该题未发起 LLM 调用，按边界行为记录空快照）")
            if ok_calls == 0 and calls:
                failed_cases.append(cid)
        except Exception as e:  # noqa: BLE001 - 单题失败不中断整批采集
            say(f"    [!] 采集异常（该题将不写入快照）：{type(e).__name__}: {e}")
            failed_cases.append(cid)
            continue
        snaps[cid] = {
            "id": cid,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "model": model,
            "calls": calls,
        }
        captured += 1
        _write_snapshots(path, snaps)   # 每题即时落盘：中断后可续跑

    _write_snapshots(path, snaps)
    return {"captured": captured, "skipped": skipped,
            "invalid_cases": invalid_cases, "failed_cases": failed_cases,
            "total_snapshots": len(snaps)}
