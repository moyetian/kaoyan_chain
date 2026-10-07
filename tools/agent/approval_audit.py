# -*- coding: utf-8 -*-
"""Append-only audit trail for permission decisions."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

#: 审计落点环境变量覆盖（测试隔离 / 特殊部署）；设置后读写统一走该目录。
AUDIT_ROOT_ENV = "KY_APPROVAL_AUDIT_ROOT"

#: 单文件大小上限：超过则轮转（当前文件转 .1，覆盖更旧归档），防无限增长。
MAX_AUDIT_BYTES = 5 * 1024 * 1024


def resolve_audit_root(workspace_root) -> Path:
    """解析审批审计根目录 —— 写入与读取的**唯一实现处**。

    优先级：
    1. 环境变量 ``KY_APPROVAL_AUDIT_ROOT``（测试隔离 / 特殊部署）；
    2. 工作区父目录 ``.kaoyan_chain_audit``（默认：不污染工作区字节）；
    3. ``%LOCALAPPDATA%/kaoyan-study-chain/approval_audit``（打包版安装目录
       父目录不可写时的兜底，避免审计静默失效）。
    """
    override = os.environ.get(AUDIT_ROOT_ENV)
    if override:
        return Path(override)
    primary = Path(workspace_root).parent / ".kaoyan_chain_audit"
    try:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "kaoyan-study-chain" / "approval_audit"


def _rotate_if_oversized(path: Path) -> None:
    """超过上限时把当前文件转 .1（覆盖旧归档），保证 append-only 不无限增长。"""
    try:
        if path.exists() and path.stat().st_size >= MAX_AUDIT_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


def append_approval_event(root: Path, *, tool_name: str, level: int,
                          allowed: bool, reason: str, mode: str,
                          interactive: bool, args: dict[str, Any] | None = None) -> None:
    """Write one redacted, append-only approval event.

    Arguments are intentionally reduced to path/url keys and bounded strings;
    credentials or full command payloads must never enter the audit log.
    """
    safe_args = {}
    for key in ("path", "file_name", "target_file", "url"):
        value = (args or {}).get(key)
        if value is not None:
            safe_args[key] = str(value)[:240]
    event = {
        "ts": time.time(), "tool": str(tool_name), "level": int(level),
        "allowed": bool(allowed), "reason": str(reason)[:500],
        "mode": str(mode), "interactive": bool(interactive), "args": safe_args,
    }
    path = Path(root) / ".memory" / "approval_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_oversized(path)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_approval_events(root: Path, limit: int = 100) -> list[dict[str, Any]]:
    path = Path(root) / ".memory" / "approval_audit.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
