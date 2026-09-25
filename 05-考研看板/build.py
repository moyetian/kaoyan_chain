# -*- coding: utf-8 -*-
"""
考研学习链 · 移动端自测看板构建器

[职责] 只做编排：取数 → 渲染 → 落盘（本地 docs/ 与 Pages 发布目录）。
改造前本文件 2062 行，其中 1025 行是内联的 HTML 模板、build() 又达 193 行，
属典型「上帝文件」。现按职责物理分包到 web/：

    web/config.py     路径 / 考期 / 科目与板块映射
    web/markdown.py   轻量 Markdown 解析（章节、表格、正文 → HTML）
    web/cards.py      卡片与指标构建器
    web/radar.py      招考与考纲变动雷达 HTML 片段
    web/snapshot.py   脱敏与 state_snapshot.json 写出
    web/theme_vars.py 设计系统 token → CSS 变量（与 GUI/终端同源）
    web/vendor.py     第三方前端库版本/地址单一真源 + 公式降级脚本 + --offline
    web/template.html 页面模板（含 {{占位符}}）

用法：
    py 05-考研看板/build.py            常规构建（第三方资源走 CDN）
    py 05-考研看板/build.py --offline  第三方资源本地化到 docs/assets/vendor/
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent          # 05-考研看板/
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from web.cards import (  # noqa: E402
    build_cards,
    build_metrics,
    formula_prompt,
    plain,
    to_pct,
    to_target,
)
from web.config import (  # noqa: E402
    ENG,
    EXAM_DATE,
    EXAM_DAY1,
    INDEX_HEADERS,
    MATH,
    OUT,
    PLAN_START,
    POL,
    PRO,
    ROOT,
    ROOT_DOCS,
    SECTIONS,
    SUBJECTS,
)
from web.markdown import (  # noqa: E402
    esc,
    get_section,
    icon_html,
    inline,
    md2html,
    parse_tables,
    read,
    strip_tables,
)
from web.radar import build_radar_html, count_notes  # noqa: E402
from web.snapshot import (  # noqa: E402
    sanitize_public_data,
    snapshot_opt_in,
    write_state_snapshot,
)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考研四科学习看板生成器 v2
- 必背 / 薄弱：翻卡模式（表格 → 卡片对象）
- 数据：进度条模式（表格 → 指标对象）
用法：python build.py
"""

import re
import json
import html
import datetime
import pathlib
from pathlib import Path
import sys

# ════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════

# [根因修复·日期硬编码] 此前 EXAM_DATE=2026-12-20 / EXAM_DAY1=2026-12-19 /
# PLAN_START=2026-08-09 三个日期全部写死，与 ky_config.json 完全脱钩：学员改期后
# 看板的倒计时、备考第几天、总天数、日历进度条全部失真，且与 CLI/TUI/GUI 不一致。
# 现优先读取项目配置，缺失时按「12 月第 3 个周六」日历推算。


