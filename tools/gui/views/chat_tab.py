# -*- coding: utf-8 -*-
"""私教对话页视图：消息气泡区 + 快捷指令药丸 + 输入栏

[P2 改造] 显示区从「一个占满的只读 QTextEdit + 占位符文字」换成气泡视图
（``widgets/chat_view.py``：用户右 / 私教左 / 系统播报居中留白），并补上
可点击的空状态示例提示词。输入、上传、发送逻辑与快捷键完全保持原样。

快捷指令药丸改造前带内联样式（写死 `background: #2e344e; color: #a5b4fc`），
在浅色主题下会与白底冲突 —— 现统一走 ``#QuickPill`` 选择器 + 主题 token。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

try:  # pragma: no cover - 取决于运行方式
    from gui.widgets.chat_view import ChatView
except ImportError:  # pragma: no cover
    from tools.gui.widgets.chat_view import ChatView  # type: ignore

#: 私教快捷指令
QUICK_COMMANDS = ("数学报到", "英语报到", "政治报到", "专业课报到", "交作业", "查漏", "更新看板")


def build(win) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setSpacing(12)
    layout.setContentsMargins(12, 12, 12, 12)

    win.chat_display = ChatView()
    win.chat_display.example_clicked.connect(win._on_example_prompt)
    layout.addWidget(win.chat_display, stretch=3)

    quick_bar = QHBoxLayout()
    quick_bar.setSpacing(8)
    for cmd in QUICK_COMMANDS:
        plan = win.config.get("study_plan") or {}
        if cmd == "数学报到" and (plan.get("math_key") == "none" or plan.get("math_name") == "不考数学"):
            continue
        pill = QPushButton(cmd)
        pill.setObjectName("QuickPill")          # 样式全部来自主题 QSS
        pill.setCursor(Qt.PointingHandCursor)
        pill.clicked.connect(lambda checked=False, c=cmd: win._on_quick_command(c))
        quick_bar.addWidget(pill)
    quick_bar.addStretch()
    layout.addLayout(quick_bar)

    input_bar = QHBoxLayout()
    input_bar.setSpacing(8)

    upload_img_btn = QPushButton("📷 图片")
    upload_img_btn.setObjectName("UploadBtn")
    upload_img_btn.setToolTip("上传手写解答、草稿或错题图片 (/img 视觉批改)")
    upload_img_btn.setMinimumHeight(40)
    upload_img_btn.setCursor(Qt.PointingHandCursor)
    upload_img_btn.clicked.connect(win._on_upload_image)
    input_bar.addWidget(upload_img_btn)

    upload_file_btn = QPushButton("📎 文件")
    upload_file_btn.setObjectName("UploadBtn")
    upload_file_btn.setToolTip("上传考纲、真题讲义或备考文档 (/file 挂载分析)")
    upload_file_btn.setMinimumHeight(40)
    upload_file_btn.setCursor(Qt.PointingHandCursor)
    upload_file_btn.clicked.connect(win._on_upload_file)
    input_bar.addWidget(upload_file_btn)

    win.input_box = QLineEdit()
    win.input_box.setMinimumHeight(40)
    win.input_box.setPlaceholderText(
        "输入口令 (如：英语长难句拆解 / 帽子词秒杀 / 交作业) 或向私教提问...")
    win.input_box.returnPressed.connect(win._on_send_message)
    input_bar.addWidget(win.input_box, stretch=1)

    send_btn = QPushButton("发送")
    send_btn.setMinimumHeight(40)
    send_btn.setFixedWidth(88)
    send_btn.setCursor(Qt.PointingHandCursor)
    send_btn.clicked.connect(win._on_send_message)
    input_bar.addWidget(send_btn)
    layout.addLayout(input_bar)
    return widget


__all__ = ["QUICK_COMMANDS", "build"]
