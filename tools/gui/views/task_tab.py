# -*- coding: utf-8 -*-
"""今日任务页视图：概览统计块 + 四科进度行 + 刷新按钮

[P2 修复·科目名截断] 改造前科目标签写死 ``label.setFixedWidth(200)``，
自命题长科目名（如「自命题专业课科目」）
在 200px 处被硬切且没有省略号、也没有 tooltip。现改为**自适应宽度**：
标签按自身文字宽度占位（进度条保留最小宽度，避免极端长名下被挤没），
完整名称同时进 tooltip。

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
    from gui.widgets.ky_card import KYStatTile
except ImportError:  # pragma: no cover
    from tools.gui import services  # type: ignore
    from tools.gui.widgets.ky_card import KYStatTile  # type: ignore

#: 概览统计块的 key → (caption, 初值)
STAT_TILES = (
    ("progress", "今日四科平均完成度", "0%"),
    ("due", "FSRS 待复测错题", "0"),
    ("countdown", "距初试天数", "—"),
)


def build(win) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setSpacing(14)
    layout.setContentsMargins(14, 14, 14, 14)

    # ── 概览统计块（大数字 + caption） ──────────────────────
    win.task_stat_tiles = {}
    stat_row = QHBoxLayout()
    stat_row.setSpacing(12)
    for key, caption, initial in STAT_TILES:
        tile = KYStatTile(caption=caption, value=initial)
        win.task_stat_tiles[key] = tile
        stat_row.addWidget(tile, stretch=1)
    layout.addLayout(stat_row)

    win.task_progress_bars = {}
    win.task_count_labels = {}

    for key, _folder, label_text in services.subject_labels(win.workspace_root):
        frame = QFrame()
        frame.setObjectName("TaskRow")
        h = QHBoxLayout(frame)

        label = QLabel(label_text)
        label.setObjectName("TaskLabel")
        # [P2 修复] 不再 setFixedWidth(200)：按文字宽度自适应，完整名称进 tooltip
        label.setToolTip(label_text)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.setFixedHeight(18)
        progress.setMinimumWidth(120)          # 长科目名下进度条不被挤没

        pct_label = QLabel("0/0 (0%)")
        pct_label.setObjectName("TaskPct")
        pct_label.setMinimumWidth(90)
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


__all__ = ["STAT_TILES", "build"]
