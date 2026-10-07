# -*- coding: utf-8 -*-
"""
判卷回归对比命令模块 (grading.py)
包含 grade-regress —— 判卷 prompt 版本的回归对比入口（报告先行）

[为什么需要这个入口] 判分链路（``open_grader.grade_open_question``）的 prompt
一旦修改，其效果是否退化此前只能靠人工跑评测脚本对比；且快照回放依赖采集时的
prompt 文本 —— 换版本后旧快照不可回放，若无显式报错，很容易拿错配快照算出
"看起来正常"的错误指标。本命令把「两版本三指标对比」接到 ``ky grade-regress``：
  * 默认快照回放（零网络、零成本）；版本不匹配时**明确报错**（fail-closed）；
  * ``--live`` 真实 LLM 评测（**计费**，不写快照文件）；
  * 判定阈值与门禁开关（``exam_grading.regress_gate``，默认 False）为
    **报告先行**：只影响本命令的显示与退出码，不改变任何默认行为。
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.repl.renderer import C, colorize
    from tools.cli.shared import ROOT
except ImportError:
    from cli.dispatch import Command, register
    from cli.repl.renderer import C, colorize
    from cli.shared import ROOT

_USAGE = """
判卷 prompt 回归对比 (ky grade-regress) —— 报告先行，默认只报告不阻断
用法：
  ky grade-regress --prompt <候选版本> --baseline <基线版本> [--live] [--cases PATH] [--json]
示例：
  ky grade-regress --prompt v2 --baseline v1
  ky grade-regress --prompt v2 --baseline v1 --json
  ky grade-regress --prompt v2 --baseline v1 --live     # [计费] 真实跑

说明：
  对比两个判卷 prompt 版本在 grading pilot（30 份 AI 预标）上的三指标：
    1) 采分点命中一致率（Jaccard）；2) 总分 MAE（10 分制）；3) 错因一致率。
  默认用冻结快照回放（零网络、零成本）。快照响应绑定采集时的 prompt 文本，
  换版本后旧快照不可回放 —— 版本不匹配时**明确报错**（不静默给出错误指标），
  请改用 --live 真实跑（计费，≈21 分钟/30 份）或用 `--grading-live` 重新采集。
  --live 真实调用 LLM 评测（**计费**；不写快照文件）。

判定（报告先行；阈值可在 ky_config.json 的 exam_grading 段覆盖）：
  MAE 上升 > 0.5 或 命中一致率下降 > 5% 或 错因一致率下降 > 10% → fail
  （覆盖键：regress_mae_delta / regress_jaccard_drop / regress_mistake_drop）
  退化达到阈值一半但未超 → warn；否则 pass。

退出码：
  0 = 对比完成（pass / warn；或 fail 但门禁未开启）
  2 = 无法对比（快照版本不匹配 / 评测不可行），或门禁开启且 verdict=fail
