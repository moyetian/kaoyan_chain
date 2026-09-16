# -*- coding: utf-8 -*-
"""
卡片与指标构建器（把 Markdown 表格行变成看板卡片 / 进度指标）

正面文字、公式卡提示语、掌握度百分比与目标线都在这里归一化，
是「必背卡/薄弱卡/数据指标」三个页签的数据来源。
"""

from __future__ import annotations

import re

from .config import INDEX_HEADERS

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
