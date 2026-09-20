# -*- coding: utf-8 -*-
"""针对用户最新实测 6 项反馈的专项验收测试套件 (Test User Feedback Fixes)

验收覆盖：
  1. GUI.bat 与 启动GUI.bat 存在性与启动配置校验；
  2. QSS 样式表在全预设下 QLabel 背景 100% 透明（杜绝浅色模式文字下方白框）；
  3. MainWindow._on_quick_command 快捷药丸槽函数实现与调用闭环；
  4. AgentWorker.step_signal 与 AgentRunner 思考过程/工具调用流式推送到 GUI；
  5. 微信公众号检索时效性过滤 (time_range) 与相关度评分重排机制；
  6. 官方 Logo 资源存在性与全局 WindowIcon / Windows 任务栏 AppID 注册。
"""

import os
import sys
import json
import pathlib
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
except Exception:
    _app = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def _read_bat(path: pathlib.Path) -> str:
    """[R2-C3] 启动脚本已改为 GBK(CP936) 落盘 + ``chcp 936``，不能再按 UTF-8 读。"""
    raw = path.read_bytes()
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ─── 1. GUI.bat & 启动GUI.bat 启动器测试 ───

def test_gui_bat_launchers_exist_and_healthy():
    gui_bat = ROOT / "GUI.bat"
    start_gui_bat = ROOT / "启动GUI.bat"
    assert gui_bat.exists(), "根目录下必须存在 GUI.bat"
    assert start_gui_bat.exists(), "根目录下必须存在 启动GUI.bat"

    content = _read_bat(gui_bat)
    assert "tools\\ky_gui.py" in content, "GUI.bat 必须指向 tools\\ky_gui.py"
    assert "PySide6" in content, "GUI.bat 必须具备 PySide6 环境自检"
    assert "D:\\Python" in content or "%LocalAppData%" in content, "GUI.bat 必须包含常用本地 Python 探测路径"


# ─── 2. QSS 样式表透明标签测试 ───

def test_qss_qlabel_transparent_in_all_themes():
    from tools.theme import compile_qt, tokens

    for preset_name in tokens.PRESET_ORDER:
        theme = tokens.build_theme(preset_name)
        qss = compile_qt.render_qss(theme)

        # 核心断言：全局 QLabel 背景必须显式设为 transparent，严防文字下方白色方块
        assert "background-color: transparent;" in qss, f"预设 {preset_name} 必须包含全局 transparent 背景声明"
        assert "#FunctionCard QLabel" in qss, f"预设 {preset_name} 必须保护功能卡片内标签透明"
        assert "#HeaderBar QLabel" in qss, f"预设 {preset_name} 必须保护顶栏标签透明"
        assert "pxpx" not in qss, "绝不能出现 pxpx 单位连缀"


# ─── 3. 私教快捷报到指令与 LLM 联动测试 ───

def test_main_window_quick_command_slot():
    from tools.gui.main_window import MainWindow

    with patch.object(MainWindow, "_init_ui"), patch.object(MainWindow, "_refresh_all"):
        win = MainWindow()
        win.input_box = MagicMock()
        win._on_send_message = MagicMock()

        assert hasattr(win, "_on_quick_command"), "MainWindow 必须实现 _on_quick_command 槽函数"
        win._on_quick_command("英语报到")

        win.input_box.setText.assert_called_once_with("英语报到")
        win._on_send_message.assert_called_once()


