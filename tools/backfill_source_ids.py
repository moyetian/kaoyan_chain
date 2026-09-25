# -*- coding: utf-8 -*-
"""存量题卡题源 ID 补录（C3 题源溯源 · 存量迁移入口）。

背景（升级规划 C3）：题源身份 ``source_id`` 自本批次起由渲染侧
（``material_ingestion``）自动写入新题卡；但**存量**题卡（既有错题记录、
已导入的题库切片）没有 ID 行。组卷侧对「声明了身份却校验不过」的卡片会
按 ``source_tampered`` 排除 —— 存量卡片虽可走惰性认证（现场构建），
但落盘的显式身份才能支撑跨文件比对与人工核对。本脚本对存量执行一次性
补录，**可安全重复执行**。

用法::

    python tools/backfill_source_ids.py --dry-run        # 预演：只报告，不落盘
    python tools/backfill_source_ids.py                  # 执行补录
    python tools/backfill_source_ids.py --json           # 机器可读报告
    python tools/backfill_source_ids.py --root D:/测试/考研学习chain

退出码：``0`` = 处理完成；``1`` = 有文件因读取失败被跳过（不静默失败）。

安全性：补录是**纯派生**操作 —— ``source_id`` / ``checksum`` 都是题干的
函数（去空白后 sha256 摘要），不改动题干本身；重复执行不产生任何差异
（幂等由 ``question_source.backfill_markdown_text`` 保证）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
for _p in (str(ROOT), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # 脚本式路径（tools/ 已在 sys.path）
    from skills.question_source import backfill_workspace  # noqa: E402
except ImportError:  # pragma: no cover - 包式导入
    from tools.skills.question_source import backfill_workspace  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="存量题卡题源 ID 补录（C3：幂等、可预演）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只报告将补录的卡片，不写入任何文件")
    parser.add_argument("--root", type=str, default="",
                        help="工作区根目录（默认本仓库根；亦可用 KY_WORKSPACE_ROOT 环境变量）")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 输出报告（便于脚本 / CI 消费）")
    args = parser.parse_args(argv)

    report = backfill_workspace(Path(args.root) if args.root else None,
                                dry_run=args.dry_run)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"题源 ID 补录 · {'预演（未写入）' if report['dry_run'] else '已写入'}")
        if not report["files"]:
            print("  无需补录：没有发现缺少题源 ID 的存量题卡。")
        for item in report["files"]:
            print(f"  + {item['cards']:>3} 张 [{item['kind']}] {item['file']}")
        if report["files"]:
            print(f"  合计补录 {report['cards']} 张题卡。")
        for item in report["skipped"]:
            print(f"  ! 跳过 {item['file']}（{item['reason']}）")
        if report["dry_run"] and report["cards"]:
            print("  提示：这是预演，未写入；去掉 --dry-run 才会落盘。")

    return 1 if report["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
