# -*- coding: utf-8 -*-
"""输出预算档位命令模块 (budget.py)

包含 ``ky budget`` —— 查看/切换**输出预算档位**（速览 / 标准 / 深入）。

[为什么必须有这个入口] R4 真机对照实测：耗时 ≈ f(输出量)（3979 字→65.9s、
767 字→34.4s、237 字→18.3s），而软性字数约束无效（prompt 要求「不少于 1200 字」
→ 实测输出 3979 字，**超标 3.3 倍**）。改用「固定节 + 固定条数」的结构化约束后，
字符 CV 降到 4.89%~9.38%、耗时 CV 13.45%~14.66%（均 ≤15% 达标）。

于是输出长度成了**可调的产品参数**。但硬上界一旦触达，回答会被截断——
截断标注里写着「可切换到更深的档位后重问」，若没有切换入口，那句话就是空头支票。
本命令就是那个入口。

[与显式 max_tokens 的关系] 两者都有效，``ky_config.json`` 里显式写
``max_tokens`` 仍然优先（向后兼容：老配置行为不变）；本命令写的是 ``output_budget``，
二者冲突时显式 max_tokens 胜出（见 ``output_budget.max_tokens_for``）。
"""
from __future__ import annotations

import sys
from typing import List

try:  # 源码脚本式（``py tools/ky_cli.py``）
    from tools.cli.dispatch import Command, register
    from tools.cli.repl.renderer import C, colorize
    from tools.cli import shared as cli_shared
    from tools import output_budget
except ImportError:  # pragma: no cover - 包式导入
    from cli.dispatch import Command, register  # type: ignore
    from cli.repl.renderer import C, colorize  # type: ignore
    from cli import shared as cli_shared  # type: ignore
    from tools import output_budget  # type: ignore

_USAGE = """
输出预算档位 (ky budget) —— 控制私教回答的长度档位
用法：
  ky budget                查看当前档位与各档对照
  ky budget brief          切到「速览」档（约 300 字，查概念/确认方向）
  ky budget standard       切到「标准」档（约 800 字，日常讲题/错题复盘，默认）
  ky budget deep           切到「深入」档（约 2000 字，难点突破/模拟考详解）
说明：
  * 档位决定回答的**长度上界**（max_tokens）与**结构骨架**（每节几条）；
  * 回答触达上界时会被**显式标注截断**，不会静默丢内容；
  * 耗时随输出量增长：速览档最快，深入档真机实测 60s+；
  * 显式配置了 max_tokens 的工作区以显式值为准，本命令不覆盖它。
退出码：
  0 = 成功（查看或切换）；1 = 档位名非法。
"""


def _cmd_budget(args: List[str]) -> None:
    """``ky budget [档位]``：无参查看，带参切换。"""
    rest = [a for a in (args or [])[1:] if not str(a).startswith("-")]
    cfg = cli_shared.load_config()

    if not rest:
        print("=== 输出预算档位 ===")
        print(output_budget.describe(cfg))
        print()
        print("用法：ky budget brief | standard | deep")
        return

    level = str(rest[0]).strip().lower()
    if not output_budget.is_valid_level(level):
        print(colorize(
            f"[✘] 未知档位「{rest[0]}」；可选："
            f"{' / '.join(output_budget.available_levels())}", C.RED))
        sys.exit(1)

    if not output_budget.set_level(cfg, level):
        # 理论不可达（上面已校验），仍如实失败而不是静默返回成功
        print(colorize(f"[✘] 切换到「{level}」失败（配置未写入）", C.RED))
        sys.exit(1)
    cli_shared.save_config(cfg)
    info = output_budget.BUDGET_LEVELS[level]
    print(colorize(
        f"[√] 输出预算已切到「{info['label']}」档："
        f"上界 {info['max_tokens']} tokens（约 {info['target_chars']} 字）", C.GREEN))
    print(f"     {info['hint']}")


register(Command(
    'budget', ("budget", "--budget"),
    '[brief|standard|deep]',
    '输出预算档位（查看/切换回答长度：速览/标准/深入）',
    help=_USAGE,
    handler=_cmd_budget,
    write=True,
))
