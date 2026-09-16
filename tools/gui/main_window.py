# -*- coding: utf-8 -*-
"""
考研学习链 GUI 主窗口 (MainWindow)

[本文件职责] 只做「组装界面 + 事件分发 + 生命周期」。

分层（对应 agent.md 的单一职责与领域物理隔离）：
    views/        构建控件与连线（header / function_cards / 四个页签）
    services/     取数据与调后端，纯数据、无 Qt 控件、可离屏单测
    theme_apply   主题解析应用 + QSettings 偏好持久化
    widgets/      可复用控件（FunctionCard 等）

改造前这里同时承担布局、业务、样式三件事（700 行；10 处内联 setStyleSheet
把颜色写死在控件上，导致浅色主题被压过而实际不可用；倒计时是局部变量、
永不刷新）。现在这些职责各自归位。

对外契约（scripts/gui_real_session_check.py 依赖，不得改名）：
    _feature_buttons / tab_widget / _toggle_theme()
    _load_today_task_progress() / _refresh_error_tab() / _refresh_intel_tab()
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import QTimer
# QTextCursor 用于流式输出时把光标移到末尾；否则 append 会另起段落，流式片段会断成多行。
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QInputDialog, QMainWindow, QMessageBox,
    QTabWidget, QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

# 双导入路径兼容（py tools/ky_gui.py 脚本式 / import tools.gui 包式）
try:  # pragma: no cover
    from gui import services, theme_apply, views
    from gui.widgets.function_card import FunctionCard
except ImportError:  # pragma: no cover
    from tools.gui import services, theme_apply, views  # type: ignore
    from tools.gui.widgets.function_card import FunctionCard  # type: ignore

CONFIG_FILE = ROOT / "ky_config.json"

TAB_TITLES = ("私教对话", "今日任务", "错题本", "研招情报")


class MainWindow(QMainWindow):
    def __init__(self, parent=None, workspace_root=None):
        super().__init__(parent)
        self.workspace_root = Path(workspace_root) if workspace_root else ROOT
        # [P0 修复] 私教工作线程强引用池与当前线程句柄。
        # QThread 无 parent，若仅靠 self.agent_worker 单引用持有，
        # 再次发送时被覆盖会导致运行中线程对象被 GC 销毁、进程直接崩溃。
        self.agent_worker = None
        self._worker_refs = []
        self._today = date.today()
        #: 本轮是否已流式输出过（决定收尾时是否补整段，避免答案打两遍）
        self._streamed = False

        self.setWindowTitle("考研学习链 · 全科智能私教中枢")
        self.setMinimumSize(1180, 780)
        self._load_config()
        self._theme = self._apply_initial_theme()
        self._init_ui()
        self._init_timer()
        self._refresh_all()
        theme_apply.restore_geometry(self)

    # ════════════════════════════════════════════════════════════
    # 初始化
    # ════════════════════════════════════════════════════════════

    def _load_config(self):
        self.config = {}
        cfg_p = self.workspace_root / "ky_config.json"
        if cfg_p.exists():
            try:
                self.config = json.loads(cfg_p.read_text(encoding="utf-8"))
            except Exception:
                self.config = {}

    def _apply_initial_theme(self):
        """解析并应用主题（token 现场编译，不再读两份手写 QSS）。"""
        theme = theme_apply.resolve_theme(self.workspace_root)
        app = QApplication.instance()
        if app is not None:
            theme_apply.apply_theme(app, theme)
        return theme

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(14)
        main_layout.setContentsMargins(18, 16, 18, 16)

        main_layout.addWidget(views.header.build(self))
        main_layout.addWidget(views.function_cards.build(self), stretch=1)

        self.tabs = QTabWidget()
        self.tab_widget = self.tabs          # 对外契约名
        for builder, title in zip(
            (views.chat_tab.build, views.task_tab.build,
             views.error_tab.build, views.intel_tab.build),
            TAB_TITLES,
        ):
            self.tabs.addTab(builder(self), title)
        self.tabs.setCurrentIndex(self._restore_last_tab())
        main_layout.addWidget(self.tabs, stretch=3)

    def _restore_last_tab(self) -> int:
        try:
            idx = int(theme_apply.read_prefs().get("last_tab", 0) or 0)
        except (TypeError, ValueError):
            return 0
        return idx if 0 <= idx < self.tabs.count() else 0

    # ════════════════════════════════════════════════════════════
    # 主题
    # ════════════════════════════════════════════════════════════

    def _sync_header_text(self):
        """把服务层取到的头部数据写进控件（含倒计时）。

        [缺陷修复·死数字] 头部倒计时改造前是局部变量，构造时算一次就再也不动。
        """
        info = services.header_info(self.workspace_root)
        self.countdown_label.setText(f"初试倒计时: {info['days_left']} 天")
        self.meta_label.setText(
            f"目标: {info['school']} · {info['major']}  |  "
            f"风格: {info['style_short']}")
        self._sync_theme_button()

    def _sync_theme_button(self):
        self.theme_btn.setText("深色" if self._theme.mode == "light" else "浅色")

    def _refresh_card_icons(self):
        """用当前主题的主色重渲染所有功能卡片图标（SVG 跟随主题色）。"""
        color = self._theme.color("acc")
        for card in getattr(self, "feature_cards", []):
            card.refresh_icon(color)

    def _open_settings(self):
        """打开主题 L2 旋钮设置面板。"""
        try:
            from gui.widgets.settings_dialog import SettingsDialog
        except ImportError:  # pragma: no cover
            from tools.gui.widgets.settings_dialog import SettingsDialog  # type: ignore
        SettingsDialog(self).exec()

    def _toggle_theme(self):
        """在明暗预设间切换并持久化（改造前重启即回退深色）。"""
        app = QApplication.instance()
        if app is None:
            return
        target = theme_apply.next_preset(self._theme.name)
        self._theme = theme_apply.set_preset(app, target, self.workspace_root)
        self._sync_theme_button()
        self._refresh_card_icons()

    # ════════════════════════════════════════════════════════════
    # 数据刷新（全部委托 services，本类不自行解析文件）
    # ════════════════════════════════════════════════════════════

    def _refresh_all(self):
        self._sync_header_text()
        self._load_today_task_progress()
        self._refresh_error_tab()
        self._refresh_intel_tab()

    def _load_today_task_progress(self):
        """刷新各科今日任务进度条（与 CLI / TUI 同源的共享解析器）。"""
        for subject in services.subject_progress(self.workspace_root):
            bar = self.task_progress_bars.get(subject.key)
            label = self.task_count_labels.get(subject.key)
            if bar is not None:
                bar.setValue(subject.pct)
            if label is not None:
                label.setText(subject.summary_text)

    def _refresh_error_tab(self):
        self.error_info.setMarkdown(services.error_queue_markdown(self.workspace_root))

    def _refresh_intel_tab(self):
        self.intel_display.setMarkdown(services.intel_markdown(self.workspace_root))

    # ════════════════════════════════════════════════════════════
    # 事件分发
    # ════════════════════════════════════════════════════════════

    def _on_quick_command(self, cmd_text: str):
        self.input_box.setText(cmd_text)
        self._on_send_message()

    def _run_action_to_display(self, alias: str, display_widget, brief_to_chat: bool = True):
        """执行后端模块并把输出回显到指定文本框。"""
        display_widget.append(f"\n▶ 正在启动模块 [{alias}] ...")
        out = services.run_action_capture(alias)
        if out:
            display_widget.append(out)
        display_widget.append(f"[√] 模块 [{alias}] 执行调用完毕。")
        if brief_to_chat:
            self.chat_display.append(f"\n[√] 模块 [{alias}] 已在对应页面执行完毕，详见上方分页。")

    def _on_card_clicked(self, alias: str):
        if alias == "wechat_search":
            self._open_wechat_search_dialog()
        elif alias == "today":
            self.tabs.setCurrentIndex(1)
            self._load_today_task_progress()
        elif alias == "watch":
            self.tabs.setCurrentIndex(3)
            self._run_action_to_display("watch", self.intel_display)
        elif alias == "ingest":
            self._run_ingest_from_dialog()
        elif alias == "diff":
            self._run_diff_from_dialog()
        elif alias == "scout":
            self.tabs.setCurrentIndex(3)
            self._run_action_to_display("scout", self.intel_display)
        elif alias == "compare":
            self._run_compare_from_dialog()
        else:
            self.tabs.setCurrentIndex(0)
            self.chat_display.append(f"\n▶ 正在启动模块 [{alias}] ...")
            out = services.run_action_capture(alias)
            if out:
                self.chat_display.append(out)
            self.chat_display.append(f"[√] 模块 [{alias}] 执行调用完毕。")

    def _run_ingest_from_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要切片的真题 / 讲义文件", str(self.workspace_root),
            "题库文件 (*.md *.txt *.pdf);;所有文件 (*.*)")
        if not path:
            return
        self.tabs.setCurrentIndex(0)
        self.chat_display.append(f"\n▶ 正在切片入库 [{path}] ...")
        self.chat_display.append(services.ingest_file(self.workspace_root, path))

    def _run_diff_from_dialog(self):
        """考纲 Diff：必须选到两个真实文件，严禁伪造变动。"""
        old_path, _ = QFileDialog.getOpenFileName(
            self, "选择【基准(旧)】考纲文件", str(self.workspace_root / "04-专业课"),
            "Markdown (*.md *.txt);;所有文件 (*.*)")
        if not old_path:
            return
        new_path, _ = QFileDialog.getOpenFileName(
            self, "选择【最新】考纲文件", str(Path(old_path).parent),
            "Markdown (*.md *.txt);;所有文件 (*.*)")
        if not new_path:
            return
        self.tabs.setCurrentIndex(0)
        self.chat_display.append(
            f"\n▶ 正在比对考纲：\n   基准: {old_path}\n   最新: {new_path} ...")
        self.chat_display.append(services.diff_syllabus(self.workspace_root, old_path, new_path))

    def _run_compare_from_dialog(self):
        """双校对标：显式询问第二所高校与专业，避免沿用残留默认值。"""
        info = self.config.get("study_plan", {})
        s1 = info.get("school") or self.config.get("target_school") or "目标院校"
        s2, ok1 = QInputDialog.getText(self, "双校对标", "请输入第二所高校:", text="")
        if not ok1 or not s2.strip():
            self.chat_display.append("\n[!] 双校对标已取消：未指定第二所高校。")
            return
        mj, ok2 = QInputDialog.getText(
            self, "双校对标", "请输入专业关键词（可选）:",
            text=info.get("major") or self.config.get("target_major") or "")
        if not ok2:
            return
        major = mj.strip() or info.get("major") or self.config.get("target_major") or ""
        report, saved = services.compare_schools(self.workspace_root, s1, s2.strip(), major)
        self.tabs.setCurrentIndex(3)
        self.intel_display.append(f"\n{report}")
        if saved:
            self.intel_display.append(f"\n[+] 双校对标研报已落盘: {saved}")
            self.chat_display.append(f"\n[+] 双校对标研报已落盘: {saved}")

    def _open_wechat_search_dialog(self):
        try:
            from gui.widgets.wechat_search_dialog import WeChatSearchDialog
        except ImportError:  # pragma: no cover
            from tools.gui.widgets.wechat_search_dialog import WeChatSearchDialog  # type: ignore
        WeChatSearchDialog(self).exec()

    def _generate_error_quiz(self):
        display, saved = services.make_error_quiz(self.workspace_root)
        if display.startswith("[×]"):
            QMessageBox.warning(self, "提示", display)
            return
        self.error_info.setPlainText(self.error_info.toPlainText() + display)
        self.chat_display.append(f"\n[√] 错题盲盒自测卷已生成: {saved}\n")

    # ════════════════════════════════════════════════════════════
    # 私教工作线程
    # ════════════════════════════════════════════════════════════

    def _on_send_message(self):
        text = self.input_box.text().strip()
        if not text:
            return

        # [P1 修复·D1] 二次交互必现 RuntimeError 的根因链：
        # 上一轮 worker 结束后被 deleteLater() 销毁底层 C++ 对象，而 self.agent_worker
        # 仍指向该悬垂包装器 → 本轮 isRunning() 直接抛
        # "RuntimeError: Internal C++ object (AgentWorker) already deleted"，
        # 槽内异常被 Qt/PySide 静默吞掉 → 用户消息不上屏、无回复、无报错。
        # 修复：① 访问前做 RuntimeError 兜底并就地清理悬垂引用；② 结束后不再 deleteLater。
        worker = getattr(self, "agent_worker", None)
        if worker is not None:
            try:
                still_running = worker.isRunning()
            except RuntimeError:
                still_running = False
                self.agent_worker = None
                worker = None
            if still_running:
                self.chat_display.append(
                    "\n[!] 私教仍在思考中，请等待本轮回复完成后再发送下一条指令。")
                return

        self.input_box.clear()
        self.chat_display.append(f"\n你: {text}\n私教:")

        try:
            from gui.workers.agent_worker import AgentWorker
        except ImportError:  # pragma: no cover
            from tools.gui.workers.agent_worker import AgentWorker  # type: ignore

        self.agent_worker = AgentWorker(self.config, text)
        self._worker_refs.append(self.agent_worker)
        # [S3 改善·流式输出] 原先只有一次性 finished_signal，学员盯着空白等几十秒；现逐段追加。
        self.agent_worker.chunk_signal.connect(self._on_agent_chunk)
        self.agent_worker.finished_signal.connect(self._on_agent_reply)
        self.agent_worker.finished.connect(self._on_agent_finished)
        self._streamed = False
        self.agent_worker.start()

    def _on_agent_chunk(self, chunk: str):
        """流式片段：直接插入光标处，不另起段落（保持一段话连续）。
        """
        if not chunk:
            return
        self._streamed = True
        cur = self.chat_display.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.chat_display.setTextCursor(cur)
        self.chat_display.insertPlainText(chunk)
        self.chat_display.ensureCursorVisible()

    def _on_agent_finished(self):
        """[P1 修复·D1] 线程结束后只释放"当前活跃"语义，不再 deleteLater。"""
        w = self.sender()
        if w is None:
            return
        if w in self._worker_refs:
            self._worker_refs.remove(w)
        if getattr(self, "agent_worker", None) is w:
            self.agent_worker = None

    def _on_agent_reply(self, reply: str):
        """收尾：已流式输出过就不再重复整段；未流式（本地兜底路径）才整段补上。

        这样两类路径都能正确显示，且不会把答案打两遍。
        """
        if getattr(self, "_streamed", False):
            self.chat_display.append("\n" + "-" * 50)
        else:
            self.chat_display.append(f"\n{reply}\n" + "-" * 50)
        self._streamed = False

    # ════════════════════════════════════════════════════════════
    # 定时器与生命周期
    # ════════════════════════════════════════════════════════════

    def _init_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start(60000)

    def _on_timer_tick(self):
        # [缺陷修复·死数字 + 跨天不刷新] 旧实现只刷任务进度，头部倒计时永不更新；
        # 挂机跨天后任务清单也仍是昨天的。现每次刷新倒计时，跨天则整表重读。
        self._sync_header_text()
        today = date.today()
        if today != self._today:
            self._today = today
            self._refresh_all()
        else:
            self._load_today_task_progress()

    def closeEvent(self, event):
        theme_apply.write_pref(theme_apply.KEY_LAST_TAB, self.tabs.currentIndex())
        theme_apply.write_geometry(self)
        super().closeEvent(event)


__all__ = ["FunctionCard", "MainWindow", "TAB_TITLES"]
