# -*- coding: utf-8 -*-
"""
REPL 渲染层 (renderer.py) —— Rich 结构化渲染

[P3 改造] 本模块此前是「手绘盒线 + 逐行 print Markdown 源码」：
  * ``print_status_summary`` 直接把 AGENTS.md 的行原样打印，用户看到的是
    ``- **目标院校**：`xxx```、``| **科目一** | ... |`` 这类**源码**；
  * 欢迎框/指令面板的 ``╭───╮`` 宽度写死 74/76 列，中文与 emoji 一多就错位
    （原 ``cjk_width`` 算了列宽却零调用，是死代码，本版整体删除）；
  * 青/绿/黄/品红全高亮，命令、说明、状态同一视觉权重。

现统一交给 Rich：
  * Panel / Table 自适应终端宽度（手绘盒线整段移除，宽度错位根治）；
  * 状态大盘改为**结构化解析**：AGENTS.md 的键值行与表格 + ``ky_config.json``
    合并渲染成键值表与目标矩阵表，不再倾倒 Markdown 源码；
  * 语义色由 ``tools/theme`` 的 token 派生（与 GUI/Web/TUI 同源）：
    主色 = acc、成功 = ok、警告 = warn、失败 = bad、说明 = mut；
  * 非 TTY / ``NO_COLOR`` 自动降级（Rich 自带；管道里不会留下转义码）。

[兼容性] ``C`` / ``colorize`` 与全部既有函数签名保持不变 —— 它们被
``tools/cli/repl/loop.py``、``tools/ky_cli.py`` 以及各 commands 模块调用。
"""

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

try:
    from tools.cli.shared import (
        ROOT, SUBJECT_DIRS, COACHING_STYLES, load_config, read_text_safe,
        get_today_tasks_data, is_math_disabled, recommended_checkin_command
    )
except ImportError:
    from cli.shared import (
        ROOT, SUBJECT_DIRS, COACHING_STYLES, load_config, read_text_safe,
        get_today_tasks_data, is_math_disabled, recommended_checkin_command
    )

try:
    import exam_calendar
except ImportError:
    try:
        from tools import exam_calendar
    except ImportError:
        exam_calendar = None

try:
    from skills import list_skills
except ImportError:
    try:
        from tools.skills import list_skills
    except ImportError:
        def list_skills(): return {}

try:
    import intelligence
except ImportError:
    try:
        from tools import intelligence
    except ImportError:
        intelligence = None

# ── 主题 token（四端单一真源；取不到则用内置兜底色，纯外观降级） ─────────
try:
    from tools.theme import (
        apply_to_colors_class as _apply_colors,
        colors_disabled as _theme_colors_disabled,
        load_theme as _load_theme,
    )
except ImportError:                                     # pragma: no cover
    try:
        from theme import (                             # type: ignore
            apply_to_colors_class as _apply_colors,
            colors_disabled as _theme_colors_disabled,
            load_theme as _load_theme,
        )
    except ImportError:
        _apply_colors = None
        _theme_colors_disabled = None
        _load_theme = None

#: 非 TTY（管道 / 重定向 / 测试捕获）时的渲染宽度。
#: Rich 在非终端下固定按 80 列排版，中文长行会被硬折行 —— 折在「数学报到」
#: 这类短语中间既难读、也让既有断言失效。给一个稳定的宽上限。
_PIPE_WIDTH = 100


class C:
    """ANSI 调色板（历史字段名保留，调用点零改动）。

    [P3] 字段值由 ``tools/theme`` 的 token 编译覆盖（见 ``_install_theme_palette``），
    从而与 GUI / Web 看板 / TUI 用同一套色；主题不可用时保留下面的 16 色默认值。
    """

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"


def _install_theme_palette() -> None:
    """把主题 token 编译成 ANSI 调色板并覆盖到 ``C`` 上。"""
    if _apply_colors is None:
        return
    try:
        _apply_colors(C, _load_theme(ROOT))
    except Exception:                                  # pragma: no cover - 纯外观降级
        return


_install_theme_palette()

#: 主题对象按 root 缓存（测试会 monkeypatch ``renderer.ROOT``，故按路径分别缓存）
_THEME_CACHE: Dict[str, object] = {}


def _theme():
    """加载并缓存工作区主题（``ui_theme.json`` 优先，缺失走内置默认预设）。"""
    key = str(ROOT)
    if key not in _THEME_CACHE:
        try:
            _THEME_CACHE[key] = _load_theme(ROOT) if _load_theme else None
        except Exception:                              # pragma: no cover - 纯外观降级
            _THEME_CACHE[key] = None
    return _THEME_CACHE[key]


def _token(name: str, fallback: str) -> str:
    """取主题 token 色值（非十六进制或主题不可用时返回兜底色）。"""
    theme = _theme()
    if theme is not None:
        try:
            return theme.color(name, fallback)
        except Exception:                              # pragma: no cover
            return fallback
    return fallback


def _styles() -> Dict[str, Style]:
    """语义色集合：主色/成功/警告/失败/说明 —— 全部来自主题 token。"""
    return {
        "accent": Style(color=_token("acc", "#a78bfa"), bold=True),
        "accent_plain": Style(color=_token("acc", "#a78bfa")),
        "ok": Style(color=_token("ok", "#34d399")),
        "warn": Style(color=_token("warn", "#fbbf24")),
        "bad": Style(color=_token("bad", "#f87171")),
        "muted": Style(color=_token("mut", "#94a3b8")),
        "value": Style(color=_token("fg", "#f8fafc")),
        "title": Style(color=_token("acc", "#a78bfa"), bold=True),
    }


