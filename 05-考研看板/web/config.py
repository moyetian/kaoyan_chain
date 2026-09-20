# -*- coding: utf-8 -*-
"""
看板配置层：路径、考期、科目与板块映射

[拆分] 原属 build.py 的头部配置区。独立成模块后，解析/渲染模块可以安全地
import 这些常量而不与 build.py 形成循环依赖。
"""

from __future__ import annotations

import datetime
import json
import pathlib
from pathlib import Path

#: 05-考研看板/（本包所在目录）
_PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent
#: 仓库根（ky_config.json、各科目录所在处）
_REPO_ROOT = _PKG_ROOT.parent
def _third_saturday_of_december(year: int) -> datetime.date:
    """考研初试固定为 12 月倒数第二个周六（等价于 12 月第 3 个周六）。"""
    first = datetime.date(int(year), 12, 1)
    return first + datetime.timedelta(days=((5 - first.weekday()) % 7) + 14)
def _load_project_config() -> dict:
    """读取项目根目录的 ky_config.json；读不到返回空 dict 走日历推算。"""
    for cand in (_REPO_ROOT / "ky_config.json", _PKG_ROOT / "ky_config.json",
                 pathlib.Path.cwd() / "ky_config.json"):
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

# [拆分修正] 本模块下沉到 web/ 后，不能再靠 __file__ 反推包目录，
# 否则 OUT 会落到 05-考研看板/web/docs/（曾实测发生）。
ROOT = _PKG_ROOT                       # 05-考研看板/
OUT = ROOT / "docs" / "index.html"     # 内层产物目录
ROOT_DOCS = _REPO_ROOT / "docs" / "index.html"   # Pages 发布的唯一真源

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

_sp = _CONFIG.get("study_plan") if isinstance(_CONFIG.get("study_plan"), dict) else {}
_exam_mode = _sp.get("exam_mode") or _CONFIG.get("exam_mode")
_pro2_name = (_sp.get("pro2_name") or _CONFIG.get("pro2_name") or "").strip()
_is_mode_c = _exam_mode == "mode_c" or _sp.get("pol_disabled") or _CONFIG.get("pol_disabled") or ("199" in str(_sp.get("pro_name", "")))
_is_mode_b = _exam_mode == "mode_b" or bool(_pro2_name)
_math_none = _is_mode_b or _is_mode_c or _sp.get("math_key") == "none" or _sp.get("math_name") == "不考数学"

if _is_mode_c:
    SUBJECTS = [
        {"key": "pro", "name": _sp.get("pro_name") or "199管综", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><path d='M22 12h-4l-3 9L9 3l-3 9H2'/></svg>", "color": "#059669", "dark": "#34d399",
         "dir": PRO, "full": 200, "target": 140, "notes": "每日作业"},
        {"key": "eng", "name": _sp.get("eng_name") or "英语二", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><path d='M4 7V4h16v3M9 20h6M12 4v16'/></svg>", "color": "#e11d48", "dark": "#fb7185",
         "dir": ENG, "full": 100, "target": 70, "notes": "每日笔记"},
    ]
elif _is_mode_b:
    SUBJECTS = [
        {"key": "eng", "name": _sp.get("eng_name") or "英语", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><path d='M4 7V4h16v3M9 20h6M12 4v16'/></svg>", "color": "#e11d48", "dark": "#fb7185",
         "dir": ENG, "full": 100, "target": 65, "notes": "每日笔记"},
        {"key": "pol", "name": "政治", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><circle cx='12' cy='12' r='10'/><path d='M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'/><line x1='2' y1='12' x2='22' y2='12'/></svg>", "color": "#d97706", "dark": "#fbbf24",
         "dir": POL, "full": 100, "target": 70, "notes": None},
        {"key": "pro", "name": _sp.get("pro_name") or "专业课一", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><path d='M22 12h-4l-3 9L9 3l-3 9H2'/></svg>", "color": "#059669", "dark": "#34d399",
         "dir": PRO, "full": 150, "target": 125, "notes": "每日作业"},
        {"key": "pro2", "name": _pro2_name or "专业课二", "icon": "<svg viewBox='0 0 24 24' width='1em' height='1em' stroke='currentColor' stroke-width='2' fill='none'><path d='M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z'/></svg>", "color": "#7c3aed", "dark": "#a78bfa",
         "dir": PRO, "full": 150, "target": 125, "notes": "每日作业"},
    ]
else:
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
    # [P5 修复·math_key=none 贯穿] 不考数学时（math_key=none / math_name=不考数学，
    # 或 mode_b 双专业课 / mode_c 管综），看板不得再渲染数学卡、数学雷达、数学必背
    # 与数学指标。此前 _math_none 仅用于把数学 target 置 0，SUBJECTS/SECTIONS 里的
    # math 条目仍在，文科看板因此保留数学卡与 52 个数学二考点。mode_b/mode_c 分支
    # 本就不含 math 条目，此处过滤只影响纯 math_key=none 场景，不改变既有行为。
    if _math_none:
        SUBJECTS = [s for s in SUBJECTS if s["key"] != "math"]
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
        # [P24 修复·重复抽卡] 此前同时配置「章节掌握度」与「掌握度」两条，
        # 二者都会子串命中同一标题「二、章节掌握度雷达」，导致 weak 页签生成
        # 两份完全相同的卡片组。保留更具体的「章节掌握度」，删除冗余短词。
        ("学情档案.md", "章节掌握度", "weak", {"front": 1}),
        ("学情档案.md", "错题重做队列", "weak", {"front": 1}),
        ("学情档案.md", "错因", "stat", {"label": 0, "value": 1}),
    ],
}

# [P5 修复·math_key=none 贯穿] 不考数学时一并移除数学板块，确保看板不再出现
# 数学必背/数学雷达/数学指标（SUBJECTS 已在上面过滤掉 math）。
if _math_none:
    SECTIONS.pop("math", None)

if _is_mode_b:
    SECTIONS["pro2"] = [
        ("_状态/今日任务_专业课二.md", None, "today", {}),
        ("学情档案.md", "掌握度", "weak", {"front": 1}),
    ]

INDEX_HEADERS = {"#", "编号", "排名", "序号", "代码", "类", "no", "id"}
