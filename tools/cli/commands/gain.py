# -*- coding: utf-8 -*-
"""
学习增益报告命令模块 (gain.py)
包含 gain —— C6「学习增益代理指标」的用户可达入口

[为什么需要这个入口] ``tools/benchmarks/learning_gain.py`` 会读工作区内三类
既有数据（复测日志 / 错题本 / 打卡历史）生成周趋势报告，但改造前**没有任何
用户可达入口**：考生只能自己去跑 Python 脚本，也就没人会去看「我到底有没有
进步」的证据。本模块把它接到 ``ky gain`` 与 REPL ``/gain``，报告落盘在
``.memory/``（本地、不上传、非门禁）。
"""

import sys
from typing import List

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.repl.renderer import C, colorize, print_learning_gain
except ImportError:
    from cli.dispatch import Command, register
    from cli.repl.renderer import C, colorize, print_learning_gain

_USAGE = """
学习增益代理指标 (ky gain) —— 周趋势报告，本地落盘、不上传、非门禁
用法：
  ky gain [--no-save]
说明：
  从工作区内三类既有数据聚合周趋势（不联网、不调 LLM）：
    1) 错题复测通过率 —— .memory/review_log.jsonl（FSRS 复测回写）
    2) 同类错因复发   —— 各科错题本「错因分类」行（跨 ≥2 天出现视为复发）
    3) 计划完成率     —— ky_config.json 的 completion_history（打卡记录）
  默认把报告写入 .memory/learning_gain_report.md 并打印；--no-save 只打印。
  严格只读模式 (--permission=safe) 下自动不落盘（不是错误）。
  某条指标数据不足时如实提示「不可评估」，而不是编造趋势。
退出码：
  0 = 至少一条指标有数据；2 = 全部指标数据不足（如实不可评估）。
"""


def run_gain_report(save: bool = True) -> int:
    """生成并渲染学习增益报告（``ky gain`` 与 REPL ``/gain`` 共用）。

    Returns:
        0 = 至少一条指标有数据；2 = 全部指标数据不足（原样透传，理由见
        :func:`_cmd_gain`）。
    """
    try:
        from tools.benchmarks.learning_gain import run_learning_gain
    except ImportError:
        from benchmarks.learning_gain import run_learning_gain  # type: ignore

    report, code = run_learning_gain(save=save)
    print_learning_gain(report)
    return code


def _cmd_gain(args: List[str]) -> None:
    """``ky gain [--no-save]`` 命令处理器。"""
    if "--help" in args or "-h" in args:
        print(colorize(_USAGE, C.YELLOW))
        sys.exit(0)

    save = True
    for a in args[1:]:
        if a == "--no-save":
            save = False
        elif a.startswith("-"):
            print(colorize(f"[!] 未知参数: {a}（用法: ky gain [--no-save]）", C.RED))
            sys.exit(1)

    code = run_gain_report(save=save)
    # [与 search.py 的差异] search.py 把非零退出码统一压成 1；这里**原样传递**
    # （0/2）：2 是本模块「全部指标数据不足、如实不可评估」的明确语义，与
    # 「运行出错」不是一回事，压成 1 会让脚本调用方丢失这一信息。
    sys.exit(code)


register(Command(
    'gain', ("gain", "--gain"),
    '[--no-save]',
    '学习增益代理指标（复测通过率/错因复发/计划完成率周趋势；本地落盘，不上传）',
    handler=_cmd_gain,
))
