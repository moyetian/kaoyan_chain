# -*- coding: utf-8 -*-
"""
考研学习链 · 四端共享状态层 (Shared UI State)

对外只暴露这里。CLI / TUI / GUI / Web 看板构建器一律从本包取状态，
不再各自扫盘解析 —— 这是「三端不一致」类缺陷的结构性解法：
把「同一份本地数据怎么读」收敛成一处实现，而不是靠人去记得同步改三遍。

用法::

    from state import load_dashboard_state
    st = load_dashboard_state()               # 默认本仓库根
    st.days_left, st.total, st.completed      # 倒计时 / 任务总数 / 已完成
    st.by_key("math").summary_text            # "2/3 (67%)"

包式导入同样可用：``from tools.state import load_dashboard_state``。
"""

from __future__ import annotations

from .dashboard_state import (
    SUBJECT_ORDER,
    DashboardState,
    SubjectProgress,
    SubjectSpec,
    load_config,
    load_dashboard_state,
    read_subject_tasks,
    resolve_daily_hours,
    resolve_school_major,
    resolve_stage,
    resolve_style,
    resolve_subject_specs,
    state_to_cli_dict,
)
from .task_parser import TaskItem, is_separator_row, parse_task_lines, pct, split_row

__all__ = [
    # 数据模型
    "DashboardState",
    "SubjectProgress",
    "SubjectSpec",
    "TaskItem",
    "SUBJECT_ORDER",
    # 加载与解析
    "load_dashboard_state",
    "load_config",
    "read_subject_tasks",
    "parse_task_lines",
    "split_row",
    "is_separator_row",
    "pct",
    # 字段解析（各端按需单独取用）
    "resolve_subject_specs",
    "resolve_school_major",
    "resolve_style",
    "resolve_daily_hours",
    "resolve_stage",
    # 兼容旧结构
    "state_to_cli_dict",
]
