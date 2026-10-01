# -*- coding: utf-8 -*-
"""研招情报页视图：监控雷达 + 院校侦察入口

[UI 重构·阶段 E] 页面结构：API 横幅 → 操作按钮条 → 指标卡双栏区 → IntelDisplay
文本报告。指标卡数据全部来自 ``services.dashboard.intel_metrics``（只读真实
数据）；任何一项探测不到真实值都回落为显式空态说明，绝不编造数字。

[零内联样式] API 横幅此前是全项目最后一份存量内联样式（setStyleSheet 硬编码
绿/橙两态）——本轮收编进主题 QSS 的 ``#IntelApiBanner[state="ok"|"warn"]``，
由动态属性切换；指标卡族（``#IntelMetricCard`` / ``#IntelMetricValue`` /
``#IntelMetricLabel`` / ``#IntelMetricDesc``）同样零内联样式。

内容由 ``services.intel_markdown`` 提供；按钮复用 TUI 的动作分发
（``services.run_action_capture``），不另写一套后端调用。
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

try:  # pragma: no cover - 取决于运行方式
    from gui.services import dashboard as dashboard_services
except ImportError:  # pragma: no cover
    from tools.gui.services import dashboard as dashboard_services  # type: ignore

#: 浏览器采集通道状态 → (状态值文案, 说明文案)；未知状态原样展示
_BROWSER_STATUS_TEXT = {
    "READY": ("已就绪", "已检测到浏览器内核，动态页可直采"),
    "DISABLED": ("未启用", "默认关闭；设 KY_BROWSER_ACQUISITION=on 启用"),
    "NOT_INSTALLED": ("未安装", "未安装 playwright（静态抓取不受影响）"),
    "UNAVAILABLE": ("不可用", "未检测到可用浏览器内核"),
}

#: 空态文案（无真实数据时展示，绝不编造数字）
_WATCH_EMPTY_TEXT = "暂无巡检数据，先运行一次简章监控"
_SOURCE_EMPTY_TEXT = "暂未探测到可用数据源"


def update_api_status_banner(win) -> None:
    """根据大模型 API 是否配置更新情报页指引横幅，并联动刷新指标卡双栏。

    [UI 重构·阶段 E] 横幅配色收编进 QSS：只切 ``state`` 动态属性
    （``ok``/``warn``），不再写任何内联样式；unpolish/polish 让属性选择器
    立即生效。指标卡刷新搭在同一条链路上（``main_window._refresh_intel_tab``
    → 本函数），刷新按钮 / 配置保存后无需另接入口。
    """
    if not hasattr(win, "intel_api_banner") or not hasattr(win, "intel_api_status_label"):
        return
    try:
        from tools.intelligence.agentic_research import get_research_engine
        # [缺陷修复·横幅读启动快照] 显式传入当前窗口的工作区根，且
        # is_api_configured 内部会重读配置 —— 设置中心保存 API Key 后
        # 立即刷新横幅，不再需要重启 GUI。
        engine = get_research_engine(workspace_root=getattr(win, "workspace_root", None))
        is_configured = engine.is_api_configured()
    except Exception:
        is_configured = False

    banner = win.intel_api_banner
    banner.setProperty("state", "ok" if is_configured else "warn")
    style = banner.style()
    if style is not None:  # 动态属性变化后需重新求值样式（Qt 不会自动重算）
        style.unpolish(banner)
        style.polish(banner)

    if is_configured:
        win.intel_api_status_label.setText(
            "✨ <b>LLM Agentic 深度招考情报研究引擎已激活</b>"
            "（DeepSeek / Qwen 多轮自主 Tool-Calling 模式）"
        )
        if hasattr(win, "intel_btn_setup_api"):
            win.intel_btn_setup_api.setText("⚙️ 修改模型配置")
    else:
        win.intel_api_status_label.setText(
            "⚡ <b>深度招考研究智能体就绪</b>：当前未配置大模型 API 密钥，系统已自动无缝切换至官方研招网与权威院校库直接检索。"
            "<br>点击右侧按钮一键配置大模型 API，解锁多轮自主推理与证据交叉比对。"
        )
        if hasattr(win, "intel_btn_setup_api"):
            win.intel_btn_setup_api.setText("⚙️ 配置大模型 API")

    refresh_intel_metrics(win)


# ── 指标卡双栏 ──────────────────────────────────────────────────

def _metric_card(value: str, label: str, desc: str = "") -> QFrame:
    """单张玻璃指标卡：大数字 + 标签 +（可选）说明；外观全部由 QSS 决定。"""
    card = QFrame()
    card.setObjectName("IntelMetricCard")
    box = QVBoxLayout(card)
    box.setContentsMargins(12, 10, 12, 10)
    box.setSpacing(2)

    value_lbl = QLabel(value)
    value_lbl.setObjectName("IntelMetricValue")
    label_lbl = QLabel(label)
    label_lbl.setObjectName("IntelMetricLabel")
    box.addWidget(value_lbl)
    box.addWidget(label_lbl)
    if desc:
        desc_lbl = QLabel(desc)
        desc_lbl.setObjectName("IntelMetricDesc")
        desc_lbl.setWordWrap(True)
        box.addWidget(desc_lbl)
    return card


def _empty_state_card(text: str) -> QFrame:
    """空态卡：明确说明「没有数据」，绝不编造数字。"""
    card = QFrame()
    card.setObjectName("IntelMetricCard")
    box = QVBoxLayout(card)
    box.setContentsMargins(12, 10, 12, 10)
    lbl = QLabel(text)
    lbl.setObjectName("IntelMetricDesc")
    lbl.setWordWrap(True)
    box.addWidget(lbl)
    return card


def _clear_metric_column(layout) -> None:
    """清空一列指标卡（保留第 0 项的列标题），旧卡片立即脱离视觉树并延迟销毁。"""
    if layout is None:
        return
    while layout.count() > 1:
        item = layout.takeAt(1)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)   # 与 error_tab._clear_layout 同口径：先脱离再销毁
            widget.deleteLater()


def refresh_intel_metrics(win) -> None:
    """按最新数据重建「监控概览 / 数据源状态」双栏指标卡。

    数据来自 ``services.dashboard.intel_metrics``（只读真实数据）；无数据的
    栏位回落为显式空态说明。任何异常都不外抛（界面保底不崩）。
    """
    columns = getattr(win, "intel_metric_columns", None)
    if not isinstance(columns, dict) or not columns:
        return
    try:
        metrics = dashboard_services.intel_metrics(win.workspace_root)
    except Exception:
        metrics = {"watch": None, "sources": {}}
    watch = metrics.get("watch") or {}
    sources = metrics.get("sources") or {}

    # ── 左栏：监控概览 ──
    left = columns.get("watch")
    _clear_metric_column(left)
    if left is not None:
        if watch:
            names = [str(n) for n in (watch.get("school_names") or [])]
            shown = "、".join(names[:3]) + (" 等" if len(names) > 3 else "")
            left.addWidget(_metric_card(
                f"{watch.get('school_count', len(names))} 所", "监控院校", shown))
            last_check = str(watch.get("last_check") or "")
            left.addWidget(_metric_card(
                last_check or "—", "最近巡检",
                "动态简章监控最近一次核验时间" if last_check else "尚无巡检记录"))
            left.addWidget(_metric_card(
                f"{int(watch.get('notice_count') or 0)} 条", "最新通知快照",
                "最近一轮巡检抓取到的公告标题数"))
        else:
            left.addWidget(_empty_state_card(_WATCH_EMPTY_TEXT))
        left.addStretch()

    # ── 右栏：数据源状态 ──
    right = columns.get("sources")
    _clear_metric_column(right)
    if right is not None:
        cards = 0
        api_configured = sources.get("api_configured")
        if api_configured is not None:
            right.addWidget(_metric_card(
                "已激活" if api_configured else "未配置", "大模型 API",
                "DeepSeek / Qwen 多轮自主 Tool-Calling"
                if api_configured else "已自动切换研招网与院校库直接检索"))
            cards += 1
        registry_count = sources.get("registry_count")
        if registry_count is not None:
            right.addWidget(_metric_card(
                f"{int(registry_count)} 所", "本地院校库", "研招网公开名录，离线可用"))
            cards += 1
        browser_status = sources.get("browser_status")
        if browser_status is not None:
            value, desc = _BROWSER_STATUS_TEXT.get(
                str(browser_status), (str(browser_status), "浏览器采集通道状态"))
            right.addWidget(_metric_card(value, "浏览器采集通道", desc))
            cards += 1
        cache_entries = sources.get("cache_entries")
        if cache_entries is not None:
            right.addWidget(_metric_card(
                f"{int(cache_entries)} 条", "检索缓存", "有效期内命中可免重复联网"))
            cards += 1
        if not cards:
            right.addWidget(_empty_state_card(_SOURCE_EMPTY_TEXT))
        right.addStretch()


def build(win) -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setSpacing(12)
    layout.setContentsMargins(12, 12, 12, 12)

    # 顶部大模型 API 引导横幅
    api_banner = QFrame()
    api_banner.setObjectName("IntelApiBanner")
    win.intel_api_banner = api_banner
    banner_layout = QHBoxLayout(api_banner)
    banner_layout.setContentsMargins(12, 8, 12, 8)
    banner_layout.setSpacing(10)

    lbl_status = QLabel()
    lbl_status.setObjectName("IntelApiStatusLabel")
    win.intel_api_status_label = lbl_status
    banner_layout.addWidget(lbl_status, stretch=1)

    btn_setup_api = QPushButton("⚙️ 配置大模型 API")
    btn_setup_api.setObjectName("SecondaryBtn")
    btn_setup_api.clicked.connect(win._open_settings)
    win.intel_btn_setup_api = btn_setup_api
    banner_layout.addWidget(btn_setup_api)

    layout.addWidget(api_banner)

    # 顶层操作工具条
    btn_bar = QHBoxLayout()
    btn_bar.setSpacing(10)

    btn_watch = QPushButton("动态简章监控巡检")
    btn_watch.setObjectName("SecondaryBtn")
    btn_watch.clicked.connect(lambda: win._run_action_to_display("watch", win.intel_display))

    btn_scout = QPushButton("目标院校深度侦察")
    # [缺陷修复·按钮常亮] 此前漏设 objectName → 走了全局 QPushButton 主按钮
    # 样式（实心紫），其余工具条按钮都是 SecondaryBtn 轮廓样式 —— 视觉上
    # 「目标院校深度侦察」永远像被选中高亮。与兄弟按钮对齐为次要样式。
    btn_scout.setObjectName("SecondaryBtn")
    btn_scout.clicked.connect(win._run_scout_from_dialog)

    btn_compare = QPushButton("双校考情深度对标")
    btn_compare.setObjectName("SecondaryBtn")
    btn_compare.clicked.connect(win._run_compare_from_dialog)

    btn_wechat = QPushButton("公众号考研文章检索")
    btn_wechat.setObjectName("SecondaryBtn")
    btn_wechat.clicked.connect(win._open_wechat_search_dialog)

    btn_refresh = QPushButton("刷新情报看板")
    btn_refresh.setObjectName("SecondaryBtn")
    btn_refresh.clicked.connect(win._refresh_intel_tab)

    btn_bar.addWidget(btn_watch)
    btn_bar.addWidget(btn_scout)
    btn_bar.addWidget(btn_compare)
    btn_bar.addWidget(btn_wechat)
    btn_bar.addStretch()
    btn_bar.addWidget(btn_refresh)
    layout.addLayout(btn_bar)

    # 指标卡双栏区（有数据则填；无数据显式空态，绝不编造数字）
    win.intel_metrics_area = QWidget()
    metrics_row = QHBoxLayout(win.intel_metrics_area)
    metrics_row.setContentsMargins(0, 0, 0, 0)
    metrics_row.setSpacing(12)
    win.intel_metric_columns = {}
    for key, title in (("watch", "监控概览"), ("sources", "数据源状态")):
        column = QVBoxLayout()
        column.setSpacing(8)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("IntelColumnTitle")
        column.addWidget(title_lbl)
        win.intel_metric_columns[key] = column
        metrics_row.addLayout(column, stretch=1)
    layout.addWidget(win.intel_metrics_area)

    win.intel_display = QTextEdit()
    win.intel_display.setReadOnly(True)
    win.intel_display.setObjectName("IntelDisplay")
    win.intel_display.setPlaceholderText("正在汇聚研招网与目标院校深度招考动态与情报研报...")
    layout.addWidget(win.intel_display, stretch=1)

    win._refresh_intel_tab()
    update_api_status_banner(win)
    return widget


__all__ = ["build", "refresh_intel_metrics", "update_api_status_banner"]
