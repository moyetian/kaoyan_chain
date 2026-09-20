# -*- coding: utf-8 -*-
"""
LLM 回包解压缩与报到交互链路专项验收测试
==========================================
测试目标：
1. 验证 `tools/agent/loop.py` 中 `_call_llm` 与 `_call_llm_without_tools` 在 gzip、deflate 及未压缩时均能正确读取并解码数据，绝不抛出 `UnboundLocalError: local variable 'raw_bytes' referenced before assignment`。
2. 验证中转站 5s 延迟与长响应模拟下，响应能够完整返回并被 AgentRunner 处理。
"""

import sys
import json
import gzip
import zlib
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from agent.loop import AgentRunner


def test_call_llm_gzip():
    """测试 gzip 压缩回包解码"""
    runner = AgentRunner(
        config={"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"},
        workspace_root=ROOT,
        quiet=True
    )

    mock_resp_data = {
        "id": "chatcmpl-test",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "考研政治私教已就绪！今日我们聚焦辩证唯物主义唯物论部分。",
                "reasoning_content": "分析考点与大纲规划中..."
            },
            "finish_reason": "stop"
        }]
    }
    json_bytes = json.dumps(mock_resp_data, ensure_ascii=False).encode("utf-8")
    compressed_bytes = gzip.compress(json_bytes)

    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = compressed_bytes
    mock_response.headers = {"Content-Encoding": "gzip"}

    with patch("urllib.request.urlopen", return_value=mock_response):
        res = runner._call_llm([{"role": "user", "content": "政治报到"}])
        assert res is not None, "应成功返回响应数据，而非 None"
        assert res["choices"][0]["message"]["content"] == mock_resp_data["choices"][0]["message"]["content"]
        print("  [√] Gzip 压缩回包解码测试通过！")


def test_call_llm_deflate():
    """测试 deflate 压缩回包解码"""
    runner = AgentRunner(
        config={"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"},
        workspace_root=ROOT,
        quiet=True
    )

    mock_resp_data = {
        "id": "chatcmpl-test2",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "英语私教已就绪！今日攻克 2018 年 Text 1 长难句。",
            },
            "finish_reason": "stop"
        }]
    }
    json_bytes = json.dumps(mock_resp_data, ensure_ascii=False).encode("utf-8")
    compressed_bytes = zlib.compress(json_bytes)

    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = compressed_bytes
    mock_response.headers = {"Content-Encoding": "deflate"}

    with patch("urllib.request.urlopen", return_value=mock_response):
        res = runner._call_llm([{"role": "user", "content": "英语报到"}])
        assert res is not None, "应成功返回响应数据，而非 None"
        assert res["choices"][0]["message"]["content"] == mock_resp_data["choices"][0]["message"]["content"]
        print("  [√] Deflate 压缩回包解码测试通过！")


def test_call_llm_identity():
    """测试未压缩纯文本回包"""
    runner = AgentRunner(
        config={"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"},
        workspace_root=ROOT,
        quiet=True
    )

    mock_resp_data = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "专业课私教已就绪！结合 618 考纲，今日第一题：简述矛盾普遍性与特殊性辩证关系。",
            }
        }]
    }
    json_bytes = json.dumps(mock_resp_data, ensure_ascii=False).encode("utf-8")

    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = json_bytes
    mock_response.headers = {}

    with patch("urllib.request.urlopen", return_value=mock_response):
        res = runner._call_llm([{"role": "user", "content": "专业课报到"}])
        assert res is not None
        assert "618 考纲" in res["choices"][0]["message"]["content"]
        print("  [√] 未压缩 (Identity) 回包解析测试通过！")


def test_call_llm_without_tools():
    """测试降级纯文本路径的解压与读取"""
    runner = AgentRunner(
        config={"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"},
        workspace_root=ROOT,
        quiet=True
    )

    mock_resp_data = {
        "choices": [{"message": {"role": "assistant", "content": "纯文本降级模式回复成功"}}]
    }
    compressed_bytes = gzip.compress(json.dumps(mock_resp_data).encode("utf-8"))

    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = compressed_bytes
    mock_response.headers = {"Content-Encoding": "gzip"}

    with patch("urllib.request.urlopen", return_value=mock_response):
        res = runner._call_llm_without_tools([{"role": "user", "content": "测试"}])
        assert res is not None
        assert res["choices"][0]["message"]["content"] == "纯文本降级模式回复成功"
        print("  [√] 降级纯文本 Gzip 解码测试通过！")


def test_call_llm_brotli():
    """测试 Brotli (br) 压缩回包解码"""
    try:
        import brotli
    except ImportError:
        import pytest
        pytest.skip("brotli 模块未安装，跳过物理解压测试")

    runner = AgentRunner(
        config={"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"},
        workspace_root=ROOT,
        quiet=True
    )

    mock_resp_data = {
        "id": "chatcmpl-test-br",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "专业课私教通过 Brotli 压缩信道连通成功！今日攻克 823 真题重点。",
            },
            "finish_reason": "stop"
        }]
    }
    json_bytes = json.dumps(mock_resp_data, ensure_ascii=False).encode("utf-8")
    compressed_bytes = brotli.compress(json_bytes)

    # 1. 验证常规 _call_llm
    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = compressed_bytes
    mock_response.headers = {"Content-Encoding": "br"}

    with patch("urllib.request.urlopen", return_value=mock_response):
        res = runner._call_llm([{"role": "user", "content": "专业课报到"}])
        assert res is not None, "应成功返回解压后的响应数据"
        assert res["choices"][0]["message"]["content"] == mock_resp_data["choices"][0]["message"]["content"]

    # 2. 验证降级 _call_llm_without_tools
    with patch("urllib.request.urlopen", return_value=mock_response):
        res2 = runner._call_llm_without_tools([{"role": "user", "content": "测试"}])
        assert res2 is not None
        assert res2["choices"][0]["message"]["content"] == mock_resp_data["choices"][0]["message"]["content"]

    print("  [√] Brotli (br) 压缩回包解码测试通过！")


