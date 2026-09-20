# -*- coding: utf-8 -*-
"""
四端共享状态层 (DashboardState) · 单一真源

[缺陷修复·四端各扫各的盘] 修复前 CLI / TUI / GUI / Web 看板各自解析同一批
本地文件，于是同一件事在不同端显示不同的数字。本轮已核实并收敛的四处不一致：

  1. 今日任务勾选解析被写三遍（ky_cli:1389 / tui_navigator:269 / gui:375），
     且 GUI 的表格分隔行判定不认 `| --- |` 这种带空格写法 → 任务总数虚高。
  2. 每日时长预算：TUI 读 `daily_budget_hours`（**全项目无人写入该键**）
     → 永远显示兜底 8.5 小时；而向导实际写入的是 `study_plan.total_hours`
     （本机为 6.8 小时），两端对不上。
  3. 辅导风格键名：曾出现只读顶层 `coaching_style` 而读不到向导写的
     `study_plan.style_name` 的情况，现已统一为
     ``study_plan.style_name → coaching_style → 默认``。
  4. 科目显示名：CLI 从 `study_plan.math_name/eng_name/pro_name` 取，
     TUI/GUI 各自硬编码「数学/英语/政治/专业课」，自命题科目名显示不一致。

本模块把这些「同一份本地数据」的读取规则收敛到一处，四端只负责渲染。
倒计时/初试日不在此重复实现——它已由 :mod:`exam_calendar` 统一提供，
本模块只是代为取值放进同一个状态对象，避免调用方再各自 import。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .task_parser import TaskItem, parse_task_lines, pct

# 双导入路径兼容（项目同时存在 tools.X 与 X 两种运行方式）
try:  # pragma: no cover - 取决于运行方式
    import exam_calendar
except ImportError:  # pragma: no cover
    from tools import exam_calendar  # type: ignore

try:  # pragma: no cover
    from ky_io import read_text_fallback
except ImportError:  # pragma: no cover
    from tools.ky_io import read_text_fallback  # type: ignore

_LOG = logging.getLogger(__name__)

#: 四科目录与稳定键（顺序即界面展示顺序）
SUBJECT_ORDER: Tuple[Tuple[str, str], ...] = (
    ("math", "01-数学"),
    ("eng", "02-英语"),
    ("pol", "03-思想政治理论"),
    ("pro", "04-专业课"),
)

#: 各科兜底显示名（配置缺失时使用；政治无配置项，恒用官方全称）
_FALLBACK_LABELS = {
    "math": "数学",
    "eng": "英语",
    "pol": "思想政治理论",
    "pro": "专业课",
}

#: 每日时长预算的兜底值
_DEFAULT_HOURS = 8.5
#: 辅导风格兜底
_DEFAULT_STYLE = "严格把关·保姆提分型"


@dataclass(frozen=True)
class SubjectSpec:
    """一门科目的「键 / 目录 / 显示名」。"""

    key: str
    folder: str
    label: str


@dataclass(frozen=True)
class SubjectProgress:
    """单科今日任务与完成度。"""

    key: str
    folder: str
    label: str
    tasks: Tuple[TaskItem, ...] = ()

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def completed(self) -> int:
        return sum(1 for t in self.tasks if t.done)

    @property
    def pct(self) -> int:
        return pct(self.completed, self.total)

    @property
    def summary_text(self) -> str:
        """``"2/3 (67%)"`` —— GUI 进度标签与 TUI 摘要共用同一文案。"""
        return f"{self.completed}/{self.total} ({self.pct}%)"


@dataclass(frozen=True)
class DashboardState:
    """四端共享的看板状态快照（不可变，可安全跨线程/跨视图传递）。"""

    date: str                                   # 生成日期 YYYY-MM-DD
    exam_date: str                              # 初试日 ISO
    exam_date_source: str                       # 初试日来源（配置/入学年/日历推算）
    exam_year: int                              # 入学年（= 初试年份 + 1 的语义，由 exam_calendar 给出）
    days_left: int                              # 距初试剩余天数（已过则 0）
    school: str
    major: str
    style: str
    stage: str
    daily_hours: float
    subjects: Tuple[SubjectProgress, ...] = ()

    # ── 汇总 ────────────────────────────────────────────────
    @property
    def total(self) -> int:
        return sum(s.total for s in self.subjects)

    @property
    def completed(self) -> int:
        return sum(s.completed for s in self.subjects)

    @property
    def rate(self) -> float:
        """全局完成率（百分比，保留一位小数）。"""
        total = self.total
        return round(self.completed / total * 100, 1) if total > 0 else 0.0

    @property
    def style_short(self) -> str:
        """风格简称（取「·」前一段），GUI 头部与 TUI 摘要共用。"""
        return (self.style or "").split("·")[0] or _DEFAULT_STYLE

    # ── 查询 ────────────────────────────────────────────────
    def by_key(self, key: str) -> Optional[SubjectProgress]:
        for s in self.subjects:
            if s.key == key:
                return s
        return None

    def progress(self, key: str) -> Tuple[int, int]:
        """返回 ``(已完成, 总数)``；科目不存在时返回 ``(0, 0)``。"""
        s = self.by_key(key)
        return (s.completed, s.total) if s else (0, 0)


# ════════════════════════════════════════════════════════════════
# 配置读取与字段解析
# ════════════════════════════════════════════════════════════════

def load_config(workspace_root: Optional[Path] = None) -> Dict[str, Any]:
    """读取 ky_config.json；缺失或损坏时返回空 dict（调用方走各自兜底）。"""
    root = Path(workspace_root) if workspace_root else _default_root()
    cfg_path = root / "ky_config.json"
    if not cfg_path.exists():
        return {}
    try:
        data = json.loads(read_text_fallback(cfg_path))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        # 用户可见的降级：个性化目标/科目名会全部退回默认值
        _LOG.warning("ky_config.json 解析失败，将使用默认值: %s -> %s", cfg_path, exc)
        return {}


def _default_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _plan(cfg: Dict[str, Any]) -> Dict[str, Any]:
    sp = cfg.get("study_plan")
    return sp if isinstance(sp, dict) else {}


def resolve_subject_specs(cfg: Dict[str, Any]) -> Tuple[SubjectSpec, ...]:
    """按配置解析科目的显示名（支持不考数学、双自命题专业课、199管综等多种模式）。"""
    sp = _plan(cfg)
    exam_mode = sp.get("exam_mode") or cfg.get("exam_mode")
    pro2_name = (sp.get("pro2_name") or cfg.get("pro2_name") or "").strip()
    is_mode_c = exam_mode == "mode_c" or sp.get("pol_disabled") or cfg.get("pol_disabled") or ("199" in str(sp.get("pro_name", "")))
    is_mode_b = exam_mode == "mode_b" or bool(pro2_name)
    math_off = is_mode_b or is_mode_c or sp.get("math_key") == "none" or sp.get("math_name") == "不考数学"

    specs = []
    for key, folder in SUBJECT_ORDER:
        if key == "math" and math_off:
            continue
        if key == "pol" and is_mode_c:
            continue
        label = ""
        if key != "pol":                      # 政治是统考，恒用官方全称
            label = (sp.get(f"{key}_name") or cfg.get(f"{key}_name") or "").strip()
        specs.append(SubjectSpec(
            key=key, folder=folder, label=label or _FALLBACK_LABELS[key]))

    if is_mode_b:
        specs.append(SubjectSpec(
            key="pro2", folder="04-专业课", label=pro2_name or "专业课二"
        ))
    return tuple(specs)


def resolve_school_major(cfg: Dict[str, Any]) -> Tuple[str, str]:
    """目标院校 / 报考专业。兼容 `study_plan.*` 与顶层 `target_*` 两种存量写法。"""
    sp = _plan(cfg)
    school = sp.get("school") or cfg.get("target_school") or "目标院校"
    major = sp.get("major") or cfg.get("target_major") or "报考专业"
    return str(school), str(major)


def resolve_style(cfg: Dict[str, Any]) -> str:
    """辅导风格单一真源：``study_plan.style_name`` → ``coaching_style`` → 默认。

    历史上 TUI/GUI 曾只读顶层 `coaching_style`，而向导写入的是
    `study_plan.style_name`，导致学员选的风格显示不出来。
    """
    sp = _plan(cfg)
    return str(sp.get("style_name")
               or cfg.get("coaching_style")
               or sp.get("coach_style")
               or _DEFAULT_STYLE)


def resolve_daily_hours(cfg: Dict[str, Any]) -> float:
    """每日时长预算（小时）。

    [缺陷修复] TUI 旧实现读 ``cfg["daily_budget_hours"]``，而该键**全项目无人写入**
    → 无论学员怎么配，TUI 恒显示兜底 8.5 小时。真实来源是向导写入的
    ``study_plan.total_hours``（本机 6.8），此处按真实来源优先取值。
    """
    sp = _plan(cfg)
    for raw in (sp.get("total_hours"), cfg.get("daily_budget_hours"),
                cfg.get("total_hours")):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return _DEFAULT_HOURS


def resolve_stage(cfg: Dict[str, Any]) -> str:
    sp = _plan(cfg)
    return str(sp.get("stage_name") or cfg.get("stage") or "强化题型攻坚阶段")


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

def read_subject_tasks(folder: Path, key: str = "") -> Tuple[TaskItem, ...]:
    """读取某科 ``_状态/今日任务.md`` 并解析为任务元组。
    若 key == 'pro2'，优先读取 ``今日任务_专业课二.md``。

    编码回退走 :func:`ky_io.read_text_fallback`（utf-8-sig/utf-8/gbk）——
    与 CLI 既有行为一致；GUI/TUI 旧实现只试 utf-8 且静默吞异常，
    GBK 手写笔记会被当成「没有任务」。

    文件不存在时返回空元组（与三端旧实现一致，不代为回落到模板文件）。
    """
    if key == "pro2":
        task_file = folder / "_状态" / "今日任务_专业课二.md"
        if not task_file.exists():
            task_file = folder / "_状态" / "今日任务.md"
    else:
        task_file = folder / "_状态" / "今日任务.md"
    if not task_file.exists():
        return ()
    try:
        return parse_task_lines(read_text_fallback(task_file))
    except Exception as exc:
        _LOG.warning("今日任务读取失败（按空处理）: %s -> %s", task_file, exc)
        return ()


def load_dashboard_state(
    workspace_root: Optional[Path] = None,
    cfg: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
) -> DashboardState:
    """一次性装填四端共用的状态对象。

    :param workspace_root: 工作区根目录；默认取本仓库根（支持模拟/副本工作区）。
    :param cfg: 已解析的配置；不传则从 ``workspace_root/ky_config.json`` 读取。
    :param today: 注入「今天」以便测试跨天行为。
    """
    root = Path(workspace_root) if workspace_root else _default_root()
    cfg = cfg if isinstance(cfg, dict) else load_config(root)
    now = today or date.today()

    exam_d, exam_src = exam_calendar.resolve_exam_date(cfg, now)
    days_left = max(0, (exam_d - now).days)
    try:
        exam_year = exam_calendar.infer_exam_year(now) + 1
    except Exception:                     # pragma: no cover - 极端兜底
        exam_year = exam_d.year + 1

    school, major = resolve_school_major(cfg)

    subjects = []
    for spec in resolve_subject_specs(cfg):
        subjects.append(SubjectProgress(
            key=spec.key,
            folder=spec.folder,
            label=spec.label,
            tasks=read_subject_tasks(root / spec.folder, key=spec.key),
        ))

    return DashboardState(
        date=now.strftime("%Y-%m-%d"),
        exam_date=exam_d.isoformat(),
        exam_date_source=exam_src,
        exam_year=exam_year,
        days_left=days_left,
        school=school,
        major=major,
        style=resolve_style(cfg),
        stage=resolve_stage(cfg),
        daily_hours=resolve_daily_hours(cfg),
        subjects=tuple(subjects),
    )


def state_to_cli_dict(state: DashboardState) -> Dict[str, Any]:
    """把状态转成 ``ky today --json`` 的既有字典结构（键名与旧实现逐一对齐）。

    [零破坏] 旧结构：``{"date", "subjects": {key: {label, tasks[], total,
    completed}}, "summary": {total, completed, rate}}``，本函数保证一致，
    使 ``ky today --json`` 的消费者与既有测试无需改动。
    """
    subjects: Dict[str, Any] = {}
    for s in state.subjects:
        subjects[s.key] = {
            "label": s.label,
            "tasks": [
                {"module": t.module, "content": t.content,
                 "duration": t.duration, "done": t.done}
                for t in s.tasks
            ],
            "total": s.total,
            "completed": s.completed,
        }
    return {
        "date": state.date,
        "subjects": subjects,
        "summary": {
            "total": state.total,
            "completed": state.completed,
            "rate": state.rate,
        },
    }


__all__ = [
    "DashboardState",
    "SubjectProgress",
    "SubjectSpec",
    "TaskItem",
    "SUBJECT_ORDER",
    "load_config",
    "load_dashboard_state",
    "read_subject_tasks",
    "resolve_daily_hours",
    "resolve_school_major",
    "resolve_stage",
    "resolve_style",
    "resolve_subject_specs",
    "state_to_cli_dict",
]
