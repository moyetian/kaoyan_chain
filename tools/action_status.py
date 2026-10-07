# -*- coding: utf-8 -*-
"""Shared classification for text returned by CLI/TUI-backed actions."""

from __future__ import annotations

import re


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_FATAL_HINTS = (
    "异常", "失败", "未组卷", "未指定", "未知", "不存在", "找不到", "未找到",
    "缺少", "无法", "取消", "不可用", "未载入", "错误", "error", "failed",
    "exception",
)

#: [缺陷修复·正常空态被标"执行异常"] 用户可见的「未执行/正常空态」提示词：
#: 本地暂无题源（未组卷）、用户未选文件/未输入而取消 —— 这些不是执行故障，
#: GUI 此前把它们路由到 error_signal，显示成「[×] 执行异常」。
#: 判定规则：以 ``[!]`` 开头 + 命中本组词 + **不含**硬故障词 → 正常空态。
_BENIGN_HINTS = ("未组卷", "已取消", "未输入")
#: 硬故障词：与 _FATAL_HINTS 的差别在于排除「未组卷/取消/未指定」这类
#: 可由用户行为自然产生的状态词（防「[!] 未组卷：…执行异常」被误放行）。
_HARD_FAILURE_HINTS = (
    "异常", "失败", "错误", "不存在", "找不到", "未找到", "缺少", "无法",
    "不可用", "未载入", "error", "failed", "exception",
)


def output_has_failure(text: object) -> bool:
    """Return whether an action output contains a user-visible failure line.

    The dispatcher intentionally keeps its public return value as the
    ``continue-running`` boolean used by the interactive loops.  This helper
    lets GUI and textual TUI classify the captured output without changing
    that compatibility contract.
    """
    if text is None:
        return False
    for line in str(text).splitlines():
        line = _ANSI_RE.sub("", line).strip()
        if line.startswith(("[×]", "[x]", "[X]", "❌")):
            return True
        if line.startswith("[!]"):
            lowered = line.lower()
            # 正常空态/用户取消：无题源、未选文件、未输入 —— 不进失败通道
            if (any(h in line for h in _BENIGN_HINTS)
                    and not any(h in lowered for h in _HARD_FAILURE_HINTS)):
                continue
            if any(hint in lowered for hint in _FATAL_HINTS):
                return True
    return False


__all__ = ["output_has_failure"]
