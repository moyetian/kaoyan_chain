# -*- coding: utf-8 -*-
"""
考研学习链 · 统一大语言模型客户端 (LLM Client)
提供极简、高鲁棒的 OpenAI 兼容端点调用与模型探查功能，零第三方重度依赖。
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

_LOG = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# 确保在各种导入路径与 pytest mock 环境下 tools.llm_client 与 llm_client 指向同一模块对象
sys.modules.setdefault("tools.llm_client", sys.modules[__name__])
sys.modules.setdefault("llm_client", sys.modules[__name__])
sys.modules["tools.llm_client"] = sys.modules[__name__]
sys.modules["llm_client"] = sys.modules[__name__]


def normalize_openai_url(base_url: str, endpoint: str = "chat/completions") -> str:
    """智能规范化 OpenAI 兼容接口地址。
    兼容 /v1, /v2, /v3, /v4, /v1beta/openai 等端点及反代。
    """
    b = (base_url or "https://api.deepseek.com/v1").strip().rstrip("/")
    ep = (endpoint or "chat/completions").strip().lstrip("/")
    # 如果给定的 URL 已经以 chat/completions 结尾，但我们请求的是 models 或其他 endpoint
    if b.endswith("/chat/completions"):
        if ep == "chat/completions":
            return b
        b = b[:-len("/chat/completions")].rstrip("/")

    if b.endswith("/" + ep):
        return b
    if re.search(r"/v\d+[a-z]*(?:/.*)?$", b) or b.endswith("/openai"):
        return f"{b}/{ep}"
    return f"{b}/v1/{ep}"


def get_llm_config(workspace_root: Optional[Path | str] = None) -> Dict[str, Any]:
    """读取本地工作区 ky_config.json 中的 LLM 配置。"""
    ws = Path(workspace_root) if workspace_root else ROOT
    cfg_file = ws / "ky_config.json"
    if not cfg_file.exists():
        return {}
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "api_key": str(data.get("api_key", "")).strip(),
                "base_url": str(data.get("base_url", "https://api.deepseek.com/v1")).strip(),
                "model": str(data.get("model", "deepseek-chat")).strip(),
                "temperature": float(data.get("temperature", 0.3)),
            }
    except Exception as e:
        _LOG.debug("读取 ky_config.json 失败: %s", e)
    return {}


def is_llm_configured(config: Optional[Dict[str, Any]] = None, workspace_root: Optional[Path | str] = None) -> bool:
    """检查是否配置了有效的 LLM API Key 与模型。"""
    cfg = config if config is not None else get_llm_config(workspace_root)
    api_key = cfg.get("api_key", "").strip()
    base_url = cfg.get("base_url", "").strip()
    model = cfg.get("model", "").strip()
    return bool(api_key and base_url and model and not api_key.startswith("sk-placeholder"))


def fetch_upstream_models(api_key: str, base_url: str, timeout: float = 15.0) -> Tuple[bool, List[str], str]:
    """异步请求 GET /models 端点，动态探查上游服务商支持的模型列表。"""
    k = (api_key or "").strip()
    b = (base_url or "").strip()
    if not k:
        return False, [], "缺少 API Key，无法探查上游模型"
    if not b:
        return False, [], "缺少 Base URL，无法探查上游模型"

    url = normalize_openai_url(b, "models")
    headers = {
        "Authorization": f"Bearer {k}",
        "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain/1.0",
        "Accept": "application/json",
        "Connection": "close",
    }

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # 编码解压缩处理
            enc = getattr(resp.headers, "get", lambda *_: "")("Content-Encoding", "").lower()
            if enc == "gzip":
                import gzip
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            elif enc == "deflate":
                import zlib
                try:
                    raw = zlib.decompress(raw)
                except Exception:
                    pass

            text = raw.decode("utf-8", errors="ignore")
            data = json.loads(text)

            models: List[str] = []
            raw_list = []
            if isinstance(data, dict):
                if isinstance(data.get("data"), list):
                    raw_list = data["data"]
                elif isinstance(data.get("models"), list):
                    raw_list = data["models"]
            elif isinstance(data, list):
                raw_list = data

            for item in raw_list:
                if isinstance(item, dict) and "id" in item:
                    models.append(str(item["id"]).strip())
                elif isinstance(item, str):
                    models.append(item.strip())

            if not models:
                return False, [], "上游端点返回模型列表为空"

            # 排序：推荐模型排前面
            def _score_model(m: str) -> int:
                m_low = m.lower()
                if "chat" in m_low or "plus" in m_low or "pro" in m_low:
                    return 0
                if "deepseek" in m_low or "gpt-4" in m_low or "qwen" in m_low or "glm" in m_low:
                    return 1
                return 2

            sorted_models = sorted(list(dict.fromkeys(models)), key=lambda x: (_score_model(x), x))
            return True, sorted_models, f"成功发现 {len(sorted_models)} 个上游可用模型"

    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            raw_err = e.read()
            enc = (getattr(e.headers, "get", lambda *_: "")("Content-Encoding") or "").lower()
            if enc == "gzip" or raw_err.startswith(b"\x1f\x8b"):
                import gzip
                try:
                    raw_err = gzip.decompress(raw_err)
                except Exception:
                    pass
            err_body = raw_err.decode("utf-8", errors="ignore")
        except Exception:
            pass
        err_msg = ""
        try:
            err_json = json.loads(err_body)
            if isinstance(err_json, dict) and "error" in err_json:
                err_val = err_json["error"]
                err_msg = err_val.get("message", "") if isinstance(err_val, dict) else str(err_val)
            elif isinstance(err_json, dict) and "message" in err_json:
                err_msg = str(err_json["message"])
        except Exception:
            pass
        detail = err_msg or f"HTTP {e.code}"
        if e.code in (401, 403):
            return False, [], f"鉴权失败 (HTTP {e.code})：API Key 无效或过期"
        elif e.code == 404:
            return False, [], f"端点未找到 (HTTP 404)：上游未开放 /models 接口或 Base URL 路径需修正"
        return False, [], f"探查接口返回错误 (HTTP {e.code}): {detail}"
    except Exception as e:
        err_str = str(e)
        if "timed out" in err_str.lower():
            return False, [], f"探查超时 ({timeout}s)，网络连接缓慢"
        return False, [], f"网络连接失败: {err_str}"


def chat_completion(
    messages_or_prompt: Union[str, List[Dict[str, Any]]],
    config: Optional[Dict[str, Any]] = None,
    workspace_root: Optional[Path | str] = None,
    system_prompt: Optional[str] = None,
    temperature: float = 0.3,
    timeout: float = 40.0,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """向 OpenAI 兼容端点发送对话请求并返回模型回复文本。
    支持自动容错：若遇 HTTP 400 提示 max_tokens 或 system 角色受限，自动调整后重试一次。
    """
    cfg = config if config is not None else get_llm_config(workspace_root)
    if not is_llm_configured(cfg):
        return None

    api_key = cfg.get("api_key", "").strip()
    base_url = cfg.get("base_url", "").strip()
    model = cfg.get("model", "").strip()

    # 规范化消息列表
    if isinstance(messages_or_prompt, str):
        msgs: List[Dict[str, Any]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": messages_or_prompt})
    else:
        msgs = list(messages_or_prompt)
        if system_prompt and not any(m.get("role") == "system" for m in msgs):
            msgs.insert(0, {"role": "system", "content": system_prompt})

    url = normalize_openai_url(base_url, "chat/completions")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Core/1.0",
        "Connection": "close",
        "Accept-Encoding": "gzip, deflate, identity",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens is not None and max_tokens > 0:
        payload["max_tokens"] = max_tokens

    def _execute_req(cur_payload: dict, cur_timeout: float) -> Tuple[Optional[str], Optional[urllib.error.HTTPError]]:
        data_bytes = json.dumps(cur_payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=cur_timeout) as resp:
                raw_bytes = resp.read()
                enc = getattr(resp.headers, "get", lambda *_: "")("Content-Encoding", "").lower()
                if enc == "gzip":
                    import gzip
                    try:
                        raw_bytes = gzip.decompress(raw_bytes)
                    except Exception:
                        pass
                elif enc == "deflate":
                    import zlib
                    try:
                        raw_bytes = zlib.decompress(raw_bytes)
                    except Exception:
                        pass
                text = raw_bytes.decode("utf-8", errors="ignore").strip()
                if text.startswith("<html") or text.startswith("<!doctype"):
                    return None, None
                resp_obj = json.loads(text)
                choices = resp_obj.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    content = msg.get("content") or ""
                    return content.strip(), None
                return None, None
        except urllib.error.HTTPError as he:
            return None, he
        except Exception as exc:
            _LOG.debug("chat_completion 网络或超时异常: %s", exc)
            return None, None

    # 尝试第一次发送
    content, http_err = _execute_req(payload, timeout)
    if content:
        return content

    # 错误重试与自适应降级（针对特定反代对 max_tokens 或 system 角色的限制）
    if http_err and http_err.code == 400:
        err_body = ""
        try:
            err_body = http_err.read().decode("utf-8", errors="ignore")
        except Exception:
            pass

        retry_payload = dict(payload)
        # 1. 若报错包含 max_tokens，剔除 max_tokens 字段重试
        if "max_tokens" in err_body.lower() or "token" in err_body.lower():
            retry_payload.pop("max_tokens", None)

        # 2. 若报错包含 system 角色，合并到首个 user 消息中
        if "system" in err_body.lower() or "role" in err_body.lower():
            new_msgs: List[Dict[str, Any]] = []
            sys_text = ""
            for m in msgs:
                if m.get("role") == "system":
                    sys_text += f"[系统设定: {m.get('content', '')}]\n"
                else:
                    new_msgs.append(dict(m))
            if new_msgs and sys_text:
                new_msgs[0]["content"] = sys_text + str(new_msgs[0].get("content", ""))
            retry_payload["messages"] = new_msgs

        content2, _ = _execute_req(retry_payload, timeout)
        if content2:
            return content2

    return None


__all__ = [
    "chat_completion",
    "fetch_upstream_models",
    "get_llm_config",
    "is_llm_configured",
    "normalize_openai_url",
]
