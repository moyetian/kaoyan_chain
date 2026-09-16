# -*- coding: utf-8 -*-
"""今日任务页视图：四科进度行 + 刷新按钮

科目显示名来自共享状态层（自命题科目名不再被硬编码成「专业课」）。
任务行改造前带内联样式（``background: #1a1e2e``），浅色主题下会留下深底，
现统一走 ``#TaskRow`` / ``#TaskLabel`` / ``#TaskPct`` 选择器。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

try:  # pragma: no cover - 取决于运行方式
    from gui import services
except ImportError:  # pragma: no cover
    from tools.gui import services  # type: ignore


def build(win) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setSpacing(12)

    win.task_progress_bars = {}
    win.task_count_labels = {}

    for key, _folder, label_text in services.subject_labels(win.workspace_root):
        frame = QFrame()
        frame.setObjectName("TaskRow")
        h = QHBoxLayout(frame)

        label = QLabel(label_text)
        label.setObjectName("TaskLabel")
        label.setFixedWidth(200)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setFixedHeight(18)

        pct_label = QLabel("0/0 (0%)")
        pct_label.setObjectName("TaskPct")
        pct_label.setFixedWidth(90)
        pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        h.addWidget(label)
        h.addWidget(progress, stretch=1)
        h.addWidget(pct_label)
        layout.addWidget(frame)

        win.task_progress_bars[key] = progress
        win.task_count_labels[key] = pct_label

    refresh_btn = QPushButton("刷新今日进度")
    refresh_btn.setMaximumWidth(160)
    refresh_btn.clicked.connect(win._load_today_task_progress)
    layout.addWidget(refresh_btn)
    layout.addStretch()
    return widget


__all__ = ["build"]
