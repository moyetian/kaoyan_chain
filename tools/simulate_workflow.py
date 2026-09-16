# -*- coding: utf-8 -*-
"""
CLI 全流程回归冒烟脚本（**开发工具，不属于运行时功能**）

依次执行一批 ``ky`` 子命令，收集返回码与输出，落盘为 ``simulation_cli.json``，
用于人工核对"三端一致 / 参数透传 / 降级行为"。

用法：``python tools/simulate_workflow.py``

.. note::
   本模块**不产生任何导入副作用**（历史版本在 import 时即执行全部子进程并写文件），
   且不再硬编码解释器路径，改用 ``sys.executable``，保证跨机可运行。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COMMANDS = [
    "status",
    "today --json",
    "style",
    "doctor",
    "memory status",
    "map pro --json",
    "review",
    "fatigue",
    "subject",
    "fetch info",
    "admission 湖南大学 电子信息 --year=2027",
    "compare 湖南大学 长沙理工大学 电子信息",
    "watch 湖南大学 --list",
    "scout 湖南大学 电子信息",
    "diff",
]


def main() -> int:
    rows = []
    for cmd in COMMANDS:
        try:
            proc = subprocess.run(
                [PY, os.path.join(ROOT, "tools", "ky_cli.py"), *cmd.split()],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=8,
                encoding="utf-8",
                errors="replace",
            )
            rows.append({
                "command": f"ky {cmd}",
                "status": proc.returncode,
                "stdout": proc.stdout[-4000:],
                "stderr": proc.stderr[-1000:],
                "deviation": "记录实际输出并与预期比对",
                "cause": "待根据输出判定",
            })
        except Exception as e:  # 超时/启动失败：如实记录，绝不伪造成功
            rows.append({
                "command": f"ky {cmd}",
                "status": "error",
                "stdout": "",
                "stderr": str(e),
                "deviation": "命令未在时限内完成",
                "cause": "交互方式限制或外部数据不可用",
            })

    out_path = os.path.join(ROOT, "simulation_cli.json")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"completed {len(rows)} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
