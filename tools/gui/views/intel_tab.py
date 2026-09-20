# -*- coding: utf-8 -*-
"""研招情报页视图：监控雷达 + 院校侦察入口

内容由 ``services.intel_markdown`` 提供；按钮复用 TUI 的动作分发
（``services.run_action_capture``），不另写一套后端调用。
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget


def update_api_status_banner(win) -> None:
    """根据大模型 API 是否配置更新情报页指引横幅"""
    if not hasattr(win, "intel_api_banner") or not hasattr(win, "intel_api_status_label"):
        return
    try:
        from tools.intelligence.agentic_research import get_research_engine
        engine = get_research_engine()
        is_configured = engine.is_api_configured()
    except Exception:
        is_configured = False

    if is_configured:
        win.intel_api_banner.setStyleSheet(
            "background: rgba(16, 185, 129, 0.08); "
            "border: 1px solid rgba(16, 185, 129, 0.35); border-radius: 8px;"
        )
        win.intel_api_status_label.setText(
            "✨ <b>LLM Agentic 深度招考情报研究引擎已激活</b> "
            "<span style='color: rgb(16, 185, 129);'>(DeepSeek / Qwen 多轮自主 Tool-Calling 模式)</span>"
        )
        if hasattr(win, "intel_btn_setup_api"):
            win.intel_btn_setup_api.setText("⚙️ 修改模型配置")
    else:
        win.intel_api_banner.setStyleSheet(
            "background: rgba(245, 158, 11, 0.10); "
            "border: 1px solid rgba(245, 158, 11, 0.45); border-radius: 8px;"
        )
        win.intel_api_status_label.setText(
            "⚡ <b>深度招考研究智能体就绪</b>：当前未配置大模型 API 密钥，系统已自动无缝切换至官方研招网与权威院校库直接检索。"
            "<br><span style='color: rgb(245, 158, 11);'>点击右侧按钮一键配置大模型 API，解锁多轮自主推理与证据交叉比对。</span>"
        )
        if hasattr(win, "intel_btn_setup_api"):
            win.intel_btn_setup_api.setText("⚙️ 配置大模型 API")


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

    win.intel_display = QTextEdit()
    win.intel_display.setReadOnly(True)
    win.intel_display.setObjectName("IntelDisplay")
    win.intel_display.setPlaceholderText("正在汇聚研招网与目标院校深度招考动态与情报研报...")
    layout.addWidget(win.intel_display, stretch=1)

    win._refresh_intel_tab()
    update_api_status_banner(win)
    return widget


__all__ = ["build", "update_api_status_banner"]
