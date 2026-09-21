# -*- coding: utf-8 -*-
"""
微信公众号文章检索 GUI 对话框 (WeChat Search Dialog)
"""

import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QSpinBox, QCheckBox, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QApplication
)
from PySide6.QtCore import QThread, Signal

ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# 线程生命周期收尾（P2-9）
# ---------------------------------------------------------------------------
# [缺陷] 本轮 parent + _worker_refs 修复挡住了「重入覆盖引用」，但**没有**处理
# 「检索进行中退出应用」：worker 是对话框的 QObject 子对象，主窗口析构会连带
# 销毁仍在跑的 QThread → "QThread: Destroyed while thread is still running"
# → 进程 abort（离屏实测 exit 127）。
#
# 收尾策略（不卡死 UI、不把运行中的 QThread 交给析构）：
#   * ``_ACTIVE_WORKERS``：模块级强引用池，登记所有在跑的 worker，避免任何
#     父对象析构路径把它连带销毁；
#   * ``_stop_worker``：先 ``requestInterruption`` + **带超时** ``wait``；超时
#     仍不结束则 ``terminate`` 兜底并再等一次 —— 保证线程**真的停了**，
#     否则它进入析构就是 "Destroyed while thread is still running" → abort；
#   * ``aboutToQuit`` 钩子：在 Qt 开始析构对象**之前**触发，对在跑池逐条收尾；
#   * 对话框 ``closeEvent``：同上；若线程连 terminate 都停不掉（异常替身等），
#     把它从对话框下「摘」出来改挂 QApplication，对话框可安全析构。
_CLOSE_WAIT_MS = 3000   # 关闭对话框时等待线程收尾的上限
_QUIT_WAIT_MS = 3000    # 退出应用时等待每个线程收尾的上限
_TERMINATE_WAIT_MS = 1000   # terminate 后再等线程彻底停止的上限
_ACTIVE_WORKERS: set = set()
_QUIT_HOOK_INSTALLED = False


def _stop_worker(w, grace_ms: int) -> bool:
    """请求中断并带超时等待线程结束；超时则 ``terminate`` 兜底。

    返回线程是否已停止。**关键**：只要还返回 False，调用方就不得让该线程
    进入析构（Qt 对运行中的 QThread 析构会直接 abort 进程）。
    """
    try:
        if not w.isRunning():
            return True
    except Exception:
        return True
    try:
        w.requestInterruption()
    except Exception:
        pass
    try:
        if w.wait(grace_ms):
            return True
    except Exception:
        return True          # 替身没有 wait：无法等待，交给上层决定
    # 超时仍未结束 → terminate 兜底（本 worker 只做网络 I/O，不持共享锁）
    try:
        w.terminate()
        w.wait(_TERMINATE_WAIT_MS)
    except Exception:
        pass
    try:
        return not w.isRunning()
    except Exception:
        return True


def _wait_for_active_workers() -> None:
    """退出应用前给在跑的检索线程一次收尾机会（每个线程上限 ``_QUIT_WAIT_MS``）。

    有上限故不会卡死退出流程；收尾失败（线程连 terminate 都停不掉）时线程对象
    仍被 ``_ACTIVE_WORKERS`` 强引用持有，不会被 Qt 析构阶段销毁。
    """
    for w in list(_ACTIVE_WORKERS):
        _stop_worker(w, _QUIT_WAIT_MS)


def _install_quit_hook() -> None:
    """幂等地在 QApplication.aboutToQuit 上挂收尾钩子。"""
    global _QUIT_HOOK_INSTALLED
    if _QUIT_HOOK_INSTALLED:
        return
    app = QApplication.instance()
    if app is None:
        return
    try:
        app.aboutToQuit.connect(_wait_for_active_workers)
    except Exception:
        return
    _QUIT_HOOK_INSTALLED = True


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
        school: str,
        time_range: str = "year",
        parent=None
    ):
        # [P2-8 修复·parent 缺失] 原先只调 super().__init__()，worker 的
        # parent() 恒为 None。QThread 一旦失去 Python 侧引用就会被 GC，
        # 而 Qt 侧线程还在跑 → "QThread: Destroyed while thread is still
        # running" 直接崩进程。挂到对话框上，Qt 侧父子关系即为兜底强引用。
        super().__init__(parent)
        self.keyword = keyword
        self.max_results = max_results
        self.source = source
        self.fetch_content = fetch_content
        self.save = save
        self.school = school
        self.time_range = time_range

    def run(self):
        try:
            from skills.wechat_searcher import wechat_search

            result = wechat_search(
                keyword=self.keyword,
                max_results=self.max_results,
                fetch_content=self.fetch_content,
                save_to_local=self.save,
                school_name=self.school,
                source=self.source,
                time_range=self.time_range
            )
            self.finished_signal.emit(result)
        except Exception as e:
            self.finished_signal.emit({"success": False, "error": str(e), "results": []})


class WeChatSearchDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("微信公众号考研文章多源检索")
        self.setMinimumSize(880, 620)
        self._current_results = []
        # [P2-8 修复] 与主窗口 _worker_refs 同款强引用池：仅靠 self.worker
        # 单引用持有时，重入会被覆盖 → 运行中的 QThread 被 GC 销毁而崩进程。
        self.worker = None
        self._worker_refs = []
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # 检索条件区
        cond_layout = QHBoxLayout()
        self.keyword_input = QLineEdit()
        # [收尾修复·placeholder 硬编码 408] TUI 已按档案动态化，GUI 同步：
        # 有档案取"学校 专业 考研经验"，否则保留通用示例。
        _ph_school, _ph_major = "", ""
        try:
            import json as _js
            _cfg_p = ROOT / "ky_config.json"
            if _cfg_p.exists():
                _sp = (_js.loads(_cfg_p.read_text(encoding="utf-8")) or {}).get("study_plan", {}) or {}
                _ph_school = str(_sp.get("school", "") or "")
                _ph_major = str(_sp.get("major", "") or "")
                if _ph_school in ("目标院校", "未指定"):
                    _ph_school = ""
                if _ph_major in ("报考专业", "专业课", "未指定"):
                    _ph_major = ""
        except Exception:
            pass
        _ph_kw = f"{_ph_school} {_ph_major} 考研经验".strip() or "408计算机考研经验 / 数学二高分复盘"
        self.keyword_input.setPlaceholderText(f"输入检索关键词 (如: {_ph_kw})")
        self.keyword_input.returnPressed.connect(self._on_search)

        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 30)
        self.max_spin.setValue(10)

        self.time_combo = QComboBox()
        self.time_combo.addItems(["近一年 (推荐)", "近半年", "近三年", "全部时间"])

        self.source_combo = QComboBox()
        self.source_combo.addItems(["auto (自动降级)", "sogou (搜狗微信)", "bing (Bing定向)", "local (本地沉淀)"])

        self.fetch_check = QCheckBox("抓取正文")
        self.fetch_check.setChecked(True)

        self.save_check = QCheckBox("沉淀到本地")
        self.save_check.setChecked(False)

        self.school_input = QLineEdit()
        self.school_input.setPlaceholderText("联动高校名 (可选)")
        self.school_input.setMaximumWidth(140)
        if _ph_school:
            self.school_input.setText(_ph_school)

        self.search_btn = QPushButton("检索")
        self.search_btn.clicked.connect(self._on_search)

        cond_layout.addWidget(QLabel("关键词:"))
        cond_layout.addWidget(self.keyword_input, stretch=2)
        cond_layout.addWidget(QLabel("时效:"))
        cond_layout.addWidget(self.time_combo)
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
        self.status_label = QLabel("输入关键词点击「检索」开始搜索考研经验与考点文章。")
        # [同批修复·内联样式] 原先在此写死 #94a3b8 —— 与主窗口那 10 处同一类问题：
        # widget 自带样式表优先级高于全局 QSS，切浅色主题后这行字仍是深灰偏白、
        # 在白底上看不清。改走 objectName + 主题 token。
        self.status_label.setObjectName("StatusLabel")
        layout.addWidget(self.status_label)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("点击上方表格行，在此处预览文章摘要与抓取详情...")
        layout.addWidget(self.preview, stretch=2)

    def _on_search(self):
        # [P2-8 修复·重入保护] 检索未完成时用户再按回车（returnPressed 与本按钮
        # 都连到本方法）会重入：旧实现直接 new 一个 worker 覆盖 self.worker，
        # 前一个 QThread 失去 Python 引用 → "QThread: Destroyed while thread is
        # still running"。这里早退，既防重入也防引用被覆盖。
        if self.worker is not None and self.worker.isRunning():
            return

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

        time_map = {
            "近一年 (推荐)": "year",
            "近半年": "half_year",
            "近三年": "three_years",
            "全部时间": "all",
        }
        time_range = time_map.get(self.time_combo.currentText(), "year")

        self.search_btn.setEnabled(False)
        self.search_btn.setText("正在检索...")
        self.status_label.setText(f"⏳ 正在多源检索关键词「{keyword}」中，请稍候...")
        self.result_table.setRowCount(0)
        self.preview.clear()

        self.worker = WeChatSearchWorker(
            parent=self,
            keyword=keyword,
            max_results=self.max_spin.value(),
            source=source,
            fetch_content=self.fetch_check.isChecked(),
            save=self.save_check.isChecked(),
            school=self.school_input.text().strip(),
            time_range=time_range
        )
        self._worker_refs.append(self.worker)
        # [P2-9] 同时登记进模块级在跑池 + 装退出钩子：即便用户从不关闭本对话框
        # 而直接退出应用，aboutToQuit 也能先给线程收尾机会。
        _ACTIVE_WORKERS.add(self.worker)
        _install_quit_hook()
        self.worker.finished_signal.connect(self._on_search_done)
        # 线程结束即出池，避免长会话里强引用无限累积（同主窗口写法）。
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_worker_finished(self):
        """线程自然结束：从强引用池中出池（同时清模块级在跑池）。

        用**绑定方法**而非 lambda 作为槽：Qt 在接收者（本对话框）析构时会自动
        断开该连接，不会在对话框已销毁后仍回调进来。
        """
        w = self.sender() or self.worker
        try:
            self._worker_refs.remove(w)
        except (ValueError, TypeError):
            pass
        _ACTIVE_WORKERS.discard(w)

    def closeEvent(self, event):
        """[P2-9] 关闭对话框时安全收尾在跑的检索线程。

        检索进行中关闭对话框 / 退出应用时，worker 若仍是对话框的子对象，主窗口
        析构会连带销毁仍在跑的 QThread → 进程 abort。这里走 ``_stop_worker``
        （带超时的 wait + terminate 兜底），既不阻塞 UI，也不把运行中的 QThread
        交给析构；万一仍停不掉，再把它从对话框下摘出来（``_detach_worker``）。
        """
        self._shutdown_worker()
        super().closeEvent(event)

    def _shutdown_worker(self) -> None:
        w = self.worker
        if w is None:
            return
        if _stop_worker(w, _CLOSE_WAIT_MS):
            return  # 已停止，随对话框一起析构是安全的
        self._detach_worker(w)

    def _detach_worker(self, w) -> None:
        """超时后把仍在跑的 worker 摘出对话框，避免被连带析构。"""
        try:
            w.finished_signal.disconnect(self._on_search_done)
        except (RuntimeError, TypeError):
            pass
        try:
            app = QApplication.instance()
            if app is not None:
                w.setParent(app)
        except Exception:
            pass
        _ACTIVE_WORKERS.add(w)
        try:
            w.finished.connect(lambda ww=w: _ACTIVE_WORKERS.discard(ww))
        except Exception:
            pass
        if w in self._worker_refs:
            self._worker_refs.remove(w)
        self.worker = None

    def _on_search_done(self, result: dict):
        self.search_btn.setEnabled(True)
        self.search_btn.setText("检索")

        if not result.get("success", True):
            err = result.get("error", "未知异常")
            self.status_label.setText(f"[×] 检索失败: {err}")
            self.preview.setText(f"错误详情:\n{err}")
            return

        items = result.get("results", [])
        self._current_results = items
        total = result.get("total", len(items))
        fetched = result.get("fetched", 0)

        saved_msg = f" | 已沉淀 {len(result.get('saved_paths', []))} 篇到 .memory/experiences/ (本地隐私目录)" if result.get("saved_paths") else ""
        scout_msg = " | 已联动更新目标校口碑档案" if result.get("scout_linked") else ""
        self.status_label.setText(f"[√] 检索完成：找到 {total} 篇，正文抓取 {fetched} 篇{saved_msg}{scout_msg}")

        self.result_table.setRowCount(len(items))
        for row, item in enumerate(items):
            status = "[√] 已抓取" if item.get("fetched") else "[-] 仅标题"
            self.result_table.setItem(row, 0, QTableWidgetItem(item.get("title", "")))
            # [P3 修复·D8] 读取入口统一下发的 account_display/date_display，避免 GUI 独写「未知」
            self.result_table.setItem(row, 1, QTableWidgetItem(
                item.get("account_display") or item.get("source_account", "")))
            self.result_table.setItem(row, 2, QTableWidgetItem(
                item.get("date_display") or item.get("publish_date", "")))
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
                f"【公众号】: {it.get('account_display') or it.get('source_account') or '未识别（平台未公开）'}"
                f"    【发布日期】: {it.get('date_display') or it.get('publish_date') or '未标注日期'}",
                f"【原文链接】: {it.get('url')}",
                f"【正文长度】: {it.get('content_length', 0)} 字符    【来源平台】: {it.get('source_platform', 'auto')}",
                "-" * 60,
                f"【摘要预览】:\n{it.get('summary', '暂无摘要')}"
            ]
            self.preview.setText("\n".join(info))
