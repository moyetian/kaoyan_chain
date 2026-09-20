# -*- coding: utf-8 -*-
"""研报落盘路径单一真源。

[收尾修复·落盘文件名不统一] `ky scout`（skills/school_scout.py）与
`ky admission`（intelligence/scout_engine.py）此前各写各的文件名拼接，
TUI 与 CLI 传入的专业串稍有不同（"010101 马克思主义哲学" vs "马克思主义哲学"）
就会落成两个文件，且与 admission 落盘相撞后互相覆盖。现收敛到同一函数：
目录 + 前缀 + 归一化（去首尾空白、压平内部空白、过滤文件名非法字符）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

SCOUT_REPORT_PREFIX = "目标院校情报"
SCOUT_REPORT_DIRNAME = "04-专业课"


def _normalize_part(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return re.sub(r'[\\/:*?"<>|]+', "_", cleaned)


def scout_report_filename(school: str, major: Optional[str] = "") -> str:
    """返回 `目标院校情报_<学校>[_<专业>].md`（归一化后）。"""
    school_part = _normalize_part(school) or "未知院校"
    major_part = _normalize_part(major)
    if major_part:
        return f"{SCOUT_REPORT_PREFIX}_{school_part}_{major_part}.md"
    return f"{SCOUT_REPORT_PREFIX}_{school_part}.md"


def scout_report_path(root: Union[str, Path], school: str,
                      major: Optional[str] = "") -> Path:
    """返回落盘完整路径（` root/04-专业课/目标院校情报_....md`）。"""
    return Path(root) / SCOUT_REPORT_DIRNAME / scout_report_filename(school, major)
