# -*- coding: utf-8 -*-
"""
考研学习链 GUI 主窗口 (MainWindow)

[本文件职责] 只做「组装界面 + 事件分发 + 生命周期」。

分层（对应 agent.md 的单一职责与领域物理隔离）：
    views/        构建控件与连线（header / nav_rail / 四个页面视图）
    services/     取数据与调后端，纯数据、无 Qt 控件、可离屏单测
    theme_apply   主题解析应用 + QSettings 偏好持久化
    widgets/      可复用控件（KYNavRail / KYCard / ChatView / FunctionCard …）

[P2 导航架构] 原「顶部 10 卡 2×5 平铺 + 4 页签」两套导航收敛为
**左侧 rail（视图组 + 工具组）+ 命令面板（Ctrl+K）**。页面容器仍是 QTabWidget
（tabBar 隐藏、导航交给 rail），故 ``win.tabs`` / ``win.tab_widget`` 的
count()==4 等既有契约零破坏。

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
import time
from datetime import date
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QInputDialog, QMainWindow, QMessageBox,
    QTabWidget, QVBoxLayout, QWidget,
)

ROOT = resolve_workspace_root(__file__)
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

#: [修复④] 恢复会话时载入聊天页的消息条数上限（防超长会话卡 UI）。与
#: ``session_log.RESUME_TAIL_MESSAGES=12`` 是两回事：后者是 AgentRunner 重建
#: 上下文用的条数，这里是「给人看」的回放条数。
RESTORE_TAIL_MESSAGES = 60

#: 页面标题的唯一真源在 views/nav_rail.NAV_VIEWS（rail 的「视图」组同源）
TAB_TITLES = tuple(title for _icon, title in views.nav_rail.NAV_VIEWS)


class MainWindow(QMainWindow):
    def __init__(self, parent=None, workspace_root=None):
        super().__init__(parent)
        self.workspace_root = Path(workspace_root) if workspace_root else ROOT
        # [P0 修复] 私教工作线程强引用池与当前线程句柄。
        # QThread 无 parent，若仅靠 self.agent_worker 单引用持有，
        # 再次发送时被覆盖会导致运行中线程对象被 GC 销毁、进程直接崩溃。
        self.agent_worker = None
        self._worker_refs = []
        # [缺陷修复·审批"本会话记住"跨消息失效] 进程级审批信任集：GUI 每条
        # 消息新建 AgentWorker → 新建 GuiApproval，信任集必须由窗口持有并
        # 透传（AgentWorker → GuiApproval → PermissionManager 共享同一 set），
        # 否则勾选"本会话记住"只在本条消息内生效，下一条又会弹卡。
        self._session_allowed_tools: set = set()
        # Keep modal entry points alive and prevent duplicate nested dialogs
        # when a toolbar click races the automatic onboarding timer.
        self._settings_dialog = None
        self._onboarding_wizard = None
        # [轻微泄漏修复] 微信检索对话框的唯一实例（惰性创建后长期复用）。
        # 旧实现 ``WeChatSearchDialog(self).exec()`` 用临时对象弹窗：exec() 返回后
        # Python 引用即失效，但对话框是主窗口的 Qt 子对象 → 每打开一次就留下一个
        # 隐藏的 QDialog 直到主窗口销毁。
        self._wechat_dialog = None
        self._today = date.today()
        #: 本轮是否已流式输出过（决定收尾时是否补整段，避免答案打两遍）
        self._streamed = False
        # [K7-U1] 关窗 SessionEnd：仅当本窗口真实发生过 Agent 会话（发过消息且
        # 启动过 AgentRunner）才在关窗时触发一次日终复盘钩子；打开即关的「空窗」
        # 不触发（避免无意义写盘/IM 推送）。
        self._agent_session_ran = False
        self._session_end_fired = False

        self.setWindowTitle("考研学习链 · 全科智能私教中枢")
        icon_path = self.workspace_root / "docs" / "assets" / "logo" / "logo.png"
        if not icon_path.exists():
            icon_path = self.workspace_root / "docs" / "assets" / "logo.png"
        if not icon_path.exists():
            icon_path = self.workspace_root / "docs" / "assets" / "favicon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(1180, 780)
        self._load_config()
        self._theme = self._apply_initial_theme()
        self._init_ui()
        self._init_timer()
        self._refresh_all()
        theme_apply.restore_geometry(self)

        # [P2-9 收尾·钩子位置] 退出收尾钩子必须在**启动时**就装好，而不是等
        # 用户真的发起过一次检索（旧实现只在 WeChatSearchDialog._on_search 里
        # 调 _install_quit_hook）。否则「开着检索对话框直接退出应用」这条路径
        # 上 aboutToQuit 上没有收尾回调，运行中的 QThread 会随主窗口析构被销毁
        # → "QThread: Destroyed while thread is still running" → 进程 abort。
        try:
            try:
                from tools.gui.widgets.wechat_search_dialog import _install_quit_hook
            except ImportError:  # pragma: no cover
                from gui.widgets.wechat_search_dialog import _install_quit_hook  # type: ignore
            _install_quit_hook()
        except Exception:  # pragma: no cover - 钩子装不上不应阻断主界面启动
            pass

        # 首次进入或配置缺失时自动唤起新手引导与学情建档向导
        if hasattr(services, "is_unconfigured") and services.is_unconfigured(self.workspace_root):
            QTimer.singleShot(150, self._open_onboarding_wizard)

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

        body = QHBoxLayout()
        body.setSpacing(14)

        # 左侧分组导航 rail（视图组切页 + 工具组触发动作），回填 _feature_buttons 契约
        self.nav_rail = views.nav_rail.build(self)
        body.addWidget(self.nav_rail)

        # 页面容器：仍是 QTabWidget，但 tabBar 隐藏、导航交给 rail ——
        # 既保留 win.tabs / win.tab_widget（count()==4、tabBar().count()==4）契约，
        # 又不再出现「rail + 横向页签」两套并列导航。
        self.tabs = QTabWidget()
        self.tab_widget = self.tabs          # 对外契约名
        self.tabs.tabBar().hide()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        for builder, title in zip(
            (views.chat_tab.build, views.task_tab.build,
             views.error_tab.build, views.intel_tab.build),
            TAB_TITLES,
        ):
            self.tabs.addTab(builder(self), title)
        self.tabs.setCurrentIndex(self._restore_last_tab())
        body.addWidget(self.tabs, stretch=1)

        main_layout.addLayout(body, stretch=1)

        # Ctrl+K 命令面板（惰性创建后长期复用，见 _open_command_palette）
        self._palette = None
        self._palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._palette_shortcut.activated.connect(self._open_command_palette)

    def _on_tab_changed(self, index: int):
        """页面切换 → 同步 rail 激活态（rail 点击 / 工具动作 / 恢复上次页 都走这里）。"""
        rail = getattr(self, "nav_rail", None)
        if rail is not None:
            rail.set_active_index(index)

    def _on_nav_view_clicked(self, index: int):
        """rail「视图」组点击 → 切页。"""
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def _on_example_prompt(self, prompt: str):
        """对话页空状态示例：只填入输入框（不直接发送，避免误触发起计费调用）。"""
        self.input_box.setText(prompt)
        self.input_box.setFocus()

    # ════════════════════════════════════════════════════════════
    # 命令面板（Ctrl+K）
    # ════════════════════════════════════════════════════════════

    def _open_command_palette(self):
        """打开命令面板：列出全部页面与工具动作，回车执行（Raycast 模式）。

        复用同一实例（同 ``_wechat_dialog`` 的理由：避免每次弹窗都留一个隐藏子对象）；
        用 ``open()`` 而非 ``exec()`` —— 窗口模态但**不阻塞调用方**，
        离屏测试可以直接驱动它。
        """
        if self._palette is None:
            self._palette = views.nav_rail.build_palette(self)
        self._palette.input.clear()
        self._palette.refresh("")
        self._palette.open()
        self._palette.input.setFocus()

    def _on_palette_activated(self, key: str):
        """命令面板执行：``view:`` 前缀切页，``tool:`` 前缀走既有动作分发。"""
        view_prefix = views.nav_rail.VIEW_PREFIX
        tool_prefix = views.nav_rail.TOOL_PREFIX
        if key.startswith(view_prefix):
            try:
                index = int(key[len(view_prefix):])
            except ValueError:            # pragma: no cover - 防御非法 key
                return
            self._on_nav_view_clicked(index)
        elif key.startswith(tool_prefix):
            self._on_card_clicked(key[len(tool_prefix):])

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
        """把服务层取到的头部数据写进控件（含倒计时与个性化元标签）。

        优先与当前内存中的 self.config 合并，确保设置与向导修改即刻毫秒级生效。
        例外：``days_left`` 是派生字段（由 exam_date 推算），不走内存/存储快照
        —— 见下方缺陷修复注释。
        """
        info = services.header_info(self.workspace_root)
        cfg = getattr(self, "config", None) or {}
        plan = cfg.get("study_plan") or {}
        school = plan.get("school") or cfg.get("target_school") or info["school"]
        major = plan.get("major") or cfg.get("target_major") or info["major"]
        style = cfg.get("coaching_style") or plan.get("style_name") or info["style"]
        style_short = style.split("·")[0] if "·" in style else (style.split()[0] if style else info["style_short"])

        # [缺陷修复·顶栏陈旧倒计时] 旧实现优先读 plan["days_left"]（ky_config.json
        # 存储快照），而快照只在「保存设置」时补算（settings.py），挂机跨天/日常
        # 启动永不刷新 —— 真机实测：配置存 90、真实 80，顶栏与今日页卡片同屏
        # 互相矛盾。与 G2（study_planner）/ load_dashboard_state 同口径：现算。
        self.countdown_label.setText(f"初试倒计时: {info['days_left']} 天")
        self.meta_label.setText(
            f"目标: {school} · {major}  |  "
            f"风格: {style_short}")
        self._sync_theme_button()

    def _sync_theme_button(self):
        self.theme_btn.setText("深色" if self._theme.mode == "light" else "浅色")

    def _refresh_card_icons(self):
        """用当前主题的主色重渲染导航图标（SVG 跟随主题色）。

        rail 已覆盖全部功能卡（``win.feature_cards`` 即 ``rail.tool_items``）
        与 4 个视图项；无 rail 时（理论上不会发生）退回旧路径。
        """
        color = self._theme.color("acc")
        rail = getattr(self, "nav_rail", None)
        if rail is not None:
            rail.refresh_icons(color)
            return
        for card in getattr(self, "feature_cards", []):
            card.refresh_icon(color)

    def _open_settings(self):
        """打开主题 L2 旋钮设置面板。"""
        try:
            from gui.widgets.settings_dialog import SettingsDialog
        except ImportError:  # pragma: no cover
            from tools.gui.widgets.settings_dialog import SettingsDialog  # type: ignore
        dialog = self._settings_dialog
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return dialog
        dialog = SettingsDialog(self, parent=self)
        self._settings_dialog = dialog
        dialog.finished.connect(
            lambda _result, d=dialog: setattr(self, "_settings_dialog", None)
            if self._settings_dialog is d else None
        )
        dialog.exec()
        return dialog

    def _open_onboarding_wizard(self):
        """打开 5 步新手引导与个性化学情建档向导。"""
        try:
            from gui.widgets.onboarding_wizard import OnboardingWizard
        except ImportError:  # pragma: no cover
            from tools.gui.widgets.onboarding_wizard import OnboardingWizard  # type: ignore
        wizard = self._onboarding_wizard
        if wizard is not None and wizard.isVisible():
            wizard.raise_()
            wizard.activateWindow()
            return wizard.result()
        wizard = OnboardingWizard(self, workspace_root=self.workspace_root)
        self._onboarding_wizard = wizard
        wizard.config_saved.connect(self.on_config_updated)
        wizard.finished.connect(
            lambda _result, d=wizard: setattr(self, "_onboarding_wizard", None)
            if self._onboarding_wizard is d else None
        )
        result = wizard.exec()
        # Some offscreen test adapters implement exec() as show()+return and
        # therefore never emit finished; do not leave a stale visible guard.
        if not wizard.isVisible() and self._onboarding_wizard is wizard:
            self._onboarding_wizard = None
        return result

    def on_config_updated(self, config: dict):
        """向导或设置中心保存后即时热更新 GUI 界面，无需重启。"""
        # [问题3/6 修复] 剥离向导返回的联动同步旁路警告（仅供弹窗展示，
        # 不是配置项；留在内存配置里会在下次保存时被写进 ky_config.json）。
        config = dict(config)
        sync_warnings = config.pop("_sync_warnings", None)
        self.config = config
        self._sync_header_text()
        self._load_today_task_progress()
        self._refresh_error_tab()
        self._refresh_intel_tab()
        plan = config.get("study_plan") or {}
        school = plan.get("school") or config.get("target_school") or "目标院校"
        major = plan.get("major") or config.get("target_major") or "报考专业"
        days = plan.get("days_left", "")
        self.chat_display.append(f"\n[√] 考研个性化档案已更新并即时生效：{school} · {major} (初试倒计时 {days} 天)")
        if sync_warnings:
            self.chat_display.append(
                "\n[!] 建档联动同步存在未完成项：\n"
                + "\n".join(f"    • {w}" for w in sync_warnings))

    def _toggle_theme(self):
        """在明暗预设间切换并持久化（改造前重启即回退深色）。"""
        app = QApplication.instance()
        if app is None:
            return
        target = theme_apply.next_preset(self._theme.name)
        self._theme = theme_apply.set_preset(app, target, self.workspace_root)
        self._sync_theme_button()
        self._refresh_card_icons()
        # [阶段 D · 双态折叠] 切主题重设了全局 styleSheet：对 rail 幂等重放一次
        # 折叠态（视图项 unpolish/polish），确保 56px 图标栏的
        # #NavItem[collapsed="true"] 属性选择器在重 polish 后仍然生效。
        rail = getattr(self, "nav_rail", None)
        if rail is not None:
            rail.set_expanded(rail.is_expanded())

    # ════════════════════════════════════════════════════════════
    # 数据刷新（全部委托 services，本类不自行解析文件）
    # ════════════════════════════════════════════════════════════

    def _refresh_all(self):
        # [PF-1 性能修复·审计 2026-09-30] 本方法经 header_info / subject_progress /
        # error_queue_cards / error_queue_markdown 连调 services 多条路径，原先
        # 每次刷新触发 5 次 load_state（约 20 次状态文件读）+ 2 次错题本全扫。
        # 现由 services 层（gui/services/dashboard.py）按输入文件 mtime 指纹做
        # memo（load_state 缓存 + error_queue_cards 指纹缓存），调用结构不变、
        # 一次刷新收敛为 1 次真实读盘 —— 故此处不再做入口透传改造。
        self._sync_header_text()
        self._load_today_task_progress()
        self._refresh_error_tab()
        self._refresh_intel_tab()

    def _load_today_task_progress(self):
        """刷新各科今日任务进度条与概览统计块（与 CLI / TUI 同源的共享解析器）。"""
        subjects = tuple(services.subject_progress(self.workspace_root))
        for subject in subjects:
            bar = self.task_progress_bars.get(subject.key)
            label = self.task_count_labels.get(subject.key)
            if bar is not None:
                bar.setValue(subject.pct)
            if label is not None:
                label.setText(subject.summary_text)
        self._sync_task_stat_tiles(subjects)

    def _sync_task_stat_tiles(self, subjects):
        """概览统计块（大数字 + caption）：今日完成度 / 待复测错题 / 初试倒计时。"""
        tiles = getattr(self, "task_stat_tiles", None)
        if not tiles:
            return
        pcts = [subject.pct for subject in subjects]
        average = int(round(sum(pcts) / len(pcts))) if pcts else 0
        if "progress" in tiles:
            tiles["progress"].set_value(f"{average}%")
        if "due" in tiles:
            tiles["due"].set_value(str(len(services.error_queue_cards(self.workspace_root))))
        if "countdown" in tiles:
            tiles["countdown"].set_value(str(services.header_info(self.workspace_root)["days_left"]))

    def _refresh_error_tab(self):
        """刷新错题本：卡片列表（主视图）+ Markdown 原始档案（折叠兜底）。"""
        self.error_info.setMarkdown(services.error_queue_markdown(self.workspace_root))
        try:
            from tools.gui.views.error_tab import render_error_cards
        except ImportError:  # pragma: no cover - 脚本式运行
            from gui.views.error_tab import render_error_cards  # type: ignore
        render_error_cards(self)

    def _toggle_error_raw(self, visible: bool):
        """切换「原始档案」Markdown 视图（默认折叠，卡片视图为主）。"""
        self.error_info.setVisible(bool(visible))

    def _refresh_intel_tab(self):
        self.intel_display.setMarkdown(services.intel_markdown(self.workspace_root))
        try:
            from tools.gui.views.intel_tab import update_api_status_banner
            update_api_status_banner(self)
        except Exception:
            pass

    # ════════════════════════════════════════════════════════════
    # 事件分发
    # ════════════════════════════════════════════════════════════

    # [S8 修复] 此处曾有一份 _on_quick_command 重复定义（后者覆盖前者，
    # 功能一致但留死代码）。唯一实现见下方「私教工作线程」分区。

    def _on_upload_image(self):
        """选择答卷或错题图片并填入输入框，准备发送给视觉私教批改。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择作业/草稿/错题截图", str(self.workspace_root),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*.*)"
        )
        if not path:
            return
        norm_path = path.replace("\\", "/")
        cur = self.input_box.text().strip()
        if cur:
            self.input_box.setText(f"{cur} /img \"{norm_path}\"")
        else:
            self.input_box.setText(f"/img \"{norm_path}\" 请私教审阅批改我的推导过程，按考研大纲指出采分点与失分漏洞")
        self.input_box.setFocus()
        self.chat_display.append(f"\n[📷 图片已挂载]: {Path(path).name}\n    可直接点击「发送」或补充具体疑问后开始批改。")

    def _on_upload_file(self):
        """选择考研资料或真题讲义文件并挂载。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择考研资料/大纲/真题文档", str(self.workspace_root),
            "考研文档 (*.md *.txt *.pdf *.docx *.doc *.json);;所有文件 (*.*)"
        )
        if not path:
            return
        norm_path = path.replace("\\", "/")
        cur = self.input_box.text().strip()
        if cur:
            self.input_box.setText(f"{cur} /file \"{norm_path}\"")
        else:
            self.input_box.setText(f"/file \"{norm_path}\" 请私教精读分析该资料的核心考点与复习建议")
        self.input_box.setFocus()
        self.chat_display.append(f"\n[📎 文件已挂载]: {Path(path).name}\n    可直接点击「发送」或补充提问，私教将读取内容并针对性指导。")

    def _run_action_to_display(self, alias: str, display_widget, brief_to_chat: bool = True):
        """异步执行后端模块并把输出回显到指定文本框（绝不阻塞 GUI 主线程）。"""
        display_widget.append(f"\n▶ 正在启动模块 [{alias}] ...")

        try:
            from tools.gui.workers.intel_worker import IntelTaskWorker
        except ImportError:
            from gui.workers.intel_worker import IntelTaskWorker

        worker = IntelTaskWorker("action", self.workspace_root, {"alias": alias})
        self._worker_refs.append(worker)

        worker.log_signal.connect(lambda text: display_widget.append(text))
        def _on_done(out, saved):
            if out:
                display_widget.append(f"\n{out}\n")
            display_widget.append(f"[√] 模块 [{alias}] 执行完毕。\n" + "-" * 40)
            if brief_to_chat:
                self.chat_display.append(f"\n[√] 模块 [{alias}] 已在对应页面执行完毕，详见上方分页。")

        def _on_err(err):
            display_widget.append(f"\n[×] 模块 [{alias}] 执行异常: {err}\n")

        worker.finished_signal.connect(_on_done)
        worker.error_signal.connect(_on_err)
        worker.finished.connect(lambda w=worker: self._worker_refs.remove(w) if w in self._worker_refs else None)
        worker.start()

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
            self._run_scout_from_dialog()
        elif alias == "compare":
            self._run_compare_from_dialog()
        elif alias == "rag":
            # [2026-10-06 补 GUI 入口] 检索需要关键词输入，故不能走 else 分支的
            # 无参异步执行；建索引无入参，直接后台跑。
            self._run_rag_from_dialog()
        elif alias == "index":
            self._run_index_action()
        else:
            self.tabs.setCurrentIndex(0)
            self._run_action_to_display(alias, self.chat_display, brief_to_chat=False)

    def _run_rag_from_dialog(self):
        """本地知识库检索：先取检索词，再后台执行（建库/加载模型可能耗时）。

        走 ``gui.services.rag_search``（内部复用 ``ky rag`` 的
        ``cli.commands.search.run_rag_search``），不在 GUI 侧另写检索逻辑。
        """
        # [为什么默认值留空] 知识库按「考点」切片，预填「专业名+代码」这类串
        # 几乎必然 0 命中，考生会以为检索坏了。宁可让考生自己敲一个
        # 考点词（对话框标题里已给示例）。
        # [隐私] 举例一律用**通用学科词**（如「剩余价值」），不得写入考生真实
        # 专业代码/校名——注释同样会被编译进发布包 exe 的 PYZ 层，且
        # tests/test_privacy_identity_rules.py 会为此报红。
        query, ok = QInputDialog.getText(
            self, "本地知识库检索",
            "请输入要检索的考点关键词（如：剩余价值 / 矛盾的普遍性）:"
        )
        if not ok or not query.strip():
            self.chat_display.append("\n[i] 本地检索已取消：未输入检索关键词。")
            return
        self.tabs.setCurrentIndex(0)
        self.chat_display.append(
            f"\n▶ 正在检索本地知识库 [{query.strip()}] ...（只读本地索引，不联网）")

        try:
            from tools.gui.workers.intel_worker import IntelTaskWorker
        except ImportError:  # pragma: no cover
            from gui.workers.intel_worker import IntelTaskWorker  # type: ignore

        worker = IntelTaskWorker("rag_search", self.workspace_root,
                                 {"query": query.strip()})
        self._worker_refs.append(worker)
        worker.log_signal.connect(lambda text: self.chat_display.append(text))
        worker.finished_signal.connect(
            lambda out, saved: self.chat_display.append(out if out else "（无输出）"))
        worker.error_signal.connect(
            lambda err: self.chat_display.append(f"\n[×] 本地检索异常: {err}\n"))
        worker.finished.connect(
            lambda w=worker: self._worker_refs.remove(w) if w in self._worker_refs else None)
        worker.start()

    def _run_index_action(self):
        """本地知识库建索引：无入参，后台执行（切片 + 可选向量编码，耗时较长）。

        走 ``gui.services.build_index``（复用 ``ky index`` 的唯一实现）。
        不预建空库、不联网、不代造资料——资料为空时后端如实提示「没有找到
        可索引的文档」，此处原样透出，不美化。
        """
        self.tabs.setCurrentIndex(0)
        self.chat_display.append(
            "\n▶ 正在后台构建本地知识库索引（只读本地院校库与各科 参考资料/，不联网）...")

        try:
            from tools.gui.workers.intel_worker import IntelTaskWorker
        except ImportError:  # pragma: no cover
            from gui.workers.intel_worker import IntelTaskWorker  # type: ignore

        worker = IntelTaskWorker("index_build", self.workspace_root, {})
        self._worker_refs.append(worker)
        worker.log_signal.connect(lambda text: self.chat_display.append(text))
        worker.finished_signal.connect(
            lambda out, saved: self.chat_display.append(out if out else "（无输出）"))
        worker.error_signal.connect(
            lambda err: self.chat_display.append(f"\n[×] 建索引异常: {err}\n"))
        worker.finished.connect(
            lambda w=worker: self._worker_refs.remove(w) if w in self._worker_refs else None)
        worker.start()

    def _run_scout_from_dialog(self):
        """院校侦察：显式确认或输入目标高校，后台异步执行，绝不卡死界面。"""
        info = self.config.get("study_plan", {})
        default_sch = info.get("school") or self.config.get("target_school") or ""
        if default_sch in ("未指定", "目标院校"):
            default_sch = ""
        sch, ok = QInputDialog.getText(
            self, "目标院校深度侦察", "请输入要侦察的高校名称:", text=default_sch
        )
        if not ok or not sch.strip():
            self.intel_display.append("\n[i] 院校侦察已取消：未指定目标高校。")
            return
        sch = sch.strip()
        mj, ok2 = QInputDialog.getText(
            self, "目标院校深度侦察", "请输入专业关键词（可选）:",
            text=info.get("major") or self.config.get("target_major") or ""
        )
        if not ok2:
            self.intel_display.append("\n[i] 院校侦察已取消：未指定专业关键词。")
            return
        major = mj.strip() or info.get("major") or self.config.get("target_major") or ""

        self.tabs.setCurrentIndex(3)
        self.intel_display.append(f"\n▶ 正在启动【{sch}】深度考情与社媒口碑侦察 (专业: {major or '统考科目'})...\n")

        try:
            from tools.gui.workers.intel_worker import IntelTaskWorker
        except ImportError:
            from gui.workers.intel_worker import IntelTaskWorker

        worker = IntelTaskWorker("action", self.workspace_root, {
            "alias": "scout", "school": sch, "major": major
        })
        self._worker_refs.append(worker)

        worker.log_signal.connect(lambda text: self.intel_display.append(text))
        def _on_scout_done(out, saved):
            if out:
                self.intel_display.append(f"\n{out}\n")
            self.intel_display.append(f"[√] 目标院校【{sch}】深度侦察完成。\n" + "-" * 40)
            self.chat_display.append(f"\n[√] 目标院校【{sch}】深度侦察已在研招情报页完成。")

        def _on_scout_err(err):
            self.intel_display.append(f"\n[×] 院校侦察执行异常: {err}\n")

        worker.finished_signal.connect(_on_scout_done)
        worker.error_signal.connect(_on_scout_err)
        worker.finished.connect(lambda w=worker: self._worker_refs.remove(w) if w in self._worker_refs else None)
        worker.start()

    def _run_ingest_from_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要切片的真题 / 讲义文件", str(self.workspace_root),
            "题库文件 (*.md *.txt *.pdf);;所有文件 (*.*)")
        if not path:
            self.chat_display.append("\n[i] 切片入库已取消：未选择待切片文件。")
            return
        self.tabs.setCurrentIndex(0)
        self.chat_display.append(f"\n▶ 正在切片入库 [{path}] ...")
        try:
            from tools.gui.workers.intel_worker import IntelTaskWorker
        except ImportError:  # pragma: no cover
            from gui.workers.intel_worker import IntelTaskWorker  # type: ignore

        worker = IntelTaskWorker("ingest_file", self.workspace_root, {"path": path})
        self._worker_refs.append(worker)
        worker.log_signal.connect(lambda text: self.chat_display.append(text))
        worker.finished_signal.connect(lambda out, saved: self.chat_display.append(out))
        worker.error_signal.connect(lambda err: self.chat_display.append(f"\n[×] 切片入库异常: {err}\n"))
        worker.finished.connect(lambda w=worker: self._worker_refs.remove(w) if w in self._worker_refs else None)
        worker.start()

    def _run_diff_from_dialog(self):
        """考纲 Diff：必须选到两个真实文件，严禁伪造变动。"""
        old_path, _ = QFileDialog.getOpenFileName(
            self, "选择【基准(旧)】考纲文件", str(self.workspace_root / "04-专业课"),
            "Markdown (*.md *.txt);;所有文件 (*.*)")
        if not old_path:
            self.chat_display.append("\n[i] 考纲 Diff 已取消：未选择基准（旧）考纲。")
            return
        new_path, _ = QFileDialog.getOpenFileName(
            self, "选择【最新】考纲文件", str(Path(old_path).parent),
            "Markdown (*.md *.txt);;所有文件 (*.*)")
        if not new_path:
            self.chat_display.append("\n[i] 考纲 Diff 已取消：未选择最新考纲。")
            return
        self.tabs.setCurrentIndex(0)
        self.chat_display.append(
            f"\n▶ 正在比对考纲：\n   基准: {old_path}\n   最新: {new_path} ...")
        try:
            from tools.gui.workers.intel_worker import IntelTaskWorker
        except ImportError:  # pragma: no cover
            from gui.workers.intel_worker import IntelTaskWorker  # type: ignore

        worker = IntelTaskWorker("diff_syllabus", self.workspace_root,
                                 {"old_path": old_path, "new_path": new_path})
        self._worker_refs.append(worker)
        worker.log_signal.connect(lambda text: self.chat_display.append(text))
        worker.finished_signal.connect(lambda out, saved: self.chat_display.append(out))
        worker.error_signal.connect(lambda err: self.chat_display.append(f"\n[×] 考纲比对异常: {err}\n"))
        worker.finished.connect(lambda w=worker: self._worker_refs.remove(w) if w in self._worker_refs else None)
        worker.start()

    def _run_compare_from_dialog(self):
        """双校对标：显式询问第一所与第二所高校及专业，后台异步执行，绝不卡死界面。"""
        info = self.config.get("study_plan", {})
        default_s1 = info.get("school") or self.config.get("target_school") or ""
        if default_s1 in ("未指定", "目标院校"):
            default_s1 = ""
        s1, ok1 = QInputDialog.getText(
            self, "双校对标", "请输入第一所高校 (您的目标高校):", text=default_s1
        )
        if not ok1 or not s1.strip():
            self.chat_display.append("\n[!] 双校对标已取消：未指定第一所高校。")
            return
        s1 = s1.strip()
        # [P2-11 修复·备选校未联动] 建档向导收集的备选院校（study_plan.backup_school）
        # 此前只被 TUI 对标读取，GUI 对标弹窗第二校恒为空。现预填为默认值 ——
        # 考生一键回车即可对「目标校 vs 备选校」发起对标。
        default_s2 = str(
            info.get("backup_school")
            or self.config.get("backup_school")
            or self.config.get("backup_target")
            or ""
        ).strip()
        if default_s2 in ("未指定", "目标院校"):
            default_s2 = ""
        s2, ok2 = QInputDialog.getText(
            self, "双校对标",
            "请输入第二所高校 (对比高校):" if not default_s2
            else "请输入第二所高校 (对比高校，已预填档案中的备选院校):",
            text=default_s2
        )
        if not ok2 or not s2.strip():
            self.chat_display.append("\n[!] 双校对标已取消：未指定第二所高校。")
            return
        s2 = s2.strip()
        mj, ok3 = QInputDialog.getText(
            self, "双校对标", "请输入专业关键词（可选）:",
            text=info.get("major") or self.config.get("target_major") or "")
        if not ok3:
            self.chat_display.append("\n[i] 双校对标已取消：未指定专业关键词。")
            return
        major = mj.strip() or info.get("major") or self.config.get("target_major") or ""

        self.tabs.setCurrentIndex(3)
        self.intel_display.append(f"\n▶ 正在启动双校考情深度对标：【{s1}】 vs 【{s2}】({major or '统考科目'})...\n")

        try:
            from tools.gui.workers.intel_worker import IntelTaskWorker
        except ImportError:
            from gui.workers.intel_worker import IntelTaskWorker

        worker = IntelTaskWorker("compare", self.workspace_root, {
            "school1": s1, "school2": s2, "major": major
        })
        self._worker_refs.append(worker)

        worker.log_signal.connect(lambda text: self.intel_display.append(text))
        def _on_compare_done(report, saved):
            self.intel_display.append(f"\n{report}\n")
            if saved:
                self.intel_display.append(f"\n[+] 双校对标研报已落盘: {saved}")
                self.chat_display.append(f"\n[+] 双校对标研报已落盘: {saved}")
            self.intel_display.append("[√] 双校深度对标完成。\n" + "-" * 40)

        def _on_compare_err(err):
            self.intel_display.append(f"\n[×] 双校对标执行失败: {err}\n")

        worker.finished_signal.connect(_on_compare_done)
        worker.error_signal.connect(_on_compare_err)
        worker.finished.connect(lambda w=worker: self._worker_refs.remove(w) if w in self._worker_refs else None)
        worker.start()

    def _open_wechat_search_dialog(self):
        """弹出微信检索对话框 —— 全程复用同一个实例（不再每次新建）。

        [轻微泄漏修复] 旧实现 ``WeChatSearchDialog(self).exec()``：exec() 返回后
        临时对象失去 Python 引用，但它是主窗口的 Qt 子对象，于是每打开一次就多留
        一个隐藏的 QDialog，直到主窗口销毁。改为持有成员引用并复用，实例数恒为 1。

        [为什么不用 WA_DeleteOnClose] 该对话框的线程收尾（见
        ``wechat_search_dialog._shutdown_worker``）依赖对话框**对象存活期间**完成：
        超时停不掉时还要把 worker 改挂到 QApplication 上。删窗时机与线程状态耦合，
        而复用实例不引入任何新的析构路径，风险最低。

        [复用为什么安全] 每次关闭都走 ``closeEvent`` → ``_shutdown_worker``：
        线程要么已停止（``self.worker`` 是已结束的 QThread），要么被摘出并置 None。
        故再次 ``exec()`` 时不会出现「运行中的 QThread 被重新拉起/析构」。
        """
        try:
            from tools.gui.widgets.wechat_search_dialog import WeChatSearchDialog
        except ImportError:  # pragma: no cover
            from gui.widgets.wechat_search_dialog import WeChatSearchDialog  # type: ignore
        if self._wechat_dialog is None:
            self._wechat_dialog = WeChatSearchDialog(self)
        self._wechat_dialog.exec()

    #: 错题盲盒可选科目（key → 展示名，与 exam_composer.SUBJECT_DIRS 同键）。
    #: [缺陷修复·只抽专业课] 此前 IntelTaskWorker("error_quiz", ...) 不传 params，
    #: 固定回落 subject="pro" —— 英语/政治错题永远抽不到。现由考生选科目；
    #: 数学按 is_math_disabled 单源判定过滤（不考数学时不出现在选项里）。
    _QUIZ_SUBJECTS = (
        ("pro", "专业课"), ("eng", "英语"), ("pol", "思想政治理论"), ("math", "数学"),
    )

    def _quiz_subject_options(self):
        """按备考方案过滤错题盲盒可选科目。"""
        out = []
        for key, name in self._QUIZ_SUBJECTS:
            if key == "math":
                try:
                    try:
                        from cli.shared import is_math_disabled
                    except ImportError:  # pragma: no cover
                        from tools.cli.shared import is_math_disabled
                    if is_math_disabled(self.config):
                        continue
                except Exception:
                    pass
            out.append((key, name))
        return out

    def _generate_error_quiz(self):
        try:
            from tools.gui.workers.intel_worker import IntelTaskWorker
        except ImportError:  # pragma: no cover
            from gui.workers.intel_worker import IntelTaskWorker  # type: ignore

        options = self._quiz_subject_options()
        names = [name for _key, name in options]
        chosen, ok = QInputDialog.getItem(
            self, "错题盲盒自测卷", "选择要组卷的科目:", names, 0, False)
        if not ok or not chosen:
            self.chat_display.append("\n[i] 错题盲盒组卷已取消：未选择科目。")
            return
        subject = next(key for key, name in options if name == chosen)

        worker = IntelTaskWorker("error_quiz", self.workspace_root, {"subject": subject})
        self._worker_refs.append(worker)
        worker.log_signal.connect(lambda text: self.chat_display.append(text))

        def _on_done(display, saved):
            if display.startswith("[×]"):
                QMessageBox.warning(self, "提示", display)
                return
            self.error_info.setPlainText(self.error_info.toPlainText() + display)
            self.chat_display.append(f"\n[√] 错题盲盒自测卷已生成: {saved}\n")

        worker.finished_signal.connect(_on_done)
        worker.error_signal.connect(lambda err: self.chat_display.append(f"\n[×] 组卷异常: {err}\n"))
        worker.finished.connect(lambda w=worker: self._worker_refs.remove(w) if w in self._worker_refs else None)
        worker.start()

    # ════════════════════════════════════════════════════════════
    # 私教工作线程
    # ════════════════════════════════════════════════════════════

    def _on_quick_command(self, cmd: str):
        """响应私教快捷药丸点击（如：英语报到、政治报到、专业课报到、交作业等）。"""
        self.input_box.setText(cmd)
        self._on_send_message()

    def _on_send_message(self):
        text = self.input_box.text().strip()
        if not text:
            return

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
        self.chat_display.add_user_message(text)

        try:
            from tools.gui.workers.agent_worker import AgentWorker
        except ImportError:  # pragma: no cover
            from gui.workers.agent_worker import AgentWorker  # type: ignore

        self.agent_worker = AgentWorker(self.config, text,
                                        workspace_root=self.workspace_root,
                                        session_allowed_tools=self._session_allowed_tools)
        self._worker_refs.append(self.agent_worker)
        # [S3 改善·流式输出与中间态上屏] 实时追加思考链、工具调用与文字片段
        self.agent_worker.chunk_signal.connect(self._on_agent_chunk)
        self.agent_worker.step_signal.connect(self._on_agent_step)
        self.agent_worker.finished_signal.connect(self._on_agent_reply)
        self.agent_worker.finished.connect(self._on_agent_finished)
        # [K7-U1] 记录「真实 Agent 会话发生过」（关窗时据此触发 SessionEnd）
        self.agent_worker.session_ran_signal.connect(self._mark_agent_session_ran)
        self._streamed = False
        self.agent_worker.start()

    def _mark_agent_session_ran(self):
        """[K7-U1] 标记本窗口已发生过真实 Agent 会话（跨线程信号槽）。"""
        self._agent_session_ran = True

    def _on_stop_agent(self):
        """[缺陷修复·无法中断] 聊天区「停止」按钮：中止本轮回答并立即恢复可输入。

        语义分两层：
          * ``worker.cancel()`` 置取消位 —— 回调包装（_guarded_callback）会在
            AgentRunner 的下一个步骤边界抛 AgentCancelled，终止 run 主循环；
            LLM 请求在途期间无法打断，但请求返回后即终止。
          * ``agent_worker`` 立即置 None —— 用户无需等旧线程收尾即可发下一条
            消息；旧 worker 的迟到回复由 _on_agent_reply 的发送者校验丢弃。
        """
        worker = getattr(self, "agent_worker", None)
        if worker is None:
            self.chat_display.append("\n[i] 当前没有正在进行的回答。")
            return
        try:
            running = worker.isRunning()
        except RuntimeError:
            running = False
        if not running:
            self.agent_worker = None
            self.chat_display.append("\n[i] 当前没有正在进行的回答。")
            return
        try:
            worker.cancel()
        except Exception:
            pass
        self.agent_worker = None
        self.chat_display.append(
            "\n[i] 已停止本轮回答；私教将在当前请求返回后终止，可直接发送下一条消息。")

    # ════════════════════════════════════════════════════════════
    # 会话管理（历史 / 恢复 / 新建）——修复④
    # ════════════════════════════════════════════════════════════

    def _agent_is_running(self) -> bool:
        """当前是否有正在跑的私教 worker（含 RuntimeError 防御，同 _on_send_message）。"""
        worker = getattr(self, "agent_worker", None)
        if worker is None:
            return False
        try:
            return worker.isRunning()
        except RuntimeError:
            # 底层 C++ 对象已销毁：清掉句柄，按空闲处理
            self.agent_worker = None
            return False

    def _on_open_sessions(self):
        """「历史」按钮：打开会话列表；选中恢复时重建上下文并载入消息到聊天页。

        恢复动作由「进程内共享会话 id」驱动（AgentWorker._shared_session_id =
        所选 id）——后续每条消息新建的 worker 都会复用该 id，AgentRunner 构造
        时自动从日志重建 history（loop.py._restore_history_from_log）。
        """
        if self._agent_is_running():
            self.chat_display.append(
                "\n[!] 私教仍在回答中，请先停止或等待完成后再切换会话。")
            return
        try:
            from tools.gui.widgets.session_dialog import SessionDialog
        except ImportError:  # pragma: no cover
            from gui.widgets.session_dialog import SessionDialog  # type: ignore
        dialog = SessionDialog(self.workspace_root, parent=self)
        if not dialog.exec():
            return
        chosen = dialog.chosen_session_id()
        if not chosen:
            return
        try:
            from tools.gui.workers.agent_worker import AgentWorker
        except ImportError:  # pragma: no cover
            from gui.workers.agent_worker import AgentWorker  # type: ignore
        AgentWorker._shared_session_id = chosen
        self._apply_session_restore(chosen)

    def _apply_session_restore(self, sid: str):
        """把指定会话的历史消息回放到聊天页（尾部最近 RESTORE_TAIL_MESSAGES 条）。

        只消费 ``user`` / ``assistant`` 事件（与 resume 重建同口径，工具事件
        不上屏）；超长会话截断时先插一条系统气泡说明省略条数。
        """
        try:
            from tools.agent.session_log import SessionLog, load_events
        except ImportError:  # pragma: no cover
            from agent.session_log import SessionLog, load_events  # type: ignore
        self.chat_display.clear()
        messages = []
        try:
            log = SessionLog(workspace_root=self.workspace_root, session_id=sid)
            for evt in load_events(log.path):
                if not isinstance(evt, dict):
                    continue
                etype = evt.get("type")
                if etype not in ("user", "assistant"):
                    continue
                payload = evt.get("payload")
                content = payload.get("content") if isinstance(payload, dict) else None
                if isinstance(content, str) and content.strip():
                    messages.append((etype, content))
        except Exception:
            messages = []
        omitted = max(0, len(messages) - RESTORE_TAIL_MESSAGES)
        if omitted:
            self.chat_display.append(f"\n[i] （更早的 {omitted} 条消息已省略）")
        for role, content in messages[-RESTORE_TAIL_MESSAGES:]:
            if role == "user":
                self.chat_display.add_user_message(content)
            else:
                self.chat_display.add_agent_message(content)
        self.chat_display.append(
            f"\n[i] 已恢复会话 {sid[:15]}，继续对话将接续上下文。")

    def _on_new_session(self):
        """「新建」按钮：开始一段新对话（旧对话已自动保存，可在历史中找回）。"""
        if self._agent_is_running():
            self.chat_display.append(
                "\n[!] 私教仍在回答中，请先停止或等待完成后再新建会话。")
            return
        try:
            from tools.gui.workers.agent_worker import AgentWorker
        except ImportError:  # pragma: no cover
            from gui.workers.agent_worker import AgentWorker  # type: ignore
        AgentWorker._shared_session_id = None
        self.chat_display.clear()
        self.chat_display.append(
            "\n[+] 已开始新对话（上一段对话已自动保存，可在「历史会话」中找回）。")

    def _on_agent_step(self, step_text: str):
        """私教动作/思考链实时上屏（收进默认收起的思考折叠块，不再占满页面）。"""
        if not step_text:
            return
        self.chat_display.append_step(step_text.strip())

    def _on_agent_chunk(self, chunk: str):
        """流式片段：续写当前私教气泡（不另起一条，保持一段话连续）。"""
        if not chunk:
            return
        # 答案开始即封口思考折叠块：后续步骤会新起一块（区分「思考」与「作答」）
        self.chat_display.finish_step_group()
        self._streamed = True
        self.chat_display.append_agent_chunk(chunk)

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
        """收尾：已流式输出过就只封口当前气泡；未流式（本地兜底路径）才整条补上。

        这样两类路径都能正确显示，且不会把答案打两遍。

        [缺陷修复·停止后迟到回复] 点「停止」会把 ``agent_worker`` 置 None 并
        允许立即发下一条消息；被停止的旧 worker 之后返回的回复必须丢弃，
        否则会插到新对话中间。判据 = 发送者是否为当前活跃 worker。
        """
        # 收尾即封口思考折叠块（放在发送者校验之前：迟到回复也保证不再续写旧块）
        self.chat_display.finish_step_group()
        w = self.sender()
        if w is not None and w is not getattr(self, "agent_worker", None):
            return
        if getattr(self, "_streamed", False):
            self.chat_display.finish_agent_message()
        else:
            self.chat_display.add_agent_message(reply)
        self._streamed = False
        # [缺陷修复·报到后任务面板不刷新] 报到/交作业/打卡可能刚写入
        # _状态/今日任务.md —— 回复上屏后立即刷新任务进度，不让考生等到
        # 60 秒定时器或手动点「刷新今日进度」。
        self._load_today_task_progress()

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
        # [B5 修复·关窗 abort] IntelTaskWorker/AgentWorker 均为无 parent 的
        # QThread，仅靠 _worker_refs 持有。任务进行中关窗会触发
        # "QThread: Destroyed while thread is still running" 导致进程 abort。
        # 现先 cancel 再 quit+wait。
        # [缺陷修复·关窗等待过短] 原先每个线程 wait(2000)：LLM 请求在途时 2s
        # 远不够（流式单次读超时可达 90s），关窗后线程仍在跑 → 析构风险照旧。
        # 现改为**全局 5s 截止**：cancel 后给每个在跑线程分配剩余时间等待，
        # 总时长有界（不无限等待，不让关窗卡死）。
        for w in list(getattr(self, "_worker_refs", [])):
            try:
                w.cancel()
            except Exception:
                pass
        deadline = time.monotonic() + 5.0
        for w in list(getattr(self, "_worker_refs", [])):
            try:
                if w.isRunning():
                    w.quit()
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        w.wait(int(remaining * 1000))
            except Exception:
                pass
        theme_apply.write_pref(theme_apply.KEY_LAST_TAB, self.tabs.currentIndex())
        theme_apply.write_geometry(self)
        # [K7-U1 修复·GUI 关窗不触发 SessionEnd] CLI 三处退出路径都会
        # trigger_session_end（日终复盘落盘 + 可选 IM 推送），GUI 此前没有 ——
        # 桌面端考生永远收不到复盘卡。现仅当本窗口真实发生过 Agent 会话时
        # 触发一次；任何失败静默降级，绝不阻塞关窗。
        self._trigger_session_end_once()
        super().closeEvent(event)

    def _trigger_session_end_once(self):
        """[K7-U1] 关窗时触发一次 SessionEnd 钩子（幂等；仅真实会话过）。

        构造独立 HookManager（内置 SessionEnd 钩子注册于构造时），与 CLI 的
        ``agent_runner.hooks.trigger_session_end(...)`` 同语义。任何失败静默
        降级 —— 关窗流程绝不因钩子异常被阻塞。
        """
        if not getattr(self, "_agent_session_ran", False):
            return
        if getattr(self, "_session_end_fired", False):
            return
        self._session_end_fired = True
        try:
            try:
                from agent.hooks import HookManager
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.agent.hooks import HookManager
            cfg = getattr(self, "config", None) or {}
            hm = HookManager(workspace_root=self.workspace_root)
            hm.trigger_session_end({
                "active_subject": cfg.get("active_subject", ""),
            })
        except Exception:
            pass


__all__ = ["FunctionCard", "MainWindow", "TAB_TITLES"]