def _console() -> Console:
    """构造 Rich 控制台。

    [为什么不复用同一个实例] Rich 在**构造时**探测 ``is_terminal`` 并据此决定
    是否上色与定宽，而本进程的输出目标可能在运行期被替换（测试的 capsys、
    ``redirect_stdout``）。每次新建即可跟随当前 ``sys.stdout``；NO_COLOR /
    非 TTY 去色由 Rich 自身处理。
    """
    try:
        is_tty = bool(sys.stdout.isatty())
    except Exception:                                  # pragma: no cover
        is_tty = False
    return Console(highlight=False, markup=False,
                   width=None if is_tty else _PIPE_WIDTH)


def colorize(text: str, color_code: str) -> str:
    """对字符串追加 ANSI 色彩转义字符（Windows 传统 cmd 容错处理）。

    [P3] 去色判定统一走 ``tools/theme`` 的 ``colors_disabled()``：尊重
    ``NO_COLOR`` / ``KY_NO_COLOR`` 约定与非 TTY 管道（此前只看 Windows 环境变量，
    重定向到文件时仍会塞入转义码）。主题包不可用时退回旧判定。
    """
    if not color_code:
        return text
    if _theme_colors_disabled is not None:
        try:
            if _theme_colors_disabled():
                return text
        except Exception:                              # pragma: no cover
            pass
    elif os.name == "nt" and "WT_SESSION" not in os.environ and "TERM" not in os.environ:
        return text
    return f"{color_code}{text}{C.RESET}"


# ══════════════════════════════════════════════════════════════
# Markdown 结构化解析（状态大盘不再倾倒源码的关键）
# ══════════════════════════════════════════════════════════════

#: ``- **键**：值`` 形态的配置行
_MD_BULLET = re.compile(r"^(?P<indent>\s*)-\s+\*\*(?P<key>[^*]+)\*\*\s*[：:]\s*(?P<value>.*)$")
#: ``  - 子键: 值`` 形态的嵌套行（白名单 / 薄弱点等）
_MD_PLAIN_BULLET = re.compile(r"^(?P<indent>\s*)-\s+(?P<key>[^：:*]+)[：:]\s*(?P<value>.*)$")
#: ``| a | b |`` 表格行
_MD_TABLE_ROW = re.compile(r"^\s*\|(?P<cells>.+?)\|\s*$")


def _clean_md(text: str) -> str:
    """去掉 Markdown 装饰（``**加粗**``、``\\`代码\\```、``\\[转义\\]``），只留纯文本。

    这是「状态大盘倾倒 Markdown 源码」的正面修法：结构化读取 + 去装饰，
    而不是把源行原样 ``print`` 出去。
    """
    out = text.replace("**", "")
    out = re.sub(r"`([^`]*)`", r"\1", out)
    out = out.replace("\\[", "[").replace("\\]", "]")
    out = re.sub(r"（示例模板）|\(示例模板\)", "", out)
    return out.strip()


#: 标题行（``#`` ~ ``######``，ATX 允许前置至多 3 空格）
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
#: 列表项 / 有序列表前缀
_MD_LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,2}[.)])\s+")
#: 引用块
_MD_QUOTE = re.compile(r"^\s{0,3}>\s?")


def _md_to_snippet(text: str, limit: int = 160) -> str:
    """把知识库片段（Markdown 原文）压成一行纯文本预览。

    [C5] 检索结果展示的是「给考生看的片段」，不是「给渲染器看的源码」：直接
    回显原文会把 ``#`` / ``##`` / ``**`` / 表格竖线一并倒进终端（P4 验收明确
    要求「无 Markdown 源码泄漏」）。此处逐行去掉块级标记，再交给
    :func:`_clean_md` 去行内装饰，压成单行后按字符数截断。
    """
    parts: List[str] = []
    for raw in text.splitlines():
        line = _MD_QUOTE.sub("", _MD_LIST_ITEM.sub("", _MD_HEADING.sub("", raw)))
        # 表格行：竖线换空格；分隔行（|---|---|）整行丢弃
        if line.count("|") >= 2:
            if set(line.strip()) <= set("|-: "):
                continue
            line = line.replace("|", " ")
        line = line.strip()
        if line:
            parts.append(line)
    snippet = " ".join(_clean_md(" ".join(parts)).split())
    if len(snippet) > limit:
        snippet = snippet[:limit] + "…"
    return snippet


def _md_sections(text: str) -> List[Tuple[str, List[str]]]:
    """按三级标题（``### ``）切分 Markdown，返回 ``[(标题, 正文行), ...]``。"""
    sections: List[Tuple[str, List[str]]] = []
    title = ""
    body: List[str] = []
    for line in text.splitlines():
        if line.startswith("### "):
            if title:
                sections.append((title, body))
            title, body = line[4:].strip(), []
        elif title:
            body.append(line)
    if title:
        sections.append((title, body))
    return sections


def _parse_bullets(lines: Sequence[str]) -> List[Dict[str, object]]:
    """把配置型列表解析成 ``[{"key", "value", "children"}]``（缩进即从属）。"""
    entries: List[Dict[str, object]] = []
    for line in lines:
        match = _MD_BULLET.match(line)
        if match:
            entry: Dict[str, object] = {
                "key": _clean_md(match.group("key")),
                "value": _clean_md(match.group("value")),
                "children": [],
            }
            if match.group("indent") and entries:
                entries[-1]["children"].append(entry)      # type: ignore[attr-defined]
            else:
                entries.append(entry)
            continue
        match = _MD_PLAIN_BULLET.match(line)
        if match and match.group("indent") and entries:
            entries[-1]["children"].append({               # type: ignore[attr-defined]
                "key": _clean_md(match.group("key")),
                "value": _clean_md(match.group("value")),
                "children": [],
            })
    return entries


