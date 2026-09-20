# -*- coding: utf-8 -*-
"""
Adversarial Resiliency & Ingestion Verification Suite
=====================================================
Challenger 2 Empirical Verification:
1. Adversarial URL Normalization (agent.loop & gui.services.settings)
2. Adversarial Decompression (gzip, deflate, brotli with corrupted/truncated payloads)
3. Adversarial PDF Ingestion (empty, corrupted, truncated, non-PDF, non-existent files)
4. End-to-End AgentRunner & AgentWorker Resiliency
"""

import sys
import json
import gzip
import zlib
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from agent.loop import normalize_openai_url as norm_agent, AgentRunner
from gui.services.settings import normalize_openai_url as norm_gui
from skills.material_ingestion import extract_text_from_pdf
from gui.workers.agent_worker import AgentWorker

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False


# ============================================================================
# 1. Adversarial URL Normalization Tests
# ============================================================================

URL_TEST_CASES = [
    ("https://api.example.com/v4/", "https://api.example.com/v4/chat/completions"),
    ("https://api.example.com/v3/beta", "https://api.example.com/v3/beta/chat/completions"),
    ("https://api.example.com/prefix/v1", "https://api.example.com/prefix/v1/chat/completions"),
    ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.example.com/v1/chat/completions", "https://api.example.com/v1/chat/completions"),
]


@pytest.mark.parametrize("input_url,expected_url", URL_TEST_CASES)
def test_adversarial_url_normalization_agent(input_url, expected_url):
    """验证 agent.loop.normalize_openai_url 对所有边界用例规范化符合预期"""
    assert norm_agent(input_url) == expected_url


@pytest.mark.parametrize("input_url,expected_url", URL_TEST_CASES)
def test_adversarial_url_normalization_gui(input_url, expected_url):
    """验证 gui.services.settings.normalize_openai_url 对所有边界用例规范化符合预期"""
    assert norm_gui(input_url) == expected_url


def test_adversarial_url_normalization_additional_boundaries():
    """验证额外边界：空值、首尾空白、自定义 endpoint、多斜杠"""
    for fn in (norm_agent, norm_gui):
        # 空字符串与 None 默认回退 DeepSeek v1
        assert fn("") == "https://api.deepseek.com/v1/chat/completions"
        assert fn(None) == "https://api.deepseek.com/v1/chat/completions"
        assert fn("   https://api.deepseek.com/v1/   ") == "https://api.deepseek.com/v1/chat/completions"

        # 尾部带斜杠的 chat/completions
        assert fn("https://api.example.com/v1/chat/completions/") == "https://api.example.com/v1/chat/completions"

        # 自定义 endpoint: models
        assert fn("https://open.bigmodel.cn/api/paas/v4", "models") == "https://open.bigmodel.cn/api/paas/v4/models"
        assert fn("https://open.bigmodel.cn/api/paas/v4", "/models") == "https://open.bigmodel.cn/api/paas/v4/models"
        assert fn("https://api.deepseek.com", "models") == "https://api.deepseek.com/v1/models"


# ============================================================================
# 2. Adversarial Decompression Tests (gzip, deflate, brotli)
# ============================================================================

def _make_runner():
    return AgentRunner(
        config={"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"},
        workspace_root=ROOT,
        quiet=True
    )


@pytest.mark.parametrize("corrupted_payload,case_name", [
    (b"totally_invalid_non_gzip_bytes_1234567890", "garbage"),
    (gzip.compress(json.dumps({"choices": []}).encode("utf-8"))[:12], "truncated"),
    (gzip.compress(json.dumps({"choices": []}).encode("utf-8"))[:8] + b"\xff\xff\x00\x00", "bitflipped"),
    (b"", "empty"),
])
def test_adversarial_decompression_gzip(corrupted_payload, case_name):
    """测试各种损坏的 gzip 报文在 AgentRunner 中被安全捕获，不抛出未处理异常"""
    runner = _make_runner()
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = corrupted_payload
    mock_resp.headers = {"Content-Encoding": "gzip"}

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res_tools = runner._call_llm([{"role": "user", "content": "test"}])
        assert res_tools is None, f"[{case_name}] _call_llm 应在流损坏时安全返回 None"

        res_no_tools = runner._call_llm_without_tools([{"role": "user", "content": "test"}])
        assert res_no_tools is None, f"[{case_name}] _call_llm_without_tools 应在流损坏时安全返回 None"


@pytest.mark.parametrize("corrupted_payload,case_name", [
    (b"totally_invalid_non_deflate_bytes_1234567890", "garbage"),
    (zlib.compress(json.dumps({"choices": []}).encode("utf-8"))[:8], "truncated"),
    (zlib.compress(json.dumps({"choices": []}).encode("utf-8"))[:5] + b"\x00\xff", "bitflipped"),
    (b"", "empty"),
])
def test_adversarial_decompression_deflate(corrupted_payload, case_name):
    """测试各种损坏的 deflate 报文在 AgentRunner 中被安全捕获，不抛出未处理异常"""
    runner = _make_runner()
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = corrupted_payload
    mock_resp.headers = {"Content-Encoding": "deflate"}

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res_tools = runner._call_llm([{"role": "user", "content": "test"}])
        assert res_tools is None, f"[{case_name}] _call_llm 应在流损坏时安全返回 None"

        res_no_tools = runner._call_llm_without_tools([{"role": "user", "content": "test"}])
        assert res_no_tools is None, f"[{case_name}] _call_llm_without_tools 应在流损坏时安全返回 None"


