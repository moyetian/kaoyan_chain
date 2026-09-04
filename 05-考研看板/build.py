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
import sys

# ════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════

EXAM_DATE = datetime.date(2026, 12, 20)
EXAM_DAY1 = datetime.date(2026, 12, 19)
PLAN_START = datetime.date(2026, 8, 9)

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "docs" / "index.html"
ROOT_DOCS = ROOT.parent / "docs" / "index.html"

def resolve_dir(rel_name, default_path):
    candidates = [
        ROOT.parent / rel_name,
        ROOT / rel_name,
        pathlib.Path(default_path),
    ]
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    return pathlib.Path(default_path)

MATH = resolve_dir("01-数学", r"01-数学")
ENG = resolve_dir("02-英语", r"02-英语")
POL = resolve_dir("03-思想政治理论", r"03-思想政治理论")
PRO = resolve_dir("04-专业课", r"04-专业课")

SUBJECTS = [
    {"key": "math", "name": "数学", "icon": "∫", "color": "#2563eb", "dark": "#60a5fa",
     "dir": MATH, "full": 150, "target": 110, "notes": "每日笔记"},
    {"key": "eng", "name": "英语", "icon": "En", "color": "#e11d48", "dark": "#fb7185",
     "dir": ENG, "full": 100, "target": 60, "notes": "每日笔记"},
    {"key": "pol", "name": "政治", "icon": "政", "color": "#d97706", "dark": "#fbbf24",
     "dir": POL, "full": 100, "target": 70, "notes": None},
    {"key": "pro", "name": "专业课", "icon": "★", "color": "#059669", "dark": "#34d399",
     "dir": PRO, "full": 150, "target": 120, "notes": "每日作业"},
]

# (相对路径, 标题关键词, 页签, 可选覆盖)
#   tab: today / memo / weak / stat
#   front: 指定卡片正面用第几列（0 起）；不给则自动判断
#   mode: "formula" → 正面取去掉 LaTeX 后的描述文字
SECTIONS = {
    "math": [
        ("_状态/今日任务.md", None, "today", {}),
        ("_状态/薄弱点雷达.md", "公式默写卡", "memo", {"mode": "formula", "front": 1}),
        ("_状态/薄弱点雷达.md", "复发错误", "memo", {"front": 1}),
        ("_状态/薄弱点雷达.md", "模块掌握度雷达", "weak", {"front": 0}),
        ("错题本/_索引.md", "索引表", "weak", {"front": 3}),
        ("_状态/薄弱点雷达.md", "错因五分类", "stat", {"label": 1, "value": 4}),
        ("_状态/薄弱点雷达.md", "计算失误", "stat", {"label": 0, "value": 2}),
    ],
    "eng": [
        ("_状态/今日任务.md", None, "today", {}),
        ("_状态/薄弱点雷达.md", "长难句", "memo", {"front": 0}),
        ("_状态/薄弱点雷达.md", "题型能力评估", "weak", {"front": 0}),
        ("_状态/薄弱点雷达.md", "题型能力评估", "stat", {"label": 0, "value": 2, "target": 4}),
        ("_状态/薄弱点雷达.md", "错因累计", "stat", {}),
    ],
    "pol": [
        ("_状态/今日任务.md", None, "today", {}),
        ("_状态/核心速记_帽子词与历史节点.md", "马原", "memo", {"front": 0}),
        ("_状态/核心速记_帽子词与历史节点.md", "毛中特", "memo", {"front": 0}),
        ("_状态/核心速记_帽子词与历史节点.md", "新思想", "memo", {"front": 0}),
        ("_状态/核心速记_帽子词与历史节点.md", "史纲", "memo", {"front": 0}),
        ("_状态/薄弱点雷达.md", "复发易混点", "memo", {}),
        ("_状态/薄弱点雷达.md", "模块雷达", "weak", {"front": 0}),
        ("_状态/薄弱点雷达.md", "分析题能力", "weak", {"front": 0}),
        ("_状态/薄弱点雷达.md", "下周优先级", "weak", {}),
        ("_状态/薄弱点雷达.md", "总体指标", "stat", {"label": 0, "value": 1, "target": 3}),
        ("_状态/薄弱点雷达.md", "七类错因", "stat", {"label": 1, "value": 4}),
    ],
    "pro": [
        ("_状态/今日任务.md", None, "today", {}),
        ("02_核心公式与考点速查模板.md", "核心概念", "memo", {"front": 0}),
        ("学情档案.md", "章节掌握度", "weak", {"front": 1}),
        ("学情档案.md", "掌握度", "weak", {"front": 1}),
        ("学情档案.md", "错题重做队列", "weak", {"front": 1}),
        ("学情档案.md", "错因", "stat", {"label": 0, "value": 1}),
    ],
}

INDEX_HEADERS = {"#", "编号", "排名", "序号", "代码", "类", "no", "id"}


# ════════════════════════════════════════════════════════════
# 读取与切片
# ════════════════════════════════════════════════════════════

def read(p):
    path_obj = pathlib.Path(p)
    if not path_obj.exists() and path_obj.suffix == ".md":
        for ext in (".template.md", ".example.md"):
            cand = path_obj.with_name(path_obj.stem + ext)
            if cand.exists():
                path_obj = cand
                break
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return path_obj.read_text(encoding=enc)
        except Exception:
            continue
    return None


def get_section(md, kw):
    if md is None:
        return None
    if kw is None:
        return md
    lines = md.splitlines()
    start = lvl = None
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{2,4})\s+(.*)$", ln)
        if m and kw in m.group(2):
            start, lvl = i, len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,4})\s+", lines[j])
        if m and len(m.group(1)) <= lvl:
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def split_row(line):
    """按 | 切分表格行，但保护 $...$ 内的竖线（线代公式里 |A| 极常见）"""
    s = line.strip().strip("|")
    keep = []

    def _k(m):
        keep.append(m.group(0))
        return f"\x01{len(keep)-1}\x01"

    s = re.sub(r"\$[^$\n]*\$", _k, s)
    cells = []
    for c in s.split("|"):
        c = c.strip()
        for i, k in enumerate(keep):
            c = c.replace(f"\x01{i}\x01", k)
        cells.append(c)
    return cells


def parse_tables(md):
    """返回 [(headers, rows)]"""
    out, lines = [], md.splitlines()
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if ln.strip().startswith("|") and i + 1 < n and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            head = split_row(ln)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                cells = split_row(lines[i])
                if any(c for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append((head, rows))
            continue
        i += 1
    return out


def strip_tables(md):
    """去掉表格后剩下的正文"""
    keep, lines, i, n = [], md.splitlines(), 0, len(md.splitlines())
    while i < n:
        ln = lines[i]
        if ln.strip().startswith("|") and i + 1 < n and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                i += 1
            continue
        keep.append(ln)
        i += 1
    return "\n".join(keep).strip()


# ════════════════════════════════════════════════════════════
# 数值抽取
# ════════════════════════════════════════════════════════════

STARS = {"★": 20, "☆": 0, "⭐": 20}


def to_pct(text):
    """从单元格里抽百分比 → (值, 展示文本) 或 None"""
    if not text:
        return None
    t = text.strip()
    if t in ("-", "—", "未测", "待评估", ""):
        return None

    # 星级 ★★★☆☆
    if re.fullmatch(r"[★☆⭐]{3,7}", t):
        return (sum(STARS.get(c, 0) for c in t), t)

    # 显式百分比
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return (min(float(m.group(1)), 100.0), t)

    # 分数 a/b
    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", t)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if b > 0:
            return (min(a / b * 100, 100.0), t)

    # 纯数字（次数类）—— 不转百分比，交给调用方
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", t)
    if m:
        return (None, t)
    return None


def to_target(text):
    """目标列 → (百分比, 方向) 方向 lower 表示越小越好"""
    if not text:
        return None
    t = text.strip()
    lower = bool(re.search(r"(<=|≤|<|不超过|以下)", t))
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return (float(m.group(1)), "lower" if lower else "higher")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-–~]\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", t)
    if m:
        return (float(m.group(1)) / float(m.group(3)) * 100, "higher")
    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", t)
    if m and float(m.group(2)) > 0:
        return (float(m.group(1)) / float(m.group(2)) * 100, "lower" if lower else "higher")
    return None