def _parse_table(lines: Sequence[str]) -> Tuple[List[str], List[List[str]]]:
    """解析 Markdown 管道表，返回 ``(表头, 数据行)``（分隔行自动跳过）。"""
    header: List[str] = []
    rows: List[List[str]] = []
    for line in lines:
        match = _MD_TABLE_ROW.match(line)
        if not match:
            continue
        cells = [_clean_md(cell) for cell in match.group("cells").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):   # |---|---| 分隔行
            continue
        if not header:
            header = cells
        else:
            rows.append(cells)
    return header, rows


def _kv_grid(entries: Sequence[Dict[str, object]]) -> Table:
    """把键值条目渲染成两列表（键用主色加粗，值用前景色；子项缩进一行）。"""
    st = _styles()
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=False)
    table.add_column(overflow="fold")
    for entry in entries:
        children = entry.get("children") or []
        if children:
            table.add_row(Text(str(entry["key"]), style=st["title"]), "")
            for child in children:                       # type: ignore[union-attr]
                table.add_row(Text(f"  {child['key']}", style=st["muted"]),
                              Text(str(child["value"]), style=st["value"]))
        else:
            table.add_row(Text(str(entry["key"]), style=st["title"]),
                          Text(str(entry["value"]), style=st["value"]))
    return table


def _subject_style(subject: str) -> Style:
    """学科色（取自主题的学科色板 token，缺失时退回语义色）。"""
    token, fallback = {
        "math": ("chart-1", "#60a5fa"), "eng": ("chart-2", "#34d399"),
        "pol": ("chart-3", "#fbbf24"), "pro": ("chart-4", "#f472b6"),
    }.get(subject, ("acc", "#a78bfa"))
    return Style(color=_token(token, fallback), bold=True)


# ══════════════════════════════════════════════════════════════
# 欢迎横幅
# ══════════════════════════════════════════════════════════════

#: 品牌横幅（单色渲染：三色彩虹让标题与正文同权重，且与主题主色冲突）
_BANNER = r"""
  ██╗  ██╗ █████╗  ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗     ██████╗██╗     ██╗
  ██║ ██╔╝██╔══██╗██╔═══██╗╚██╗ ██╔╝██╔══██╗████╗  ██║    ██╔════╝██║     ██║
  █████═╝ ███████║██║   ██║ ╚████╔╝ ███████║██╔██╗ ██║    ██║     ██║     ██║
  ██╔═██╗ ██╔══██║██║   ██║  ╚██╔╝  ██╔══██║██║╚██╗██║    ██║     ██║     ██║
  ██║ ╚██╗██║  ██║╚██████╔╝   ██║   ██║  ██║██║ ╚████║    ╚██████╗███████╗██║
  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝     ╚═════╝╚══════╝╚═╝
"""

#: 启动动画步骤（文本 + 每步停顿秒数）
_WELCOME_STEPS = (
    ("装载考研全科中枢总控协议 (AGENTS.md)...", 0.04),
    ("唤醒 {skills}考研专有技能 ({preview})...", 0.04),
    ("启动 Web 实时可视化伴侣 (:{port}/live)...", 0.04),
)


def print_welcome(live_port: int = 8088, animate: bool = True) -> None:
    """启动横幅欢迎大屏与技能唤醒动画（Rich 版：单色品牌横幅 + 键值面板）。"""
    console = _console()
    st = _styles()
    try:
        _skills_map = list_skills() or {}
    except Exception:
        _skills_map = {}
    skill_count = len(_skills_map)
    _preview = [str(k) for k in list(_skills_map.keys())[:6]]
    skill_preview = " / ".join(_preview) if _preview else "Vision/Math/Composer"
    skill_count_text = f"{skill_count} 项" if skill_count else "全部"
    # [B4] 真实状态统计：此前写死"N项全就绪"，技能缺依赖（sympy/pypdf/API Key）时
    # 横幅仍宣称全就绪，与 /skills 面板的真实状态自相矛盾。现在按 health 档位统计，
    # 只有全部 READY 才说"全就绪"。
    _ready_n = sum(1 for sk in _skills_map.values()
                   if (sk.get("health") or {}).get("status") == "READY")
    if skill_count and _ready_n == skill_count:
        skill_status_text = f"{skill_count}项全就绪"
    elif skill_count:
        skill_status_text = f"{_ready_n}/{skill_count} 项就绪"
    else:
        skill_status_text = "已就绪"

    console.print(Text(_BANNER, style=st["accent_plain"]))

    if animate:
        for step, delay in _WELCOME_STEPS:
            text = step.format(skills=skill_count_text, preview=skill_preview,
                               port=live_port)
            sys.stdout.write(f"  ⠋ {text}")
            sys.stdout.flush()
            time.sleep(delay)
            console.print(Text("  ✔ ", style=st["ok"]) + Text(text, style=st["muted"])
                          + Text(" [就绪]", style=st["ok"]))
        console.print()

    today = datetime.now().date()
    exam_date = datetime(today.year, 12, 19).date()
    if today > exam_date:
        exam_date = datetime(today.year + 1, 12, 19).date()
    days_left = (exam_date - today).days

    cfg = load_config()
    curr_subj = cfg.get("active_subject", "math")
    subj_name = SUBJECT_DIRS.get(curr_subj, ("01-数学", "数学"))[1]
    provider = cfg.get("api_provider", "deepseek")
    model_name = cfg.get("model", "deepseek-chat")

    style_tag = "严格把关·保姆流"
    agents_root = ROOT / "AGENTS.md"
    if agents_root.exists():
        txt = read_text_safe(agents_root)
        m = re.search(r"- \*\*当前激活辅导风格\*\*：`([^`]+)`", txt)
        if m:
            raw_s = m.group(1).strip().strip("[]")
            m_s = re.search(r"(\d+\.\s*)?([^\s/\]]+(?:·[^\s/\]]+)?)", raw_s)
            if m_s:
                style_tag = re.sub(r"^\d+\.\s*", "", m_s.group(2)).strip()
            else:
                style_tag = "严格把关保姆流"

    subj_short = subj_name.replace("专属私教", "").replace("私教", "").strip()
    style_short = style_tag.split("·")[0] if "·" in style_tag else style_tag
    # [R2-A4 修复] 不考数学的方案不得在快捷指令速查里列 /math（文科考生输入必被拒）。
    _math_shortcut = "" if is_math_disabled(cfg) else "/math 数学  "

    info = Table.grid(padding=(0, 2))
    info.add_column(no_wrap=True)
    info.add_column(overflow="fold")
    info.add_row(Text("专属私教", style=st["muted"]), Text(subj_short, style=st["ok"]))
    info.add_row(Text("激活风格", style=st["muted"]), Text(style_short, style=st["warn"]))
    info.add_row(Text("初试倒计时", style=st["muted"]),
                 Text(f"{days_left} 天", style=st["accent"]))
    info.add_row(Text("模型", style=st["muted"]),
                 Text(f"{provider}/{model_name}", style=st["value"]))
    info.add_row(Text("网页伴侣", style=st["muted"]),
                 Text(f":{live_port}/live", style=st["value"]))
    info.add_row(Text("技能", style=st["muted"]), Text(skill_status_text, style=st["ok"]))

    shortcuts = Text("快捷指令速查（随时输入 / 展开完整指令大盘）：", style=st["muted"])
    shortcuts.append("\n  ")
    for token in (_math_shortcut, "/eng 英语  ", "/pol 政治  ", "/pro 专业课  "):
        if token:
            shortcuts.append(token.strip() + "  ", style=st["ok"])
    shortcuts.append("\n  ")
    for token in ("/view 网页伴侣  ", "/admission 招考证据  ", "/watch 简章监控  ",
                  "/exam 靶向组卷  ", "/variant 变式检索"):
        shortcuts.append(token, style=st["accent_plain"])
    shortcuts.append("\n  ")
    shortcuts.append("页面排版说明：本终端为纯文本渲染，网页伴侣提供印刷级 KaTeX 公式。",
                     style=st["muted"])

    console.print(Panel(
        Group(info, Text(""), shortcuts),
        title=Text("🎓 考研全科 AI 专属私教终端 · Kaoyan CLI", style=st["title"]),
        border_style=st["accent_plain"],
        box=box.ROUNDED,
    ))

    try:
        import study_planner
        fatigue_info = study_planner.check_fatigue_alert(cfg)
        if fatigue_info.get("alert"):
            message = "\n".join(fatigue_info.get("message", "").splitlines())
            console.print(Panel(
                Text(message, style=st["warn"]),
                title=Text("⚠️ 防疲劳减负保障警报 (Fatigue Protection Alert)",
                           style=st["warn"]),
                border_style=st["warn"],
                box=box.ROUNDED,
            ))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# 指令面板
