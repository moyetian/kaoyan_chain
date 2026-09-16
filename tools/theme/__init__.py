# -*- coding: utf-8 -*-
"""
考研学习链 · 设计系统（主题 Token 单一真源）

对外只暴露这里。四端（CLI 文案 / TUI 终端色 / GUI Qt 样式 / Web 看板变量）
都从同一组 token 渲染，从而消除「四个端四种紫」与「加主题要再抄 163 行 QSS」。

用法::

    from theme import load_theme, render_qss, render_css_vars, render_ansi
    th = load_theme()                     # 读工作区 ui_theme.json，缺失则用内置默认
    qss = render_qss(th)                  # GUI 用
    css = render_css_vars(th)             # Web 看板用
    ansi = render_ansi(th)                # TUI 用

包式导入同样可用：``from tools.theme import load_theme``。
"""

from __future__ import annotations

from .compile_ansi import (
    apply_to_colors_class,
    colors_disabled,
    render_ansi,
    supports_truecolor,
)
from .compile_qt import (
    TemplateError,
    placeholder_names,
    render_qss,
    write_preset_qss,
    write_qss,
)
from .compile_web import (
    CSS_PLACEHOLDER,
    render_css_vars,
    render_preset_gallery,
    theme_tokens_for_js,
    var_block,
)
from .contrast import (
    CONTRAST_LARGE,
    CONTRAST_TEXT,
    REQUIRED_PAIRS,
    contrast_ratio,
    darken,
    is_dark,
    lighten,
    mix,
    relative_luminance,
    validate_tokens,
)
from .tokens import (
    DEFAULT_PRESET,
    PRESETS,
    PRESET_ORDER,
    STRUCTURE_TOKENS,
    THEME_FILE,
    Theme,
    ThemeContrastError,
    build_theme,
    derive_tokens,
    list_presets,
    load_theme,
    validate_all_presets,
)
from .preset_rules import (
    RHYTHM_STAGES,
    preset_meta,
    preset_rules_css,
    rhythm_label,
    rhythm_preset,
)

__all__ = [
    # token 与主题
    "Theme",
    "PRESETS",
    "PRESET_ORDER",
    "DEFAULT_PRESET",
    "STRUCTURE_TOKENS",
    "THEME_FILE",
    "ThemeContrastError",
    "build_theme",
    "derive_tokens",
    "load_theme",
    "list_presets",
    "validate_all_presets",
    # 预设选择器与备考节律
    "RHYTHM_STAGES",
    "preset_rules_css",
    "preset_meta",
    "rhythm_preset",
    "rhythm_label",
    # 色彩与可读性
    "contrast_ratio",
    "relative_luminance",
    "validate_tokens",
    "REQUIRED_PAIRS",
    "CONTRAST_TEXT",
    "CONTRAST_LARGE",
    "mix",
    "lighten",
    "darken",
    "is_dark",
    # 各端编译器
    "render_qss",
    "write_qss",
    "write_preset_qss",
    "placeholder_names",
    "TemplateError",
    "render_css_vars",
    "render_preset_gallery",
    "theme_tokens_for_js",
    "CSS_PLACEHOLDER",
    "var_block",
    "render_ansi",
    "apply_to_colors_class",
    "supports_truecolor",
    "colors_disabled",
]
