# -*- coding: utf-8 -*-
"""错题本页视图：FSRS 待复测队列 + 一键组卷

内容由 ``services.error_queue_markdown`` 提供（纯数据、可离屏单测）。
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget


def build(win) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setSpacing(10)

    win.error_info = QTextEdit()
    win.error_info.setReadOnly(True)
    win.error_info.setObjectName("ErrorDisplay")
    layout.addWidget(win.error_info, stretch=1)

    btn_bar = QHBoxLayout()
    quiz_btn = QPushButton("一键组装错题盲盒自测卷")
    quiz_btn.clicked.connect(win._generate_error_quiz)
    refresh_err_btn = QPushButton("刷新待复测队列")
    refresh_err_btn.clicked.connect(win._refresh_error_tab)
    btn_bar.addWidget(quiz_btn)
    btn_bar.addWidget(refresh_err_btn)
    btn_bar.addStretch()
    layout.addLayout(btn_bar)

    win._refresh_error_tab()
    return widget


__all__ = ["build"]
