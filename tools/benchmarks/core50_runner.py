#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core50 评测适配层（W2）—— 数据集入库本仓库，评测器独立安装。

背景
----
core50（50 题 / 8 类 / 100 分制 7 维）是 KaoyanBench（独立公开仓库
``moyetian/Kaoyan_chain_Bench``）的主测试集。W2 把**数据集**入库本仓库
（``tests/benchmarks/core50/``，目录原样镜像上游；**契约 schema 以评测器为
真源**，本仓库不自行发明字段），本适配层负责把评测器 CLI 接进仓库 CI：

* ``--check``  数据集完整性（``validate --require-snapshots``，只读，零网络）；
* ``--smoke``  冒烟 10 题（``smoke10.tasks.txt``，8 类覆盖，离线 mock，
  确定性重放）+ 全 PASS 断言；
* ``--full``   全量 50 题（``core50`` suite，离线 mock）+ 全 PASS 断言；
* ``--gate``   真实评测回归门禁：对比两个 suite JSON —— 成功率降超 3pp
  或任一维降超 10 点即失败（升级规划 W2 的验收阈值）。

退出码（沿用 ``tools/benchmarks/runner.py`` 的三态约定，口径唯一）：
  0 = 通过；1 = 门禁失败（评测结果不达标）；2 = 不可评测
  （评测器缺失 / 数据集损坏 / 输入文件不可读）。

评测器定位顺序（任一命中即用）：
  1. ``--bench-cli`` 显式路径；
  2. 环境变量 ``KAOYANBENCH_CLI``；
  3. PATH 上的 ``kaoyanbench``；
  4. 当前解释器的 ``python -m kaoyanbench``。
全部不可用时退出码 2，并打印安装命令（不做静默跳过）。

为什么 mock 模式的门禁是"全 PASS"而不是对比历史基线：mock 的预设答案
（``config/agents/mock.yaml``）就是为满分设计的确定性重放——任何一题 FAIL
都说明数据集/检查/管线被改坏；而真实评测的对比门禁（-3pp / -10）由
``--gate`` 承担。两类门禁语义不同，刻意分开。

运行产物（``<数据集根>/results/``，评测器固定写入位置）由
``tests/benchmarks/core50/.gitignore`` 排除，绝不入库。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── sys.path 引导：必须先于任何 `tools.*` 导入 ──────────────────────────
# 直跑（`py tools/benchmarks/core50_runner.py`）时 sys.path[0] 是本目录，
# 仓库根不在路径上；而本机 site-packages 里存在同名空目录 `tools`
# （命名空间包），一旦它先被导入缓存进 sys.modules，之后再补 sys.path
# 也无效（实测：二次导入仍报 No module named 'tools.benchmarks'）。
# 故引导必须先完成：仓库根（含 pyproject.toml）插到 sys.path 最前。


