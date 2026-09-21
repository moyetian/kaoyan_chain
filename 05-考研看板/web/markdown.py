# -*- coding: utf-8 -*-
"""
轻量 Markdown 解析与切片（零第三方依赖）

看板数据源是各科 `_状态/*.md`，需要把「章节 + 表格」切成卡片与指标，
并把正文渲染成 HTML。全部用标准库正则实现，不引入 markdown 库。
"""

from __future__ import annotations

import html
import pathlib
import re
from pathlib import Path

from .config import INDEX_HEADERS, SUBJECTS

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


#: 章节标题的编号前缀（「一、」「2.」「3)」等），精确匹配时先行剥离
_ENUM_PREFIX = re.compile(r"^\s*(?:[0-9]+|[一二三四五六七八九十百]+)\s*[、.．:：)）\-]\s*")


def _is_exact_heading(title: str, kw: str) -> bool:
    """标题（去编号前缀后）是否与关键词全等 —— 精确章节名匹配。"""
    t = title.strip()
    return t == kw or _ENUM_PREFIX.sub("", t).strip() == kw


def get_section(md, kw):
    if md is None:
        return None
    if kw is None:
        return md
    lines = md.splitlines()
    start = lvl = None
    # [P24 修复·精确章节名匹配] 第一遍只认「去编号前缀后全等」的精确命中。
    # 旧实现单遍子串匹配时，pro 同时配置的「章节掌握度」与「掌握度」都会命中
    # 标题「二、章节掌握度雷达」，导致同一章节被重复抽卡成两份相同卡片组。
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{2,4})\s+(.*)$", ln)
        if m and _is_exact_heading(m.group(2), kw):
            start, lvl = i, len(m.group(1))
            break
    # 第二遍回退子串匹配：保留「长难句」「公式默写卡」「马原」「核心概念」等
    # 描述性关键词命中完整章节标题的既有能力（这些并非精确章节名）。
    if start is None:
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
        if not buf:
            # [P0-4 修复·死循环] 走到这里说明当前行命中了上面的段落守卫（最典型的是
            # 「以 | 开头、但下一行不是合法表格分隔行」的游离竖线行 / 写错的表格头），
            # 却没有任何前置分支消费它 —— 此时 while 体一次都不执行、buf 为空、i 不推进，
            # 外层 `while i < n` 会原地空转（CPU 100%，实测 8s 超时被强杀）。兜底把该行
            # 当普通段落输出并显式推进 i：既不丢内容，也保证主循环每轮必定前进。
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")
    return "\n".join(out)