"""


def _load_regress_options() -> Dict[str, Any]:
    """从 ky_config.json 的 exam_grading 段读取门禁开关与阈值覆盖。

    默认 ``regress_gate=False``（报告先行：不阻断任何流程）；阈值键缺失时
    不覆盖，交由 ``grading_judge.REGRESS_THRESHOLDS`` 默认值。读取失败
    （文件缺失 / JSON 损坏）一律回落默认 —— 评测命令不得因配置读取失败崩溃。
    """
    opts: Dict[str, Any] = {"regress_gate": False, "thresholds": {}}
    try:
        data = json.loads((ROOT / "ky_config.json").read_text(encoding="utf-8"))
    except Exception:
        return opts
    eg = data.get("exam_grading")
    if not isinstance(eg, dict):
        return opts
    opts["regress_gate"] = bool(eg.get("regress_gate", False))
    thresholds: Dict[str, float] = {}
    for key, cfg_key in (("mae_delta", "regress_mae_delta"),
                         ("hit_jaccard_drop", "regress_jaccard_drop"),
                         ("mistake_type_drop", "regress_mistake_drop")):
        val = eg.get(cfg_key)
        if val is not None:
            try:
                thresholds[key] = float(val)
            except (TypeError, ValueError):
                pass
    opts["thresholds"] = thresholds
    return opts


def _load_llm_config() -> Optional[Dict[str, str]]:
    """读取 ky_config.json 顶层的 LLM 连接配置（--live 用；缺失由 compare 报错）。"""
    try:
        data = json.loads((ROOT / "ky_config.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    return {"base_url": str(data.get("base_url") or "").strip(),
            "api_key": str(data.get("api_key") or "").strip(),
            "model": str(data.get("model") or "").strip()}


def _parse_args(args: List[str]) -> Dict[str, Any]:
    """手动解析 grade-regress 参数（支持 ``--opt=v`` 与 ``--opt v`` 两种写法）。"""
    opts: Dict[str, Any] = {"prompt": "", "baseline": "", "live": False,
                            "cases": "", "json": False}
    i = 1
    while i < len(args):
        a = str(args[i])
        if a == "--live":
            opts["live"] = True
        elif a == "--json":
            opts["json"] = True
        elif a.startswith("--prompt="):
            opts["prompt"] = a.split("=", 1)[1].strip()
        elif a == "--prompt" and i + 1 < len(args):
            i += 1
            opts["prompt"] = str(args[i]).strip()
        elif a.startswith("--baseline="):
            opts["baseline"] = a.split("=", 1)[1].strip()
        elif a == "--baseline" and i + 1 < len(args):
            i += 1
            opts["baseline"] = str(args[i]).strip()
        elif a.startswith("--cases="):
            opts["cases"] = a.split("=", 1)[1].strip()
        elif a == "--cases" and i + 1 < len(args):
            i += 1
            opts["cases"] = str(args[i]).strip()
        else:
            print(colorize(f"[!] 未知参数: {a}", C.RED))
            print(_USAGE)
            sys.exit(1)
        i += 1
    return opts


def _pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def _render_comparison(result: Dict[str, Any]) -> None:
    """渲染三指标对比表（基线 / 候选 / 差值 / 判定）。"""
    print("\n=== 判卷 prompt 回归对比 (ky grade-regress) ===")
    mode = "--live 真实评测（计费）" if result["live"] else "快照回放（零网络、零成本）"
    print(f"  模式    : {mode}")
    cand, base = result["candidate"], result["baseline"]
    print(f"  候选版本: {cand['prompt_version'] or 'latest'}"
          f"  ｜  基线版本: {base['prompt_version'] or 'latest'}")
    print(f"  样本    : {result['samples']} 份  ｜  数据集: {result['cases_path']}")
    print()

    checks = result.get("checks") or {}
    mark = {"fail": "❌ 超阈值", "warn": "⚠ 接近阈值", "ok": "✅ 在阈值内"}
    # (显示名, 指标键, 差值键, 格式器, checks 键)
    rows = (
        ("采分点命中一致率", "hit_jaccard", "hit_jaccard", _pct, "hit_jaccard"),
        ("总分 MAE", "total_mae", "mae", lambda x: f"{x:.2f}", "mae"),
        ("错因一致率", "mistake_agreement", "mistake_type_rate", _pct, "mistake_type_rate"),
    )
    print(f"  {'指标':<9} {'基线':>10} {'候选':>10} {'差值':>11}  判定")
    print("  " + "─" * 60)
    for label, key, dkey, fmt, ckey in rows:
        b, c = float(base[key]), float(cand[key])
        d = float(result["deltas"][dkey])
        d_txt = f"{d * 100:+.2f}pp" if fmt is _pct else f"{d:+.2f}"
        print(f"  {label:<9} {fmt(b):>10} {fmt(c):>10} {d_txt:>11}  "
              f"{mark.get(checks.get(ckey, 'ok'), '?')}")
    print("  " + "─" * 60)

    verdict_txt = {"pass": "✅ pass（未检出显著退化）",
                   "warn": "⚠ warn（接近阈值，建议关注）",
                   "fail": "❌ fail（检出显著退化）"}.get(result["verdict"], result["verdict"])
    print(f"\n  结论: {verdict_txt}")
    for reason in result.get("reasons") or []:
        print(f"        - {reason}")
    th = result["thresholds"]
    print(f"  阈值: MAE 上升 > {th['mae_delta']} 或 命中一致率下降 > {th['hit_jaccard_drop']:.0%}"
          f" 或 错因一致率下降 > {th['mistake_type_drop']:.0%} → fail")
    if result.get("disclaimer"):
        print(f"  口径: {result['disclaimer']}")


def _cmd_grade_regress(args: List[str]) -> None:
    """``ky grade-regress`` 命令处理器（报告先行：默认不阻断）。"""
    opts = _parse_args(args)
    if not opts["prompt"] or not opts["baseline"]:
        print(colorize(
            "用法: ky grade-regress --prompt <候选版本> --baseline <基线版本> "
            "[--live] [--cases PATH] [--json]", C.YELLOW))
        print(_USAGE)
        sys.exit(1)

    try:
        from tools.benchmarks.grading_judge import (
            GradingRegressError, compare_prompt_versions)
    except ImportError:
        from benchmarks.grading_judge import (  # type: ignore
            GradingRegressError, compare_prompt_versions)

    cfg = _load_regress_options()
    llm_config = None
    if opts["live"] and not opts["json"]:
        print(colorize(
            "[计费警告] --live 将真实调用 LLM 评测两个 prompt 版本"
            "（≈21 分钟/30 份 × 2，不写快照文件），会产生费用。", C.YELLOW))
    if opts["live"]:
        llm_config = _load_llm_config()

    progress = None if opts["json"] else (lambda msg: print("  " + msg))
    try:
        result = compare_prompt_versions(
            opts["prompt"], opts["baseline"],
            cases_path=Path(opts["cases"]) if opts["cases"] else None,
            live=opts["live"],
            llm_config=llm_config,
            thresholds=cfg["thresholds"] or None,
            progress=progress,
        )
    except GradingRegressError as e:
        if opts["json"]:
            print(json.dumps({"error": str(e), "verdict": None}, ensure_ascii=False))
        else:
            print(colorize(f"[✘] 无法对比：{e}", C.RED))
        sys.exit(2)

    if opts["json"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _render_comparison(result)
        gate_txt = ("开启（fail 将阻断）" if cfg["regress_gate"]
                    else "关闭（报告先行：默认只报告不阻断）")
        print(f"  门禁: exam_grading.regress_gate = {gate_txt}")

    if cfg["regress_gate"] and result["verdict"] == "fail":
        print(colorize(
            "[✘] 门禁开启（exam_grading.regress_gate=true）且判定 fail → 退出码 2",
            C.RED))
        sys.exit(2)
    sys.exit(0)


register(Command(
    'grade-regress', ("grade-regress", "--grade-regress"),
    '--prompt <cand> --baseline <base> [--live] [--cases PATH] [--json]',
    '判卷 prompt 版本回归对比（三指标快照回放；报告先行，默认不阻断）',
    handler=_cmd_grade_regress,
))
