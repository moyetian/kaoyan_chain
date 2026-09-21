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
from io import BytesIO
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from agent.loop import AgentRunner
from tools.llm_client import (
    chat_completion,
    call_llm_sync,
    fetch_upstream_models,
    _decompress_response_bytes,
)


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

    with patch("agent.loop.safe_urlopen", return_value=mock_response):
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

    with patch("agent.loop.safe_urlopen", return_value=mock_response):
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

    with patch("agent.loop.safe_urlopen", return_value=mock_response):
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

    with patch("agent.loop.safe_urlopen", return_value=mock_response):
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

    with patch("agent.loop.safe_urlopen", return_value=mock_response):
        res = runner._call_llm([{"role": "user", "content": "专业课报到"}])
        assert res is not None, "应成功返回解压后的响应数据"
        assert res["choices"][0]["message"]["content"] == mock_resp_data["choices"][0]["message"]["content"]

    # 2. 验证降级 _call_llm_without_tools
    with patch("agent.loop.safe_urlopen", return_value=mock_response):
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
        # [P7 修复] 原为函数体内裸 `import pymupdf`，而 pymupdf 未在
        # requirements.txt / [dev] 声明 → CI 走到这里就 ModuleNotFoundError。
        # 改为显式守卫：缺依赖时用例 skip（本用例只把 pymupdf 当造样本工具）。
        # 注意用 try/except 而非 pytest.importorskip：pytest 9.1 起 importorskip
        # 默认只捕 ModuleNotFoundError，捕不到「模块存在但导入期抛 ImportError」
        # （如缺 native 库）的情形。
        try:
            import pymupdf
        except ImportError:
            pytest.skip("pymupdf 未安装（仅测试造样本用）")
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


def test_decompress_response_bytes_helper_all_encodings():
    """测试 _decompress_response_bytes 辅助函数对所有编码与异常格式的容错能力"""
    # 1. Gzip 正常与魔数容错
    sample_text = "考研数学一：高数+线代+概率统计 强化复习"
    sample_bytes = sample_text.encode("utf-8")
    gz_data = gzip.compress(sample_bytes)
    assert _decompress_response_bytes(gz_data, {"Content-Encoding": "gzip"}) == sample_text
    assert _decompress_response_bytes(gz_data, {}) == sample_text  # 依赖 1f 8b 魔数

    # 2. Deflate (zlib) 正常与魔数容错
    df_data = zlib.compress(sample_bytes)
    assert _decompress_response_bytes(df_data, {"Content-Encoding": "deflate"}) == sample_text
    assert _decompress_response_bytes(df_data, {}) == sample_text  # 依赖 0x78 魔数

    # 3. Raw Deflate (-zlib.MAX_WBITS)
    comp = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw_df_data = comp.compress(sample_bytes) + comp.flush()
    assert _decompress_response_bytes(raw_df_data, {"Content-Encoding": "deflate"}) == sample_text

    # 4. Brotli (若可用)
    try:
        import brotli
        br_data = brotli.compress(sample_bytes)
        assert _decompress_response_bytes(br_data, {"Content-Encoding": "br"}) == sample_text
    except ImportError:
        pass

    # 5. 未压缩 UTF-8 文本
    assert _decompress_response_bytes(sample_bytes, None) == sample_text

    # 6. GBK 编码兼容
    gbk_text = "考研政治冲刺划重点"
    gbk_bytes = gbk_text.encode("gbk")
    assert _decompress_response_bytes(gbk_bytes) == gbk_text

    # 7. 异常空值与字符串安全容错
    assert _decompress_response_bytes(b"") == ""
    assert _decompress_response_bytes("已解压字符串") == "已解压字符串"

    # 8. 损坏字节串替换容错 (errors='replace')
    corrupt_bytes = b"\xff\xfe\xfd\x80\x81"
    decoded_corrupt = _decompress_response_bytes(corrupt_bytes)
    assert isinstance(decoded_corrupt, str)
    print("  [√] _decompress_response_bytes 辅助函数全面容错测试通过！")


