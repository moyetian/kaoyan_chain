# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan Study Chain) · 终端全景交互中枢 (TUI Navigator)

提供纯终端/控制台下的高颜值全功能交互式操作面板：
  [1] 📋 今日任务 · 跨科目任务推进与打卡
  [2] 🎯 靶向组卷 · 考点难度智能拼卷 (Exam Composer)
  [3] 🔄 同源变式 · 错题反思与同源变式 (Variant Retrieval)
  [4] 📈 考纲Diff · 新旧考纲层级对比与动荡率分析 (Syllabus Diff)
  [5] 📥 切片入库 · 真题与教材试题白名单切片 (Material Ingestion)
  [6] 🔍 院校侦察 · 研招网与社媒实名口碑研报 (School Scout)
  [7] ⚖️ 双校对标 · 招生规模/复试线/保护度对比 (School Comparator)
  [8] 📡 简章监控 · 目标高校研究生院变动预警 (Admission Watcher)
  [9] 📊 看板构建 · 静态 Web 看板同步与掌握度更新 (Dashboard Build)
  [10] 📱 公众号检索 · 微信公众号考研经验文章检索与沉淀 (WeChat Search)
  [11] 🎓 学科报到 · 选科目报到并给出今日攻坚任务 (Subject Check-in)
  [12] 📝 交作业 · 三种提交方式指引与批改入口 (Homework Submit)
  [0] 🚪 安全退出 · 退出系统

[2026-10-06 新增 11/12 · 为什么是「追加」而不是重排]
  R2/R3 两轮全矩阵仿真（54 格 / 610 条记录）把「报到 / 交作业」记为交互方式
  限制：能力在 REPL 私教链路里齐备，但 TUI 十个入口里完全没有它们，考生在
  终端中枢按不到。此处只补这两个最高频项，且**编号追加为 11/12**——既有
  1-10 与 0 的编号含义、分组归属和相对顺序一律不动（改既有编号会毁掉肌肉记忆，
  也会让操作手册/看板文案里的「按 6 侦察」这类指引集体失真）。新组插在
  「研招情报」与「全景大盘」之间，于是 [0] 安全退出仍留在菜单最后一行。
