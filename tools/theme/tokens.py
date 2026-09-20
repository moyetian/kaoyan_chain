# -*- coding: utf-8 -*-
"""
主题 Token 与解析（设计系统单一真源）

[为什么需要这一层] 改造前四端各有一套配色与主题机制：
  * GUI      两份逐条手写的 QSS（dark.qss / light.qss，各 163 行）
  * Web 看板  `:root[data-t=dark]` + CSS 变量（写在 build.py 的 HTML 模板字符串里）
  * Web 伴侣  live.html 的 `body.dark` + 另一套变量
  * TUI      ANSI 转义硬编码在 Colors 类里
主色四个端各不相同（#6366f1 / #8b5cf6 / #4f46e5 / ANSI 96），加第三套主题
就要再抄 163 行样式表 —— 这是「无法扩展」的根因，而不是某处写错了。

本模块维护的是**一套语义 token + 多组取值**，各端由编译器渲染出自己那份产物：
  tokens.py  →  compile_qt.py  (QSS)  /  compile_web.py (CSS 变量)  /  compile_ansi.py

Token 命名一律**语义化**（surf = 表面，而不是 gray-800），这样换主题时不会出现
「深色主题里写死浅灰」。

用户可定制入口：工作区根目录的 ``ui_theme.json``（受 .gitignore 保护，不入库）：
  L1  ``{"preset": "light"}``                      —— 切预设
  L2  ``{"preset": "light", "overrides": {"acc": "#7c3aed", "radius": 12}}``
  L3  直接给完整 token 字典（``{"tokens": {...}}``）
未提供该文件时使用内置默认预设。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .contrast import (
    darken,
    is_dark,
    is_hex,
    mix,
    validate_tokens,
)

_LOG = logging.getLogger(__name__)

#: 用户可定制入口文件名（工作区根目录）
THEME_FILE = "ui_theme.json"

#: 默认预设
DEFAULT_PRESET = "dark"

#: 由核心色派生的 token（预设未显式给出时按规则推导，避免用户填二十个色值）
_DERIVED_KEYS = (
    "fg-strong", "line-strong", "input-bg", "acc-soft", "acc-line",
    "acc-grad-from", "acc-grad-to", "sel-bg", "sel-fg", "grid-line",
    "focus-ring", "on-acc",
)

#: 与主色绑定的派生 token。用户覆盖 `acc` 时必须一并重派生 ——
#: 预设里为「预设自己的主色」调好的焦点环/渐变，在新主色下就不协调了
#: （例如把主色换成青色、焦点环却还是紫色）。
_ACC_DEPENDENT = (
    "focus-ring", "acc-soft", "acc-line",
    "acc-hover", "acc-press", "acc-grad-from", "acc-grad-to",
)

#: 非颜色 token（结构/节奏），各预设共用，用户可覆盖
STRUCTURE_TOKENS: Dict[str, Any] = {
    "radius": 16,
    "radius-sm": 8,
    "density": 1.0,          # 1.0 = 舒适，<1 紧凑
    "font-scale": 1.0,       # 字号缩放
    "focus-w": 2,
    # 跨平台字体栈：改造前 GUI 硬编码 "Microsoft YaHei"，Linux/macOS 上会回退到
    # 一个不保证存在中文字形的字体。此处按「现代无衬线 → 各平台中文字体」排列。
    "font-family": ('"Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", '
                    '"Noto Sans CJK SC", "Source Han Sans SC", sans-serif'),
    "dur-fast": "180ms",
    "dur-base": "240ms",
    "dur-slow": "320ms",
    "ease-std": "cubic-bezier(.2,.8,.2,1)",
    "ease-emph": "cubic-bezier(.3,1.4,.5,1)",
}


def _base(
    *,
    display_name: str,
    mode: str,
    bg: str, surf: str, surf2: str, surf3: str,
    fg: str, mut: str, line: str,
    acc: str, acc_sub: str, on_acc: str,
    ok: str, warn: str, bad: str,
    **extra: Any,
) -> Dict[str, Any]:
    """构造一个预设（只给核心色，其余派生）。"""
    tokens: Dict[str, Any] = {
        "display_name": display_name,
        "mode": mode,
        "bg": bg, "surf": surf, "surf2": surf2, "surf3": surf3,
        "fg": fg, "mut": mut, "line": line,
        "acc": acc, "acc-sub": acc_sub, "on-acc": on_acc,
        "ok": ok, "warn": warn, "bad": bad,
    }
    tokens.update(extra)
    tokens.update(STRUCTURE_TOKENS)
    return tokens


# ════════════════════════════════════════════════════════════════
# 内置预设
# ════════════════════════════════════════════════════════════════
# 说明：dark / light 的取值直接采用 Web 看板（docs/index.html）里那套**已验证
# 可用**的 CSS 变量，把它升格为全端标准；GUI 的旧配色（#6366f1 系）随之统一，
# 以消除「四个端四种紫」。
#
# 三处刻意的无障碍修正（原设计不满足 WCAG，故在预设里改正）：
#   1. light 的主色由 #8b5cf6 微调为 #7c3aed —— 前者配白字仅 3.5:1（不达标），
#      加深后白字达标；色相几乎无感差异。
#   2. light 的 ok 由 #10b981 改为 #059669、warn 由 #f59e0b 改为 #b45309 ——
#      原值在白底上分别只有 2.54:1 / 2.15:1，远低于状态色 3:1 的门限，
#      而它们在看板上是「达标/预警」的文字与图标色。
#   3. dark 的 `on-acc` 由白改为深靛 #1e1b4b —— 白字压在浅紫主色 #a78bfa 上
#      只有 2.76:1；改为深色文字后达标。

PRESETS: Dict[str, Dict[str, Any]] = {
    "dark": _base(
        display_name="曜石黑",
        mode="dark",
        bg="#090d16", surf="#111827", surf2="#1e293b", surf3="#334155",
        fg="#f8fafc", mut="#94a3b8", line="#1e293b",
        acc="#a78bfa", acc_sub="#2e1065", on_acc="#1e1b4b",
        ok="#34d399", warn="#fbbf24", bad="#f87171",
        **{
            "focus-ring": "#c4b5fd",
            "acc-grad-from": "#c4b5fd",
            "acc-grad-to": "#a78bfa",
            "sh": "0 1px 3px rgba(0,0,0,.3)",
            "sh2": "0 8px 24px rgba(0,0,0,.4)",
        },
    ),
    "light": _base(
        display_name="晨曦白",
        mode="light",
        bg="#f8fafc", surf="#ffffff", surf2="#f1f5f9", surf3="#e2e8f0",
        fg="#0f172a", mut="#64748b", line="#e2e8f0",
        acc="#7c3aed", acc_sub="#ede9fe", on_acc="#ffffff",
        ok="#059669", warn="#b45309", bad="#ef4444",
        **{
            "focus-ring": "#6d28d9",
            "acc-grad-from": "#a78bfa",
            "acc-grad-to": "#7c3aed",
            "sh": "0 4px 12px rgba(139, 92, 246, 0.06),0 1px 3px rgba(0,0,0,.04)",
            "sh2": "0 8px 24px rgba(139, 92, 246, 0.12),0 2px 6px rgba(0,0,0,.03)",
        },
    ),
    "eye-green": _base(
        display_name="护眼绿",
        mode="light",
        bg="#eef4ea", surf="#f8fbf5", surf2="#e3ecdc", surf3="#d2e0c8",
        fg="#1b2a1f", mut="#4d6b52", line="#d5e3cb",
        acc="#2f7d4f", acc_sub="#dff0e3", on_acc="#ffffff",
        ok="#1f7a3d", warn="#a16207", bad="#b3261e",
        **{
            "focus-ring": "#1f5f3c",
            "acc-grad-from": "#4b9c6a",
            "acc-grad-to": "#2f7d4f",
            "sh": "0 4px 12px rgba(47, 125, 79, 0.08),0 1px 3px rgba(0,0,0,.04)",
            "sh2": "0 8px 24px rgba(47, 125, 79, 0.14),0 2px 6px rgba(0,0,0,.03)",
        },
    ),
    "pink": _base(
        display_name="樱粉",
        mode="light",
        bg="#fdf3f7", surf="#fffafc", surf2="#fbe9f1", surf3="#f6d6e4",
        fg="#3a1f2b", mut="#7d5468", line="#f3d9e5",
        acc="#be185d", acc_sub="#fce7f0", on_acc="#ffffff",
        ok="#15803d", warn="#b45309", bad="#be123c",
        **{
            "focus-ring": "#9d174d",
            "acc-grad-from": "#db2777",
            "acc-grad-to": "#be185d",
            "sh": "0 4px 12px rgba(190, 24, 93, 0.07),0 1px 3px rgba(0,0,0,.04)",
            "sh2": "0 8px 24px rgba(190, 24, 93, 0.13),0 2px 6px rgba(0,0,0,.03)",
        },
    ),
    "hc": _base(
        display_name="高对比（无障碍）",
        mode="dark",
        bg="#000000", surf="#000000", surf2="#141414", surf3="#262626",
        fg="#ffffff", mut="#e6e6e6", line="#ffffff",
        acc="#ffff00", acc_sub="#333300", on_acc="#000000",
        ok="#4ade80", warn="#fde047", bad="#ff6b6b",
        **{
            "focus-ring": "#00ffff",
            "acc-grad-from": "#ffff66",
            "acc-grad-to": "#ffff00",
            "sh": "none",
            "sh2": "none",
        },
    ),
}

#: 预设顺序（界面展示用）
PRESET_ORDER: Tuple[str, ...] = ("dark", "light", "eye-green", "pink", "hc")


class ThemeContrastError(ValueError):
    """主题可读性不达标。"""


def list_presets() -> List[Tuple[str, str, str]]:
    """返回 ``[(key, 显示名, 明暗), ...]``，供界面列出可选主题。"""
    out = []
    for key in PRESET_ORDER:
        preset = PRESETS[key]
        out.append((key, str(preset["display_name"]), str(preset["mode"])))
    return out


def derive_tokens(tokens: Mapping[str, Any]) -> Dict[str, Any]:
    """补齐派生 token（预设未显式给出时按规则推导）。

    派生而非全写死，是为了让「用户只改一个主色」也能得到协调的整套配色
    （悬停色、按下色、柔化强调色、输入框底色都随之变化）。
    """
    t: Dict[str, Any] = dict(tokens)
    dark_mode = str(t.get("mode", "dark")) == "dark" or (
        is_hex(t.get("bg")) and is_dark(str(t["bg"])))

    if "fg" in t and "fg-strong" not in t:
        t["fg-strong"] = t["fg"]
    if "line" in t and "fg" in t and "line-strong" not in t:
        t["line-strong"] = mix(str(t["line"]), str(t["fg"]), 0.12)
    if "bg" in t and "input-bg" not in t:
        t["input-bg"] = (darken(str(t["bg"]), 0.15) if dark_mode else str(t.get("surf", t["bg"])))
    if "acc" in t:
        acc = str(t["acc"])
        t.setdefault("acc-soft", acc)
        t.setdefault("acc-line", mix(acc, str(t.get("surf", "#ffffff")), 0.55))
        t.setdefault("acc-grad-from", acc)
        t.setdefault("acc-grad-to", acc)
        t.setdefault("focus-ring", acc)
    if "acc-sub" in t:
        t.setdefault("sel-bg", t["acc-sub"])
    if "on-acc" in t:
        t.setdefault("sel-fg", t["on-acc"])
    t.setdefault("grid-line", t.get("line", "#888888"))

    # ── 交互态派生 ─────────────────────────────────────────────
    # 悬停/按下都朝「前景色」方向偏移：浅色主题下前景为深色 → 主色变深；
    # 深色主题下前景为浅色 → 主色变亮。这样用户只改主色也能得到协调的交互态。
    if "acc" in t and "fg" in t:
        acc, fg = str(t["acc"]), str(t["fg"])
        t.setdefault("acc-hover", mix(acc, fg, 0.18))
        t.setdefault("acc-press", mix(acc, fg, 0.32))

    # ── 结构数值 → 可注入模板的字面量 ──────────────────────────
    # 让 density / font-scale / radius 这三个「L2 可调项」真正生效，
    # 而不是散落在样式表里各写一遍。
    scale = _as_float(t.get("font-scale"), 1.0)
    density = _as_float(t.get("density"), 1.0)
    radius = _as_float(t.get("radius"), 16)
    radius_sm = _as_float(t.get("radius-sm"), 8)
    t.setdefault("fs-xl", f"{round(18 * scale, 1):g}px")
    t.setdefault("fs-lg", f"{round(16 * scale, 1):g}px")
    t.setdefault("fs-base", f"{round(13 * scale, 1):g}px")
    t.setdefault("fs-sm", f"{round(12 * scale, 1):g}px")
    t.setdefault("pad", f"{max(4, round(9 * density)):g}px")
    t.setdefault("pad-sm", f"{max(2, round(5 * density)):g}px")
    t.setdefault("pad-lg", f"{max(6, round(16 * density)):g}px")
    t.setdefault("radius-px", f"{radius:g}px")
    t.setdefault("radius-sm-px", f"{radius_sm:g}px")
    return t


def _as_float(value: Any, default: float) -> float:
    try:
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d.-]", "", value.strip())
            out = float(cleaned) if cleaned else default
        else:
            out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out > 0 else default


@dataclass(frozen=True)
class Theme:
    """解析完成、已通过可读性校验的主题。"""

    name: str
    display_name: str
    mode: str
    tokens: Mapping[str, Any] = field(default_factory=dict)
    source: str = "builtin"

    # ── 取值 ────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        return self.tokens.get(key, default)

    def color(self, key: str, default: str = "#000000") -> str:
        value = self.tokens.get(key)
        return str(value) if is_hex(value) else default

    def number(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.tokens.get(key, default))
        except (TypeError, ValueError):
            return default

    def to_flat(self) -> Dict[str, str]:
        """全部 token 转为字符串（供模板 ``{{key}}`` 替换）。"""
        flat: Dict[str, str] = {}
        for key, value in self.tokens.items():
            if key in ("display_name", "mode"):
                continue
            flat[str(key)] = str(value)
        return flat

    def preset_label(self) -> str:
        return f"{self.display_name} ({self.name})"


def build_theme(preset: str, overrides: Optional[Mapping[str, Any]] = None,
                source: str = "builtin") -> Theme:
    """由预设名 + 覆盖项构造主题（不做校验）。

    覆盖主色时，预设里为「原主色」调好的派生色（焦点环、渐变、柔化强调色等）
    会被丢弃并重派生；若用户也显式给了这些派生 token，则以用户值为准。
    """
    key = preset if preset in PRESETS else DEFAULT_PRESET
    tokens: Dict[str, Any] = dict(PRESETS[key])
    tokens["preset"] = key
    if overrides:
        ov = {str(k): v for k, v in overrides.items()}
        if "acc" in ov:
            for dep in _ACC_DEPENDENT:
                if dep not in ov:
                    tokens.pop(dep, None)
        for k, v in ov.items():
            if k in ("display_name", "mode", "preset"):
                continue
            tokens[k] = v
    tokens = derive_tokens(tokens)
    return Theme(
        name=str(tokens.get("preset", key)),
        display_name=str(tokens.get("display_name", PRESETS[key]["display_name"])),
        mode=str(tokens.get("mode", PRESETS[key]["mode"])),
        tokens=tokens,
        source=source,
    )


def load_theme(workspace_root: Optional[Path] = None,
               require_contrast: bool = True) -> Theme:
    """加载主题：读 ``ui_theme.json``，校验可读性，不达标回退默认预设。

    回退而不是报错中断 —— 主题是外观层，不该让整个程序起不来；
    但必须留痕并且明确告知用户"你的自定义被拒了"。
    """
    root = Path(workspace_root) if workspace_root else _default_root()
    cfg_path = root / THEME_FILE
    preset = DEFAULT_PRESET
    overrides: Dict[str, Any] = {}
    source = "builtin"

    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                source = str(cfg_path.name)
                preset = str(raw.get("preset") or raw.get("name") or DEFAULT_PRESET)
                if isinstance(raw.get("tokens"), dict):
                    # L3：完整 token 覆盖
                    overrides.update(raw["tokens"])
                if isinstance(raw.get("overrides"), dict):
                    overrides.update(raw["overrides"])
        except Exception as exc:
            _LOG.warning("ui_theme.json 解析失败，改用内置默认主题: %s -> %s", cfg_path, exc)

    theme = build_theme(preset, overrides, source=source)
    if not require_contrast:
        return theme

    problems = validate_tokens(dict(theme.tokens))
    if problems:
        for p in problems:
            _LOG.warning("主题可读性校验未通过: %s", p)
        fallback = build_theme(DEFAULT_PRESET, None, source="builtin-fallback")
        fb_problems = validate_tokens(dict(fallback.tokens))
        if fb_problems:  # pragma: no cover - 内置预设自检失败说明预设被改坏
            raise ThemeContrastError(
                "内置默认主题未通过对比度校验: " + "; ".join(fb_problems))
        _LOG.warning("已回退到内置默认主题 %s（你的自定义配色可读性不达标）", DEFAULT_PRESET)
        return fallback
    return theme


def _default_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def validate_all_presets() -> Dict[str, List[str]]:
    """自检：逐个校验内置预设，返回 ``{预设名: 违规列表}``（供测试与 doctor 用）。"""
    report: Dict[str, List[str]] = {}
    for key in PRESET_ORDER:
        report[key] = validate_tokens(build_theme(key).tokens)
    return report


__all__ = [
    "PRESETS",
    "PRESET_ORDER",
    "DEFAULT_PRESET",
    "STRUCTURE_TOKENS",
    "THEME_FILE",
    "Theme",
    "ThemeContrastError",
    "build_theme",
    "derive_tokens",
    "list_presets",
    "load_theme",
    "validate_all_presets",
]