# ════════════════════════════════════════════════════════════
# 卡片 / 指标构建
# ════════════════════════════════════════════════════════════

def pick_front(head, rows, override):
    if "front" in override and override["front"] < len(head):
        return override["front"]
    for i, h in enumerate(head):
        if h.strip().lower() in INDEX_HEADERS:
            continue
        vals = [r[i] for r in rows if i < len(r)]
        if vals and sum(len(v) for v in vals) / max(len(vals), 1) >= 3:
            return i
    return 0


def clean_prompt(text):
    """去掉 LaTeX / 括号补充，留下可作提问的描述"""
    s = re.sub(r"\$[^$]*\$", " ", text)
    s = re.sub(r"[（(][^）)]*[）)]", " ", s)
    s = re.sub(r"\*\*|`|\[\[|\]\]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ·—-：:，,。、")
    return s


def _demath(t):
    """
    去掉 LaTeX，但保留极短的符号（$n$ → n），
    这样「$n$ 阶方阵…」不会退化成「阶方阵…」。
    """
    def rep(m):
        inner = m.group(1).strip()
        if len(inner) <= 3 and re.fullmatch(r"[A-Za-z0-9^_{}\\]+", inner):
            return re.sub(r"[\\^_{}]", "", inner)
        return " "
    return re.sub(r"\$([^$\n]*)\$", rep, t)


def formula_prompt(text):
    """
    公式卡正面要出「提示」而非答案。必须先去 LaTeX 再找括号，
    否则 o(x^3) 里的 ASCII 括号会被误当成说明。按优先级取：
      ① （ 之前的引导文字     伴随矩阵四大公式（…） → 伴随矩阵四大公式
      ② 括号内的说明          $\\sin x=…$（六个基本泰勒展开） → 六个基本泰勒展开
      ③ 去掉公式后的残余文字
    """
    bare = _demath(text.strip())

    lead = re.split(r"[（(]", bare, 1)[0]
    lead = re.sub(r"\*\*|`", "", lead).strip(" ·—-：:，,。、")
    if len(lead) >= 3:
        return lead

    for m in re.finditer(r"[（(]([^）)]*)[）)]", bare):
        inner = re.sub(r"\s+", " ", m.group(1)).strip(" ·—-：:，,。、")
        if len(inner) >= 3:
            return inner

    rest = re.sub(r"[（()）]", " ", bare)
    rest = re.sub(r"\s+", " ", rest).strip(" ·—-：:，,。、")
    if len(rest) >= 3:
        return rest
    return ""


def plain(text):
    """去掉 markdown 强调标记，保留 $...$ 给 KaTeX 渲染"""
    s = re.sub(r"\*\*|__|`|\[\[|\]\]", "", text)
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"[；;、，,\s]{2,}(?=[；;、，,])", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ·—-：:，,。、；;")


def build_cards(head, rows, override, fallback_title):
    fi = pick_front(head, rows, override)
    mode = override.get("mode")
    cards = []
    for r in rows:
        r = r + [""] * (len(head) - len(r))
        raw = r[fi] if fi < len(r) else ""
        if not raw.strip():
            continue

        if mode == "formula":
            p = plain(formula_prompt(raw))
            if len(p) < 2:
                idx = r[0].strip() if r and r[0].strip() else fallback_title
                p = f"默写：{plain(idx)}"
            front, back = p, [(head[fi], plain(raw))]
        else:
            front, back = plain(raw), []

        for j, h in enumerate(head):
            if j == fi:
                continue
            v = plain(r[j])
            if not v or v in ("-", "—"):
                continue
            if h.strip().lower() in INDEX_HEADERS and len(v) <= 4:
                continue
            back.append((plain(h), v))
        if not back:
            continue          # 背面全空（未填写的模板行）→ 不生成卡片
        cards.append({"f": front, "b": back})
    return cards


def build_metrics(head, rows, override):
    li = override.get("label")
    vi = override.get("value")
    ti = override.get("target")

    if li is None:
        li = pick_front(head, rows, {})
    if vi is None:
        for j, h in enumerate(head):
            if any(k in h for k in ("正确率", "占比", "当前", "得分", "掌握", "累计", "熟练")):
                vi = j
                break
    if ti is None:
        for j, h in enumerate(head):
            if any(k in h for k in ("目标", "达标", "标准")):
                ti = j
                break
    if vi is None:
        return []

    items = []
    for r in rows:
        r = r + [""] * (len(head) - len(r))
        label = r[li].strip() if li < len(r) else ""
        if not label:
            continue
        pv = to_pct(r[vi]) if vi < len(r) else None
        if pv is None:
            continue
        pct, txt = pv
        it = {"label": clean_prompt(label)[:40] or label[:40], "text": txt}
        if pct is None:
            it["count"] = float(re.sub(r"[^\d.]", "", txt) or 0)
        else:
            it["pct"] = round(pct, 1)
        if ti is not None and ti < len(r):
            tg = to_target(r[ti])
            if tg:
                it["target"] = round(tg[0], 1)
                it["dir"] = tg[1]
        items.append(it)
    return items


# ════════════════════════════════════════════════════════════
# 极简 markdown → html（今日页签与补充说明用）
# ════════════════════════════════════════════════════════════

def esc(s):
    return html.escape(s, quote=False)


