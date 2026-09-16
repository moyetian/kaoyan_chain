# -*- coding: utf-8 -*-
"""
色彩数学与可读性门禁 (WCAG)

[设计意图] 允许用户自定义主题（主色/圆角/密度）就必须同时给他一条兜底下限，
否则很容易做出「浅灰字配浅灰底」这种看不清的界面，而用户只会觉得"这软件坏了"。
本模块提供两件事：

1. 纯函数色彩运算 —— 供 token 派生使用（悬停色/按下色/输入框底色等由主色推导，
   用户只改一个主色，其余自动协调，不必手填二十个色值）。
2. WCAG 对比度校验 —— 主题加载时强制把关，不达标即拒绝并回退到内置预设。

对比度算法依据 WCAG 2.1 相对亮度定义（sRGB 线性化后再加权求和）。
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

#: WCAG 阈值：正文 4.5:1，大字与非文字界面元素 3:1
CONTRAST_TEXT = 4.5
CONTRAST_LARGE = 3.0


def parse_hex(value: str) -> Tuple[int, int, int]:
    """把 ``#rgb`` / ``#rrggbb`` 解析为 ``(r, g, b)``；非法输入抛 ValueError。"""
    m = _HEX_RE.match(str(value).strip())
    if not m:
        raise ValueError(f"不是合法的十六进制颜色: {value!r}")
    body = m.group(1)
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)
    return (int(body[0:2], 16), int(body[2:4], 16), int(body[4:6], 16))


def to_hex(rgb: Sequence[float]) -> str:
    """``(r, g, b)`` → ``#rrggbb``（自动夹紧到 0~255）。"""
    parts = []
    for c in rgb:
        v = int(round(c))
        parts.append(max(0, min(255, v)))
    return "#{:02x}{:02x}{:02x}".format(*parts)


def is_hex(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX_RE.match(value.strip()))


def is_dark(value: str) -> bool:
    """判断颜色是否偏暗（用于决定派生时该加深还是该提亮）。"""
    r, g, b = parse_hex(value)
    return relative_luminance((r, g, b)) < 0.5


# ── 明暗调整 ────────────────────────────────────────────────────

def _adjust(value: str, amount: float) -> str:
    """按比例向黑（amount<0）或向白（amount>0）靠拢，amount ∈ (-1, 1)。"""
    r, g, b = parse_hex(value)
    t = max(-1.0, min(1.0, amount))
    if t >= 0:
        return to_hex((r + (255 - r) * t, g + (255 - g) * t, b + (255 - b) * t))
    return to_hex((r * (1 + t), g * (1 + t), b * (1 + t)))


def lighten(value: str, amount: float) -> str:
    return _adjust(value, abs(amount))


def darken(value: str, amount: float) -> str:
    return _adjust(value, -abs(amount))


def mix(a: str, b: str, ratio: float = 0.5) -> str:
    """按比例混合两色（ratio=0 取 a，1 取 b）。"""
    ra, ga, ba = parse_hex(a)
    rb, gb, bb = parse_hex(b)
    t = max(0.0, min(1.0, ratio))
    return to_hex((ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t))


def rgba(value: str, alpha: float) -> str:
    """输出 QSS/CSS 通用的 ``rgba(r, g, b, a)`` 字面量。"""
    r, g, b = parse_hex(value)
    return f"rgba({r}, {g}, {b}, {max(0.0, min(1.0, alpha)):.2f})"


# ── WCAG 对比度 ────────────────────────────────────────────────

def _linearize(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: Sequence[float]) -> float:
    """WCAG 相对亮度（0=黑，1=白）。"""
    r, g, b = (_linearize(float(c)) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """两色对比度（1.0 ~ 21.0）。"""
    l1 = relative_luminance(parse_hex(fg))
    l2 = relative_luminance(parse_hex(bg))
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


#: 必须校验的配对：(前景 token, 背景 token, 阈值, 说明)
REQUIRED_PAIRS: Tuple[Tuple[str, str, float, str], ...] = (
    ("fg", "surf", CONTRAST_TEXT, "正文文字 / 表面"),
    ("fg", "bg", CONTRAST_TEXT, "正文文字 / 页面底色"),
    ("mut", "surf", CONTRAST_TEXT, "次要文字 / 表面"),
    ("acc", "surf", CONTRAST_LARGE, "主色（按钮、图标）/ 表面"),
    ("on-acc", "acc", CONTRAST_TEXT, "主色按钮上的文字"),
    ("ok", "surf", CONTRAST_LARGE, "成功色 / 表面"),
    ("warn", "surf", CONTRAST_LARGE, "警示色 / 表面"),
    ("bad", "surf", CONTRAST_LARGE, "错误色 / 表面"),
)


def validate_tokens(tokens: Dict[str, object]) -> List[str]:
    """校验 token 集合的可读性，返回违规说明列表（空列表 = 通过）。

    只校验颜色；缺失的 token 跳过（派生逻辑会先补齐）。
    """
    problems: List[str] = []
    for fg_key, bg_key, threshold, desc in REQUIRED_PAIRS:
        fg, bg = tokens.get(fg_key), tokens.get(bg_key)
        if not is_hex(fg) or not is_hex(bg):
            continue
        try:
            ratio = contrast_ratio(str(fg), str(bg))
        except ValueError:
            continue
        if ratio + 1e-9 < threshold:
            problems.append(
                f"{desc} 对比度不足: {fg_key}={fg} / {bg_key}={bg} "
                f"= {ratio:.2f}:1（要求 ≥ {threshold}:1）")
    return problems
