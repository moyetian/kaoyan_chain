# -*- coding: utf-8 -*-
"""错题本页视图：FSRS 待复测队列**卡片列表** + 一键组卷 + 原始档案折叠视图

[P2 改造] 改造前整页是一个 QTextEdit 直出 Markdown 的文本墙，看不出「哪一科、
哪一道、什么时候到期」。现改为卡片列表（科目 chip / 错因 chip / 到期日 / 题干摘要），
数据来自 ``services.error_queue_cards``（只读解析，与 CLI/TUI 同源的错题本文件）。

[契约] ``win.error_info`` 仍是那个 QTextEdit（``_generate_error_quiz`` 会往里追加
自测卷文本，既有测试断言 ``toPlainText()`` 含「错题」），只是默认折叠为
「原始档案」视图 —— 卡片与原文两种看法都在，不丢任何既有能力。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

try:  # pragma: no cover - 取决于运行方式
    from gui import services
    from gui.widgets.ky_card import KYCard
except ImportError:  # pragma: no cover
    from tools.gui import services  # type: ignore
    from tools.gui.widgets.ky_card import KYCard  # type: ignore


def _chip(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    return label


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def render_error_cards(win) -> list:
    """按最新数据重建错题卡片列表；返回卡片控件列表（供测试与自检）。"""
    layout = win.error_cards_layout
    if layout is None:
        return []
    _clear_layout(layout)

    records = services.error_queue_cards(win.workspace_root)
    cards = []
    for rec in records:
        card = KYCard()
        title = rec.get("question") or rec.get("title") or "（未命名错题）"
        due = rec.get("next_due") or ""
        card.set_header(title, meta=(f"到期 {due}" if due else rec.get("status") or "待复测"))

        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addWidget(_chip(rec.get("subject_name") or "错题", "CardChip"))
        if rec.get("error_type"):
            chips.addWidget(_chip(rec["error_type"], "ErrChip"))
        if due:
            chips.addWidget(_chip(f"复测 {due}", "DueChip"))
        if rec.get("date"):
            chips.addWidget(_chip(f"记录 {rec['date']}", "DueChip"))
        chips.addStretch(1)
        card.body.addLayout(chips)
        cards.append(card)
        layout.addWidget(card)

    if not cards:
        empty = QLabel("暂无待复测错题 —— 交作业后错题会自动排入 FSRS 复测队列。")
        empty.setObjectName("EmptyHint")
        empty.setWordWrap(True)
        layout.addWidget(empty)

    layout.addStretch(1)
    win.error_cards = cards
    if getattr(win, "error_count_label", None) is not None:
        win.error_count_label.setText(
            f"待复测错题 {len(cards)} 道 · 按到期日由近及远" if cards else "待复测错题 0 道")
    return cards


def build(win) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setSpacing(12)
    layout.setContentsMargins(12, 12, 12, 12)

    # ── 卡片列表（主视图） ─────────────────────────────────
    win.error_count_label = QLabel("")
    win.error_count_label.setObjectName("KYCardMeta")
    layout.addWidget(win.error_count_label)

    scroll = QScrollArea()
    scroll.setObjectName("ErrorScroll")
    scroll.setWidgetResizable(True)
    canvas = QWidget()
    canvas.setObjectName("ErrorCanvas")
    win.error_cards_layout = QVBoxLayout(canvas)
    win.error_cards_layout.setContentsMargins(2, 2, 2, 2)
    win.error_cards_layout.setSpacing(8)
    scroll.setWidget(canvas)
    win.error_cards_container = canvas
    win.error_cards = []
    layout.addWidget(scroll, stretch=1)

    # ── 原始档案（折叠；既有契约控件，保留全部能力） ────────
    win.error_info = QTextEdit()
    win.error_info.setReadOnly(True)
    win.error_info.setObjectName("ErrorDisplay")
    win.error_info.setVisible(False)
    layout.addWidget(win.error_info, stretch=1)

    btn_bar = QHBoxLayout()
    btn_bar.setSpacing(10)
    quiz_btn = QPushButton("一键组装错题盲盒自测卷")
    quiz_btn.clicked.connect(win._generate_error_quiz)
    refresh_err_btn = QPushButton("刷新待复测队列")
    refresh_err_btn.setObjectName("SecondaryBtn")
    refresh_err_btn.clicked.connect(win._refresh_error_tab)
    raw_btn = QPushButton("原始档案")
    raw_btn.setObjectName("SecondaryBtn")
    raw_btn.setCheckable(True)
    raw_btn.setCursor(Qt.PointingHandCursor)
    raw_btn.setToolTip("显示/隐藏 Markdown 原始档案（卡片视图的兜底）")
    raw_btn.toggled.connect(win._toggle_error_raw)
    win.error_raw_btn = raw_btn
    btn_bar.addWidget(quiz_btn)
    btn_bar.addWidget(refresh_err_btn)
    btn_bar.addWidget(raw_btn)
    btn_bar.addStretch()
    layout.addLayout(btn_bar)

    win._refresh_error_tab()
    return widget


__all__ = ["build", "render_error_cards"]
