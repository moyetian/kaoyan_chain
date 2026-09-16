# -*- coding: utf-8 -*-
"""
考研学习链 · 终端交互中枢（TUI）

对外只暴露这里：
  * ``terminal``  终端能力探测与排版工具（宽度感知 / 颜色开关 / Windows VT 启用）
  * ``app``       textual 版界面（键鼠双控、无闪烁重绘）

``tools/tui_navigator.py`` 仍是动作分发与纯文本渲染的实现处（``execute_action``
是 TUI 与 GUI 共用的单一实现），并作为兼容门面重新导出
``render_header`` / ``render_menu`` / ``execute_action`` / ``run_tui_loop``，
因此既有调用点（ky_cli、scripts、测试）零改动。

用哪种界面由 ``run_tui_loop()`` 自动决定：
    textual 可用 且 处于真实终端 且 未设 KY_TUI_LEGACY  →  textual 界面
    否则                                              →  原纯文本循环（降级）
"""

from __future__ import annotations

from .terminal import (
    center_display,
    clear_screen,
    colors_disabled,
    display_width,
    enable_windows_vt,
    is_tty,
    pad_display,
    panel_width,
    supports_textual,
    terminal_columns,
)

__all__ = [
    "center_display",
    "clear_screen",
    "colors_disabled",
    "display_width",
    "enable_windows_vt",
    "is_tty",
    "pad_display",
    "panel_width",
    "supports_textual",
    "terminal_columns",
]
