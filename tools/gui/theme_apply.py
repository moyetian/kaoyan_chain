# -*- coding: utf-8 -*-
"""
GUI 主题应用与偏好持久化

[缺陷修复·三处]
1. **浅色主题实际不可用** —— 主题 QSS 由 tools/theme 从 token 现场编译，
   不再依赖两份逐条手写的 dark.qss / light.qss；所有控件改用 objectName
   选择器，不存在「widget 内联样式压过全局样式表」的问题。
2. **主题不持久化** —— 改造前 `_current_theme` 每次启动写死 "dark"，
   用户切到浅色、重启又变回深色。现用 QSettings 记住预设、圆角、密度、字号
   与窗口几何。
3. **四端配色不一** —— 主题来自 tools/theme 的同一组 token，
   GUI 与看板/终端不再各有一套紫。

QSettings 落点（Windows 为注册表 HKCU\\Software\\KaoyanStudyChain\\ky-gui）：
    ui/preset  ui/radius  ui/density  ui/font_scale
    win/geometry  win/state  ui/last_tab
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

# 双导入路径兼容（项目同时存在 tools.X 与 X 两种运行方式）
try:  # pragma: no cover - 取决于运行方式
    from theme import PRESET_ORDER, Theme, build_theme, load_theme, render_qss
except ImportError:  # pragma: no cover
    from tools.theme import (  # type: ignore
        PRESET_ORDER, Theme, build_theme, load_theme, render_qss,
    )

_LOG = logging.getLogger(__name__)

SETTINGS_ORG = "KaoyanStudyChain"
SETTINGS_APP = "ky-gui"

#: 用户偏好键
KEY_PRESET = "ui/preset"
KEY_ACCENT = "ui/acc"
KEY_RADIUS = "ui/radius"
KEY_DENSITY = "ui/density"
KEY_FONT_SCALE = "ui/font_scale"
KEY_GEOMETRY = "win/geometry"
KEY_WINDOW_STATE = "win/state"
KEY_LAST_TAB = "ui/last_tab"


# ── QSettings（延迟导入，便于无 Qt 环境下单测数据层） ─────────────

def _settings():
    from PySide6.QtCore import QSettings

    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def read_prefs() -> Dict[str, Any]:
    """读取本机偏好（缺失项不返回）。"""
    try:
        s = _settings()
    except Exception:                      # pragma: no cover - 无 Qt 环境
        return {}
    prefs: Dict[str, Any] = {}
    for key, name in ((KEY_PRESET, "preset"), (KEY_ACCENT, "acc"),
                      (KEY_RADIUS, "radius"), (KEY_DENSITY, "density"),
                      (KEY_FONT_SCALE, "font_scale")):
        raw = s.value(key)
        if raw not in (None, ""):
            prefs[name] = raw
    return prefs


def write_pref(key: str, value: Any) -> None:
    try:
        _settings().setValue(key, value)
    except Exception as exc:               # pragma: no cover
        _LOG.debug("偏好写入失败（忽略）: %s -> %s", key, exc)


def write_geometry(window) -> None:
    """保存窗口几何与状态（关闭时调用）。"""
    try:
        s = _settings()
        s.setValue(KEY_GEOMETRY, window.saveGeometry())
        s.setValue(KEY_WINDOW_STATE, window.saveState())
    except Exception as exc:               # pragma: no cover
        _LOG.debug("窗口几何保存失败（忽略）: %s", exc)


def restore_geometry(window) -> bool:
    """恢复窗口几何；返回是否成功恢复过。"""
    try:
        s = _settings()
        geo = s.value(KEY_GEOMETRY)
        if geo is not None:
            window.restoreGeometry(geo)
        state = s.value(KEY_WINDOW_STATE)
        if state is not None and hasattr(window, "restoreState"):
            window.restoreState(state)
        return geo is not None
    except Exception:                      # pragma: no cover
        return False


# ── 主题解析与应用 ──────────────────────────────────────────────

def resolve_theme(workspace_root: Optional[Path] = None,
                  preset: Optional[str] = None) -> Theme:
    """确定当前主题。

    优先级：调用方显式指定的 preset → 工作区 ``ui_theme.json``（用户手改的文件，
    最直观）→ QSettings 里记住的上次选择 → 内置默认。
    无论走哪条路，都在 tokens 层做可读性校验，不达标自动回退。
    """
    if preset:
        return build_theme(preset, read_overrides())
    theme = load_theme(workspace_root)
    if theme.source == "builtin":
        remembered = read_prefs().get("preset")
        if remembered in PRESET_ORDER:
            return build_theme(str(remembered), read_overrides())
    return theme


def read_overrides() -> Dict[str, Any]:
    """QSettings 里的 L2 覆盖项（主色/圆角/密度/字号）。"""
    prefs = read_prefs()
    overrides: Dict[str, Any] = {}
    if "acc" in prefs:
        overrides["acc"] = prefs["acc"]
    if "radius" in prefs:
        overrides["radius"] = prefs["radius"]
    if "density" in prefs:
        overrides["density"] = prefs["density"]
    if "font_scale" in prefs:
        overrides["font-scale"] = prefs["font_scale"]
    return overrides


def apply_theme(app, theme: Theme) -> str:
    """把主题编译成 QSS 并应用到 QApplication，返回渲染出的 QSS。"""
    qss = render_qss(theme)
    app.setStyleSheet(qss)
    _LOG.debug("已应用主题 %s（%s）", theme.name, theme.display_name)
    return qss


def set_preset(app, preset: str, workspace_root: Optional[Path] = None) -> Theme:
    """切换并持久化主题预设。"""
    theme = resolve_theme(workspace_root, preset=preset)
    apply_theme(app, theme)
    write_pref(KEY_PRESET, theme.name)
    return theme


def apply_prefs(app, workspace_root: Optional[Path] = None) -> Theme:
    """从 QSettings 读取全部 L2 旋钮（预设 + 主色 + 圆角 + 密度 + 字号），
    重新解析并应用主题。

    设置面板的「应用」按钮调此函数即可即时预览，无需重启。"""
    theme = resolve_theme(workspace_root)
    apply_theme(app, theme)
    return theme


def next_preset(current: str) -> str:
    """在明暗两套主预设间切换（GUI 顶栏按钮的语义：深色 ↔ 浅色）。"""
    if current == "light":
        return "dark"
    if current == "dark":
        return "light"
    # 非明暗二态预设（如护眼绿/樱粉）→ 切到其相反明暗的内置预设
    return "light" if build_theme(current).mode == "dark" else "dark"


__all__ = [
    "KEY_ACCENT",
    "KEY_DENSITY",
    "KEY_FONT_SCALE",
    "KEY_GEOMETRY",
    "KEY_LAST_TAB",
    "KEY_PRESET",
    "KEY_RADIUS",
    "KEY_WINDOW_STATE",
    "SETTINGS_APP",
    "SETTINGS_ORG",
    "apply_prefs",
    "apply_theme",
    "next_preset",
    "read_overrides",
    "read_prefs",
    "resolve_theme",
    "restore_geometry",
    "set_preset",
    "write_geometry",
    "write_pref",
]
