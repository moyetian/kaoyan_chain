# -*- coding: utf-8 -*-
"""
Web 看板回归测试（资源版本 / 模板 / 主题注入 / 单文件规模）

覆盖本轮修复的真实缺陷：
  1. **版本漂移**：看板用 KaTeX 0.16.9、实时伴侣用 0.16.8（且 marked 不定版），
     同一产品三个版本，行为差异无从排查。
  2. **降级脚本只有一半**：`fallbackMathUnicode()` 只在看板里有，live.html 缺失，
     断网时两边表现不一致。
  3. **配色硬编码在 build.py 的模板字符串里**（四段变量块），与 GUI/终端各一套。
  4. **上帝文件**：build.py 2062 行（其中 1025 行是内联 HTML）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web import vendor  # noqa: E402

INDEX = ROOT / "docs" / "index.html"
LIVE = ROOT / "docs" / "live.html"


# ── 第三方资源单一真源 ──────────────────────────────────────────

def test_katex_version_is_single_source():
    """docs 下的看板与实时伴侣必须引用同一个 KaTeX 版本。"""
    index_html = INDEX.read_text(encoding="utf-8")
    live_html = LIVE.read_text(encoding="utf-8")
    index_versions = set(re.findall(r"katex@([\d.]+)", index_html))
    live_versions = set(re.findall(r"katex@([\d.]+)", live_html))

    assert index_versions == {vendor.KATEX_VERSION}, f"看板 KaTeX 版本: {index_versions}"
    assert live_versions == {vendor.KATEX_VERSION}, f"伴侣 KaTeX 版本: {live_versions}"


def test_marked_version_is_pinned():
    """marked 原先写作 `npm/marked/marked.min.js`（不定版），升级即可能静默失败。"""
    live_html = LIVE.read_text(encoding="utf-8")
    assert f"marked@{vendor.MARKED_VERSION}" in live_html
    assert "npm/marked/marked.min.js" not in live_html, "仍存在未固定版本的 marked 引用"


def test_fallback_js_exists_in_both_pages():
    """断网降级脚本必须两边都有，否则同为断网、两端表现却不同。"""
    for path in (INDEX, LIVE):
        text = path.read_text(encoding="utf-8")
        assert "function fallbackMathUnicode" in text, f"{path.name} 缺少公式降级脚本"


def test_fallback_js_matches_single_source():
    """live.html 是手工维护的静态文件，必须与降级脚本真源保持同步。

    这条是「手工文件 vs 代码真源」的漂移守门员：任何人只改一边都会在这里失败。
    """
    canonical = vendor.load_fallback_math_js()
    # 取函数签名与首两个语句作为指纹（避免空白差异导致误报）
    fingerprint = [ln.strip() for ln in canonical.splitlines() if ln.strip()][:3]
    live_html = LIVE.read_text(encoding="utf-8")
    for ln in fingerprint:
        assert ln in live_html, f"live.html 中的降级脚本与真源不一致，缺少: {ln}"


# ── 模板与占位符 ────────────────────────────────────────────────

def test_template_readable_and_placeholders_declared():
    """模板已移出 build.py，且其占位符都能被构建期解析。"""
    template_path = DASHBOARD / "web" / "template.html"
    template = template_path.read_text(encoding="utf-8")
    assert template.lstrip().startswith("<!doctype html>")
    placeholders = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", template))
    assert {"THEME_CSS", "FALLBACK_MATH_JS", "DATA", "RADAR", "DDAY1"} <= placeholders


def test_built_html_has_no_leftover_placeholders():
    """构建产物不得残留任何 {{占位符}}（残留会直接显示给用户）。"""
    html = INDEX.read_text(encoding="utf-8")
    leftover = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", html)))
    assert not leftover, f"产物残留未替换占位符: {leftover}"


def test_placeholder_map_covers_template():
    """构建期提供的占位符映射必须覆盖模板实际用到的非数据占位符。"""
    sys.path.insert(0, str(DASHBOARD))
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dash_build", DASHBOARD / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # type: ignore[union-attr]

    mapping = module.render_theme_placeholders(offline=False)
    template = (DASHBOARD / "web" / "template.html").read_text(encoding="utf-8")
    declared = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", template))
    data_keys = {"DMATH", "DDAY1", "DAYNO", "TOTALDAYS", "PLANPCT", "TODAY",
                 "MEMONOTES", "WEAKNOTES", "RADAR", "DATA", "STAMP"}
    assets = {k.strip("{}") for k in mapping}
    missing = declared - data_keys - assets
    assert not missing, f"模板占位符无人提供: {missing}"


# ── 主题注入（Web 并入设计系统） ────────────────────────────────

def test_theme_vars_injected_from_design_system():
    """看板配色必须来自 tools/theme 的 token，而不是模板里的硬编码四段。"""
    from tools.theme import build_theme

    html = INDEX.read_text(encoding="utf-8")
    light, dark = build_theme("light"), build_theme("dark")
    for theme in (light, dark):
        acc = str(theme.get("acc"))
        assert acc in html, f"主题主色未注入看板: {acc}"
        assert str(theme.get("bg")) in html, f"主题底色未注入看板: {theme.get('bg')}"


def test_theme_vars_cover_dark_and_light_selectors():
    html = INDEX.read_text(encoding="utf-8")
    for selector in (":root{", ":root[data-t=dark]{", ":root[data-t=light]{"):
        assert selector in html, f"缺少主题变量块: {selector}"


# ── --offline ──────────────────────────────────────────────────

def test_offline_assets_point_to_local_vendor():
    mapping = vendor.asset_map(offline=True)
    for url in mapping.values():
        assert url.startswith(vendor.VENDOR_REL), f"离线模式资源应为本地路径: {url}"
    on_mapping = vendor.asset_map(offline=False)
    for url in on_mapping.values():
        assert url.startswith("https://"), f"常规模式资源应为 CDN: {url}"


def test_committed_html_never_references_local_vendor():
    """提交的产物必须走 CDN。

    否则 GitHub Pages 上会引用 gitignore 掉的 assets/vendor/，公式直接失效 ——
    这是「本地跑了一次 --offline 就随手提交」的典型事故。
    """
    for path in (INDEX, LIVE):
        text = path.read_text(encoding="utf-8")
        assert "assets/vendor" not in text, f"{path.name} 引用了未入库的本地 vendor 资源"


def test_font_list_parsed_from_css():
    """离线渲染需要 KaTeX 字体（缺字体会让公式版式明显走形）。"""
    css = "a{src:url(fonts/KaTeX_Main-Regular.woff2)}b{src:url(fonts/KaTeX_AMS-Regular.woff2)}"
    fonts = vendor.font_urls_from_css(css)
    assert len(fonts) == 2
    for rel, url in fonts.items():
        assert rel.startswith(f"{vendor.VENDOR_REL}/katex/")
        assert url.endswith(".woff2")


# ── 单文件规模（agent.md 红线） ─────────────────────────────────

def test_build_py_within_size_ceiling():
    """build.py 必须回到硬红线以内（改造前 2062 行）。"""
    lines = len((DASHBOARD / "build.py").read_text(encoding="utf-8").splitlines())
    assert lines <= 800, f"build.py 仍有 {lines} 行，超出 800 行硬红线"


def test_web_modules_within_recommended_band():
    for path in sorted((DASHBOARD / "web").glob("*.py")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 500, f"{path.name} 有 {lines} 行，超出推荐区间上限"