@pytest.mark.skipif(not HAS_BROTLI, reason="brotli 未安装")
@pytest.mark.parametrize("corrupted_payload,case_name", [
    (b"totally_invalid_non_brotli_bytes_1234567890", "garbage"),
    (brotli.compress(json.dumps({"choices": []}).encode("utf-8"))[:6], "truncated"),
    (brotli.compress(json.dumps({"choices": []}).encode("utf-8"))[:4] + b"\xff\x00", "bitflipped"),
    (b"", "empty"),
])
def test_adversarial_decompression_brotli(corrupted_payload, case_name):
    """测试各种损坏的 brotli (br) 报文在 AgentRunner 中被安全捕获，不抛出未处理异常"""
    runner = _make_runner()
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = corrupted_payload
    mock_resp.headers = {"Content-Encoding": "br"}

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res_tools = runner._call_llm([{"role": "user", "content": "test"}])
        assert res_tools is None, f"[{case_name}] _call_llm 应在流损坏时安全返回 None"

        res_no_tools = runner._call_llm_without_tools([{"role": "user", "content": "test"}])
        assert res_no_tools is None, f"[{case_name}] _call_llm_without_tools 应在流损坏时安全返回 None"


def test_adversarial_decompression_runner_run_e2e():
    """端到端验证：当网络底层遇到损坏的压缩数据时，AgentRunner.run 不会产生未捕获异常崩溃"""
    runner = _make_runner()
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = b"corrupted payload bytes"
    mock_resp.headers = {"Content-Encoding": "gzip"}

    with patch("urllib.request.urlopen", return_value=mock_resp):
        ans = runner.run("政治报到")
        # 应该优雅降级返回空字符串或完成退出，绝不崩溃
        assert isinstance(ans, str)


# ============================================================================
# 3. Adversarial PDF Ingestion Tests
# ============================================================================

def test_adversarial_pdf_ingestion_empty_file():
    """测试 0 字节空 PDF 文件抽取：绝不崩溃，返回描述性提示"""
    with tempfile.TemporaryDirectory() as tmpdir:
        empty_pdf = Path(tmpdir) / "empty.pdf"
        empty_pdf.write_bytes(b"")

        res = extract_text_from_pdf(empty_pdf)
        assert isinstance(res, str)
        assert "empty.pdf" in res
        assert "未能提取到文本" in res


def test_adversarial_pdf_ingestion_corrupted_file():
    """测试内容损坏的 PDF 文件抽取（乱码头与截断流）：绝不崩溃，返回描述性提示"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 纯乱码文件
        corrupted_pdf = Path(tmpdir) / "corrupted.pdf"
        corrupted_pdf.write_bytes(b"NON_PDF_RANDOM_GARBAGE_BYTES_0987654321")
        res1 = extract_text_from_pdf(corrupted_pdf)
        assert isinstance(res1, str)
        assert "corrupted.pdf" in res1
        assert "未能提取到文本" in res1

        # 2. 伪造 PDF 头但中途截断的残损文件
        truncated_pdf = Path(tmpdir) / "truncated.pdf"
        truncated_pdf.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n")
        res2 = extract_text_from_pdf(truncated_pdf)
        assert isinstance(res2, str)
        assert "truncated.pdf" in res2
        assert "未能提取到文本" in res2


def test_adversarial_pdf_ingestion_non_pdf_file():
    """测试非 PDF 文件（图片二进制、普通文本）传入 extract_text_from_pdf：安全返回"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 图片伪装成 PDF 或直接传入二进制文件
        bin_file = Path(tmpdir) / "fake.pdf"
        bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")
        res1 = extract_text_from_pdf(bin_file)
        assert isinstance(res1, str)
        assert "fake.pdf" in res1
        assert "未能提取到文本" in res1

        # 2. 不存在的文件
        res2 = extract_text_from_pdf(Path(tmpdir) / "non_existent.pdf")
        assert isinstance(res2, str)
        assert "未找到文件" in res2


def test_adversarial_agent_worker_corrupted_pdf_attachment():
    """端到端验证：AgentWorker 挂载损坏的 PDF 时，优雅将提示封装进 prompt 并安全执行"""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_pdf = Path(tmpdir) / "bad_exam.pdf"
        bad_pdf.write_bytes(b"CORRUPTED_EXAM_DATA")

        cfg = {"api_key": "test-key", "base_url": "https://api.test.com/v1", "model": "test-model"}
        worker = AgentWorker(cfg, f"/file {bad_pdf} 请解析本题")

        with patch.object(AgentRunner, "run", return_value="解析完成") as mock_run:
            worker.run()
            assert "【bad_exam.pdf】" in worker.user_input
            assert "未能提取到文本" in worker.user_input
            assert mock_run.called
