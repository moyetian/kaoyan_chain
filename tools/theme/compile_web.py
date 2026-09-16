# -*- coding: utf-8 -*-
"""
Web 编译器：Theme → CSS 自定义属性（CSS 变量）

看板与实时伴侣共用同一份变量定义，取代改造前「CSS 变量写在 build.py 的 HTML
模板字符串里、live.html 又有另一套变量」的局面。产出为可直接内联进 ``<style>``
的文本块：

    :root { --bg:#090d16; --acc:#a78bfa; ... }      ← 亮色（默认）
    :root[data-t=dark] { ... }                       ← 显式深色
    :media(prefers-color-scheme:dark) → :root:not([data-t=light]) { ... }

命名沿用看板既有的 ``--bg/--surf/--acc`` 体系，使既有 CSS 消费方零改动。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from .tokens import PRESETS, Theme, build_theme, list_presets

_LOG = logging.getLogger(__name__)

#: 注入 HTML 的占位符（build.py 侧用它替换原本硬编码的四段变量块）
CSS_PLACEHOLDER = "{{THEME_CSS}}"

#: 只在 CSS 里有意义、不进 Qt/ANSI 的 token（避免污染其它端）
_CSS_ONLY = ("acc-grad-from", "acc-grad-to", "dur-fast", "dur-base", "dur-slow",
             "ease-std", "ease-emph", "focus-ring", "focus-w", "radius", "radius-sm")


def var_block(theme: Theme, indent: str = "  ") -> str:
    """把一套主题渲染成 ``--key:value`` 声明块（预设规则与基础变量块共用）。"""
    lines: List[str] = []
    for key, value in theme.to_flat().items():
        if key.startswith("fs-") or key.startswith("pad") or key.endswith("-px"):
            continue          # 像素字面量是 Qt 侧便捷 token，Web 用原值
        lines.append(f"{indent}--{key}:{value};")
    # 看板 CSS 里既有 `--acc-grad` 整体渐变变量，单独合成一个保持兼容
    grad = (f"linear-gradient(135deg, {theme.get('acc-grad-from')}, "
            f"{theme.get('acc-grad-to')})")
    lines.append(f"{indent}--acc-grad: {grad};")
    return "\n".join(lines)


def render_css_vars(theme: Optional[Theme] = None,
                    dark_theme: Optional[Theme] = None,
                    include_media_query: bool = True) -> str:
    """渲染完整的主题 CSS 变量块。

    :param theme: 亮色（或默认）主题。
    :param dark_theme: 深色主题；不传则取内置 dark 预设。
    """
    light = theme or build_theme("light")
    dark = dark_theme or build_theme("dark")

    parts = [
        f"/* 由 tools/theme 编译生成 · 请勿手工编辑 */",
        ":root{",
        var_block(light),
        "}",
        ":root[data-t=dark]{",
        var_block(dark),
        "}",
        ":root[data-t=light]{",
        var_block(light),
        "}",
    ]
    if include_media_query:
        parts += [
            "@media(prefers-color-scheme:dark){:root:not([data-t=light]){",
            var_block(dark),
            "}}",
        ]
    return "\n".join(parts)


def render_preset_gallery() -> str:
    """渲染「预设一览」CSS（``.preset-<name>`` 类），供主题选择器预览使用。"""
    blocks = []
    for key, display_name, mode in list_presets():
        theme = build_theme(key)
        body = "\n".join(f"  --{k}:{v};" for k, v in theme.to_flat().items()
                         if not k.startswith(("fs-", "pad")) and not k.endswith("-px"))
        blocks.append(f"/* {display_name} ({mode}) */\n.theme-{key}{{\n{body}\n}}")
    return "\n".join(blocks)


def theme_tokens_for_js(theme_names: Optional[List[str]] = None) -> Mapping[str, Dict[str, str]]:
    """导出 ``{预设名: {token: 值}}``，供前端主题选择器（localStorage 切换）使用。"""
    names = theme_names or [key for key, _n, _m in list_presets()]
    return {name: build_theme(name).to_flat() for name in names if name in PRESETS}


def render_to_file(out_path: Path, theme: Optional[Theme] = None,
                   dark_theme: Optional[Theme] = None) -> Path:
    """把变量块写盘（供构建脚本或手工排查使用）。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_css_vars(theme, dark_theme), encoding="utf-8")
    return out


__all__ = [
    "CSS_PLACEHOLDER",
    "render_css_vars",
    "var_block",
    "render_preset_gallery",
    "render_to_file",
    "theme_tokens_for_js",
]