def build(offline: bool = False):
    """构建看板 HTML 与脱敏快照。

    :param offline: 为 True 时第三方前端资源改用本地 vendor 目录，
                    供无外网环境使用（配合 `--offline` 参数）。
    """
    today = datetime.date.today()
    d_math = (EXAM_DATE - today).days
    d_day1 = (EXAM_DAY1 - today).days
    day_no = (today - PLAN_START).days + 1
    total_days = (EXAM_DATE - PLAN_START).days

    decks = {"memo": [], "weak": []}
    metrics = []
    today_html = []
    notes_html = {"memo": [], "weak": []}
    subj_meta = []
    parse_warnings = []        # ← 新增：解析告警收集
    sections_status = []       # ← 新增：每个 section 解析状态（用于快照诊断）

    for s in SUBJECTS:
        ok = s["dir"].is_dir()
        subj_meta.append({
            "key": s["key"], "name": s["name"], "icon": icon_html(s["icon"]),
            "color": s["color"], "dark": s["dark"],
            "target": s["target"], "full": s["full"],
            "notes": count_notes(s) if ok else None, "ok": ok,
        })
        if not ok:
            for rel, kw, tab, ov in SECTIONS.get(s["key"], []):
                warn_msg = f"[{s['name']}] 目录不存在：{rel}"
                parse_warnings.append({"severity": "error", "subject": s["key"], "kw": kw, "msg": warn_msg})
                sections_status.append({"subject": s["key"], "kw": kw, "status": "missing_dir"})
            continue

        for rel, kw, tab, ov in SECTIONS.get(s["key"], []):
            md = read(s["dir"] / rel)
            if md is None:
                warn_msg = f"[{s['name']}] 源文件不存在或读取失败：{rel}（kw={kw!r}）"
                parse_warnings.append({"severity": "error", "subject": s["key"], "kw": kw, "msg": warn_msg})
                sections_status.append({"subject": s["key"], "kw": kw, "status": "file_missing", "path": rel})
                continue
            sec = get_section(md, kw)
            if not sec or len(sec.strip()) < 20:
                warn_msg = f"[{s['name']}] 未找到或过短的章节：{kw!r}（源: {rel}）。可能原因：标题改名、缺失、或仅有占位。卡片静默丢失。"
                parse_warnings.append({"severity": "warn", "subject": s["key"], "kw": kw, "msg": warn_msg})
                sections_status.append({"subject": s["key"], "kw": kw, "status": "section_missing", "path": rel})
                continue
            title = kw if kw else pathlib.Path(rel).stem
            title = re.sub(r"^\d+[-_.]?\s*", "", title)
            sections_status.append({"subject": s["key"], "kw": kw, "status": "ok", "path": rel, "tab": tab})

            if tab == "today":
                today_html.append((s, md2html(sec)))
                continue

            tables = parse_tables(sec)
            if tab == "stat":
                if not tables:
                    parse_warnings.append({"severity": "warn", "subject": s["key"], "kw": kw, "msg": f"[{s['name']}] 指标页签未解析出任何表格（kw={kw!r}）"})
                for head, rows in tables:
                    items = build_metrics(head, rows, ov)
                    if items:
                        metrics.append({
                            "subj": s["name"], "key": s["key"],
                            "color": s["color"], "dark": s["dark"], "icon": icon_html(s["icon"]),
                            "title": title, "items": items,
                        })
                    else:
                        parse_warnings.append({"severity": "warn", "subject": s["key"], "kw": kw, "msg": f"[{s['name']}] 指标表格未提取出有效数据（kw={kw!r}）"})
                continue

            cards = []
            for head, rows in tables:
                cards += build_cards(head, rows, ov, title)
            if cards:
                decks[tab].append({
                    "id": f"{s['key']}-{tab}-{len(decks[tab])}",
                    "subj": s["name"], "key": s["key"], "icon": s["icon"],
                    "color": s["color"], "dark": s["dark"],
                    "title": title, "cards": cards,
                })
            else:
                body = strip_tables(sec)
                if len(body) > 40:
                    notes_html[tab].append((s, title, md2html(body)))
                else:
                    parse_warnings.append({"severity": "warn", "subject": s["key"], "kw": kw, "msg": f"[{s['name']}] 章节存在但无可提取内容（kw={kw!r}）。可能：表格为空、或格式不被解析。"})

    # 把告警打到 stdout，CI 也能直接看到
    if parse_warnings:
        print(f"\n[⚠️ 解析告警 {len(parse_warnings)} 条]")
        for w in parse_warnings:
            prefix = "[ERROR]" if w["severity"] == "error" else "[WARN] "
            print(f"  {prefix} {w['msg']}")
        print()

    # 今日
    if today_html:
        th = []
        for s, body in today_html:
            th.append(
                f"<section class='blk' style='--c:{s['color']};--cd:{s['dark']}'>"
                f"<div class='blk-h'><span class='ic'>{icon_html(s['icon'])}</span>{esc(s['name'])}</div>"
                f"<div class='blk-b'>{body}</div></section>"
            )
        today_out = "".join(th)
    else:
        # [P1 空状态三件套] 改造前只有一句「今日任务尚未生成」+ 一行小字，
        # 属 DESIGN.md §6.4 明令禁止的「裸奔空状态」。现按规范补齐：
        # ① 图标（sprite，icon-lg，mut 色）② 一句话说明这里会有什么
        # ③ 一个 CTA（可展开的三步操作说明，纯前端，无外部依赖）。
        today_out = (
            "<div class='empty'>"
            f"<div class='ei'>{sprite_icon('clipboard', 24)}</div>"
            "<div class='empty-t'>今日任务尚未生成</div>"
            "<div class='empty-d'>这里会显示四科今日任务清单与勾选状态</div>"
            "<button class='cta' type='button' data-help='today-help'"
            " aria-expanded='false' aria-controls='today-help'>如何生成今日任务？</button>"
            "<div class='empty-help' id='today-help' hidden>"
            "<b>三步生成今日任务</b>"
            "<ol><li>在 Agent 会话里发「英语报到」这类口令；</li>"
            "<li>私教按前一日错题与今日规划派题，并写入各科 "
            "<code>_状态/今日任务.md</code>；</li>"
            "<li>回到本页刷新，任务清单会出现在这里。</li></ol>"
            "</div></div>"
        )

    # Public builds must not embed the private daily task prose. The structured
    # cards/metrics remain available in the sanitized payload above.
    if snapshot_opt_in():
        today_out = (
            "<div class='empty'>"
            f"<div class='ei'>{sprite_icon('check-circle', 24)}</div>"
            "<div class='empty-t'>今日任务已生成</div>"
            "<div class='empty-d'>公开副本不含任务正文，本地完整模式可见</div>"
            "<button class='cta' type='button' data-help='today-help'"
            " aria-expanded='false' aria-controls='today-help'>为什么看不到内容？</button>"
            "<div class='empty-help' id='today-help' hidden>"
            "<b>公开副本的脱敏规则</b>"
            "<ol><li>任务正文属私人学习记录，发布时按脱敏规则移除；</li>"
            "<li>结构化卡片、指标与倒计时仍照常展示；</li>"
            "<li>本地完整模式（构建时置 <code>KY_SNAPSHOT_OPT_IN=0</code>）可见正文。</li>"
            "</ol></div></div>"
        )

    def notes_out(tab):
        if snapshot_opt_in():
            return ""
        if not notes_html[tab]:
            return ""
        h = ["<details class='extra'><summary>补充说明（非卡片内容）</summary>"]
        for s, title, body in notes_html[tab]:
            h.append(
                f"<section class='blk' style='--c:{s['color']};--cd:{s['dark']}'>"
                f"<div class='blk-h'><span class='ic'>{icon_html(s['icon'])}</span>{esc(s['name'])}"
                f"<span class='sep'>·</span>{esc(title)}</div>"
                f"<div class='blk-b'>{body}</div></section>"
            )
        h.append("</details>")
        return "".join(h)

    # 知识图谱挂载 (S3-4)
    k_maps = {}
    try:
        if str(ROOT.parent) not in sys.path:
            sys.path.insert(0, str(ROOT.parent))
        from tools.skills import knowledge_map
        # [P5 残留修复·图谱数据] 只为本考生实际报考的科目构建图谱，不再无条件
        # 塞入 maps.math。SUBJECTS 已由 web/config.py 按同一口径（_math_none：
        # math_key=none / math_name=不考数学 / mode_b 双专业课 / mode_c 管综）
        # 过滤掉数学，这里直接复用，避免另写一套判定再次漂移。
        # 再与 knowledge_map 支持的科目键取交集：它仅支持 math/eng/pol/pro，
        # 传入未知键（如 mode_b 的 pro2）会回退到 01-数学 目录产出错误图谱。
        present = {s["key"] for s in SUBJECTS}
        for sk in ("math", "eng", "pol", "pro"):
            if sk in present:
                k_maps[sk] = knowledge_map.build_knowledge_map(sk)
    except Exception as e:
        parse_warnings.append({"severity": "warn", "subject": "all", "kw": "knowledge_map", "msg": f"知识图谱构建失败: {e}"})
        k_maps = {}

    # 历史趋势挂载 (S3-4)
    trend_history = []
    cfg_file = ROOT.parent / "ky_config.json"
    if cfg_file.exists():
        try:
            cfg_obj = json.loads(cfg_file.read_text(encoding="utf-8"))
            ch = cfg_obj.get("completion_history", {})
            for d in sorted(ch.keys())[-7:]:
                trend_history.append({
                    "date": d,
                    "short_date": d[-5:],
                    "rate": float(ch[d].get("rate", 0.0)),
                    "total": int(ch[d].get("total", 0)),
                    "completed": int(ch[d].get("completed", 0))
                })
        except Exception:
            trend_history = []

    # 考情与考纲变动雷达 (S7)
    radar_out = build_radar_html(ROOT.parent)

    data = {
        "memo": decks["memo"],
        "weak": decks["weak"],
        "metrics": metrics,
        "subjects": subj_meta,
        "plan": {"day": day_no, "total": total_days},
        "maps": k_maps,
        "trend": trend_history,
    }
    html_data = sanitize_public_data(data) if snapshot_opt_in() else data
    payload = json.dumps(html_data, ensure_ascii=False).replace("</", "<\\/")

    # [P2-2 修复·除零] 起跑日 ≥ 初试日（ky_config.json 自相矛盾）时 total_days<=0，
    # 原先 `day_no / total_days` 会抛 ZeroDivisionError 中断整个看板构建
    # （update_dashboard / ky build 直接失败）。此时进度无意义，clamp 为 0.0。
    plan_pct = f"{day_no / total_days * 100:.1f}" if total_days > 0 else "0.0"

    # [P2-1 修复·占位符二次替换] 此前是「先注入 today/notes/radar 正文，最后再对整串
    # .replace("{{DATA}}", payload)」——用户笔记正文里若出现字面量 {{DATA}}，它会被当成
    # 模板占位符，把整份 JSON payload 塞进正文（实测复现）。现改为**单遍替换**：
    # 用 re.sub + 一次性映射表，任何被注入的内容都不会再被当作模板扫描。
    # [前端修复·考期硬编码] 页头「2026 研考倒计时」「初试首日 · 12-19」此前写死
    # 年份与月日：学员在 ky_config.json 改期后，倒计时数字变了、这两处文案不变，
    # 同一页面出现两个互相矛盾的考期。改由 EXAM_DAY1（考期真源，模块级全局，
    # 便于测试 monkeypatch）派生。两个占位符必须同时进 values，否则单遍 re.sub
    # 会留下未替换的字面量。
    values = {
        "DMATH": str(d_math),
        "DDAY1": str(d_day1),
        "EXAM_YEAR": str(EXAM_DAY1.year),
        "EXAM_MMDD": EXAM_DAY1.strftime("%m-%d"),
        "DAYNO": str(day_no),
        "TOTALDAYS": str(total_days),
        "PLANPCT": plan_pct,
        "TODAY": today_out,
        "MEMONOTES": notes_out("memo"),
        "WEAKNOTES": notes_out("weak"),
        "RADAR": radar_out,
        "DATA": payload,
        "STAMP": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    # 主题变量/降级脚本/第三方资源地址与数据占位符并入同一次替换（键统一为 {{NAME}}）。
    values.update({k.strip("{}"): v
                   for k, v in render_theme_placeholders(offline=offline,
                                                         days_left=d_day1).items()})

    html = load_template()
    html = re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda m: values.get(m.group(1), m.group(0)), html)
    return html, data, parse_warnings, sections_status

