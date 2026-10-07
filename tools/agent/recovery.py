# -*- coding: utf-8 -*-
"""Final-answer recovery and JSON delivery helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional


def parse_fallback_tool_calls(content: str, now: Callable[[], float]) -> list[dict[str, Any]]:
    calls = []
    for idx, raw in enumerate(re.findall(r"<tool_call>(.*?)</tool_call>", str(content or ""), re.DOTALL)):
        try:
            data = json.loads(raw.strip())
            calls.append({"id": f"call_fallback_{idx}_{int(now())}", "type": "function",
                          "function": {"name": data.get("name"), "arguments": data.get("arguments", {})}})
        except Exception:
            continue
    return calls


def strip_code_fence(text: str) -> str:
    value = (text or "").strip()
    if not value.startswith("```"):
        return value
    lines = value.splitlines()[1:]
    if lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines).strip() or value


def extract_json_payload(text: str) -> Any:
    value = str(text or "").strip()
    for candidate in (value, strip_code_fence(value)):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    # 从最靠前的 { / [ 起点尝试：数组场景必须命中 "["，否则
    # raw_decode 会把 [{...},{...}] 截成第一个对象（只修语法不缩数据）。
    starts = sorted(i for i in (value.find("{"), value.find("[")) if i >= 0)
    for start in starts:
        try:
            return json.JSONDecoder().raw_decode(value[start:])[0]
        except Exception:
            continue
    return None


def missing_outputs(root, required: list[str]) -> list[str]:
    try:
        return [rel for rel in required if not (root / rel).exists()]
    except Exception:
        return []


def autosave_json_outputs(root, missing: list[str], answer: str) -> list[str]:
    payload = extract_json_payload(answer)
    if payload is None:
        return []
    saved = []
    for rel in missing:
        path = root / rel
        if path.suffix.lower() != ".json":
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            saved.append(rel)
        except Exception:
            continue
    return saved


def build_json_repair_instruction(text: str, error: str) -> str:
    return (
        "（系统提示）你上一条最终回复以 { 或 [ 开头，本应是完整 JSON，"
        f"但存在语法错误：{error}\n"
        "请**仅修复 JSON 语法**（补齐或修正括号、引号、逗号、键名等），"
        "**不得增删或改变任何实质内容**——所有事实、数字、链接、文字必须"
        "保持原样。直接输出修复后的完整 JSON 本体：不要任何解释，不要用 "
        "``` 代码块包裹。\n\n=== 你上一条回复的原文 ===\n" + text
    )


def validate_repaired_json(candidate: str) -> str | None:
    value = strip_code_fence(candidate)
    if "<tool_call>" in value:
        value = value.split("<tool_call>")[0].strip()
    if value[:1] not in ("{", "["):
        return None
    try:
        json.loads(value)
    except Exception:
        return None
    return value


def finalize_attempts(*, api_failed: bool, messages: list[dict[str, Any]],
                      minimal_messages: list[dict[str, Any]],
                      instructions: tuple[str, str, str],
                      request: Callable[[list[dict[str, Any]], str], str]) -> str:
    """Run the bounded final-answer recovery chain and return first non-empty text."""
    full, retry, minimal = instructions
    attempts = ((minimal_messages, minimal), (messages, full)) if api_failed else (
        (messages, full), (messages, retry), (minimal_messages, minimal))
    for attempt_messages, instruction in attempts:
        try:
            content = request(attempt_messages, instruction)
        except Exception:
            content = ""
        if isinstance(content, str) and content.strip():
            return content
    return ""