# ══════════════════════════════════════════════════════════════

def _palette_sections(math_off: bool) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """指令大盘的分节数据：``[(分节标题, [(指令, 说明), ...]), ...]``。

    [R2-A4 修复] 不考数学的方案不得再把 /math 与「数学报到」列在最前（文科考生
    照着输入只会被拒），故按 ``is_math_disabled`` 动态决定是否展示数学路由。
    """
    subject_section: List[Tuple[str, str]] = [
        ("/today", "查看四科今日必做任务清单与完成进度打钩（或直接输入 /done <词>）"),
    ]
    if not math_off:
        subject_section.append(
            ("/math", "切换数学私教（或直接输入「数学报到」/「学数学」）"))
    subject_section += [
        ("/eng", "切换英语私教（或直接输入「英语报到」/「学英语」）"),
        ("/pol", "切换政治私教（或直接输入「政治报到」/「学政治」）"),
        ("/pro", "切换专业课私教（或直接输入「专业课报到」/「学专业课」）"),
    ]
    return [
        ("🎓 学科专属私教路由与每日任务", subject_section),
        ("🧩 考研专有扩展技能 (Skills)", [
            ("/admission <校> [专业]", "研招网与高校官方招考事实与证据链核验（S/A 级权威）"),
            ("/watch [高校]", "跟踪目标高校研究生院最新简章与自命题动态指纹监控雷达"),
            ("/compare <校1> <校2>", "双校招考核心指标横向深度对标（408/自命题/复试线/保护）"),
            ("/scout <高校> [专业]", "目标院校招生简章、大纲、招生人数与知乎/B站口碑侦察"),
            ("/exam [科目]", "错题反向靶向组卷（阶段自测盲盒试卷，支持导出与评分）"),
            ("/variant <考点>", "考研同类真题变式检索与防伪溯源（优先白名单真题，严禁伪造）"),
            ("/map [科目]", "官方考纲知识点图谱与四维掌握度映射（大纲/错题薄弱点对齐）"),
            ("/diagnose <文本>", "整卷级多题诊断与失分聚类引擎（章节失分排行与个性化处方）"),
            ("/diff [路径]", "考纲版本异动 Diff 与考点增删看板（演示样例自动隔离）"),
            ("/ingest <路径>", "试题智能切片入库（分块切片/采分点提取/白名单归档）"),
            ("/review", "FSRS 错题盲盒重测（隐去原答案，独立重做，通过后出库）"),
            ("/hint", "苏格拉底微步骤启发（拒绝全解剧透，分级引导突破口）"),
            ("/done <词>", "快速将今日任务标记为完成并同步回写文件"),
            ("/batch", "客观题答题卡批量对题（快速比对选项，统计正确率与错题归因）"),
            ("/img <路径>", "上传草稿纸或截图，逐行批改、采分点打分与 LaTeX 题干提取"),
            ("/calc <式子>", "数学高精度验算（微分方程/二次型/级数/极限/微积分/矩阵）"),
            ("/dissect <句>", "英语长难句搭积木解剖（主干骨架/从句解构/考点词/润色翻译）"),
            ("/pdf [关键词]", "全文检索四科资料库中的官方教材与历年真题"),
            ("/skills", "查看当前已装载的所有技能详细清单与状态"),
        ]),
        ("🌐 前端联动与外设协同", [
            ("/view", "打开实时可视化网页伴侣（印刷级 KaTeX 排版与双端同步）"),
            ("/notify", "一键向微信、钉钉、飞书、QQ 群广播今日考研晨报与自测卡片"),
            ("/build", "重新编译并刷新本地与手机自测看板（或直接输入「更新看板」）"),
        ]),
        ("⚙️ 终端管理与辅助", [
            ("/style [1-4]", "查看或动态切换 4 种私教辅导风格（严格/秒杀/鼓励/溯源）"),
            ("/doctor", "一键系统健康全链路体检（环境/依赖/状态/连通性）"),
            ("/fatigue", "查看疲劳度与完成率监控警报"),
            ("/relieve", "一键启动智能减负模式（任务下调 25%，切换为鼓励型）"),
            ("/memory [status|prune]", "三级分层记忆健康度查看与滚动修剪"),
            ("/rag <关键词>", "本地知识库混合检索（向量不可用时显式降级并给出原因）"),
            ("/gain", "学习增益代理指标周趋势报告（复测通过率/错因复发/计划完成率，本地落盘）"),
            ("/rollback", "快速回滚 Plan Mode 上一次快照备份"),
            ("/plan", "个人专属定制化必考方案向导（时间/考纲/白名单/学情摸底/作息）"),
            ("/status", "查看考研总战役大盘态势、倒计时与四科目标矩阵"),
            ("/config", "分类多选管理菜单：配置大模型 API 与机器人 Webhook"),
            ("/clear", "清空当前会话上下文"),
            ("/exit", "退出私教终端（落盘记忆与会话钩子）"),
        ]),
    ]