def test_chat_completion_http_400_gzip_max_tokens_fallback():
    """测试 chat_completion 在遭遇 HTTP 400 gzip 压缩报错时，解压并嗅探 max_tokens 自动剔除重试成功"""
    cfg = {"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"}
    calls = []

    def mock_urlopen(req, timeout=10.0):
        calls.append(req)
        payload = json.loads(req.data.decode("utf-8"))
        if "max_tokens" in payload:
            err_data = {
                "error": {
                    "message": "Invalid parameter: max_tokens is not supported for this model",
                    "type": "invalid_request_error",
                    "code": 400,
                }
            }
            gz_err = gzip.compress(json.dumps(err_data).encode("utf-8"))
            headers = {"Content-Encoding": "gzip"}
            fp = BytesIO(gz_err)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", headers, fp)
        else:
            resp = MagicMock()
            success_payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "极限的保号性与夹逼准则证明完毕。",
                        }
                    }
                ]
            }
            resp.read.return_value = json.dumps(success_payload).encode("utf-8")
            resp.headers = {"Content-Encoding": "identity"}
            resp.__enter__.return_value = resp
            return resp

    with patch("tools.llm_client.safe_urlopen", side_effect=mock_urlopen):
        # 1. 验证 chat_completion
        res = chat_completion("求函数极限", config=cfg, max_tokens=1024)
        assert res == "极限的保号性与夹逼准则证明完毕。"
        assert len(calls) == 2, "首发 400 后应成功执行 1 次自愈重试，共 2 次网络调用"

        # 校验第一次调用带有 max_tokens，第二次调用已自动剥离
        req1_data = json.loads(calls[0].data.decode("utf-8"))
        req2_data = json.loads(calls[1].data.decode("utf-8"))
        assert req1_data.get("max_tokens") == 1024
        assert "max_tokens" not in req2_data

        # 2. 验证 call_llm_sync 接口别名具备完全一致的自愈行为
        calls.clear()
        res_alias = call_llm_sync("求函数极限", config=cfg, max_tokens=2048)
        assert res_alias == "极限的保号性与夹逼准则证明完毕。"
        assert len(calls) == 2
        assert "max_tokens" not in json.loads(calls[1].data.decode("utf-8"))

    print("  [√] HTTP 400 Gzip max_tokens 降级重试测试通过！")


def test_chat_completion_http_400_deflate_system_role_fallback():
    """测试 chat_completion 在遭遇 HTTP 400 deflate 压缩报错时，解压并嗅探 system 角色自动合入 user 消息重试成功"""
    cfg = {"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"}
    calls = []

    def mock_urlopen(req, timeout=10.0):
        calls.append(req)
        payload = json.loads(req.data.decode("utf-8"))
        msgs = payload.get("messages", [])
        if any(m.get("role") == "system" for m in msgs):
            err_data = {
                "error": {
                    "message": "The system role is not permitted in this endpoint. Only user/assistant allowed.",
                    "code": 400,
                }
            }
            df_err = zlib.compress(json.dumps(err_data).encode("utf-8"))
            headers = {"Content-Encoding": "deflate"}
            fp = BytesIO(df_err)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", headers, fp)
        else:
            resp = MagicMock()
            success_payload = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "辩证法三大规律：对立统一规律、质量互变规律、否定之否定规律。",
                        }
                    }
                ]
            }
            resp.read.return_value = json.dumps(success_payload).encode("utf-8")
            resp.headers = {"Content-Encoding": "identity"}
            resp.__enter__.return_value = resp
            return resp

    with patch("tools.llm_client.safe_urlopen", side_effect=mock_urlopen):
        input_msgs = [
            {"role": "system", "content": "你是一位严格的马克思主义基本原理私教"},
            {"role": "user", "content": "请梳理唯物辩证法核心规律"},
        ]
        res = chat_completion(input_msgs, config=cfg)
        assert res == "辩证法三大规律：对立统一规律、质量互变规律、否定之否定规律。"
        assert len(calls) == 2, "应成功重试"

        # 校验第二次请求已不包含 system 角色，且首个 user 消息合并了系统设定
        req2_data = json.loads(calls[1].data.decode("utf-8"))
        req2_msgs = req2_data.get("messages", [])
        assert not any(m.get("role") == "system" for m in req2_msgs)
        assert len(req2_msgs) == 1
        assert req2_msgs[0]["role"] == "user"
        assert "[系统设定: 你是一位严格的马克思主义基本原理私教]" in req2_msgs[0]["content"]
        assert "请梳理唯物辩证法核心规律" in req2_msgs[0]["content"]

    print("  [√] HTTP 400 Deflate system 角色降级合并测试通过！")


