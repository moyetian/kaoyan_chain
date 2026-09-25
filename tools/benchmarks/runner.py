# -*- coding: utf-8 -*-
"""通用 jsonl 评测 runner —— C1（考纲守卫）/ C2（引文忠实度）共用。

设计边界（刻意最小，避免成为"又一个评测框架"）：
  * 读 jsonl 用例（每行一个 JSON 对象，至少含 ``id``）；
  * 逐条调用调用方注入的 ``judge(case) -> (passed, detail)``；
  * 统计通过率、输出可读报告、给出与 ``evaluate_pipeline`` 一致的退出码语义：
    ``0`` = 达标 / ``1`` = 不达标 / ``2`` = 样本不足或数据损坏（如实不可评估）。

不做什么：不定义"什么算通过"（那是各评测集自己的契约）、不联网、不调 LLM、
不写盘。这样 C1 与 C2 只需要各自提供数据文件 + 一个 judge 回调。

为什么要抽这一层（而不是各写各的循环）：A1 修 FSRS 评测时已经踩过一次
"评测口径散落、退出码语义各写各的"的坑；C 系列有 6 个批次，如果每个评测
都重新实现一遍"读数据 → 跑 → 统计 → 退出码"，口径必然漂移。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: 退出码语义（与 tools/evaluate_pipeline.py 的 0/1/2 完全一致）
EXIT_OK = 0          # 达标
EXIT_FAIL = 1        # 不达标（通过率低于阈值）
EXIT_INSUFFICIENT = 2  # 样本不足 / 数据损坏，如实不可评估

Judge = Callable[[Dict[str, Any]], Tuple[bool, str]]


@dataclass
class BenchReport:
    """一次评测的汇总结果。"""

    name: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    #: 失败明细：``(case_id, detail)``；detail 由 judge 提供（失败原因）。
    failures: List[Tuple[str, str]] = field(default_factory=list)
    #: 无效样本：JSON 解析失败 / 缺必需键的行，``(行号或 id, 原因)``。
    invalid: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        """通过率；无有效样本时返回 0.0（由退出码层负责判"样本不足"）。"""
        return (self.passed / self.total) if self.total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "failures": [{"id": cid, "detail": d} for cid, d in self.failures],
            "invalid": [{"id": cid, "reason": d} for cid, d in self.invalid],
        }


def load_cases(
    path: Path,
    required_keys: Sequence[str] = ("id",),
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """读取 jsonl 用例文件。

    容错口径（与 session_log 的"半截行丢弃"同款）：
      * 空行跳过；
      * 非法 JSON / 非对象 / 缺必需键 → 计入 invalid 并**继续**（不中断整批）；
      * 其余原样返回。

    Returns:
        ``(cases, invalid)``；invalid 为 ``(行号字符串, 原因)`` 列表。
    """
    cases: List[Dict[str, Any]] = []
    invalid: List[Tuple[str, str]] = []
    text = Path(path).read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            invalid.append((f"line:{lineno}", f"JSON 解析失败: {e.msg}"))
            continue
        if not isinstance(obj, dict):
            invalid.append((f"line:{lineno}", f"顶层不是对象: {type(obj).__name__}"))
            continue
        missing = [k for k in required_keys if k not in obj]
        if missing:
            invalid.append((f"line:{lineno}", f"缺少必需键: {', '.join(missing)}"))
            continue
        cases.append(obj)
    return cases, invalid


def run_benchmark(
    cases: Sequence[Dict[str, Any]],
    judge: Judge,
    name: str = "benchmark",
    invalid: Optional[Sequence[Tuple[str, str]]] = None,
) -> BenchReport:
    """逐条执行 judge 并汇总。

    judge 抛异常时**不中断整批**：该条记为失败（detail 含异常类型与消息），
    因为"评测对象崩了"本身就是不达标信号，而不是"评测没法跑"。
    """
    report = BenchReport(name=name, invalid=list(invalid or []))
    for case in cases:
        cid = str(case.get("id", "?"))
        try:
            passed, detail = judge(case)
        except Exception as e:  # noqa: BLE001 - 评测需要把被测方的崩溃计入失败
            passed, detail = False, f"judge 异常: {type(e).__name__}: {e}"
        report.total += 1
        if passed:
            report.passed += 1
        else:
            report.failed += 1
            report.failures.append((cid, detail))
    return report


def exit_code(
    report: BenchReport,
    threshold: float = 0.98,
    min_samples: int = 1,
) -> int:
    """按通过率与样本量给出退出码（0/1/2 语义与 evaluate_pipeline 一致）。

    * 有效样本 < ``min_samples``，或存在**任何**无效样本 → ``2``
      （数据损坏时"通过率"没有意义，如实不可评估，不得伪装成 0 或 1）；
    * 通过率 >= ``threshold`` → ``0``；
    * 否则 → ``1``。
    """
    if report.total < min_samples or report.invalid:
        return EXIT_INSUFFICIENT
    return EXIT_OK if report.pass_rate >= threshold else EXIT_FAIL


def print_report(report: BenchReport, threshold: float = 0.98) -> None:
    """打印人类可读的评测报告（失败明细截断到前 20 条）。"""
    print(f"\n===== 评测报告：{report.name} =====")
    print(f"  有效样本: {report.total}  通过: {report.passed}  失败: {report.failed}  "
          f"通过率: {report.pass_rate:.2%}（阈值 {threshold:.0%}）")
    if report.invalid:
        print(f"  [!] 无效样本 {len(report.invalid)} 条（数据损坏 → 退出码 2）：")
        for cid, reason in report.invalid[:10]:
            print(f"      - {cid}: {reason}")
    if report.failures:
        print(f"  [!] 失败明细（前 20 条）：")
        for cid, detail in report.failures[:20]:
            print(f"      - {cid}: {detail}")
        if len(report.failures) > 20:
            print(f"      ... 其余 {len(report.failures) - 20} 条省略")
    print("=" * 40)
