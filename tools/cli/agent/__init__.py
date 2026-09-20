# -*- coding: utf-8 -*-
"""
Agent 逻辑层
包含提示词构建、流式打字调度与多模型对话引擎
"""

from tools.cli.agent.engine import (
    build_system_prompt,
    build_demo_syllabus_text,
    normalize_openai_url,
    stream_chat,
    query_llm_reply,
)

__all__ = [
    "build_system_prompt",
    "build_demo_syllabus_text",
    "normalize_openai_url",
    "stream_chat",
    "query_llm_reply",
]
