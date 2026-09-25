# -*- coding: utf-8 -*-
"""C6 学习增益代理指标 —— 周趋势报告（本地落盘，不上传）。

红队审查指出的空白：C1–C5 全在测 **AI 正确性**（考纲守卫 / 引文忠实度 /
题源溯源 / 判分一致 / 检索降级），没有一条测**学习增益**。本模块给出三条
最便宜的代理指标 —— 数据均已在工作区内，不联网、不调 LLM：

1. 错题复测通过率周趋势 —— ``.memory/review_log.jsonl``
   （``error_logger.record_review_event`` 在每次复测回写时追加）；
2. 同类错因复发 —— 各科错题本 ``错题记录_*.md`` 里的 ``**错因分类**`` 行；
3. 计划完成率 —— ``ky_config.json`` 的 ``completion_history``。

诚实边界（刻意写死，避免过度解读）：
  * 这是**可观测证据**而非门禁：没有"达标线"，退出码只有 ``0``（至少一条
    指标有数据、报告已生成）与 ``2``（全部指标数据不足，如实不可评估）；
    复用 ``benchmarks.runner`` 的常量以保持全仓口径一致。
  * 「通过」口径 = FSRS 评级 ``good`` / ``easy``（回忆成功）。报告同时给出
    四级原始分布，任何口径都可自行换算。
  * 「复发」是弱判据 = 同一错因出现在 **≥2 个不同日期**；不等于"同一知识点
    再次做错"（那需要题目指纹，本项目暂未采集）。
  * 单条坏行（JSON 损坏 / ts 不可解析）**跳过并计数**，不让整份报告作废 ——
    与评测场景（runner 的 invalid → 退出码 2）刻意不同：那是"数据损坏则评测
    不可信"，这里是"用户日志坏一行，其余数据仍可用"。
  * 报告落盘 ``.memory/learning_gain_report.md``（``.memory`` 在
    ``sync_publish.EXCLUDE_DIRS`` 内，不进公开副本）；严格只读模式
    （``--permission=safe``）下不落盘、只打印。

不做什么：不联网、不调 LLM、不写工作区其它位置、不做达标判定。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:  # 常规：tools/ 在 sys.path（ky CLI / 部分测试）
    from benchmarks.runner import EXIT_INSUFFICIENT, EXIT_OK
except ImportError:  # pragma: no cover - 仓库根在 sys.path
    from tools.benchmarks.runner import EXIT_INSUFFICIENT, EXIT_OK  # type: ignore

try:
    from ky_io import PermissionDeniedError, guard_write, is_read_only_mode
except ImportError:  # pragma: no cover
    from tools.ky_io import (  # type: ignore
        PermissionDeniedError,
        guard_write,
        is_read_only_mode,
    )

try:
    from skills.open_grader import MISTAKE_TYPES
except ImportError:  # pragma: no cover
    from tools.skills.open_grader import MISTAKE_TYPES  # type: ignore

__all__ = [
    "GAIN_REPORT_REL",
    "RATINGS",
    "PASS_RATINGS",
    "GainMetric",
    "GainReport",
    "collect_review_events",
    "collect_mistake_records",
    "collect_completion_history",
    "compute_review_trend",
    "compute_mistake_recurrence",
    "compute_completion_trend",
    "build_report",
    "save_report",
    "run_learning_gain",
]

#: 报告落盘位置（相对工作区根）—— ``.memory`` 在 sync_publish.EXCLUDE_DIRS 内
GAIN_REPORT_REL = (".memory", "learning_gain_report.md")

#: FSRS 四级评级；「通过」= 回忆成功（good/easy）
RATINGS = ("again", "hard", "good", "easy")
PASS_RATINGS = ("good", "easy")

#: 错题本扫描口径（目录命名真源：error_logger.SUBJECT_DIRS；英语科目目录名不同）
_MISTAKE_GLOBS = ("0*/错题本/错题记录_*.md", "0*/错题与长难句本/错题记录_*.md")

_TS_FMT = "%Y-%m-%d %H:%M:%S"
_CARD_HEAD_RE = re.compile(r"^##\s*📌\s*\[(\d{4}-\d{2}-\d{2})\]\s*(.+?)\s*$")
_ERROR_TYPE_RE = re.compile(r"^-\s*\*\*错因分类\*\*：`([^`]+)`")
_STATUS_RE = re.compile(r"^-\s*\*\*掌握状态\*\*：`([^`]+)`")


def _week_key(d: date) -> str:
    """ISO 周键（``2026-W39``）；跨年周归属以 ISO 定义为准（12-29 可能属次年 W01）。"""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _default_root() -> Path:
    """默认工作区根：本文件位于 ``<root>/tools/benchmarks/``。"""
    return Path(__file__).resolve().parents[2]


@dataclass
class GainMetric:
    """单条代理指标。"""

    kind: str  # "review_trend" / "mistake_recurrence" / "completion_trend"
    name: str  # 人类可读标题
    status: str  # "ok" | "insufficient"
    reason: str = ""  # insufficient 时的人话原因
    data: Dict[str, Any] = field(default_factory=dict)
    weeks: int = 0  # 有数据的周数（趋势类指标）

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "weeks": self.weeks,
            "data": self.data,
        }


@dataclass
class GainReport:
    """一次学习增益报告。"""

    generated_at: str
    metrics: List[GainMetric]
    saved_to: str = ""  # 落盘路径（未落盘为空串）

    def any_ok(self) -> bool:
        return any(m.ok for m in self.metrics)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "saved_to": self.saved_to,
            "metrics": [m.to_dict() for m in self.metrics],
        }

    def to_markdown(self) -> str:
        lines = [
            "# 学习增益代理指标报告",
            "",
            f"- 生成时间：{self.generated_at}",
            "- 口径：通过 = FSRS 评级 good/easy；周 = ISO 周；复发 = 同一错因跨 ≥2 天出现。",
            "- 本报告仅存本地（`.memory/`），不上传；作为学习效果的可观测证据，不作为门禁。",
            "",
        ]
        renderers = {
            "review_trend": _md_review_trend,
            "mistake_recurrence": _md_mistake_recurrence,
            "completion_trend": _md_completion_trend,
        }
        for idx, m in enumerate(self.metrics, start=1):
            lines.append(f"## {idx}. {m.name}")
            lines.append("")
            if not m.ok:
                lines.append(f"数据不足：{m.reason}")
                lines.append("")
                continue
            lines.extend(renderers.get(m.kind, lambda _m: ["（无渲染器）"])(m))
            lines.append("")
        # [设计] 不在正文里写自身路径（``saved_to`` 页脚）：落盘时 ``save_report``
        # 先渲染后赋值，页脚永远进不了文件；stdout 的落盘提示由调用方（renderer）
        # 承担。去掉这半死分支，避免"文件里没有、打印时却有"的不一致。
        return "\n".join(lines) + "\n"


def _md_cell(value: Any) -> str:
    """Markdown 表格单元格转义（``|`` 会截断列；端到端实测错因名含 ``|`` 破表）。"""
    return str(value).replace("|", "\\|")


def _md_review_trend(m: GainMetric) -> List[str]:
    rows = m.data.get("rows", [])
    out = [
        f"共 {m.data.get('events', 0)} 次复测、{m.weeks} 周。",
        "",
        "| 周 | 复测 | 通过 | 通过率 | again | hard | good | easy |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        out.append(
            f"| {_md_cell(r['week'])} | {r['total']} | {r['passed']} | {r['pass_rate']:.0%} | "
            f"{r['again']} | {r['hard']} | {r['good']} | {r['easy']} |"
        )
    note = m.data.get("note")
    if note:
        out.extend(["", f"> {note}"])
    inv = m.data.get("invalid") or []
    if inv:
        out.extend(["", f"> 另有 {len(inv)} 行日志不可解析已跳过（原文件未改动）。"])
    return out


def _md_mistake_recurrence(m: GainMetric) -> List[str]:
    rows = m.data.get("rows", [])
    out = [
        f"共 {m.data.get('total_records', 0)} 条错题记录。",
        "",
        "| 错因 | 次数 | 涉及天数 | 首次 | 最近 | 复发 |",
        "|---|---:|---:|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| {_md_cell(r['error_type'])} | {r['count']} | {r['days']} | {r['first']} | "
            f"{r['last']} | {'是' if r['recurring'] else '否'} |"
        )
    rec = m.data.get("recurring_types") or []
    if rec:
        out.extend(["", f"> 跨日复发错因：{'、'.join(rec)}。"])
    if m.data.get("skipped"):
        out.extend(["", f"> 另有 {m.data['skipped']} 条记录日期不可解析已跳过。"])
    return out


def _md_completion_trend(m: GainMetric) -> List[str]:
    rows = m.data.get("rows", [])
    out = [
        f"共 {m.data.get('days', 0)} 天打卡记录、{m.weeks} 周。",
        "",
        "| 周 | 打卡天数 | 完成 | 总任务 | 完成率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        out.append(
            f"| {r['week']} | {r['days']} | {r['completed']} | {r['total']} | {r['rate']:.0%} |"
        )
    return out


def collect_review_events(
    path: Path,
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """读取复测事件日志（容错口径与 runner.load_cases 同款：坏行跳过并计数）。

    Returns:
        ``(events, invalid)``；events 每条含 ``date``(date) / ``subject`` /
        ``title`` / ``rating``；invalid 为 ``(行号字符串, 原因)``。
    """
    events: List[Dict[str, Any]] = []
    invalid: List[Tuple[str, str]] = []
    p = Path(path)
    if not p.exists():
        return events, invalid
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            invalid.append((f"line:{lineno}", f"JSON 解析失败: {e.msg}"))
            continue
        if not isinstance(obj, dict):
            invalid.append((f"line:{lineno}", f"顶层不是对象: {type(obj).__name__}"))
            continue
        ts = str(obj.get("ts", "") or "").strip()
        try:
            day = datetime.strptime(ts, _TS_FMT).date()
        except ValueError:
            invalid.append((f"line:{lineno}", f"ts 不可解析: {ts!r}"))
            continue
        rating = str(obj.get("rating", "") or "").strip().lower()
        if rating not in RATINGS:
            invalid.append((f"line:{lineno}", f"rating 非法: {rating!r}"))
            continue
        events.append(
            {
                "date": day,
                "subject": str(obj.get("subject", "") or ""),
                "title": str(obj.get("title", "") or ""),
                "rating": rating,
            }
        )
    return events, invalid


def collect_mistake_records(root: Path) -> List[Dict[str, Any]]:
    """扫描各科错题本，抽取每条错题的日期 / 错因 / 掌握状态。

    解析口径：卡片头 ``## 📌 [YYYY-MM-DD] 标题`` + ``**错因分类**：`X```
    （``error_logger.log_error_record`` 的落盘格式，单一真源）。
    """
    root = Path(root)
    records: List[Dict[str, Any]] = []
    for pattern in _MISTAKE_GLOBS:
        for f in sorted(root.glob(pattern)):
            subject = f.parent.parent.name  # 如 "01-数学"
            text = f.read_text(encoding="utf-8", errors="replace")
            cur: Optional[Dict[str, Any]] = None
            for raw in text.splitlines():
                m = _CARD_HEAD_RE.match(raw)
                if m:
                    if cur:
                        records.append(cur)
                    cur = {
                        "subject": subject,
                        "date": m.group(1),
                        "title": m.group(2),
                        "error_type": "",
                        "status": "",
                        "file": f.name,
                    }
                    continue
                if cur is None:
                    continue
                m = _ERROR_TYPE_RE.match(raw)
                if m and not cur["error_type"]:
                    cur["error_type"] = m.group(1).strip()
                m = _STATUS_RE.match(raw)
                if m and not cur["status"]:
                    cur["status"] = m.group(1).strip()
            if cur:
                records.append(cur)
    return records


def collect_completion_history(cfg_path: Path) -> Dict[str, Dict[str, Any]]:
    """读取 ``ky_config.json`` 的 ``completion_history``。

    文件缺失 / JSON 损坏 / 结构不符 → 返回空 dict（报告层如实显示"数据不足"）。
    """
    p = Path(cfg_path)
    if not p.exists():
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    hist = cfg.get("completion_history") if isinstance(cfg, dict) else None
    if not isinstance(hist, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in hist.items():
        if isinstance(v, dict) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(k)):
            out[str(k)] = v
    return out


def compute_review_trend(
    events: List[Dict[str, Any]],
    invalid: Optional[List[Tuple[str, str]]] = None,
) -> GainMetric:
    """错题复测通过率周趋势（通过 = good/easy）。"""
    name = "错题复测通过率（周趋势）"
    invalid = list(invalid or [])
    if not events:
        reason = "尚无复测事件"
        if invalid:
            reason += f"（另有 {len(invalid)} 行不可解析已跳过）"
        reason += "。完成一次错题复测（交作业 / 盲盒复测）后自动产生记录。"
        return GainMetric(
            kind="review_trend",
            name=name,
            status="insufficient",
            reason=reason,
            data={"invalid": [{"line": c, "reason": r} for c, r in invalid]},
        )
    weeks: Dict[str, Dict[str, int]] = {}
    for ev in events:
        bucket = weeks.setdefault(_week_key(ev["date"]), {r: 0 for r in RATINGS})
        bucket[ev["rating"]] += 1
    rows = []
    for wk in sorted(weeks):
        b = weeks[wk]
        total = sum(b.values())
        passed = sum(b[r] for r in PASS_RATINGS)
        rows.append(
            {
                "week": wk,
                "total": total,
                "passed": passed,
                "pass_rate": round(passed / total, 4) if total else 0.0,
                "again": b["again"],
                "hard": b["hard"],
                "good": b["good"],
                "easy": b["easy"],
            }
        )
    data: Dict[str, Any] = {
        "rows": rows,
        "events": len(events),
        "pass_rule": "good/easy",
        "invalid": [{"line": c, "reason": r} for c, r in invalid],
    }
    if len(rows) < 2:
        data["note"] = f"仅 {len(rows)} 周数据，趋势判读意义有限（继续记录后自然改善）"
    return GainMetric(kind="review_trend", name=name, status="ok", weeks=len(rows), data=data)


def compute_mistake_recurrence(records: List[Dict[str, Any]]) -> GainMetric:
    """同类错因分组计数 + 跨日复发标记（弱判据，见模块 docstring）。"""
    name = "同类错因复发"
    if not records:
        return GainMetric(
            kind="mistake_recurrence",
            name=name,
            status="insufficient",
            reason="各科错题本暂无错题记录。交作业判分出现错误后自动归档。",
        )
    skipped = 0
    groups: Dict[str, Dict[str, Any]] = {}
    days_by_type: Dict[str, set] = {}
    for rec in records:
        try:
            d = date.fromisoformat(str(rec.get("date", "")))
        except ValueError:
            skipped += 1
            continue
        et = str(rec.get("error_type", "") or "") or "未标注"
        g = groups.setdefault(et, {"count": 0, "first": d, "last": d, "statuses": {}})
        g["count"] += 1
        g["first"] = min(g["first"], d)
        g["last"] = max(g["last"], d)
        st = str(rec.get("status", "") or "") or "未知"
        g["statuses"][st] = g["statuses"].get(st, 0) + 1
        days_by_type.setdefault(et, set()).add(d)
    if not groups:
        return GainMetric(
            kind="mistake_recurrence",
            name=name,
            status="insufficient",
            reason=f"共 {len(records)} 条错题记录但日期均不可解析，无法聚合。",
            data={"skipped": skipped},
        )
    # 固定顺序：MISTAKE_TYPES 内的错因优先（按五分类原序），未知错因排后
    known = [t for t in MISTAKE_TYPES if t in groups]
    unknown = [t for t in groups if t not in MISTAKE_TYPES]
    order = known + unknown
    rows = []
    for et in order:
        g = groups[et]
        days = len(days_by_type[et])
        rows.append(
            {
                "error_type": et,
                "count": g["count"],
                "days": days,
                "first": g["first"].isoformat(),
                "last": g["last"].isoformat(),
                "recurring": days >= 2,
                "statuses": g["statuses"],
            }
        )
    return GainMetric(
        kind="mistake_recurrence",
        name=name,
        status="ok",
        data={
            "rows": rows,
            "total_records": len(records) - skipped,
            "recurring_types": [r["error_type"] for r in rows if r["recurring"]],
            "skipped": skipped,
        },
    )


def compute_completion_trend(history: Dict[str, Dict[str, Any]]) -> GainMetric:
    """计划完成率按周聚合（周完成率 = sum(completed) / sum(total)）。"""
    name = "计划完成率（周）"
    if not history:
        return GainMetric(
            kind="completion_trend",
            name=name,
            status="insufficient",
            reason="ky_config.json 无 completion_history 记录"
            "（文件缺失、损坏或尚未打卡；完成一次今日任务打卡后自动产生）。",
        )
    weeks: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    for day_str in sorted(history):
        try:
            d = date.fromisoformat(day_str)
        except ValueError:
            skipped += 1
            continue
        b = weeks.setdefault(_week_key(d), {"days": 0, "total": 0, "completed": 0, "rates": []})
        b["days"] += 1
        rec = history[day_str]

        def _as_int(key: str) -> int:
            try:
                return int(rec.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        b["total"] += _as_int("total")
        b["completed"] += _as_int("completed")
        try:
            rate_val: Optional[float] = float(rec.get("rate", 0.0) or 0.0)
        except (TypeError, ValueError):
            rate_val = None  # 脏 rate 值跳过（total/completed 仍参与加权口径）
        if rate_val is not None:
            b["rates"].append(rate_val)
    rows = []
    for wk in sorted(weeks):
        b = weeks[wk]
        if b["total"] > 0:
            rate = b["completed"] / b["total"]
        elif b["rates"]:
            rate = sum(b["rates"]) / len(b["rates"])
        else:
            rate = 0.0
        rows.append(
            {
                "week": wk,
                "days": b["days"],
                "total": b["total"],
                "completed": b["completed"],
                "rate": round(rate, 4),
            }
        )
    if not rows:  # 直调边界：日期键均不可解析时不得返回"ok 但零行"的矛盾状态
        return GainMetric(
            kind="completion_trend",
            name=name,
            status="insufficient",
            reason=f"completion_history 共 {len(history)} 条记录但日期键均不可解析，无法聚合。",
            data={"skipped": skipped},
        )
    return GainMetric(
        kind="completion_trend",
        name=name,
        status="ok",
        weeks=len(rows),
        data={"rows": rows, "days": len(history) - skipped, "skipped": skipped},
    )


def build_report(root: Optional[Path] = None) -> GainReport:
    """在工作区内收集三类数据并生成报告（不落盘；落盘见 ``save_report``）。"""
    root = Path(root) if root else _default_root()
    events, invalid = collect_review_events(root / ".memory" / "review_log.jsonl")
    records = collect_mistake_records(root)
    history = collect_completion_history(root / "ky_config.json")
    metrics = [
        compute_review_trend(events, invalid),
        compute_mistake_recurrence(records),
        compute_completion_trend(history),
    ]
    return GainReport(generated_at=datetime.now().strftime(_TS_FMT), metrics=metrics)


def save_report(report: GainReport, path: Path) -> Path:
    """把报告写入磁盘（过 ky_io 统一写闸门；只读模式下抛 ``PermissionDeniedError``）。"""
    path = Path(path)
    guard_write("写入学习增益报告", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_markdown(), encoding="utf-8")
    return path


def run_learning_gain(
    save: bool = True, root: Optional[Path] = None
) -> Tuple[GainReport, int]:
    """生成报告并按需落盘。

    Returns:
        ``(report, exit_code)``；退出码 ``0`` = 至少一条指标有数据、
        ``2`` = 全部数据不足（如实不可评估）。严格只读模式下 ``save``
        被忽略（不落盘、只打印），不算错误。
    """
    root = Path(root) if root else _default_root()
    report = build_report(root)
    if save:
        target = root.joinpath(*GAIN_REPORT_REL)
        try:
            if not is_read_only_mode():
                report.saved_to = str(save_report(report, target))
        except PermissionDeniedError:
            report.saved_to = ""  # 只读模式竞态兜底：闸门拒绝即视为不落盘
    code = EXIT_OK if report.any_ok() else EXIT_INSUFFICIENT
    return report, code
