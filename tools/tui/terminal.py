# -*- coding: utf-8 -*-
"""
终端能力与排版工具（宽度感知、颜色开关、VT 启用、清屏）

[为什么单独成模块] 改造前这些能力散在 ``tui_navigator.py`` 里且有两个真问题：
  1. **宽度写死 84**：`TOTAL_PANEL_WIDTH = 84` 与终端实际列数无关，
     窄终端会折行错位、宽终端右侧留一大片空白；
  2. **ANSI 在旧版 Windows 控制台会打成乱码**：仅靠 `os.system("color")`
     并不足以启用虚拟终端处理（ENABLE_VIRTUAL_TERMINAL_PROCESSING），
     转义序列会被当字面量打印出来。

本模块把「终端到底多大、支不支持颜色」这类探测收敛到一处，
供纯文本 TUI 与 textual 版 TUI 共用。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import unicodedata
from typing import Optional

#: ANSI 转义序列（用于计算可见宽度）
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

#: 面板宽度兜底值与可接受区间
DEFAULT_WIDTH = 84
MIN_WIDTH = 60
MAX_WIDTH = 110


# ── 颜色开关 ────────────────────────────────────────────────────

def is_tty() -> bool:
    """标准输出是否连着真实终端。"""
    try:
        return bool(sys.stdout.isatty())
    except Exception:                     # pragma: no cover
        return False


def colors_disabled() -> bool:
    """是否应完全去色。

    尊重 ``NO_COLOR``（业界约定：该变量存在且非空即去色）与 ``KY_NO_COLOR``，
    并在非 TTY（管道/重定向）下自动去色。
    """
    if os.environ.get("NO_COLOR") or os.environ.get("KY_NO_COLOR"):
        return True
    return not is_tty()


# ── Windows 虚拟终端 ────────────────────────────────────────────

def enable_windows_vt() -> bool:
    """在 Windows 上启用 ANSI 虚拟终端处理。

    [缺陷修复] 旧实现只调 `os.system("color")`：它只是给控制台设了默认前景/背景色，
    **并不会**打开 ENABLE_VIRTUAL_TERMINAL_PROCESSING。在 Windows 10 早期版本与
    传统 conhost 下，ANSI 转义序列会被原样打印成 ``←[96m`` 这类乱码。
    此处直接改控制台模式位，成功即说明支持 ANSI 渲染。
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32                    # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)                  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False                                     # 无控制台（如重定向）
        enable_vt = 0x0004
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except Exception:
        return False


# ── 宽度感知 ────────────────────────────────────────────────────

def terminal_columns(fallback: int = DEFAULT_WIDTH) -> int:
    """终端列数；非 TTY 或探测失败时返回兜底值。"""
    try:
        cols = shutil.get_terminal_size((fallback, 24)).columns
    except Exception:                     # pragma: no cover
        return fallback
    return cols if cols > 0 else fallback


def panel_width(fallback: int = DEFAULT_WIDTH,
                minimum: int = MIN_WIDTH,
                maximum: int = MAX_WIDTH) -> int:
    """面板宽度：跟随终端列数，并夹在 ``[minimum, maximum]`` 之间。

    夹紧的理由：过窄会把两栏信息挤成多行、过宽则单行内容被拉得很稀疏，
    可读性都变差。
    """
    width = terminal_columns(fallback)
    if width <= 0:
        width = fallback
    return max(minimum, min(maximum, width))


def is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF or
        0x2600 <= cp <= 0x27BF or
        0x2300 <= cp <= 0x23FF or
        0x2B50 <= cp <= 0x2B55
    )


def display_width(s: str) -> int:
    """字符串在终端的实际打印宽度（去除 ANSI，精准判定全角汉字与 Emoji）。"""
    clean_s = _ANSI_RE.sub("", str(s))
    clean_s = clean_s.replace("\ufe0f", "").replace("\u200d", "")
    w = 0
    for ch in clean_s:
        if is_emoji_char(ch) or unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


def pad_display(s: str, target_w: int) -> str:
    cur_w = display_width(s)
    if cur_w >= target_w:
        return s
    return s + " " * (target_w - cur_w)


def center_display(s: str, target_w: int) -> str:
    """居中填充（左右各补一半空格），供盒状边框标题使用。"""
    cur_w = display_width(s)
    if cur_w >= target_w:
        return s
    left = (target_w - cur_w) // 2
    return " " * left + s + " " * (target_w - cur_w - left)


# ── 清屏 ────────────────────────────────────────────────────────

def clear_screen() -> None:
    """清屏。非 TTY 时不做任何事（避免往管道里灌控制字符）。"""
    if not is_tty():
        return
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except Exception:                     # pragma: no cover - 清屏失败不影响功能
        return


def supports_textual() -> Optional[bool]:
    """textual 是否可用（None 表示探测失败）。"""
    try:
        import importlib.util

        return importlib.util.find_spec("textual") is not None
    except Exception:                     # pragma: no cover
        return None


__all__ = [
    "DEFAULT_WIDTH",
    "MAX_WIDTH",
    "MIN_WIDTH",
    "center_display",
    "clear_screen",
    "colors_disabled",
    "display_width",
    "enable_windows_vt",
    "is_emoji_char",
    "is_tty",
    "pad_display",
    "panel_width",
    "supports_textual",
    "terminal_columns",
]