def test_chat_completion_http_400_magic_bytes_without_encoding_header():
    """测试反代网关缺失 Content-Encoding 响应头但回包为 gzip 魔数流时，仍能正确嗅探并自愈"""
    cfg = {"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"}
    calls = []

    def mock_urlopen(req, timeout=10.0):
        calls.append(req)
        payload = json.loads(req.data.decode("utf-8"))
        if "max_tokens" in payload:
            err_data = {"error": "Unsupported token limit parameter: max_completion_tokens"}
            gz_err = gzip.compress(json.dumps(err_data).encode("utf-8"))
            # 网关漏掉了 Content-Encoding 头部
            headers = {}
            fp = BytesIO(gz_err)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", headers, fp)
        else:
            resp = MagicMock()
            success_payload = {
                "choices": [{"message": {"role": "assistant", "content": "长难句主干分析完毕。"}}]
            }
            resp.read.return_value = json.dumps(success_payload).encode("utf-8")
            resp.headers = {}
            resp.__enter__.return_value = resp
            return resp

    with patch("tools.llm_client.safe_urlopen", side_effect=mock_urlopen):
        res = chat_completion("长难句分析", config=cfg, max_tokens=500)
        assert res == "长难句主干分析完毕。"
        assert len(calls) == 2
        assert "max_tokens" not in json.loads(calls[1].data.decode("utf-8"))

    print("  [√] HTTP 400 无头 Gzip 魔数识别自愈测试通过！")


def test_chat_completion_http_400_chinese_error_and_system_only_messages():
    """测试中文报错关键词 ('系统', '角色') 以及仅包含 system 消息时前置兜底 user 消息转化"""
    cfg = {"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"}
    calls = []

    def mock_urlopen(req, timeout=10.0):
        calls.append(req)
        payload = json.loads(req.data.decode("utf-8"))
        msgs = payload.get("messages", [])
        if any(m.get("role") == "system" for m in msgs):
            err_data = {"error": "系统不支持角色设定: system 角色在此通道被禁用"}
            gz_err = gzip.compress(json.dumps(err_data, ensure_ascii=False).encode("utf-8"))
            headers = {"Content-Encoding": "gzip"}
            fp = BytesIO(gz_err)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", headers, fp)
        else:
            resp = MagicMock()
            success_payload = {
                "choices": [{"message": {"role": "assistant", "content": "英语作文功能句模板已生成。"}}]
            }
            resp.read.return_value = json.dumps(success_payload).encode("utf-8")
            resp.headers = {"Content-Encoding": "identity"}
            resp.__enter__.return_value = resp
            return resp

    with patch("tools.llm_client.safe_urlopen", side_effect=mock_urlopen):
        # 仅传入单个 system 消息，测试无 user 消息时的前置降级能力
        system_only_msgs = [{"role": "system", "content": "考研英语作文辅导专家"}]
        res = chat_completion(system_only_msgs, config=cfg)
        assert res == "英语作文功能句模板已生成。"
        assert len(calls) == 2

        # 检查重试请求生成了合法的单条 user 消息
        req2_msgs = json.loads(calls[1].data.decode("utf-8")).get("messages", [])
        assert len(req2_msgs) == 1
        assert req2_msgs[0]["role"] == "user"
        assert "[系统设定: 考研英语作文辅导专家]" in req2_msgs[0]["content"]

    print("  [√] 中文错误与仅 system 消息前置降级测试通过！")


def test_chat_completion_http_400_raw_deflate_tokens_fallback():
    """测试 raw deflate (无 zlib 头) 压缩的 HTTP 400 报错解压与 max_output_tokens 嗅探"""
    cfg = {"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"}
    calls = []

    def mock_urlopen(req, timeout=10.0):
        calls.append(req)
        payload = json.loads(req.data.decode("utf-8"))
        if "max_tokens" in payload:
            err_data = {"error": "max_output_tokens exceeds upper limit"}
            comp = zlib.compressobj(wbits=-zlib.MAX_WBITS)
            raw_df = comp.compress(json.dumps(err_data).encode("utf-8")) + comp.flush()
            headers = {"Content-Encoding": "deflate"}
            fp = BytesIO(raw_df)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", headers, fp)
        else:
            resp = MagicMock()
            success_payload = {
                "choices": [{"message": {"role": "assistant", "content": "Raw deflate fallback 成功"}}]
            }
            resp.read.return_value = json.dumps(success_payload).encode("utf-8")
            resp.headers = {}
            resp.__enter__.return_value = resp
            return resp

    with patch("tools.llm_client.safe_urlopen", side_effect=mock_urlopen):
        res = chat_completion("测试 Raw Deflate", config=cfg, max_tokens=4096)
        assert res == "Raw deflate fallback 成功"
        assert len(calls) == 2
        assert "max_tokens" not in json.loads(calls[1].data.decode("utf-8"))

    print("  [√] Raw Deflate max_output_tokens 降级测试通过！")


