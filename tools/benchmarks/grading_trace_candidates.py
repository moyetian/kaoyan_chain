# -*- coding: utf-8 -*-
"""判卷 traces 分歧候选提取（D3）—— 为开放题判分评测集补充真实"难例"。

【这个脚本解决什么问题】
判分链路会把每次真实开放题判分留一行 JSON 到
``<工作区根>/data/grading/traces/<日期>.jsonl``。其中两类行最值得进评测集：

  * ``divergence`` 高 —— 多次评审分歧大：判分边界题，评测价值最高；
  * ``arbitration.flipped == true`` —— 仲裁改变了结论：默认链路不稳的证据。

本脚本把它们筛出来，转成与评测集 ``tests/benchmarks/grading_pilot.jsonl``
同构的**半成品候选**（``expected`` 留空），人工确认后即可并入。

【完整流程：脚本 + 人工交替，共 5 步】
  1. 候选提取（本脚本）::

         py tools/benchmarks/grading_trace_candidates.py --out 候选.jsonl

     默认扫描 ``data/grading/traces``（相对工作区根），筛
     ``divergence >= 2.0`` **或** ``arbitration.flipped == true`` 的行，
     按 ``ts`` 升序输出。
  2. **人工确认（必须；脚本只产半成品，expected 绝不自动生成）**：
     逐条打开候选，对照参考信息（traces 里通常没有 ``reference_answer`` /
     ``key_points``，去题源 / 教材补），填写 ``reference_answer`` /
     ``key_points`` / ``expected``（``hit_points`` 列表 + ``total`` 总分 +
     ``mistake_type``），并补上 ``quality``（good/medium/poor/blank）与
     ``topic``；最后**删掉 ``_trace`` 字段**——它只是候选来源留痕，评估集
     不认这个键（留着会被数据集契约校验判为未知字段来源）。
  3. 并入评估集：把确认后的行追加进 ``tests/benchmarks/grading_pilot.jsonl``。
     注意：``tests/test_c4_grading_benchmark.py`` 里有评测集规模 / 分布断言
     （30 条、subject 分布、quality 分布），并入新样本后要同步更新，否则
     该测试会红。
  4. 增量采快照：``py tools/evaluate_pipeline.py --grading-live``
     （真实调用 LLM 采集新题的判分快照，一次性冻结；不进 CI 门禁）。
  5. 复算指标：``py tools/evaluate_pipeline.py --grading``（回放零成本），
     看三指标（采分点一致率 / 总分 MAE / 错因一致率）是否因新增难例变化。

【参数与口径】
  * ``--traces-dir``：traces 目录；缺省 = 工作区根下的 ``data/grading/traces``。
    显式传入时按命令行习惯相对当前目录解析。
  * ``--min-divergence``：divergence 筛选阈值，默认 ``2.0``（**含等于**）。
  * ``--out``：候选 JSONL 输出路径；缺省打印到 stdout（可重定向存文件）。
  * 坏行（JSON 损坏 / 顶层非对象）跳过并计数，不让单行损坏作废整个目录；
    目录不存在或没有 ``*.jsonl`` 属正常情况（判分链路还没留痕），退出码 0。
  * stderr 打印 ``扫描 N 行 / 候选 M 条 / 坏行 K 条``；N 含坏行、不含空行。

【输出行结构】（评测集同构 + ``_trace`` 溯源块，供人工确认）::

    {"id": "cand-01", "subject": "...", "question": "...",
     "student_answer": "...", "reference_answer": "", "key_points": [],
     "expected": {"hit_points": [], "total": 0, "mistake_type": ""},
     "_trace": {"source_file": "...", "ts": "...", "divergence": 3.5,
                "flipped": false, "prompt_version": "v1"}}

不做什么：不联网、不调 LLM、不写工作区其它位置、不自动生成 expected。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── 直跑引导：必须先于任何 ``tools.*`` 导入 ─────────────────────────────
# ``py tools/benchmarks/grading_trace_candidates.py`` 直跑时 sys.path[0] 是
# 本目录，仓库根不在路径上；而本机 site-packages 里存在同名空目录 ``tools``
# （命名空间包），一旦它先被导入缓存进 sys.modules，之后再补 sys.path 也
# 无效。故引导必须先完成：仓库根（含 pyproject.toml）插到 sys.path 最前。
_CUR = Path(__file__).resolve()
_REPO_ROOT = _CUR.parents[2]  # tools/benchmarks/x.py -> 仓库根（兜底）
for _cand in (_CUR.parent, *_CUR.parents):
    if (_cand / "pyproject.toml").is_file():
        _REPO_ROOT = _cand
        break
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.workspace import resolve_workspace_root  # noqa: E402

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__all__ = [
    "DEFAULT_MIN_DIVERGENCE",
    "TRACES_DIR_REL",
    "ScanStats",
    "collect_candidates",
    "main",
]

#: traces 目录相对工作区根的默认位置（判分链路留痕写入处）。
TRACES_DIR_REL = ("data", "grading", "traces")

#: divergence 筛选默认阈值（含等于）。
DEFAULT_MIN_DIVERGENCE = 2.0


@dataclass
class ScanStats:
    """一次目录扫描的计数（与 stderr 统计口径一一对应）。"""

    scanned: int = 0      # 读到的非空行数（含坏行）
    candidates: int = 0   # 命中筛选条件的行数
    bad: int = 0          # JSON 损坏 / 顶层非对象的行数


def _as_str(value: Any) -> str:
    """字段取字符串；缺失 / ``None`` → 空串。"""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _as_float(value: Any) -> Optional[float]:
    """数值化；``bool`` / 非数值 / ``NaN`` → ``None``（视为"无该数值"）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if result != result:  # NaN：不参与阈值比较，输出为 null
        return None
    return result


