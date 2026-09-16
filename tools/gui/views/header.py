# -*- coding: utf-8 -*-
"""顶栏视图：标题 / 目标院校与风格 / 主题切换按钮 / 初试倒计时

[缺陷修复·死数字] 倒计时改造前是构造函数的局部变量（`countdown = QLabel(...)`），
只在启动时算一次，挂一整天不动、跨天也不变。现在它挂在窗口实例上
（``win.countdown_label``），由定时器经 ``win._sync_header_text()`` 刷新。
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


def build(win) -> QFrame:
    header = QFrame()
    header.setObjectName("HeaderBar")
    header.setFixedHeight(75)
    layout = QHBoxLayout(header)

    title = QLabel("🎯 考研学习链")
    title.setObjectName("HeaderTitle")

    win.countdown_label = QLabel("")
    win.countdown_label.setObjectName("Countdown")

    win.meta_label = QLabel("")
    win.meta_label.setObjectName("MetaLabel")

    win.theme_btn = QPushButton("")
    win.theme_btn.setObjectName("ThemeToggle")
    win.theme_btn.setFixedWidth(84)
    win.theme_btn.clicked.connect(win._toggle_theme)

    layout.addWidget(title)
    layout.addSpacing(16)
    layout.addWidget(win.meta_label)
    layout.addStretch()
    layout.addWidget(win.theme_btn)
    layout.addSpacing(12)
    layout.addWidget(win.countdown_label)

    win._sync_header_text()
    return header


__all__ = ["build"]