_DIR = pathlib.Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

#: 图标 sprite 相对仓库根的位置（P0 由 tools/theme/icons.py 抽取产出）
_SPRITE_CANDIDATES = (
    ROOT.parent / "docs" / "assets" / "icons.svg",
    ROOT / "docs" / "assets" / "icons.svg",
)


def sprite_icon(name: str, size: int = 24) -> str:
    """构建期可用的 sprite 图标（symbol 定义由 ``{{ICON_SPRITE}}`` 注入本页）。

    ``<use href="#i-xxx">`` 只引用**同文档内**的 symbol，故必须与 sprite 同页；
    这也是 sprite 必须内联、不能用 ``assets/icons.svg#...`` 外链的原因
    （``file://`` 下跨文件引用会被同源策略拒绝）。
    """
    return (f"<svg class='icn' width='{size}' height='{size}' aria-hidden='true' "
            f"focusable='false'><use href='#i-{name}'/></svg>")


def load_icon_sprite() -> str:
    """读取 Lucide 子集 sprite 全文，注入 ``{{ICON_SPRITE}}``。

    取不到时返回空串并告警（构建不因此中断）：此时图标不可见但页面结构仍完整，
    且 ``tests/test_dashboard_redesign.py`` 会断言产物里存在 ``<symbol id="i-today"``，
    把这种「静默隐形」挡在 CI 里。
    """
    for cand in _SPRITE_CANDIDATES:
        try:
            if cand.exists():
                return cand.read_text(encoding="utf-8")
        except OSError as e:  # pragma: no cover - 仅在磁盘异常时触发
            print(f"[!] 图标 sprite 读取失败 {cand}: {e}")
    print(f"[!] 未找到图标 sprite（已尝试 {len(_SPRITE_CANDIDATES)} 个路径）："
          "看板图标将不可见，请先运行 py tools/theme/icons.py")
    return ""