def print_command_palette(cfg: Optional[dict] = None) -> None:
    """打印分类指令面板（两栏布局：指令主色、说明 dim、分节标题）。"""
    if cfg is None:
        try:
            cfg = load_config()
        except Exception:
            cfg = {}
    console = _console()
    st = _styles()
    _math_off = is_math_disabled(cfg)
    _native_cmds = (
        "「查漏」「交作业」「更新看板」「打卡」「组卷」「变式」「知识图谱」「整卷诊断」「减负」"
        if _math_off else
        "「数学报到」「查漏」「交作业」「更新看板」「打卡」「组卷」「变式」「知识图谱」「整卷诊断」「减负」"
    )

    # 指令列 no_wrap：命令是「照着敲」的 token，绝不能被折行拆断
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")
    for index, (section, rows) in enumerate(_palette_sections(_math_off)):
        if index:
            table.add_row(Text(""), Text(""))
        table.add_row(Text(section, style=st["title"]), Text(""))
        for command, desc in rows:
            table.add_row(Text(f"    {command}", style=st["accent_plain"]),
                          Text(desc, style=st["muted"]))

    footer = Text("沙箱: 逻辑隔离（非 OS 沙箱）；读取工作区外文件需在弹卡中授权",
                  style=st["muted"])
    footer.append("\n💡 中文原生口令: ", style=st["muted"])
    footer.append(_native_cmds, style=st["accent_plain"])

    console.print(Panel(
        Group(table, Text(""), footer),
        title=Text("🛠️ 考研私教智能终端 · 指令大盘 (Command Palette)", style=st["title"]),
        border_style=st["accent_plain"],
        box=box.ROUNDED,
    ))


# ══════════════════════════════════════════════════════════════
# 状态大盘
# ══════════════════════════════════════════════════════════════