def find_repo_root() -> Path:
    """向上查找含 ``pyproject.toml`` 的仓库根；找不到返回脚本上两级。"""
    cur = Path(__file__).resolve()
    for candidate in (cur.parent, *cur.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return cur.parent.parent.parent


_REPO_ROOT = find_repo_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.benchmarks.runner import EXIT_FAIL, EXIT_INSUFFICIENT, EXIT_OK

#: 数据集相对仓库根的路径（镜像上游 benchmark/ 与 config/ 的布局）。
DATA_ROOT_REL = ("tests", "benchmarks", "core50")

#: 冒烟题单（10 题 / 8 类覆盖，全部 offline + deterministic）。
SMOKE_TASKS_FILE = "smoke10.tasks.txt"
SMOKE_SUITE_ID = "(tasks-file)"   # 评测器对 --tasks-file 的 suite_id 口径
SMOKE_EXPECTED_N = 10

#: 全量 suite 名与题数。
FULL_SUITE = "core50"
FULL_EXPECTED_N = 50

#: 回归门禁阈值（升级规划 W2）：成功率降超 3pp 或任一维降超 10 点即失败。
GATE_SUCCESS_MAX_DROP_PP = 3.0
GATE_DIM_MAX_DROP = 10.0

#: 评测器安装提示（退出码 2 时打印）。
_INSTALL_HINT = (
    'pip install "git+https://github.com/moyetian/Kaoyan_chain_Bench@<sha>"'
)


def resolve_data_root(explicit: Optional[str]) -> Optional[Path]:
    """解析数据集根；显式路径不存在返回 None（调用方判 2）。"""
    root = Path(explicit).resolve() if explicit else _REPO_ROOT.joinpath(*DATA_ROOT_REL)
    if not root.is_dir():
        return None
    return root


def find_bench_cli(explicit: Optional[str] = None) -> Optional[Tuple[List[str], str]]:
    """定位评测器 CLI，返回 ``(命令前缀, 版本串)``；全部不可用返回 None。"""
    candidates: List[List[str]] = []
    if explicit:
        candidates.append([explicit])
    env_cli = (os.environ.get("KAOYANBENCH_CLI") or "").strip()
    if env_cli:
        candidates.append([env_cli])
    which = shutil.which("kaoyanbench")
    if which:
        candidates.append([which])
    candidates.append([sys.executable, "-m", "kaoyanbench"])

    for cmd in candidates:
        try:
            probe = subprocess.run(cmd + ["--version"], capture_output=True,
                                   text=True, encoding="utf-8",
                                   errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            lines = (probe.stdout or probe.stderr or "").strip().splitlines()
            return cmd, (lines[0].strip() if lines else "unknown")
    return None


def run_bench(bench_cmd: Sequence[str], data_root: Path,
              sub_args: Sequence[str]) -> subprocess.CompletedProcess:
    """执行一条评测器命令（``--root`` 指向本仓库数据集）。"""
    cmd = list(bench_cmd) + ["--root", str(data_root)] + list(sub_args)
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _suite_json_path(data_root: Path, suite_id: str, agent: str, tag: str) -> Path:
    return data_root / "results" / "suites" / f"{suite_id}__{agent}__{tag}.suite.json"


def load_suite_json(path: Path) -> Optional[Dict[str, Any]]:
    """读取 suite JSON；结构不合法返回 None（调用方判 2）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("aggregates"), dict):
        return None
    return data


def _new_tag(mode: str) -> str:
    return f"ky-{mode}-{time.strftime('%Y%m%d-%H%M%S')}"


# ── 各模式实现 ──────────────────────────────────────────────────────────


def run_check(bench_cmd: Sequence[str], data_root: Path) -> int:
    """数据集完整性：validate --suite core50 --require-snapshots（只读）。

    校验失败（含评测器自身报错）一律归为 2 = 不可评测：数据集坏掉时
    "结果不达标"（1）与"根本没跑起来"（2）是两种性质，CI 上都要红，
    但语义必须分得开（1 意味着评测结论有效且为负）。
    """
    print(f"[core50] 数据集校验: {data_root}")
    r = run_bench(bench_cmd, data_root,
                  ["validate", "--suite", FULL_SUITE, "--require-snapshots"])
    tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
    for line in tail:
        print(f"  {line}")
    if r.returncode == EXIT_OK:
        print("[core50] 数据集完整性通过 ✅")
        return EXIT_OK
    print(f"[core50] 数据集校验失败（评测器退出码 {r.returncode}）")
    return EXIT_INSUFFICIENT


def _first_failure_detail(grade: Dict[str, Any]) -> str:
    """从一条 grade 记录里取首个未通过检查的说明（供失败清单打印）。"""
    for chk in grade.get("checks") or []:
        if isinstance(chk, dict) and not chk.get("passed"):
            detail = str(chk.get("detail") or chk.get("id") or "").strip()
            if detail:
                return detail
    reason = str(grade.get("degraded_reason") or "").strip()
    return reason or "详见 suite JSON"


def run_mock(bench_cmd: Sequence[str], data_root: Path, mode: str,
             tag: Optional[str] = None) -> int:
    """离线 mock 冒烟（10 题）或全量（50 题）+ 全 PASS 断言。"""
    if mode == "smoke":
        tasks_file = data_root / SMOKE_TASKS_FILE
        if not tasks_file.is_file():
            print(f"[core50] 冒烟题单缺失: {tasks_file}")
            return EXIT_INSUFFICIENT
        suite_id, expected_n = SMOKE_SUITE_ID, SMOKE_EXPECTED_N
        run_args = ["run", "--tasks-file", str(tasks_file), "--agent", "mock",
                    "--runs", "1", "--concurrency", "4"]
    else:
        suite_id, expected_n = FULL_SUITE, FULL_EXPECTED_N
        run_args = ["run", "--suite", FULL_SUITE, "--agent", "mock",
                    "--runs", "1", "--concurrency", "4"]

    tag = tag or _new_tag(mode)
    run_args += ["--tag", tag]
    print(f"[core50] {mode} 离线重放（mock）tag={tag}")
    r = run_bench(bench_cmd, data_root, run_args)
    if r.returncode != EXIT_OK:
        print(f"[core50] 评测器执行失败（退出码 {r.returncode}）:")
        for line in (r.stderr or r.stdout or "").strip().splitlines()[-5:]:
            print(f"  {line}")
        return EXIT_INSUFFICIENT

    suite_path = _suite_json_path(data_root, suite_id, "mock", tag)
    data = load_suite_json(suite_path)
    if data is None:
        print(f"[core50] suite JSON 不可读或结构异常: {suite_path}")
        return EXIT_INSUFFICIENT

    n_tasks = int(data.get("task_count") or 0)
    if n_tasks != expected_n:
        print(f"[core50] 题数不符：期望 {expected_n}，实际 {n_tasks}（数据集损坏？）")
        return EXIT_INSUFFICIENT

    # 每题成败的真源是 grades[].metrics.task_success —— runs[].success 在本
    # 评测器版本（1.1.0）里恒为 null（实测：success_rate=100% 而 run.success
    # 全为 null），按它判会把全通过误报成全失败。
    grades = data.get("grades")
    if not isinstance(grades, list) or not grades:
        print("[core50] suite JSON 缺少 grades 段（评测器版本不兼容？）")
        return EXIT_INSUFFICIENT

    agg = data["aggregates"]
    success_rate = float(agg.get("task_success_rate") or 0.0)
    score_mean = float(agg.get("score_mean") or 0.0)

    failed = [g for g in grades
              if not (g.get("metrics") or {}).get("task_success")]
    if failed or success_rate < 1.0:
        print(f"[core50] ❌ 未全部通过：success_rate={success_rate:.1%} "
              f"score_mean={score_mean:.2f}")
        for g in failed[:10]:
            print(f"  FAIL {g.get('task_id')}: {_first_failure_detail(g)}")
        return EXIT_FAIL

    print(f"[core50] ✅ {n_tasks} 题全部通过（score_mean={score_mean:.2f}，"
          f"确定性重放一致）")
    print(f"[core50] suite JSON: {suite_path}")
    return EXIT_OK


def run_gate(baseline: Optional[str], current: Optional[str]) -> int:
    """真实评测回归门禁：成功率 -3pp / 任一维 -10 点。"""
    if not baseline or not current:
        print("[core50] --gate 需要 --baseline 与 --current（两个 suite JSON 路径）")
        return EXIT_INSUFFICIENT
    base = load_suite_json(Path(baseline))
    cur = load_suite_json(Path(current))
    if base is None:
        print(f"[core50] 基线不可读: {baseline}")
        return EXIT_INSUFFICIENT
    if cur is None:
        print(f"[core50] 当前结果不可读: {current}")
        return EXIT_INSUFFICIENT

    problems: List[str] = []

    b_sr = float(base["aggregates"].get("task_success_rate") or 0.0)
    c_sr = float(cur["aggregates"].get("task_success_rate") or 0.0)
    delta_pp = (c_sr - b_sr) * 100.0
    flag = "FAIL" if delta_pp < -GATE_SUCCESS_MAX_DROP_PP else "pass"
    print(f"[core50] task_success_rate  {b_sr:.1%} → {c_sr:.1%}  "
          f"Δ={delta_pp:+.1f}pp  {flag}")
    if delta_pp < -GATE_SUCCESS_MAX_DROP_PP:
        problems.append(
            f"成功率下降 {abs(delta_pp):.1f}pp（阈值 -{GATE_SUCCESS_MAX_DROP_PP:g}pp）")

    b_dims = base.get("score_breakdown_mean") or {}
    c_dims = cur.get("score_breakdown_mean") or {}
    for dim in sorted(set(b_dims) & set(c_dims)):
        try:
            delta = float(c_dims[dim]) - float(b_dims[dim])
        except (TypeError, ValueError):
            continue
        flag = "FAIL" if delta < -GATE_DIM_MAX_DROP else "pass"
        print(f"[core50] {dim:<18} {float(b_dims[dim]):7.2f} → "
              f"{float(c_dims[dim]):7.2f}  Δ={delta:+7.2f}  {flag}")
        if delta < -GATE_DIM_MAX_DROP:
            problems.append(
                f"维度 {dim} 下降 {abs(delta):.1f} 点（阈值 -{GATE_DIM_MAX_DROP:g}）")

    if problems:
        print("[core50] ❌ 回归门禁失败:")
        for p in problems:
            print(f"  - {p}")
        return EXIT_FAIL
    print("[core50] ✅ 回归门禁通过")
    return EXIT_OK


# ── CLI ─────────────────────────────────────────────────────────────────


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="core50 评测适配层（数据集在本仓库，评测器独立安装）")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="数据集完整性校验（validate --require-snapshots）")
    mode.add_argument("--smoke", action="store_true",
                      help="冒烟 10 题（离线 mock，确定性重放）")
    mode.add_argument("--full", action="store_true",
                      help="全量 50 题（离线 mock，确定性重放）")
    mode.add_argument("--gate", action="store_true",
                      help="回归门禁：对比 --baseline 与 --current")
    p.add_argument("--baseline", help="基线 suite JSON 路径（--gate 用）")
    p.add_argument("--current", help="当前 suite JSON 路径（--gate 用）")
    p.add_argument("--data-root", help="数据集根（默认 tests/benchmarks/core50）")
    p.add_argument("--bench-cli", help="评测器可执行路径（默认自动探测）")
    p.add_argument("--tag", help="本次 run 的 tag（默认自动生成）")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    if args.gate:
        return run_gate(args.baseline, args.current)

    data_root = resolve_data_root(args.data_root)
    if data_root is None:
        print(f"[core50] 数据集根不存在: {args.data_root or '/'.join(DATA_ROOT_REL)}")
        return EXIT_INSUFFICIENT

    bench = find_bench_cli(args.bench_cli)
    if bench is None:
        print("[core50] 未找到 KaoyanBench 评测器（退出码 2 = 不可评测）。")
        print(f"  安装: {_INSTALL_HINT}")
        print("  或用 --bench-cli / KAOYANBENCH_CLI 指定路径。")
        return EXIT_INSUFFICIENT
    bench_cmd, bench_version = bench
    print(f"[core50] 评测器: {' '.join(bench_cmd)}（版本 {bench_version}）")

    if args.check:
        return run_check(bench_cmd, data_root)
    if args.smoke:
        return run_mock(bench_cmd, data_root, "smoke", tag=args.tag)
    return run_mock(bench_cmd, data_root, "full", tag=args.tag)


if __name__ == "__main__":
    raise SystemExit(main())
