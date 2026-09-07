# -*- coding: utf-8 -*-
"""
考研学习链 GUI 主窗口 (MainWindow)
"""

import sys
import json
import re
from pathlib import Path
from datetime import date, datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QTabWidget, QProgressBar, QFrame, QScrollArea,
    QLineEdit, QTextEdit, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

CONFIG_FILE = ROOT / "ky_config.json"


class FunctionCard(QFrame):
    """功能模块卡片组件"""
    clicked = Signal(str)  # 发射功能别名

    def __init__(self, icon: str, title: str, desc: str, alias: str, parent=None):
        super().__init__(parent)
        self.alias = alias
        self.setObjectName("FunctionCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(200, 84)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        title_label = QLabel(f"{icon}  {title}")
        title_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #6366f1;")
        desc_label = QLabel(desc)
        desc_label.setStyleSheet("font-size: 11px; color: #94a3b8;")
        desc_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(desc_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.alias)
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("考研学习链 · 全科智能私教中枢")
        self.setMinimumSize(1180, 780)
        self._load_config()
        self._init_ui()
        self._init_timer()
        self._load_today_task_progress()

    def _load_config(self):
        self.config = {}
        if CONFIG_FILE.exists():
            try:
                self.config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.config = {}

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(14)
        main_layout.setContentsMargins(18, 16, 18, 16)

        # ── 顶部状态栏 ──
        header = self._build_header()
        main_layout.addWidget(header)

        # ── 核心功能区 (网格卡片) ──
        cards_area = self._build_function_cards()
        main_layout.addWidget(cards_area, stretch=1)

        # ── 底部标签页 ──
        self.tabs = QTabWidget()
        self.tab_widget = self.tabs
        self.tabs.addTab(self._build_chat_tab(), "💬 私教对话")
        self.tabs.addTab(self._build_task_tab(), "📋 今日任务")
        self.tabs.addTab(self._build_error_tab(), "📕 错题本")
        self.tabs.addTab(self._build_intel_tab(), "🏛️ 研招情报")
        main_layout.addWidget(self.tabs, stretch=3)

    def _build_header(self):
        header = QFrame()
        header.setObjectName("HeaderBar")
        header.setFixedHeight(75)
        layout = QHBoxLayout(header)

        days_left = self._calc_countdown()

        title = QLabel("🎯 考研学习链")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #6366f1;")

        sp = self.config.get("study_plan", {})
        school = sp.get("school") or self.config.get("target_school") or "目标院校"
        major = sp.get("major") or self.config.get("target_major") or "报考专业"
        style = sp.get("coach_style") or self.config.get("active_style") or "严格把关·保姆提分型"

        countdown = QLabel(f"⏳ 初试倒计时: {days_left} 天")
        countdown.setStyleSheet("font-size: 15px; font-weight: bold; color: #f59e0b;")

        meta_label = QLabel(f"🏛️ 目标: {school} · {major}  |  🛡️ 风格: {style.split('·')[0]}")
        meta_label.setStyleSheet("font-size: 12px; color: #94a3b8;")

        layout.addWidget(title)
        layout.addSpacing(16)
        layout.addWidget(meta_label)
        layout.addStretch()
        layout.addWidget(countdown)
        return header

    def _build_function_cards(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("CardScrollArea")
        scroll.setMaximumHeight(200)

        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(10)
        grid.setContentsMargins(4, 4, 4, 4)

        cards_data = [
            ("📋", "今日任务", "查看四科任务量与推进打卡", "today"),
            ("🎯", "靶向组卷", "按考点与难度智能拼卷演练", "compose"),
            ("🔄", "同源变式", "薄弱考点同源变式真题检索", "variant"),
            ("📈", "考纲Diff", "新旧考纲层级对比与动荡率", "diff"),
            ("📥", "切片入库", "真题/模拟卷结构化切片入库", "ingest"),
            ("🔍", "院校侦察", "研招网与社媒实名口碑研报", "scout"),
            ("⚖️", "双校对标", "双校初复试指标横向对标", "compare"),
            ("📡", "简章监控", "高校研究生院简章变动预警", "watch"),
            ("📊", "看板更新", "重新编译掌握度雷达看板", "build"),
            ("📱", "公众号检索", "微信公众号考研文章检索与沉淀", "wechat_search"),
        ]

        self._feature_buttons = []
        self.feature_cards = []
        for idx, (icon, title, desc, alias) in enumerate(cards_data):
            card = FunctionCard(icon, title, desc, alias, container)
            card.clicked.connect(self._on_card_clicked)
            self._feature_buttons.append(card)
            self.feature_cards.append(card)
            row, col = divmod(idx, 5)
            grid.addWidget(card, row, col)

        scroll.setWidget(container)
        return scroll

    def _build_chat_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText("欢迎来到考研全科专属私教中枢！输入口令 (如：数学报到 / 交作业) 或直接提问开始辅导...")
        layout.addWidget(self.chat_display, stretch=3)

        input_bar = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("输入口令 (如：数学报到 / 英语长难句 / 交作业) 或向私教提问...")
        self.input_box.returnPressed.connect(self._on_send_message)

        send_btn = QPushButton("发送 ➤")
        send_btn.clicked.connect(self._on_send_message)

        input_bar.addWidget(self.input_box, stretch=1)
        input_bar.addWidget(send_btn)
        layout.addLayout(input_bar)
        return widget

    def _build_task_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        self.task_progress_bars = {}
        self.task_count_labels = {}

        subjects = [
            ("01-数学", "数学二 (302)", "math"),
            ("02-英语", "英语二 (204)", "eng"),
            ("03-思想政治理论", "思想政治理论", "pol"),
            ("04-专业课", "408 计算机基础", "pro"),
        ]

        for folder, label_text, key in subjects:
            frame = QFrame()
            frame.setObjectName("TaskRow")
            frame.setStyleSheet("background: #1a1e2e; border-radius: 8px; padding: 8px 14px;")
            h = QHBoxLayout(frame)

            label = QLabel(f"📚 {label_text}")
            label.setFixedWidth(180)
            label.setStyleSheet("font-weight: bold; font-size: 13px;")

            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(0)
            progress.setFixedHeight(18)

            pct_label = QLabel("0/0 (0%)")
            pct_label.setFixedWidth(90)
            pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            pct_label.setStyleSheet("color: #94a3b8; font-size: 12px;")

            h.addWidget(label)
            h.addWidget(progress, stretch=1)
            h.addWidget(pct_label)
            layout.addWidget(frame)

            self.task_progress_bars[key] = progress
            self.task_count_labels[key] = pct_label

        refresh_btn = QPushButton("🔄 刷新今日进度")
        refresh_btn.setMaximumWidth(140)
        refresh_btn.clicked.connect(self._load_today_task_progress)
        layout.addWidget(refresh_btn)

        layout.addStretch()
        return widget

    def _build_error_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        self.error_info = QTextEdit()
        self.error_info.setReadOnly(True)
        layout.addWidget(self.error_info, stretch=1)

        btn_bar = QHBoxLayout()
        quiz_btn = QPushButton("🎯 一键组装错题盲盒自测卷")
        quiz_btn.clicked.connect(self._generate_error_quiz)
        refresh_err_btn = QPushButton("🔄 刷新待复测队列")
        refresh_err_btn.clicked.connect(self._refresh_error_tab)

        btn_bar.addWidget(quiz_btn)
        btn_bar.addWidget(refresh_err_btn)
        btn_bar.addStretch()
        layout.addLayout(btn_bar)

        self._refresh_error_tab()
        return widget

    def _build_intel_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        self.intel_display = QTextEdit()
        self.intel_display.setReadOnly(True)
        layout.addWidget(self.intel_display, stretch=1)

        btn_bar = QHBoxLayout()
        btn_watch = QPushButton("📡 查看监控高校")
        btn_watch.clicked.connect(lambda: self._on_card_clicked("watch"))
        btn_scout = QPushButton("🔍 院校深度侦察")
        btn_scout.clicked.connect(lambda: self._on_card_clicked("scout"))
        btn_bar.addWidget(btn_watch)
        btn_bar.addWidget(btn_scout)
        btn_bar.addStretch()
        layout.addLayout(btn_bar)

        self._refresh_intel_tab()
        return widget

    def _calc_countdown(self) -> int:
        exam_str = self.config.get("exam_date") or \
                    self.config.get("study_plan", {}).get("exam_date", "2026-12-19")
        try:
            exam_d = datetime.strptime(exam_str, "%Y-%m-%d").date()
            return max(0, (exam_d - date.today()).days)
        except Exception:
            return 103

    def _load_today_task_progress(self):
        """解析各科 _状态/今日任务.md 的勾选进度"""
        subjs = [
            ("01-数学", "math"),
            ("02-英语", "eng"),
            ("03-思想政治理论", "pol"),
            ("04-专业课", "pro"),
        ]
        for folder, key in subjs:
            task_file = ROOT / folder / "_状态" / "今日任务.md"
            done_count = 0
            total_count = 0
            if task_file.exists():
                try:
                    text = task_file.read_text(encoding="utf-8")
                    for line in text.splitlines():
                        if re.match(r"^\s*-\s*\[[ xX]\]", line):
                            total_count += 1
                            if re.match(r"^\s*-\s*\[[xX]\]", line):
                                done_count += 1
                except Exception:
                    pass

            pct = int(done_count / total_count * 100) if total_count > 0 else 0
            if key in self.task_progress_bars:
                self.task_progress_bars[key].setValue(pct)
                self.task_count_labels[key].setText(f"{done_count}/{total_count} ({pct}%)")

    def _refresh_error_tab(self):
        """扫描各科待复测错题"""
        lines = [
            "# 📕 艾宾浩斯记忆遗忘曲线 · 到期错题复测队列",
            f"> 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            ""
        ]
        subjs = [("01-数学", "数学"), ("02-英语", "英语"), ("03-思想政治理论", "政治"), ("04-专业课", "专业课")]
        total_due = 0
        for folder, name in subjs:
            mistake_dir = ROOT / folder / "错题本"
            if mistake_dir.exists():
                due_files = list(mistake_dir.glob("*.md"))
                lines.append(f"### 📚 {name}错题本: 共 {len(due_files)} 道错题档案")
                total_due += len(due_files)
                for f in due_files[:3]:
                    lines.append(f"- 📄 `{f.stem}`")
                if len(due_files) > 3:
                    lines.append(f"- *(其余 {len(due_files) - 3} 道已归档)*")
            else:
                lines.append(f"### 📚 {name}错题本: 暂无到期错题")
            lines.append("")

        lines.append(f"**全科待攻坚错题总数**: `{total_due}` 道")
        self.error_info.setMarkdown("\n".join(lines))

    def _generate_error_quiz(self):
        """生成自测盲盒试卷"""
        try:
            from skills import exam_composer
            res = exam_composer.compose_exam("pro", total_questions=3)
            paper_text = exam_composer.format_exam_paper(res)
            self.tabs.setCurrentIndex(0)
            self.chat_display.append("\n\n" + "=" * 50 + "\n🎯 【错题盲盒自测卷】已生成：\n" + paper_text)
        except Exception as e:
            QMessageBox.warning(self, "提示", f"组卷异常: {e}")

    def _refresh_intel_tab(self):
        """加载研招监控与情报概要"""
        lines = [
            "# 🏛️ 研招招考动态与高校监控雷达",
            f"> 数据基准: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            ""
        ]
        watch_file = ROOT / ".memory" / "admission_watch.json"
        if watch_file.exists():
            try:
                data = json.loads(watch_file.read_text(encoding="utf-8"))
                lines.append(f"### 📡 正在动态监控的高校 ({len(data)} 所):")
                for code, it in data.items():
                    lines.append(f"- **{it.get('name')}** (`{code}`) | 上次核验: `{it.get('last_check', '-')}`")
                    titles = it.get("recent_titles", [])
                    if titles:
                        lines.append(f"  - 最新通知: *{titles[0]}*")
            except Exception:
                lines.append("暂无监控数据。")
        else:
            lines.append("暂未配置监控高校，点击下方按钮或在 TUI 中输入 8 即可纳入监控。")

        self.intel_display.setMarkdown("\n".join(lines))

    def _on_card_clicked(self, alias: str):
        """功能卡片点击处理"""
        if alias == "wechat_search":
            self._open_wechat_search_dialog()
            return
        elif alias == "today":
            self.tabs.setCurrentIndex(1)
            self._load_today_task_progress()
            return
        elif alias == "watch":
            self.tabs.setCurrentIndex(3)
            self._refresh_intel_tab()
            return

        # 其他模块在私教对话窗口中以执行日志形式展现
        self.tabs.setCurrentIndex(0)
        self.chat_display.append(f"\n▶ 正在启动模块 [{alias}] ...")

        try:
            from tui_navigator import execute_action
            execute_action(alias, interactive=False)
            self.chat_display.append(f"✔ 模块 [{alias}] 执行调用完毕。")
        except Exception as e:
            self.chat_display.append(f"❌ 模块 [{alias}] 执行异常: {e}")

    def _open_wechat_search_dialog(self):
        """打开微信公众号文章检索对话框"""
        from gui.widgets.wechat_search_dialog import WeChatSearchDialog
        dialog = WeChatSearchDialog(self)
        dialog.exec()

    def _on_send_message(self):
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self.chat_display.append(f"\n👤 你: {text}\n🤖 私教正在思考中...")

        from gui.workers.agent_worker import AgentWorker
        self.agent_worker = AgentWorker(self.config, text)
        self.agent_worker.finished_signal.connect(self._on_agent_reply)
        self.agent_worker.start()

    def _on_agent_reply(self, reply: str):
        self.chat_display.append(f"\n🤖 私教:\n{reply}\n" + "-" * 50)

    def _init_timer(self):
        """定时刷新倒计时与任务进度"""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start(60000)

    def _on_timer_tick(self):
        self._load_today_task_progress()
