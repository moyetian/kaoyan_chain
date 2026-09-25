# -*- coding: utf-8 -*-
"""
Web 看板 P1 首屏 / 侧栏重构回归（外部评审方案 P1 阶段）

被钉住的缺陷（均已独立核实）：

  1. **媒体查询反向覆盖（根因）**：桌面端规则写在前部、基础（移动优先）规则
     写在后部且特异性相同 → 桌面规则被基础规则吃掉：
       · ``.wrap`` 的 ``max-width:1080px;margin-left:240px`` 被后面的
         ``max-width:820px;margin:0 auto`` 覆盖（1440px 视口下内容仅约 790px）；
       · ``.bar button`` 的 ``flex:none;padding:12px 16px`` 被后面的
         ``flex:1;padding:9px 4px 10px`` 覆盖 → 侧栏 6 项纵向平分 900px 视口，
         激活项变成约 130px 的巨型高亮块。
  2. **侧栏无品牌区**：CSS 里有 ``.sidebar-header`` 但 HTML 无该元素（死 CSS）。
  3. **侧栏 6 个页签混用内联 SVG 与 emoji**（🧠 🎯 🗺️ 📡）。
  4. **字体栈 token 首项 "Inter" 无字体文件**（仓库不随包分发、也不允许 CDN），
     且 ``var(--font-family)`` 全仓无消费点。
  5. **右上角 4 组信息 + 全宽进度行挤成一团**（主次不分）。
  6. **今日任务空状态裸奔**：只有一句说明，无图标、无 CTA。
  7. **sprite 二次注入**：模板 JS 注释里写了字面量占位符名，而 build() 是单遍
     ``re.sub`` 全文替换——整份 sprite（约 15KB XML）被再注一遍进 ``<script>``
     注释里（产物 145.9KB → 131.4KB 即此）。
  8. **preset_rules_css() 的 Inter 漏网**：Web 侧剥离发生在拼接预设规则之前，
     eye-green / pink / hc 三套预设重声明的 ``--font-family`` 仍指向 Inter。

[范围] 只读：模板源码 + 一次真实 ``build.build()``（不写盘）。
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

import build  # noqa: E402

TEMPLATE_PATH = DASHBOARD / "web" / "template.html"
SPRITE_PATH = ROOT / "docs" / "assets" / "icons.svg"
COMMITTED_INDEX = ROOT / "docs" / "index.html"

#: emoji 判定。刻意**不含** 2500–257F（制表/几何符号）与 2190–21FF（箭头）：
#: 模板里的 ``─ ═ ● ○ ‹ › → ↑ ↓`` 是排版记号（分隔线、图例圆点、翻页箭头、
#: 键盘方向键说明），属设计系统内的合法字符，算作 emoji 会造成误报。
EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # 图形符号与表情（🧠 📡 🗺️ 📈）
    "\u2600-\u26FF"           # 杂项符号（⚠ ☀ ⚡）
    "\u2700-\u27BF"           # 装饰符号（✨ ✓ ❌）
    "\u2B00-\u2BFF"           # 杂项符号与箭头（⭐）
    "\uFE0F"                  # 变体选择符（emoji 呈现）
    "]"
)


# ── 读取与解析辅助 ──────────────────────────────────────────────

def _style_block(text: str) -> str:
    m = re.search(r"<style>(.*?)</style>", text, re.S)
    assert m, "模板缺少 <style> 块"
    return m.group(1)


def _norm(css: str) -> str:
    """把连续空白压成单空格：CSS 里一条规则常跨多行，直接按原文匹配会漏。

    只压成单空格（而非删光）是必须的——``.bar button`` 这类后代选择器里
    的空格是语义的一部分，删掉会变成 ``.barbutton``。
    """
    return re.sub(r"\s+", " ", css)


def _strip_comments(css: str) -> str:
    """去掉 CSS 注释后再做字面量断言。

    [踩坑记录] 模板注释里**故意**写有缺陷原文作对照说明
    （如 ``.wrap{max-width:820px;margin:0 auto}`` 与 ``max-width:1080px``）。
    直接按字面量 grep 会命中注释造成误报——本仓库已有同类教训
    （见 test_web_assets.py 的 host.clientWidth 注释误伤条）。
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _css(text: str) -> str:
    """模板/产物 → 已剥注释、已压空白的 CSS（字面量断言的统一入口）。"""
    return _norm(_strip_comments(_style_block(text)))


