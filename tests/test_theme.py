# -*- coding: utf-8 -*-
"""
设计系统（tools/theme）回归测试

覆盖目标：把「四端各造一套配色」变成「一套 token + 三个编译器」之后，
必须保证 ① 内置预设自身可读（WCAG 门禁）② 自定义配色不达标会被拒 ③
模板与 token 不发生漂移（占位符写错必须报错，而不是静默失效）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.theme import (  # noqa: E402
    DEFAULT_PRESET,
    PRESET_ORDER,
    TemplateError,
    build_theme,
    contrast_ratio,
    derive_tokens,
    list_presets,
    load_theme,
    placeholder_names,
    render_ansi,
    render_css_vars,
    render_qss,
    validate_all_presets,
    validate_tokens,
)

TEMPLATE = (ROOT / "tools" / "theme" / "templates" / "app.qss.tmpl").read_text(encoding="utf-8")


# ── 色彩数学 ────────────────────────────────────────────────────

def test_contrast_ratio_extremes():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)


def test_contrast_ratio_is_symmetric():
    assert contrast_ratio("#a78bfa", "#111827") == pytest.approx(
        contrast_ratio("#111827", "#a78bfa"), abs=1e-9)


def test_shorthand_hex_supported():
    assert contrast_ratio("#fff", "#000") == pytest.approx(21.0, abs=0.01)


def test_invalid_color_raises():
    with pytest.raises(ValueError):
        contrast_ratio("not-a-color", "#000000")


# ── 门禁：内置预设必须全部达标 ──────────────────────────────────

def test_all_builtin_presets_pass_contrast():
    report = validate_all_presets()
    bad = {k: v for k, v in report.items() if v}
    assert not bad, f"内置预设未通过可读性校验: {bad}"


def test_preset_order_matches_available_presets():
    names = [k for k, _display, _mode in list_presets()]
    assert names == list(PRESET_ORDER)
    assert len(names) >= 5, "至少应有 5 套预设（含高对比无障碍主题）"


def test_every_preset_declares_dark_or_light_mode():
    for key in PRESET_ORDER:
        assert build_theme(key).mode in ("dark", "light")


# ── 门禁：不达标的自定义配色必须被拒并回退 ──────────────────────

def test_low_contrast_custom_theme_is_rejected(tmp_path: Path):
    """浅灰字配白底（约 1.2:1）必须被拒，回退内置默认预设。"""
    (tmp_path / "ui_theme.json").write_text(json.dumps({
        "preset": "light",
        "overrides": {"fg": "#eeeeee", "mut": "#f0f0f0"},
    }, ensure_ascii=False), encoding="utf-8")

    theme = load_theme(tmp_path)
    assert theme.name == DEFAULT_PRESET, "不达标的自定义主题应回退到内置默认预设"
    assert theme.source == "builtin-fallback"
    assert not validate_tokens(theme.tokens)


def test_validate_tokens_reports_actionable_message():
    problems = validate_tokens({"fg": "#dddddd", "surf": "#ffffff",
                                "mut": "#64748b", "acc": "#7c3aed"})
    assert problems, "低对比度必须报出问题"
    assert any("对比度不足" in p for p in problems)


# ── 自定义入口三级 ──────────────────────────────────────────────

def test_l1_preset_switch(tmp_path: Path):
    (tmp_path / "ui_theme.json").write_text('{"preset": "eye-green"}', encoding="utf-8")
    theme = load_theme(tmp_path)
    assert theme.name == "eye-green"
    assert theme.source == "ui_theme.json"


def test_l2_single_accent_override_propagates():
    """只改主色，悬停/按下/柔化强调色应自动跟着变（派生而非手填）。"""
    base = build_theme("dark")
    tuned = build_theme("dark", {"acc": "#22d3ee"})

    assert tuned.get("acc") == "#22d3ee"
    assert tuned.get("acc-hover") != base.get("acc-hover")
    assert tuned.get("acc-press") != base.get("acc-press")
    assert tuned.get("focus-ring") == "#22d3ee", "焦点环应跟随主色"
    qss = render_qss(tuned)
    assert "#22d3ee" in qss and base.get("acc") not in qss.split("acc-hover")[0]


def test_l2_radius_density_fontscale_take_effect():
    default_qss = render_qss(build_theme("dark"))
    compact = render_qss(build_theme("dark", {"radius": 4, "density": 0.5,
                                              "font-scale": 1.5}))
    assert "border-radius: 4px" in compact
    assert "border-radius: 16px" in default_qss
    assert "font-size: 19.5px" in compact, "字号缩放应反映到字号 token"
    assert compact != default_qss


def test_l3_full_token_dict(tmp_path: Path):
    (tmp_path / "ui_theme.json").write_text(json.dumps({
        "tokens": build_theme("dark").to_flat(),
    }, ensure_ascii=False), encoding="utf-8")
    theme = load_theme(tmp_path)
    assert not validate_tokens(theme.tokens)


def test_unknown_preset_name_falls_back_to_default():
    assert build_theme("no-such-preset").name == DEFAULT_PRESET


def test_broken_theme_file_does_not_crash(tmp_path: Path):
    (tmp_path / "ui_theme.json").write_text("{ this is not json", encoding="utf-8")
    theme = load_theme(tmp_path)
    assert theme.name == DEFAULT_PRESET
    assert theme.source == "builtin"


def test_missing_theme_file_uses_builtin_default(tmp_path: Path):
    theme = load_theme(tmp_path)
    assert theme.source == "builtin"


# ── 模板与 token 的一致性（防空转） ─────────────────────────────

def test_template_placeholders_all_defined():
    """模板里引用的每个 token 都必须在内置预设里存在。

    这条是「加主题时忘补 token」的守门员：一旦有人往模板写了新占位符却没定义，
    所有预设的渲染都会失败，而不是只在某个主题下悄悄少一条样式。
    """
    defined = set(build_theme(DEFAULT_PRESET).to_flat())
    used = placeholder_names(TEMPLATE)
    missing = sorted(used - defined)
    assert not missing, f"模板引用了未定义的 token: {missing}"


def test_undefined_placeholder_raises():
    with pytest.raises(TemplateError):
        render_qss(build_theme("dark"), "QWidget{color:{{no_such_token}}}")


def test_all_presets_render_clean_qss():
    for key in PRESET_ORDER:
        qss = render_qss(build_theme(key))
        assert "{{" not in qss and "}}" not in qss, f"{key} 渲染后残留未替换占位符"
        assert "QMainWindow" in qss
        assert build_theme(key).get("acc") in qss, f"{key} 的主色未进入样式表"


def test_derived_tokens_are_mode_aware():
    """深色主题的悬停色应比主色更亮，浅色主题应更暗（朝前景色方向偏移）。"""
    for key, expect_lighter in (("dark", True), ("light", False)):
        theme = build_theme(key)
        acc = str(theme.get("acc"))
        hover = str(theme.get("acc-hover"))
        lighter = contrast_ratio(hover, "#000000") > contrast_ratio(acc, "#000000")
        assert lighter is expect_lighter, f"{key} 的 acc-hover 偏移方向不对"


def test_derive_fills_only_missing_keys():
    tokens = derive_tokens({"bg": "#ffffff", "surf": "#ffffff", "fg": "#000000",
                            "acc": "#7c3aed", "acc-hover": "#custom-hover"})
    assert tokens["acc-hover"] == "#custom-hover", "已显式给出的值不应被派生覆盖"
    assert tokens["fg-strong"] == "#000000"


# ── Web / TUI 编译器 ────────────────────────────────────────────

def test_css_vars_contain_expected_blocks():
    css = render_css_vars(build_theme("light"), build_theme("dark"))
    for marker in (":root{", ":root[data-t=dark]{", ":root[data-t=light]{",
                   "@media(prefers-color-scheme:dark)"):
        assert marker in css, f"缺少 CSS 变量块: {marker}"
    assert "--acc:#7c3aed" in css.replace(" ", ""), "亮色主色应进入 CSS 变量"
    assert "--acc-grad:" in css, "看板既有的 --acc-grad 变量必须保留"


def test_ansi_truecolor_and_degrade():
    dark = build_theme("dark")
    tc = render_ansi(dark, truecolor=True, disabled=False)
    assert tc["ACCENT"].startswith("\033[38;2;"), "应输出 24 位真彩序列"
    assert tc["RESET"] == "\033[0m"

    low = render_ansi(dark, truecolor=False, disabled=False)
    assert low["ACCENT"] == "\033[96m", "不支持真彩时应降级为 16 色"

    off = render_ansi(dark, disabled=True)
    assert all(code == "" for code in off.values()), "去色模式下不应输出任何转义码"


def test_ansi_respects_no_color_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    from tools.theme.compile_ansi import colors_disabled
    assert colors_disabled() is True


def test_ansi_keeps_legacy_field_names():
    """TUI 的 Colors 类字段名必须被保留，调用点才能零改动。"""
    ansi = render_ansi(build_theme("dark"), truecolor=True, disabled=False)
    for field in ("RESET", "BOLD", "DIM", "ITALIC", "UNDERLINE",
                  "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE"):
        assert field in ansi, f"缺少既有字段 {field}"


def test_apply_to_colors_class_updates_in_place():
    from tools.theme import apply_to_colors_class

    class Colors:
        RESET = "\033[0m"
        CYAN = "\033[96m"

    apply_to_colors_class(Colors, build_theme("dark"), truecolor=True, disabled=False)
    assert Colors.CYAN.startswith("\033[38;2;"), "既有字段应被主题覆盖"
    assert hasattr(Colors, "OK"), "语义扩展字段应被补充"
