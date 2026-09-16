# -*- coding: utf-8 -*-
"""研招情报页视图：监控雷达 + 院校侦察入口

内容由 ``services.intel_markdown`` 提供；按钮复用 TUI 的动作分发
（``services.run_action_capture``），不另写一套后端调用。
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget


def build(win) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setSpacing(10)

    win.intel_display = QTextEdit()
    win.intel_display.setReadOnly(True)
    win.intel_display.setObjectName("IntelDisplay")
    layout.addWidget(win.intel_display, stretch=1)

    btn_bar = QHBoxLayout()
    btn_watch = QPushButton("📡 查看监控高校")
    btn_watch.clicked.connect(lambda: win._run_action_to_display("watch", win.intel_display))
    btn_scout = QPushButton("🔍 院校深度侦察")
    btn_scout.clicked.connect(lambda: win._run_action_to_display("scout", win.intel_display))
    btn_bar.addWidget(btn_watch)
    btn_bar.addWidget(btn_scout)
    btn_bar.addStretch()
    layout.addLayout(btn_bar)

    win._refresh_intel_tab()
    return widget


__all__ = ["build"]
