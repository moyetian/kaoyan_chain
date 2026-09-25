# -*- coding: utf-8 -*-
"""
设计系统 P0 回归测试：token 五类扩展（type-scale / space / elevation /
chart-palette / icon）与 Lucide 图标子集的一致性守卫。

覆盖目标：
  ① 新 token 的取值与派生规则（含 font-scale 联动、density 不联动）；
  ② 学科色板对比度门禁（含阴性：低对比必须被拒）；
  ③ 图标清单与 sprite 不漂移（含阴性：清单写错必须响亮失败）；
  ④ 三端编译器仍消费新 token（Web 放行 fs-*、pad 仍过滤）；
  ⑤ DESIGN.md 与实现不脱节（文档必须提到关键 token）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.theme import (  # noqa: E402
    ICONS,
    ICON_USAGE,
    build_theme,
    extract_sprite,
    load_sprite,
    missing_icons,
    render_css_vars,
    render_qss,
    validate_all_presets,
    validate_tokens,
)
from tools.theme.tokens import PRESET_ORDER  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 第 1 层：图标系统（清单 ↔ sprite 一致性）
# ════════════════════════════════════════════════════════════════

def test_icons_manifest_and_sprite_in_sync():
    """sprite 必须覆盖 ICONS 清单全部语义名（防「加了清单忘抽取」静默缺失）。"""
    sprite = load_sprite(ROOT)
    assert missing_icons(sprite) == [], f"sprite 缺失: {missing_icons(sprite)}"


def test_sprite_symbols_are_themed_and_prefixed():
    """每个 symbol：id 以 i- 前缀、保留 data-lucide 追溯名、描边用 currentColor。"""
    sprite = load_sprite(ROOT)
    assert sprite.count("<symbol ") == len(ICONS)
    for semantic, lucide in ICONS.items():
        assert f'id="i-{semantic}"' in sprite
        assert f'data-lucide="{lucide}"' in sprite
    assert 'stroke="currentColor"' in sprite, "图标必须随文字色（currentColor）"
    assert 'viewBox="0 0 24 24"' in sprite


def test_icon_usage_covers_manifest():
    """用途说明表必须与清单键一致（防止加图标不写用途）。"""
    assert set(ICON_USAGE) == set(ICONS), (
        f"仅在清单: {set(ICONS) - set(ICON_USAGE)}；仅在说明: {set(ICON_USAGE) - set(ICONS)}")


def test_extract_sprite_fails_loudly_on_unknown_icon():
    """阴性：清单里出现源 sprite 没有的图标名 → KeyError（不是静默少一个图标）。"""
    fake_source = '<svg><symbol id="calendar" viewBox="0 0 24 24"><path d="M0 0"/></symbol></svg>'
    # 源里只有 calendar；清单里还有 brain 等 41 个 → 必然失败
    with pytest.raises(KeyError, match="brain"):
        extract_sprite(fake_source)


def test_extract_sprite_roundtrip_on_full_manifest():
    """正路：源含全部所需图标时，抽取结果与清单一致且可被 missing_icons 认账。"""
    source = "<svg>" + "".join(
        f'<symbol id="{lucide}" viewBox="0 0 24 24"><path d="M1 1"/></symbol>'
        for lucide in ICONS.values()) + "</svg>"
    out = extract_sprite(source)
    assert missing_icons(out) == []
    assert out.count("<symbol ") == len(ICONS)


# ════════════════════════════════════════════════════════════════
# 第 2 层：token 五类扩展（取值 + 派生规则）
# ════════════════════════════════════════════════════════════════

def test_type_scale_tokens_exist():
    """字阶四档：hero 40 / title 20 / body 14 / caption 12。"""
    t = build_theme("dark")
    assert t.get("fs-hero") == "40px"
    assert t.get("fs-title") == "20px"
    assert t.get("fs-body") == "14px"
    assert t.get("fs-caption") == "12px"


def test_type_scale_follows_font_scale():
    """font-scale 联动：缩放 2 倍后 hero 应为 80px。"""
    t = build_theme("dark", {"font-scale": 2.0})
    assert t.get("fs-hero") == "80px"
    assert t.get("fs-title") == "40px"


def test_space_grid_is_fixed_and_density_independent():
    """间距网格固定 4/8/12/16/24/32，不随 density 变化。"""
    normal = build_theme("dark")
    dense = build_theme("dark", {"density": 0.5})
    for i, expected in enumerate(("4px", "8px", "12px", "16px", "24px", "32px"), 1):
        assert normal.get(f"space-{i}") == expected
        assert dense.get(f"space-{i}") == expected, "space 网格不应随 density 缩放"


def test_elevation_three_levels_in_all_presets():
    """elev-1/2/3 五套预设全部就位；sh/sh2 是 elev-1/2 的兼容别名。"""
    for name in PRESET_ORDER:
        t = build_theme(name)
        for key in ("elev-1", "elev-2", "elev-3"):
            assert t.get(key) is not None, f"{name} 缺 {key}"
        assert t.get("sh") == t.get("elev-1")
        assert t.get("sh2") == t.get("elev-2")


def test_chart_palette_independent_of_acc_override():
    """学科色板不随主色覆盖而变（用户换主色，图表语义不乱）。"""
    base = build_theme("light")
    overridden = build_theme("light", {"acc": "#123456"})
    for i in range(1, 5):
        assert base.get(f"chart-{i}") == overridden.get(f"chart-{i}")


def test_icon_size_tokens():
    """图标尺寸三档 + 描边宽度。"""
    t = build_theme("dark")
    assert t.get("icon-sm") == "16px"
    assert t.get("icon-md") == "20px"
    assert t.get("icon-lg") == "24px"
    assert t.get("icon-stroke") == 1.75


# ════════════════════════════════════════════════════════════════
# 第 3 层：学科色板对比度门禁（含阴性）
# ════════════════════════════════════════════════════════════════

def test_all_presets_pass_chart_contrast_gate():
    """五套内置预设的 chart 色对 surf 全部 ≥3:1（validate_all_presets 汇总）。"""
    report = validate_all_presets()
    offenders = {k: v for k, v in report.items() if v}
    assert not offenders, f"预设对比度违规: {offenders}"


def test_chart_contrast_gate_rejects_low_contrast():
    """阴性：把 chart-1 换成接近 surf 的颜色 → validate_tokens 必须报违规。"""
    tokens = dict(build_theme("light").tokens)
    tokens["chart-1"] = "#fefefe"  # 白底上的近白色
    problems = validate_tokens(tokens)
    assert any("chart-1" in p for p in problems), (
        f"低对比 chart 色未被拦下: {problems}")


# ════════════════════════════════════════════════════════════════
# 第 4 层：三端编译器消费新 token
# ════════════════════════════════════════════════════════════════

def test_web_compiler_emits_new_tokens_and_still_filters_pad():
    """Web：新 token 全部进 CSS 变量；pad* 仍被过滤（QSS 专用）。"""
    css = render_css_vars(build_theme("light"), build_theme("dark"))
    for probe in ("--fs-hero:40px", "--fs-title:20px", "--space-1:4px",
                  "--space-6:32px", "--icon-md:20px", "--chart-1:#2563eb",
                  "--elev-1:", "--elev-3:", "--sh:", "--sh2:",
                  "--icon-stroke:1.75"):
        assert probe in css, f"Web 产物缺 {probe}"
    assert "--pad:" not in css, "pad* 是 QSS 专用，不应进 Web 变量"
    assert "--radius-px:" not in css, "*-px 是 QSS 专用，不应进 Web 变量"


def test_qt_compiler_still_renders_with_extended_tokens():
    """Qt：模板渲染不因新 token 报错（未定义占位符会抛 TemplateError）。"""
    qss = render_qss(build_theme("dark"))
    assert len(qss) > 1000
    assert "请勿手工编辑" in qss.splitlines()[0]


# ════════════════════════════════════════════════════════════════
# 第 5 层：DESIGN.md 与实现不脱节
# ════════════════════════════════════════════════════════════════

def test_design_doc_exists_and_names_core_tokens():
    """规范文档必须存在，且提到核心 token 名（防「实现改了文档没跟上」）。"""
    doc = ROOT / "DESIGN.md"
    assert doc.exists(), "DESIGN.md 缺失"
    text = doc.read_text(encoding="utf-8")
    for token in ("fs-hero", "fs-title", "fs-body", "fs-caption",
                  "space-1", "space-6", "elev-1", "elev-2", "elev-3",
                  "chart-1", "chart-4", "icon-sm", "icon-md", "icon-lg",
                  "icon-stroke", "elev-1", "prefers-reduced-motion"):
        assert token in text, f"DESIGN.md 未提及 {token}"
    assert "Lucide" in text and "ISC" in text, "图标来源与协议必须写明"