def print_status_summary() -> None:
    """打印考研总战役大盘态势、打卡 Streak 与周日休整关怀提示。

    [P3 修复·Markdown 源码倾倒] 旧实现逐行 ``print`` AGENTS.md 的原文，
    终端上出现 ``- **目标院校**：`xxx``` 与 ``| **科目一** | ... |`` 这类源码。
    现改为：解析出键值行与管道表 → 渲染成 Rich 键值表 / 目标矩阵表，
    ``ky_config.json`` 的 school/major 覆盖优先（与 ``ky plan`` 同一口径）。
    """
    console = _console()
    st = _styles()
    today_d = date.today()
    today_s = today_d.strftime("%Y-%m-%d")

    cfg = load_config()
    plan = cfg.get("study_plan", {})

    if exam_calendar:
        exam_d, exam_src = exam_calendar.resolve_exam_date(cfg)
    else:
        exam_d, exam_src = datetime(today_d.year, 12, 19).date(), "fallback"
    days_left = (exam_d - today_d).days

    hist = cfg.get("completion_history", {})
    streak = 0
    chk_d = today_d
    if today_s not in hist:
        chk_d = today_d - timedelta(days=1)
    while True:
        ds = chk_d.strftime("%Y-%m-%d")
        if ds in hist and (hist[ds].get("rate", 0.0) >= 60.0 or hist[ds].get("completed", 0) > 0):
            streak += 1
            chk_d -= timedelta(days=1)
        else:
            break

    weekday = today_d.weekday()
    if weekday == 6:
        rest_msg = "今日为周日！系统预定晚间 18:00~22:30 为休整放松窗口，适度给大脑减压，严防考前倦怠！"
        rest_style = st["warn"]
    else:
        days_to_sun = (6 - weekday) % 7
        rest_msg = f"距下次周日休整窗口（周日晚 18:00~22:30）还有 {days_to_sun} 天，按部就班高效攻坚！"
        rest_style = st["muted"]

    overview = Table.grid(padding=(0, 2))
    overview.add_column(no_wrap=True)
    overview.add_column(overflow="fold")
    overview.add_row(Text("今日日期", style=st["muted"]),
                     Text(f"{today_s}（初试首日 {exam_d.strftime('%Y-%m-%d')}）",
                          style=st["value"]))
    overview.add_row(Text("连续打卡", style=st["muted"]),
                     Text(f"{streak} 天（Streak 保持中）", style=st["ok"]))
    overview.add_row(Text("作息节律", style=st["muted"]), Text(rest_msg, style=rest_style))
    if days_left < 0:
        overview.add_row(Text("⚠ 倒计时失效", style=st["warn"]), Text(
            f"配置中的初试日期 {exam_d.isoformat()} 已过期 {-days_left} 天。"
            f"请用 ky plan 重新确认初试日期。", style=st["warn"]))
    elif exam_calendar and exam_src != exam_calendar.SOURCE_CONFIG:
        overview.add_row(Text("i 日期来源", style=st["muted"]), Text(
            f"配置中无明确初试日期，当前按「{exam_src}」取 {exam_d.isoformat()}"
            f"（12 月倒数第二个周六）；如需固定请用 ky plan 确认。", style=st["muted"]))

    console.print(Panel(
        overview,
        title=Text(f"🏆 考研总战役态势大盘 · 倒计时 {days_left} 天", style=st["title"]),
        border_style=st["accent_plain"],
        box=box.ROUNDED,
    ))

    agents_root = ROOT / "AGENTS.md"
    if not agents_root.exists():
        return

    _cfg_school = (plan.get("school") or "").strip()
    _cfg_major = (plan.get("major") or "").strip()
    overrides = {
        "目标院校": _cfg_school if _cfg_school and _cfg_school not in ("目标院校", "未指定") else "",
        "报考专业": _cfg_major if _cfg_major and _cfg_major not in ("报考专业", "未指定") else "",
    }

    txt = read_text_safe(agents_root)
    for title, body in _md_sections(txt):
        entries = _parse_bullets(body)
        if title.startswith("一、"):
            for entry in entries:
                override = overrides.get(str(entry["key"]))
                if override:
                    entry["value"] = override
            console.print(Panel(
                _kv_grid(entries),
                title=Text(_clean_md(title), style=st["title"]),
                border_style=st["accent_plain"],
                box=box.ROUNDED,
            ))
            header, rows = _parse_table(body)
            if header and rows:
                matrix = Table(box=box.SIMPLE_HEAD, expand=True,
                               header_style=st["title"], padding=(0, 1))
                for column in header:
                    matrix.add_column(Text(column), overflow="fold")
                for row in rows:
                    cells = [Text(cell, style=st["value"]) for cell in row]
                    if row and "合计" in row[0]:
                        cells = [Text(cell, style=Style(bold=True)) for cell in row]
                    matrix.add_row(*cells)
                console.print(Panel(
                    matrix,
                    title=Text("📊 各科目标矩阵", style=st["title"]),
                    border_style=st["accent_plain"],
                    box=box.ROUNDED,
                ))
        elif title.startswith("【个性化"):
            console.print(Panel(
                _kv_grid(entries),
                title=Text(_clean_md(title), style=st["title"]),
                border_style=st["accent_plain"],
                box=box.ROUNDED,
            ))


def print_today_tasks_summary(as_json: bool = False, show_flash: bool = True) -> None:
    """读取并打印四科今日真实任务清单，支持终端全彩或结构化 JSON"""
    if as_json:
        print(json.dumps(get_today_tasks_data(), ensure_ascii=False, indent=2))
        return

    console = _console()
    st = _styles()

    # [R2-A4 修复·不考数学贯穿] 科目列表改由 dashboard_state 的权威 specs 驱动
    # （与 `ky today --json`、看板同源），不再硬编码四科 —— 否则不考数学的文科
    # 考生会看到空的【数学】段落。取不到状态时回退为四科，保持既有行为。
    _display = {
        "math": ("01-数学", "数学"),
        "eng": ("02-英语", "英语"),
        "pol": ("03-思想政治理论", "思想政治理论"),
        "pro": ("04-专业课", "专业课"),
    }
    try:
        _active_keys = list((get_today_tasks_data().get("subjects") or {}).keys())
    except Exception:
        _active_keys = []
    subjs = [(key, _display[key][0], _display[key][1])
             for key in ("math", "eng", "pol", "pro") if key in _active_keys]
    if not subjs:
        subjs = [(key, dir_name, label) for key, (dir_name, label) in _display.items()]
    try:
        # [P2-3 修复·研招速递噪音] 三道闸：① ky_config.json 开关
        # (study_plan.news_flash=false 关闭)；② --no-flash 单次关闭；
        # ③ 年份过滤（>2 年的旧公告直接丢弃）+ 每校最多 5 条。
        _flash_on = show_flash
        try:
            _cfg = load_config() or {}
            if (( _cfg.get("study_plan") or {}).get("news_flash") is False):
                _flash_on = False
        except Exception:
            pass
        if _flash_on and intelligence:
            import re as _re
            from datetime import datetime as _dt
            _keep_year = _dt.now().year - 1
            watcher = intelligence.AdmissionWatcher()
            if watcher.list_watched():
                findings = watcher.check_updates()
                updated_findings = [f for f in findings if f.get("status") == "UPDATED"]
                if updated_findings:
                    flash = Text()
                    for uf in updated_findings:
                        _titles = []
                        for tit in uf.get("alert_titles", []):
                            _years = [int(y) for y in _re.findall(r"(?<!\d)(20\d{2})(?!\d)", str(tit))]
                            if _years and max(_years) < _keep_year:
                                continue
                            _titles.append(tit)
                        if not _titles:
                            continue
                        flash.append(f"📢 【{uf['school']}】官方研究生院发布最新招生变动：\n",
                                     style=st["warn"])
                        for tit in _titles[:5]:
                            flash.append(f"     • {tit}\n", style=st["value"])
                        if len(_titles) > 5:
                            flash.append(f"     …等共 {len(_titles)} 条（仅展示最新 5 条）\n",
                                         style=st["muted"])
                    console.print(Panel(
                        flash,
                        title=Text("🔥 研招动态突发情报速递 (Admission News Flash)",
                                   style=st["bad"]),
                        border_style=st["bad"],
                        box=box.ROUNDED,
                    ))
    except Exception:
        pass

    today_str = datetime.now().strftime("%Y-%m-%d")
    console.print(Text(f"📋 今日全科复习任务清单 ({today_str})", style=st["title"]))
    for key, dir_name, label in subjs:
        task_file = ROOT / dir_name / "_状态" / "今日任务.md"
        subject_style = _subject_style(key)
        if task_file.exists():
            content = read_text_safe(task_file)
            console.print(Text(f"  【{label}】", style=subject_style))
            rows = [l.strip() for l in content.splitlines()
                    if "|" in l and not l.replace(" ", "").startswith("|---|")
                    and "完成状态" not in l and "模块" not in l]
            table = Table.grid(padding=(0, 2))
            table.add_column(no_wrap=True)
            table.add_column(overflow="fold")
            table.add_column(no_wrap=True)
            table.add_column(no_wrap=True)
            for line in rows:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 3:
                    done = "[x]" in parts[-1].lower()
                    table.add_row(
                        Text("  [√] " if done else "  [ ] ",
                             style=st["ok"] if done else st["muted"]),
                        Text(parts[0], style=st["value"]),
                        Text(parts[1], style=st["muted"]),
                        Text(f"({parts[2]})", style=st["muted"]),
                    )
            console.print(table)
            console.print()
        else:
            console.print(Text(f"  【{label}】: 暂未生成今日任务，输入 /plan 一键生成。\n",
                               style=subject_style))
    # [R2-A4 修复] 提示口令必须指向真实可用的科目：不考数学时不得再写「数学报到」。
    _checkin = recommended_checkin_command(load_config())
    tip = Text("💡 开始学习口令: 输入 ", style=st["muted"])
    tip.append("[科目]报到", style=st["ok"])
    tip.append(f"（如「{_checkin}」）立即由私教派题；完成输入 ", style=st["muted"])
    tip.append("交作业", style=st["warn"])
    tip.append(" 自动批改打分！", style=st["muted"])
    console.print(tip)