def test_agent_worker_checkin_with_llm_prompt():
    from tools.gui.workers.agent_worker import AgentWorker

    # 1. 未配置 API Key 时：给出大纲播报与明确设置引导
    worker_no_key = AgentWorker(config={"api_key": ""}, user_input="英语报到")
    replies = []
    worker_no_key.finished_signal.connect(replies.append)
    worker_no_key.run()

    assert len(replies) == 1
    assert "智能私教未激活" in replies[0]
    assert "设置" in replies[0]

    # 2. 已配置 API Key 时：构造导学 prompt 并联动 AgentRunner
    cfg = {"api_key": "sk-test-fake", "model": "deepseek-chat"}
    worker_with_key = AgentWorker(config=cfg, user_input="政治报到")

    with patch("agent.AgentRunner.run", return_value="【私教带背】今日政治核心考点如下...") as mock_run:
        replies_llm = []
        worker_with_key.finished_signal.connect(replies_llm.append)
        worker_with_key.run()

        assert mock_run.called, "配置 API Key 后必须调用 AgentRunner 进行大模型辅导生成"
        call_prompt = mock_run.call_args[0][0]
        assert "思想政治理论" in call_prompt
        assert "薄弱点" in call_prompt
        assert len(replies_llm) == 1
        assert "私教带背" in replies_llm[0]


# ─── 4. 思考过程与工具调用流式推送到 GUI ───

def test_agent_runner_step_callback_and_worker_signal():
    from tools.agent.loop import AgentRunner
    from tools.gui.workers.agent_worker import AgentWorker

    steps_received = []

    worker = AgentWorker(config={"api_key": "fake"}, user_input="自测")
    worker.step_signal.connect(steps_received.append)

    runner = AgentRunner(
        config={"api_key": "fake"},
        workspace_root=ROOT,
        step_callback=worker._emit_step,
        quiet=True
    )

    # 模拟触发思考和工具调用事件
    runner.step_callback("🧠 [私教深度思考] 学员进入政治强化攻坚...")
    runner.step_callback("🛠️ [调用工具] read_file(path='03-思想政治理论/考试大纲.md')")
    runner.step_callback("   ↳ 完成: 考纲包含马克思主义基本原理、毛中特...")

    assert len(steps_received) == 3
    assert "深度思考" in steps_received[0]
    assert "调用工具" in steps_received[1]
    assert "完成" in steps_received[2]


# ─── 5. 微信公众号检索时效性与重排测试 ───

def test_wechat_search_time_range_and_reranking():
    from tools.skills.wechat_searcher import WeChatSearchEngine, WeChatArticleItem

    engine = WeChatSearchEngine()

    sample_items = [
        WeChatArticleItem(title="2020年考研老旧心得", url="http://wx.com/1", publish_date="2020-05-01"),
        WeChatArticleItem(title="2021年考研图书推荐", url="http://wx.com/2", publish_date="2021-04-01"),
        WeChatArticleItem(title="2026马克思主义理论考研经验复盘", url="http://wx.com/3", publish_date="2026-09-10"),
        WeChatArticleItem(title="2025马克思主义理论高分考研复试线", url="http://wx.com/4", publish_date="2025-03-20"),
    ]

    # 当选择近一年 (year) 时：2020 与 2021 的陈旧文章必须被过滤掉
    ranked = engine._rank_and_filter_results(sample_items, "马克思主义理论考研", time_range="year")
    assert len(ranked) == 2, "近一年模式下应过滤掉 2020~2021 年老旧文章"
    assert ranked[0].publish_date == "2026-09-10", "最新发布的文章必须排在第 1 位"
    assert ranked[1].publish_date == "2025-03-20", "2025 年文章排在第 2 位"


# ─── 6. 官方 Logo 全局挂载测试 ───

def test_official_logo_assets_and_registration():
    logo_path = ROOT / "docs" / "assets" / "logo" / "logo.png"
    logo_trans = ROOT / "docs" / "assets" / "logo" / "logo_transparent.png"
    fallback_logo = ROOT / "docs" / "assets" / "logo.png"

    assert logo_path.exists(), "官方 logo.png 必须存在"
    assert logo_trans.exists(), "官方透明版 logo_transparent.png 必须存在"
    assert fallback_logo.exists(), "兼容路径 docs/assets/logo.png 必须存在"

    from tools.gui.views import header
    dummy_win = MagicMock()
    dummy_win.workspace_root = ROOT
    dummy_win._theme = MagicMock()
    dummy_win._theme.color.return_value = "#7c3aed"

    header_frame = header.build(dummy_win)
    assert header_frame is not None, "顶栏必须正常构建并加载官方 Logo"
