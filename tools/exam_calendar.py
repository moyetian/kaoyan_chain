# -*- coding: utf-8 -*-
"""
考研初试日期解析与倒计时计算 · 单一事实来源 (Single Source of Truth)

[根因修复·日期硬编码 / 年份语义混淆]
修复前，初试日期在下列位置各自硬编码兜底，彼此数值都不一致：
  - tools/tui_navigator.py    `or "2026-12-19"` / `except: return 104`
  - tools/gui/main_window.py  `"2026-12-19"`    / `except: return 103`
  - tools/agent/memory.py     `plan.get('exam_date', '2026-12-19')`
  - tools/study_planner.py    `plan.get('exam_date', '2026-12-19')`（同文件另一处写 '2027-12-19'）
  - 05-考研看板/build.py       EXAM_DATE=2026-12-20 / EXAM_DAY1=2026-12-19 / PLAN_START=2026-08-09
  - tools/init_workspace.py   f"{year}-12-19"，而 year 默认取 now.year + 1

由此产生三类真实缺陷：
  1) 学员改期后各端倒计时互不相同（GUI 兜底 103 天、TUI 兜底 104 天）；
  2) 硬编码日期一旦过期，倒计时永久失效（TUI/GUI 被 max(0, …) 压成 0）；
  3) 「考研年份（入学年份）」与「初试年份」混用 —— 入学年 N 的初试在 N-1 年 12 月，
     直接用 N 去减会整体偏移一年。

本模块把「初试日期」收敛为唯一入口，供 CLI / TUI / GUI / 看板构建器 / Agent 记忆共用。
考研初试固定为 12 月倒数第二个周六，等价表述为「12 月第 3 个周六」。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "SOURCE_CONFIG",
    "SOURCE_ENROLL_YEAR",
    "SOURCE_CALENDAR",
    "third_saturday_of_december",
    "exam_date_for_exam_year",
    "exam_date_for_enrollment_year",
    "infer_exam_year",
    "resolve_exam_date",
    "countdown_days",
    "countdown_signed",
]

# 初试日期的三个可能来源，供调用方提示学员「这个日期是怎么来的」
SOURCE_CONFIG = "配置"
SOURCE_ENROLL_YEAR = "入学年推算"
SOURCE_CALENDAR = "日历推算"

_SATURDAY = 5  # date.weekday(): Monday=0 ... Saturday=5
_MIN_YEAR = 2000
_MAX_YEAR = 2100


def third_saturday_of_december(year: int) -> date:
    """返回指定年份 12 月的第 3 个周六（= 倒数第二个周六）。

    实测校验：2024 → 12-21、2025 → 12-20、2026 → 12-19，与历年初试日吻合。
    """
    first = date(int(year), 12, 1)
    offset = (_SATURDAY - first.weekday()) % 7
    return first + timedelta(days=offset + 14)


def exam_date_for_exam_year(exam_year: int) -> date:
    """给定「初试年份」返回当年的初试日。"""
    return third_saturday_of_december(exam_year)


def exam_date_for_enrollment_year(enroll_year: int) -> date:
    """给定「考研年份 / 入学年份」返回初试日 —— 头年 12 月初试，次年 9 月入学。"""
    return third_saturday_of_december(int(enroll_year) - 1)


def infer_exam_year(today: Optional[date] = None) -> int:
    """推断「下一个尚未到来的初试年份」（当年初试已过则取次年）。"""
    t = today or date.today()
    return t.year if t <= third_saturday_of_december(t.year) else t.year + 1


def _coerce_date(raw: Any) -> Optional[date]:
    """把配置里的各种日期写法统一解析为 date，解析不出返回 None。"""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _coerce_year(raw: Any) -> Optional[int]:
    """从 "2027" / 2027 / "2027届" 之类的写法里取四位年份。"""
    if raw is None or raw == "":
        return None
    try:
        y = int(str(raw).strip()[:4])
    except (TypeError, ValueError):
        return None
    return y if _MIN_YEAR <= y <= _MAX_YEAR else None


def resolve_exam_date(
    cfg: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
) -> Tuple[date, str]:
    """解析本次备考的初试日期，返回 ``(初试日, 来源说明)``。

    优先级（高 → 低）：
      1. 配置中显式的 ``exam_date`` —— 学员已确认过的日期，最高优先；
      2. 配置中的 ``target_year``（考研年份 / 入学年份）→ 头年 12 月初试；
      3. 日历推算 —— 12 月第 3 个周六，今年已过则顺延一年。

    兼容 ``study_plan.exam_date`` 与顶层 ``exam_date`` 两种存量写法。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    plan = cfg.get("study_plan") if isinstance(cfg.get("study_plan"), dict) else {}

    for raw in (plan.get("exam_date"), cfg.get("exam_date")):
        d = _coerce_date(raw)
        if d:
            return d, SOURCE_CONFIG

    for raw in (plan.get("target_year"), cfg.get("target_year")):
        y = _coerce_year(raw)
        if y:
            return exam_date_for_enrollment_year(y), SOURCE_ENROLL_YEAR

    t = today or date.today()
    return exam_date_for_exam_year(infer_exam_year(t)), SOURCE_CALENDAR


def countdown_days(
    cfg: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
) -> int:
    """距离初试的剩余天数；初试日已过则返回 0，避免出现负数倒计时。"""
    t = today or date.today()
    exam_d, _ = resolve_exam_date(cfg, t)
    return max(0, (exam_d - t).days)


def countdown_signed(
    cfg: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
) -> Tuple[int, date, str]:
    """返回 ``(带符号剩余天数, 初试日, 来源)``。

    供需要识别「配置里的初试日期已过期」的调用方使用（如 ``ky status`` 的告警）。
    """
    t = today or date.today()
    exam_d, source = resolve_exam_date(cfg, t)
    return (exam_d - t).days, exam_d, source
