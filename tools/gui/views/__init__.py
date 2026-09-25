# -*- coding: utf-8 -*-
"""
GUI 视图层（每个页签/区域一个模块）

拆分原则（对应 agent.md 的「单一职责 + 领域物理隔离」）：
  * ``views/``     只负责**构建控件与连线**（本包）
  * ``services/``  只负责**取数据与调后端**（无 Qt 控件）
  * ``theme_apply`` 只负责**主题与偏好**
  * ``main_window`` 只做**组装与事件分发**

每个模块统一暴露 ``build(win) -> QWidget``，把创建出来的控件挂到窗口实例上
（Qt 惯用的共享状态持有方式），窗口方法名保持不变，
因此既有对外契约（``_feature_buttons`` / ``tab_widget`` / ``countdown_label`` …）零破坏。
"""

from __future__ import annotations

from . import (
    chat_tab,
    error_tab,
    function_cards,
    header,
    intel_tab,
    nav_rail,
    task_tab,
)

__all__ = [
    "chat_tab",
    "error_tab",
    "function_cards",
    "header",
    "intel_tab",
    "nav_rail",
    "task_tab",
]
