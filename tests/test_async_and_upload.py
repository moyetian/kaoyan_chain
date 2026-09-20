# -*- coding: utf-8 -*-
"""针对院校侦察异步化、文件图片上传与大模型防超时加固的专项测试套件"""

import os
import sys
import gzip
import json
import socket
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


# ─── 1. IntelTaskWorker 异步工作线程测试 ───

def test_intel_task_worker_action_execution():
    from tools.gui.workers.intel_worker import IntelTaskWorker

    worker = IntelTaskWorker("action", ROOT, {"alias": "scout"})
    logs = []
    results = []
    worker.log_signal.connect(logs.append)
    worker.finished_signal.connect(lambda out, saved: results.append((out, saved)))

    with patch("tools.gui.services.run_action_capture", return_value="[测试侦察研报] 目标院校考情解析完成"), \
         patch("gui.services.run_action_capture", return_value="[测试侦察研报] 目标院校考情解析完成", create=True):
        worker.run()

    assert len(logs) >= 1
    assert "目标院校深度侦察" in logs[0]
    assert len(results) == 1
    assert "目标院校" in results[0][0]


def test_intel_task_worker_compare_execution():
    from tools.gui.workers.intel_worker import IntelTaskWorker

    worker = IntelTaskWorker("compare", ROOT, {
        "school1": "目标院校",
        "school2": "对比院校B",
        "major": "马克思主义理论"
    })
    logs = []
    results = []
    worker.log_signal.connect(logs.append)
    worker.finished_signal.connect(lambda out, saved: results.append((out, saved)))

    fake_report = "# 双校深度对比研报\n1. 院校代码对比...\n2. 初试科目对比..."
    fake_path = str(ROOT / "data" / "reports" / "compare_test.md")
    with patch("tools.gui.services.compare_schools", return_value=(fake_report, fake_path)), \
         patch("gui.services.compare_schools", return_value=(fake_report, fake_path), create=True):
        worker.run()

    assert len(logs) >= 1
    assert "目标院校" in logs[0] and "对比院校B" in logs[0]
    assert len(results) == 1
    assert "双校深度对比研报" in results[0][0]
    assert results[0][1] == fake_path


# ─── 2. MainWindow 图片与文件上传按钮与槽函数测试 ───

def test_main_window_upload_buttons_and_slots():
    from tools.gui.main_window import MainWindow

    with patch.object(MainWindow, "_init_ui"), patch.object(MainWindow, "_refresh_all"), \
         patch.object(MainWindow, "_open_onboarding_wizard"), patch.object(MainWindow, "_init_timer"):
        win = MainWindow()
        win.input_box = MagicMock()
        win.input_box.text.return_value = ""
        win.chat_display = MagicMock()

        assert hasattr(win, "_on_upload_image"), "MainWindow 必须实现 _on_upload_image"
        assert hasattr(win, "_on_upload_file"), "MainWindow 必须实现 _on_upload_file"

        # 测试图片选择与输入框填充
        fake_img = "c:/test_workspace/homework_draft.png"
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(fake_img, "images")):
            win._on_upload_image()

            win.input_box.setText.assert_called_once()
            called_text = win.input_box.setText.call_args[0][0]
            assert "/img" in called_text
            assert "homework_draft.png" in called_text

        # 测试文件选择与输入框填充
        win.input_box.setText.reset_mock()
        fake_pdf = "c:/test_workspace/syllabus_2026.pdf"
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(fake_pdf, "documents")):
            win._on_upload_file()

            win.input_box.setText.assert_called_once()
            called_text = win.input_box.setText.call_args[0][0]
            assert "/file" in called_text
            assert "syllabus_2026.pdf" in called_text


# ─── 3. AgentWorker 对 /img 与 /file 指令的识别与分流 ───

def test_agent_worker_img_and_file_commands(tmp_path):
    from tools.gui.workers.agent_worker import AgentWorker

    # 创建虚拟图片与文本
    img_file = tmp_path / "test_math_hw.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake image data")

    txt_file = tmp_path / "test_guide.md"
    txt_file.write_text("# 2026 专业课大纲考点\n1. 马克思主义基本原理导论", encoding="utf-8")

    # 1. 测试 /img 触发 vision_solver
    worker_img = AgentWorker(config={}, user_input=f'/img "{str(img_file)}" 请批改第2题步骤分')
    replies_img = []
    steps_img = []
    worker_img.step_signal.connect(steps_img.append)
    worker_img.finished_signal.connect(replies_img.append)

    with patch("tools.skills.vision_solver.solve_image_with_model", return_value="【采分点评定】第一步得2分，第二步无逻辑漏洞。") as mock_vision, \
         patch("skills.vision_solver.solve_image_with_model", return_value="【采分点评定】第一步得2分，第二步无逻辑漏洞。", create=True):
        worker_img.run()
        assert len(steps_img) >= 1
        assert "视觉私教" in steps_img[0]
        assert len(replies_img) == 1
        assert "第一步得2分" in replies_img[0]
        mock_vision.assert_called_once()

    # 2. 测试 /file 读取并挂载考点文本
    worker_file = AgentWorker(config={"api_key": "sk-test"}, user_input=f'/file "{str(txt_file)}" 请提炼核心考点')
    steps_file = []
    worker_file.step_signal.connect(steps_file.append)

    with patch("agent.AgentRunner.run", return_value="【考点提炼】包含马克思主义哲学与政治经济学两大板块。") as mock_agent_run:
        worker_file.run()
        assert len(steps_file) >= 1
        assert "考研资料" in steps_file[0]
        assert "马克思主义基本原理导论" in worker_file.user_input
        mock_agent_run.assert_called_once()