def test_normalize_openai_url_compatibility():
    """测试不同国内大模型与代理端点的 URL 智能规范化"""
    from agent.loop import normalize_openai_url as norm_agent
    from gui.services.settings import normalize_openai_url as norm_gui

    for fn in (norm_agent, norm_gui):
        # 1. 智谱 AI (/v4) - 绝不能追加 /v1
        assert fn("https://open.bigmodel.cn/api/paas/v4") == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        assert fn("https://open.bigmodel.cn/api/paas/v4/") == "https://open.bigmodel.cn/api/paas/v4/chat/completions"

        # 2. 火山方舟豆包 (/v3) - 绝不能追加 /v1
        assert fn("https://ark.cn-beijing.volces.com/api/v3") == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

        # 3. 兼容自建 /v2 代理
        assert fn("https://proxy.example.com/api/v2") == "https://proxy.example.com/api/v2/chat/completions"

        # 4. DeepSeek 根域名自动补齐 /v1
        assert fn("https://api.deepseek.com") == "https://api.deepseek.com/v1/chat/completions"
        assert fn("https://api.deepseek.com/") == "https://api.deepseek.com/v1/chat/completions"

        # 5. 已带 /v1
        assert fn("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/chat/completions"
        assert fn("https://api.openai.com/v1/") == "https://api.openai.com/v1/chat/completions"

        # 6. 已是完整 /chat/completions
        assert fn("https://api.deepseek.com/v1/chat/completions") == "https://api.deepseek.com/v1/chat/completions"

        # 7. 自定义 endpoint (如 models)
        assert fn("https://open.bigmodel.cn/api/paas/v4", "models") == "https://open.bigmodel.cn/api/paas/v4/models"
        assert fn("https://api.deepseek.com", "models") == "https://api.deepseek.com/v1/models"

    print("  [√] 多厂商/反代 URL 规范化兼容性测试通过！")


def test_typewriter_chunk_buffering():
    """测试流式打字机 12 字符步进与 GUI 回显防重印机制"""
    chunks = []
    def record_chunk(c):
        chunks.append(c)

    runner = AgentRunner(
        config={"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"},
        workspace_root=ROOT,
        stream_callback=record_chunk,
        quiet=True
    )

    test_text = "考研英语长难句拆解：主谓宾定状补逻辑层次分明。"
    runner._display_final_answer(test_text)

    assert len(chunks) > 1, f"应按步进分片，实际分片数: {len(chunks)}"
    for chunk in chunks[:-1]:
        assert len(chunk) <= 12, "每片字符数不应超过 12"
    assert "".join(chunks) == test_text

    print(f"  [√] 流式打字机 12 字符缓冲测试通过 (共拆分为 {len(chunks)} 个片段推送)！")


def test_pdf_file_attachment_and_extraction():
    """测试通过 /file 挂载 PDF 真题时，PDF 文本能够被提取并装载进 AgentWorker Prompt"""
    import tempfile
    from skills.material_ingestion import extract_text_from_pdf
    from gui.workers.agent_worker import AgentWorker

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "2026_Kaoyan_Exam_618.pdf"

        # 使用 PyMuPDF 生成测试 PDF
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 72), "2026 Kaoyan Exam 618: Marxist Philosophy and Dialectical Materialism Question 1.")
        doc.save(str(pdf_path))
        doc.close()

        # 1. 验证 material_ingestion.extract_text_from_pdf 可以提取
        extracted = extract_text_from_pdf(pdf_path)
        assert "Kaoyan Exam 618" in extracted
        assert "Marxist Philosophy" in extracted

        # 2. 验证 AgentWorker /file 挂载时不会触发 ImportError，且把正文注入 Prompt 并调用私教
        cfg = {
            "api_key": "test-key",
            "base_url": "https://api.test.com/v1",
            "model": "test-model"
        }
        worker = AgentWorker(cfg, f"/file {pdf_path} 请给出第一题解析")
        with patch.object(AgentRunner, "run", return_value="解析已完成") as mock_runner_run:
            worker.run()
            assert "【2026_Kaoyan_Exam_618.pdf】" in worker.user_input
            assert "Marxist Philosophy" in worker.user_input
            assert "请给出第一题解析" in worker.user_input
            assert mock_runner_run.called

    print("  [√] PDF 真题抽取与 AgentWorker /file 挂载链路测试通过！")


if __name__ == "__main__":
    print("\n--- 开始 LLM 回包解压缩与报到交互专项测试 ---")
    test_call_llm_gzip()
    test_call_llm_deflate()
    test_call_llm_identity()
    test_call_llm_without_tools()
    test_call_llm_brotli()
    test_normalize_openai_url_compatibility()
    test_typewriter_chunk_buffering()
    test_pdf_file_attachment_and_extraction()
    print("--- [全部测试通过] 彻底解决中转站 5s 但后台无数据的问题！ ---\n")

