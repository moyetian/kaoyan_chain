# -*- coding: utf-8 -*-
"""Answer presentation and citation extraction helpers for AgentRunner."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable


def display_final_answer(text: str, *, quiet: bool = False,
                         stream_callback: Callable[[str], None] | None = None) -> None:
    if not text:
        return
    if stream_callback:
        for i in range(0, len(text), 12):
            chunk = text[i:i + 12]
            stream_callback(chunk)
            if not quiet:
                sys.stdout.write(chunk)
                sys.stdout.flush()
                time.sleep(0.02)
    elif not quiet:
        for char in text:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(0.002)
    if not quiet:
        print()


def extract_page_quote_refs(text: str) -> list[dict[str, Any]]:
    stripped = str(text or "").strip()
    if not stripped:
        return []
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines.pop()
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        return []
    source_file = str(data.get("source_file") or data.get("来源文件") or "").strip() if isinstance(data, dict) else ""
    out: list[dict[str, Any]] = []
    def walk(node: Any) -> None:
        if len(out) >= 50:
            return
        if isinstance(node, dict):
            page = node.get("page", node.get("页码"))
            quote = node.get("quote", node.get("原文", node.get("引用")))
            if page is not None and isinstance(quote, str) and quote.strip():
                out.append({"page": page, "quote": quote.strip(), "source_file": source_file})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(data)
    return out


def emit_citations(final_answer: str, messages: list[dict[str, Any]], workspace_root: Path,
                   max_items: int = 20) -> None:
    try:
        text = str(final_answer or "")
        if not text.strip():
            return
        evidence = "\n".join(str(m.get("content") or "") for m in messages
                              if isinstance(m, dict) and m.get("role") == "tool")
        citations: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for match in re.finditer(r"https?://[^\s\"'<>（）()【】\[\]]+", text):
            url = match.group(0).rstrip(".,;:!?，。；：！？")
            if url in seen:
                continue
            seen.add(url)
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            claim = " ".join(text[start:(len(text) if end < 0 else end)].split())[:300]
            supported = url in evidence
            citations.append({"citation_id": f"cit{len(citations)+1}", "claim": claim,
                              "source_ref": url, "supported": supported,
                              "unsupported_reason": None if supported else "该 URL 未在本轮工具结果中出现（未经检索证据核验）",
                              "judge": "agent_reported"})
            if len(citations) >= max_items:
                break
        for ref in extract_page_quote_refs(text):
            if len(citations) >= max_items:
                break
            quote = str(ref.get("quote") or "")
            key = (ref.get("source_file"), ref.get("page"), quote[:80])
            if key in seen:
                continue
            seen.add(key)
            supported = bool(quote[:40]) and quote[:40] in evidence
            src = str(ref.get("source_file") or "")
            citations.append({"citation_id": f"cit{len(citations)+1}", "claim": quote[:300],
                              "source_ref": f"{src} 第 {ref.get('page')} 页" if src else f"第 {ref.get('page')} 页",
                              "supported": supported,
                              "unsupported_reason": None if supported else "引文未在本轮工具结果中出现（未经原文核验）",
                              "judge": "agent_reported"})
        path = Path(workspace_root) / ".memory" / "last_citations.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(citations, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