# ─── 4. AgenticResearchEngine 超时设置与重试健壮性 ───

def test_agentic_research_engine_timeout_and_retries():
    from tools.intelligence.agentic_research import AgenticResearchEngine

    engine = AgenticResearchEngine(ROOT)
    # 默认超时必须放宽至 >= 90s，防止多轮大上下文网络过早中断
    assert engine.timeout >= 90.0

    # 模拟网络波动时自动重试并最终成功
    mock_urlopen = MagicMock()
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.headers = {"Content-Encoding": ""}
    fake_json = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "研究完成",
                    "tool_calls": None
                }
            }
        ]
    }
    mock_resp.read.return_value = json.dumps(fake_json).encode("utf-8")

    # 第一次抛出超时，第二次返回正确结果
    mock_urlopen.side_effect = [
        socket.timeout("The read operation timed out"),
        mock_resp
    ]

    with patch("urllib.request.urlopen", mock_urlopen), patch("time.sleep"):
        res = engine.execute_loop(
            prompt="测试提示词",
            api_config={"api_key": "sk-real-mock-123456", "base_url": "https://api.mock.ai/v1"}
        )
        assert res == "研究完成"
        assert mock_urlopen.call_count == 2, "在发生 socket.timeout 时必须进行自动重试"


# ─── 5. Gzip 解压缩支持校验 ───

def test_agentic_research_gzip_decompression():
    from tools.intelligence.agentic_research import AgenticResearchEngine

    engine = AgenticResearchEngine(ROOT)
    fake_json = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Gzip内容已成功解析",
                    "tool_calls": None
                }
            }
        ]
    }
    raw_payload = json.dumps(fake_json).encode("utf-8")
    compressed_payload = gzip.compress(raw_payload)

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.headers = {"Content-Encoding": "gzip"}
    mock_resp.read.return_value = compressed_payload

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = engine.execute_loop(
            prompt="测试压缩响应",
            api_config={"api_key": "sk-real-mock-123456", "base_url": "https://api.mock.ai/v1"}
        )
        assert res == "Gzip内容已成功解析"


# ─── 6. Docx 纯 Python 解析与带空格路径挂载测试 ───

def test_agent_worker_docx_parsing_and_quoted_path(tmp_path):
    import zipfile
    from tools.gui.workers.agent_worker import AgentWorker

    # 创建带有空格路径的虚拟 docx 文件
    sub_dir = tmp_path / "folder with space"
    sub_dir.mkdir(parents=True, exist_ok=True)
    docx_file = sub_dir / "kaoyan outline.docx"

    # 构造标准 docx 内部结构 (word/document.xml)
    xml_content = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>\xe9\xa9\xac\xe5\x85\x8b\xe6\x80\x9d\xe4\xb8\xbb\xe4\xb9\x89\xe5\x9f\xba\xe6\x9c\xac\xe5\x8e\x9f\xe7\x90\x86</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(docx_file, "w") as z:
        z.writestr("word/document.xml", xml_content)

    worker = AgentWorker(config={"api_key": "sk-test"}, user_input=f'/file "{str(docx_file)}" 请提炼核心考点')
    with patch("agent.AgentRunner.run", return_value="【Docx已提炼】") as mock_run:
        worker.run()
        assert "马克思主义基本原理" in worker.user_input
        mock_run.assert_called_once()


# ─── 7. VisionSolver URL 规范化与 Gzip 解压缩支持 ───

