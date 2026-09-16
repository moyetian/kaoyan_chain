# -*- coding: utf-8 -*-
"""GUI 内联 SVG 图标库

[为什么不用 emoji] emoji 跨系统/跨字体字形差异极大（📋 在 Noto Color Emoji
与 Segoe UI Emoji 下宽高不一、基线不齐、有的彩色有的单色），功能卡片的
图标是界面最显眼的视觉锚点。改用内联 SVG 可以：

  1. 保证所有平台渲染一致（QSvgRenderer 矢量渲染，与字体无关）。
  2. 颜色跟随主题 token（accent / fg），切主题时重新渲染。

图标为 24×24 viewBox 的描边风格（Lucide/Feather 风），``stroke`` 占位符
``{COLOR}`` 在渲染时替换为主题色，``fill="none"`` 保证透明背景。

用法::

    from gui.widgets.icons import render_icon, SVG_TEMPLATES, ICON_KEYS
    pm = render_icon("today", size=20, color="#a78bfa")
    label.setPixmap(pm)
"""

from __future__ import annotations

from typing import Dict, Tuple

# QSvgRenderer 是 QtSvg 模块，PySide6 标准分发的可选组件。
# 极简 headless CI 或无显示器环境可能缺 QtSvg → 回退空 pixmap。
try:  # pragma: no cover - 取决于安装
    from PySide6.QtCore import QByteArray, QSize, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    _HAS_SVG = True
except Exception:  # pragma: no cover
    _HAS_SVG = False

#: 描边公共属性（放在 <svg> 根上，子元素继承）
_STROKE = 'fill="none" stroke="{COLOR}" stroke-width="2" ' \
          'stroke-linecap="round" stroke-linejoin="round"'

#: SVG 图标模板注册表（key → 含 {COLOR} 占位符的 SVG 字符串）
SVG_TEMPLATES: Dict[str, str] = {
    # ── 功能卡片 10 张 ──────────────────────────────────────────
    "today": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
             '<rect x="8" y="2" width="8" height="4" rx="1"/>'
             '<path d="M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2"/>'
             '<path d="M9 12h6"/><path d="M9 16h6"/></svg>',
    "compose": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
               '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/>'
               '<circle cx="12" cy="12" r="1.5" fill="{COLOR}" stroke="none"/></svg>',
    "variant": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
               '<path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>'
               '<path d="M3 21v-5h5"/>'
               '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>'
               '<path d="M21 3v5h-5"/></svg>',
    "diff": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
            '<path d="M3 17l6-6 4 4 8-8"/>'
            '<path d="M15 7h6v6"/></svg>',
    "ingest": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
              '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>',
    "scout": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
             '<circle cx="11" cy="11" r="7"/><path d="M16 16l5 5"/></svg>',
    "compare": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
               '<path d="M12 3v18"/><path d="M5 21h14"/><path d="M5 8h14"/>'
               '<path d="M5 8l-3 5h6z"/><path d="M19 8l-3 5h6z"/></svg>',
    "watch": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
             '<circle cx="12" cy="12" r="1.5" fill="{COLOR}" stroke="none"/>'
             '<path d="M16.2 7.8a6 6 0 0 1 0 8.4"/>'
             '<path d="M7.8 16.2a6 6 0 0 1 0-8.4"/>'
             '<path d="M19 5a10 10 0 0 1 0 14"/>'
             '<path d="M5 19A10 10 0 0 1 5 5"/></svg>',
    "build": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
             '<path d="M3 21h18"/><path d="M7 21V13"/><path d="M12 21V8"/>'
             '<path d="M17 21V4"/></svg>',
    "wechat_search": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
                     '<rect x="7" y="2" width="10" height="20" rx="2"/>'
                     '<path d="M11 18h2"/></svg>',
    # ── 页签 / 头栏 / 按钮 ─────────────────────────────────────
    "chat": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
            '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H8l-5 4V11.5a8.5 8.5 0 0 1 8.5-8.5h1'
            'A8.5 8.5 0 0 1 21 11.5z"/></svg>',
    "book": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
            '<path d="M12 7v13"/><path d="M7 7H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h3"/>'
            '<path d="M17 7h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-3"/>'
            '<path d="M7 7c0-2 2-4 5-4s5 2 5 4v0"/></svg>',
    "landmark": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
                '<path d="M3 22h18"/><path d="M5 22V10"/><path d="M9 22V10"/>'
                '<path d="M15 22V10"/><path d="M19 22V10"/>'
                '<path d="M3 10h18"/><path d="M12 2L3 6h18z"/></svg>',
    "send": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
            '<path d="M22 2L11 13"/><path d="M22 2L15 22L11 13L2 9z"/></svg>',
    "library": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
               '<path d="M5 4v16"/><path d="M9 4v16"/><path d="M15 4l4 1-3 15-4-1z"/></svg>',
    "theme": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
             '<circle cx="12" cy="12" r="4" fill="{COLOR}" stroke="none"/>'
             '<path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2'
             'M19 5l-2 2M7 17l-2 2"/></svg>',
    "refresh": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
               '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/>'
               '<path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>',
    "quiz": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
            '<path d="M9 11l2 2 4-4"/><path d="M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5'
            'a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/></svg>',
    "hourglass": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
                 '<path d="M6 2h12M6 22h12"/><path d="M6 2v6l6 4-6 4v6"/>'
                 '<path d="M18 2v6l-6 4 6 4v6"/></svg>',
    "shield": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + _STROKE + '>'
              '<path d="M12 2L4 5v7c0 5 3.5 8 8 10 4.5-2 8-5 8-10V5z"/></svg>',
}

#: 全部图标 key（供自检与遍历）
ICON_KEYS: Tuple[str, ...] = tuple(SVG_TEMPLATES.keys())

#: 回退色（theme 色不可用时）
_FALLBACK_COLOR = "#a78bfa"


def _format_svg(key: str, color: str) -> str:
    """把模板的 ``{COLOR}`` 占位符替换为实际色值。"""
    tmpl = SVG_TEMPLATES.get(key)
    if tmpl is None:
        return ""
    return tmpl.replace("{COLOR}", color)


def render_icon(key: str, size: int = 20, color: str = "") -> "QPixmap":
    """把 SVG 图标渲染为指定尺寸的透明背景 QPixmap。

    无 QtSvg 或 key 不存在时返回空 pixmap（调用方应能优雅降级）。
    """
    if not _HAS_SVG:
        return QPixmap() if _HAS_SVG else None  # type: ignore[return-value]
    from PySide6.QtGui import QPixmap  # noqa: PLC0415
    svg = _format_svg(key, color or _FALLBACK_COLOR)
    if not svg:
        return QPixmap()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    pm = QPixmap(QSize(size, size))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pm


def icon_label(key: str, size: int = 20, color: str = "") -> "QLabel":
    """创建一个带 SVG 图标的 QLabel（便捷构造器）。"""
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415
    lbl = QLabel()
    lbl.setObjectName("SvgIcon")
    pm = render_icon(key, size, color)
    if not pm.isNull():
        lbl.setPixmap(pm)
    return lbl


__all__ = ["SVG_TEMPLATES", "ICON_KEYS", "render_icon", "icon_label"]
