# -*- coding: utf-8 -*-
"""
主题预设选择器与「备考节律」主题。

两件事放在同一个模块，因为它们回答的是同一个问题的两面：

* **预设选择器** —— 内置 5 套主题（曜石黑 / 晨曦白 / 护眼绿 / 樱粉 / 高对比）
  此前只有 dark / light 两套真正接进了看板开关，另外三套虽然存在于 token 层，
  页面上却无从选择。这里把它们编译成 ``:root[data-t='<name>']`` 规则，
  与看板既有的 ``[data-t=dark]`` 机制完全共用一套开关，不引入新的 class 体系。
* **备考节律** —— 距初试的天数不同，用眼强度与情绪压力也不同：
  基础期长夜刷题用深色、强化期长时间用眼切护眼绿、冲刺期提神转亮色、
  临考月用暖色减压、决战周疲劳与焦虑最高则让位给可读性最强的高对比主题。
  这是**默认建议**而非强制：用户一旦手动选过主题，选择即写入 localStorage，
  节律不再覆盖。

节律只回答「推荐哪一套」，不改动任何 token —— 配色仍然只有 tools/theme 一个真源。
"""

from __future__ import annotations

from typing import List, Mapping, Tuple

from .compile_web import var_block
from .tokens import PRESETS, PRESET_ORDER, build_theme

#: 已在 ``render_css_vars()`` 里作为基础块输出的预设（重复输出只会让 CSS 变胖）
_BASE_COVERED: Tuple[str, ...] = ("dark", "light")

#: 备考节律：``(天数下界（不含）, 预设, 阶段名)``，由远及近排列。
#: 第一个满足 ``days_left > 下界`` 的档位生效；都不满足时取最后一档。
RHYTHM_STAGES: Tuple[Tuple[int, str, str], ...] = (
    (180, "dark", "基础期"),
    (90, "eye-green", "强化期"),
    (30, "light", "冲刺期"),
    (7, "pink", "临考月"),
    (0, "hc", "决战周"),
)


def preset_rules_css() -> str:
    """生成各预设的 CSS 规则；无预设可输出时返回空串（调用方按空串处理）。"""
    blocks: List[str] = []
    for name in PRESET_ORDER:
        if name in _BASE_COVERED or name not in PRESETS:
            continue
        theme = build_theme(name)
        body = var_block(theme)
        if not body:
            continue
        blocks.append(f":root[data-t='{name}']{{\n{body}\n}}")
    if not blocks:
        return ""

    return (
        "/* ── 主题预设（由 tools/theme 编译注入，勿手工编辑） ───────────────── */\n"
        + "\n".join(blocks)
    )


def _rhythm_stage(days_left: int) -> Tuple[str, str]:
    days = max(0, int(days_left))
    for floor, preset, label in RHYTHM_STAGES:
        if days > floor:
            return preset, label
    return RHYTHM_STAGES[-1][1], RHYTHM_STAGES[-1][2]


def rhythm_preset(days_left: int) -> str:
    """按距初试天数推荐预设名（不存在的预设名一律回落到内置默认）。"""
    preset, _label = _rhythm_stage(days_left)
    return preset if preset in PRESETS else "dark"


def rhythm_label(days_left: int) -> str:
    """当前节律阶段名（用于界面提示，如「距初试 12 天 · 临考月」）。"""
    return _rhythm_stage(days_left)[1]


def preset_meta() -> List[Mapping[str, str]]:
    """预设清单（键 / 显示名 / 明暗），供前端选择器与 ``isDark()`` 使用。

    用 JSON 安全的结构返回，避免前端再解析一遍 CSS；中文按 Unicode 原样输出，
    由调用方决定如何转义。
    """
    out: List[Mapping[str, str]] = []
    for name in PRESET_ORDER:
        if name not in PRESETS:
            continue
        theme = build_theme(name)
        out.append({"k": name, "n": str(theme.display_name), "m": str(theme.mode)})
    return out


__all__ = ["RHYTHM_STAGES", "preset_meta",
           "preset_rules_css", "rhythm_label", "rhythm_preset"]
