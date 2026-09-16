# -*- coding: utf-8 -*-
"""
前端第三方资源（KaTeX / Marked）的**版本与地址单一真源**

[缺陷修复·版本漂移 + CDN 单点]
1. **版本不一致**：看板 `docs/index.html` 用 KaTeX 0.16.9，而实时伴侣
   `docs/live.html` 用 0.16.8，同一产品两个版本，行为差异无从排查。
2. **CDN 是唯一来源**：项目主打 Local-First / 纯离线，但给手机用的自测看板
   公式渲染依赖境外 CDN；CDN 一断，`$\\int_0^1 x^2dx$` 就退化成源码串。
3. **降级脚本只有一半**：`fallbackMathUnicode()` 只在看板里有，live.html 缺失，
   于是断网时两边表现还不一样。

本模块把版本、地址与降级脚本收敛到一处；`--offline` 时改用本地 vendor 目录。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

_LOG = logging.getLogger(__name__)

#: 第三方前端库版本（改版本只改这里）
KATEX_VERSION = "0.16.9"
MARKED_VERSION = "12.0.2"

#: 资源相对路径（CDN 与本地 vendor 目录共用同一套相对路径）
_KATEX_CSS_PATH = f"katex@{KATEX_VERSION}/dist/katex.min.css"
_KATEX_JS_PATH = f"katex@{KATEX_VERSION}/dist/katex.min.js"
_KATEX_AUTORENDER_PATH = f"katex@{KATEX_VERSION}/dist/contrib/auto-render.min.js"
_MARKED_PATH = f"marked@{MARKED_VERSION}/marked.min.js"

_CDN_BASE = "https://cdn.jsdelivr.net/npm"
#: 本地 vendor 目录（相对 docs/，由 --offline 生成的产物引用）
VENDOR_REL = "assets/vendor"


def _url(rel_path: str, offline: bool) -> str:
    if offline:
        # 本地路径：katex@0.16.9/... → assets/vendor/katex/...
        parts = rel_path.split("/")
        pkg, version = parts[0].split("@", 1)
        return f"{VENDOR_REL}/{pkg}/{version}/{'/'.join(parts[1:])}"
    return f"{_CDN_BASE}/{rel_path}"


def katex_css_url(offline: bool = False) -> str:
    return _url(_KATEX_CSS_PATH, offline)


def katex_js_url(offline: bool = False) -> str:
    return _url(_KATEX_JS_PATH, offline)


def katex_autorender_url(offline: bool = False) -> str:
    return _url(_KATEX_AUTORENDER_PATH, offline)


def marked_js_url(offline: bool = False) -> str:
    return _url(_MARKED_PATH, offline)


def asset_map(offline: bool = False) -> dict:
    """返回模板占位符 → 资源地址的映射。"""
    return {
        "{{KATEX_CSS}}": katex_css_url(offline),
        "{{KATEX_JS}}": katex_js_url(offline),
        "{{KATEX_AUTORENDER_JS}}": katex_autorender_url(offline),
        "{{MARKED_JS}}": marked_js_url(offline),
    }


def load_fallback_math_js() -> str:
    """读取公式降级脚本 `fallbackMathUnicode()` 源码（单一真源）。

    该脚本在 KaTeX 加载失败时把 LaTeX 近似转成 Unicode，避免公式变成源码串。
    看板与 live.html 共用同一份，杜绝「一边有降级、一边没有」。
    """
    js_path = Path(__file__).resolve().parent / "vendor_fallback.js"
    try:
        return js_path.read_text(encoding="utf-8").strip()
    except OSError as exc:                     # pragma: no cover - 文件缺失属打包问题
        _LOG.warning("公式降级脚本缺失，公式在断网时将退化为源码: %s -> %s", js_path, exc)
        return "function fallbackMathUnicode(){}"


def vendor_files(offline: bool = False) -> dict:
    """本地化时需要的资源清单：``{落盘相对路径: 远程 URL}``。"""
    if not offline:
        return {}
    return {
        f"{VENDOR_REL}/katex/{KATEX_VERSION}/dist/katex.min.css": f"{_CDN_BASE}/{_KATEX_CSS_PATH}",
        f"{VENDOR_REL}/katex/{KATEX_VERSION}/dist/katex.min.js": f"{_CDN_BASE}/{_KATEX_JS_PATH}",
        f"{VENDOR_REL}/katex/{KATEX_VERSION}/dist/contrib/auto-render.min.js": f"{_CDN_BASE}/{_KATEX_AUTORENDER_PATH}",
        f"{VENDOR_REL}/marked/{MARKED_VERSION}/marked.min.js": f"{_CDN_BASE}/{_MARKED_PATH}",
    }


def font_urls_from_css(css_text: str, katex_version: str = KATEX_VERSION) -> dict:
    """从 KaTeX 的 CSS 中解析出它引用的字体文件地址。

    只取 woff2（现代浏览器足够，体积最小）：少了字体会让公式排版走形 ——
    KaTeX 的版式依赖这些字体的度量，缺字体时上下标与分式会明显错位。
    """
    found: dict = {}
    for rel in re.findall(r"url\(([^)]+\.woff2)\)", css_text):
        name = rel.strip().strip("'\"").split("/")[-1]
        target = f"{VENDOR_REL}/katex/{katex_version}/dist/fonts/{name}"
        found[target] = f"{_CDN_BASE}/katex@{katex_version}/dist/fonts/{name}"
    return found


def download_vendor_assets(docs_dir: Path, fetcher=None) -> list:
    """`--offline` 时把第三方资源（含 KaTeX 字体）抓到 ``docs/assets/vendor/``。

    故意不引入 requests/bs4 等第三方依赖，复用项目既有的标准库抓取器；
    下载失败只告警不中断（公式仍可走 fallbackMathUnicode 降级）。

    产物是可弃构建物：已在 .gitignore 中排除，不入库（避免把几百 KB
    第三方资源塞进仓库，与"仓库瘦身"目标相悖）。
    """
    files = vendor_files(offline=True)
    if not files:
        return []
    if fetcher is None:
        try:
            from intelligence.fetcher import HTTPFetcher
        except ImportError:                     # pragma: no cover
            from tools.intelligence.fetcher import HTTPFetcher  # type: ignore
        fetcher = HTTPFetcher()

    saved: list = []

    def _fetch_one(rel: str, url: str) -> bool:
        target = Path(docs_dir) / rel
        if target.exists() and target.stat().st_size > 0:
            saved.append(target)
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            res = fetcher.fetch(url)
            if not getattr(res, "is_valid", False):
                _LOG.warning("vendor 资源抓取失败（保留 CDN 引用即可）: %s", url)
                return False
            target.write_text(res.content, encoding="utf-8")
            saved.append(target)
            return True
        except Exception as exc:                # pragma: no cover - 离线环境常态
            _LOG.warning("vendor 资源落盘失败: %s -> %s", url, exc)
            return False

    for rel, url in files.items():
        _fetch_one(rel, url)

    # 字体：需先从 CSS 里解析出文件名，再逐个抓取
    css_rel = f"{VENDOR_REL}/katex/{KATEX_VERSION}/dist/katex.min.css"
    try:
        css_text = (Path(docs_dir) / css_rel).read_text(encoding="utf-8")
        for rel, url in font_urls_from_css(css_text).items():
            _fetch_one(rel, url)
    except OSError as exc:                      # pragma: no cover
        _LOG.warning("无法读取 KaTeX CSS 以解析字体清单: %s", exc)

    return saved


__all__ = [
    "font_urls_from_css",
    "KATEX_VERSION",
    "MARKED_VERSION",
    "VENDOR_REL",
    "asset_map",
    "download_vendor_assets",
    "katex_autorender_url",
    "katex_css_url",
    "katex_js_url",
    "load_fallback_math_js",
    "marked_js_url",
    "vendor_files",
]
