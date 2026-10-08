# -*- coding: utf-8 -*-
"""私教对话页视图：消息气泡区 + 快捷指令药丸 + 输入栏

[P2 改造] 显示区从「一个占满的只读 QTextEdit + 占位符文字」换成气泡视图
（``widgets/chat_view.py``：用户右 / 私教左 / 系统播报居中留白），并补上
可点击的空状态示例提示词。输入、上传、发送逻辑与快捷键完全保持原样。

快捷指令药丸改造前带内联样式（写死 `background: #2e344e; color: #a5b4fc`），
在浅色主题下会与白底冲突 —— 现统一走 ``#QuickPill`` 选择器 + 主题 token。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

try:  # pragma: no cover - 取决于运行方式
    from gui.widgets.chat_view import ChatView
except ImportError:  # pragma: no cover
    from tools.gui.widgets.chat_view import ChatView  # type: ignore

#: 私教快捷指令
QUICK_COMMANDS = ("数学报到", "英语报到", "政治报到", "专业课报到", "交作业", "查漏", "更新看板")


class ChatInput(QPlainTextEdit):
    """[P1 修复·2026-10-08] 多行对话输入框：Enter 发送 / Shift+Enter 换行。

    为什么换掉 QLineEdit：政治大题、专业课论述动辄数百字，单行框既看不全
    已写内容，回车还会立刻误发半截答案。QPlainTextEdit 默认 Enter 即换行，
    这里覆写 ``keyPressEvent`` 把「无修饰键的 Enter/Return」转为发送信号，
    Shift/Ctrl/Alt+Enter 保持换行语义；高度随内容在 1~6 行间自适应。
    """

    send_requested = Signal()

    #: 高度自适应范围（行数），与字体行高相乘得到像素高度
    MIN_LINES = 1
    MAX_LINES = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        # Tab 交给焦点切换而不是插入制表符（作答场景不需要缩进控制）
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.textChanged.connect(self._adjust_height)
        self._adjust_height()

    def keyPressEvent(self, event):  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            if mods & (
                Qt.KeyboardModifier.ShiftModifier
                | Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
            ):
                # Shift/Ctrl/Alt+Enter：交给基类做换行
                super().keyPressEvent(event)
            else:
                self.send_requested.emit()
            return
        super().keyPressEvent(event)

    def _adjust_height(self):
        """按逻辑行数（Enter 分段）在 1~6 行之间调整固定高度。

        用 ``blockCount`` 而不是 ``document().size()``：后者依赖布局完成，
        离屏/首帧时读数不可靠（实测 3 行文本仍报 1 行高）；超长折行由内部
        滚动条兜底，不影响「多行可见」的核心诉求。
        """
        line_h = max(1, self.fontMetrics().lineSpacing())
        lines = min(max(self.document().blockCount(), self.MIN_LINES), self.MAX_LINES)
        # 上下各留 8px 内边距；下限 40px 与原 QLineEdit 初始高度一致
        self.setFixedHeight(max(40, lines * line_h + 16))


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
        if cmd == "数学报到":
            # [缺陷修复·判定不同源] 此前只查 math_key/math_name 两个键，
            # 漏了 mode_b / mode_c / pro2_name / pol_disabled 等模式；
            # 现改用与 CLI 同源的 is_math_disabled 单源判定。
            try:
                try:
                    from cli.shared import is_math_disabled
                except ImportError:  # pragma: no cover
                    from tools.cli.shared import is_math_disabled
                if is_math_disabled(win.config):
                    continue
            except Exception:
                plan = win.config.get("study_plan") or {}
                if plan.get("math_key") == "none" or plan.get("math_name") == "不考数学":
                    continue
        pill = QPushButton(cmd)
        pill.setObjectName("QuickPill")          # 样式全部来自主题 QSS
        pill.setCursor(Qt.PointingHandCursor)
        pill.clicked.connect(lambda checked=False, c=cmd: win._on_quick_command(c))
        quick_bar.addWidget(pill)
    quick_bar.addStretch()
    # [修复④·会话管理] 行尾两个会话操作按钮：查看/恢复历史、开始新对话。
    # 样式走 #SessionBtn（主题 token），逻辑在 MainWindow。
    sessions_btn = QPushButton("🕘 历史")
    sessions_btn.setObjectName("SessionBtn")
    sessions_btn.setCursor(Qt.PointingHandCursor)
    sessions_btn.setToolTip("查看/恢复历史会话")
    sessions_btn.clicked.connect(win._on_open_sessions)
    quick_bar.addWidget(sessions_btn)

    new_session_btn = QPushButton("＋ 新建")
    new_session_btn.setObjectName("SessionBtn")
    new_session_btn.setCursor(Qt.PointingHandCursor)
    new_session_btn.setToolTip("开始一段新对话")
    new_session_btn.clicked.connect(win._on_new_session)
    quick_bar.addWidget(new_session_btn)
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

    # [P1 修复·2026-10-08] 单行 QLineEdit → 多行 ChatInput（1~6 行自适应）：
    # 大题作答/论述题需要可见的多行书写区；Enter 发送、Shift+Enter 换行的
    # 语义由 ChatInput 内部实现（send_requested 信号替代原 returnPressed）。
    win.input_box = ChatInput()
    win.input_box.setMinimumHeight(40)
    win.input_box.setPlaceholderText(
        "输入口令 (如：英语长难句拆解 / 帽子词秒杀 / 交作业) 或向私教提问...\n"
        "Enter 发送 · Shift+Enter 换行（大题作答可多行输入）")
    win.input_box.send_requested.connect(win._on_send_message)
    input_bar.addWidget(win.input_box, stretch=1)

    # [缺陷修复·无法中断进行中的回答] 停止按钮：点击调 agent_worker.cancel()，
    # 取消检查回调会在 AgentRunner 的下一个步骤边界终止本轮 run，界面立即
    # 恢复可输入（见 MainWindow._on_stop_agent）。
    # [P2 修复·2026-10-08] 初始置灰：无正在生成的回答时「停止」不可点，
    # 由 MainWindow._set_agent_ui_running 随流式开始/结束联动。
    stop_btn = QPushButton("停止")
    stop_btn.setObjectName("SecondaryBtn")
    stop_btn.setMinimumHeight(40)
    stop_btn.setFixedWidth(64)
    stop_btn.setCursor(Qt.PointingHandCursor)
    stop_btn.setEnabled(False)
    stop_btn.setToolTip("停止当前正在生成的本轮回答（随后可直接发送新消息）")
    stop_btn.clicked.connect(win._on_stop_agent)
    win.stop_btn = stop_btn
    input_bar.addWidget(stop_btn)

    send_btn = QPushButton("发送")
    send_btn.setObjectName("SendBtn")        # 主色渐变 + 圆角三态样式来自主题 QSS
    send_btn.setMinimumHeight(40)
    send_btn.setFixedWidth(88)
    send_btn.setCursor(Qt.PointingHandCursor)
    send_btn.clicked.connect(win._on_send_message)
    win.send_btn = send_btn
    input_bar.addWidget(send_btn)
    layout.addLayout(input_bar)
    return widget


__all__ = ["ChatInput", "QUICK_COMMANDS", "build"]
