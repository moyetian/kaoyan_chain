# -*- coding: utf-8 -*-
"""
微信公众号文章检索 GUI 对话框 (WeChat Search Dialog)
"""

import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QSpinBox, QCheckBox, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal

ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)


class WeChatSearchWorker(QThread):
    """微信检索异步 Worker"""
    finished_signal = Signal(dict)

    def __init__(
        self,
        keyword: str,
        max_results: int,
        source: str,
        fetch_content: bool,
        save: bool,
        school: str
    ):
        super().__init__()
        self.keyword = keyword
        self.max_results = max_results
        self.source = source
        self.fetch_content = fetch_content
        self.save = save
        self.school = school

    def run(self):
        try:
            from skills.wechat_searcher import wechat_search

            result = wechat_search(
                keyword=self.keyword,
                max_results=self.max_results,
                fetch_content=self.fetch_content,
                save_to_local=self.save,
                school_name=self.school,
                source=self.source
            )
            self.finished_signal.emit(result)
        except Exception as e:
            self.finished_signal.emit({"success": False, "error": str(e), "results": []})


class WeChatSearchDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📱 微信公众号考研文章多源检索")
        self.setMinimumSize(880, 620)
        self._current_results = []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # 检索条件区
        cond_layout = QHBoxLayout()
        self.keyword_input = QLineEdit()
        self.keyword_input.setPlaceholderText("输入检索关键词 (如: 408计算机考研经验 / 数学二高分复盘)")
        self.keyword_input.returnPressed.connect(self._on_search)

        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 30)
        self.max_spin.setValue(10)

        self.source_combo = QComboBox()
        self.source_combo.addItems(["auto (自动降级)", "sogou (搜狗微信)", "bing (Bing定向)", "local (本地沉淀)"])

        self.fetch_check = QCheckBox("抓取正文")
        self.fetch_check.setChecked(True)

        self.save_check = QCheckBox("沉淀到本地")
        self.save_check.setChecked(False)

        self.school_input = QLineEdit()
        self.school_input.setPlaceholderText("联动高校名 (可选)")
        self.school_input.setMaximumWidth(140)

        self.search_btn = QPushButton("🔍 检索")
        self.search_btn.clicked.connect(self._on_search)

        cond_layout.addWidget(QLabel("关键词:"))
        cond_layout.addWidget(self.keyword_input, stretch=2)
        cond_layout.addWidget(QLabel("数量:"))
        cond_layout.addWidget(self.max_spin)
        cond_layout.addWidget(self.source_combo)
        cond_layout.addWidget(self.fetch_check)
        cond_layout.addWidget(self.save_check)
        cond_layout.addWidget(QLabel("联动高校:"))
        cond_layout.addWidget(self.school_input)
        cond_layout.addWidget(self.search_btn)
        layout.addLayout(cond_layout)

        # 结果表格
        self.result_table = QTableWidget(0, 5)
        self.result_table.setHorizontalHeaderLabels(["文章标题", "来源公众号", "发布日期", "状态", "原文链接"])
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.setSelectionMode(QTableWidget.SingleSelection)
        self.result_table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.result_table, stretch=2)

        # 状态与详情预览
        self.status_label = QLabel("输入关键词点击「🔍 检索」开始搜索考研经验与考点文章。")
        self.status_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(self.status_label)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("点击上方表格行，在此处预览文章摘要与抓取详情...")
        layout.addWidget(self.preview, stretch=2)

    def _on_search(self):
        keyword = self.keyword_input.text().strip()
        if not keyword:
            QMessageBox.information(self, "提示", "请输入检索关键词！")
            return

        source_map = {
            "auto (自动降级)": "auto",
            "sogou (搜狗微信)": "sogou",
            "bing (Bing定向)": "bing",
            "local (本地沉淀)": "local"
        }
        source = source_map.get(self.source_combo.currentText(), "auto")

        self.search_btn.setEnabled(False)
        self.search_btn.setText("正在检索...")
        self.status_label.setText(f"⏳ 正在多源检索关键词「{keyword}」中，请稍候...")
        self.result_table.setRowCount(0)
        self.preview.clear()

        self.worker = WeChatSearchWorker(
            keyword=keyword,
            max_results=self.max_spin.value(),
            source=source,
            fetch_content=self.fetch_check.isChecked(),
            save=self.save_check.isChecked(),
            school=self.school_input.text().strip()
        )
        self.worker.finished_signal.connect(self._on_search_done)
        self.worker.start()

    def _on_search_done(self, result: dict):
        self.search_btn.setEnabled(True)
        self.search_btn.setText("🔍 检索")

        if not result.get("success", True):
            err = result.get("error", "未知异常")
            self.status_label.setText(f"❌ 检索失败: {err}")
            self.preview.setText(f"错误详情:\n{err}")
            return

        items = result.get("results", [])
        self._current_results = items
        total = result.get("total", len(items))
        fetched = result.get("fetched", 0)

        saved_msg = f" | 已沉淀 {len(result.get('saved_paths', []))} 篇到 .memory/experiences/ (本地隐私目录)" if result.get("saved_paths") else ""
        scout_msg = " | 已联动更新目标校口碑档案" if result.get("scout_linked") else ""
        self.status_label.setText(f"✅ 检索完成：找到 {total} 篇，正文抓取 {fetched} 篇{saved_msg}{scout_msg}")

        self.result_table.setRowCount(len(items))
        for row, item in enumerate(items):
            status = "✅ 已抓取" if item.get("fetched") else "📋 仅标题"
            self.result_table.setItem(row, 0, QTableWidgetItem(item.get("title", "")))
            self.result_table.setItem(row, 1, QTableWidgetItem(item.get("source_account", "")))
            self.result_table.setItem(row, 2, QTableWidgetItem(item.get("publish_date", "")))
            self.result_table.setItem(row, 3, QTableWidgetItem(status))
            self.result_table.setItem(row, 4, QTableWidgetItem(item.get("url", "")))

    def _on_row_selected(self):
        selected_rows = self.result_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        if 0 <= row < len(self._current_results):
            it = self._current_results[row]
            info = [
                f"【标题】: {it.get('title')}",
                f"【公众号】: {it.get('source_account') or '未知'}    【发布日期】: {it.get('publish_date') or '未知'}",
                f"【原文链接】: {it.get('url')}",
                f"【正文长度】: {it.get('content_length', 0)} 字符    【来源平台】: {it.get('source_platform', 'auto')}",
                "-" * 60,
                f"【摘要预览】:\n{it.get('summary', '暂无摘要')}"
            ]
            self.preview.setText("\n".join(info))