def _flipped_of(obj: Dict[str, Any]) -> bool:
    """``arbitration.flipped`` 是否为布尔 ``true``（缺失 / 非对象 → False）。"""
    arbitration = obj.get("arbitration")
    if not isinstance(arbitration, dict):
        return False
    return arbitration.get("flipped") is True


def _parse_line(line: str) -> Optional[Dict[str, Any]]:
    """解析一行 JSON；损坏或顶层非对象 → ``None``（调用方计入坏行）。"""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _is_candidate(obj: Dict[str, Any], min_divergence: float) -> bool:
    """命中条件：``divergence >= min_divergence`` **或** ``flipped is True``。"""
    divergence = _as_float(obj.get("divergence"))
    if divergence is not None and divergence >= min_divergence:
        return True
    return _flipped_of(obj)


def _render_candidate(
    obj: Dict[str, Any], index: int, source_file: str
) -> Dict[str, Any]:
    """把一条 trace 行转成半成品候选（评测集同构 + ``_trace`` 溯源块）。"""
    return {
        "id": f"cand-{index:02d}",
        "subject": _as_str(obj.get("subject")),
        "question": _as_str(obj.get("question")),
        "student_answer": _as_str(obj.get("student_answer")),
        "reference_answer": "",
        "key_points": [],
        "expected": {"hit_points": [], "total": 0, "mistake_type": ""},
        "_trace": {
            "source_file": source_file,
            "ts": _as_str(obj.get("ts")),
            "divergence": _as_float(obj.get("divergence")),
            "flipped": _flipped_of(obj),
            "prompt_version": _as_str(obj.get("prompt_version")),
        },
    }


def collect_candidates(
    traces_dir: Path,
    min_divergence: float = DEFAULT_MIN_DIVERGENCE,
) -> Tuple[List[Dict[str, Any]], ScanStats]:
    """扫描 ``traces_dir`` 下全部 ``*.jsonl``，返回（候选列表, 统计）。

    - 逐行容错：JSON 损坏 / 顶层非对象 → 跳过并计入 ``bad``；
    - 筛选：``divergence >= min_divergence`` 或 ``arbitration.flipped is True``；
    - 输出按 ``ts`` 升序（稳定排序，同 ``ts`` 保持文件内原序）；
    - 目录不存在 / 没有文件 → 空列表 + 零统计（不是错误场景）。
    """
    stats = ScanStats()
    matched: List[Tuple[Dict[str, Any], str]] = []
    if traces_dir.is_dir():
        for path in sorted(traces_dir.glob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line:
                            continue
                        stats.scanned += 1
                        obj = _parse_line(line)
                        if obj is None:
                            stats.bad += 1
                            continue
                        if _is_candidate(obj, min_divergence):
                            matched.append((obj, path.name))
            except OSError as exc:
                print(
                    f"[traces] 跳过无法读取的文件 {path.name}：{exc}",
                    file=sys.stderr,
                )

    matched.sort(key=lambda pair: _as_str(pair[0].get("ts")))
    stats.candidates = len(matched)
    candidates = [
        _render_candidate(obj, index, source_file)
        for index, (obj, source_file) in enumerate(matched, start=1)
    ]
    return candidates, stats


def _print_stats(stats: ScanStats) -> None:
    """统计行（stderr）：口径与 :class:`ScanStats` 字段一一对应。"""
    print(
        f"[traces] 扫描 {stats.scanned} 行 / 候选 {stats.candidates} 条"
        f" / 坏行 {stats.bad} 条",
        file=sys.stderr,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口。返回退出码：0 = 正常（含空目录）；1 = ``--out`` 写入失败。"""
    parser = argparse.ArgumentParser(
        description=(
            "从判分 traces 中提取分歧候选（半成品，expected 留空，"
            "人工确认后并入评测集）"
        ),
    )
    parser.add_argument(
        "--traces-dir",
        default=None,
        help="traces 目录；缺省 = <工作区根>/data/grading/traces",
    )
    parser.add_argument(
        "--min-divergence",
        type=float,
        default=DEFAULT_MIN_DIVERGENCE,
        help=f"divergence 筛选阈值（含等于），默认 {DEFAULT_MIN_DIVERGENCE}",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="候选 JSONL 输出路径；缺省打印到 stdout",
    )
    args = parser.parse_args(argv)

    if args.traces_dir is not None:
        traces_dir = Path(args.traces_dir)
    else:
        traces_dir = resolve_workspace_root(__file__).joinpath(*TRACES_DIR_REL)

    if not traces_dir.is_dir():
        print(
            f"[traces] 目录不存在：{traces_dir}"
            "（判分链路尚未留痕，属正常情况）",
            file=sys.stderr,
        )
    elif not any(traces_dir.glob("*.jsonl")):
        print(
            f"[traces] 目录中没有 *.jsonl 文件：{traces_dir}"
            "（尚未产生留痕，属正常情况）",
            file=sys.stderr,
        )

    candidates, stats = collect_candidates(traces_dir, args.min_divergence)

    if args.out is not None:
        out_path = Path(args.out)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps(c, ensure_ascii=False) for c in candidates]
            out_path.write_text(
                ("\n".join(lines) + "\n") if lines else "",
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"[traces] 写入失败：{out_path}（{exc}）", file=sys.stderr)
            return 1
        print(
            f"[traces] 已写入 {len(candidates)} 条候选 → {out_path}",
            file=sys.stderr,
        )
    else:
        for candidate in candidates:
            print(json.dumps(candidate, ensure_ascii=False))

    _print_stats(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