def _sidebar_block(text: str) -> str:
    m = re.search(r'<aside class="sidebar">(.*?)</aside>', text, re.S)
    assert m, '模板缺少侧栏 <aside class="sidebar">'
    return m.group(1)


def _block_end(flat: str, start: int) -> int:
    """返回 ``start`` 处规则块（含嵌套花括号）的结束下标。"""
    i = flat.index("{", start)
    depth = 0
    for j in range(i, len(flat)):
        if flat[j] == "{":
            depth += 1
        elif flat[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(flat)


def _desktop_media_is_last(css: str) -> bool:
    """桌面媒体查询必须晚于基础规则，且是样式表里最后一个 @media 块。

    [阴性对照入口] 本函数被 ``test_desktop_media_query_is_last`` 同时用于
    真实模板与两个「已知反向覆盖样本」，后者必须返回 False。
    """
    flat = _norm(css)
    try:
        i_wrap = flat.index(".wrap{max-width:none")
        i_bar = flat.index(".bar button{flex:1;min-width:0")
        i_tablet = flat.index("@media(min-width:768px)")
        i_desktop = flat.index("@media(min-width:1100px)")
        i_first_minw = flat.index("@media(min-width:")
    except ValueError:
        return False
    if not (i_desktop > i_wrap and i_desktop > i_bar and i_tablet > i_bar):
        return False
    if i_first_minw != i_tablet:          # 基础段里混进了 min-width 媒体查询
        return False
    if flat.rindex("@media") != i_desktop:
        return False
    # 桌面段之后不得再出现同名基础规则——「后面的基础规则吃掉前面的桌面规则」
    # 正是 P1 根因；只查媒体查询顺序会漏掉这一形态。
    tail = flat[_block_end(flat, i_desktop) + 1:]
    return ".wrap{" not in tail and ".bar button{" not in tail


# ── 夹具 ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def template_text() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def built_html() -> str:
    """真实构建一次（只读仓库数据、不落盘），供产物级断言复用。"""
    html, _data, _warns, _secs = build.build(offline=True)
    return html


# ── ① 侧栏无 emoji ─────────────────────────────────────────────

def test_sidebar_has_no_emoji(template_text):
    """侧栏（及整个模板）不得残留 emoji——图标一律走 sprite。

    阴性对照在末尾：检测器必须能抓到真 emoji，否则本条会因正则失效而假绿。
    """
    hits = EMOJI_RE.findall(_sidebar_block(template_text))
    assert not hits, f"侧栏仍残留 emoji: {hits}"

    all_hits = sorted(set(EMOJI_RE.findall(template_text)))
    assert not all_hits, f"模板仍残留 emoji: {all_hits}"

    # 阴性对照：检测器自检（若正则被写坏/清空，这里先失败）
    assert EMOJI_RE.search("🧠 必背"), "emoji 检测正则失效（阴性对照未命中）"
    assert EMOJI_RE.search("⚠️"), "emoji 检测正则未覆盖带变体选择符的符号"


def test_sidebar_tabs_all_use_lucide_sprite(template_text):
    """6 个页签必须全部用 sprite 图标，且引用的 symbol 真实存在于 sprite 文件。"""
    side = _sidebar_block(template_text)
    btns = re.findall(
        r'<button role="tab"[^>]*data-p="([a-z]+)"[^>]*>(.*?)</button>', side, re.S)
    assert [k for k, _ in btns] == ["today", "memo", "weak", "stat", "map", "radar"]

    sprite = SPRITE_PATH.read_text(encoding="utf-8")
    for key, body in btns:
        uses = re.findall(r'<use href="#(i-[a-z0-9-]+)"/>', body)
        assert len(uses) == 1, f"页签 {key} 的图标不是唯一的 sprite <use>: {uses}"
        assert f'id="{uses[0]}"' in sprite, f"页签 {key} 引用了 sprite 中不存在的 {uses[0]}"
        assert "class='ic'" in body or 'class="ic"' in body, \
            f"页签 {key} 的图标未包在 .ic 容器里（运行时探针会判空图标）"


def test_all_sprite_references_resolve(template_text):
    """模板与 build.py 引用的每个 ``#i-xxx`` 都必须在 sprite 里有定义。

    运行时探针只断言「.ic 容器内有 <svg>」——``<use href="#不存在的 id">``
    同样会生成一个空 ``<svg>`` 元素，图标却是隐形的（静默失败）。
    故必须在构建期把「引用的 id」与「sprite 定义的 id」做交叉核对。
    """
    sprite = SPRITE_PATH.read_text(encoding="utf-8")
    defined = set(re.findall(r'id="(i-[a-z0-9-]+)"', sprite))
    assert len(defined) >= 40, f"sprite 定义过少（{len(defined)}），疑似被截断"

    used = set(re.findall(r"#(i-[a-z0-9-]+)", template_text))
    used |= {"i-" + n for n in
             re.findall(r"icon(?:Html|Box)\('([a-z0-9-]+)'", template_text)}
    build_src = (DASHBOARD / "build.py").read_text(encoding="utf-8")
    used |= {"i-" + n for n in re.findall(r"sprite_icon\('([a-z0-9-]+)'", build_src)}

    assert used, "未解析到任何图标引用（正则失效？）"
    assert used <= defined, f"引用了 sprite 中不存在的图标: {sorted(used - defined)}"


# ── ② sprite 已内联且只注入一次 ────────────────────────────────

def test_icon_sprite_inlined_exactly_once(built_html):
    """产物必须内联 sprite（file:// 下跨文件 <use> 会被同源策略拒绝）。

    且**恰好一次**：模板里出现第二处字面量占位符名时，单遍 re.sub 会把整份
    sprite 再注一遍（实测发生在 ``<script>`` 的 JS 注释里，产物 145.9KB→131.4KB）。
    第二份落在脚本块内更危险——sprite 内容一旦出现注释终止序列，整个
    ``<script>`` 会被提前截断，全页交互报废。
    """
    assert '<symbol id="i-today"' in built_html, "产物未内联图标 sprite，图标将全部不可见"
    assert built_html.count('<symbol id="i-today"') == 1, "sprite 被注入了多次"
    assert built_html.count("<symbol ") == 42, "sprite symbol 数量与 icons.py 子集不符"
    assert "{{ICON_SPRITE}}" not in built_html

    for block in re.findall(r"<script[^>]*>(.*?)</script>", built_html, re.S):
        assert "<symbol " not in block, "sprite 被注入了 <script> 块内部"


# ── ③ .wrap 去掉宽度上限 ───────────────────────────────────────

def test_wrap_fills_available_width(template_text, built_html):
    """.wrap 不得再有 1080/820px 内容宽度上限；首屏改为响应式网格。"""
    flat = _css(template_text)

    wrap = re.search(r"\.wrap\{([^}]*)\}", flat)
    assert wrap, "缺少 .wrap 基础规则"
    assert "max-width:none" in wrap.group(1), f".wrap 仍有宽度上限: {wrap.group(1)}"
    for bad in ("max-width:1080px", "max-width:820px"):
        assert bad not in flat, f"CSS 仍存在内容宽度上限 {bad}"
        assert bad not in _css(built_html), f"产物仍存在内容宽度上限 {bad}"

    # 新网格：基础单列（移动优先）→ 桌面 12 栏
    assert ".dash{display:grid;grid-template-columns:minmax(0,1fr)" in flat, "首屏未改为网格"
    assert "repeat(12,minmax(0,1fr))" in flat, "缺少桌面 12 栏网格"


# ── ④ 媒体查询顺序（含阴性对照） ───────────────────────────────

def test_desktop_media_query_is_last(template_text):
    """桌面媒体查询必须在基础规则之后、且位于样式表末尾（反向覆盖的根因修复）。"""
    css = _strip_comments(_style_block(template_text))
    assert _desktop_media_is_last(css), \
        "桌面媒体查询不在基础规则之后/文件末尾——反向覆盖会复发（.wrap 上限、巨型激活块）"

    # 桌面段必须真的承载「侧栏 pill + 12 栏网格」这两处覆盖
    flat = _norm(css)
    desktop = flat[flat.index("@media(min-width:1100px)"):]
    assert ".bar button{flex:none;width:100%;height:40px" in desktop, "桌面侧栏未改为 40px pill"
    assert "repeat(12,minmax(0,1fr))" in desktop, "桌面 12 栏网格不在桌面媒体查询内"

    # 阴性对照 1：把桌面规则放回基础规则之前（P1 根因的原始形态），检测器必须报警
    reversed_css = (
        "@media(min-width:1100px){.wrap{margin-left:240px;max-width:1080px}}"
        ".wrap{max-width:none;margin:0}"
        ".bar button{flex:1;min-width:0}"
    )
    assert not _desktop_media_is_last(reversed_css), \
        "顺序检测器对已知反向覆盖样本未报警（阴性对照 1 失败）"

    # 阴性对照 2：桌面段之后又补了一条基础规则（后面的规则吃掉桌面规则），
    # 只查媒体查询顺序的实现会漏掉这一形态
    overwritten_css = (
        ".wrap{max-width:none}"
        ".bar button{flex:1;min-width:0}"
        "@media(min-width:768px){}"
        "@media(min-width:1100px){.wrap{margin-left:240px}}"
        ".wrap{max-width:820px;margin:0 auto}"
    )
    assert not _desktop_media_is_last(overwritten_css), \
        "顺序检测器对「桌面段后补基础规则」未报警（阴性对照 2 失败）"


# ── ⑤ 品牌区 ──────────────────────────────────────────────────

def test_sidebar_brand_block_present(template_text, built_html):
    """侧栏顶部品牌区：logo 标记 + 产品名，且位于导航之上；移动端隐藏。"""
    side = _sidebar_block(template_text)
    assert 'class="brand"' in side, "侧栏缺少品牌区"
    assert "brand-mark" in side and "brand-txt" in side, "品牌区缺少 logo/文字结构"
    assert "考研学习链" in side, "品牌区缺少产品名"
    assert side.index('class="brand"') < side.index('<nav class="bar"'), "品牌区必须在导航之上"
    assert '<use href="#i-cap"/>' in side, "品牌区 logo 未使用 sprite 图标"

    flat = _css(template_text)
    assert ".brand{display:none}" in flat, "品牌区未在移动端隐藏（底栏无空间）"
    assert ".brand{display:flex" in flat, "品牌区未在桌面侧栏显示"

    assert 'class="brand"' in built_html and "考研学习链" in built_html


# ── ⑥ 空状态三件套 ─────────────────────────────────────────────

def test_empty_today_state_is_three_piece(monkeypatch):
    """今日任务为空时必须渲染「图标 + 说明 + CTA」三件套，不得裸奔。

    用 ``get_section`` 打桩模拟「今日任务尚未生成」（today 章节 kw 为 None），
    并置非脱敏模式，避免走到「公开副本已脱敏」那条分支。
    """
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")
    original = build.get_section

    def fake_get_section(md, kw):
        if kw is None:                    # today 章节
            return None
        return original(md, kw)

    monkeypatch.setattr(build, "get_section", fake_get_section)
    html, _data, _warns, _secs = build.build(offline=True)

    assert "<div class='empty'>" in html, "今日任务空状态未使用三件套容器"
    # ① 图标（sprite 渲染成 <svg>，而不是 emoji 文本）
    assert "<div class='ei'><svg class='icn'" in html, "空状态图标未渲染为 sprite svg"
    assert "#i-clipboard" in html, "空状态图标未使用 sprite symbol"
    # ② 说明
    assert "<div class='empty-t'>今日任务尚未生成</div>" in html, "空状态缺少标题"
    assert "<div class='empty-d'>" in html, "空状态缺少说明文字"
    # ③ CTA（按钮 + 可展开的三步说明，且处理器已绑定）
    assert "class='cta'" in html and "data-help='today-help'" in html, "空状态缺少 CTA"
    assert "<div class='empty-help' id='today-help' hidden>" in html, "CTA 目标面板缺失"
    assert "t.closest('[data-help]')" in html, "CTA 的展开处理器未绑定"
    # 不得再出现「裸奔」的旧文案
    assert "今日任务已生成（内容保留在本地完整模式）" not in html


# ── ⑦ hero / KPI 卡（设计系统 token 消费） ─────────────────────

def test_hero_card_and_kpi_cards_use_tokens(template_text):
    """hero 倒计时卡 + 4 张 KPI 卡（7 日 sparkline + 上下文）落实设计系统 token。"""
    flat = _css(template_text)

    # hero：渐变底 + 环形进度 + --fs-hero 大数字 + elev-2
    hero = re.search(r"\.hero\{([^}]*)\}", flat)
    assert hero, "缺少 .hero 规则"
    assert "linear-gradient(" in hero.group(1), "hero 未使用渐变背景"
    assert "var(--elev-2)" in hero.group(1), "hero 未使用阴影 token"
    assert ".hero-num{font-size:var(--fs-hero)" in flat, "hero 大数字未使用 --fs-hero"
    assert 'pathLength="100"' in template_text, "环形进度未用 pathLength 归一（dasharray 无法按百分比算）"
    assert "stroke-dasharray" in template_text, "环形进度缺少 stroke-dasharray"
    assert 'id="ring-fill"' in template_text and "strokeDashoffset" in template_text, \
        "环形进度未由备考进度驱动"

    # KPI 卡：间距/阴影 token + sparkline + 上下文
    kpi = re.search(r"\.kpi\{([^}]*)\}", flat)
    assert kpi, "缺少 .kpi 规则"
    assert "var(--space-3)" in kpi.group(1) and "var(--elev-1)" in kpi.group(1), \
        "KPI 卡未使用间距/阴影 token"
    assert "polyline" in template_text, "KPI 卡缺少 sparkline polyline"
    assert "kpi-ctx" in template_text, "KPI 卡缺少「给数字配上下文」的说明行"
    assert "var(--chart-" in template_text, "KPI 卡未使用学科色板 token"
    for i in (1, 2, 3, 4):
        assert f"ci:{i}," in template_text, f"缺少第 {i} 张 KPI 卡定义"


def test_mobile_grid_degrades_to_single_column(template_text):
    """390px 不破：基础段必须是单列，12 栏网格只能在桌面媒体查询里出现。"""
    flat = _css(template_text)
    i_desktop = flat.index("@media(min-width:1100px)")
    base = flat[:i_desktop]

    assert "repeat(12,minmax(0,1fr))" not in base, "12 栏网格泄漏到移动端基础段"
    assert ".dash{display:grid;grid-template-columns:minmax(0,1fr)" in base, \
        "基础段首屏未降级为单列"
    # 移动端底栏逻辑保留（.sidebar 固定底部 + .bar 横向 flex）
    assert ".sidebar{position:fixed;left:0;right:0;bottom:0;z-index:40}" in flat, \
        "移动端底栏定位被破坏"


# ── ⑧ 字体栈如实（无 Inter）+ 自包含 ───────────────────────────

def test_font_stack_has_no_missing_inter_and_is_consumed(built_html):
    """字体栈不得再指向仓库不存在的 Inter；body 必须消费 var(--font-family)。"""
    assert '"Inter"' not in built_html, "产物仍引用无字体文件的 Inter"
    assert "font-family:var(--font-family" in built_html, \
        "body 未消费 var(--font-family)（token 改了页面也不会变）"
    # 三套非默认预设（eye-green / pink / hc）重声明的 --font-family 也必须已剥离
    for block in re.findall(r"--font-family:[^;}]*", built_html):
        assert '"Inter"' not in block, f"预设规则块仍含 Inter: {block[:60]}"


def test_product_is_self_contained(built_html):
    """离线自包含：产物不得引用任何外部 http(s) 资源。"""
    attrs = re.findall(r'(?:src|href)="([^"]+)"', built_html)
    external = [a for a in attrs if re.match(r"https?://", a)]
    assert external == [], f"产物引用了外部资源: {external}"
    for domain in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com",
                   "fonts.googleapis.com", "fonts.gstatic.com"):
        assert domain not in built_html, f"产物仍含外部域名 {domain}"


