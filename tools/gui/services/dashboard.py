# -*- coding: utf-8 -*-
"""
GUI 数据服务层（纯数据，不依赖 Qt）

把「读盘 + 组装文本」从 MainWindow 里挪出来，好处有三：
  1. 不再与界面耦合 —— 可以在无图形环境（离屏/CI）下直接单测；
  2. 数据来源统一走 tools/state 共享层，与 CLI / TUI / 看板同源；
  3. MainWindow 只需「取数据 → 塞进控件」，不再同时承担解析逻辑。

本模块只返回数据（字符串 / 数据对象），不创建任何 Qt 控件。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 双导入路径兼容
try:  # pragma: no cover
    import exam_calendar
except ImportError:  # pragma: no cover
    from tools import exam_calendar  # type: ignore

try:  # pragma: no cover
    from state import DashboardState, SubjectProgress, load_dashboard_state
except ImportError:  # pragma: no cover
    from tools.state import (  # type: ignore
        DashboardState, SubjectProgress, load_dashboard_state,
    )

_LOG = logging.getLogger(__name__)

#: 科目目录（GUI 侧展示顺序）
SUBJECT_DIRS: Tuple[Tuple[str, str], ...] = (
    ("01-数学", "数学"),
    ("02-英语", "英语"),
    ("03-思想政治理论", "政治"),
    ("04-专业课", "专业课"),
)

# ── [PF-1 性能修复·审计 2026-09-30] load_state 的 mtime 缓存 ──────────────
# 一次 ``MainWindow._refresh_all`` 会经 header_info / subject_progress /
# error_queue_cards / error_queue_markdown 连调 ``load_state`` 5 次，
# 每次都重读 ky_config.json 与四科今日任务.md（实测一次刷新约 20 次状态文件读）。
# 现按「输入文件指纹 + 当天日期」缓存：文件未变（mtime_ns + size 相同）且
# 未跨天时直接复用上次结果。模式参照 privacy_policy 的院校库 mtime 缓存。

#: ``load_dashboard_state`` 的磁盘输入（相对工作区根）：配置 + 四科今日任务。
#: 多收指纹（如本不存在的 pro2 任务文件）只会让缓存更保守地失效，不会漏判。
_STATE_INPUT_RELS: Tuple[str, ...] = ("ky_config.json",) + tuple(
    f"{folder}/_状态/{name}"
    for folder, _name in SUBJECT_DIRS
    for name in ("今日任务.md", "今日任务_专业课二.md")
)

#: ``{(root, 日期, 指纹): DashboardState|None}``。上限 8 条，超了整体清空
#: （键含 mtime 与日期，天然失效）。
_STATE_CACHE: Dict[Tuple[str, str, tuple], Optional["DashboardState"]] = {}
_STATE_CACHE_MAX = 8


def _input_fingerprint(root: Path) -> Tuple[tuple, ...]:
    """``load_dashboard_state`` 输入文件的 ``(路径, mtime_ns, size)`` 指纹。"""
    parts = []
    for rel in _STATE_INPUT_RELS:
        try:
            st = (root / rel).stat()
            parts.append((rel, st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((rel, None, None))
    return tuple(parts)


def load_state(workspace_root: Path) -> Optional[DashboardState]:
    """读取共享状态；失败返回 None（界面按空态渲染，不崩）。

    [PF-1 性能修复·审计 2026-09-30] 带 mtime 缓存：输入文件（ky_config.json +
    四科今日任务）的 mtime_ns/size 与当天日期都未变时，直接复用上次结果 ——
    一次 ``_refresh_all`` 从 5 次真实读盘收敛为 1 次。

    日期必须进入缓存键：倒计时按天推进，即使考生当天没有改写任何状态文件，
    ``days_left`` 也要刷新（状态文件被重写只是额外的失效路径，不是唯一路径）。
    状态对象及其子对象均为 frozen dataclass，调用方只读，可安全共享。
    """
    root = Path(workspace_root)
    key = (str(root), date.today().isoformat(), _input_fingerprint(root))
    if key in _STATE_CACHE:
        return _STATE_CACHE[key]
    try:
        state: Optional[DashboardState] = load_dashboard_state(root)
    except Exception as exc:
        _LOG.warning("看板状态加载失败: %s -> %s", root, exc)
        state = None
    if len(_STATE_CACHE) >= _STATE_CACHE_MAX:
        _STATE_CACHE.clear()
    _STATE_CACHE[key] = state
    return state


def countdown_days(workspace_root: Path) -> int:
    """距初试剩余天数。

    [根因修复·日期硬编码] 旧实现兜底初试日写死 "2026-12-19"、异常返回魔法数
    103，与 TUI（104）、CLI（动态推算）三端互不一致且过期后永久失效。
    现统一委托 exam_calendar（配置 → 入学年 → 日历推算）。
    """
    state = load_state(workspace_root)
    if state is not None:
        return state.days_left
    return exam_calendar.countdown_days(None)


def subject_progress(workspace_root: Path) -> Tuple[SubjectProgress, ...]:
    """各科今日任务进度（用于进度条）。"""
    state = load_state(workspace_root)
    return state.subjects if state else ()


def subject_labels(workspace_root: Path) -> List[Tuple[str, str, str]]:
    """返回 ``[(key, 目录名, 显示名), ...]``，显示名取自配置（自命题科目可自定义）。"""
    state = load_state(workspace_root)
    if state is None:
        return [(k, folder, folder) for folder, k in
                (("01-数学", "math"), ("02-英语", "eng"),
                 ("03-思想政治理论", "pol"), ("04-专业课", "pro"))]
    folder_of = {k: f for k, f in (("math", "01-数学"), ("eng", "02-英语"),
                                   ("pol", "03-思想政治理论"), ("pro", "04-专业课"))}
    return [(s.key, folder_of.get(s.key, s.folder), s.label) for s in state.subjects]


# ── 错题复测队列 ────────────────────────────────────────────────

def error_queue_markdown(workspace_root: Path) -> str:
    """扫描各科错题本，生成「待复测队列」Markdown 文本。"""
    lines = [
        "# FSRS 记忆稳定性曲线 · 到期错题复测队列",
        f"> 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
    ]
    total_due = 0
    for folder, name in SUBJECT_DIRS:
        mistake_dir = workspace_root / folder / "错题本"
        if mistake_dir.exists():
            due_files = [f for f in mistake_dir.glob("*.md")
                         if not f.stem.startswith("自测卷_") and not f.stem.startswith("_")]
            lines.append(f"### {name}错题本: 共 {len(due_files)} 道错题档案")
            total_due += len(due_files)
            for f in due_files[:3]:
                lines.append(f"- `{f.stem}`")
            if len(due_files) > 3:
                lines.append(f"- *(其余 {len(due_files) - 3} 道已归档)*")
        else:
            lines.append(f"### {name}错题本: 暂无到期错题")
        lines.append("")
    lines.append(f"**全科待攻坚错题总数**: `{total_due}` 道")
    return "\n".join(lines)


#: 错题本文件里每道错题以 ``## 📌 [YYYY-MM-DD] 标题`` 起头（格式真源见
#: ``tools/skills/error_logger.py::scan_error_records``，本模块只做**只读**解析，
#: 不改写任何文件，故不复用那个绑定在真实仓库根上的扫描器）。
_ERROR_SECTION_RE = re.compile(r"\n(?=##\s+📌)")
_ERROR_HEADER_RE = re.compile(r"##\s+📌\s*\[(\d{4}-\d{2}-\d{2})\]\s*(.*)")
_ERROR_STATUS_RE = re.compile(r"\*\*掌握状态\*\*[：:]\s*`?\[?([^\]`\n]*)\]?`?")
_ERROR_TYPE_RE = re.compile(r"\*\*错因分类\*\*[：:]\s*`?([^`\n(]*)`?")
_ERROR_QUESTION_RE = re.compile(
    r"\*\*题干\s*设问\*\*[：:]\s*```(?:text)?\s*(.*?)\s*```", re.DOTALL)
_ERROR_DUE_RE = re.compile(r"下次到期\s*`?(\d{4}-\d{2}-\d{2})`?")

#: 非错题档案（模板/索引/自测卷）文件名特征
_ERROR_SKIP_MARKERS = ("模板", "索引")

# ── [PF-1 性能修复·审计 2026-09-30] error_queue_cards 的指纹 memo ─────────
# 一次 ``_refresh_all`` 里本函数被调两次（统计块 ``_sync_task_stat_tiles`` +
# 错题卡片列表 ``render_error_cards``），每次都重扫四科错题本并逐文件解析。
# 现按「错题本文件指纹」memo：目录内容（路径 + mtime_ns + size，含增删）未变时
# 直接复用上次结果。指纹收集只做 glob + stat，远低于原「读取 + 逐段正则解析」成本。
_ERROR_CARDS_CACHE: Dict[Tuple[str, tuple], List[Dict[str, str]]] = {}
_ERROR_CARDS_CACHE_MAX = 4


def _error_queue_fingerprint(root: Path) -> Tuple[tuple, ...]:
    """错题本输入指纹：各科错题本目录下 ``*.md`` 的 ``(路径, mtime_ns, size)``。

    同时收集 02-英语 的备选目录（``错题与长难句本``）——多收只会让缓存更保守地
    失效，不会出现「指纹相同但解析结果不同」。
    """
    parts = []
    for folder, _name in SUBJECT_DIRS:
        for dirname in ("错题本", "错题与长难句本"):
            d = root / folder / dirname
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                try:
                    st = f.stat()
                    parts.append((str(f), st.st_mtime_ns, st.st_size))
                except OSError:
                    parts.append((str(f), None, None))
    return tuple(parts)


def error_queue_cards(workspace_root: Path) -> List[Dict[str, str]]:
    """扫描各科错题本，返回**结构化**待复测错题列表（供卡片列表渲染）。

    每项字段：``subject`` / ``subject_name`` / ``date`` / ``title`` /
    ``status`` / ``error_type`` / ``next_due`` / ``question`` / ``file_name``。
    解析失败的文件整体跳过（界面按空态渲染，绝不因个别脏文件崩）。

    [PF-1 性能修复·审计 2026-09-30] 带文件指纹 memo（见
    ``_error_queue_fingerprint``）。返回的列表/字典调用方只读
    （``len()`` / ``rec.get()``），故共享同一缓存对象、不做深拷贝。
    """
    root = Path(workspace_root)
    cache_key = (str(root), _error_queue_fingerprint(root))
    cached = _ERROR_CARDS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    label_of = {folder: label for _key, folder, label in subject_labels(root)}
    records: List[Dict[str, str]] = []

    for folder, default_name in SUBJECT_DIRS:
        subject_name = label_of.get(folder, default_name)
        mistake_dir = root / folder / "错题本"
        if not mistake_dir.exists() and folder == "02-英语":
            mistake_dir = root / folder / "错题与长难句本"
        if not mistake_dir.exists():
            continue
        for md_file in sorted(mistake_dir.glob("*.md")):
            if md_file.stem.startswith(("_", "自测卷_")) or \
                    any(marker in md_file.name for marker in _ERROR_SKIP_MARKERS):
                continue
            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
            except Exception as exc:  # pragma: no cover - 磁盘异常
                _LOG.warning("错题档案读取失败: %s -> %s", md_file, exc)
                continue
            for section in _ERROR_SECTION_RE.split(content):
                if not section.lstrip().startswith("## 📌"):
                    continue
                header = _ERROR_HEADER_RE.search(section)
                if not header:
                    continue
                records.append({
                    "subject": folder,
                    "subject_name": subject_name,
                    "date": header.group(1),
                    "title": header.group(2).strip(),
                    "status": _first_group(_ERROR_STATUS_RE, section) or "待复测",
                    "error_type": _first_group(_ERROR_TYPE_RE, section),
                    "next_due": _first_group(_ERROR_DUE_RE, section),
                    "question": _first_group(_ERROR_QUESTION_RE, section),
                    "file_name": md_file.name,
                })

    # 到期日由近及远；无到期日的排最后
    records.sort(key=lambda r: (r["next_due"] == "", r["next_due"], r["subject"], r["date"]))
    if len(_ERROR_CARDS_CACHE) >= _ERROR_CARDS_CACHE_MAX:
        _ERROR_CARDS_CACHE.clear()
    _ERROR_CARDS_CACHE[cache_key] = records
    return records


def _first_group(pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


# ── 研招监控情报 ────────────────────────────────────────────────

def intel_markdown(workspace_root: Path) -> str:
    """读取监控高校清单，生成「研招动态」Markdown 文本。"""
    lines = [
        "# 研招招考动态与高校监控雷达",
        f"> 数据基准: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
    ]
    watch_file = workspace_root / ".memory" / "admission_watch.json"
    if not watch_file.exists():
        lines.append("暂未配置监控高校，点击下方按钮或在 TUI 中输入 8 即可纳入监控。")
        return "\n".join(lines)

    try:
        data = json.loads(watch_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _LOG.warning("监控数据解析失败: %s -> %s", watch_file, exc)
        lines.append("监控数据损坏，已跳过展示。")
        return "\n".join(lines)

    if not isinstance(data, dict) or not data:
        lines.append("暂未配置监控高校，点击下方按钮或在 TUI 中输入 8 即可纳入监控。")
        return "\n".join(lines)

    lines.append(f"### 正在动态监控的高校 ({len(data)} 所):")
    for code, it in data.items():
        if not isinstance(it, dict):
            continue
        lines.append(f"- **{it.get('name')}** (`{code}`) | 上次核验: `{it.get('last_check', '-')}`")
        titles = it.get("recent_titles", []) or []
        if titles:
            lines.append(f"  - 最新通知: *{titles[0]}*")
    return "\n".join(lines)


# ── 研招情报页：指标卡双栏结构化数据（UI 重构·阶段 E） ────────────
# 指标卡只吃**真实数据**：任何一项探测不到真实值即为 None（界面隐藏该卡或
# 回落空态说明），绝不编造数字。全部探测只读、逐项隔离，单项失败不影响其它项。

def _watch_metrics(root: Path) -> Optional[Dict[str, Any]]:
    """从 ``.memory/admission_watch.json`` 提取「监控概览」指标。

    无监控配置 / 文件损坏 / 结构非法 / 无有效校名时返回 None（界面按空态
    渲染，绝不编造数字）。字段：``school_count`` / ``school_names`` /
    ``last_check``（最近一次核验时间，无记录为空串）/ ``notice_count``
    （最近一轮巡检抓取的通知标题总数）。
    """
    watch_file = root / ".memory" / "admission_watch.json"
    if not watch_file.exists():
        return None
    try:
        data = json.loads(watch_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _LOG.warning("监控指标解析失败: %s -> %s", watch_file, exc)
        return None
    if not isinstance(data, dict) or not data:
        return None

    names: List[str] = []
    last_checks: List[str] = []
    notice_count = 0
    for item in data.values():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
        last_check = str(item.get("last_check") or "").strip()
        if last_check:
            last_checks.append(last_check)
        titles = item.get("recent_titles")
        if isinstance(titles, list):
            notice_count += len(titles)

    if not names:
        return None
    return {
        "school_count": len(names),
        "school_names": names,
        # 时间戳格式 "YYYY-MM-DD HH:MM" 零填充，字典序即时间序
        "last_check": max(last_checks) if last_checks else "",
        "notice_count": notice_count,
    }


def _api_configured(root: Path) -> Optional[bool]:
    """大模型 API 配置状态；探测失败返回 None（界面不显示该卡）。"""
    try:  # 与 GUI 其余入口同源（intel_tab / settings 均优先 tools. 包式导入）
        from tools.intelligence.agentic_research import get_research_engine  # type: ignore
    except ImportError:  # pragma: no cover - 脚本式导入
        try:
            from intelligence.agentic_research import get_research_engine  # type: ignore
        except ImportError:  # pragma: no cover
            return None
    try:
        return bool(get_research_engine(workspace_root=root).is_api_configured())
    except Exception as exc:  # pragma: no cover - 配置读盘异常
        _LOG.warning("大模型 API 状态探测失败: %s", exc)
        return None


def _registry_count() -> Optional[int]:
    """本地院校库条目数（惰性导入，不拖慢服务层模块导入与 GUI 启动）。"""
    try:  # 与 GUI 其余入口同源（onboarding_wizard 亦优先 tools. 包式导入）
        from tools.intelligence.registry import get_registry  # type: ignore
    except ImportError:  # pragma: no cover - 脚本式导入
        try:
            from intelligence.registry import get_registry  # type: ignore
        except ImportError:  # pragma: no cover
            return None
    try:
        return int(get_registry().count())
    except Exception as exc:  # pragma: no cover - 数据文件异常
        _LOG.warning("本地院校库计数失败: %s", exc)
        return None


def _browser_channel_status() -> Optional[str]:
    """浏览器采集通道聚合状态（DISABLED / NOT_INSTALLED / UNAVAILABLE / READY）。"""
    try:  # 与 GUI 其余入口同源（intel 相关均优先 tools. 包式导入）
        from tools.intelligence.fetcher import BrowserPluginManager  # type: ignore
    except ImportError:  # pragma: no cover - 脚本式导入
        try:
            from intelligence.fetcher import BrowserPluginManager  # type: ignore
        except ImportError:  # pragma: no cover
            return None
    try:
        return str(BrowserPluginManager.status())
    except Exception as exc:  # pragma: no cover - 文件系统探测异常
        _LOG.warning("浏览器采集通道探测失败: %s", exc)
        return None


def _cache_entries(root: Path) -> Optional[int]:
    """检索缓存**有效期内**条目数（文件不存在或损坏返回 None）。"""
    cache_file = root / ".memory" / "search_cache.json"
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception as exc:
        _LOG.warning("检索缓存解析失败: %s -> %s", cache_file, exc)
        return None
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    now = time.time()
    valid = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            age = now - float(entry.get("stored_at") or 0.0)
            ttl = float(entry.get("ttl") or 0.0)
        except (TypeError, ValueError):
            continue
        if age <= ttl:
            valid += 1
    return valid


def intel_metrics(workspace_root: Path) -> Dict[str, Any]:
    """研招情报页「指标卡双栏」的结构化数据（只读，不依赖 Qt）。

    返回::

        {
          "watch": None | {"school_count", "school_names", "last_check", "notice_count"},
          "sources": {"api_configured", "registry_count", "browser_status", "cache_entries"},
        }

    **铁律：有数据则填，无数据为 None** —— 任何一项探测不到真实值都返回
    None，由界面决定隐藏该卡或显示空态说明，绝不编造数字；各项探测互相隔离，
    单项失败不影响其它项。
    """
    root = Path(workspace_root)
    return {
        "watch": _watch_metrics(root),
        "sources": {
            "api_configured": _api_configured(root),
            "registry_count": _registry_count(),
            "browser_status": _browser_channel_status(),
            "cache_entries": _cache_entries(root),
        },
    }


# ── 头部信息 ────────────────────────────────────────────────────

def header_info(workspace_root: Path) -> Dict[str, Any]:
    """顶栏所需字段：倒计时 / 目标院校 / 专业 / 风格 / 阶段 / 每日时长。"""
    state = load_state(workspace_root)
    if state is None:
        return {"days_left": exam_calendar.countdown_days(None), "school": "目标院校",
                "major": "报考专业", "style": "严格把关·保姆提分型",
                "style_short": "严格把关", "stage": "", "daily_hours": 8.5}
    return {
        "days_left": state.days_left,
        "school": state.school,
        "major": state.major,
        "style": state.style,
        "style_short": state.style_short,
        "stage": state.stage,
        "daily_hours": state.daily_hours,
        "exam_date": state.exam_date,
    }


__all__ = [
    "SUBJECT_DIRS",
    "countdown_days",
    "error_queue_cards",
    "error_queue_markdown",
    "header_info",
    "intel_markdown",
    "intel_metrics",
    "load_state",
    "subject_labels",
    "subject_progress",
]
