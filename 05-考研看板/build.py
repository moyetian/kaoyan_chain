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

_PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _third_saturday_of_december(year: int) -> datetime.date:
    """考研初试固定为 12 月倒数第二个周六（等价于 12 月第 3 个周六）。"""
    first = datetime.date(int(year), 12, 1)
    return first + datetime.timedelta(days=((5 - first.weekday()) % 7) + 14)


def _load_project_config() -> dict:
    """读取项目根目录的 ky_config.json；读不到返回空 dict 走日历推算。"""
    for cand in (_PKG_ROOT / "ky_config.json", pathlib.Path.cwd() / "ky_config.json"):
        try:
            if cand.exists():
                return json.loads(cand.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
    return {}


def _resolve_exam_day1(cfg: dict) -> datetime.date:
    """解析初试首日：显式 exam_date → target_year（入学年，头年 12 月初试）→ 日历推算。"""
    plan = cfg.get("study_plan") if isinstance(cfg.get("study_plan"), dict) else {}
    raw = plan.get("exam_date") or cfg.get("exam_date")
    if raw:
        try:
            return datetime.datetime.strptime(str(raw).strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    raw_year = plan.get("target_year") or cfg.get("target_year")
    try:
        return _third_saturday_of_december(int(str(raw_year).strip()[:4]) - 1)
    except (TypeError, ValueError):
        pass
    today = datetime.date.today()
    this_year = _third_saturday_of_december(today.year)
    return this_year if today <= this_year else _third_saturday_of_december(today.year + 1)


def _resolve_plan_start(cfg: dict, exam_day1: datetime.date) -> datetime.date:
    """解析备考起跑日：配置 start_date → 最早打卡记录 → 距初试 180 天。

    [审查 R-03 修复] 打卡记录须具备足够样本量（≥3 天）或足够时间跨度（最早记录
    早于一周前）才被采信。否则「今天刚打了第一次卡」会把起跑日钉死在今天，
    使备考进度条从 47% 视觉归零到 1%，此时应回退到「距初试 180 天」锚点。
    """
    plan = cfg.get("study_plan") if isinstance(cfg.get("study_plan"), dict) else {}
    raw = plan.get("start_date") or cfg.get("start_date")
    if not raw:
        hist = cfg.get("completion_history") or {}
        if isinstance(hist, dict) and hist:
            earliest = str(min(hist.keys(), key=str))
            week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
            if len(hist) >= 3 or earliest <= week_ago:
                raw = earliest
    if raw:
        try:
            return datetime.datetime.strptime(str(raw).strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return exam_day1 - datetime.timedelta(days=180)


_CONFIG = _load_project_config()
EXAM_DAY1 = _resolve_exam_day1(_CONFIG)
EXAM_DATE = EXAM_DAY1 + datetime.timedelta(days=1)
PLAN_START = _resolve_plan_start(_CONFIG, EXAM_DAY1)

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
    {"key": "math", "name": "数学", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><path d='M14.5 4a3.5 3.5 0 0 0-5 0v16a3.5 3.5 0 0 1-5 0'/><line x1='6' y1='12' x2='18' y2='12'/></svg>", "color": "#2563eb", "dark": "#60a5fa",
     "dir": MATH, "full": 150, "target": 110, "notes": "每日笔记"},
    {"key": "eng", "name": "英语", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><path d='M4 7V4h16v3M9 20h6M12 4v16'/></svg>", "color": "#e11d48", "dark": "#fb7185",
     "dir": ENG, "full": 100, "target": 60, "notes": "每日笔记"},
    {"key": "pol", "name": "政治", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><circle cx='12' cy='12' r='10'/><path d='M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'/><line x1='2' y1='12' x2='22' y2='12'/></svg>", "color": "#d97706", "dark": "#fbbf24",
     "dir": POL, "full": 100, "target": 70, "notes": None},
    {"key": "pro", "name": "专业课", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><path d='M22 12h-4l-3 9L9 3l-3 9H2'/></svg>", "color": "#059669", "dark": "#34d399",
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

STARS = {"★": 20, "☆": 0}   # 注：键必须是字符，空串键永远匹配不到（历史死数据已移除）


def to_pct(text):
    """从单元格里抽百分比 → (值, 展示文本) 或 None"""
    if not text:
        return None
    t = text.strip()
    if t in ("-", "—", "未测", "待评估", ""):
        return None

    # 星级 ★★★☆☆
    if re.fullmatch(r"[★☆]{3,7}", t):
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

    lead = re.split(r"[（(]", bare, maxsplit=1)[0]
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


#: build.py 内置的受信 SVG 图标（SUBJECTS 表）。图标是**代码内静态资源**而非用户数据，
#: 必须原样进入 HTML 才能被渲染成图形；走 esc() 会把源码当文本显示（实测全看板 10 处
#: 图标容器曾因此显示 <svg viewBox=...> 字面量，且其不可断行的长 token 还会把窄屏页面
#: 撑破）。用白名单放行，防止未来有人把用户可编辑字符串塞进 icon 字段引入 XSS。
_TRUSTED_ICONS = frozenset(s["icon"] for s in SUBJECTS)


def icon_html(icon: str) -> str:
    """受信图标渲染：白名单内的内置 SVG 原样放行，其余一律 HTML 转义。"""
    return icon if icon in _TRUSTED_ICONS else esc(icon)


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


def build_radar_html(root_path: pathlib.Path) -> str:
    """构建【📡 招考与考纲变动雷达】全景 HTML 模块 (Sprint 7)"""
    sections = []

    # 获取学员当前目标院校
    target_school = ""
    cfg_file = root_path / "ky_config.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            target_school = cfg.get("study_plan", {}).get("school") or cfg.get("target_school") or ""
        except Exception:
            pass

    # 1. 目标高校简章监控雷达 (Admission Watcher)
    watch_file = root_path / ".memory" / "admission_watch.json"
    watch_items = []
    if watch_file.exists():
        try:
            w_data = json.loads(watch_file.read_text(encoding="utf-8"))
            for code, winfo in w_data.items():
                watch_items.append(winfo)
        except Exception:
            pass

    # 若未建立独立监控库但已配置目标院校，自动合成目标院校动态监控卡片
    if not watch_items and target_school:
        watch_items.append({
            "school": target_school,
            "status": "WATCHING",
            "last_check": "系统自动纳入监控",
            "url": "https://yz.chsi.com.cn",
            "alert_titles": [f"已建立【{target_school}】研究生院招生简章与专业目录动态监控"]
        })

    w_html = []
    w_html.append("<section class='radar-sec'><h3><span><svg viewBox='0 0 24 24' width='16' height='16' stroke='currentColor' stroke-width='2' fill='none'><path d='M12 2v20M2 12h20M12 7a5 5 0 0 0-5 5M12 3a9 9 0 0 0-9 9'/></svg></span>目标院校简章监控雷达 (Admission Watcher)</h3>")
    if watch_items:
        w_html.append("<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>系统自动每隔周期轮询目标高校研究生院公告，比对哈希指纹变动：</div>")
        for it in watch_items:
            st = it.get("status", "UNCHANGED")
            is_new = st == "UPDATED"
            badge_cls = "radar-badge add" if is_new else "radar-badge del"
            st_text = "发现新简章/变动" if is_new else "指纹正常·未见变动"
            w_html.append("<div class='radar-card'>")
            w_html.append(f"<div class='radar-card-h'><span>{html.escape(it.get('school', '高校'))}</span><span class='{badge_cls}'>{st_text}</span></div>")
            w_html.append(f"<div style='font-size:12px;color:var(--mut);margin-bottom:4px'>最近检测: {it.get('last_check', '未巡检')} ｜ 官方通道: <a href='{it.get('url', '#')}' target='_blank' style='color:var(--acc);text-decoration:none;'>研究生院/招办官网 ↗</a></div>")
            if it.get("alert_titles"):
                w_html.append("<div style='font-size:12px;margin-top:6px;background:var(--surf);padding:6px 10px;border-radius:6px;'>")
                w_html.append("<b>最新简章线索:</b><ul style='margin:4px 0 0 16px;padding:0;'>")
                for at in it.get("alert_titles", [])[:3]:
                    w_html.append(f"<li>{html.escape(at)}</li>")
                w_html.append("</ul></div>")
            w_html.append("</div>")
    else:
        w_html.append("<div class='empty' style='padding:16px;'><div class='ei'>📡</div>暂未配置实时监控高校<br><small>在终端输入 <code>ky fetch watch 目标高校</code> 即可开启招生简章动态指纹轮询</small></div>")
    w_html.append("</section>")
    sections.append("".join(w_html))

    # 2. 考纲版本异动与掌握度分析 (Syllabus Diff Radar)
    diff_html = []
    diff_html.append("<section class='radar-sec'><h3><span><svg viewBox='0 0 24 24' width='16' height='16' stroke='currentColor' stroke-width='2' fill='none'><path d='M3 3v18h18M18 17l-5-5-4 4-6-6'/></svg></span>考纲版本异动与动荡率分析 (Syllabus Diff)</h3>")
    pro_dir = root_path / "04-专业课"
    diff_files = sorted(list(pro_dir.glob("考纲变动分析_*.md")), key=lambda p: (0 if target_school and target_school in p.name else 1, -p.stat().st_mtime)) if pro_dir.exists() else []
    if diff_files:
        diff_html.append("<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>基于大纲知识点多层 AST 结构化逐级对比（掌握/理解/了解）：</div>")
        for df in diff_files[:3]:
            txt = df.read_text(encoding="utf-8", errors="ignore")
            vol_match = re.search(r"考点动荡率[^\d]*(\d+\.?\d*)%", txt)
            vol = vol_match.group(1) if vol_match else "0.0"
            add_match = re.search(r"新增考点[^\d]*(\d+)", txt)
            del_match = re.search(r"删减考点[^\d]*(\d+)", txt)
            mod_match = re.search(r"考查微调[^\d]*(\d+)", txt)
            c_add = add_match.group(1) if add_match else "0"
            c_del = del_match.group(1) if del_match else "0"
            c_mod = mod_match.group(1) if mod_match else "0"

            diff_html.append("<div class='radar-card'>")
            diff_html.append(f"<div class='radar-card-h'><span>{html.escape(df.stem)}</span><span class='radar-badge mod'>动荡率 {vol}%</span></div>")
            diff_html.append("<div class='radar-stat'>")
            diff_html.append(f"<span class='radar-badge add'>+ 新增必考 {c_add} 处</span>")
            diff_html.append(f"<span class='radar-badge del'>- 彻底剔除 {c_del} 处</span>")
            diff_html.append(f"<span class='radar-badge mod'>~ 考查微调 {c_mod} 处</span>")
            diff_html.append("</div>")
            diff_html.append("<div style='font-size:11.5px;color:var(--mut);'>详见本地报告: <code>04-专业课/" + html.escape(df.name) + "</code></div>")
            diff_html.append("</div>")
    else:
        diff_html.append("<div class='empty' style='padding:16px;'><div class='ei'>📑</div>暂无大纲对比研报<br><small>在终端输入 <code>ky fetch diff --school 目标院校</code> 即可生成逐级 AST 差异透视与突破处方</small></div>")
    diff_html.append("</section>")
    sections.append("".join(diff_html))

    # 3. 社媒真实经验与就读体验精选 (Community Experiences)
    exp_html = []
    exp_html.append("<section class='radar-sec'><h3><span><svg viewBox='0 0 24 24' width='16' height='16' stroke='currentColor' stroke-width='2' fill='none'><path d='M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z'/></svg></span>社媒真实经验与避坑口碑档案 (Community Experiences)</h3>")
    # [P0 修复] 优先读取 .memory/experiences/（隐私目录），兼容旧 docs/experiences/ 存量
    exp_dir = root_path / ".memory" / "experiences"
    if not exp_dir.exists():
        exp_dir = root_path / "docs" / "experiences"
    exp_files = sorted(list(exp_dir.glob("*.md")), key=lambda p: (0 if target_school and target_school in p.name else 1, -p.stat().st_mtime)) if exp_dir.exists() else []
    # [审查修复] 脱敏模式（默认开启）：隐私目录中的院校名与本地路径不得写入公开看板
    sanitize = _snapshot_opt_in()
    if exp_files:
        exp_html.append("<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>聚合知乎、B站、小红书实名学长学姐真实就读体验与避坑指南 (AI 置信度降噪清洗)：</div>")
        for ef in exp_files[:4]:
            txt = ef.read_text(encoding="utf-8", errors="ignore")
            first_line = txt.splitlines()[0] if txt.splitlines() else ef.stem
            clean_title = re.sub(r"^#+\s*", "", first_line).replace("🎓", "").strip()

            pos_matches = re.findall(r"-\s*✅\s*\*\*([^\*]+)\*\*", txt)
            risk_matches = re.findall(r"-\s*⚠️\s*\*\*([^\*]+)\*\*", txt)

            if sanitize:
                token = ef.stem.split("_")[0]  # 文件名形如「目标院校_目标专业.md」
                clean_title = "目标院校 · 目标专业 社媒经验档案"
                pos_matches = [x.replace(token, "目标院校") for x in pos_matches]
                risk_matches = [x.replace(token, "目标院校") for x in risk_matches]

            exp_html.append("<div class='radar-card'>")
            exp_html.append(f"<div class='radar-card-h'><span>{html.escape(clean_title)}</span><span class='radar-badge tag'>AI置信清洗</span></div>")
            if pos_matches:
                exp_html.append("<div style='font-size:12px;margin:4px 0;color:var(--ok);'><b>优势亮点:</b> " + html.escape(" · ".join(pos_matches[:3])) + "</div>")
            if risk_matches:
                exp_html.append("<div style='font-size:12px;margin:4px 0;color:var(--bad);'><b>避坑防线:</b> " + html.escape(" · ".join(risk_matches[:3])) + "</div>")
            rel_exp = f".memory/experiences/{ef.name}" if ".memory" in str(ef) else f"docs/experiences/{ef.name}"
            if sanitize:
                rel_exp = ".memory/experiences/（本地隐私目录，不随看板公开）"
            exp_html.append(f"<div style='font-size:11.5px;color:var(--mut);margin-top:6px;'>详细经验条目与社媒直通车已归档至 <code>{html.escape(rel_exp)}</code></div>")
            exp_html.append("</div>")
    else:
        exp_html.append("<div class='empty' style='padding:16px;'><div class='ei'>💡</div>暂无沉淀的社媒经验贴<br><small>在终端输入 <code>ky fetch info 目标院校 目标专业 --save</code> 即可自动清洗并归档学长学姐实名经验</small></div>")
    exp_html.append("</section>")
    sections.append("".join(exp_html))

    return "\n".join(sections)


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


def _snapshot_opt_in():
    """Return True only for the publish-safe/sanitized mode."""
    import os
    return os.environ.get("KY_SNAPSHOT_OPT_IN", "1").lower() in ("1", "true", "yes", "on")


def _sanitize_public_data(data: dict) -> dict:
    """Remove answer/detail text before embedding data in HTML or JSON."""
    safe_memo, safe_weak = [], []
    for key in ("memo", "weak"):
        target = safe_memo if key == "memo" else safe_weak
        for d in data.get(key, []):
            d2 = dict(d)
            d2["cards"] = [{"f": c.get("f", ""), "b": []} for c in d.get("cards", [])]
            target.append(d2)
    safe_metrics = []
    for g in data.get("metrics", []):
        g2 = {k: v for k, v in g.items() if k != "title"}
        g2["items"] = [{k: v for k, v in it.items() if k in ("label", "text", "pct", "count", "target", "dir")} for it in g.get("items", [])]
        safe_metrics.append(g2)
    safe_subjects = [{k: s.get(k) for k in ("key", "name", "icon", "color", "dark", "notes", "ok")} for s in data.get("subjects", [])]
    # [G-3 体积治理] maps.<subj>.modules 是 chapters 的**纯投影**
    # （见 skills/knowledge_map.py: {c["title"]: c["points"] for c in chapters}），
    # 而前端只读 chapters（HTML 模板中的 m.chapters），从不读 modules。
    # 发布产物里再带一份派生副本会让 4 个科目各冗余约 9KB——实测占脱敏快照 40%。
    # 此处剥离该字段（不丢信息：可由同 payload 内的 chapters 完全重建）。
    safe_maps = {}
    for sk, m in (data.get("maps") or {}).items():
        if isinstance(m, dict) and "modules" in m:
            m = {k: v for k, v in m.items() if k != "modules"}
        safe_maps[sk] = m
    return {"memo": safe_memo, "weak": safe_weak, "metrics": safe_metrics,
            "subjects": safe_subjects, "plan": data.get("plan", {}),
            "maps": safe_maps, "trend": data.get("trend", [])}


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
        today_out = "<div class='empty'><div class='ei'><svg viewBox='0 0 24 24' width='36' height='36' stroke='currentColor' stroke-width='1.6' fill='none' style='display:block;margin:0 auto 10px'><rect x='8' y='2' width='8' height='4' rx='1'/><path d='M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2'/><path d='M9 12h6M9 16h4'/></svg></div>今日任务尚未生成<br><small>去 Antigravity 发「报道」</small></div>"

    # Public builds must not embed the private daily task prose. The structured
    # cards/metrics remain available in the sanitized payload above.
    if _snapshot_opt_in():
        today_out = "<div class='empty'><div class='ei'><svg viewBox='0 0 24 24' width='36' height='36' stroke='currentColor' stroke-width='1.6' fill='none' style='display:block;margin:0 auto 10px'><rect x='8' y='2' width='8' height='4' rx='1'/><path d='M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2'/><path d='M9 14l2 2 4-4'/></svg></div>今日任务已生成（内容保留在本地完整模式）</div>"

    def notes_out(tab):
        if _snapshot_opt_in():
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
    html_data = _sanitize_public_data(data) if _snapshot_opt_in() else data
    payload = json.dumps(html_data, ensure_ascii=False).replace("</", "<\\/")

    return (HTML
            .replace("{{DMATH}}", str(d_math))
            .replace("{{DDAY1}}", str(d_day1))
            .replace("{{DAYNO}}", str(day_no))
            .replace("{{TOTALDAYS}}", str(total_days))
            .replace("{{PLANPCT}}", f"{day_no / total_days * 100:.1f}")
            .replace("{{TODAY}}", today_out)
            .replace("{{MEMONOTES}}", notes_out("memo"))
            .replace("{{WEAKNOTES}}", notes_out("weak"))
            .replace("{{RADAR}}", radar_out)
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
<meta name="color-scheme" content="light dark">
<script>
/* [UX 升级 · 消除首屏主题闪烁 FOUC]
   旧实现把主题恢复放在页面末尾、且在所有 CDN <script src> 之后，
   浏览器必然先用默认（或系统）配色绘制一帧再跳变。
   主题必须在**首次绘制之前**确定，故前置到 <head> 内、任何外链之前。
   注：此处刻意不依赖任何外部脚本，纯同步执行。 */
(function(){
  try{
    var t=localStorage.getItem('kytheme');
    if(t==='dark'||t==='light'){document.documentElement.setAttribute('data-t',t);}
  }catch(e){}
})();
</script>
<title>考研学习看板 · 倒计时 {{DDAY1}} 天</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<style>
:root{
  --bg:#f8fafc; --surf:#ffffff; --surf2:#f1f5f9; --surf3:#e2e8f0; --fg:#0f172a; --mut:#64748b;
  --line:#e2e8f0; 
  --acc:#8b5cf6; --acc-sub:#ede9fe; 
  --ok:#10b981; --warn:#f59e0b; --bad:#ef4444; 
  --acc-grad: linear-gradient(135deg, #a78bfa, #8b5cf6);
  --radius:16px;
  --sh:0 4px 12px rgba(139, 92, 246, 0.06),0 1px 3px rgba(0,0,0,.04);
  --sh2:0 8px 24px rgba(139, 92, 246, 0.12),0 2px 6px rgba(0,0,0,.03);
  color-scheme:light dark;

  /* ── 动效 Token ──────────────────────────────────────────────────
     依据：单次微动效时长应控制在 200~350ms（克制原则），
     并统一缓动曲线。改造前散落 8 种时长（.15/.18/.2/.22/.3/.48/.9/1s），
     既有"点一下等半秒"的拖沓，也有同一页面快慢不一的不一致。 */
  --dur-fast:180ms;
  --dur-base:240ms;
  --dur-slow:320ms;
  --ease-std:cubic-bezier(.2,.8,.2,1);
  --ease-emph:cubic-bezier(.3,1.4,.5,1);

  /* ── 焦点环 Token ────────────────────────────────────────────────
     键盘用户必须能看见焦点落在哪里（此前全站 :focus 样式为 0 处）。 */
  --focus-ring:#6d28d9;
  --focus-w:2px;
}
@media(prefers-color-scheme:dark){:root:not([data-t=light]){
  --bg:#090d16; --surf:#111827; --surf2:#1e293b; --surf3:#334155; --fg:#f8fafc; --mut:#94a3b8;
  --line:#1e293b; 
  --acc:#a78bfa; --acc-sub:#2e1065; 
  --ok:#34d399; --warn:#fbbf24; --bad:#f87171;
  --acc-grad: linear-gradient(135deg, #c4b5fd, #a78bfa);
  --sh:0 1px 3px rgba(0,0,0,.3);
  --sh2:0 8px 24px rgba(0,0,0,.4);
  --focus-ring:#c4b5fd;
  color-scheme:dark;
}}
:root[data-t=dark]{
  --bg:#090d16; --surf:#111827; --surf2:#1e293b; --surf3:#334155; --fg:#f8fafc; --mut:#94a3b8;
  --line:#1e293b; 
  --acc:#a78bfa; --acc-sub:#2e1065; 
  --ok:#34d399; --warn:#fbbf24; --bad:#f87171;
  --acc-grad: linear-gradient(135deg, #c4b5fd, #a78bfa);
  --sh:0 1px 3px rgba(0,0,0,.3);
  --sh2:0 8px 24px rgba(0,0,0,.4);
  --focus-ring:#c4b5fd;
  color-scheme:dark;
}
:root[data-t=light]{
  --bg:#f8fafc; --surf:#ffffff; --surf2:#f1f5f9; --surf3:#e2e8f0; --fg:#0f172a; --mut:#64748b;
  --line:#e2e8f0; 
  --acc:#8b5cf6; --acc-sub:#ede9fe; 
  --ok:#10b981; --warn:#f59e0b; --bad:#ef4444; 
  --acc-grad: linear-gradient(135deg, #a78bfa, #8b5cf6);
  --sh:0 4px 12px rgba(139, 92, 246, 0.06),0 1px 3px rgba(0,0,0,.04);
  --sh2:0 8px 24px rgba(139, 92, 246, 0.12),0 2px 6px rgba(0,0,0,.03);
  --focus-ring:#6d28d9;
  color-scheme:light;
}

/* ── 键盘焦点可见性（全站统一焦点环） ──────────────────────────────
   改造前全站 :focus/:focus-visible 样式为 0 处，键盘用户只能依赖浏览器
   默认焦点环；而 .card/.chip/遮罩单元格等是 div/span/td 且不可聚焦，
   键盘完全无法操作。此处统一焦点样式，并只对键盘交互展示（:focus-visible），
   鼠标点击不出现焦点环，避免视觉噪音。 */
:focus-visible{
  outline:var(--focus-w) solid var(--focus-ring);
  outline-offset:2px;
  border-radius:6px;
}
/* 跳到主内容：键盘用户的第一个 Tab 落点（默认视觉隐藏，聚焦时显现） */
.skip-link{
  position:absolute;left:8px;top:-48px;z-index:99;
  padding:8px 14px;border-radius:0 0 10px 10px;
  background:var(--acc);color:#fff;font-size:13px;font-weight:600;text-decoration:none;
  transition:top var(--dur-fast) var(--ease-std);
}
.skip-link:focus{top:0;}
/* 仅供屏幕阅读器播报（视觉不可见但不使用 display:none，否则读屏也读不到） */
.sr-only{
  position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0;
}

/* ── 减少动态效果偏好（无障碍必需项） ──────────────────────────────
   改造前 CSS 中 prefers-reduced-motion 为 0 处，只有彩带在 JS 里做了判断：
   开启系统"减少动态效果"后，翻卡 3D 旋转(.48s)、页签淡入、进度条增长(1s)、
   遮罩模糊过渡仍然照跑，对前庭敏感用户不友好。
   此处统一把动效压到近乎瞬时（保留 0.01ms 而非 0，以便依赖 transitionend 的逻辑仍能触发）。 */
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{
    animation-duration:0.01ms !important;
    animation-iteration-count:1 !important;
    transition-duration:0.01ms !important;
    scroll-behavior:auto !important;
  }
}

/* ── Responsive Layout ── */
.app-container { display: flex; flex-direction: column; min-height: 100vh; }
.sidebar-header { display: none; }
.bar{position:fixed;left:0;right:0;bottom:0;z-index:40;
 background:color-mix(in srgb,var(--surf) 90%,transparent);
 backdrop-filter:saturate(180%) blur(24px);-webkit-backdrop-filter:saturate(180%) blur(24px);
 border-top:1px solid var(--line);display:flex;padding-bottom:env(safe-area-inset-bottom);box-shadow:0 -4px 16px rgba(0,0,0,.03)}

/* Tablet */
@media(min-width: 768px) {
  .bar { top: 0; bottom: auto; padding: 0 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.05); border-top: none; border-bottom: 1px solid var(--line); }
  .wrap { margin-top: 60px; padding: 0 32px; }
}

/* Desktop */
@media(min-width: 1024px) {
  .bar { 
    position: fixed; left: 0; top: 0; bottom: 0; right: auto; 
    width: 240px; flex-direction: column; padding: 24px 16px; 
    background: var(--surf); border-right: 1px solid var(--line); 
    border-bottom: none; justify-content: flex-start; align-items: flex-start; gap: 8px;
    box-shadow: none;
  }
  .bar button {
    flex: none; display: flex; align-items: center; gap: 12px;
    padding: 12px 16px; border-radius: 12px; width: 100%; text-align: left;
    background: transparent; color: var(--mut); margin: 0;
  }
  .bar button:hover { background: var(--surf2); }
  .bar button.on { background: var(--acc-sub); color: var(--acc); font-weight: 700; }
  .bar button i { margin: 0; font-size: 18px; display: flex; align-items: center; justify-content: center; width: 24px; }
  
  .sidebar-header { display: block; margin-bottom: 32px; padding: 0 16px; font-size: 18px; font-weight: 800; color: var(--fg); letter-spacing: -0.02em; }
  
  .wrap { max-width: 1080px; margin: 0 auto; margin-left: 240px; padding: 32px 40px; width: calc(100% - 240px); }
}

/* Primary buttons and progress bar gradients */
.pfill{height:100%;border-radius:99px;background:var(--acc-grad);transition:width var(--dur-slow) var(--ease-std)}
.tbtn.on{background:var(--acc-grad);color:#fff;border-color:transparent;font-weight:700}

*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{overscroll-behavior-y:none}
body{margin:0;background:var(--bg);color:var(--fg);
 font:14.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
 -webkit-font-smoothing:antialiased;padding-bottom:calc(76px + env(safe-area-inset-bottom));transition:background var(--dur-base) var(--ease-std),color var(--dur-base) var(--ease-std)}
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
.tbtn-theme{background:var(--surf2);border:1px solid var(--line);border-radius:99px;padding:6px 12px;font-size:12px;color:var(--fg);cursor:pointer;display:flex;align-items:center;gap:5px;box-shadow:var(--sh);transition:var(--dur-fast)}
.tbtn-theme:hover{background:var(--surf)}
.hr-info{font-size:11.5px;color:var(--mut);text-align:right;line-height:1.6}
.hr-info b{color:var(--fg);font-weight:700}

.plan{margin:14px 0 4px}
.plan .pt{display:flex;justify-content:space-between;font-size:11px;color:var(--mut);margin-bottom:6px;font-weight:600}
.ptrack{height:7px;background:var(--surf2);border-radius:99px;overflow:hidden;border:1px solid var(--line)}

/* ── 学科小卡 ── */
/* 列宽必须用 minmax(0,1fr) 而非 1fr：1fr 的自动下限是 min-content，
   四张卡片的固有最小宽度之和会直接把页面撑破（实测 390px 视口下文档宽 825px，
   手机端出现横向滚动、卡片被裁切）。 */
.subs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0 8px}
.sub{min-width:0;background:var(--surf);border:1px solid var(--line);border-radius:var(--radius);padding:12px 10px;
 text-align:center;box-shadow:var(--sh);transition:transform var(--dur-fast),box-shadow var(--dur-fast)}
.sub:hover{transform:translateY(-1px);box-shadow:var(--sh2)}
.sub .si{font-size:16px;font-weight:800;color:var(--c);line-height:1.2}
.sub .sn{font-size:11.5px;color:var(--mut);margin-top:4px;font-weight:600}
.sub .sv{font-size:16px;font-weight:800;margin-top:4px;font-variant-numeric:tabular-nums;color:var(--fg)}
.sub .su{font-size:10px;color:var(--mut);margin-top:2px}
/* 窄屏（手机）：四列挤在一屏会让卡片被裁切，改为 2×2 两行 */
@media(max-width: 520px){
  .subs{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(prefers-color-scheme:dark){:root:not([data-t=light]) .sub .si{color:var(--cd)}}
:root[data-t=dark] .sub .si{color:var(--cd)}

/* ── 页签 ── */
/* [UX 升级] 动效时长统一走 Token（200~350ms 区间），并显式声明焦点行为 */
.pane{display:none;animation:fade var(--dur-base) var(--ease-std)}
.pane.on{display:block}
.pane:focus{outline:none}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

.bar button{flex:1;background:none;border:0;color:var(--mut);font:inherit;font-size:11.5px;
 padding:9px 4px 10px;cursor:pointer;line-height:1.3;transition:color var(--dur-fast) var(--ease-std)}
.bar button i{display:block;font-style:normal;font-size:18px;margin-bottom:2px;
 transition:transform var(--dur-base) var(--ease-emph)}
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
.mask-active td:nth-child(n+2):not(:last-child){filter:blur(6px);user-select:none;cursor:pointer;transition:filter var(--dur-base) var(--ease-std);background:color-mix(in srgb,var(--acc-sub) 30%,transparent)}
.mask-active td:nth-child(n+2):not(:last-child).revealed{filter:none;background:transparent}

.empty{text-align:center;color:var(--mut);padding:56px 20px;font-size:13.5px}
.empty .ei{font-size:36px;margin-bottom:10px;opacity:.6}
.extra{margin:18px 0 8px}
.extra summary{cursor:pointer;font-size:12.5px;color:var(--mut);padding:10px 14px;
 background:var(--surf);border:1px solid var(--line);border-radius:12px;list-style:none;transition:background var(--dur-fast)}
.extra summary:hover{background:var(--surf2)}
.extra summary::-webkit-details-marker{display:none}
.extra[open] summary{border-radius:12px 12px 0 0;border-bottom:0}

/* ── 卡组选择 ── */
.decks{display:flex;gap:8px;overflow-x:auto;padding:12px 0 10px;
 -webkit-overflow-scrolling:touch;scrollbar-width:none}
.decks::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;background:var(--surf);border:1px solid var(--line);border-radius:99px;
 padding:7px 14px;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:6px;
 white-space:nowrap;transition:var(--dur-fast);box-shadow:var(--sh)}
.chip:hover{border-color:var(--acc)}
.chip .dot{width:6px;height:6px;border-radius:99px;background:var(--c)}
.chip .n{color:var(--mut);font-size:10.5px;font-variant-numeric:tabular-nums;background:var(--surf2);padding:1px 6px;border-radius:99px}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:700}
.chip.on .dot{background:#fff}
.chip.on .n{background:rgba(255,255,255,.2);color:#fff}

/* ── 翻卡 ── */
.stage{perspective:1400px;margin:8px 0 14px}
.card{position:relative;width:100%;min-height:50vh;transform-style:preserve-3d;
 transition:transform var(--dur-slow) var(--ease-std);cursor:pointer}
.card.flip{transform:rotateY(180deg)}
.face{position:absolute;inset:0;backface-visibility:hidden;-webkit-backface-visibility:hidden;
 background:var(--surf);border:1px solid var(--line);border-radius:20px;box-shadow:var(--sh2);
 display:flex;flex-direction:column;overflow:hidden}
.face.back{transform:rotateY(180deg)}
.f-top{display:flex;align-items:center;gap:8px;padding:14px 18px 0;font-size:11.5px;color:var(--mut)}
.f-top .tag{background:var(--acc);color:#fff;padding:3px 10px;border-radius:99px;font-weight:700;font-size:10.5px}
.f-top .hard{margin-left:auto;font-size:16px;opacity:.35;cursor:pointer;transition:var(--dur-base)}
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
 display:flex;align-items:center;justify-content:center;transition:var(--dur-fast)}
.nav:hover{background:var(--surf2)}
.nav:active{transform:scale(.94)}
.nav:disabled{opacity:.3;cursor:default}
.meter{flex:1}
.meter .mt{display:flex;justify-content:space-between;font-size:11px;color:var(--mut);
 margin-bottom:5px;font-variant-numeric:tabular-nums;font-weight:600}
.mtrack{height:5px;background:var(--surf2);border-radius:99px;overflow:hidden}
.mfill{height:100%;background:var(--acc);border-radius:99px;transition:width var(--dur-base) var(--ease-std)}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.tbtn{background:var(--surf);border:1px solid var(--line);color:var(--fg);border-radius:99px;
 padding:6px 14px;font:inherit;font-size:12px;cursor:pointer;box-shadow:var(--sh);transition:var(--dur-fast);display:flex;align-items:center;gap:5px}
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
.mfl{height:100%;border-radius:99px;width:0;transition:width var(--dur-slow) var(--ease-std)}
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

/* ── 招考与考纲变动雷达 (S7) ── */
.radar-sec{background:var(--surf);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;margin-bottom:14px;box-shadow:var(--sh)}
.radar-sec h3{margin:0 0 10px;font-size:14px;font-weight:700;display:flex;align-items:center;gap:8px;color:var(--fg)}
.radar-badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600}
.radar-badge.add{background:rgba(239,68,68,.12);color:var(--bad);border:1px solid rgba(239,68,68,.25)}
.radar-badge.del{background:rgba(16,185,129,.12);color:var(--ok);border:1px solid rgba(16,185,129,.25)}
.radar-badge.mod{background:rgba(245,158,11,.12);color:var(--warn);border:1px solid rgba(245,158,11,.25)}
.radar-badge.tag{background:var(--surf2);color:var(--mut);border:1px solid var(--line)}
.radar-card{background:var(--surf2);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-top:10px}
.radar-card-h{display:flex;justify-content:space-between;align-items:center;font-weight:700;font-size:13px;margin-bottom:6px}
.radar-stat{display:flex;gap:8px;flex-wrap:wrap;font-size:11.5px;margin:8px 0}

footer{text-align:center;color:var(--mut);font-size:11px;padding:24px 0 12px;opacity:.7}
</style>
</head>
<body>
<a class="skip-link" href="#main">跳到主要看板内容</a>
<!-- 屏幕阅读器播报区：页签切换等状态变化在此播报（视觉不可见） -->
<div id="a11y-live" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>
<div class="wrap">

<header>
  <div class="hd">
    <div class="hl">
      <div class="lb">2026 研考倒计时</div>
      <div class="big">{{DDAY1}}<i>天</i></div>
    </div>
    <div class="hr2">
      <button class="tbtn-theme" id="th-btn" type="button" aria-label="切换深色或浅色主题" title="点击切换深色/浅色模式"><svg viewBox='0 0 24 24' width='14' height='14' stroke='currentColor' stroke-width='2' fill='none' style='margin-right:4px;vertical-align:-2px'><path d='M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z'/></svg>主题模式</button>
      <div class="hr-info">
<!-- [UX 升级] 原为可点击 div：键盘不可达、读屏不识别为控件。
     改为 <button> 并同步 aria-pressed，使其可 Tab 聚焦、Enter/Space 激活。 -->
<button type="button" id="fx-toggle" aria-pressed="true"
        style="font-size:11px;color:var(--mut);background:none;border:0;font:inherit;font-size:11px;display:flex;align-items:center;gap:6px;cursor:pointer;margin-top:4px;padding:2px 0;">✨ 动效已开启 (点击关闭)</button>

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

<main id="main" tabindex="-1">
<div class="subs" id="subs"></div>

<div id="p-today" class="pane on" role="tabpanel" aria-labelledby="tab-today" tabindex="-1">
  <div style="display:flex;justify-content:flex-end;margin:8px 0 4px">
    <button class="tbtn" id="mask-today-btn"><svg viewBox='0 0 24 24' width='14' height='14' stroke='currentColor' stroke-width='2' fill='none' style='margin-right:4px;vertical-align:-2px'><path d='M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'/><circle cx='12' cy='12' r='3'/></svg>开启遮罩自测</button>
  </div>
  {{TODAY}}
</div>

<div id="p-memo" class="pane" role="tabpanel" aria-labelledby="tab-memo" tabindex="-1">
  <div style="display:flex;justify-content:space-between;align-items:center;margin:8px 0 4px">
    <div class="decks" id="dk-memo"></div>
    <button class="tbtn" id="mask-memo-btn" style="flex-shrink:0"><svg viewBox='0 0 24 24' width='14' height='14' stroke='currentColor' stroke-width='2' fill='none' style='margin-right:4px;vertical-align:-2px'><path d='M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'/><circle cx='12' cy='12' r='3'/></svg>遮罩自测</button>
  </div>
  <div id="fc-memo"></div>
  {{MEMONOTES}}
</div>

<div id="p-weak" class="pane" role="tabpanel" aria-labelledby="tab-weak" tabindex="-1">
  <div class="decks" id="dk-weak"></div>
  <div id="fc-weak"></div>
  {{WEAKNOTES}}
</div>

<div id="p-stat" class="pane" role="tabpanel" aria-labelledby="tab-stat" tabindex="-1">
  <div id="stat-trend"></div>
  <div class="leg">
    <span><i style="background:var(--ok)"></i>达标</span>
    <span><i style="background:var(--warn)"></i>接近</span>
    <span><i style="background:var(--bad)"></i>待突破</span>
    <span>│ 竖线 = 目标线</span>
  </div>
  <div id="stat"></div>
</div>

<div id="p-map" class="pane" role="tabpanel" aria-labelledby="tab-map" tabindex="-1">
  <div style="display:flex;justify-content:space-between;align-items:center;margin:8px 0 10px">
    <div class="decks" id="dk-map"></div>
  </div>
  <div id="map-summary-box"></div>
  <div id="map-tree"></div>
</div>

<div id="p-radar" class="pane" role="tabpanel" aria-labelledby="tab-radar" tabindex="-1">
  {{RADAR}}
</div>

</main>

<footer>考研学习看板 · 数据驱动 · 稳扎稳打 · 更新于 {{STAMP}}</footer>
</div>

<nav class="bar" role="tablist" aria-label="看板页签">
  <button role="tab" id="tab-today" aria-controls="p-today" aria-selected="true" tabindex="0" class="on" data-p="today"><i><svg viewBox='0 0 24 24' width='18' height='18' stroke='currentColor' stroke-width='2' fill='none'><rect x='3' y='4' width='18' height='18' rx='2'/><line x1='16' y1='2' x2='16' y2='6'/><line x1='8' y1='2' x2='8' y2='6'/><line x1='3' y1='10' x2='21' y2='10'/></svg></i>今日</button>
  <button role="tab" id="tab-memo" aria-controls="p-memo" aria-selected="false" tabindex="-1" data-p="memo"><i>🧠</i>必背</button>
  <button role="tab" id="tab-weak" aria-controls="p-weak" aria-selected="false" tabindex="-1" data-p="weak"><i>🎯</i>薄弱</button>
  <button role="tab" id="tab-stat" aria-controls="p-stat" aria-selected="false" tabindex="-1" data-p="stat"><i><svg viewBox='0 0 24 24' width='18' height='18' stroke='currentColor' stroke-width='2' fill='none'><line x1='12' y1='20' x2='12' y2='10'/><line x1='18' y1='20' x2='18' y2='4'/><line x1='6' y1='20' x2='6' y2='16'/></svg></i>数据</button>
  <button role="tab" id="tab-map" aria-controls="p-map" aria-selected="false" tabindex="-1" data-p="map"><i>🗺️</i>图谱</button>
  <button role="tab" id="tab-radar" aria-controls="p-radar" aria-selected="false" tabindex="-1" data-p="radar"><i>📡</i>考情</button>
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

/* 遮罩自测按钮的眼睛图标。
   注意：此处必须用**双引号**包裹（SVG 属性用的是单引号），
   且必须经 innerHTML 注入 —— 写成单引号字符串会因属性单引号提前闭合
   而产生 SyntaxError，导致整个 <script> 块失效。 */
var MASK_EYE_SVG = "<svg viewBox='0 0 24 24' width='14' height='14' stroke='currentColor' stroke-width='2' fill='none' style='margin-right:4px;vertical-align:-2px'><path d='M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'/><circle cx='12' cy='12' r='3'/></svg>";

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
       + "<div class='si'>"+s.icon+"</div>"   /* icon 已在构建期经 icon_html 白名单消毒，这里不能再 esc，否则 SVG 源码会被当文本显示 */
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
      btn.innerHTML = on ? '✓ 已开启遮罩自测' : MASK_EYE_SVG + '开启遮罩自测';
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
    if(!d){ host.innerHTML="<div class='empty'><div class='ei'><svg viewBox='0 0 24 24' width='36' height='36' stroke='currentColor' stroke-width='1.6' fill='none' style='display:block;margin:0 auto 10px'><path d='M12 2 2 7l10 5 10-5-10-5z'/><path d='M2 17l10 5 10-5'/><path d='M2 12l10 5 10-5'/></svg></div>暂无卡片数据<br><small>在 _状态/ 中完善表格后会自动生成卡片</small></div>"; return; }
    if(!self.order.length){
      host.innerHTML="<div class='tools'>"+self.toolsHtml()+"</div>"
        +"<div class='empty'><div class='ei'><svg viewBox='0 0 24 24' width='36' height='36' stroke='currentColor' stroke-width='1.6' fill='none' style='display:block;margin:0 auto 10px'><polygon points='12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2'/></svg></div>暂无标记难点<br><small>翻卡时点击右上角星号标记</small></div>";
      self.bindTools(); return;
    }
    var i=self.order[self.ci], c=d.cards[i], C=col(d);
    var hard=!!self.hard[self.key(i)];
    var back=c.b.map(function(kv){
      return "<div class='row'><div class='k'>"+esc(kv[0])+"</div><div class='v'>"+esc(kv[1])+"</div></div>";
    }).join('');
    /* [UX 升级 · 修"空壳卡片"] 默认（脱敏）构建下每张卡的背面 b 都被清空，
       旧实现仍然渲染翻转卡与"答案解析"标签 → 用户点开一看是空白背面，
       表现为"功能坏了"。此处：无背面内容时不再渲染翻转交互，
       改为静态卡并明确说明原因，避免"点了没反应"的挫败感。 */
    var hasBack = !!(c.b && c.b.length);

    host.innerHTML =
      "<div class='tools'>"+self.toolsHtml()+"</div>"
    + "<div class='ctrl'>"
    +   "<button class='nav' id='pv-"+tab+"' aria-label='上一张'>‹</button>"
    +   "<div class='meter' style='--c:"+C+"'><div class='mt'><span>"+esc(d.title)+"</span>"
    +     "<span>"+(self.ci+1)+" / "+self.order.length+"</span></div>"
    +     "<div class='mtrack'><div class='mfill' style='width:"+((self.ci+1)/self.order.length*100)+"%'></div></div></div>"
    +   "<button class='nav' id='nx-"+tab+"' aria-label='下一张'>›</button>"
    + "</div>"
    + "<div class='stage'><div class='card"+(hasBack?'':' noback')+"' id='cd-"+tab+"' style='--c:"+C+"'>"
    +   "<div class='face'>"
    +     "<div class='f-top'><span class='tag'>"+esc(d.subj)+"</span><span>"+esc(d.title)+"</span>"
    +       "<span class='hard"+(hard?' on':'')+"' id='hd-"+tab+"'>"+(hard?'★':'☆')+"</span></div>"
    +     "<div class='f-body'><div class='f-q'>"+esc(c.f)+"</div></div>"
    +     "<div class='f-hint'>"+(hasBack?'点击翻转卡片 · 左右轻扫切换':'答案解析已脱敏 · 本地完整模式可见')+"</div>"
    +   "</div>"
    +   (hasBack
        ? ("<div class='face back'>"
           + "<div class='f-top'><span class='tag'>答案解析</span><span>"+esc(d.title)+"</span></div>"
           + "<div class='f-body'><div class='f-a'>"+back+"</div></div>"
           + "<div class='f-hint'>再次点击返回卡片正面</div>"
           + "</div>")
        : "")
    + "</div></div>";

    var card=document.getElementById('cd-'+tab);
    if(card && hasBack) card.onclick=function(e){ if(e.target.id==='hd-'+tab) return; card.classList.toggle('flip'); if(card.classList.contains('flip') && Math.random()>0.8) fireConfetti(); };
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
    return "<button class='tbtn"+(self.shuffled?' on':'')+"' id='sf-"+self.tab+"'><svg viewBox='0 0 24 24' width='14' height='14' stroke='currentColor' stroke-width='2' fill='none' style='margin-right:4px;vertical-align:-2px'><polyline points='16 3 21 3 21 8'/><line x1='4' y1='20' x2='21' y2='3'/><polyline points='21 16 21 21 16 21'/><line x1='15' y1='15' x2='21' y2='21'/><line x1='4' y1='4' x2='9' y2='9'/></svg>随机打乱</button>"
         + "<button class='tbtn"+(self.onlyHard?' on':'')+"' id='oh-"+self.tab+"'> 重点难点 "+(n?'('+n+')':'')+"</button>"
         + "<button class='tbtn' id='rs-"+self.tab+"'><svg viewBox='0 0 24 24' width='14' height='14' stroke='currentColor' stroke-width='2' fill='none' style='margin-right:4px;vertical-align:-2px'><polyline points='1 4 1 10 7 10'/><path d='M3.51 15a9 9 0 1 0 2.13-9.36L1 10'/></svg>从头开始</button>";
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
    if(c){c.classList.toggle('flip'); if(c.classList.contains('flip') && Math.random()>0.8) fireConfetti(); e.preventDefault();}
  }
});

/* ── 指标进度条 ── */
(function(){
  var host=document.getElementById('stat');
  if(!host) return;
  if(!D.metrics.length){ host.innerHTML="<div class='empty'><div class='ei'><svg viewBox='0 0 24 24' width='36' height='36' stroke='currentColor' stroke-width='1.6' fill='none' style='display:block;margin:0 auto 10px'><line x1='12' y1='20' x2='12' y2='10'/><line x1='18' y1='20' x2='18' y2='4'/><line x1='6' y1='20' x2='6' y2='16'/></svg></div>暂无可视化指标数据<br><small>在薄弱点雷达中记录错因即可生成</small></div>"; return; }
  var h='';
  D.metrics.forEach(function(g){
    var C=col(g);
    h+="<div class='mgrp' style='--c:"+C+"'><h3><span class='ic'>"+g.icon+"</span>"
      +esc(g.subj)+(g.title?("<span class='sep'>·</span>"+esc(g.title)):"")+"</h3>";
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

/* ── 页签切换（无障碍契约） ───────────────────────────────────────────
   [UX 升级] 改造前：纯 onclick，无 role/aria，无键盘漫游，方向键不可用。
   现按 WAI-ARIA Authoring Practices 的 Tabs 模式实现：
     · tablist / tab / tabpanel 语义（标记已在 HTML 中）
     · roving tabindex：仅当前页签 tabindex=0，其余 -1（Tab 键只落一次）
     · ←/→/↑/↓ 循环移动并即时激活；Home/End 跳首尾；Enter/Space 激活
     · aria-selected 与面板显隐同步；切换结果写入 aria-live 区域播报 */
var _tabs = Array.prototype.slice.call(document.querySelectorAll('.bar button[role="tab"]'));
function _activateTab(b, focusIt){
  _tabs.forEach(function(x){
    var on = (x === b);
    x.classList.toggle('on', on);
    x.setAttribute('aria-selected', on ? 'true' : 'false');
    x.setAttribute('tabindex', on ? '0' : '-1');
  });
  document.querySelectorAll('.pane').forEach(function(x){ x.classList.remove('on'); });
  var targetPane = document.getElementById('p-' + b.dataset.p);
  if (targetPane){ targetPane.classList.add('on'); tex(targetPane); }
  if (focusIt) b.focus();
  window.scrollTo(0, 0);
  if (b.dataset.p === 'stat') animate();
  try{ localStorage.setItem('kytab', b.dataset.p); }catch(e){}
  var live = document.getElementById('a11y-live');
  if (live) live.textContent = '已切换到「' + b.textContent.trim() + '」页签';
}
_tabs.forEach(function(b, i){
  b.addEventListener('click', function(){ _activateTab(b, false); });
  b.addEventListener('keydown', function(e){
    var n = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') n = (i + 1) % _tabs.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') n = (i - 1 + _tabs.length) % _tabs.length;
    else if (e.key === 'Home') n = 0;
    else if (e.key === 'End') n = _tabs.length - 1;
    else if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); _activateTab(b, true); return; }
    else return;
    e.preventDefault();
    _activateTab(_tabs[n], true);
  });
});

tex(document.getElementById('p-today'));
/* 减少动态效果时跳过错峰启动动画，直接落到终值 */
if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) animate(); else setTimeout(animate, 180);
/* 动效开关：迁移自原内联 onclick，并维护 aria-pressed 与焦点可达性 */
(function(){
  var fx = document.getElementById('fx-toggle');
  if (!fx) return;
  function render(){
    var off = localStorage.getItem('ky-confetti-off') === '1';
    fx.setAttribute('aria-pressed', off ? 'false' : 'true');
    fx.textContent = off ? '✨ 动效已关闭 (点击开启)' : '✨ 动效已开启 (点击关闭)';
  }
  fx.addEventListener('click', function(){
    var off = localStorage.getItem('ky-confetti-off') === '1';
    localStorage.setItem('ky-confetti-off', off ? '0' : '1');
    render();
  });
  render();
})();
try{
  var t=localStorage.getItem('kytab');
  if(t&&t!=='today'){var el=document.querySelector('.bar button[data-p="'+t+'"]'); if(el)el.click();}
}catch(e){}
</script>

<canvas id="confetti" style="position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9999;"></canvas>
<script>
function fireConfetti() {
  if (localStorage.getItem('ky-confetti-off') === '1' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const canvas = document.getElementById('confetti');
  const ctx = canvas.getContext('2d');
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  const particles = [];
  const colors = ['#8b5cf6', '#10b981', '#f59e0b', '#3b82f6', '#ec4899'];
  for (let i = 0; i < 60; i++) {
    particles.push({
      x: canvas.width / 2, y: canvas.height / 2 + 100,
      r: Math.random() * 6 + 4,
      dx: Math.random() * 10 - 5, dy: Math.random() * -10 - 5,
      color: colors[Math.floor(Math.random() * colors.length)],
      tilt: Math.random() * 10 - 10,
      tiltAngle: 0,
      tiltAngleInc: (Math.random() * 0.07) + 0.05
    });
  }
  let frame = 0;
  function render() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    let active = false;
    particles.forEach(p => {
      p.tiltAngle += p.tiltAngleInc;
      p.y += (Math.cos(p.tiltAngle) + 1 + p.r / 2) / 2;
      p.x += Math.sin(p.tiltAngle) * 2;
      p.dy += 0.2; p.y += p.dy; p.x += p.dx;
      if (p.y <= canvas.height) active = true;
      ctx.beginPath();
      ctx.lineWidth = p.r;
      ctx.strokeStyle = p.color;
      ctx.moveTo(p.x + p.tilt + p.r, p.y);
      ctx.lineTo(p.x + p.tilt, p.y + p.tilt + p.r);
      ctx.stroke();
    });
    if (active && frame < 150) { frame++; requestAnimationFrame(render); }
    else ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
  render();
}
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
    # Default to the publish-safe snapshot. Full personal data requires an explicit opt-out.
    snapshot_mode = os.environ.get("KY_SNAPSHOT_OPT_IN", "1").lower()
    opt_in = snapshot_mode in ("1", "true", "yes", "on")

    snapshot_data = dict(data)  # 浅拷贝

    meta = {
        "snapshot_version": "ky-snapshot/1.1",
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
        print("[WARNING] KY_SNAPSHOT_OPT_IN=0：当前生成完整本地学情快照，请勿提交到公开仓库。")
        print("          例如: set KY_SNAPSHOT_OPT_IN=1 && python build.py   (Windows)")
        print("                 export KY_SNAPSHOT_OPT_IN=1 && python build.py   (macOS/Linux)")
        snapshot_payload = {"meta": meta, "data": snapshot_data}
    else:
        safe_data = _sanitize_public_data(data)
        meta["sanitized"] = True
        snapshot_payload = {"meta": meta, "data": safe_data}
        print("[OK] 已生成脱敏快照（默认安全模式），可安全提交至公开仓库。")

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
