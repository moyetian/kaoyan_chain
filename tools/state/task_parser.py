# -*- coding: utf-8 -*-
"""
今日任务 Markdown 解析器 (纯函数 · 无 I/O)

[缺陷修复·三端解析不一致] 「今日任务.md → 勾选进度」这套解析在修复前被写了三遍：
  - tools/ky_cli.py:1389   get_today_tasks_data()
  - tools/tui_navigator.py:269 get_today_progress()
  - tools/gui/main_window.py:375 _load_today_task_progress()
三份实现的关键差异（实测）：

  | 维度       | CLI / TUI                      | GUI                                  |
  |-----------|--------------------------------|--------------------------------------|
  | 编码回退   | CLI 有 utf-8/utf-8-sig/gbk      | 仅 utf-8，失败静默吞                 |
  | 分隔行判定 | `replace(" ","").startswith`    | **只 startswith("\\|---\\|")**       |
  | 根目录     | 硬编码 ROOT                     | 支持工作区副本                       |

GUI 那条差异是真实缺陷：手写成 `| --- |`（带空格）的表头分隔行会被当成数据行，
任务总数虚高一，进度百分比随之偏低。本模块用「整行单元格是否全为短横线」来判定分隔行，
从结构上消除这类写法差异，而不是再去修字符串前缀匹配。

本模块只做「文本 → 数据」，不碰文件系统，便于单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

#: 列表式任务行：`- [ ] xxx` / `- [x] xxx` / `- [X] xxx`
_LIST_ROW = re.compile(r"^-\s*\[([ xX])\]\s*(.*)$")
#: 表格分隔行单元格：`---` / `:---` / `---:` / `:---:`
_SEP_CELL = re.compile(r"^:?-{2,}:?$")
#: 表头行特征词（与三端既有行为保持一致：命中即跳过，不当作任务）
_HEADER_MARKERS = ("完成状态", "模块")
#: 任务完成标记
_DONE_MARK = "[x]"


@dataclass(frozen=True)
class TaskItem:
    """一条今日任务。"""

    module: str          # 模块名（表格第 1 列；列表式固定为「任务」）
    content: str         # 任务内容
    duration: str        # 预计用时（列表式为空串）
    done: bool           # 是否已勾选完成


def split_row(line: str) -> Optional[List[str]]:
    """把 Markdown 表格行拆成非空单元格列表；非表格行返回 None。

    与三端既有实现一致：按 ``|`` 切分后**丢弃空单元格**，因此首尾竖线可省。
    """
    if "|" not in line:
        return None
    cells = [c.strip() for c in line.split("|") if c.strip()]
    return cells or None


def is_separator_row(cells: Sequence[str]) -> bool:
    """判断是否表格分隔行（``|---|---|`` / ``| --- |`` / ``|:--:|`` 均可识别）。

    [缺陷修复] 旧实现用字符串前缀 ``"|---|"`` 匹配，`| --- |`（带空格）会被漏判，
    进而被当成一条任务计入总数。此处改为逐单元格判定，与书写风格无关。
    """
    return bool(cells) and all(_SEP_CELL.match(c) for c in cells)


def parse_task_lines(text: str) -> Tuple[TaskItem, ...]:
    """解析一份「今日任务.md」正文，返回全部任务条目（保持文件顺序）。

    同时兼容两种书写风格：
      * 列表式：``- [ ] 精读真题阅读``
      * 表格式：``| 模块 | 任务内容 | 预计用时 | 完成状态 |``

    表格行需至少 3 个非空单元格，且末列含 ``[x]`` 视为已完成。
    """
    if not text:
        return ()

    items: List[TaskItem] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 1) 列表式
        m = _LIST_ROW.match(line)
        if m:
            mark, desc = m.group(1), m.group(2).strip()
            items.append(TaskItem(
                module="任务",
                content=desc,
                duration="",
                done=mark.lower() == "x",
            ))
            continue

        # 2) 表格式
        if "|" not in line:
            continue
        cells = split_row(line)
        if not cells or len(cells) < 3:
            continue
        if is_separator_row(cells):
            continue
        if any(marker in line for marker in _HEADER_MARKERS):
            continue

        items.append(TaskItem(
            module=cells[0],
            content=cells[1],
            duration=cells[2] if len(cells) > 2 else "",
            done=_DONE_MARK in cells[-1].lower(),
        ))

    return tuple(items)


def pct(done: int, total: int) -> int:
    """完成百分比（整数，0~100）；总数为 0 时返回 0（避免除零）。"""
    return int(done / total * 100) if total > 0 else 0
