# -*- coding: utf-8 -*-
"""考研学习链 · CI 评测门禁驱动 (CI Evaluation Gate Driver)   [A4 门禁收紧]

把 ``tools/evaluate_pipeline.py`` 的退出码翻译成 GitHub Actions 注解，供 CI 调用：

  0 = 评测达标     → ``::notice::`` 通过；
  1 = 评测不达标   → ``::error::`` 让 CI 红（这是真不达标）；
  2 = 样本不足     → ``::warning::`` 按约定**跳过，不算失败**；
  其他（含崩溃）   → ``::error::``。

为什么单独写一个脚本，而不是在 workflow 里用 shell 判断：

1. 矩阵含 Windows / Linux / macOS，默认 shell 各不相同；Windows 的 pwsh 下
   多行 ``run:`` 的退出码由**最后一条命令**决定，前一条命令失败会被静默掩盖。
   把全部判断收进一个 Python 进程，跨平台行为完全一致。
2. GitHub Actions 的 ``continue-on-error`` 只能表达「失败是否阻塞」，
   无法区分退出码 1（真不达标，必须红）与 2（样本不足，只告警）。
3. 修复后真实用户数据大概率返回 2（样本不足），不能作为 CI 判据，故 CI 改用
   **已提交的夹具日志** ``tests/fixtures/srs_log_*.jsonl`` 跑通 0/1/2 三条路径。

本脚本零第三方依赖、**只读**（只运行评测流水线，不写任何文件），可在本地直接跑。

用法：
  py tools/ci_evaluate_gate.py --fixtures      # CI 用：夹具自检，一次跑通 0/1/2 三条路径
  py tools/ci_evaluate_gate.py --ragas         # CI 用：引文忠实度门禁（C2：109 条评测集，完全离线）
  py tools/ci_evaluate_gate.py --syllabus      # CI 用：考纲守卫门禁（128 条评测集，完全离线）
  py tools/ci_evaluate_gate.py --srs           # 生产门禁：读真实 .memory/review_log.jsonl
  py tools/ci_evaluate_gate.py --srs --log <path> [--expect N]   # 指定日志 / 断言退出码（阴性验证）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "tools" / "evaluate_pipeline.py"

#: 已提交的夹具日志 → (相对路径, 应得退出码, 说明)。
#: 依据 tests/test_evaluate_pipeline_srs.py 覆盖的同一组 0/1/2 语义，在 CI 的
#: 真实 runner 上再跑一遍，证明「门禁本身」的判别力有效（阴性验证不依赖真实数据）。
FIXTURES: List[Tuple[str, int, str]] = [
    ("tests/fixtures/srs_log_ok.jsonl", 0, "校准达标，应通过"),
    ("tests/fixtures/srs_log_fail.jsonl", 1, "校准不达标（阴性样本），应判红"),
    ("tests/fixtures/srs_log_insufficient.jsonl", 2, "有效样本不足，应告警跳过"),
    ("tests/fixtures/srs_log_residue_all_stage0.jsonl", 2, "全为 stage0 残留，应告警跳过"),
]


def _child_env() -> Dict[str, str]:
    """子进程环境：强制 UTF-8 输出，避免 Windows 控制台编码造成 UnicodeEncodeError。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_pipeline(extra_args: Sequence[str], expect: Optional[int], label: str) -> bool:
    """运行一次评测流水线，按退出码输出 GitHub 注解；返回本次调用是否通过。

    ``expect is None`` 时按生产门禁语义判定：0 通过；1 与其他退出码判红；
    2 告警跳过（不算失败）。指定 ``expect`` 时改为断言实际退出码与预期一致，
    供夹具阴性验证使用。
    """
    cmd = [sys.executable, str(PIPELINE), *extra_args]
    print(f"\n[gate] $ {' '.join(cmd)}")
    code = subprocess.run(cmd, cwd=str(ROOT), env=_child_env()).returncode

    if code == 0:
        verdict, level = "达标", "notice"
    elif code == 1:
        verdict, level = "未达标", "error"
    elif code == 2:
        verdict, level = "样本不足，按约定跳过（不算失败）", "warning"
    else:
        verdict, level = f"异常退出码 {code}（崩溃或用法错误）", "error"

    if expect is None:
        print(f"::{level}::{label}：退出码 {code} —— {verdict}")
        return code in (0, 2)
    if code == expect:
        note = "；阴性样本按预期判红，门禁判别力有效" if code == 1 else ""
        print(f"::notice::{label}：退出码 {code} —— 与预期一致：{verdict}{note}")
        return True
    print(f"::error::{label}：预期退出码 {expect}，实际 {code} —— {verdict}")
    return False


def run_fixtures() -> bool:
    """用已提交的夹具日志跑通 0/1/2 三条路径（不依赖真实用户数据）。"""
    passed = True
    for rel, expect, desc in FIXTURES:
        if not (ROOT / rel).exists():
            print(f"::error::夹具缺失：{rel}（{desc}）")
            passed = False
            continue
        passed = run_pipeline(["--srs", "--log", rel], expect, f"{rel}（{desc}）") and passed
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CI 评测门禁驱动：把 evaluate_pipeline 的退出码翻译成 GitHub Actions 注解"
    )
    parser.add_argument("--fixtures", action="store_true",
                        help="用已提交的夹具日志跑通 0/1/2 三条路径（CI 自检，无参数时的默认动作）")
    parser.add_argument("--srs", action="store_true",
                        help="FSRS 校准度门禁（默认读真实 .memory/review_log.jsonl）")
    parser.add_argument("--ragas", action="store_true",
                        help="引文忠实度门禁（C2：tests/benchmarks/citation_faithfulness.jsonl，完全离线）")
    parser.add_argument("--syllabus", action="store_true",
                        help="考纲守卫门禁（C1：tests/benchmarks/syllabus_guard.jsonl，完全离线）")
    parser.add_argument("--log", type=str, default="", help="[--srs] 指定复测事件日志路径")
    parser.add_argument("--expect", type=int, default=None,
                        help="断言流水线退出码（夹具阴性验证用）；省略则按生产门禁语义判定")
    args = parser.parse_args()

    if not (args.fixtures or args.srs or args.ragas or args.syllabus):
        args.fixtures = True

    passed = True
    if args.fixtures:
        passed = run_fixtures() and passed
    if args.srs:
        extra = ["--srs"] + (["--log", args.log] if args.log else [])
        label = f"--srs（{args.log or '.memory/review_log.jsonl'}）"
        passed = run_pipeline(extra, args.expect, label) and passed
    if args.ragas:
        passed = run_pipeline(["--ragas"], args.expect, "--ragas（引文忠实度）") and passed
    if args.syllabus:
        passed = run_pipeline(["--syllabus"], args.expect, "--syllabus（考纲守卫）") and passed

    print()
    if not passed:
        print("::error::评测门禁未通过")
        return 1
    print("[OK] 评测门禁全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