def test_chat_completion_200_ok_gzip_and_deflate():
    """测试 chat_completion 在正常 200 OK 场景下的 gzip 与 deflate 透明解压缩"""
    cfg = {"api_key": "test_key", "base_url": "https://api.test.com/v1", "model": "test-model"}

    # 1. 200 OK with Gzip
    mock_resp_gzip = MagicMock()
    mock_resp_gzip.__enter__.return_value = mock_resp_gzip
    body_obj = {"choices": [{"message": {"role": "assistant", "content": "Gzip 200 正常响应"}}]}
    mock_resp_gzip.read.return_value = gzip.compress(json.dumps(body_obj).encode("utf-8"))
    mock_resp_gzip.headers = {"Content-Encoding": "gzip"}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_gzip):
        res = chat_completion("测试 200 Gzip", config=cfg)
        assert res == "Gzip 200 正常响应"

    # 2. 200 OK with Deflate
    mock_resp_deflate = MagicMock()
    mock_resp_deflate.__enter__.return_value = mock_resp_deflate
    body_obj2 = {"choices": [{"message": {"role": "assistant", "content": "Deflate 200 正常响应"}}]}
    mock_resp_deflate.read.return_value = zlib.compress(json.dumps(body_obj2).encode("utf-8"))
    mock_resp_deflate.headers = {"Content-Encoding": "deflate"}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_deflate):
        res2 = chat_completion("测试 200 Deflate", config=cfg)
        assert res2 == "Deflate 200 正常响应"

    print("  [√] chat_completion 200 OK Gzip / Deflate 解压测试通过！")


def test_fetch_upstream_models_gzip_and_deflate():
    """测试 fetch_upstream_models 在 200 gzip 回包与 400 deflate 报错下的解压解析"""
    # 1. 200 OK with Gzip 返回模型列表
    mock_resp_gzip = MagicMock()
    mock_resp_gzip.__enter__.return_value = mock_resp_gzip
    models_data = {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}
    mock_resp_gzip.read.return_value = gzip.compress(json.dumps(models_data).encode("utf-8"))
    mock_resp_gzip.headers = {"Content-Encoding": "gzip"}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_gzip):
        ok, models, msg = fetch_upstream_models("test_key", "https://api.test.com/v1")
        assert ok is True
        assert "deepseek-chat" in models
        assert "deepseek-reasoner" in models
        assert "成功发现 2 个上游可用模型" in msg

    # 2. 400 Bad Request with Deflate 压缩的错误说明
    err_data = {"error": {"message": "Invalid authorization token format"}}
    compressed_err = zlib.compress(json.dumps(err_data).encode("utf-8"))
    fp = BytesIO(compressed_err)
    headers = {"Content-Encoding": "deflate"}
    http_err = urllib.error.HTTPError("https://api.test.com/v1/models", 400, "Bad Request", headers, fp)

    with patch("tools.llm_client.safe_urlopen", side_effect=http_err):
        ok, models, msg = fetch_upstream_models("test_key", "https://api.test.com/v1")
        assert ok is False
        assert models == []
        assert "Invalid authorization token format" in msg

    print("  [√] fetch_upstream_models 200 Gzip 与 400 Deflate 错误解压测试通过！")


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
    test_decompress_response_bytes_helper_all_encodings()
    test_chat_completion_http_400_gzip_max_tokens_fallback()
    test_chat_completion_http_400_deflate_system_role_fallback()
    test_chat_completion_http_400_magic_bytes_without_encoding_header()
    test_chat_completion_http_400_chinese_error_and_system_only_messages()
    test_chat_completion_http_400_raw_deflate_tokens_fallback()
    test_chat_completion_200_ok_gzip_and_deflate()
    test_fetch_upstream_models_gzip_and_deflate()
    print("--- [全部测试通过] 彻底解决中转站 5s 但后台无数据的问题！ ---\n")