def print_followup_toolbar() -> None:
    """打印输入后的快捷跟随操作工具栏"""
    console = _console()
    st = _styles()
    bar = Text("💡 下一步: ", style=st["muted"])
    for shortcut in ("/1 📐 符号验算", "/2 📌 记错题", "/3 🌐 网页伴侣",
                     "/4 🔄 变式演练", "/5 💡 启发提示"):
        bar.append(f"  {shortcut}", style=st["accent_plain"])
    console.print(Panel(bar, border_style=st["accent_plain"], box=box.ROUNDED))


def print_rag_results(outcome, query: str) -> None:
    """打印本地知识库检索结果（``/rag``、``ky rag`` 共用）。

    [C5] 降级提示是本函数的重点：向量分支不可用时，用户必须一眼看到
    「这次只搜了词法、为什么」——而不是把纯词法结果误当成语义检索结果。
    0 条命中时同样要出提示（故诊断走 ``SearchOutcome``，不挂在结果条目上）。

    Args:
        outcome: ``tools.search.hybrid.SearchOutcome``（鸭子类型，只读
            ``results`` / ``degraded`` / ``degrade_reason`` / ``lexical_count``
            / ``vector_count`` 五个字段，避免渲染层反向依赖检索层）
        query: 原始查询串（回显用）
    """
    console = _console()
    st = _styles()
    results = list(getattr(outcome, "results", []) or [])

    console.print()
    head = Text("🔍 本地知识库检索: ", style=st["muted"])
    head.append(query, style=st["value"])
    head.append(f"  （命中 {len(results)} 条）", style=st["muted"])
    console.print(head)

    # ── 降级提示（先于结果展示，避免用户误读结果性质）──
    if getattr(outcome, "degraded", False):
        reason = getattr(outcome, "degrade_reason", "") or "原因未知"
        warn = Text("⚠️ 已降级：本次仅词法检索", style=st["warn"])
        warn.append(f"\n   {reason}", style=st["muted"])
        warn.append(f"\n   词法召回 {getattr(outcome, 'lexical_count', 0)} 条 · "
                    f"向量召回 {getattr(outcome, 'vector_count', 0)} 条",
                    style=st["muted"])
        console.print(Panel(warn, border_style=st["warn"], box=box.ROUNDED))

    if not results:
        hint = Text("   没有匹配的片段。", style=st["muted"])
        # [C5] 提示必须指向**真实可用**的数据通路：ky ingest 只把题卡归档到
        # 各科 参考资料/ 目录，并不会写知识库；建索引的是 indexer.build_index()
        # （目前没有 ky 子命令，只能直接跑该脚本）。此处不得写「ky ingest 即可检索」。
        hint.append("\n   知识库是空的/未建索引时：先 ky ingest <文件> 把题卡归档，"
                    "再执行 python tools/search/indexer.py 建索引。", style=st["muted"])
        hint.append("\n   已建索引却搜不到，换个更具体的考点关键词再试。", style=st["muted"])
        console.print(hint)
        console.print()
        return

    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)     # 序号
    table.add_column(no_wrap=True)     # 来源
    table.add_column(overflow="fold")  # 片段
    for idx, hit in enumerate(results, 1):
        src = str(getattr(hit, "source", "") or "未知来源")
        text = _md_to_snippet(str(getattr(hit, "text", "")))
        ranks = []
        lex_rank = int(getattr(hit, "lexical_rank", -1))
        vec_rank = int(getattr(hit, "vector_rank", -1))
        if lex_rank >= 0:
            ranks.append(f"词法#{lex_rank + 1}")
        if vec_rank >= 0:
            ranks.append(f"向量#{vec_rank + 1}")
        table.add_row(
            Text(f"{idx}.", style=st["accent_plain"]),
            Text(Path(src).name if src else "未知来源", style=st["value"]),
            Text(text, style=st["muted"]),
        )
        if ranks:
            table.add_row(Text(""), Text(""),
                          Text("   命中: " + " / ".join(ranks), style=st["muted"]))
    console.print(table)
    console.print()


