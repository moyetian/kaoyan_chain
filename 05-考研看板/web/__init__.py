# -*- coding: utf-8 -*-
"""
Web 看板构建支撑模块

拆出来的原因（agent.md 的单文件红线与单一职责）：
``05-考研看板/build.py`` 改造前 2062 行，其中 HTML 模板本身占 1025 行、
`build()` 又达 185 行，属典型「上帝文件」—— 加一个看板区块就要在同一文件里
翻几百行。现在按职责分开放置：

    template.html    纯前端产物模板（含 {{占位符}}），可被编辑器正确高亮
    vendor.py        第三方前端库版本/地址单一真源 + 公式降级脚本 + --offline
    theme_vars.py    设计系统 token → CSS 变量注入

``build.py`` 只留「取数据 → 渲染 → 落盘」的编排。
"""

from __future__ import annotations

from .theme_vars import build_theme_css
from .vendor import (
    KATEX_VERSION,
    MARKED_VERSION,
    asset_map,
    download_vendor_assets,
    load_fallback_math_js,
)

__all__ = [
    "KATEX_VERSION",
    "MARKED_VERSION",
    "asset_map",
    "build_theme_css",
    "download_vendor_assets",
    "load_fallback_math_js",
]