def inline(s):
    s = esc(s)
    keep = []

    def _k(m):
        keep.append(m.group(0))
        return f"\x00{len(keep)-1}\x00"

    s = re.sub(r"\$[^$\n]+\$", _k, s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    for i, m in enumerate(keep):
        s = s.replace(f"\x00{i}\x00", m)
    return s


def md2html(md):
    if not md:
        return ""
    out, lines, i, n = [], md.splitlines(), 0, len(md.splitlines())
    while i < n:
        ln = lines[i]
        if ln.strip().startswith("|") and i + 1 < n and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ["<div class='tw'><table><thead><tr>"]
            t += [f"<th>{inline(h)}</th>" for h in head]
            t.append("</tr></thead><tbody>")
            for r in rows:
                if not any(r):
                    continue
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            out.append(f"<h{min(len(m.group(1))+2,6)}>{inline(m.group(2))}</h{min(len(m.group(1))+2,6)}>")
            i += 1
            continue
        if re.match(r"^\s*(---|\*\*\*)\s*$", ln):
            out.append("<hr>")
            i += 1
            continue
        if ln.strip().startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + "<br>".join(inline(b) for b in buf if b) + "</blockquote>")
            continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", ln):
            tag = "ol" if re.match(r"^\s*\d+\.", ln) else "ul"
            buf = []
            while i < n and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
                buf.append(re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(b)}</li>" for b in buf) + f"</{tag}>")
            continue
        if ln.strip().startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre>" + esc("\n".join(buf)) + "</pre>")
            continue
        if not ln.strip():
            i += 1
            continue
        buf = []
        while i < n and lines[i].strip() and not re.match(r"^(#{1,6}\s|\s*\||\s*>|\s*([-*+]|\d+\.)\s|```)", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append(f"<p>{inline(' '.join(buf))}</p>")
    return "\n".join(out)


# ════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════

def count_notes(s):
    if not s["notes"]:
        return None
    d = s["dir"] / s["notes"]
    if not d.is_dir():
        return None
    return sum(1 for f in d.iterdir() if f.is_file() and f.suffix == ".md" and not f.name.startswith("_"))


def build():
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
            "key": s["key"], "name": s["name"], "icon": s["icon"],
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
                            "color": s["color"], "dark": s["dark"], "icon": s["icon"],
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
                f"<div class='blk-h'><span class='ic'>{esc(s['icon'])}</span>{esc(s['name'])}</div>"
                f"<div class='blk-b'>{body}</div></section>"
            )
        today_out = "".join(th)
    else:
        today_out = "<div class='empty'><div class='ei'>📋</div>今日任务尚未生成<br><small>去 Antigravity 发「报道」</small></div>"

    def notes_out(tab):
        if not notes_html[tab]:
            return ""
        h = ["<details class='extra'><summary>📄 补充说明（非卡片内容）</summary>"]
        for s, title, body in notes_html[tab]:
            h.append(
                f"<section class='blk' style='--c:{s['color']};--cd:{s['dark']}'>"
                f"<div class='blk-h'><span class='ic'>{esc(s['icon'])}</span>{esc(s['name'])}"
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
        for sk in ("math", "eng", "pol", "pro"):
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

    data = {
        "memo": decks["memo"],
        "weak": decks["weak"],
        "metrics": metrics,
        "subjects": subj_meta,
        "plan": {"day": day_no, "total": total_days},
        "maps": k_maps,
        "trend": trend_history,
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    return (HTML
            .replace("{{DMATH}}", str(d_math))
            .replace("{{DDAY1}}", str(d_day1))
            .replace("{{DAYNO}}", str(day_no))
            .replace("{{TOTALDAYS}}", str(total_days))
            .replace("{{PLANPCT}}", f"{day_no / total_days * 100:.1f}")
            .replace("{{TODAY}}", today_out)
            .replace("{{MEMONOTES}}", notes_out("memo"))
            .replace("{{WEAKNOTES}}", notes_out("weak"))
            .replace("{{DATA}}", payload)
            .replace("{{STAMP}}", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))), data, parse_warnings, sections_status


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1">
<meta name="theme-color" content="#f8fafc" media="(prefers-color-scheme:light)">
<meta name="theme-color" content="#090d16" media="(prefers-color-scheme:dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>考研学习看板 · 倒计时 {{DDAY1}} 天</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<style>
:root{
  --bg:#f8fafc; --surf:#ffffff; --surf2:#f1f5f9; --surf3:#e2e8f0; --fg:#0f172a; --mut:#64748b;
  --line:#e2e8f0; --acc:#4f46e5; --acc-sub:#e0e7ff; --ok:#10b981; --warn:#f59e0b; --bad:#ef4444;
  --radius:16px;
  --sh:0 1px 3px rgba(0,0,0,.04),0 4px 12px rgba(0,0,0,.03);
  --sh2:0 4px 20px -2px rgba(0,0,0,.06),0 2px 6px -1px rgba(0,0,0,.03);
}
@media(prefers-color-scheme:dark){:root:not([data-t=light]){
  --bg:#090d16; --surf:#111827; --surf2:#1e293b; --surf3:#334155; --fg:#f8fafc; --mut:#94a3b8;
  --line:#1e293b; --acc:#818cf8; --acc-sub:#1e1e38; --ok:#34d399; --warn:#fbbf24; --bad:#f87171;
  --sh:0 1px 3px rgba(0,0,0,.3);
  --sh2:0 8px 24px rgba(0,0,0,.4);
}}
:root[data-t=dark]{
  --bg:#090d16; --surf:#111827; --surf2:#1e293b; --surf3:#334155; --fg:#f8fafc; --mut:#94a3b8;
  --line:#1e293b; --acc:#818cf8; --acc-sub:#1e1e38; --ok:#34d399; --warn:#fbbf24; --bad:#f87171;
  --sh:0 1px 3px rgba(0,0,0,.3);
  --sh2:0 8px 24px rgba(0,0,0,.4);
}
:root[data-t=light]{
  --bg:#f8fafc; --surf:#ffffff; --surf2:#f1f5f9; --surf3:#e2e8f0; --fg:#0f172a; --mut:#64748b;
  --line:#e2e8f0; --acc:#4f46e5; --acc-sub:#e0e7ff; --ok:#10b981; --warn:#f59e0b; --bad:#ef4444;
  --sh:0 1px 3px rgba(0,0,0,.04),0 4px 12px rgba(0,0,0,.03);
  --sh2:0 4px 20px -2px rgba(0,0,0,.06),0 2px 6px -1px rgba(0,0,0,.03);
}

*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{overscroll-behavior-y:none}
body{margin:0;background:var(--bg);color:var(--fg);
 font:14.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
 -webkit-font-smoothing:antialiased;padding-bottom:calc(76px + env(safe-area-inset-bottom));transition:background .2s,color .2s}
.wrap{max-width:820px;margin:0 auto;padding:0 18px}

/* ── 顶部 ── */
header{padding:22px 0 6px}
.hd{display:flex;align-items:flex-end;justify-content:space-between;gap:12px}
.hl{line-height:1}
.hl .lb{font-size:11.5px;letter-spacing:.12em;color:var(--mut);text-transform:uppercase;margin-bottom:7px;display:flex;align-items:center;gap:6px}
.hl .lb::before{content:"";width:7px;height:7px;border-radius:99px;background:var(--ok);display:inline-block}
.hl .big{font-size:56px;font-weight:850;letter-spacing:-.04em;font-variant-numeric:tabular-nums;color:var(--fg)}
.hl .big i{font-size:20px;font-weight:600;color:var(--mut);font-style:normal;margin-left:4px}
.hr2{display:flex;flex-direction:column;align-items:flex-end;gap:6px}
.tbtn-theme{background:var(--surf2);border:1px solid var(--line);border-radius:99px;padding:6px 12px;font-size:12px;color:var(--fg);cursor:pointer;display:flex;align-items:center;gap:5px;box-shadow:var(--sh);transition:.15s}
.tbtn-theme:hover{background:var(--surf)}
.hr-info{font-size:11.5px;color:var(--mut);text-align:right;line-height:1.6}
.hr-info b{color:var(--fg);font-weight:700}

.plan{margin:14px 0 4px}
.plan .pt{display:flex;justify-content:space-between;font-size:11px;color:var(--mut);margin-bottom:6px;font-weight:600}
.ptrack{height:7px;background:var(--surf2);border-radius:99px;overflow:hidden;border:1px solid var(--line)}
.pfill{height:100%;border-radius:99px;background:linear-gradient(90deg,var(--acc),#06b6d4,var(--ok));transition:width 1s cubic-bezier(.2,.8,.2,1)}

/* ── 学科小卡 ── */
.subs{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0 8px}
.sub{background:var(--surf);border:1px solid var(--line);border-radius:var(--radius);padding:12px 10px;
 text-align:center;box-shadow:var(--sh);transition:transform .18s,box-shadow .18s}
.sub:hover{transform:translateY(-1px);box-shadow:var(--sh2)}
.sub .si{font-size:16px;font-weight:800;color:var(--c);line-height:1.2}
.sub .sn{font-size:11.5px;color:var(--mut);margin-top:4px;font-weight:600}
.sub .sv{font-size:16px;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums;color:var(--fg)}
.sub .su{font-size:10px;color:var(--mut);margin-top:2px}
@media(prefers-color-scheme:dark){:root:not([data-t=light]) .sub .si{color:var(--cd)}}
:root[data-t=dark] .sub .si{color:var(--cd)}

/* ── 页签 ── */
.pane{display:none;animation:fade .22s ease-out}
.pane.on{display:block}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

.bar{position:fixed;left:0;right:0;bottom:0;z-index:40;
 background:color-mix(in srgb,var(--surf) 90%,transparent);
 backdrop-filter:saturate(180%) blur(24px);-webkit-backdrop-filter:saturate(180%) blur(24px);
 border-top:1px solid var(--line);display:flex;padding-bottom:env(safe-area-inset-bottom);box-shadow:0 -4px 16px rgba(0,0,0,.03)}
.bar button{flex:1;background:none;border:0;color:var(--mut);font:inherit;font-size:11.5px;
 padding:9px 4px 10px;cursor:pointer;line-height:1.3;transition:color .15s}
.bar button i{display:block;font-style:normal;font-size:18px;margin-bottom:2px;
 transition:transform .2s cubic-bezier(.3,1.4,.5,1)}
.bar button.on{color:var(--acc);font-weight:700}
.bar button.on i{transform:translateY(-2px) scale(1.1)}

/* ── 通用卡片块 ── */
.blk{background:var(--surf);border:1px solid var(--line);border-radius:var(--radius);margin:14px 0;
 overflow:hidden;box-shadow:var(--sh)}
.blk-h{padding:12px 18px;font-size:12.5px;color:var(--fg);font-weight:700;
 border-bottom:1px solid var(--line);background:var(--surf2);display:flex;align-items:center}
.blk-h .ic{color:var(--c);font-weight:800;margin-right:8px;font-size:14px}
.blk-h .sep{margin:0 6px;opacity:.4;color:var(--mut)}
.blk-b{padding:4px 18px 16px}
.blk-b h3,.blk-b h4,.blk-b h5,.blk-b h6{font-size:14px;margin:14px 0 6px;font-weight:700}
.blk-b p{margin:8px 0;line-height:1.6}
.blk-b ul,.blk-b ol{margin:8px 0;padding-left:18px}
.blk-b li{margin:4px 0}
.blk-b hr{border:0;border-top:1px solid var(--line);margin:14px 0}
.blk-b blockquote{margin:10px 0;padding:10px 14px;background:var(--surf2);
 border-left:3px solid var(--acc);border-radius:0 10px 10px 0;font-size:13px;color:var(--mut)}
.blk-b code{background:var(--surf2);padding:2px 6px;border-radius:6px;font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace}
.blk-b pre{background:var(--surf2);padding:12px;border-radius:12px;overflow-x:auto;font-size:12px;line-height:1.5}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:10px -18px;padding:0 18px}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px;min-width:320px;border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{border-bottom:1px solid var(--line);border-right:1px solid var(--line);padding:9px 12px;text-align:left;vertical-align:middle}
th:last-child,td:last-child{border-right:0}
tr:last-child td{border-bottom:0}
th{background:var(--surf2);font-weight:700;color:var(--fg);white-space:nowrap}
tr:nth-child(even) td{background:color-mix(in srgb,var(--surf2) 40%,transparent)}

/* ── 遮罩自测效果 ── */
.mask-active td:nth-child(n+2):not(:last-child){filter:blur(6px);user-select:none;cursor:pointer;transition:filter .2s ease;background:color-mix(in srgb,var(--acc-sub) 30%,transparent)}
.mask-active td:nth-child(n+2):not(:last-child).revealed{filter:none;background:transparent}

.empty{text-align:center;color:var(--mut);padding:56px 20px;font-size:13.5px}
.empty .ei{font-size:36px;margin-bottom:10px;opacity:.6}
.extra{margin:18px 0 8px}
.extra summary{cursor:pointer;font-size:12.5px;color:var(--mut);padding:10px 14px;
 background:var(--surf);border:1px solid var(--line);border-radius:12px;list-style:none;transition:background .15s}
.extra summary:hover{background:var(--surf2)}
.extra summary::-webkit-details-marker{display:none}
.extra[open] summary{border-radius:12px 12px 0 0;border-bottom:0}

/* ── 卡组选择 ── */
.decks{display:flex;gap:8px;overflow-x:auto;padding:12px 0 10px;
 -webkit-overflow-scrolling:touch;scrollbar-width:none}
.decks::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;background:var(--surf);border:1px solid var(--line);border-radius:99px;
 padding:7px 14px;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:6px;
 white-space:nowrap;transition:.15s;box-shadow:var(--sh)}
.chip:hover{border-color:var(--acc)}
.chip .dot{width:6px;height:6px;border-radius:99px;background:var(--c)}
.chip .n{color:var(--mut);font-size:10.5px;font-variant-numeric:tabular-nums;background:var(--surf2);padding:1px 6px;border-radius:99px}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:700}
.chip.on .dot{background:#fff}
.chip.on .n{background:rgba(255,255,255,.2);color:#fff}

/* ── 翻卡 ── */
.stage{perspective:1400px;margin:8px 0 14px}
.card{position:relative;width:100%;min-height:50vh;transform-style:preserve-3d;
 transition:transform .48s cubic-bezier(.2,.8,.2,1);cursor:pointer}
.card.flip{transform:rotateY(180deg)}
.face{position:absolute;inset:0;backface-visibility:hidden;-webkit-backface-visibility:hidden;
 background:var(--surf);border:1px solid var(--line);border-radius:20px;box-shadow:var(--sh2);
 display:flex;flex-direction:column;overflow:hidden}
.face.back{transform:rotateY(180deg)}
.f-top{display:flex;align-items:center;gap:8px;padding:14px 18px 0;font-size:11.5px;color:var(--mut)}
.f-top .tag{background:var(--acc);color:#fff;padding:3px 10px;border-radius:99px;font-weight:700;font-size:10.5px}
.f-top .hard{margin-left:auto;font-size:16px;opacity:.35;cursor:pointer;transition:.2s}
.f-top .hard.on{opacity:1;color:var(--warn);transform:scale(1.15)}
.f-body{flex:1;display:flex;align-items:center;justify-content:center;
 padding:18px 24px 20px;overflow-y:auto;-webkit-overflow-scrolling:touch}
.f-q{font-size:22px;font-weight:700;line-height:1.55;text-align:center;letter-spacing:-.01em}
.f-a{width:100%;font-size:14.5px}
.f-a .row{padding:10px 0;border-bottom:1px solid var(--line)}
.f-a .row:last-child{border-bottom:0}
.f-a .k{font-size:11px;color:var(--mut);letter-spacing:.06em;text-transform:uppercase;
 margin-bottom:4px;font-weight:700}
.f-a .v{line-height:1.65;word-break:break-word}
.f-hint{text-align:center;font-size:11px;color:var(--mut);padding:0 0 12px;opacity:.7}

/* ── 卡片控制 ── */
.ctrl{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.nav{flex:0 0 auto;width:42px;height:42px;border-radius:99px;background:var(--surf);
 border:1px solid var(--line);color:var(--fg);font-size:18px;cursor:pointer;box-shadow:var(--sh);
 display:flex;align-items:center;justify-content:center;transition:.15s}
.nav:hover{background:var(--surf2)}
.nav:active{transform:scale(.94)}
.nav:disabled{opacity:.3;cursor:default}
.meter{flex:1}
.meter .mt{display:flex;justify-content:space-between;font-size:11px;color:var(--mut);
 margin-bottom:5px;font-variant-numeric:tabular-nums;font-weight:600}
.mtrack{height:5px;background:var(--surf2);border-radius:99px;overflow:hidden}
.mfill{height:100%;background:var(--acc);border-radius:99px;transition:width .3s ease}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.tbtn{background:var(--surf);border:1px solid var(--line);color:var(--fg);border-radius:99px;
 padding:6px 14px;font:inherit;font-size:12px;cursor:pointer;box-shadow:var(--sh);transition:.15s;display:flex;align-items:center;gap:5px}
.tbtn:hover{background:var(--surf2)}
.tbtn:active{transform:scale(.96)}
.tbtn.on{background:var(--acc);color:#fff;border-color:var(--acc);font-weight:700}

/* ── 指标条 ── */
.mgrp{background:var(--surf);border:1px solid var(--line);border-radius:var(--radius);margin:14px 0;
 padding:14px 18px 8px;box-shadow:var(--sh)}
.mgrp h3{margin:0 0 12px;font-size:12.5px;color:var(--mut);font-weight:700;
 display:flex;align-items:center;gap:7px}
.mgrp h3 .ic{color:var(--c);font-weight:800}
.mgrp h3 .sep{opacity:.4}
.mi{margin-bottom:14px}
.mi .ml{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:6px}
.mi .mn{font-size:13.5px;font-weight:600;line-height:1.35}
.mi .mv{font-size:12.5px;color:var(--mut);white-space:nowrap;font-variant-numeric:tabular-nums}
.mi .mv b{font-size:14.5px;color:var(--fg);font-weight:800}
.mtk{position:relative;height:8px;background:var(--surf2);border-radius:99px;overflow:visible;border:1px solid var(--line)}
.mfl{height:100%;border-radius:99px;width:0;transition:width .9s cubic-bezier(.2,.8,.2,1)}
.mfl.good{background:linear-gradient(90deg,var(--ok),#34d399)}
.mfl.mid{background:linear-gradient(90deg,var(--warn),#fde047)}
.mfl.bad{background:linear-gradient(90deg,var(--bad),#fda4af)}
.mfl.neu{background:var(--acc)}
.tick{position:absolute;top:-3px;width:2px;height:14px;background:var(--fg);opacity:.4;border-radius:2px}
.tick::after{content:attr(data-l);position:absolute;top:-14px;left:50%;transform:translateX(-50%);
 font-size:9px;color:var(--mut);white-space:nowrap;opacity:.85}
.leg{display:flex;gap:16px;font-size:11px;color:var(--mut);padding:4px 0 10px;flex-wrap:wrap}
.leg i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:5px;vertical-align:-1px}

/* ── 知识图谱 (S3-4) ── */
.map-summary{background:var(--surf);border:1px solid var(--line);border-radius:var(--radius);padding:14px 18px;margin-bottom:14px;box-shadow:var(--sh)}
.map-badges{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 4px}
.mbadge{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:99px;font-size:11.5px;font-weight:700}
.mbadge.A{background:rgba(16,185,129,.12);color:var(--ok);border:1px solid rgba(16,185,129,.3)}
.mbadge.B{background:rgba(59,130,246,.12);color:#3b82f6;border:1px solid rgba(59,130,246,.3)}
.mbadge.C{background:rgba(245,158,11,.12);color:var(--warn);border:1px solid rgba(245,158,11,.3)}
.mbadge.D{background:rgba(239,68,68,.12);color:var(--bad);border:1px solid rgba(239,68,68,.3)}
.mbadge.U{background:rgba(148,163,184,.12);color:#64748b;border:1px solid rgba(148,163,184,.35)}
.map-chap{background:var(--surf);border:1px solid var(--line);border-radius:var(--radius);margin-bottom:12px;overflow:hidden;box-shadow:var(--sh)}
.map-chap-h{padding:12px 18px;font-weight:700;font-size:13.5px;background:var(--surf2);border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
.map-point{padding:10px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:13px}
.map-point:last-child{border-bottom:0}
.map-point-l{display:flex;align-items:center;gap:8px;flex:1}
.map-req{font-size:10.5px;color:var(--mut);background:var(--surf2);padding:2px 6px;border-radius:4px}
.map-err{font-size:11px;color:var(--bad);font-weight:600}

/* ── 趋势曲线 (S3-4) ── */
.trend-card{background:var(--surf);border:1px solid var(--line);border-radius:var(--radius);padding:14px 18px;margin-bottom:16px;box-shadow:var(--sh)}
.trend-card h3{margin:0 0 10px;font-size:13px;color:var(--mut);font-weight:700;display:flex;align-items:center;gap:6px}

footer{text-align:center;color:var(--mut);font-size:11px;padding:24px 0 12px;opacity:.7}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="hd">
    <div class="hl">
      <div class="lb">2026 研考倒计时</div>
      <div class="big">{{DDAY1}}<i>天</i></div>
    </div>
    <div class="hr2">
      <button class="tbtn-theme" id="th-btn" title="点击切换深色/浅色模式">🌓 主题模式</button>
      <div class="hr-info">
        初试首日 · <b>12-19</b><br>
        备考第 <b>{{DAYNO}}</b> / {{TOTALDAYS}} 天
      </div>
    </div>
  </div>
  <div class="plan">
    <div class="pt"><span>备考总日程推进</span><span>{{PLANPCT}}%</span></div>
    <div class="ptrack"><div class="pfill" id="pf" style="width:0"></div></div>
  </div>
</header>

<div class="subs" id="subs"></div>

<div id="p-today" class="pane on">
  <div style="display:flex;justify-content:flex-end;margin:8px 0 4px">
    <button class="tbtn" id="mask-today-btn">👁 开启遮罩自测</button>
  </div>
  {{TODAY}}
</div>

<div id="p-memo" class="pane">
  <div style="display:flex;justify-content:space-between;align-items:center;margin:8px 0 4px">
    <div class="decks" id="dk-memo"></div>
    <button class="tbtn" id="mask-memo-btn" style="flex-shrink:0">👁 遮罩自测</button>
  </div>
  <div id="fc-memo"></div>
  {{MEMONOTES}}
</div>

<div id="p-weak" class="pane">
  <div class="decks" id="dk-weak"></div>
  <div id="fc-weak"></div>
  {{WEAKNOTES}}
</div>

<div id="p-stat" class="pane">
  <div id="stat-trend"></div>
  <div class="leg">
    <span><i style="background:var(--ok)"></i>达标</span>
    <span><i style="background:var(--warn)"></i>接近</span>
    <span><i style="background:var(--bad)"></i>待突破</span>
    <span>│ 竖线 = 目标线</span>
  </div>
  <div id="stat"></div>
</div>

<div id="p-map" class="pane">
  <div style="display:flex;justify-content:space-between;align-items:center;margin:8px 0 10px">
    <div class="decks" id="dk-map"></div>
  </div>
  <div id="map-summary-box"></div>
  <div id="map-tree"></div>
</div>

<footer>考研学习看板 · 数据驱动 · 稳扎稳打 · 更新于 {{STAMP}}</footer>
</div>

<nav class="bar">
  <button class="on" data-p="today"><i>📋</i>今日</button>
  <button data-p="memo"><i>🧠</i>必背</button>
  <button data-p="weak"><i>🎯</i>薄弱</button>
  <button data-p="stat"><i>📊</i>数据</button>
  <button data-p="map"><i>🗺️</i>图谱</button>
</nav>

<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
<script>
var D = {{DATA}};

function fallbackMathUnicode(el){
  if(!el) return;
  function cleanLatex(s){
    return s
      .replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1)/($2)')
      .replace(/\\sqrt\{([^}]+)\}/g, '√($1)')
      .replace(/\\iint/g, '∬').replace(/\\int/g, '∫')
      .replace(/\\sum/g, '∑').replace(/\\prod/g, '∏').replace(/\\infty/g, '∞')
      .replace(/\\lim_?\{([^}]*)\}/g, 'lim($1)').replace(/\\lim/g, 'lim')
      .replace(/\\alpha/g, 'α').replace(/\\beta/g, 'β').replace(/\\gamma/g, 'γ')
      .replace(/\\delta/g, 'δ').replace(/\\epsilon/g, 'ε').replace(/\\theta/g, 'θ')
      .replace(/\\lambda/g, 'λ').replace(/\\pi/g, 'π').replace(/\\sigma/g, 'σ')
      .replace(/\\xi/g, 'ξ').replace(/\\eta/g, 'η').replace(/\\phi/g, 'φ')
      .replace(/\\le(q)?/g, '≤').replace(/\\ge(q)?/g, '≥').replace(/\\ne(q)?/g, '≠')
      .replace(/\\approx/g, '≈').replace(/\\pm/g, '±').replace(/\\times/g, '×')
      .replace(/\\cdot/g, '·').replace(/\\to/g, '→').replace(/\\rightarrow/g, '→')
      .replace(/\\in/g, '∈').replace(/\\subset/g, '⊂').replace(/\\cap/g, '∩').replace(/\\cup/g, '∪')
      .replace(/\^2/g, '²').replace(/\^3/g, '³').replace(/\^n/g, 'ⁿ')
      .replace(/_([0-9a-z])/g, '₍$1₎')
      .replace(/\\[a-zA-Z]+/g, '')
      .replace(/[{}]/g, '');
  }
  function walk(node){
    if(node.nodeType === 3){
      var txt = node.nodeValue;
      if(/\$|\\\(|\\\[/.test(txt)){
        var rep = txt.replace(/\$\$([\s\S]*?)\$\$/g, function(_, m){
          return '【 ' + cleanLatex(m) + ' 】';
        }).replace(/\$([\s\S]*?)\$/g, function(_, m){
          return cleanLatex(m);
        });
        if(rep !== txt) node.nodeValue = rep;
      }
    }else if(node.nodeType === 1 && node.nodeName !== 'SCRIPT' && node.nodeName !== 'STYLE'){
      for(var i=0; i<node.childNodes.length; i++){
        walk(node.childNodes[i]);
      }
    }
  }
  walk(el);
}

function tex(el){
  if(!el) return;
  if(typeof renderMathInElement === 'function'){
    try{
      renderMathInElement(el,{
        delimiters:[
          {left:'$$',right:'$$',display:true},
          {left:'$',right:'$',display:false}
        ],
        throwOnError:false,
        errorColor:'#f87171'
      });
      return;
    }catch(e){console.warn('KaTeX render warning, fallback to Unicode:', e);}
  }
  fallbackMathUnicode(el);
}
function esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
function isDark(){
  var a=document.documentElement.getAttribute('data-t');
  if(a) return a==='dark';
  return matchMedia('(prefers-color-scheme:dark)').matches;
}
function col(o){return isDark()?(o.dark||o.color):(o.color||'#4f46e5');}

/* ── 主题切换 ── */
(function(){
  var tb=document.getElementById('th-btn');
  if(!tb) return;
  try{
    var saved=localStorage.getItem('kytheme');
    if(saved) document.documentElement.setAttribute('data-t', saved);
  }catch(e){}
  tb.onclick=function(){
    var cur=document.documentElement.getAttribute('data-t');
    var next=cur==='dark'?'light':(cur==='light'?'dark':(isDark()?'light':'dark'));
    document.documentElement.setAttribute('data-t',next);
    try{localStorage.setItem('kytheme',next);}catch(e){}
  };
})();

/* ── 学科小卡 ── */
(function(){
  var h='';
  D.subjects.forEach(function(s){
    var v = s.notes===null ? '—' : s.notes;
    h += "<div class='sub' style='--c:"+s.color+";--cd:"+s.dark+"'>"
       + "<div class='si'>"+esc(s.icon)+"</div>"
       + "<div class='sn'>"+esc(s.name)+"</div>"
       + "<div class='sv'>"+v+"</div>"
       + "<div class='su'>"+(s.notes===null?'规划中':'篇归档')+"</div></div>";
  });
  var subHost=document.getElementById('subs');
  if(subHost) subHost.innerHTML=h;
})();

/* ── 遮罩自测逻辑 ── */
(function(){
  function bindMask(btnId, containerId){
    var btn=document.getElementById(btnId);
    var pane=document.getElementById(containerId);
    if(!btn || !pane) return;
    btn.onclick=function(){
      pane.classList.toggle('mask-active');
      var on = pane.classList.contains('mask-active');
      btn.classList.toggle('on', on);
      btn.textContent = on ? '✓ 已开启遮罩自测' : '👁 开启遮罩自测';
    };
    pane.addEventListener('click', function(e){
      var td=e.target.closest('td');
      if(td && pane.classList.contains('mask-active')){
        td.classList.toggle('revealed');
      }
    });
  }
  bindMask('mask-today-btn', 'p-today');
  bindMask('mask-memo-btn', 'p-memo');
})();

/* ── 闪卡引擎 ── */
function Flash(tab){
  var decks=D[tab]||[], self=this;
  this.tab=tab; this.di=0; this.ci=0; this.shuffled=false; this.onlyHard=false;
  this.order=[];
  var KEY='ky-hard-'+tab;
  try{ this.hard=JSON.parse(localStorage.getItem(KEY)||'{}'); }catch(e){ this.hard={}; }
  this.saveHard=function(){ try{localStorage.setItem(KEY,JSON.stringify(self.hard));}catch(e){} };

  this.deck=function(){ return decks[self.di]; };
  this.key=function(i){ return self.di+':'+i; };

  this.rebuild=function(){
    var d=self.deck(); if(!d){self.order=[];return;}
    var idx=d.cards.map(function(_,i){return i;});
    if(self.onlyHard) idx=idx.filter(function(i){return self.hard[self.key(i)];});
    if(self.shuffled) for(var i=idx.length-1;i>0;i--){var j=Math.floor(Math.random()*(i+1));var t=idx[i];idx[i]=idx[j];idx[j]=t;}
    self.order=idx; if(self.ci>=idx.length) self.ci=0;
  };

  this.chips=function(){
    var el=document.getElementById('dk-'+tab); if(!el) return;
    if(!decks.length){el.innerHTML='';return;}
    el.innerHTML=decks.map(function(d,i){
      return "<div class='chip"+(i===self.di?' on':'')+"' data-i='"+i+"' style='--c:"+col(d)+"'>"
           + "<span class='dot'></span>"+esc(d.subj)+" · "+esc(d.title)
           + "<span class='n'>"+d.cards.length+"</span></div>";
    }).join('');
    el.querySelectorAll('.chip').forEach(function(c){
      c.onclick=function(){ self.di=+c.dataset.i; self.ci=0; self.rebuild(); self.chips(); self.render(); };
    });
  };

  this.render=function(){
    var host=document.getElementById('fc-'+tab); if(!host) return;
    var d=self.deck();
    if(!d){ host.innerHTML="<div class='empty'><div class='ei'>🗂</div>暂无卡片数据<br><small>在 _状态/ 中完善表格后会自动生成卡片</small></div>"; return; }
    if(!self.order.length){
      host.innerHTML="<div class='tools'>"+self.toolsHtml()+"</div>"
        +"<div class='empty'><div class='ei'>⭐</div>暂无标记难点<br><small>翻卡时点击右上角星号标记</small></div>";
      self.bindTools(); return;
    }
    var i=self.order[self.ci], c=d.cards[i], C=col(d);
    var hard=!!self.hard[self.key(i)];
    var back=c.b.map(function(kv){
      return "<div class='row'><div class='k'>"+esc(kv[0])+"</div><div class='v'>"+esc(kv[1])+"</div></div>";
    }).join('');

    host.innerHTML =
      "<div class='tools'>"+self.toolsHtml()+"</div>"
    + "<div class='ctrl'>"
    +   "<button class='nav' id='pv-"+tab+"' aria-label='上一张'>‹</button>"
    +   "<div class='meter' style='--c:"+C+"'><div class='mt'><span>"+esc(d.title)+"</span>"
    +     "<span>"+(self.ci+1)+" / "+self.order.length+"</span></div>"
    +     "<div class='mtrack'><div class='mfill' style='width:"+((self.ci+1)/self.order.length*100)+"%'></div></div></div>"
    +   "<button class='nav' id='nx-"+tab+"' aria-label='下一张'>›</button>"
    + "</div>"
    + "<div class='stage'><div class='card' id='cd-"+tab+"' style='--c:"+C+"'>"
    +   "<div class='face'>"
    +     "<div class='f-top'><span class='tag'>"+esc(d.subj)+"</span><span>"+esc(d.title)+"</span>"
    +       "<span class='hard"+(hard?' on':'')+"' id='hd-"+tab+"'>"+(hard?'★':'☆')+"</span></div>"
    +     "<div class='f-body'><div class='f-q'>"+esc(c.f)+"</div></div>"
    +     "<div class='f-hint'>点击翻转卡片 · 左右轻扫切换</div>"
    +   "</div>"
    +   "<div class='face back'>"
    +     "<div class='f-top'><span class='tag'>答案解析</span><span>"+esc(d.title)+"</span></div>"
    +     "<div class='f-body'><div class='f-a'>"+back+"</div></div>"
    +     "<div class='f-hint'>再次点击返回卡片正面</div>"
    +   "</div>"
    + "</div></div>";

    var card=document.getElementById('cd-'+tab);
    if(card) card.onclick=function(e){ if(e.target.id==='hd-'+tab) return; card.classList.toggle('flip'); };
    var hd=document.getElementById('hd-'+tab);
    if(hd) hd.onclick=function(e){
      e.stopPropagation();
      var k=self.key(i);
      if(self.hard[k]) delete self.hard[k]; else self.hard[k]=1;
      self.saveHard(); self.render();
    };
    var pv=document.getElementById('pv-'+tab);
    if(pv) pv.onclick=function(){ self.go(-1); };
    var nx=document.getElementById('nx-'+tab);
    if(nx) nx.onclick=function(){ self.go(1); };
    if(card) self.swipe(card);
    self.bindTools();
    tex(host);
  };

  this.toolsHtml=function(){
    var n=Object.keys(self.hard).filter(function(k){return k.indexOf(self.di+':')===0;}).length;
    return "<button class='tbtn"+(self.shuffled?' on':'')+"' id='sf-"+self.tab+"'>🔀 随机打乱</button>"
         + "<button class='tbtn"+(self.onlyHard?' on':'')+"' id='oh-"+self.tab+"'>⭐ 重点难点 "+(n?'('+n+')':'')+"</button>"
         + "<button class='tbtn' id='rs-"+self.tab+"'>↺ 从头开始</button>";
  };
  this.bindTools=function(){
    var a=document.getElementById('sf-'+self.tab),b=document.getElementById('oh-'+self.tab),c=document.getElementById('rs-'+self.tab);
    if(a)a.onclick=function(){self.shuffled=!self.shuffled;self.ci=0;self.rebuild();self.render();};
    if(b)b.onclick=function(){self.onlyHard=!self.onlyHard;self.ci=0;self.rebuild();self.render();};
    if(c)c.onclick=function(){self.ci=0;self.rebuild();self.render();};
  };

  this.go=function(step){
    if(!self.order.length) return;
    self.ci=(self.ci+step+self.order.length)%self.order.length;
    self.render();
  };

  this.swipe=function(el){
    var x0=null,y0=null;
    el.addEventListener('touchstart',function(e){x0=e.touches[0].clientX;y0=e.touches[0].clientY;},{passive:true});
    el.addEventListener('touchend',function(e){
      if(x0===null)return;
      var dx=e.changedTouches[0].clientX-x0, dy=e.changedTouches[0].clientY-y0;
      if(Math.abs(dx)>52 && Math.abs(dx)>Math.abs(dy)*1.4){ self.go(dx<0?1:-1); }
      x0=y0=null;
    },{passive:true});
  };

  this.init=function(){ self.rebuild(); self.chips(); self.render(); };
}

var FM=new Flash('memo'), FW=new Flash('weak');
FM.init(); FW.init();

document.addEventListener('keydown',function(e){
  var btn=document.querySelector('.bar button.on');
  if(!btn) return;
  var t=btn.dataset.p;
  var f = t==='memo'?FM : (t==='weak'?FW:null);
  if(!f) return;
  if(e.key==='ArrowRight'){f.go(1);e.preventDefault();}
  if(e.key==='ArrowLeft'){f.go(-1);e.preventDefault();}
  if(e.key===' '){
    var c=document.getElementById('cd-'+t);
    if(c){c.classList.toggle('flip');e.preventDefault();}
  }
});

/* ── 指标进度条 ── */
(function(){
  var host=document.getElementById('stat');
  if(!host) return;
  if(!D.metrics.length){ host.innerHTML="<div class='empty'><div class='ei'>📊</div>暂无可视化指标数据<br><small>在薄弱点雷达中记录错因即可生成</small></div>"; return; }
  var h='';
  D.metrics.forEach(function(g){
    var C=col(g);
    h+="<div class='mgrp' style='--c:"+C+"'><h3><span class='ic'>"+esc(g.icon)+"</span>"
      +esc(g.subj)+"<span class='sep'>·</span>"+esc(g.title)+"</h3>";
    g.items.forEach(function(it){
      var pct = it.pct;
      var cls='neu', w=0, tick='';
      if(pct!==undefined){
        w=Math.max(pct,1.5);
        if(it.target!==undefined){
          var ok = it.dir==='lower' ? (pct<=it.target) : (pct>=it.target);
          var near = it.dir==='lower' ? (pct<=it.target*1.35) : (pct>=it.target*0.8);
          cls = ok?'good':(near?'mid':'bad');
          tick="<div class='tick' style='left:"+Math.min(it.target,99)+"%' data-l='目标'></div>";
        }else{
          cls = pct>=75?'good':(pct>=50?'mid':'bad');
        }
      }else if(it.count!==undefined){
        w=Math.min(it.count*10,100); cls='neu';
      }
      h+="<div class='mi'><div class='ml'><span class='mn'>"+esc(it.label)+"</span>"
        +"<span class='mv'><b>"+esc(it.text)+"</b>"+(it.target!==undefined?" / 目标 "+it.target+"%":"")+"</span></div>"
        +"<div class='mtk'>"+tick+"<div class='mfl "+cls+"' data-w='"+w+"'></div></div></div>";
    });
    h+="</div>";
  });
  host.innerHTML=h;
})();

function animate(){
  var pf=document.getElementById('pf');
  if(pf) pf.style.width='{{PLANPCT}}%';
  document.querySelectorAll('#stat .mfl').forEach(function(el,i){
    setTimeout(function(){ el.style.width=el.dataset.w+'%'; }, 40+i*22);
  });
}

/* ── 趋势曲线 SVG (S3-4) ── */
(function(){
  var host=document.getElementById('stat-trend');
  if(!host) return;
  var trend = D.trend || [];
  if(!trend.length){
    host.innerHTML = "<div class='trend-card'><h3>📈 近 7 日完成率趋势</h3><div style='font-size:12px;color:var(--mut);text-align:center;padding:12px 0;'>暂无历史打卡记录，坚持复习将在此汇聚成长曲线。</div></div>";
    return;
  }
  var w = 480, h = 120, padL = 36, padR = 24, padT = 20, padB = 28;
  var chartW = w - padL - padR, chartH = h - padT - padB;
  var n = trend.length;
  var pts = [];
  trend.forEach(function(d, i){
    var x = padL + (n === 1 ? chartW / 2 : (i / (n - 1)) * chartW);
    var y = padT + (1 - (Math.min(100, Math.max(0, d.rate)) / 100)) * chartH;
    pts.push({x: x, y: y, rate: d.rate, date: d.short_date || d.date});
  });
  var pathD = pts.map(function(p, i){ return (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ',' + p.y.toFixed(1); }).join(' ');
  var areaD = pathD + ' L' + pts[pts.length-1].x.toFixed(1) + ',' + (padT + chartH) + ' L' + pts[0].x.toFixed(1) + ',' + (padT + chartH) + ' Z';
  
  var svg = "<svg viewBox='0 0 " + w + " " + h + "' style='width:100%;height:auto;overflow:visible'>"
    + "<defs><linearGradient id='tg' x1='0' y1='0' x2='0' y2='1'><stop offset='0%' stop-color='var(--acc)' stop-opacity='0.35'/><stop offset='100%' stop-color='var(--acc)' stop-opacity='0.0'/></linearGradient></defs>"
    + "<line x1='" + padL + "' y1='" + (padT + chartH) + "' x2='" + (padL + chartW) + "' y2='" + (padT + chartH) + "' stroke='var(--line)' stroke-width='1'/>"
    + "<line x1='" + padL + "' y1='" + (padT + chartH/2) + "' x2='" + (padL + chartW) + "' y2='" + (padT + chartH/2) + "' stroke='var(--line)' stroke-width='1' stroke-dasharray='3,3'/>"
    + "<text x='" + (padL - 6) + "' y='" + (padT + 4) + "' font-size='9' fill='var(--mut)' text-anchor='end'>100%</text>"
    + "<text x='" + (padL - 6) + "' y='" + (padT + chartH/2 + 3) + "' font-size='9' fill='var(--mut)' text-anchor='end'>50%</text>"
    + "<path d='" + areaD + "' fill='url(#tg)'/>"
    + "<path d='" + pathD + "' fill='none' stroke='var(--acc)' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/>";
  
  pts.forEach(function(p){
    svg += "<circle cx='" + p.x.toFixed(1) + "' cy='" + p.y.toFixed(1) + "' r='3.5' fill='var(--surf)' stroke='var(--acc)' stroke-width='2'/>"
         + "<text x='" + p.x.toFixed(1) + "' y='" + (p.y - 7).toFixed(1) + "' font-size='10' font-weight='700' fill='var(--fg)' text-anchor='middle'>" + Math.round(p.rate) + "%</text>"
         + "<text x='" + p.x.toFixed(1) + "' y='" + (h - 8) + "' font-size='10' fill='var(--mut)' text-anchor='middle'>" + p.date + "</text>";
  });
  svg += "</svg>";
  host.innerHTML = "<div class='trend-card'><h3>📈 近 7 日任务完成率趋势 (Trend)</h3>" + svg + "</div>";
})();

/* ── 知识图谱渲染 (S3-4) ── */
(function(){
  var subjs = [
    {key:'math', name:'数学', color:'#2563eb'},
    {key:'eng', name:'英语', color:'#e11d48'},
    {key:'pol', name:'政治', color:'#d97706'},
    {key:'pro', name:'专业课', color:'#059669'}
  ];
  var currSubj = 'math';
  var maps = D.maps || {};

  function renderMap(subjKey){
    var m = maps[subjKey];
    var dk = document.getElementById('dk-map');
    if(dk){
      dk.innerHTML = subjs.map(function(s){
        return "<div class='chip" + (s.key === subjKey ? " on" : "") + "' style='--c:" + s.color + "' data-s='" + s.key + "'>"
             + "<span class='dot'></span>" + esc(s.name) + "</div>";
      }).join('');
      dk.querySelectorAll('.chip').forEach(function(c){
        c.onclick = function(){
          currSubj = c.dataset.s;
          renderMap(currSubj);
        };
      });
    }

    var sumBox = document.getElementById('map-summary-box');
    var treeBox = document.getElementById('map-tree');
    if(!sumBox || !treeBox) return;

    if(!m || !m.chapters || !m.chapters.length){
      sumBox.innerHTML = '';
      treeBox.innerHTML = "<div class='empty'><div class='ei'>🗺️</div>暂无该科目考纲图谱数据</div>";
      return;
    }

    var gc = m.grade_counts || {A:0,B:0,C:0,D:0,U:0};
    var _assessed = (typeof m.assessed_count === 'number') ? m.assessed_count : (m.total_points - (gc.U||0));
    var _arate = (typeof m.assessed_rate === 'number') ? m.assessed_rate : 0;
    sumBox.innerHTML = "<div class='map-summary'>"
      + "<div style='display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px'>"
      + "<div><b style='font-size:15px'>" + esc(m.subject_name) + " · 考纲全景掌握大盘</b></div>"
      + "<div style='font-size:12px;color:var(--mut)'>已练考点掌握率 <b style='font-size:16px;color:var(--ok)'>" + m.mastery_rate + "%</b></div></div>"
      + "<div class='ptrack' style='height:6px;margin-bottom:6px'><div class='pfill' style='width:" + m.mastery_rate + "%;background:var(--ok)'></div></div>"
      + "<div style='font-size:11.5px;color:var(--mut);margin-bottom:10px'>已评估 " + _assessed + " / " + m.total_points + " 个考点 (覆盖率 " + _arate + "%)，未练习考点不计入掌握率</div>"
      + "<div class='map-badges'>"
      + "<span class='mbadge A'>● A 熟练 " + gc.A + "</span>"
      + "<span class='mbadge B'>● B 巩固 " + gc.B + "</span>"
      + "<span class='mbadge C'>● C 生疏 " + gc.C + "</span>"
      + "<span class='mbadge D'>● D 盲区 " + gc.D + "</span>"
      + "<span class='mbadge U'>○ U 未练 " + (gc.U||0) + "</span>"
      + "<span style='margin-left:auto;font-size:11.5px;color:var(--mut)'>共 " + m.total_points + " 个核心考点</span>"
      + "</div></div>";

    var h = '';
    m.chapters.forEach(function(chap){
      h += "<div class='map-chap'><div class='map-chap-h'><span>" + esc(chap.title) + "</span>"
        + "<span style='font-size:11px;color:var(--mut);font-weight:normal'>" + (chap.points ? chap.points.length : 0) + " 个考点</span></div>";
      if(chap.points && chap.points.length){
        chap.points.forEach(function(pt){
          var g = pt.grade || 'A';
          var errTag = pt.error_count > 0 ? ("<span class='map-err'>⚠ " + pt.error_count + " 错题</span>") : "";
          h += "<div class='map-point'>"
             + "<div class='map-point-l'>"
             + "<span class='mbadge " + g + "'>" + g + "</span>"
             + "<span style='font-weight:600'>" + esc(pt.name) + "</span>"
             + (pt.req_type ? ("<span class='map-req'>" + esc(pt.req_type) + "</span>") : "")
             + "</div>"
             + errTag
             + "</div>";
        });
      }
      h += "</div>";
    });
    treeBox.innerHTML = h;
    tex(treeBox);
  }

  renderMap(currSubj);
})();

/* ── 页签切换 ── */
document.querySelectorAll('.bar button').forEach(function(b){
  b.onclick=function(){
    document.querySelectorAll('.bar button').forEach(function(x){x.classList.remove('on')});
    document.querySelectorAll('.pane').forEach(function(x){x.classList.remove('on')});
    b.classList.add('on');
    var targetPane=document.getElementById('p-'+b.dataset.p);
    if(targetPane){
      targetPane.classList.add('on');
      tex(targetPane);
    }
    window.scrollTo(0,0);
    if(b.dataset.p==='stat') animate();
    try{localStorage.setItem('kytab',b.dataset.p)}catch(e){}
  };
});

tex(document.getElementById('p-today'));
setTimeout(animate,180);
try{
  var t=localStorage.getItem('kytab');
  if(t&&t!=='today'){var el=document.querySelector('.bar button[data-p="'+t+'"]'); if(el)el.click();}
}catch(e){}
</script>
</body>
</html>"""


def _write_state_snapshot(data: dict, snapshot_path: "Path", parse_warnings=None, sections_status=None):
    """
    把 build 出来的 data 序列化到 state_snapshot.json，作为 Pages 部署时的"真相源"。
    行为受 KY_SNAPSHOT_OPT_IN 控制：
      - KY_SNAPSHOT_OPT_IN=1 → 写出"可发布"快照（脱敏，不含任何可识别字符串）
      - 未设置/=0 → 仍然写出快照但打 WARNING，提示用户不要把未脱敏版本推送到公开仓库
    parse_warnings / sections_status 由 build() 提供，会一并写入 meta 便于诊断。
    """
    import os
    opt_in = os.environ.get("KY_SNAPSHOT_OPT_IN", "").lower() in ("1", "true", "yes", "on")

    snapshot_data = dict(data)  # 浅拷贝

    meta = {
        "snapshot_version": "ky-snapshot/1.0",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "opt_in": opt_in,
        "subjects_count": len(data.get("subjects", [])),
        "memo_cards": sum(len(d.get("cards", [])) for d in data.get("memo", [])),
        "weak_cards": sum(len(d.get("cards", [])) for d in data.get("weak", [])),
        "metrics_count": len(data.get("metrics", [])),
        "parse_warnings": parse_warnings or [],
        "sections_status": sections_status or [],
    }

    if not opt_in:
        # 公开版会泄露个人学情；打印强提示并把 full 字段置空作为警示
        print("[WARNING] KY_SNAPSHOT_OPT_IN 未开启。")
        print("          docs/state_snapshot.json 当前包含完整学情数据。")
        print("          若要推送到 GitHub Pages 等公开环境，请设置 KY_SNAPSHOT_OPT_IN=1 重新生成。")
        print("          例如: set KY_SNAPSHOT_OPT_IN=1 && python build.py   (Windows)")
        print("                 export KY_SNAPSHOT_OPT_IN=1 && python build.py   (macOS/Linux)")
        snapshot_payload = {"meta": meta, "data": snapshot_data}
    else:
        # 脱敏：去除卡片的"back"详情（可能含个人化 weakness 文本），保留 front 与指标
        safe_memo, safe_weak = [], []
        for d in data.get("memo", []):
            d2 = dict(d)
            d2["cards"] = [{"f": c.get("f", ""), "b": []} for c in d.get("cards", [])]
            safe_memo.append(d2)
        for d in data.get("weak", []):
            d2 = dict(d)
            d2["cards"] = [{"f": c.get("f", ""), "b": []} for c in d.get("cards", [])]
            safe_weak.append(d2)
        # 指标只保留 label/text/pct/count/target/dir（去掉可能含个人化描述的字段）
        safe_metrics = []
        for g in data.get("metrics", []):
            g2 = {k: v for k, v in g.items() if k != "title"}
            safe_items = []
            for it in g.get("items", []):
                safe_items.append({k: v for k, v in it.items() if k in ("label", "text", "pct", "count", "target", "dir")})
            g2["items"] = safe_items
            safe_metrics.append(g2)
        # subject 列表只保留聚合信息（去掉具体目标分数/满分）
        safe_subjects = []
        for s in data.get("subjects", []):
            safe_subjects.append({
                "key": s.get("key"),
                "name": s.get("name"),
                "icon": s.get("icon"),
                "color": s.get("color"),
                "dark": s.get("dark"),
                "notes": s.get("notes"),
                "ok": s.get("ok"),
            })
        meta["sanitized"] = True
        snapshot_payload = {
            "meta": meta,
            "data": {
                "memo": safe_memo,
                "weak": safe_weak,
                "metrics": safe_metrics,
                "subjects": safe_subjects,
                "plan": data.get("plan", {}),
                "maps": data.get("maps", {}),
                "trend": data.get("trend", []),
            }
        }
        print("[OK] 已生成 KY_SNAPSHOT_OPT_IN=1 脱敏快照。可安全提交至公开仓库。")

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot_payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return True


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    content, data_obj, parse_warnings, sections_status = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    print(f"[OK] generated: {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")
    if ROOT_DOCS.parent.parent == ROOT.parent and (ROOT.parent / "01-数学").exists():
        ROOT_DOCS.parent.mkdir(parents=True, exist_ok=True)
        ROOT_DOCS.write_text(content, encoding="utf-8")
        print(f"[OK] synced to root docs: {ROOT_DOCS}")

    # ── 新增：生成 state_snapshot.json（Pages 部署的真相源）──
    snapshot_path = OUT.parent / "state_snapshot.json"
    try:
        _write_state_snapshot(data_obj, snapshot_path,
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