#: 学习增益三类指标的表格列定义：``kind -> ((表头, 取值键, 是否右对齐), ...)``
_GAIN_COLUMNS: Dict[str, Tuple[Tuple[str, str, bool], ...]] = {
    "review_trend": (
        ("周", "week", False), ("复测", "total", True), ("通过", "passed", True),
        ("通过率", "pass_rate", True), ("again", "again", True),
        ("hard", "hard", True), ("good", "good", True), ("easy", "easy", True),
    ),
    "mistake_recurrence": (
        ("错因", "error_type", False), ("次数", "count", True),
        ("涉及天数", "days", True), ("首次", "first", False),
        ("最近", "last", False), ("复发", "recurring", False),
    ),
    "completion_trend": (
        ("周", "week", False), ("打卡天数", "days", True),
        ("完成", "completed", True), ("总任务", "total", True), ("完成率", "rate", True),
    ),
}

#: 需要按百分比展示的列（``0.5 -> "50%"``）
_GAIN_PCT_KEYS = frozenset({"pass_rate", "rate"})


def _gain_cell(row, key: str) -> str:
    """取一行数据的单个单元格并格式化（百分比键转 ``xx%``；recurring 转是/否）。"""
    value = row.get(key, "") if isinstance(row, dict) else getattr(row, key, "")
    if key in _GAIN_PCT_KEYS:
        try:
            return f"{float(value):.0%}"
        except (TypeError, ValueError):
            return "—"
    if key == "recurring":
        return "是" if value else "否"
    return str(value)


def print_learning_gain(report) -> None:
    """打印学习增益代理指标报告（``/gain``、``ky gain`` 共用）。

    [C6] 三条指标全部来自工作区内的既有数据（复测日志 / 错题本 / 打卡历史），
    本函数只渲染、不采集。数据不足的指标**如实显示原因**、不画假趋势 ——
    这份报告是可观测证据而非门禁（无达标线），退出码 2 即「不可评估」。

    Args:
        report: ``tools.benchmarks.learning_gain.GainReport``（鸭子类型，只读
            ``generated_at`` / ``metrics`` / ``saved_to``；每个 metric 只读
            ``kind`` / ``name`` / ``status`` / ``reason`` / ``data`` / ``weeks``，
            避免渲染层反向依赖 benchmarks 层）
    """
    console = _console()
    st = _styles()

    console.print()
    head = Text("📈 学习增益代理指标", style=st["title"])
    head.append(f"   生成时间: {getattr(report, 'generated_at', '') or '未知'}",
                style=st["muted"])
    console.print(head)
    console.print(Text("   口径：通过 = FSRS good/easy · 周 = ISO 周 · "
                       "复发 = 同一错因跨 ≥2 天出现", style=st["muted"]))

    for idx, m in enumerate(list(getattr(report, "metrics", []) or []), start=1):
        console.print()
        kind = str(getattr(m, "kind", "") or "")
        name = str(getattr(m, "name", "") or "未命名指标")
        title = Text(f"{idx}. {name}", style=st["accent"])
        weeks = getattr(m, "weeks", 0) or 0
        if weeks:
            title.append(f"  （{weeks} 周）", style=st["muted"])
        console.print(title)

        if str(getattr(m, "status", "") or "") != "ok":
            console.print(Text(f"   ⚠️ 数据不足：{getattr(m, 'reason', '') or '原因未知'}",
                               style=st["muted"]))
            continue

        data = getattr(m, "data", {}) or {}
        rows = list(data.get("rows", []) or [])
        columns = _GAIN_COLUMNS.get(kind)
        if not columns:
            # 未知 kind（benchmarks 层新增指标而渲染层未同步）：如实说明，
            # 与 learning_gain 的 Markdown 渲染口径（「（无渲染器）」）一致。
            console.print(Text("   （未知指标类型，暂不支持渲染）", style=st["muted"]))
        elif not rows:
            console.print(Text("   （无数据行）", style=st["muted"]))
        else:
            table = Table(box=box.SIMPLE_HEAD, header_style=st["title"], padding=(0, 1))
            for col_head, _key, right in columns:
                table.add_column(Text(col_head),
                                 justify="right" if right else "left", no_wrap=True)
            for row in rows:
                table.add_row(*[Text(_gain_cell(row, key), style=st["value"])
                                for _h, key, _r in columns])
            console.print(table)

        note = data.get("note")
        if note:
            console.print(Text(f"   ℹ️ {note}", style=st["muted"]))
        invalid = list(data.get("invalid") or [])
        if invalid:
            console.print(Text(f"   ⚠️ 已跳过 {len(invalid)} 条不可解析的日志行"
                               "（原文件未改动）", style=st["muted"]))
        skipped = data.get("skipped") or 0
        if skipped:
            console.print(Text(f"   ⚠️ 已跳过 {skipped} 条日期不可解析的记录",
                               style=st["muted"]))
        recurring = list(data.get("recurring_types") or [])
        if recurring:
            console.print(Text("   跨日复发错因：" + "、".join(str(x) for x in recurring),
                               style=st["muted"]))

    console.print()
    saved_to = str(getattr(report, "saved_to", "") or "")
    if saved_to:
        console.print(Text(f"💾 报告已保存至 {saved_to}（本地，不上传）", style=st["ok"]))
    else:
        console.print(Text("💾 （未落盘：--no-save 或只读模式）", style=st["muted"]))
    console.print()