def test_committed_index_is_built_from_current_template(template_text):
    """已提交的 docs/index.html 必须与当前模板同源（防止产物陈旧）。"""
    if not COMMITTED_INDEX.exists():
        pytest.skip("docs/index.html 未构建")
    html = COMMITTED_INDEX.read_text(encoding="utf-8")
    assert '<symbol id="i-today"' in html, "已提交产物未内联 sprite（构建未同步）"
    assert html.count('<symbol id="i-today"') == 1, "已提交产物 sprite 重复注入"
    assert 'class="brand"' in html, "已提交产物缺少品牌区（构建未同步）"
    prod_css = _css(html)                 # 剥注释后再断言，避免误伤对照说明
    assert "max-width:820px" not in prod_css and "max-width:1080px" not in prod_css
    assert '"Inter"' not in html, "已提交产物仍引用 Inter"


# ── ①b 空状态图标容器不得是 emoji（P1 越界补齐：radar.py） ────────

#: 空状态图标容器：<div class='ei'> 后面跟的应当是 SVG（服务端渲染）或
#: JS 字符串拼接（"+iconHtml(...)），不能是 emoji 文本。
_EI_TAIL_RE = re.compile(r"<div class='ei'>(.{0,14})")


def test_empty_state_containers_use_sprite_not_emoji(built_html):
    """产物里每个 .ei 空态容器都必须是图标，不得是 emoji。

    [为什么单列一条] test_sidebar_has_no_emoji 只扫**模板**，而看板有
    一批空态是 Python 侧渲染的（05-考研看板/web/radar.py）—— 模板零 emoji
    也挡不住它们漏到产物里。本轮实测产物残留 1 个 📑 即出自 radar.py。

    阴性对照在末尾：检测器必须能抓到 <div class='ei'>📑</div>。
    """
    bad = []
    for m in _EI_TAIL_RE.finditer(built_html):
        tail = m.group(1)
        # 服务端渲染：<svg ...；JS 拼接："+iconHtml(...)  —— 两者都合规
        if tail.startswith("<") or tail.startswith('"'):
            continue
        bad.append(tail[:8])
    assert not bad, f"空状态图标容器仍是文本/emoji: {bad}"
    # 阴性对照：检测器自检
    probe = "<div class='ei'>📑</div>"
    tail = _EI_TAIL_RE.search(probe).group(1)
    assert not tail.startswith("<") and not tail.startswith('"'),         "空态容器检测器失效（阴性对照未命中）"


def test_radar_empty_states_use_sprite():
    """radar.py 三个空态必须用 sprite 引用（本次越界修复的直接锁定）。"""
    src = (DASHBOARD / "web" / "radar.py").read_text(encoding="utf-8")
    for m in _EI_TAIL_RE.finditer(src):
        tail = m.group(1)
        assert tail.startswith("<svg"), f"radar.py 空态容器未用 sprite: {tail[:20]!r}"
    assert src.count("class='ei'") == 3, "radar.py 空态数量变了，请同步复核本测试"
    for name in ("#i-radar", "#i-file", "#i-chat"):
        assert name in src, f"radar.py 空态缺少 sprite 引用 {name}"