def load_template() -> str:
    """读取 HTML 模板（内含 {{占位符}}）。

    [拆分] 模板原本是内联在本文件里的 1025 行巨型字符串（占全文件一半），
    现落到 web/template.html：编辑器能正确高亮 HTML/CSS/JS，
    改看板布局也不必在 Python 文件里上下翻找。
    """
    return (_DIR / "web" / "template.html").read_text(encoding="utf-8")


def render_theme_placeholders(offline: bool = False, days_left: int = 0) -> dict:
    """模板占位符 → 取值（主题变量 / 公式降级脚本 / 第三方资源地址）。

    第三方资源地址与版本由 web/vendor.py 统一提供，`offline=True` 时
    指向本地 vendor 目录，使看板在无外网时公式仍能渲染。
    """
    from web import (asset_map, build_theme_css, load_fallback_math_js,
                     theme_presets_json, theme_rhythm_json)

    mapping = {
        "{{THEME_CSS}}": build_theme_css(ROOT),
        "{{FALLBACK_MATH_JS}}": load_fallback_math_js(),
        "{{THEME_PRESETS}}": theme_presets_json(),
        "{{RHYTHM}}": theme_rhythm_json(days_left),
        # [P1 图标系统] sprite 全文注入：模板里用 <use href="#i-today"> 消费。
        # build() 是单遍 re.sub，注入内容不会被再次扫描，故 sprite 里的任何
        # 字符（含 { }）都不会被误当占位符。
        "{{ICON_SPRITE}}": load_icon_sprite(),
    }
    mapping.update(asset_map(offline))
    return mapping

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # [C7 修复·默认离线] 默认改用本地 vendor 资源：手机看板的主用场景是
    # 地铁/破网/自习室，走 CDN 时公式会退化成 LaTeX 源码串。
    # 需要走 CDN 时显式 `--cdn` 或设 KY_VENDOR_MODE=cdn。
    from web import vendor_mode
    _mode = vendor_mode()
    offline = _mode == "local"
    if "--cdn" in sys.argv:
        offline = False
        _mode = "cdn"
    if offline:
        print("[i] 默认离线：第三方前端资源使用 docs/assets/vendor/ 本地副本"
              "（如需 CDN 请加 --cdn）")
    else:
        print("[i] 已切换到 CDN 模式（KY_VENDOR_MODE=cdn / --cdn）")

    content, data_obj, parse_warnings, sections_status = build(offline=offline)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = OUT.with_suffix(".tmp")
    tmp_out.write_text(content, encoding="utf-8")
    tmp_out.replace(OUT)
    print(f"[OK] generated: {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")
    if ROOT_DOCS.parent.parent == ROOT.parent and (ROOT.parent / "01-数学").exists():
        ROOT_DOCS.parent.mkdir(parents=True, exist_ok=True)
        tmp_root_docs = ROOT_DOCS.with_suffix(".tmp")
        tmp_root_docs.write_text(content, encoding="utf-8")
        tmp_root_docs.replace(ROOT_DOCS)
        print(f"[OK] synced to root docs: {ROOT_DOCS}")

    # ── 新增：生成 state_snapshot.json（Pages 部署的真相源）──
    snapshot_path = OUT.parent / "state_snapshot.json"
    try:
        write_state_snapshot(data_obj, snapshot_path,
                               parse_warnings=parse_warnings,
                               sections_status=sections_status)
        print(f"[OK] state snapshot: {snapshot_path}  ({snapshot_path.stat().st_size/1024:.1f} KB)")
        # 同步到根 docs/（与 index.html 同样的同步策略）
        if ROOT_DOCS.parent.parent == ROOT.parent and (ROOT.parent / "01-数学").exists():
            ROOT_DOCS_SNAPSHOT = ROOT_DOCS.parent / "state_snapshot.json"
            ROOT_DOCS_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            ROOT_DOCS_SNAPSHOT.write_text(snapshot_path.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[OK] synced snapshot to root docs: {ROOT_DOCS_SNAPSHOT}")
    except Exception as e:
        print(f"[!] state snapshot 写入失败: {e}")
        raise

    if offline:
        from web import download_vendor_assets
        for target_docs in (OUT.parent, ROOT_DOCS.parent):
            saved = download_vendor_assets(target_docs)
            if saved:
                print(f"[OK] vendor 资源已本地化 {len(saved)} 个文件 -> {target_docs}")
