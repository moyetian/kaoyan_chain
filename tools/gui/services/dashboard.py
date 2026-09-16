# -*- coding: utf-8 -*-
"""
GUI 数据服务层（纯数据，不依赖 Qt）

把「读盘 + 组装文本」从 MainWindow 里挪出来，好处有三：
  1. 不再与界面耦合 —— 可以在无图形环境（离屏/CI）下直接单测；
  2. 数据来源统一走 tools/state 共享层，与 CLI / TUI / 看板同源；
  3. MainWindow 只需「取数据 → 塞进控件」，不再同时承担解析逻辑。

本模块只返回数据（字符串 / 数据对象），不创建任何 Qt 控件。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 双导入路径兼容
try:  # pragma: no cover
    import exam_calendar
except ImportError:  # pragma: no cover
    from tools import exam_calendar  # type: ignore

try:  # pragma: no cover
    from state import DashboardState, SubjectProgress, load_dashboard_state
except ImportError:  # pragma: no cover
    from tools.state import (  # type: ignore
        DashboardState, SubjectProgress, load_dashboard_state,
    )

_LOG = logging.getLogger(__name__)

#: 科目目录（GUI 侧展示顺序）
SUBJECT_DIRS: Tuple[Tuple[str, str], ...] = (
    ("01-数学", "数学"),
    ("02-英语", "英语"),
    ("03-思想政治理论", "政治"),
    ("04-专业课", "专业课"),
)


def load_state(workspace_root: Path) -> Optional[DashboardState]:
    """读取共享状态；失败返回 None（界面按空态渲染，不崩）。"""
    try:
        return load_dashboard_state(workspace_root)
    except Exception as exc:
        _LOG.warning("看板状态加载失败: %s -> %s", workspace_root, exc)
        return None


def countdown_days(workspace_root: Path) -> int:
    """距初试剩余天数。

    [根因修复·日期硬编码] 旧实现兜底初试日写死 "2026-12-19"、异常返回魔法数
    103，与 TUI（104）、CLI（动态推算）三端互不一致且过期后永久失效。
    现统一委托 exam_calendar（配置 → 入学年 → 日历推算）。
    """
    state = load_state(workspace_root)
    if state is not None:
        return state.days_left
    return exam_calendar.countdown_days(None)


def subject_progress(workspace_root: Path) -> Tuple[SubjectProgress, ...]:
    """各科今日任务进度（用于进度条）。"""
    state = load_state(workspace_root)
    return state.subjects if state else ()


def subject_labels(workspace_root: Path) -> List[Tuple[str, str, str]]:
    """返回 ``[(key, 目录名, 显示名), ...]``，显示名取自配置（自命题科目可自定义）。"""
    state = load_state(workspace_root)
    if state is None:
        return [(k, folder, folder) for folder, k in
                (("01-数学", "math"), ("02-英语", "eng"),
                 ("03-思想政治理论", "pol"), ("04-专业课", "pro"))]
    folder_of = {k: f for k, f in (("math", "01-数学"), ("eng", "02-英语"),
                                   ("pol", "03-思想政治理论"), ("pro", "04-专业课"))}
    return [(s.key, folder_of.get(s.key, s.folder), s.label) for s in state.subjects]


# ── 错题复测队列 ────────────────────────────────────────────────

def error_queue_markdown(workspace_root: Path) -> str:
    """扫描各科错题本，生成「待复测队列」Markdown 文本。"""
    lines = [
        "# FSRS 记忆稳定性曲线 · 到期错题复测队列",
        f"> 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
    ]
    total_due = 0
    for folder, name in SUBJECT_DIRS:
        mistake_dir = workspace_root / folder / "错题本"
        if mistake_dir.exists():
            due_files = [f for f in mistake_dir.glob("*.md")
                         if not f.stem.startswith("自测卷_") and not f.stem.startswith("_")]
            lines.append(f"### {name}错题本: 共 {len(due_files)} 道错题档案")
            total_due += len(due_files)
            for f in due_files[:3]:
                lines.append(f"- `{f.stem}`")
            if len(due_files) > 3:
                lines.append(f"- *(其余 {len(due_files) - 3} 道已归档)*")
        else:
            lines.append(f"### {name}错题本: 暂无到期错题")
        lines.append("")
    lines.append(f"**全科待攻坚错题总数**: `{total_due}` 道")
    return "\n".join(lines)


# ── 研招监控情报 ────────────────────────────────────────────────

def intel_markdown(workspace_root: Path) -> str:
    """读取监控高校清单，生成「研招动态」Markdown 文本。"""
    lines = [
        "# 研招招考动态与高校监控雷达",
        f"> 数据基准: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
    ]
    watch_file = workspace_root / ".memory" / "admission_watch.json"
    if not watch_file.exists():
        lines.append("暂未配置监控高校，点击下方按钮或在 TUI 中输入 8 即可纳入监控。")
        return "\n".join(lines)

    try:
        data = json.loads(watch_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _LOG.warning("监控数据解析失败: %s -> %s", watch_file, exc)
        lines.append("监控数据损坏，已跳过展示。")
        return "\n".join(lines)

    if not isinstance(data, dict) or not data:
        lines.append("暂未配置监控高校，点击下方按钮或在 TUI 中输入 8 即可纳入监控。")
        return "\n".join(lines)

    lines.append(f"### 正在动态监控的高校 ({len(data)} 所):")
    for code, it in data.items():
        if not isinstance(it, dict):
            continue
        lines.append(f"- **{it.get('name')}** (`{code}`) | 上次核验: `{it.get('last_check', '-')}`")
        titles = it.get("recent_titles", []) or []
        if titles:
            lines.append(f"  - 最新通知: *{titles[0]}*")
    return "\n".join(lines)


# ── 头部信息 ────────────────────────────────────────────────────

def header_info(workspace_root: Path) -> Dict[str, Any]:
    """顶栏所需字段：倒计时 / 目标院校 / 专业 / 风格 / 阶段 / 每日时长。"""
    state = load_state(workspace_root)
    if state is None:
        return {"days_left": exam_calendar.countdown_days(None), "school": "目标院校",
                "major": "报考专业", "style": "严格把关·保姆提分型",
                "style_short": "严格把关", "stage": "", "daily_hours": 8.5}
    return {
        "days_left": state.days_left,
        "school": state.school,
        "major": state.major,
        "style": state.style,
        "style_short": state.style_short,
        "stage": state.stage,
        "daily_hours": state.daily_hours,
        "exam_date": state.exam_date,
    }


__all__ = [
    "SUBJECT_DIRS",
    "countdown_days",
    "error_queue_markdown",
    "header_info",
    "intel_markdown",
    "load_state",
    "subject_labels",
    "subject_progress",
]
