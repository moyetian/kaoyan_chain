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

本模块把版本、地址与降级脚本收敛到一处；默认使用**本地 vendor 目录**，
需要时可用 `KY_VENDOR_MODE=cdn`（或 `build.py --cdn`）切回 CDN。
"""

from __future__ import annotations

import logging
import os
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
#: 本地 vendor 目录（相对 docs/，默认即引用这里的副本）
VENDOR_REL = "assets/vendor"

#: 默认资源来源：「local」（内联本地 vendor）或「cdn」。
#  [C7 修复·默认离线] 本产品有**两层离线**含义，此前被混为一谈：
#    ① 后端可离线 —— 判分/复测/考纲 Diff 等不依赖外网，早已成立；
#    ② 前端可离线 —— 手机看板的公式渲染仍走境外 CDN。
#  对「地铁 / 图书馆破网 / 考场自习室」这个主用场景，① 成立但 ② 不成立
#  等于白搭：断网时 $\\int_0^1 x^2dx$ 退化成 LaTeX 源码串，遮罩自测直接失效。
#  故默认改为 local —— vendor 资源随仓库分发（已在 .gitignore 中解除忽略），
#  克隆即用，不需要先联网构建一次。
_DEFAULT_MODE = "local"


def vendor_mode() -> str:
    """解析当前资源来源：显式环境变量 KY_VENDOR_MODE 优先，否则用默认值。"""
    raw = (os.environ.get("KY_VENDOR_MODE") or "").strip().lower()
    if raw in ("cdn", "online", "remote"):
        return "cdn"
    if raw in ("local", "offline", "vendor"):
        return "local"
    return _DEFAULT_MODE


def is_offline_default() -> bool:
    """默认构建是否走本地 vendor（供 build.py 判断是否需要预抓取资源）。"""
    return vendor_mode() == "local"


def _url(rel_path: str, offline: bool) -> str:
    if offline:
        # 本地路径：katex@0.16.9/... → assets/vendor/katex/...
        parts = rel_path.split("/")
        pkg, version = parts[0].split("@", 1)
        return f"{VENDOR_REL}/{pkg}/{version}/{'/'.join(parts[1:])}"
    return f"{_CDN_BASE}/{rel_path}"


def katex_css_url(offline: bool = True) -> str:
    return _url(_KATEX_CSS_PATH, offline)


def katex_js_url(offline: bool = True) -> str:
    return _url(_KATEX_JS_PATH, offline)


def katex_autorender_url(offline: bool = True) -> str:
    return _url(_KATEX_AUTORENDER_PATH, offline)


def marked_js_url(offline: bool = True) -> str:
    return _url(_MARKED_PATH, offline)


def asset_map(offline: bool = True) -> dict:
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


def vendor_files(offline: bool = True) -> dict:
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

    [C7 修复·字体 404] KaTeX 的 ``@font-face`` 会同时声明 woff2 / woff / ttf
    三档来源。只下载 woff2 时，浏览器**仍会按 CSS 去请求 woff 与 ttf 作兜底**，
    于是控制台出现 4 个 404（两个字体 × woff/ttf），恰好被
    ``tools/check_dashboard.py`` 的真浏览器运行时校验判为阻塞级缺陷。
    既然全部字重都已用 woff2 本地化，这里顺带把 CSS 中多余的
    woff/ttf 来源剥掉，从根上消除这些无意义的请求。
    """
    found: dict = {}
    for rel in re.findall(r"url\(([^)]+\.woff2)\)", css_text):
        name = rel.strip().strip("'\"").split("/")[-1]
        target = f"{VENDOR_REL}/katex/{katex_version}/dist/fonts/{name}"
        found[target] = f"{_CDN_BASE}/katex@{katex_version}/dist/fonts/{name}"
    return found


def strip_non_woff2_font_sources(css_text: str) -> str:
    """把 KaTeX CSS 里的 woff/ttf 字体来源删掉，只留 woff2。

    删除形如 ``,url(fonts/X.woff) format("woff")`` 与
    ``,url(fonts/X.ttf) format("truetype")`` 的兜底项（含前置逗号，
    避免留下 `url(a.woff2), }` 这类空尾项）。
    """
    return re.sub(
        r",\s*url\([^)]+\.(?:woff|ttf)\)\s*(?:format\([^)]*\))?",
        "",
        css_text,
    )


def download_vendor_assets(docs_dir: Path, fetcher=None) -> list:
    """把第三方资源（含 KaTeX 字体）抓到 ``docs/assets/vendor/``。

    默认构建即走本地 vendor（见 ``vendor_mode``），故本函数是常规路径而非
    可选的 ``--offline`` 分支。

    故意不引入 requests/bs4 等第三方依赖，复用项目既有的标准库抓取器；
    下载失败只告警不中断（公式仍可走 fallbackMathUnicode 降级）。

    [C7 调整] 产物现已**随仓库分发**（.gitignore 中已解除忽略）：看板的主用
    场景是地铁/破网环境，只有把 vendor 资源提交进仓库，克隆后断网才能直接用，
    不必先联网构建一次。体积约 740KB，换公式不再退化为 LaTeX 源码串。
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
        css_path = Path(docs_dir) / css_rel
        css_text = css_path.read_text(encoding="utf-8")
        for rel, url in font_urls_from_css(css_text).items():
            _fetch_one(rel, url)
        # 只本地化了 woff2，就把 CSS 里声明 woff/ttf 的来源剥掉，
        # 否则浏览器仍会去请求它们并产生 404（见 strip_non_woff2_font_sources）
        stripped = strip_non_woff2_font_sources(css_text)
        if stripped != css_text:
            css_path.write_text(stripped, encoding="utf-8")
    except OSError as exc:                      # pragma: no cover
        _LOG.warning("无法读取 KaTeX CSS 以解析字体清单: %s", exc)

    return saved


__all__ = [
    "font_urls_from_css",
    "is_offline_default",
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
    "strip_non_woff2_font_sources",
    "vendor_files",
    "vendor_mode",
]
