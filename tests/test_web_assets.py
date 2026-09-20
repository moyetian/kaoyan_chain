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
    """docs 下的看板与实时伴侣必须引用同一个 KaTeX 版本。

    [C7 适配] 默认已改为本地 vendor（`assets/vendor/katex/<版本>/...`），
    故版本号不再以 `katex@0.16.9` 形式出现在产物里，需同时从两条形态提取。
    """
    for path in (INDEX, LIVE):
        text = path.read_text(encoding="utf-8")
        versions = set(re.findall(r"katex@([\d.]+)", text))
        versions |= set(re.findall(r"assets/vendor/katex/([\d.]+)/", text))
        assert versions == {vendor.KATEX_VERSION}, f"{path.name} KaTeX 版本: {versions}"


def test_marked_version_is_pinned():
    """marked 原先写作 `npm/marked/marked.min.js`（不定版），升级即可能静默失败。"""
    live_html = LIVE.read_text(encoding="utf-8")
    versions = set(re.findall(r"marked@([\d.]+)", live_html))
    versions |= set(re.findall(r"assets/vendor/marked/([\d.]+)/", live_html))
    assert versions == {vendor.MARKED_VERSION}, f"伴侣 marked 版本: {versions}"
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


def test_preset_selector_injected_into_page():
    """5 套预设必须真正出现在页面上可选，而不只活在 token 层。"""
    html = INDEX.read_text(encoding="utf-8")
    from tools.theme import PRESET_ORDER

    for key in PRESET_ORDER:
        if key in ("dark", "light"):
            continue
        assert f":root[data-t='{key}']{{" in html, f"看板缺少 {key} 预设的 CSS 规则"
    assert "var KY_PRESETS=" in html, "前端选择器缺少预设清单"


def test_rhythm_theme_injected_and_valid():
    """节律主题必须是已知预设，且与构建时的倒计时一致。"""
    import json
    import re

    from tools.theme import PRESET_ORDER, rhythm_preset

    html = INDEX.read_text(encoding="utf-8")
    rhythm = json.loads(re.search(r"var KY_RHYTHM=(\{.*?\});", html).group(1))
    assert rhythm["p"] in PRESET_ORDER, f"节律推荐了未知预设: {rhythm['p']}"
    assert rhythm["p"] == rhythm_preset(rhythm["days"]), "节律预设应与自身天数自洽"
    dday = int(re.search(r"倒计时 (\d+) 天", html).group(1))
    assert rhythm["days"] == dday, "节律天数应与看板倒计时同源"


# ── --offline ──────────────────────────────────────────────────

def test_offline_assets_point_to_local_vendor():
    mapping = vendor.asset_map(offline=True)
    for url in mapping.values():
        assert url.startswith(vendor.VENDOR_REL), f"离线模式资源应为本地路径: {url}"
    on_mapping = vendor.asset_map(offline=False)
    for url in on_mapping.values():
        assert url.startswith("https://"), f"常规模式资源应为 CDN: {url}"


def test_committed_html_references_only_tracked_vendor():
    """提交的产物只能引用**随仓库分发**的本地 vendor 资源，不得引用会被 gitignore 的路径。

    [C7 适配] 本测试原为「提交的产物必须走 CDN」（因为 vendor 目录不入库，
    引用它会让 GitHub Pages 上的公式失效）。默认改为离线优先后，这条护栏的
    **意图**（产物引用的资源必须真的能被发布出去）仍然成立，但实现方式反了：
    现在 vendor 资源随仓库入库，所以改为断言所有被引用的 vendor 文件
    都真实存在于仓库内 —— 这比"不许引用本地路径"更贴近真正要防的事故：
    本地跑了一次构建、产物指向一个并不存在的资源。

    同时必须确认 `05-考研看板/docs/` 那份副本仍被 .gitignore 排除
    （它是构建副本，不是发布源）。
    """
    referenced = set()
    for path in (INDEX, LIVE):
        text = path.read_text(encoding="utf-8")
        referenced |= set(re.findall(r"(?:src|href)=\"(assets/vendor/[^\"]+)\"", text))
        assert "cdn.jsdelivr.net" not in text, f"{path.name} 仍引用境外 CDN（离线优先已生效）"

    assert referenced, "产物未引用任何 vendor 资源，公式渲染将失效"
    missing = sorted(r for r in referenced if not (ROOT / "docs" / r).exists())
    assert not missing, f"产物引用了仓库中不存在的 vendor 资源: {missing}"


def test_font_list_parsed_from_css():
    """离线渲染需要 KaTeX 字体（缺字体会让公式版式明显走形）。"""
    css = "a{src:url(fonts/KaTeX_Main-Regular.woff2)}b{src:url(fonts/KaTeX_AMS-Regular.woff2)}"
    fonts = vendor.font_urls_from_css(css)
    assert len(fonts) == 2
    for rel, url in fonts.items():
        assert rel.startswith(f"{vendor.VENDOR_REL}/katex/")
        assert url.endswith(".woff2")


def test_woff_and_ttf_sources_are_stripped():
    """只本地化了 woff2，就必须把 CSS 里 woff/ttf 的兜底来源删掉。

    否则浏览器仍会去请求这两个不存在的文件，控制台出现 404 ——
    实测 4 条（KaTeX_Main-Regular / KaTeX_Math-Italic 各 woff + ttf），
    会被 `tools/check_dashboard.py` 的真浏览器运行时校验判为阻塞级缺陷。
    """
    css = (
        '@font-face{font-family:X;src:url(fonts/A.woff2) format("woff2"),'
        'url(fonts/A.woff) format("woff"),url(fonts/A.ttf) format("truetype")}'
    )
    out = vendor.strip_non_woff2_font_sources(css)
    assert "A.woff2" in out, "woff2 来源必须保留"
    assert ".woff)" not in out and ".ttf)" not in out, f"仍残留 woff/ttf 来源: {out}"
    assert "$" not in out and "src:url(fonts/A.woff2) format(\"woff2\")}" in out, \
        f"剥离后语法被破坏: {out}"


def test_vendored_css_declares_only_existing_fonts():
    """真实产物：CSS 声明的每一个字体文件都必须真实存在（防 404 复发）。"""
    css_path = ROOT / "docs" / vendor.VENDOR_REL / "katex" / vendor.KATEX_VERSION / "dist" / "katex.min.css"
    if not css_path.exists():
        pytest.skip("vendor 资源未本地化（产物目录未构建）")
    css = css_path.read_text(encoding="utf-8")
    declared = re.findall(r"url\(([^)]+\.(?:woff2|woff|ttf))\)", css)
    assert declared, "原版 KaTeX CSS 应至少声明一种字体"
    base = css_path.parent
    missing = sorted(d for d in declared if not (base / d).exists())
    assert not missing, f"CSS 声明了仓库中不存在的字体，浏览器将 404: {missing}"


# ── 单文件规模（agent.md 红线） ─────────────────────────────────

def test_build_py_within_size_ceiling():
    """build.py 必须回到硬红线以内（改造前 2062 行）。"""
    lines = len((DASHBOARD / "build.py").read_text(encoding="utf-8").splitlines())
    assert lines <= 800, f"build.py 仍有 {lines} 行，超出 800 行硬红线"


def test_web_modules_within_recommended_band():
    for path in sorted((DASHBOARD / "web").glob("*.py")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 500, f"{path.name} 有 {lines} 行，超出推荐区间上限"