def test_vision_solver_url_norm_and_gzip():
    from tools.skills import vision_solver

    fake_json = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "视觉批改已通过Gzip解析"
                }
            }
        ]
    }
    raw_payload = json.dumps(fake_json).encode("utf-8")
    compressed_payload = gzip.compress(raw_payload)

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.headers = {"Content-Encoding": "gzip"}
    mock_resp.read.return_value = compressed_payload

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = vision_solver.call_text_llm(
            messages=[{"role": "user", "content": "ping"}],
            config={
                "base_url": "https://api.fastrun.cn",  # 未带 /v1，验证智能规范化
                "api_key": "sk-real-test-123456",
                "model": "mimo-v2.5"
            },
            stream=False
        )
        assert res == "视觉批改已通过Gzip解析"
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        # 验证 endpoint 智能补齐 /v1/chat/completions
        assert req.full_url == "https://api.fastrun.cn/v1/chat/completions"
        # 验证 Connection: close 防中转站超时
        assert req.headers.get("Connection") == "close"


# ─── 8. MainWindow 院校侦察与双校对标弹窗与并发 Worker 引用安全 ───

def test_main_window_scout_and_compare_dialogs():
    from tools.gui.main_window import MainWindow

    with patch.object(MainWindow, "_init_ui"), patch.object(MainWindow, "_refresh_all"), \
         patch.object(MainWindow, "_open_onboarding_wizard"), patch.object(MainWindow, "_init_timer"):
        win = MainWindow()
        win.tabs = MagicMock()
        win.intel_display = MagicMock()
        win.chat_display = MagicMock()

        # 1. 验证 _run_scout_from_dialog
        with patch("PySide6.QtWidgets.QInputDialog.getText", side_effect=[("武汉大学", True), ("计算机", True)]), \
             patch("tools.gui.workers.intel_worker.IntelTaskWorker.start") as mock_s1, \
             patch("gui.workers.intel_worker.IntelTaskWorker.start", create=True) as mock_s2:
            win._run_scout_from_dialog()
            assert len(win._worker_refs) == 1
            w1 = win._worker_refs[0]
            assert w1.params["school"] == "武汉大学"
            assert w1.params["major"] == "计算机"

        # 2. 验证 _run_compare_from_dialog
        with patch("PySide6.QtWidgets.QInputDialog.getText", side_effect=[("清华大学", True), ("北京大学", True), ("计算机", True)]), \
             patch("tools.gui.workers.intel_worker.IntelTaskWorker.start"), \
             patch("gui.workers.intel_worker.IntelTaskWorker.start", create=True):
            win._run_compare_from_dialog()
            assert len(win._worker_refs) == 2
            w2 = win._worker_refs[1]
            assert w2.params["school1"] == "清华大学"
            assert w2.params["school2"] == "北京大学"

        # 验证 win._worker_refs 包含两个后台 Worker
        assert len(win._worker_refs) == 2
        # 清理 worker 引用
        win._worker_refs.clear()


# ─── 9. test_api_connectivity 超时配置与防超时头验证 ───

def test_settings_api_connectivity_headers_and_timeout():
    from tools.gui.services.settings import test_api_connectivity

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = b'{"status": "ok"}'
    mock_resp.status = 200

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = test_api_connectivity(
            api_key="sk-test-123456",
            base_url="https://api.fastrun.cn",
            model="mimo-v2.5"
        )
        assert res["llm_ok"] is True
        assert mock_urlopen.call_count >= 1
        llm_call = mock_urlopen.call_args_list[0]
        req = llm_call[0][0]
        # 默认 timeout 提升至 25s
        assert llm_call[1].get("timeout") == 25.0
        # 验证 Connection: close
        assert req.headers.get("Connection") == "close"


# ─── 10. 无 headers 属性的对象（鸭子类型 mock）兼容性测试 ───

def test_resp_without_headers_attribute_safety():
    from tools.skills import vision_solver
    from tools.skills import open_grader

    class BareResp:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"choices": [{"message": {"content": "ok"}}]}'

    # vision_solver
    decompressed = vision_solver._read_and_decompress(BareResp())
    assert "choices" in decompressed

    # open_grader
    client = open_grader.OpenAICompatClient(endpoint={"base_url": "https://api.openai.com/v1", "api_key": "sk-123"})
    with patch("urllib.request.urlopen", return_value=BareResp()):
        text = client.chat([{"role": "user", "content": "hi"}])
        assert text == "ok"


# ─── 11. open_grader 连接头与 Gzip 透明解压缩测试 ───

def test_open_grader_headers_and_gzip_support():
    import gzip
    from tools.skills import open_grader

    payload = b'{"choices": [{"message": {"content": "open_grader_gzip_ok"}}]}'
    compressed = gzip.compress(payload)

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = compressed
    mock_resp.headers = {"Content-Encoding": "gzip"}

    client = open_grader.OpenAICompatClient(endpoint={"base_url": "https://api.fastrun.cn", "api_key": "sk-fastrun"})
    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = client.chat([{"role": "user", "content": "ping"}])
        assert res == "open_grader_gzip_ok"
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.headers.get("Connection") == "close"
        assert "gzip" in req.headers.get("Accept-encoding", "").lower()