"""

import sys
import os
import re
import json
import argparse
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from datetime import datetime, date, timedelta
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = resolve_workspace_root(__file__)
CONFIG_FILE = ROOT / "ky_config.json"

# [W12 P0-1] 与 ky_cli.py / init_workspace.py 一致：tools 目录与仓库根都入 path。
# 此前直跑（py tools/tui_navigator.py）时 sys.path[0] 是 tools 目录而非仓库根，
# intelligence 包内函数级绝对导入（如 agentic_research 的
# from tools.intelligence.xxx import ...）会命中 site-packages 的空 tools
# 命名空间包 —— 双校对标因此报 No module named 'tools.intelligence'。
_tools_dir = Path(__file__).resolve().parent
for _p in (str(_tools_dir), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# [W12 P0-1] intelligence 包解析三端统一（此前此处为裸 from intelligence import）
try:
    from intel_imports import resolve_intel_import
except ImportError:  # pragma: no cover - 包式导入上下文
    from tools.intel_imports import resolve_intel_import

# [根因修复·日期硬编码] 初试日期/倒计时的单一事实来源，替代各端各自硬编码的 2026-12-19
try:
    import exam_calendar
except ImportError:  # pragma: no cover - 兼容 tools.exam_calendar 包式导入
    from tools import exam_calendar

# [缺陷修复·版本号多源漂移] Banner 版本改为读单一真源；
# 此前写死 "v2.5"，与 pyproject 的 2.7.0、CLI 的 v2.6.0 三处互不相同。
try:
    from version import get_version
except ImportError:  # pragma: no cover - 兼容 tools.version 包式导入
    from tools.version import get_version

# [缺陷修复·三端重复解析] 今日任务进度统一走共享状态层
try:
    from state import load_dashboard_state
except ImportError:  # pragma: no cover - 兼容 tools.state 包式导入
    from tools.state import load_dashboard_state

try:
    from cli.shared import normalize_subject
except ImportError:  # pragma: no cover - 兼容 tools.cli 包式导入
    from tools.cli.shared import normalize_subject

# [缺陷修复·宽度写死 / 幽灵依赖 / Windows ANSI 乱码] 终端能力与排版工具
# 统一收敛到 tools/tui/terminal.py（纯文本 TUI 与 textual 版共用）：
#   * panel_width()      跟随终端列数并夹在合理区间，替代写死的 84
#   * enable_windows_vt() 真正启用虚拟终端（仅 os.system("color") 并不够，
#                        旧版 Windows 控制台会把 ANSI 转义当字面量打印）
#   * NO_COLOR          业界约定的去色开关
try:  # pragma: no cover - 取决于运行方式
    from tui.terminal import (
        colors_disabled as _colors_disabled,
        display_width as _display_width,
        enable_windows_vt as _enable_windows_vt,
        is_emoji_char as _is_emoji_char,
        is_tty as _is_tty,
        pad_display,
        panel_width,
    )
except ImportError:  # pragma: no cover
    from tools.tui.terminal import (  # type: ignore
        colors_disabled as _colors_disabled,
        display_width as _display_width,
        enable_windows_vt as _enable_windows_vt,
        is_emoji_char as _is_emoji_char,
        is_tty as _is_tty,
        pad_display,
        panel_width,
    )


# ════════════════════════════════════════════════════════════════
# 终端色彩与高精度排版引擎 (Visual Layout Engine)
# ════════════════════════════════════════════════════════════════

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def colorize(text: str, color: str) -> str:
    """给文本着色；非 TTY 或设置了 NO_COLOR/KY_NO_COLOR 时原样返回。

    [缺陷修复] 旧实现只看 `sys.stdout.isatty()`，用户即便显式声明 NO_COLOR
    （例如把输出重定向到文件、或终端本身配色受限于无障碍需求）也仍会被塞进
    转义序列。现统一走 terminal.colors_disabled()。
    """
    if _colors_disabled():
        return text
    return f"{color}{text}{Colors.RESET}"


def _install_theme_palette() -> None:
    """把主题 token 编译成 ANSI 调色板并覆盖到 Colors 上（失败则保留 16 色默认）。

    这样 TUI 的颜色与 GUI / Web 看板同源（同一组 token），
    而不是各端自己挑一套色号。
    """
    try:
        try:
            from theme import apply_to_colors_class, load_theme
        except ImportError:  # pragma: no cover
            from tools.theme import apply_to_colors_class, load_theme  # type: ignore

        apply_to_colors_class(Colors, load_theme(ROOT))
    except Exception:                          # pragma: no cover - 主题不可用不该拖垮 TUI
        # 保留 16 色默认值即可，纯外观降级
        return


_install_theme_palette()


def is_emoji_char(ch: str) -> bool:
    """兼容门面：实现在 tools/tui/terminal.py（纯文本与 textual 两版共用）。"""
    return _is_emoji_char(ch)


def display_width(s: str) -> int:
    """兼容门面：实现在 tools/tui/terminal.py。"""
    return _display_width(s)


def render_box_line(text: str, total_w: Optional[int] = None, align: str = 'left', border: str = '│', pad: int = 1) -> str:
    """渲染带边框的单行，并严格校准右边框对其"""
    if total_w is None:
        total_w = panel_width()
    w = display_width(text)
    rem = max(0, total_w - 2 - w)
    if align == 'center':
        lp = rem // 2
        rp = rem - lp
        return f"{border}{' ' * lp}{text}{' ' * rp}{border}"
    else:
        return f"{border}{' ' * pad}{text}{' ' * max(0, rem - pad)}{border}"


def render_sec_header(title: str, total_w: Optional[int] = None, color: str = '') -> str:
    # 组成: "╭"(1) + "──"(2) + " "(1) + title(w) + "(1) + 填充 + "╮"(1) = 6 + w + rem
    if total_w is None:
        total_w = panel_width()
    w = display_width(title)
    rem = max(0, total_w - 6 - w)
    return colorize(f"╭── {title} " + "─" * rem + "╮", color)


def render_sec_footer(total_w: Optional[int] = None, color: str = '') -> str:
    if total_w is None:
        total_w = panel_width()
    return colorize("╰" + "─" * (total_w - 2) + "╯", color)


def render_progress_bar(pct: float, width: int = 12) -> str:
    filled = int(round(width * max(0.0, min(100.0, pct)) / 100))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct:.1f}%"


# ════════════════════════════════════════════════════════════════
# 学情与考情指标聚合器 (State Aggregator)
# ════════════════════════════════════════════════════════════════

def _load_config_dict() -> dict:
    """读取 ky_config.json；缺失或损坏时返回空 dict（由 exam_calendar 兜底推算）。"""
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def get_countdown_days() -> int:
    """距离初试的剩余天数。

    [根因修复·日期硬编码] 旧实现把兜底初试日写死为 "2026-12-19"、异常时返回
    魔法数 104，导致：配置缺失时倒计时恒定失真、硬编码日期过期后永久失效、
    且与 GUI（兜底 103）和 CLI（动态推算）三端数字互不一致。
    现统一委托 exam_calendar 解析（配置 → 入学年 → 日历推算）。
    """
    try:
        return exam_calendar.countdown_days(_load_config_dict())
    except Exception:
        # 极端兜底：仅当 exam_calendar 自身不可用时才退回日历推算，不再写死年份
        return max(0, (exam_calendar.exam_date_for_exam_year(date.today().year) - date.today()).days)


def get_exam_year() -> int:
    """从配置动态解析初试年份，避免硬编码导致跨年后显示失效"""
    try:
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            exam_str = (
                cfg.get("study_plan", {}).get("exam_date")
                or cfg.get("exam_date")
                or ""
            )
            if exam_str:
                return datetime.strptime(exam_str, "%Y-%m-%d").year
    except Exception:
        pass
    return date.today().year


def _read_backup_school() -> str:
    """从 ky_config.json 读取考生备选院校（兼容多种键名），读不到返回空串。"""
    if not CONFIG_FILE.exists():
        return ""
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        sp = cfg.get("study_plan", {}) or {}
        for cand in (
            sp.get("backup_school"), cfg.get("backup_school"),
            cfg.get("backup_target"), sp.get("backup_target"),
        ):
            v = (cand or "").strip()
            if v and v not in ("未指定", "目标院校"):
                return v
    except Exception:
        pass
    return ""


def _discover_new_syllabus_file():
    """[P3 修复·D7] 在 04-专业课/参考资料 内探测候选「新考纲」文件（排除演示样例）。

    [F2 修复·双份模式集] 此前本函数自带第二份 glob 模式集，与 cli.shared 的
    实现各自漂移（「408考纲_2027.md」这类年份在后的命名两侧都不命中，C-P6
    实测坠演示模式）。现删除副本，惰性委托 ``cli.shared._discover_new_syllabus``
    （双导入路径兼容），探测规则与年份排序单源。
    """
    try:
        from cli.shared import _discover_new_syllabus
    except ImportError:  # pragma: no cover - 兼容 tools.cli 包式导入
        from tools.cli.shared import _discover_new_syllabus
    return _discover_new_syllabus()


def _resolve_input_path(raw: str):
    """把用户/参数传入的路径解析为绝对路径：先按当前工作目录，再回退到项目根目录。"""
    t = str(raw or "").strip().strip('"')
    if not t:
        return None
    p = Path(t)
    if p.exists():
        return p
    p2 = ROOT / t
    return p2 if p2.exists() else p


def get_config_summary() -> dict:
    if not CONFIG_FILE.exists():
        return {
            "school": "未指定", "major": "未指定", "stage": "强化阶段",
            "style": "严格把关型", "hours": 8.5
        }
    try:
        state = load_dashboard_state(ROOT)
        return {
            "school": state.school or "未指定",
            "major": state.major or "未指定",
            "stage": state.stage,
            "style": state.style_short,
            "hours": state.daily_hours,
        }
    except Exception:
        return {
            "school": "未指定", "major": "未指定", "stage": "强化阶段",
            "style": "严格把关型", "hours": 8.5
        }


def get_today_progress() -> tuple[int, int]:
    """统计今日各科总任务数与已完成数（返回 ``(已完成, 总数)``）。

    [缺陷修复·三端重复解析] 原先自带一份与 CLI / GUI 不同的解析实现。
    现统一委托 tools/state 共享层，三端数字必然一致。
    [UT4 修复·CLI-5] docstring 中性化：「四科」→「各科」（三科模式同样适用）。
    """
    state = load_dashboard_state(ROOT)
    return state.completed, state.total


def get_intel_ribbon() -> list[str]:
    """提取考情雷达关键情报条目（只读本地数据，不做任何网络请求）。

    [缺陷修复·渲染路径里发网络请求] 旧实现在「未配置监控且有目标院校」时会
    就地 new 一个 AdmissionWatcher 并调用 ``add_watch()`` —— 那是一次真实网络请求。
    而本函数被 ``render_header()`` 调用、``render_header()`` 又被主循环每轮打印，
    于是**每渲染一次主菜单就发一次网络请求**，是首屏卡顿的直接来源。
    注册监控本属用户显式动作，现已统一由 ``execute_action("watch")`` 触发
    （那里本就有 add_watch 逻辑），此处只负责读取已有数据。
    """
    ribbon: list[str] = []
    info = get_config_summary()
    target_school = info.get("school")
    if target_school in ("未指定", "目标院校", None):
        target_school = ""

    # 1. 监控状态（纯本地读取）
    watch_file = ROOT / ".memory" / "admission_watch.json"
    w_data = {}
    if watch_file.exists():
        try:
            w_data = json.loads(watch_file.read_text(encoding="utf-8"))
        except Exception:
            w_data = {}

    if w_data:
        names = [v.get("school", k) for k, v in list(w_data.items())[:2]]
        ribbon.append(f"📡 简章监控: {len(w_data)} 所院校 ({', '.join(names)}) 巡检中")
    else:
        ribbon.append(f"📡 简章监控: 暂未配置高校 (可输入 8 开启【{target_school or '目标院校'}】监控)")

    # 2. 考纲最新变动 (优先展示学员目标高校)
    pro_dir = ROOT / "04-专业课"
    if pro_dir.exists():
        diff_files = sorted(list(pro_dir.glob("考纲变动分析_*.md")), key=lambda p: p.stat().st_mtime, reverse=True)
        # 优先寻找目标高校的大纲变动
        user_diffs = [df for df in diff_files if target_school and target_school in df.name]
        chosen_df = user_diffs[0] if user_diffs else (diff_files[0] if diff_files else None)
        if chosen_df:
            txt = chosen_df.read_text(encoding="utf-8", errors="ignore")
            vol_m = re.search(r"(?:考点动荡率|波动率)[^\d]*(\d+\.?\d*)%", txt)
            vol = vol_m.group(1) if vol_m else "0.0"
            sch_tag = f"【{target_school}】" if (user_diffs and target_school) else ""
            ribbon.append(f"📑 考纲变动: {sch_tag}波动率 {vol}% (已生成逐级处方)")
        elif target_school:
            # [F11 修复·无依据宣称「已核验入库」] 此前无 diff 文件即无条件宣称
            # 考纲已核验；占位（【待自填】）与缺失态同样显示，与产品红线
            # 「替换前不得宣称按纲出题」矛盾（仿真 C 轮实测）。现按
            # pro_syllabus_state(ROOT) 真实状态分档；探测/导入异常时保守沿用
            # 原文案（不新增错误断言）。导入模式与 scout_engine 一致。
            _syl_state = None
            try:
                try:
                    from syllabus_manager import pro_syllabus_state
                except ImportError:
                    from tools.syllabus_manager import pro_syllabus_state
                _syl_state = pro_syllabus_state(ROOT)
            except Exception:
                _syl_state = None
            if _syl_state == "placeholder":
                ribbon.append(f"📑 考纲变动: 【{target_school}】考纲为待自填占位（未核验）")
            elif _syl_state == "missing":
                ribbon.append(f"📑 考纲变动: 【{target_school}】尚未导入考纲")
            else:
                ribbon.append(f"📑 考纲变动: 【{target_school}】考纲已核验入库")

    # 3. 社媒经验贴
    # [P0 修复] 经验档案属学员隐私，主读取路径迁移至 .memory/experiences/（兼容旧目录存量）
    exp_dir = ROOT / ".memory" / "experiences"
    if not exp_dir.exists():
        exp_dir = ROOT / "docs" / "experiences"  # 旧版存量目录，仅只读兼容
    if exp_dir.exists():
        exp_files = list(exp_dir.glob("*.md"))
        if exp_files:
            user_exps = [ef for ef in exp_files if target_school and target_school in ef.name]
            tag = f" (含【{target_school}】上岸档案)" if user_exps else ""
            ribbon.append(f"💬 社媒口碑: 已沉淀 {len(exp_files)} 篇院校实名经验档案{tag}")

    return ribbon


# ════════════════════════════════════════════════════════════════
# 菜单定义与分类架构 (Categorized Menu)
# ════════════════════════════════════════════════════════════════

MENU_GROUPS = [
    (
        "⚡ 核心备考与实战攻坚 (Core Preparation)",
        Colors.YELLOW,
        [
            ("1", "今日任务 (Daily Tasks)", "📋", "查看任务量、打卡推进与行动指引", "today"),
            ("2", "靶向组卷 (Exam Composer)", "🎯", "按考点与难度梯度智能拼卷演练", "compose"),
            ("3", "同源变式 (Variant Retrieval)", "🔄", "针对薄弱考点或错题智能检索同源题", "variant"),
        ]
    ),
    (
        "🏛️ 研招情报与考纲透视 (Admissions & Syllabus)",
        Colors.CYAN,
        [
            ("4", "考纲Diff (Syllabus Diff)", "📈", "对比新旧考纲 AST 掌握度变迁与动荡率", "diff"),
            ("5", "切片入库 (Material Ingestion)", "📥", "真题/模拟卷 Markdown 结构化入库", "ingest"),
            ("6", "院校侦察 (School Scout)", "🔍", "聚合研招网指标与三大社媒实名口碑研报", "scout"),
            ("7", "双校对标 (School Comparator)", "⚖️", "横向深度对标双校招生指标与保护机制", "compare"),
            ("8", "简章监控 (Admission Watcher)", "📡", "目标高校研究生院简章动态指纹预警", "watch"),
        ]
    ),
    (
        "📊 全景态势与系统大盘 (Dashboard & System)",
        Colors.MAGENTA,
        [
            ("9", "看板更新 (Dashboard Build)", "📊", "重编译掌握度雷达并刷新本地 Web 看板", "build"),
            ("10", "公众号检索 (WeChat Search)", "📱", "微信公众号考研经验、院校解读与文章沉淀", "wechat_search"),
        ]
    ),
    (
        # [2026-10-06 新增] 组标题同时涵盖 [0] 退出，是因为两项新入口追加为
        # 11/12 后菜单编号需要保持升序（1..10 → 11 → 12 → 0），而 [0] 必须
        # 留在最后一行（退出项固定末位是既有惯例）。既有 1-10 的分组归属与
        # 相对顺序完全未动，只有 [0] 从「大盘」组末尾移到了本组末尾。
        "🎓 学科报到·作业批改与退出 (Check-in, Homework & Exit)",
        Colors.GREEN,
        [
            ("11", "学科报到 (Subject Check-in)", "🎓", "选科目报到：调取学情档案、派发今日攻坚任务", "checkin"),
            ("12", "交作业 (Homework Submit)", "📝", "三种提交方式指引：截图草稿 / 答题卡 / 推导文字", "homework"),
            ("0", "安全退出 (Exit System)", "🚪", "保存状态并平稳退出终端导航器", "exit"),
        ]
    )
]

# 扁平化映射列表，供快速查询与测试断言
MENU_OPTIONS = [(k, n, d, alias) for _, _, items in MENU_GROUPS for k, n, icon, d, alias in items]


def menu_key_range() -> str:
    """菜单里**数字键**的实际区间文案（如 ``0-12``）。

    [为什么改成派生] 这段提示此前两次写死（先"0-9"、后"0-10"），每次新增菜单项
    都会静默变成错误指引——考生按提示里的编号找不到对应项。现直接由
    ``MENU_OPTIONS`` 推导，新增/删除菜单项不再需要手工同步三处文案
    （``render_menu`` 尾部提示、纯文本循环 input 提示、未知编号分支）。
    """
    nums = sorted(int(k) for k, _, _, _ in MENU_OPTIONS if str(k).isdigit())
    return f"{nums[0]}-{nums[-1]}" if nums else "无"

# [缺陷修复·宽度写死] 原先写死 TOTAL_PANEL_WIDTH = 84，与终端实际列数无关：
# 窄终端折行错位、宽终端右侧空一大片。现在宽度由 terminal.panel_width() 动态
# 计算（跟随终端列数并夹在 60~110 之间），旧常量已删除。


# ════════════════════════════════════════════════════════════════
# 渲染器实现 (Renderers)
# ════════════════════════════════════════════════════════════════

def get_study_journey_stats(days_left: int) -> tuple[int, int, float]:
    """动态计算备战总天数、已学习天数与备考历程百分比"""
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    # [根因修复·日期硬编码] 初试日与 TUI 倒计时同源，避免「头部倒计时」与
    # 「备考历程百分比」用的是两个不同的日期基准（旧实现这里写死 date(2026,12,19)）。
    exam_d, _exam_src = exam_calendar.resolve_exam_date(cfg)

    start_date_str = cfg.get("study_plan", {}).get("start_date") or cfg.get("start_date")
    if not start_date_str:
        hist = cfg.get("completion_history", {})
        if hist:
            start_date_str = min(hist.keys())

    if start_date_str:
        try:
            start_d = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except Exception:
            start_d = None
    else:
        start_d = None

    today = date.today()
    if not start_d:
        # 若未指定起跑日，默认以距离考研 180 天作为备考攻坚周期基数
        start_d = exam_d - timedelta(days=180)

    total_days = max(1, (exam_d - start_d).days)
    passed_days = max(1, min(total_days, (today - start_d).days))
    pct = round(passed_days / total_days * 100, 1)
    return total_days, passed_days, pct


def render_header() -> str:
    days = get_countdown_days()
    info = get_config_summary()
    done_tasks, total_tasks = get_today_progress()
    today_pct = (done_tasks / total_tasks * 100) if total_tasks > 0 else 0.0

    total_study_days, passed_days, calendar_pct = get_study_journey_stats(days)

    W = panel_width()
    lines = []
    lines.append(colorize("╭" + "─" * (W - 2) + "╮", Colors.CYAN))
    lines.append(render_box_line(colorize(f"🎯 考研学习链 (Kaoyan Study Chain) · 终端全景智能中枢 v{get_version()}", Colors.BOLD + Colors.CYAN), W, 'center'))
    lines.append(colorize("├" + "─" * (W - 2) + "┤", Colors.CYAN))

    # 第一行：倒计时与备考历程进度条
    row1 = (
        f"{colorize('⏳ ' + str(get_exam_year()) + ' 初试倒计时: ' + str(days) + ' 天', Colors.BOLD + Colors.YELLOW)}  │  "
        f"备考进度: {colorize(render_progress_bar(calendar_pct, 12), Colors.CYAN)} "
        f"{colorize(f'(第{passed_days}/{total_study_days}天)', Colors.DIM)}"
    )
    lines.append(render_box_line(row1, W, 'left'))

    # 第二行：目标院校与拟报专业
    row2 = (
        f"{colorize('🏛️  目标院校: ' + info['school'], Colors.BOLD + Colors.GREEN)}   │  "
        f"{colorize('📚 专业方向: ' + info['major'], Colors.BOLD + Colors.MAGENTA)}"
    )
    lines.append(render_box_line(row2, W, 'left'))

    # 第三行：辅导风格与今日各科任务打卡进度（[UT4 修复·CLI-5] 四科→各科）
    task_stat_str = f"{done_tasks}/{total_tasks} 完成 ({today_pct:.1f}%)" if total_tasks > 0 else "待生成 (去报道)"
    row3 = (
        f"🛡️  辅导风格: {colorize(info['style'], Colors.WHITE)}    │  "
        f"{colorize('📋 今日打卡: ' + task_stat_str, Colors.BOLD + (Colors.GREEN if done_tasks == total_tasks and total_tasks > 0 else Colors.YELLOW))}"
    )
    lines.append(render_box_line(row3, W, 'left'))

    # 考情情报动态小饰条
    ribbon_items = get_intel_ribbon()
    if ribbon_items:
        lines.append(colorize("├" + "─" * (W - 2) + "┤", Colors.CYAN))
        for rib in ribbon_items:
            lines.append(render_box_line(colorize("  • " + rib, Colors.DIM + Colors.WHITE), W, 'left'))

    lines.append(colorize("╰" + "─" * (W - 2) + "╯", Colors.CYAN))
    return "\n".join(lines)


def render_menu() -> str:
    W = panel_width()
    lines = []
    lines.append("")

    for group_title, group_color, items in MENU_GROUPS:
        lines.append(render_sec_header(group_title, W, group_color))
        for key, name, icon, desc, _ in items:
            key_badge = colorize(f"[{key}]", Colors.BOLD + Colors.GREEN if key != "0" else Colors.DIM + Colors.WHITE)
            name_padded = colorize(pad_display(f"{name}", 32), Colors.BOLD)
            desc_str = colorize(f"{icon} {desc}", Colors.DIM)
            content = f" {key_badge} {name_padded} {desc_str}"
            lines.append(render_box_line(content, W, 'left', pad=0))
        lines.append(render_sec_footer(W, group_color))
        lines.append("")

    lines.append(colorize(
        f"💡 提示：输入操作序号 [{menu_key_range()}] 或指令别名 (如 1 / today / compose) 即可启动模块",
        Colors.DIM + Colors.CYAN))
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 动作分发与业务执行器 (Action Dispatcher)
# ════════════════════════════════════════════════════════════════

def execute_action(action_key: str, interactive: bool = True, extra: dict | None = None,
                   batch: bool = False) -> bool:
    """
    分发执行菜单动作。返回 False 表示退出循环，True 表示继续。

    extra: 非交互模式下由命令行传入的附加参数（如 compare 的第二所高校）。
    batch: True 表示命令行批处理入口（``ky tui --action N``）——失败/未组卷时以
        非零退出码反映结果，供脚本与 CI 判定。交互式界面（纯文本循环 / textual）
        必须保持 False：textual 的动作在线程里执行，SystemExit 会静默杀死工作
        线程并把界面卡在「正在执行」，单次动作失败不得踢出整个界面。
    """
    extra = extra or {}
    key = str(action_key).strip()
    if key in ("0", "exit", "quit", "q"):
        print(colorize("\n👋 祝备考顺利，金榜题名！下次会话再见。\n", Colors.BOLD + Colors.GREEN))
        return False

    matched = None
    for opt_key, opt_name, _, cmd_alias in MENU_OPTIONS:
        if key == opt_key or key.lower() == cmd_alias.lower():
            matched = (opt_key, opt_name, cmd_alias)
            break

    if not matched:
        # [P2 修复·文案] 提示区间此前写死（"0-9" → "0-10"），两次新增菜单项都
        # 留下过错误指引；现统一由 menu_key_range() 派生。
        _keys = [k for k, _, _, _ in MENU_OPTIONS]
        _hint = menu_key_range()
        _extra = "/".join(k for k in _keys if not k.isdigit())
        if _extra:
            _hint += f" 或 {_extra}"
        print(colorize(f"\n[!] 未知操作编号: '{key}'，请输入 {_hint} 之间的选项。", Colors.RED))
        return True

    opt_key, opt_name, cmd_alias = matched
    print(colorize(f"\n▶ 正在启动模块：{opt_name} ...", Colors.BOLD + Colors.CYAN))

    try:
        if cmd_alias == "today":
            from ky_cli import print_today_tasks_summary
            print_today_tasks_summary(as_json=False)
        elif cmd_alias == "compose":
            cfg_sub = normalize_subject(_load_config_dict().get("active_subject"), "pro") or "pro"
            subj_input = input(f"请输入组卷科目 [math/eng/pol/pro，默认 {cfg_sub}]: ").strip() if interactive else ""
            sub = normalize_subject(subj_input or extra.get("subject"), cfg_sub) or cfg_sub
            cnt_input = input("请输入组卷题量 [默认 3]: ").strip() if interactive else str(extra.get("count") or "")
            try:
                count = int(cnt_input) if cnt_input.strip().isdigit() else 3
            except Exception:
                count = 3
            if count < 1:
                count = 3
            from skills import exam_composer
            res = exam_composer.compose_exam_paper(subject=sub, count=count, include_weak=True, save_file=True)
            print(res.get("formatted_paper") or res.get("content") or "")
            if res.get("success") is False:
                # [P2-10 修复·退出码分裂] 与 CLI ky exam 同口径：空题库不产卷、
                # 复用同一「未组卷」引导文案；批处理模式（--action 2）退出码统一为 2
                # —— 此前同一场景 CLI EXIT 2 而 TUI EXIT 0，脚本无法判定未组卷。
                print(colorize("\n[!] 未组卷：本地暂无可用题源（按上方 3 步启动闭环指引操作）。", Colors.YELLOW))
                if batch:
                    sys.exit(2)
                return True
            if res.get("saved_path"):
                print(colorize(f"\n[+] 自测卷已落盘: {res['saved_path']}", Colors.GREEN))
        elif cmd_alias == "variant":
            cfg_sub = normalize_subject(_load_config_dict().get("active_subject"), "pro") or "pro"
            sub_input = input(f"请输入科目 [math/eng/pol/pro，默认 {cfg_sub}]: ").strip() if interactive else ""
            sub = normalize_subject(sub_input or extra.get("subject"), cfg_sub) or cfg_sub
            from skills import variant_retriever
            # 默认考点必须从本科目真实考纲中推荐，严禁硬编码他科考点（如 408 的「二叉树」）
            default_kw = variant_retriever.suggest_keyword(sub)
            prompt = f"请输入需要寻找变式题的考点关键词 [默认 {default_kw or '考纲首个考点'}]: "
            # [缺陷修复·入参通道] 非交互模式下此前恒取空串，导致 --keyword 完全失效、
            # 只能落到 default_kw（并与 action 4/5 已有的 --new/--file 通道不一致）。
            kw = input(prompt).strip() if interactive else str(extra.get("keyword", "")).strip()
            if not kw:
                kw = default_kw
            res = variant_retriever.search_real_variant(subject=sub, keyword=kw, limit=3)
            print(variant_retriever.format_variant_output(res))
        elif cmd_alias == "diff":
            # [P3 修复·D7] 非交互模式此前无入参通道，new_p 恒空 -> 必然「已取消」，菜单项不可达。
            # 现支持 --old= / --new= 显式传入；缺 new 时自动探测 04-专业课/参考资料 候选新考纲。
            old_p = input("旧大纲路径 [回车使用 04-专业课/考试大纲.md]: ").strip() if interactive else (extra.get("old") or "").strip()
            if interactive:
                new_p = input("新大纲路径 (必填，避免虚构大纲误导复习): ").strip()
            else:
                new_p = (extra.get("new") or "").strip()
                if not new_p:
                    cand = _discover_new_syllabus_file()
                    if cand:
                        new_p = str(cand)
                        print(colorize(f"[i] 未指定新大纲，自动关联参考资料库候选文件: {cand}", Colors.CYAN))
            if not new_p:
                print(colorize(
                    "\n[!] 未指定【新考纲】文件路径，已取消本次比对。\n"
                    "    本项目严守「杜绝 AI 编造」红线：未提供真实新大纲时不会自动伪造变动内容。\n"
                    "    非交互模式请使用:  ky tui --action 4 --new=\"04-专业课/2027考纲.md\"\n"
                    "    或交互模式直接输入；亦可先用 CLI 的演示模式了解功能: ky diff", Colors.RED))
            else:
                default_old = ROOT / "04-专业课" / "考试大纲.md"
                old_path = _resolve_input_path(old_p) if old_p else default_old
                new_path = _resolve_input_path(new_p) or Path(new_p)
                if not old_path.exists():
                    print(colorize(f"\n[!] 基准(旧)大纲不存在: {old_path}", Colors.RED))
                elif not new_path.exists():
                    print(colorize(f"\n[!] 新大纲文件不存在: {new_path}", Colors.RED))
                else:
                    # [W12 P0-1] 裸导入改三端统一解析（见文件头 resolve_intel_import）
                    _intel_mod = resolve_intel_import()
                    y_new = _intel_mod.current_exam_year()
                    y_old = y_new - 1
                    gen = _intel_mod.get_syllabus_diff_generator()
                    _cfg = get_config_summary()
                    # [W12 P1-5] 命名随实：显式参数 > 路径科目推导（公共课标「全国统考」）
                    # > config 回退。此前恒取 config 志愿——对英语考纲比对却落盘
                    # 「考纲变动分析_目标院校_目标专业 (专业代码)...」（张冠李戴）。
                    _sch_inf, _maj_inf = _intel_mod.syllabus_diff.infer_diff_naming(
                        old_path, new_path)
                    rep = gen.compare_files(
                        old_file=old_path, new_file=new_path,
                        school=(extra.get("school1") or _sch_inf
                                or _cfg.get("school") or "目标院校"),
                        major=(extra.get("major") or _maj_inf
                               or _cfg.get("major") or new_path.stem),
                        year_old=y_old, year_new=y_new
                    )
                    saved = gen.save_diff_report(rep)
                    m = rep["metrics"]
                    print(colorize(
                        f"\n[+] 考纲 Diff 报告生成完毕 (考点动荡率: {m['volatility_percentage']}%，"
                        f"定性: {m['stability_grade']}):", Colors.GREEN))
                    print(f"    新增 {m['added_count']} / 剔除 {m['removed_count']} / "
                          f"调整 {m['modified_count']} / 不变 {m['unchanged_count']}")
                    # [F13 显示位] 基准异常警示（同一文件自我对照 / 占位模板作基准）。
                    # 契约键 baseline_warning 由 syllabus_diff 提供（compare_texts 检测
                    # 占位基准；compare_files 检测同文件自我对照，优先级更高）；
                    # 缺失/为空不打，兼容旧版返回。
                    if rep.get("baseline_warning"):
                        print(colorize("  [!] " + str(rep["baseline_warning"]), Colors.YELLOW))
                    print(f"    生成路径: {saved}")
        elif cmd_alias == "ingest":
            # [P3 修复·D7] 同上：非交互模式支持 --file= / --subject=，此前恒「未指定文件路径，已返回」。
            f_path = input("请输入要切片的真题/讲义 Markdown 路径: ").strip() if interactive else (extra.get("file") or "").strip()
            if not f_path:
                print(colorize(
                    "\n[!] 未指定待切片文件路径。\n"
                    "    非交互模式请使用:  ky tui --action 5 --file=\"04-专业课/真题.md\" [--subject=pro]", Colors.YELLOW))
            else:
                if interactive:
                    sub_input = input("请输入归属科目 [math/eng/pol/pro，默认 pro]: ").strip()
                else:
                    sub_input = (extra.get("subject") or "").strip()
                from skills import material_ingestion
                pipe = material_ingestion.get_material_ingestion_pipeline()
                _ing_path = _resolve_input_path(f_path)
                if _ing_path is None or not _ing_path.exists():
                    print(colorize(f"\n[!] 找不到待切片文件: {f_path}", Colors.RED))
                    return True
                ing_res = pipe.ingest_file(_ing_path, subject=(sub_input or "pro"))
                if ing_res.get("success"):
                    print(colorize(
                        f"\n[+] 切片入库成功：识别 {ing_res['count']} 道题目 "
                        f"(选择 {ing_res['choices']} / 填空 {ing_res['blanks']} / 大题 {ing_res['essays']})",
                        Colors.GREEN))
                    print(f"    生成路径: {ing_res['target_path']}")
                else:
                    print(colorize(f"\n[!] 切片入库失败: {ing_res.get('msg')}", Colors.RED))
        elif cmd_alias == "scout":
            info = get_config_summary()
            school = input(f"请输入目标高校 [默认 {info['school']}]: ").strip() if interactive else ""
            if not school:
                # [W12 P1-4] 非交互参数键名修正：main() 组装的是 school1/school2，
                # 此前读 extra["school"] 恒空 —— --action 6 --school1=XX 被无视，
                # 恒用 config 旧志愿生成研报（实测生成「目标院校」而非传入校）。
                school = (extra.get("school1") or extra.get("school")
                          or info.get("school", "")).strip()
            major = input(f"请输入专业 [默认 {info['major']}]: ").strip() if interactive else ""
            if not major:
                major = (extra.get("major") or info.get("major", "")).strip()
            if not school or school in ("未指定", "目标院校"):
                print(colorize("\n[!] 目标院校尚未指定，请在【设置】中配置高校或传入指定高校名称。", Colors.RED))
                return True
            from skills import school_scout
            res = school_scout.scout_school(school=school, major=major, include_social=True, save_report=True)
            print(colorize(f"\n[+] 院校研报生成完毕: {res.get('saved_path')}", Colors.GREEN))
            if res.get("experience_dossier_path"):
                print(colorize(f"[+] 社媒真实经验档案沉淀完毕: {res.get('experience_dossier_path')}", Colors.GREEN))
        elif cmd_alias == "compare":
            info = get_config_summary()
            target_sch = info.get("school") or "目标院校"
            # [P1 修复·非交互不可用] 此前 --action 7 在非交互模式下，第二所高校恒为空，
            # 必然打印「需要指定第二所高校，未输入已取消」，等于该模式无法使用对标功能。
            # 现支持通过 `--school2` / `--school1` 传入；若未传且无第二校，
            # 自动回退到考生档案里的备选院校（backup_school）。
            target_mj = info.get("major") or ""
            # 第二所默认留空，避免硬编码「中山大学/武汉大学」导致误导
            s1 = input(f"请输入第一所对标高校 [默认 {target_sch}]: ").strip() if interactive else ""
            if not s1:
                s1 = (extra.get("school1") or "").strip()
            s2 = input("请输入第二所对标高校 [默认 无，必填]: ").strip() if interactive else ""
            if not s2:
                s2 = (extra.get("school2") or "").strip()
            if not s1:
                if target_sch in ("未指定", "目标院校"):
                    print(colorize("\n[!] 双校对标需要指定第一所高校，未输入已取消。", Colors.RED))
                    return True
                s1 = target_sch
            if not s2:
                # [审查修正] get_config_summary() 并不返回 backup_school，
                # 此前写 info.get("backup_school") 是死代码，永远取不到。改为直接读配置，
                # 兼容 backup_school / backup_target / study_plan.backup_school 多种键名。
                backup_sch = _read_backup_school()
                if backup_sch:
                    s2 = backup_sch
                    print(colorize(f"  [i] 未指定第二所高校，自动采用档案备选院校: {s2}", Colors.CYAN))
                else:
                    print(colorize(
                        "\n[!] 双校对标需要指定第二所高校。\n"
                        "      非交互模式请使用:  ky tui --action 7 --school2 \"XX大学\"\n"
                        "      或交互模式直接输入。已取消。", Colors.RED))
                    return True
            mj = input(f"请输入专业关键词 [默认 {target_mj}]: ").strip() if interactive else ""
            if not mj:
                mj = (extra.get("major") or "").strip()
            # [W12 P0-1] 裸导入改三端统一解析（此前直跑报 No module named 'tools.intelligence'）
            comp_res = resolve_intel_import().get_school_comparator().compare(
                school1_query=s1, school2_query=s2,
                major_keyword=(mj or target_mj), save_report=True
            )
            print("\n" + comp_res.get("terminal_report", ""))
            if comp_res.get("saved_path"):
                print(colorize(f"\n[+] 双校对标研报已落盘: {comp_res['saved_path']}", Colors.GREEN))
        elif cmd_alias == "watch":
            # [W12 P0-1] 裸导入改三端统一解析
            watcher = resolve_intel_import().AdmissionWatcher()
            watched = watcher.list_watched()
            if not watched:
                info = get_config_summary()
                s = info.get("school")
                if s and s not in ("未指定", "目标院校"):
                    add_res = watcher.add_watch(s)
                    if not add_res.get("success"):
                        print(colorize(f"\n[!] 自动纳入监控失败: {add_res.get('msg')}", Colors.YELLOW))
                        print(colorize("    请在「设置」核对目标院校，或使用 ky watch add <校名> 手动添加。", Colors.DIM))
            findings = watcher.check_updates()
            watched_now = watcher.list_watched()
            if not findings:
                print(colorize("\n[!] 当前没有正在监控的高校。请先在设置中配置目标院校，"
                               "或使用 ky watch add <校名> 添加监控目标。", Colors.YELLOW))
                return True
            print(colorize(f"\n[+] 招考动态巡检完成，已监控 {len(watched_now)} 所高校：", Colors.GREEN))
            for f in findings:
                st = f.get("status")
                status_color = Colors.GREEN if st == "UPDATED" else (Colors.YELLOW if st == "FETCH_FAILED" else Colors.CYAN)
                if st == "UPDATED":
                    titles = f.get("alert_titles") or []
                    print(f"  • {f.get('school')}: {colorize(f'发现 {len(titles)} 条新动态', status_color)}")
                    for t in titles[:5]:
                        print(f"      - {t}")
                    if len(titles) > 5:
                        print(f"      …… 另有 {len(titles) - 5} 条见报告文件")
                elif st == "FETCH_FAILED":
                    print(f"  • {f.get('school')}: {colorize(f.get('msg', '访问超时或受阻'), status_color)}")
                else:
                    print(f"  • {f.get('school')}: {colorize('暂无变动（页面指纹未变化）', status_color)}")
            # 报告落盘：含每校监控页面、巡检时间、新增要点与页面标题样本
            report_path = watcher.save_report(findings, watched_now)
            if report_path:
                print(colorize(f"\n[+] 巡检报告已落盘: {report_path}", Colors.GREEN))
                print(colorize("    报告含每所高校的监控页面、上次/本次巡检时间、新增要点与页面标题样本。", Colors.DIM))
            else:
                print(colorize("\n[!] 当前为只读模式或落盘失败，报告未写入磁盘，以上要点即本次巡检结果。", Colors.YELLOW))
        elif cmd_alias == "build":
            # [问题7 根因修复] 统一走 dashboard_build：frozen（exe 版）下旧实现
            # subprocess.run([sys.executable, build.py]) 的 sys.executable 是 GUI
            # 主程序自身 —— 实测点击「更新看板」弹出的是第二个 GUI 主界面、构建
            # 从未发生。现 frozen 改进程内执行（见 tools/dashboard_build.py）。
            try:
                from dashboard_build import run_dashboard_build
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.dashboard_build import run_dashboard_build
            # [W13 收口·本地入口分模式] 显式完整模式，与 更新看板.bat / ky build 一致；
            # 否则走缺省脱敏，把本地完整看板产物覆盖掉。
            rc = run_dashboard_build(workspace_root=ROOT, snapshot_opt_in=False)
            if rc == 0:
                print(colorize("\n[+] 考研看板已构建完成！可打开 docs/index.html 查看。", Colors.GREEN))
            elif rc == 127:
                print(colorize("\n[!] 未找到 05-考研看板/build.py 脚本", Colors.RED))
            else:
                print(colorize(f"\n[!] 看板构建失败（退出码 {rc}），请检查上方输出。", Colors.RED))
        elif cmd_alias == "checkin":
            # [2026-10-06 新增·TUI 报到入口] 业务逻辑**不重写**：播报文本来自
            # cli.shared.build_subject_checkin_brief（REPL/GUI/ky_cli 同一实现），
            # 今日任务生成走 study_planner.ensure_subject_today_task（REPL 报到
            # 分支同款）。本分支只做「选科目 + 调既有实现 + 说明后续去哪派题」。
            # 为什么需要这一层：R2/R3 仿真证实 TUI 十项里没有报到入口，考生在
            # 终端中枢无法触发私教链路；而 AGENTS.md 把「[科目]报到 → 做题 →
            # 交作业」列为日常主流程。
            _cfg = _load_config_dict()
            _plan = _cfg.get("study_plan", {}) or {}
            # 科目清单与 REPL 侧同源（SUBJECT_DIRS），并按 is_math_disabled 过滤
            # —— 不考数学的方案不得出现「数学报到」（R2-A4 统一口径）。
            try:
                from cli.shared import SUBJECT_DIRS, is_math_disabled
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.cli.shared import (  # type: ignore
                    SUBJECT_DIRS, is_math_disabled,
                )
            _math_off = is_math_disabled(_cfg)
            _subj_items = [(k, v[1]) for k, v in SUBJECT_DIRS.items()
                           if not (_math_off and k == "math")]
            if interactive:
                print(colorize("\n请选择要报到的科目:", Colors.CYAN))
                for _i, (_k, _label) in enumerate(_subj_items, 1):
                    print(f"  {_i}. {_label}")
                _raw = input("输入序号或科目代号 (回车用当前激活科目): ").strip()
                _pick = ""
                if _raw.isdigit() and 1 <= int(_raw) <= len(_subj_items):
                    _pick = _subj_items[int(_raw) - 1][0]
                elif _raw:
                    _pick = normalize_subject(_raw, "") or ""
                if not _pick:
                    _pick = normalize_subject(
                        extra.get("subject") or _cfg.get("active_subject"),
                        _subj_items[0][0])
            else:
                _pick = normalize_subject(
                    extra.get("subject") or _cfg.get("active_subject"),
                    _subj_items[0][0])
            if _pick == "math" and _math_off:
                print(colorize(
                    "\n[!] 当前备考方案为「不考数学」，不派发数学任务。"
                    "请选择英语 / 政治 / 专业课报到。", Colors.YELLOW))
                return True
            # 报到时确保该科今日任务文件存在（与 REPL 报到分支同款调用）——
            # 缺这一步，菜单 1「今日任务」在报到后仍显示 0/0。
            try:
                try:
                    from study_planner import ensure_subject_today_task
                except ImportError:  # pragma: no cover - 包式导入上下文
                    from tools.study_planner import ensure_subject_today_task  # type: ignore
                _task_res = ensure_subject_today_task(
                    _plan, _pick, workspace_root=ROOT) or {}
            except Exception:
                _task_res = {}
            try:
                from cli.shared import build_subject_checkin_brief
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.cli.shared import build_subject_checkin_brief  # type: ignore
            print(colorize(
                "\n" + build_subject_checkin_brief(_cfg, _pick) + "\n", Colors.GREEN))
            if _task_res.get("status") in ("created", "overwritten", "refreshed"):
                print(colorize(
                    f"  [i] 今日任务已生成: {_task_res.get('path')}"
                    f"（{_task_res.get('task_count', 0)} 项）", Colors.DIM))
            # 报到口令取自 REPL 的中文口令表（单一真源，避免这里另拼一个
            # 「英语报到」字符串 future-proof 地写错）。找不到就退回科目键。
            try:
                from cli.repl.router import CHINESE_SUBJECT_MAP
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.cli.repl.router import CHINESE_SUBJECT_MAP  # type: ignore
            _cmd = next((k for k, v in CHINESE_SUBJECT_MAP.items()
                         if v == _pick and k.endswith("报到")), f"{_pick}报到")
            print(colorize(
                "  [i] 题目派发、逐步讲解与采分点批改由私教大模型完成，"
                f"请在私教会话中继续：运行 ky 后输入「{_cmd}」。\n",
                Colors.CYAN))
        elif cmd_alias == "homework":
            # [2026-10-06 新增·TUI 交作业入口] 复用 REPL 的同一份指引文本
            # （cli.repl.router.build_homework_menu），三端话术天然一致。
            # 之所以只到「指引」为止：批改必须由私教大模型按采分点完成，
            # TUI 本身没有会话上下文，让考生在此粘贴答案是错位交互。
            try:
                from cli.repl.router import build_homework_menu
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.cli.repl.router import build_homework_menu  # type: ignore
            print(colorize(build_homework_menu(), Colors.CYAN))
            print(colorize(
                "\n  [i] 批改由私教大模型按采分点逐步赋分，请在私教会话中提交："
                "运行 ky（或 ky gui）后输入「交作业」，再按上方指引"
                "粘贴草稿照片（/paste）、提交答题卡（/batch）或直接贴推导文字。\n",
                Colors.DIM))
        elif cmd_alias in ("wechat_search", "wechat", "wx"):
            # [P0 修复] 非交互默认词此前硬编码「408计算机考研经验」，
            # 对自命题考生（如 814 信号与系统）完全无关；改为取自考生档案。
            # [P1 修复·0 命中] 档案里的专业串常形如
            #   「085400 电子信息-通信工程（085400-02）」——含数字专业代码与全角括号，
            #   直接拼接成关键词后公众号检索恒为 0 命中。此处做去噪：剔除专业代码数字、
            #   括号及其内容，只保留院校名与专业中文名。
            _cfg_info = get_config_summary()
            _school = (_cfg_info.get("school") or "").strip()
            _major = (_cfg_info.get("major") or "").strip()
            _major_clean = re.sub(r"[（(][^）)]*[）)]", "", _major)   # 去括号段
            _major_clean = re.sub(r"\b\d{4,6}\b", "", _major_clean)   # 去 4-6 位专业代码
            _major_clean = re.sub(r"\s+", " ", _major_clean).strip(" -·")
            if _school and _school not in ("未指定", "目标院校"):
                default_kw = f"{_school} {_major_clean} 考研".strip() if _major_clean else f"{_school} 考研"
            else:
                default_kw = "408计算机考研经验"
            kw = input(f"请输入微信公众号文章检索关键词 [默认 {default_kw}]: ").strip() if interactive else ""
            if not kw:
                kw = (extra.get("keyword") or "").strip()
            if not kw:
                kw = default_kw
            from skills.wechat_searcher import wechat_search
            res = wechat_search(keyword=kw, max_results=5, fetch_content=True, save_to_local=False)
            print(colorize(f"\n[+] 微信公众号文章检索完成 (共找到 {res.get('total', 0)} 篇，抓取正文 {res.get('fetched', 0)} 篇)：", Colors.GREEN))
            # [P2-9 修复·两端口径] 与 CLI 报告头同源打印各源成功/失败清单：
            # 此前 TUI 只透出 stderr 的 [warn]，与 CLI 结果数不同时无从判定原因。
            _status_line = " | ".join(res.get("source_status") or [])
            if _status_line:
                print(colorize(f"  检索源: {_status_line}", Colors.DIM))
            if not res.get("results"):
                print(colorize("  [i] 未检索到相关文章，建议更换关键词或使用 --source 显式指定数据源重试。", Colors.YELLOW))
            for idx, it in enumerate(res.get("results", [])[:5], 1):
                st = "已抓取" if it.get("fetched") else "仅标题"
                print(f"  [{idx}] {it.get('title')} ({it.get('account_display') or it.get('source_account') or '未识别（平台未公开）'}) "
                      f"[{it.get('date_display') or it.get('publish_date') or '未标注日期'}] [{st}]")
                print(f"      链接: {it.get('url')}")


    except Exception as e:
        print(colorize(f"\n[!] 执行过程中发生异常: {e}", Colors.RED))
        # [W12 P0-1] 批处理模式（--action）下异常必须反映到退出码：此前统一吞成
        # EXIT 0，脚本/CI 无法判定失败（实测 action 7 导入崩溃仍返回 0）。
        # 交互式界面（纯文本循环 / textual 线程）保持现状：打印后回菜单循环，
        # 不让一次异常踢出会话，也不静默杀死 textual 的工作线程。
        if batch:
            sys.exit(1)

    try:
        sys.stdout.flush()
    except Exception:
        pass
    return True


def should_use_textual() -> bool:
    """是否启用 textual 版界面。

    条件：textual 已安装 + 处于真实终端 + 未被 KY_TUI_LEGACY 显式禁用。
    任何一条不满足都回落到下面的纯文本循环 —— 降级必须可用，
    否则「TUI 层开箱即用」的承诺就破了。
    """
    if os.environ.get("KY_TUI_LEGACY"):
        return False
    if not _is_tty():
        return False
    try:
        import importlib.util

        return importlib.util.find_spec("textual") is not None
    except Exception:
        return False


def _run_text_loop():
    """纯文本交互循环（textual 不可用时的降级路径）。"""
    _enable_windows_vt()

    while True:
        if _is_tty():
            try:
                os.system("cls" if os.name == "nt" else "clear")
            except Exception:
                pass

        print("\n" + render_header())
        print(render_menu())
        try:
            # [W13 R2-4a 修复·文案口径] 序号提示必须与菜单实际编号一致；
            # [2026-10-06] 区间改为 menu_key_range() 派生（新增 11/12 后
            # 「0-10」即为错指引），不再手工维护。
            prompt_str = colorize(
                f"⌨️  请输入操作序号 [{menu_key_range()}] 或指令别名: ",
                Colors.BOLD + Colors.YELLOW)
            choice = input(prompt_str).strip()
            keep_running = execute_action(choice, interactive=True)
            if not keep_running:
                break
            # [UX] 执行结果保留在屏幕上，按 Enter 才返回主菜单（旧实现立即清屏，
            # 输出一闪而过，用户经常来不及看）
            input(colorize("\n按 Enter 键返回主菜单...", Colors.DIM + Colors.WHITE))
        except (KeyboardInterrupt, EOFError):
            print(colorize("\n👋 操作中断，已安全返回。\n", Colors.YELLOW))
            break


def run_tui_loop():
    """交互式主循环：优先 textual（键鼠双控），不可用时回落纯文本。"""
    if should_use_textual():
        try:
            try:
                from tui.app import run_textual_app
            except ImportError:  # pragma: no cover
                from tools.tui.app import run_textual_app  # type: ignore

            run_textual_app(ROOT)
            return
        except Exception as exc:               # pragma: no cover - 界面起不来不该阻断使用
            print(colorize(f"[!] 图形化终端界面启动失败（{exc}），已回落到纯文本模式。",
                           Colors.YELLOW))
    _run_text_loop()


def main():
    parser = argparse.ArgumentParser(description="考研学习链 (Kaoyan Study Chain) · 终端交互中枢 (TUI)")
    parser.add_argument("--action", "-a", type=str, default="", help="直接执行指定编号动作 (非交互模式)")
    parser.add_argument("--school1", type=str, default="", help="[action 6 院校侦察 / action 7 对标] 目标高校")
    parser.add_argument("--school2", type=str, default="", help="[action 7 对标] 第二所高校")
    parser.add_argument("--major", type=str, default="", help="[action 7 对标] 专业关键词")
    parser.add_argument("--keyword", "-k", type=str, default="", help="[action 10 公众号] 检索关键词")
    # [P3 修复·D7] 此前 action 4/5 无二级参数入口，非交互模式下必然「未输入已取消」，
    # 菜单项形同不可达；现补齐考纲 Diff 与切片入库的非交互入参。
    parser.add_argument("--new", dest="new_path", type=str, default="", help="[action 4 考纲Diff] 新考纲文件路径")
    parser.add_argument("--old", dest="old_path", type=str, default="", help="[action 4 考纲Diff] 基准(旧)考纲文件路径")
    parser.add_argument("--file", "-f", dest="in_file", type=str, default="", help="[action 5 切片入库] 待切片试题文件路径")
    parser.add_argument("--subject", type=str, default="", help="[action 2/3/5/11] 科目 math/eng/pol/pro（也接受 308/护理等名称）")
    parser.add_argument("--count", type=int, default=0, help="[action 2 组卷] 题量（默认 3）")
    parser.add_argument("--list", "-l", action="store_true", help="打印可用菜单并退出")
    args = parser.parse_args()

    if args.list:
        print(render_header())
        print(render_menu())
        return

    if args.action:
        extra = {
            "school1": args.school1,
            "school2": args.school2,
            "major": args.major,
            "keyword": args.keyword,
            "new": args.new_path,
            "old": args.old_path,
            "file": args.in_file,
            "subject": args.subject,
            "count": args.count,
        }
        execute_action(args.action, interactive=False, extra=extra, batch=True)
        return

    run_tui_loop()


if __name__ == "__main__":
    main()
