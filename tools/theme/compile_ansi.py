# -*- coding: utf-8 -*-
"""
终端编译器：Theme → ANSI 调色板

TUI 原先在 ``Colors`` 类里硬编码 ``\\033[91m`` 这类转义码，主题无法统一。
本模块把语义 token 映射成 24 位真彩色转义序列，并保持 ``Colors`` 的字段名
（RESET / BOLD / RED / GREEN / …）不变 —— 调用点零改动。

真彩不受支持时（如旧版 Windows 控制台、``TERM=dumb``）自动降级为 16 色代码，
避免打出乱码；``NO_COLOR`` 环境变量被尊重（业界约定：只要该变量非空即去色）。
"""

from __future__ import annotations

import os
import sys
from typing import Dict, Optional

from .contrast import darken, lighten, mix, parse_hex
from .tokens import Theme

#: 16 色降级表（字段名 → 传统 ANSI 码）
_ANSI16 = {
    "RESET": "\033[0m", "BOLD": "\033[1m", "DIM": "\033[2m",
    "ITALIC": "\033[3m", "UNDERLINE": "\033[4m",
    "RED": "\033[91m", "GREEN": "\033[92m", "YELLOW": "\033[93m", "BLUE": "\033[94m",
    "MAGENTA": "\033[95m", "CYAN": "\033[96m", "WHITE": "\033[97m",
}

#: token → Colors 字段名（一个字段可对应多个候选 token，取第一个存在的）
_FIELD_TOKENS = (
    ("RED", ("bad",)),
    ("GREEN", ("ok",)),
    ("YELLOW", ("warn",)),
    ("CYAN", ("acc",)),
    ("MAGENTA", ("acc-soft", "acc")),
    ("BLUE", ("acc-hover", "acc")),
    ("WHITE", ("fg",)),
)


def supports_truecolor() -> bool:
    """判断当前终端是否支持 24 位真彩。

    保守策略：只有明确知道不支持时才降级（``TERM=dumb``、Windows 且未开
    虚拟终端、非 TTY）。Linux/macOS 主流终端与 Windows Terminal 均支持。
    """
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    term = os.environ.get("TERM", "")
    if term in ("dumb", ""):
        # TERM 为空在 Windows 上很常见，此时看是否处于现代终端
        return sys.platform != "win32" or bool(os.environ.get("WT_SESSION"))
    if not sys.stdout.isatty():
        return False
    return True


def colors_disabled() -> bool:
    """是否应完全去色（尊重 NO_COLOR 约定与非 TTY 管道）。"""
    if os.environ.get("NO_COLOR"):      # 业界约定：只要设置且非空即去色
        return True
    if os.environ.get("KY_NO_COLOR"):
        return True
    return not sys.stdout.isatty()


def _fg(hex_color: str) -> str:
    r, g, b = parse_hex(hex_color)
    return f"\033[38;2;{r};{g};{b}m"


def _bg(hex_color: str) -> str:
    r, g, b = parse_hex(hex_color)
    return f"\033[48;2;{r};{g};{b}m"


def render_ansi(theme: Theme, truecolor: Optional[bool] = None,
                disabled: Optional[bool] = None) -> Dict[str, str]:
    """渲染 ANSI 调色板字典（可直接作为 ``Colors`` 类的字段来源）。

    :param truecolor: 强制指定是否真彩；默认自动探测。
    :param disabled: 强制去色；默认按 ``NO_COLOR`` / 非 TTY 判定。
    """
    use_tc = supports_truecolor() if truecolor is None else truecolor
    off = colors_disabled() if disabled is None else disabled
    if off:
        return {name: "" for name in list(_ANSI16) + ["BG", "MUTED", "ACCENT", "OK", "WARN", "BAD"]}

    if not use_tc:
        return dict(_ANSI16) | {
            "BG": "", "MUTED": _ANSI16["DIM"], "ACCENT": _ANSI16["CYAN"],
            "OK": _ANSI16["GREEN"], "WARN": _ANSI16["YELLOW"], "BAD": _ANSI16["RED"],
        }

    palette: Dict[str, str] = {
        "RESET": "\033[0m", "BOLD": "\033[1m", "DIM": "\033[2m",
        "ITALIC": "\033[3m", "UNDERLINE": "\033[4m",
    }
    for field, candidates in _FIELD_TOKENS:
        value = None
        for token in candidates:
            raw = theme.get(token)
            if isinstance(raw, str) and raw.startswith("#"):
                value = raw
                break
        palette[field] = _fg(value or "#ffffff")

    # 语义补充字段（TUI 新增能力：次要文字与语义色可直接取用）
    palette["MUTED"] = _fg(theme.color("mut", "#94a3b8"))
    palette["ACCENT"] = _fg(theme.color("acc", "#a78bfa"))
    palette["OK"] = _fg(theme.color("ok", "#34d399"))
    palette["WARN"] = _fg(theme.color("warn", "#fbbf24"))
    palette["BAD"] = _fg(theme.color("bad", "#f87171"))
    palette["BG"] = _bg(theme.color("bg", "#090d16"))
    # 深色终端上让强调色再亮一点、浅色终端上再深一点，保证对比度
    palette["ACCENT_SOFT"] = _fg(
        lighten(theme.color("acc", "#a78bfa"), 0.25) if theme.mode == "dark"
        else darken(theme.color("acc", "#a78bfa"), 0.15))
    palette["SEP"] = _fg(mix(theme.color("line", "#1e293b"), theme.color("mut", "#94a3b8"), 0.5))
    return palette


#: 允许往 Colors 类上新增的扩展字段（语义色，供新代码使用）
_EXTENDED_FIELDS = frozenset({"MUTED", "ACCENT", "ACCENT_SOFT", "OK", "WARN", "BAD", "BG", "SEP"})


def apply_to_colors_class(colors_cls, theme: Theme,
                          truecolor: Optional[bool] = None,
                          disabled: Optional[bool] = None) -> None:
    """把渲染结果写进既有 ``Colors`` 类（原地更新，调用点零改动）。

    只覆盖类上已有的字段 + 白名单内的语义扩展字段，不往别人的类里塞
    意料之外的属性。``truecolor`` / ``disabled`` 可显式指定，便于测试与
    「管道里跑但仍想要彩色」的特殊场景。
    """
    for name, code in render_ansi(theme, truecolor=truecolor, disabled=disabled).items():
        if hasattr(colors_cls, name) or name in _EXTENDED_FIELDS:
            setattr(colors_cls, name, code)


__all__ = [
    "apply_to_colors_class",
    "colors_disabled",
    "render_ansi",
    "supports_truecolor",
]
