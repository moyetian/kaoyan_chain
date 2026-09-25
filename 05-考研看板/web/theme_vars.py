# -*- coding: utf-8 -*-
"""
Web 看板主题变量注入（把设计系统接到 Web 端）

[缺陷修复·四个端四种紫] 改造前 Web 看板的配色**硬编码在 build.py 的 HTML 模板
字符串里**（`:root{}` / `@media(prefers-color-scheme:dark)` / `[data-t=dark]` /
`[data-t=light]` 四段），而 GUI 又是另一套 indigo、TUI 一套 ANSI 色号。
现在四端共用 tools/theme 的同一组语义 token：本模块把 token 渲染成 CSS 变量块，
由构建脚本注入模板的 ``{{THEME_CSS}}`` 占位符。

用户在工作区根目录的 ``ui_theme.json`` 里改一个主色，
Web / GUI / 终端三端会一起变 —— 这是「可自定义风格」能真正落地的前提。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)

#: 仓库根（05-考研看板/web/theme_vars.py → 仓库根）
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:              # 便于以脚本方式运行本文件
    sys.path.insert(0, str(ROOT))


#: 设计系统 token 的字体栈首项是 "Inter"，但仓库既不随包分发 Inter 字体文件、
#: 也不允许引 CDN（离线自包含）。浏览器会为首屏白等一次必然失败的字形查找，
#: 且该 token 在 Web 端此前**没有任何消费点**（body 用的是另一套硬编码系统栈）。
#: 这里在 Web 侧如实摘掉这个取不到的字体，保持纯系统栈；token 层保持不动。
_MISSING_FONT = '"Inter", '


def build_theme_css(workspace_root: Path | None = None) -> str:
    """生成注入 ``{{THEME_CSS}}`` 的 CSS 变量块。

    取不到主题时回落到一段最小可用变量，保证看板仍能正常显示
    （样式降级不该让整个页面崩掉）。
    """
    try:
        try:
            from theme import (build_theme, preset_meta, preset_rules_css,
                               render_css_vars, rhythm_label, rhythm_preset)
        except ImportError:                # pragma: no cover
            from tools.theme import (  # type: ignore
                build_theme, preset_meta, preset_rules_css, render_css_vars,
                rhythm_label, rhythm_preset)

        css = render_css_vars(build_theme("light"), build_theme("dark"))
        rules = preset_rules_css()
        # [缺陷修复·Inter 残留] 剥离必须发生在**拼接之后**：preset_rules_css()
        # 为 eye-green / pink / hc 三套预设各自重声明一次 --font-family，
        # 原先先剥离再拼接，这三块里的 "Inter" 就漏网了（实测产物 7 处
        # --font-family 中有 3 处仍指向仓库不存在的字体文件）。
        css = (css + (("\n" + rules) if rules else "")).replace(_MISSING_FONT, "")
        return css
    except Exception as exc:
        _LOG.warning("主题变量生成失败，看板回落到内置兜底配色: %s", exc)
        return _FALLBACK_CSS


#: 主题模块不可用时的兜底变量（深色 + 浅色两态，仅保证可读）
_FALLBACK_CSS = """/* 兜底配色：tools/theme 不可用 */
:root{--bg:#f8fafc;--surf:#ffffff;--surf2:#f1f5f9;--surf3:#e2e8f0;--fg:#0f172a;
--mut:#64748b;--line:#e2e8f0;--acc:#7c3aed;--acc-sub:#ede9fe;--ok:#059669;
--warn:#b45309;--bad:#ef4444;--radius:16px;--dur-fast:180ms;--dur-base:240ms;
--dur-slow:320ms;--ease-std:cubic-bezier(.2,.8,.2,1)}
:root[data-t=dark]{--bg:#090d16;--surf:#111827;--surf2:#1e293b;--surf3:#334155;
--fg:#f8fafc;--mut:#94a3b8;--line:#1e293b;--acc:#a78bfa;--acc-sub:#2e1065;
--ok:#34d399;--warn:#fbbf24;--bad:#f87171}"""


def theme_presets_json() -> str:
    """预设清单 JSON（键/显示名/明暗），注入 ``{{THEME_PRESETS}}``。

    前端选择器与 ``isDark()`` 都读它：明暗判断若继续写死 ``t==='dark'``，
    新增的护眼绿/樱粉会被误判成深色，图表与主题色会一起错位。
    """
    try:
        try:
            from theme import preset_meta
        except ImportError:                # pragma: no cover
            from tools.theme import preset_meta  # type: ignore

        raw = json.dumps(preset_meta(), ensure_ascii=False, separators=(",", ":"))
        return raw.replace("</", "<\\/")
    except Exception as exc:               # pragma: no cover
        _LOG.warning("预设清单生成失败，看板回落到深色/浅色两套: %s", exc)
        return '[{"k":"dark","n":"深色","m":"dark"},{"k":"light","n":"浅色","m":"light"}]'


def theme_rhythm_json(days_left: int) -> str:
    try:
        try:
            from theme import rhythm_label, rhythm_preset
        except ImportError:                # pragma: no cover
            from tools.theme import rhythm_label, rhythm_preset  # type: ignore

        payload = {"p": rhythm_preset(days_left),
                   "label": rhythm_label(days_left),
                   "days": max(0, int(days_left))}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:               # pragma: no cover
        _LOG.warning("节律主题生成失败，回落到深色: %s", exc)
        return '{"p":"dark","label":"","days":0}'


def theme_preset_gallery() -> str:
    """主题预设一览（供看板的主题选择器预览；取不到时返回空串）。"""
    try:
        try:
            from theme import render_preset_gallery
        except ImportError:                # pragma: no cover
            from tools.theme import render_preset_gallery  # type: ignore

        return render_preset_gallery()
    except Exception as exc:               # pragma: no cover
        _LOG.debug("预设一览生成失败（忽略）: %s", exc)
        return ""


if __name__ == "__main__":                 # pragma: no cover - 手工排查用
    print(build_theme_css()[:400])
