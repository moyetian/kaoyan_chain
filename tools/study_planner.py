# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan Study Chain) · 个人定制化必考方案设计核心引擎
设计维度：
1. 研考时间与战役节奏 (目标年份、初试日期、倒计时、备考阶段)
2. 报考院校专业与官方考纲 (数一/二/三/396、英一/二、政治、408/自命题)
3. 当下学情摸底与薄弱项诊断 (各科摸底分、核心失分点、痛点盲区)
4. 手头备考资料白名单 (各科已有权威书籍，严禁 AI 虚构未有资料)
5. 每日时间预算与各科切分 (每日总时长、四科投入小时、复习时间表)
6. 每周/每月休息与调节机制 (每周放风时间、每月模考复盘日、防内耗调节)
7. 私教辅导风格与提分目标矩阵 (各科目标分、总分目标、辅导风格)
"""

import sys
import re
import os
import json
from pathlib import Path
from typing import Dict, List

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from datetime import datetime, date

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

# [审计 2026-09-30 P1-7 出站收敛] 规划生成的大模型请求携带 `Authorization: Bearer
# <key>`，此前裸 urlopen 默认跟随 3xx —— 恶意 base_url 回 302 即可收割 Key。
# 统一走 net_guard.safe_urlopen（SSRF 校验 + 逐跳复核 + 跨主机剥离 Authorization）。
try:
    from net_guard import safe_urlopen  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.net_guard import safe_urlopen  # type: ignore  # noqa: E402

# [根因修复·日期硬编码] 初试日期统一由 exam_calendar 提供，替代本文件内
# `f"{year}-12-19"` 与 `plan.get('exam_date', '2026-12-19' / '2027-12-19')` 等多处硬编码
try:
    import exam_calendar  # noqa: E402
except ImportError:  # pragma: no cover
    from tools import exam_calendar  # type: ignore  # noqa: E402

# plan 中缺失 exam_date 时的兜底初试日：按日历推算，绝不写死年份
_FALLBACK_EXAM_DATE = exam_calendar.resolve_exam_date({})[0].isoformat()

#: 摸底分解析：只认「数字 + 分」的明确写法（「50分」「摸底40分」）。
_BASELINE_SCORE_RE = re.compile(r"(\d+)\s*分")


def baseline_total_label(plan, keys) -> str:
    """把各科摸底文本汇总为矩阵表「合计」行文案（D8 修复：不再残留 [摸底总分] 占位符）。

    摸底字段是自由文本（「摸底 58 分」「四级 480，摸底 58 分」「未启动」都合法）。
    每科取**最后一个 ≤300 的「N分」数值**（摸底分通常写在句末；「四级 480 分，
    摸底 58 分」中 480 是四级成绩、58 才是摸底分，>300 的值直接剔除）；任一科
    找不到有效数值 → 返回「见各科摸底」。不猜测、不把定性描述折算成分数。
    """
    total = 0
    for k in keys:
        scores = [int(x) for x in _BASELINE_SCORE_RE.findall(str(plan.get(k, "") or ""))]
        valid = [s for s in scores if s <= 300]
        if not valid:
            return "见各科摸底"
        total += valid[-1]
    return f"约 {total} 分"


_CN_SUBJECT_NUM = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六"}


def subject_count_label(plan) -> str:
    """按方案实际科目数返回「N科」中文标签（如「四科」「三科」「两科」）。

    [P2-3 修复·四科文案残留] 此前总结页/进度提示硬编码「四科」，不考数学
    （实际 3 科）或 199 管综（实际 2 科）的考生会看到与方案不符的文案。
    口径与各处 math_disabled / is_mode_b / is_mode_c 判定一致：
    mode_c（199 管综）= 管综+英语 2 科；mode_b（不考数学双专业课）=
    英语+政治+专业课一+专业课二 4 科；不考数学 = 3 科；常规统考 = 4 科。
    """
    pro_name = str(plan.get("pro_name", ""))
    pro2_name = str(plan.get("pro2_name", "") or "").strip()
    is_mode_c = (plan.get("exam_mode") in ("mode_c", "mgmt_199")
                 or plan.get("pol_disabled") or "199" in pro_name)
    is_mode_b = (plan.get("exam_mode") in ("mode_b", "no_math_dual_pro")
                 or bool(pro2_name))
    math_off = (is_mode_b or is_mode_c
                or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"}
                or plan.get("math_name") == "不考数学")
    if is_mode_c:
        n = 2
    else:
        n = 3 + (0 if math_off else 1) + (1 if is_mode_b else 0)
    return f"{_CN_SUBJECT_NUM.get(n, str(n))}科"

#: 工作区根。默认是「本文件上两级」（即仓库根），但允许 ``KY_WORKSPACE_ROOT``
#: 环境变量覆盖。
#:
#: [P5 修复] 为什么必须可覆盖：独立套件 ``tools/test_ky_suite.py`` 会把脚本拷进
#: 一个**假工作区**再运行（它自己的 ROOT=tmp），但本模块是被 PYTHONPATH 从
#: **真实仓库**导入的 —— 于是本模块所有写盘都会落到真实考生工作区（实测：
#: 跑一次守卫测试就会在真实仓库留下被改写的 AGENTS.md/规划/今日任务）。
#: 调用方（测试）把 KY_WORKSPACE_ROOT 指向自己的 tmp 工作区即可彻底隔离。
ROOT = Path(os.environ.get("KY_WORKSPACE_ROOT")
            or resolve_workspace_root(__file__))
CONFIG_FILE = ROOT / "ky_config.json"

# 跨平台控制台 UTF-8 编码保护 (防止 Windows GBK 环境乱码或崩溃)
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ANSI 终端色彩
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"

def colorize(text, color_code):
    return f"{color_code}{text}{C.RESET}"

# 备考阶段定义
STAGES = {
    "1": ("基础夯实阶段", "地毯式过教材与基础考点，吃透基本概念与公式推导，打牢地基"),
    "2": ("强化题型攻坚阶段", "抓核心 80% 高频必考得分盘，专题题型专练，严查推导与步骤规范化"),
    "3": ("真题冲刺模考阶段", "全真近 15 年真题闭卷限时模考，阅卷人视角采分点严格批改"),
    "4": ("临考查漏补缺阶段", "核心公式与帽子词遮罩自测默写，错题队列全部清零，调整临考生物钟")
}

# 辅导风格定义
STYLES = {
    "1": ("严格把关·保姆提分型 (Strict & Disciplined)", "真题阅卷人严苛视角，步骤明确赋分扣分，严抓跳步计算失误，强制错题追查与重做"),
    "2": ("高效应试·高频秒杀型 (High-Yield Hacker)", "80/20法则导向，聚焦高频必考盘，传授代入排除、特征解题模板与阅读主干速抓套路"),
    "3": ("温和启发·减负鼓励型 (Encouraging Mentor)", "难题小步拆解，生活化比喻，先肯定思路再温和纠错，降低复习挫败感与焦虑内耗"),
    "4": ("深度原理·学霸溯源型 (Deep Conceptual Master)", "从命题人设陷阱视角反推题干，溯源定理几何/物理背景，打通跨章节知识图谱")
}

# 学员作息类型默认建议
ROUTINE_PRESETS = {
    "1": {
        "name": "全脱产 / 毕业二战 / 假期全天备考",
        "total_hours": 8.5,
        "math_hours": 3.0,
        "eng_hours": 2.0,
        "pol_hours": 1.0,
        "pro_hours": 2.5,
        "rest_weekly": "每周日晚 18:00~22:30 放松休整，不安排新题",
        "rest_monthly": "每月最后一个周日全天进行全真闭卷模考与全科雷达复盘"
    },
    "2": {
        "name": "在校生备考 (兼顾部分大四课程/毕业设计)",
        "total_hours": 6.5,
        "math_hours": 2.5,
        "eng_hours": 1.5,
        "pol_hours": 0.5,
        "pro_hours": 2.0,
        "rest_weekly": "每周六晚或周日半天放风，调节身心",
        "rest_monthly": "每月最后周日进行一次全科阶段性复盘测试"
    },
    "3": {
        "name": "在职人员备考 (工作日晚间 + 周末集中冲刺)",
        "total_hours": 4.0,
        "math_hours": 1.5,
        "eng_hours": 1.0,
        "pol_hours": 0.5,
        "pro_hours": 1.0,
        "rest_weekly": "工作日保底 3.5 小时，周日预留半天用于家庭或个人休整",
        "rest_monthly": "月末周末全天进行单科限时自测"
    }
}

def scan_local_materials(subject_rel_path):
    """
    真实扫描本地各科目 参考资料/ 目录中的实际文件 (严防虚构不存在的书籍)
    subject_rel_path: 如 '01-数学' / '02-英语' / '03-思想政治理论' / '04-专业课'
    返回: 相对「参考资料/」的路径列表（递归含子目录；过滤 .gitkeep/readme 等占位文件）

    [问题5 根因修复·子目录资料不识别] 旧实现只 iterdir() 单层：学员把资料
    整理进子目录（如 参考资料/英语真题/2020.pdf）后扫描不到，向导与报到
    显示「未放置资料」。现与 material_scanner.iter_material_files 同源递归。
    """
    mat_dir = ROOT / subject_rel_path / "参考资料"
    valid_exts = {".pdf", ".doc", ".docx", ".epub", ".txt", ".md", ".png", ".jpg", ".jpeg"}
    try:
        try:
            from material_scanner import iter_material_files, relative_label
        except ImportError:
            from tools.skills.material_scanner import iter_material_files, relative_label
        files = iter_material_files(mat_dir, valid_exts=valid_exts)
        return sorted(relative_label(mat_dir, f) for f in files)
    except Exception:
        return []

def calculate_countdown(target_date_str):
    """计算距离初试日期的倒计时天数

    [根因修复·日期硬编码] 日期串无法解析时，旧实现返回 0（等价于「今天就是初试」），
    会将学员的备考节奏误判为最后一天。现改为回退到 exam_calendar 的日历推算。
    """
    try:
        t_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        today = date.today()
        days = (t_date - today).days
        return max(0, days)
    except Exception:
        try:
            return exam_calendar.countdown_days({})
        except Exception:
            return 0


def _days_left_now(plan) -> int:
    """[UT4 修复·PLANNER-3] 按当日与 exam_date 实时重算倒计时（钳非负）。

    plan 里的 ``days_left`` 是建档当日写入的快照，跨日引用会与动态口径矛盾
    （UT4 西医沙箱 P1-4 实测：09-30 建档写 80，10-01 报到重写今日任务仍 80）。
    口径与 apply_study_plan 写根 AGENTS.md 的 G2/G2-b 修复一致：exam_date 可
    解析则按当日重算并钳非负；不可解析时回退快照值（同样钳非负）。
    """
    _exam = (plan or {}).get("exam_date") or _FALLBACK_EXAM_DATE
    try:
        return max(0, (date.fromisoformat(str(_exam)) - date.today()).days)
    except (TypeError, ValueError):
        try:
            return max(0, int((plan or {}).get("days_left", 0) or 0))
        except (TypeError, ValueError):
            return 0


def is_custom_math_key(math_key) -> bool:
    """[UT4 修复·PLANNER-6] 判定数学科目是否为「院校自主命题数学」（custom）。

    custom 不在 MATH_SYLLABI（math1/math2/math3/math396）也不在 MATH_NONE_KEYS
    （none/no/不考数学）中，由 init_workspace 完整向导选项 [5] 或非交互 preset
    写入（math_key=custom）。自命题数学没有全国统考大纲，科目显示名与白名单
    文案必须区别于统考科目——判定收口在本函数（单一真源），与
    syllabus_manager.apply_syllabus_selection 的 custom 分支同口径。
    """
    k = str(math_key or "").strip().lower()
    if not k or k in {"none", "no", "不考数学"}:
        return False
    try:
        try:
            from syllabus_manager import MATH_SYLLABI as _math_syllabi
        except ImportError:  # pragma: no cover - 包式导入上下文
            from tools.syllabus_manager import MATH_SYLLABI as _math_syllabi
        return k not in _math_syllabi
    except Exception:  # pragma: no cover - 模块不可用时按字面量判定
        return k == "custom"


def _brief_books(raw, max_items: int = 2) -> str:
    """把冗长的白名单资料清单折叠成「前 N 份 … 等 M 份」，避免单行超宽。

    [根因修复·单行超宽截断] 此前把整条白名单原样拼进「今日任务」表格单元格：
    专业课切片多时单行长度逼近 400 字符，在 80/100 列的终端与 markdown 阅读器里
    都会被硬截断，学员看到的是半截文件名列表。白名单全量清单仍完整保留在
    00_考研全科总战役规划.md 第二节与 AGENTS.md 中，此处只放摘要。
    """
    s = re.sub(r"^\[本地资料库已就绪\]:\s*", "", str(raw or "")).strip()
    if not s:
        return "（待挂载）"
    items = [x.strip() for x in s.split(",") if x.strip()]
    if len(items) <= max_items:
        return "、".join(items)
    return "、".join(items[:max_items]) + f" 等 {len(items)} 份"


def _pro_syllabus_ready(workspace_root=None) -> bool:
    """专业课考试大纲是否已就绪（``ready``）。

    与 :func:`syllabus_manager.pro_syllabus_state` 同源判定。探测失败时按
    「就绪」处理，维持既有文案——避免因探测异常向考生刷出无关警告。
    """
    try:
        try:
            from syllabus_manager import pro_syllabus_state
        except ImportError:
            from tools.syllabus_manager import pro_syllabus_state
        root = Path(workspace_root) if workspace_root else ROOT
        return pro_syllabus_state(root) == "ready"
    except Exception:
        return True


def _material_phrase(raw, verb: str, *, is_pro: bool = False,
                     workspace_root=None) -> str:
    """今日任务「题源出处」短语：有真实白名单时引用，无资料时不引用白名单。

    [P17 修复·无资料却派白名单刷题] 白名单为空时其默认值是一句
    「暂未放置实体资料（私教严格按【…】官方考纲出题，严禁虚构书目）」的说明，
    旧实现把整句塞进「精做白名单【…】20 道核心选择题自测」，读起来自相矛盾，
    还等于让考生去刷一份不存在的资料。无资料时改为「按官方考纲{verb}」。

    [问题5 修复·专业课无大纲却宣称按纲] 公共课（英/政/数）有统考大纲，
    「按官方考纲」属实；自命题专业课的大纲是考生自填件，占位/缺失时该短语
    即为虚假承诺，改为如实标注「待导入」。
    """
    s = re.sub(r"^\[本地资料库已就绪\]:\s*", "", str(raw or "")).strip()
    if not s or s == "不考数学" or s.startswith("暂未放置实体资料"):
        if is_pro and not _pro_syllabus_ready(workspace_root):
            return f"（⚠️ 专业课大纲与真题待导入）{verb}"
        return f"按官方考纲{verb}"
    return f"{verb}白名单【{_brief_books(s)}】"


def _load_existing_study_plan():
    """读取工作区既有 ``ky_config.json`` 里的 ``study_plan``，供非交互向导作基线。

    返回空字典表示「工作区尚无配置」（首次初始化），此时才允许回落内置默认值。
    """
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    plan = cfg.get("study_plan")
    return dict(plan) if isinstance(plan, dict) else {}


def run_study_plan_wizard(interactive=True, preset_data=None):
    """
    运行个人定制化必考方案向导
    interactive: 是否交互式提问
    preset_data: 预填数据字典 (用于自动化测试或批量写入)
    """
    _plan_from_existing = (not preset_data) and (not interactive)
    if preset_data:
        plan = preset_data.copy()
    elif interactive:
        plan = {}
    else:
        # [R2-D8 修复·非交互向导静默重置用户方案 · P0]
        # 旧行为：``preset_data`` 缺省时 plan 恒为 {}，而本函数各维度的非交互分支
        # 一律写作 ``plan.get(key, 内置默认值)``，于是「不带 preset 的非交互重跑」
        # 等价于「把内置默认方案整份写回磁盘」—— 默认值是数学二 302 / 英语二 204 /
        # 408 / 同济教材 / 8.5 小时，与考生真实方案毫无关系。
        #
        # 实锤（2026-09-19）：``tools/test_ky_suite.py:596`` 就是这条路径，且该脚本
        # 被 README / CONTRIBUTING / CI 都写成「用户应执行的测试命令」，用户照做一次
        # 自己的备考方案就被抹成默认模板。同一天并发跑了 4 份套件，把真实工作区的
        # ky_config.json、根 AGENTS.md、四科 AGENTS.md / 00_*备考总规划.md /
        # 考试大纲.md / _状态/今日任务.md 全部覆盖。
        #
        # 修法取舍：不在每个维度各加一次「已有值优先」判断（本仓库已因「同一件事
        # 两处实现、只修一处」被坑两次），而是在唯一入口把基线换成工作区既有配置，
        # 使非交互重跑天然幂等；仅当工作区尚无 ky_config.json 时才回落默认值。
        plan = _load_existing_study_plan()

    print(f"""
{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮
│  🎓 考研全科 AI 私人教师 · 个人专属定制化必考方案设计向导             │
│  (7 大维度：时间 · 考纲 · 资料白名单·学情摸底·每日投入·作息·辅导风格)  │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")
    print("💡 提示：每一项均已配置科学默认值，直接按回车 (Enter) 即可沿用推荐预设。\n")

    # ── 维度 1: 研考时间与战役节奏 ──
    print(colorize("【维度 1/7 · ⏱️ 研考时间与战役节奏】", C.BOLD))
    curr_year = datetime.now().year
    # [缺陷修复·年份口径与校验] 此前该问项写作「目标考研初试年份」，默认值取
    # `curr_year if month<11 else curr_year+1`（今年 2026 → 默认 2026），
    # 但项目其余部分（init_workspace / ky_config / AGENTS.md）一律把该数字当
    # 「考研年份＝入学年」语义（2027 表示 2026 年 12 月初试）。
    # 两种口径混用会让按文档作答的考生把初试日整整写晚一年；
    # 且输入 "1" 这类值会直接推算出 0001-12-15，全程无校验。
    try:
        default_enroll_year = exam_calendar.infer_exam_year() + 1
    except Exception:
        default_enroll_year = curr_year + 1
    if interactive:
        target_year = input(
            f"  1. 报考年份 / 入学年 [如 2027 表示 2026 年 12 月初试，默认: {default_enroll_year}]: "
        ).strip() or str(default_enroll_year)
        _yr_raw = str(target_year)[:4]
        _yr = int(_yr_raw) if _yr_raw.isdigit() else default_enroll_year
        if not (curr_year <= _yr <= curr_year + 5):
            print(colorize(
                f"  [!] 年份 {_yr} 超出合理范围（{curr_year}~{curr_year + 5}），已回退为 {default_enroll_year}。",
                C.YELLOW))
            _yr = default_enroll_year
        target_year = str(_yr)
        # 初试日恒为「入学年的头一年 12 月第 3 个周六」
        default_exam_date = exam_calendar.exam_date_for_enrollment_year(_yr).isoformat()
        exam_date = input(f"  2. 预计初试日期 (YYYY-MM-DD) [默认: {default_exam_date}]: ").strip() or default_exam_date
        
        print("\n  --- 请选择您当前所处的备考阶段 ---")
        for k, (s_name, s_desc) in STAGES.items():
            print(f"    [{k}] {s_name}\n        -> {s_desc}")
        stage_choice = input("  选择备考阶段 (1~4) [默认 2]: ").strip() or "2"
        stage_name = STAGES.get(stage_choice, STAGES["2"])[0]
    else:
        # 非交互分支：沿用同一口径（target_year = 入学年）
        target_year = plan.get("target_year", str(default_enroll_year))
        _yr_raw = str(target_year)[:4]
        _yr = int(_yr_raw) if _yr_raw.isdigit() else default_enroll_year
        if not (curr_year <= _yr <= curr_year + 5):
            _yr = default_enroll_year
        target_year = str(_yr)
        exam_date = plan.get("exam_date") or exam_calendar.exam_date_for_enrollment_year(_yr).isoformat()
        stage_name = plan.get("stage_name", STAGES["2"][0])

    days_left = calculate_countdown(exam_date)
    plan["target_year"] = target_year
    plan["exam_date"] = exam_date
    plan["stage_name"] = stage_name
    plan["days_left"] = days_left
    print(colorize(f"  [√] 战役倒计时: {days_left} 天 · 当前战役阶段: {stage_name}\n", C.GREEN))

    # ── 维度 2: 报考院校、专业与官方考纲 ──
    print(colorize("【维度 2/7 · 📜 报考院校与官方考纲锁定】", C.BOLD))
    try:
        from tools import syllabus_manager
    except ImportError:
        import syllabus_manager

    if interactive:
        school = input("  1. 目标院校 [默认: 目标院校]: ").strip() or "目标院校"
        major = input("  2. 报考专业与代码 [如 085400 电子信息]: ").strip() or "报考专业"
        # [补齐能力·备选院校] 此前向导不采集备选院校，导致「双校对标」在未显式指定
        # 第二所高校时无数据可回退（TUI --action 7 非交互模式因此不可用）。
        # 现补采，写入 study_plan.backup_school；留空表示无备选。
        backup_school = input(
            "  3. 备选院校 (用于双校对标，可留空) [默认: 无]: "
        ).strip()
        if backup_school in ("无", "none", "None", "-"):
            backup_school = ""

        print("\n  --- 请选择您的数学科目方案 ---")
        for k, info in syllabus_manager.MATH_SYLLABI.items():
            m_desc = info.get('description') or info.get('scope') or ''
            print(f"    [{k}] {info['name']} - {m_desc}")
        print("    [none] 不考数学")
        # [问题3 同族修复·无效输入静默回退] 菜单键是 MATH_SYLLABI 的键
        # （math396，不是 "396"），旧实现把任何无法识别的输入原样存进 plan：
        # 考生输入 "2" 以为选了数学一，实际 math_key="2" → apply_syllabus_selection
        # 走「院校自主命题数学」占位分支，01-数学/考试大纲.md 变成自命题骨架、
        # 科目名显示「数学」，全程零提示。现改为：无效值显式告警并回退菜单默认值，
        # 且提示串里的有效值取源码真源（不再手写 "396" 这类与键不一致的字样）。
        _ok_math = "/".join(list(syllabus_manager.MATH_SYLLABI) + ["none"])
        m_choice = input(f"  请选择数学科目 ({_ok_math}) [默认: math2]: ").strip().lower() or "math2"
        if m_choice not in syllabus_manager.MATH_SYLLABI and m_choice not in syllabus_manager.MATH_NONE_KEYS:
            # [UT4 修复·PLANNER-5] 旧文案「如需院校自命题数学请重跑 ky plan 并选择
            # 对应项」是死胡同：ky plan 菜单本就没有自命题项（UT4 理论物理沙箱
            # BUG-2）。改为指向真实可行路径：init_workspace 完整向导选项 [5]，
            # 或非交互 preset 配置（math_key=custom）。
            print(colorize(
                f"  [!] 无效选择 '{m_choice}'，已回退使用默认 math2（有效值：{_ok_math}）。"
                f"如需院校自主命题数学：请运行 py tools/init_workspace.py 完整向导并选择"
                f" [5] 院校自主命题数学，或使用非交互 preset 配置（math_key=custom）。", C.YELLOW))
            m_choice = "math2"

        print("\n  --- 请选择您的英语科目方案 ---")
        for k, info in syllabus_manager.ENGLISH_SYLLABI.items():
            e_desc = info.get('features') or info.get('scope') or ''
            print(f"    [{k}] {info['name']} - {e_desc}")
        _ok_eng = "/".join(syllabus_manager.ENGLISH_SYLLABI)
        e_choice = input(f"  请选择英语科目 ({_ok_eng}) [默认: eng2]: ").strip().lower() or "eng2"
        if e_choice not in syllabus_manager.ENGLISH_SYLLABI:
            print(colorize(
                f"  [!] 无效选择 '{e_choice}'，已回退使用默认 eng2（有效值：{_ok_eng}）。",
                C.YELLOW))
            e_choice = "eng2"

        print("\n  --- 请选择您的专业课方案 ---")
        print("    [1] 全国统考 408 计算机学科专业基础")
        print("    [2] 全国统考 199 管理类综合能力")
        print("    [3] 院校自命题专业课")
        # [P2-12 修复·护理考生无预设] 308 护理综合：载入模块骨架（含【待自填】告警）
        print("    [4] 全国统考/自命题 308 护理综合 (载入模块骨架，须按目标院校官网核验)")
        print("    [5] 全国统考 312 心理学专业基础综合 (须按目标院校官网核验)")
        # [2026-10-06 批次·内置统考/联考/自命题大纲] 注册表入口（清单动态生成）
        _builtin_codes = syllabus_manager.builtin_pro_syllabus_codes()
        print(f"    [6] 更多内置统考/联考/自命题大纲 (按科目代码载入，共 {len(_builtin_codes)} 科；"
              f"自命题为参考框架，以院校官方大纲为准)")
        p_sel = input("  请选择专业课类型 (1~6) [默认: 1]: ").strip() or "1"
        if p_sel == "6":
            _sel = syllabus_manager.prompt_builtin_pro_selection()
            if _sel:
                pro_type, pro_name = _sel
            else:
                pro_type = "custom"
                pro_name = input("  请输入自命题专业课科目名称与代码 [如 801 信号与系统]: ").strip() or "专业课"
        elif p_sel == "1":
            pro_type = "408"
            pro_name = "408 计算机学科专业基础"
        elif p_sel == "2":
            pro_type = "199"
            pro_name = "199 管理类综合能力"
        elif p_sel == "4":
            pro_type = "308"
            pro_name = "308 护理综合"
        elif p_sel == "5":
            pro_type = "312"
            pro_name = "312 心理学专业基础综合"
        else:
            pro_type = "custom"
            pro_name = input("  请输入自命题专业课科目名称与代码 [如 801 信号与系统]: ").strip() or "专业课"
    else:
        school = plan.get("school", "目标院校")
        major = plan.get("major", "报考专业")
        backup_school = (plan.get("backup_school") or "").strip()
        m_choice = plan.get("math_key", "math2")
        e_choice = plan.get("eng_key", "eng2")
        pro_type = plan.get("pro_type", "408")
        pro_name = plan.get("pro_name", "408 计算机学科专业基础")

    plan["school"] = school
    plan["major"] = major
    plan["backup_school"] = backup_school
    plan["math_key"] = m_choice
    plan["eng_key"] = e_choice
    plan["pro_type"] = pro_type
    plan["pro_name"] = pro_name
    # [UT4 修复·PLANNER-6] math_key=custom（院校自主命题数学）此前回落通用名
    # 「数学」，白名单又宣称「按官方考纲出题」——自命题数学无全国统考大纲，
    # 显示名与防幻觉锚点双失真（UT4 理论物理沙箱 BUG-5：三端统一显示通用名）。
    # 现默认名改为「数学（院校自命题）」；考生已自定义过科目名（非通用「数学」）
    # 时不覆盖。
    if m_choice != "none" and is_custom_math_key(m_choice):
        _prev_math_name = str(plan.get("math_name") or "").strip()
        m_title = _prev_math_name if _prev_math_name and _prev_math_name != "数学" else "数学（院校自命题）"
    else:
        m_title = syllabus_manager.MATH_SYLLABI.get(m_choice, {}).get("name", "数学") if m_choice != "none" else "不考数学"
    e_title = syllabus_manager.ENGLISH_SYLLABI.get(e_choice, {}).get("name", "英语")
    plan["math_name"] = m_title
    plan["eng_name"] = e_title
    print(colorize(f"  [√] 考纲锁定: {m_title} + {e_title} + 思想政治理论 + {pro_name}\n", C.GREEN))

    # ── 维度 3: 当下学情摸底与痛点诊断 ──
    print(colorize("【维度 3/7 · 📊 当下学情摸底与核心痛点诊断】", C.BOLD))
    print("  (让 AI 私教了解您的真实起点，从而针对性查漏，拒绝假大空)")
    if interactive:
        # [收尾修复·编号错乱] 不考数学时跳过数学问项，后续编号动态前移
        # （此前英语仍显示"2."、政治"3."，编号硬编码）。
        _n_eng = 1 if m_choice == "none" else 2
        _n_pol, _n_pro = _n_eng + 1, _n_eng + 2
        if m_choice != "none":
            math_baseline = input("  1. 数学目前摸底成绩/基础水平 [如: 50分 / 零基础 / 60分]: ").strip() or "待摸底 (零基础)"
            math_weakness = input("     数学最核心失分点/痛点 [如: 极限计算常错、证明不会、概念模糊]: ").strip() or "待首次自测诊断 (从零建立学情雷达)"
        else:
            math_baseline = "不考数学"
            math_weakness = "无"

        eng_baseline = input(f"  {_n_eng}. 英语目前摸底成绩/英语基础 [如: 四级450 / 六级未过 / 摸底水平分]: ").strip() or "待摸底 (基础起步)"
        eng_weakness = input("     英语最核心失分点/痛点 [如: 长难句读不懂、阅读推断题失分多、作文写不出]: ").strip() or "待首次自测诊断 (从零建立长难句与题型雷达)"

        pol_baseline = input(f"  {_n_pol}. 政治目前复习进度/摸底水平 [如: 未启动 / 已听网课 / 摸底40分]: ").strip() or "待摸底 (未启动)"
        pol_weakness = input("     政治主要痛点 [如: 马原哲学原理混淆、多选常错、帽子词记不牢]: ").strip() or "待首次自测诊断 (从零建立选择题得分盘雷达)"

        pro_baseline = input(f"  {_n_pro}. 专业课 ({pro_name}) 目前摸底水平 [如: 跨考零基础 / 摸底水平分]: ").strip() or "待摸底 (基础起步)"
        pro_weakness = input("     专业课核心失分点/痛点 [如: 核心考点未过、大题步骤不规范]: ").strip() or "待首次自测诊断 (从零建立专业课知识图谱雷达)"
    else:
        math_baseline = plan.get("math_baseline", "待摸底 (零基础)")
        math_weakness = plan.get("math_weakness", "待首次自测诊断 (从零建立学情雷达)")
        eng_baseline = plan.get("eng_baseline", "待摸底 (基础起步)")
        eng_weakness = plan.get("eng_weakness", "待首次自测诊断 (从零建立长难句与题型雷达)")
        pol_baseline = plan.get("pol_baseline", "待摸底 (未启动)")
        pol_weakness = plan.get("pol_weakness", "待首次自测诊断 (从零建立选择题得分盘雷达)")
        pro_baseline = plan.get("pro_baseline", "待摸底 (基础起步)")
        pro_weakness = plan.get("pro_weakness", "待首次自测诊断 (从零建立专业课知识图谱雷达)")

    plan["math_baseline"] = math_baseline
    plan["math_weakness"] = math_weakness
    plan["eng_baseline"] = eng_baseline
    plan["eng_weakness"] = eng_weakness
    plan["pol_baseline"] = pol_baseline
    plan["pol_weakness"] = pol_weakness
    # [P2-6 修复·建档缺字段] 政治科目名建档即写入，与 material_scanner 的
    # mount 补齐口径同源（规范名「思想政治理论」）。此前唯一写入点是 ky mount
    # 的 material_scanner 内存规范化：建档后未 mount 前 ky_config 缺该字段
    # （三沙箱实测 mount 输出「pol_name 旧:（无）」）。既有值（若有）不覆盖。
    if not str(plan.get("pol_name") or "").strip():
        plan["pol_name"] = "思想政治理论"
    plan["pro_baseline"] = pro_baseline
    plan["pro_weakness"] = pro_weakness
    # [P2 修复·宣称与实况不符] 此处只完成摸底数据收集（写入 plan），雷达与
    # 学员档案的实际注入发生在建档收尾 generate_plan_and_today_files（见
    # seed_radars_from_plan / init_state_profiles_from_plan）。旧文案「痛点
    # 已注入…雷达」在此时点为不实宣称（三沙箱实测：向导结束后雷达仍全为
    # 「待首次自测评估」）。现如实描述时点。
    print(colorize("  [√] 学情摸底数据已记录，建档收尾将自动注入各科薄弱项雷达与学员档案！\n", C.GREEN))

    # ── 维度 4: 手头备考资料白名单 (真实扫描与核验，杜绝 AI 凭空捏造) ──
    print(colorize("【维度 4/7 · 📚 手头已有备考资料白名单真实核验】", C.BOLD))
    print(colorize("  ⚠️ 【防虚构铁律】：私教仅能从您指定的真实白名单出题，严禁捏造未拥有的书籍！", C.YELLOW))

    local_math_files = scan_local_materials("01-数学")
    local_eng_files = scan_local_materials("02-英语")
    local_pol_files = scan_local_materials("03-思想政治理论")
    local_pro_files = scan_local_materials("04-专业课")

    def make_default_book_label(files, subj_name, key=None):
        """白名单缺省文案。

        [问题5 补修·向导绕过占位检测] 此前这里一律写「私教严格按【…】官方考纲
        出题」，而非交互路径的 ``_material_phrase`` / 挂载路径的
        ``pro_books_placeholder_text`` 都已按大纲三态分档 —— 同一要求两处实现
        只修了一处，于是自命题大纲仍是【待自填】占位时，「今日任务」如实标注
        「专业课大纲与真题待导入」，而 AGENTS.md 与 ky_config.json 却宣称
        「按【837 …】官方考纲出题」。现专业课统一复用
        ``pro_books_placeholder_text``（与 ky mount 同源），数学/英语/政治
        （内置统考大纲不会是占位）维持原文案。

        [问题8 修复·不考数学却要求按纲出题] 不考数学时数学白名单一律写中性
        表述「不考数学」，不再生成「按【不考数学】官方考纲出题」这类自相矛盾文案。
        """
        if files:
            return f"[本地资料库已就绪]: {', '.join(files)}"
        if key == "math" and (str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"}
                              or plan.get("math_name") == "不考数学"):
            return "不考数学"
        if key == "pro":
            try:
                try:
                    from syllabus_manager import pro_books_placeholder_text
                except ImportError:
                    from tools.syllabus_manager import pro_books_placeholder_text
                return pro_books_placeholder_text(ROOT, str(subj_name))
            except Exception:
                pass
        if key == "math" and is_custom_math_key(plan.get("math_key")):
            # [UT4 修复·PLANNER-6] 自命题数学无全国统考大纲，白名单缺省文案
            # 不得宣称「官方考纲」（UT4 理论物理沙箱 BUG-5），改按自命题大纲
            # 表述，与 material_scanner 的挂载文案同口径。
            return "暂未放置实体资料（私教按目标院校自命题大纲出题，严禁虚构书目）"
        return f"暂未放置实体资料（私教严格按【{subj_name}】官方考纲出题，严禁虚构书目）"

    def_m = make_default_book_label(local_math_files, m_title, key="math")
    def_e = make_default_book_label(local_eng_files, e_title, key="eng")
    def_p = make_default_book_label(local_pol_files, "思想政治理论", key="pol")
    def_pro = make_default_book_label(local_pro_files, pro_name, key="pro")

    if interactive:
        # [P16 修复·向导编号跳号] 不考数学时跳过数学问项，维度 4 的编号动态前移
        # （此前英语仍显示"2."、政治"3."、专业课"4."，缺 1 造成跳号）。
        _n_book = 1 if m_choice == "none" else 2
        if m_choice != "none":
            hint_m = f"已扫描本地: {', '.join(local_math_files)}" if local_math_files else "本地参考资料文件夹暂未放入文件"
            print(f"  1. 数学权威资料 ({hint_m}):")
            in_m = input(f"     输入您手头资料 [直接回车沿用: {def_m}]: ").strip()
            math_books = in_m or def_m
        else:
            math_books = "不考数学"

        hint_e = f"已扫描本地: {', '.join(local_eng_files)}" if local_eng_files else "本地参考资料文件夹暂未放入文件"
        print(f"  {_n_book}. 英语权威资料 ({hint_e}):")
        in_e = input(f"     输入您手头资料 [直接回车沿用: {def_e}]: ").strip()
        eng_books = in_e or def_e

        hint_p = f"已扫描本地: {', '.join(local_pol_files)}" if local_pol_files else "本地参考资料文件夹暂未放入文件"
        print(f"  {_n_book + 1}. 政治权威资料 ({hint_p}):")
        in_p = input(f"     输入您手头资料 [直接回车沿用: {def_p}]: ").strip()
        pol_books = in_p or def_p

        hint_pro = f"已扫描本地: {', '.join(local_pro_files)}" if local_pro_files else "本地参考资料文件夹暂未放入文件"
        print(f"  {_n_book + 2}. 专业课权威资料 ({hint_pro}):")
        in_pro = input(f"     输入您手头资料 [直接回车沿用: {def_pro}]: ").strip()
        pro_books = in_pro or def_pro
    else:
        math_books = plan.get("math_books") or def_m
        eng_books = plan.get("eng_books") or def_e
        pol_books = plan.get("pol_books") or def_p
        pro_books = plan.get("pro_books") or def_pro

    plan["math_books"] = math_books
    plan["eng_books"] = eng_books
    plan["pol_books"] = pol_books
    plan["pro_books"] = pro_books
    print(colorize("  [√] 资料白名单严格核验完毕：杜绝虚构，真实锁定题源！\n", C.GREEN))

    # ── 维度 5: 每日时间预算与各科切分 ──
    print(colorize("【维度 5/7 · ⏳ 每日可用复习时间与各科预算】", C.BOLD))
    if interactive:
        print("  请选择您日常的备考作息模式：")
        for k, p in ROUTINE_PRESETS.items():
            # [P16 修复·不考数学文案] 文科考生不应在作息菜单里看到「数3.0h」。
            _math_seg = f"数{p['math_hours']}h/" if m_choice != "none" else ""
            print(f"    [{k}] {p['name']} (日均 {p['total_hours']}h: {_math_seg}英{p['eng_hours']}h/政{p['pol_hours']}h/专{p['pro_hours']}h)")
        r_choice = input("  选择作息模式 (1/2/3) [默认 1]: ").strip() or "1"
        preset = ROUTINE_PRESETS.get(r_choice, ROUTINE_PRESETS["1"])

        tot_h_input = input(f"  1. 每日可用总时长(小时) [默认: {preset['total_hours']}]: ").strip()
        total_hours = float(tot_h_input) if tot_h_input else preset['total_hours']

        # [P16 修复·向导编号跳号] 不考数学时跳过数学问项，英语/政治/专业课编号前移
        # （此前固定为 3/4/5，缺 2 造成跳号）。
        _n_hour = 2
        if m_choice != "none":
            m_h_input = input(f"  2. 数学每日分配小时 [默认: {preset['math_hours']}]: ").strip()
            math_hours = float(m_h_input) if m_h_input else preset['math_hours']
            _n_hour = 3
        else:
            math_hours = 0.0

        e_h_input = input(f"  {_n_hour}. 英语每日分配小时 [默认: {preset['eng_hours']}]: ").strip()
        eng_hours = float(e_h_input) if e_h_input else preset['eng_hours']

        p_h_input = input(f"  {_n_hour + 1}. 政治每日分配小时 [默认: {preset['pol_hours']}]: ").strip()
        pol_hours = float(p_h_input) if p_h_input else preset['pol_hours']

        pro_h_input = input(f"  {_n_hour + 2}. 专业课每日分配小时 [默认: {preset['pro_hours']}]: ").strip()
        pro_hours = float(pro_h_input) if pro_h_input else preset['pro_hours']
        
        default_rest_w = preset["rest_weekly"]
        default_rest_m = preset["rest_monthly"]
    else:
        total_hours = float(plan.get("total_hours", 8.5))
        math_hours = float(plan.get("math_hours", 3.0))
        eng_hours = float(plan.get("eng_hours", 2.0))
        pol_hours = float(plan.get("pol_hours", 1.0))
        pro_hours = float(plan.get("pro_hours", 2.5))
        default_rest_w = plan.get("rest_weekly", "每周日晚 18:00~22:30 放松休整")
        default_rest_m = plan.get("rest_monthly", "每月最后一个周日全真闭卷模考与全科雷达复盘")

    # [P9 修复·不考数学 total_hours 未重算] ROUTINE_PRESETS 三档的 total_hours 都
    # 含数学（如全脱产 8.5h = 数3.0 + 英2.0 + 政1.0 + 专2.5）。不考数学时 math_hours
    # 被置 0，若仍沿用含数学的预设默认总时长，就会出现「共 8.5 小时」但分科预算只有
    # 5.5 小时的矛盾，并被写入 AGENTS.md 与看板。取舍：仅当 total_hours 仍等于某一档
    # 含数学的预设默认值时，才重算为分科之和；用户显式输入的其它总时长一律保留，
    # 以免覆盖考生真实的时间预算。
    _math_disabled = (
        plan.get("exam_mode") in ("mode_b", "no_math_dual_pro", "mode_c", "mgmt_199")
        or bool(plan.get("pol_disabled"))
        or bool(str(plan.get("pro2_name") or "").strip())
        or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"}
        or plan.get("math_name") == "不考数学"
    )
    if _math_disabled:
        # 非交互分支此前沿用 plan 里的 math_hours（可能仍是 3.0），与交互分支
        # 不考数学时置 0 的口径不一致，此处统一归零。
        math_hours = 0.0
    _subj_sum = round(eng_hours + pol_hours + pro_hours, 2)
    _preset_totals = {round(p["total_hours"], 2) for p in ROUTINE_PRESETS.values()}
    # [R2-D8 补充·重算不得篡改既有配置] ``_plan_from_existing`` 为真表示这次
    # 运行是「非交互重跑、基线取自工作区既有 ky_config.json」。此时 total_hours
    # 是考生自己存下来的值（可能是「在职 6.5h 档 + 自定分科」这类合法组合），
    # 不是预设默认残留，重算会把 6.5 悄悄改成 5.0 —— 于是「跑一次测试，每日
    # 预算就变一次」，破坏幂等。仅当基线来自预设/显式 preset 时才重算。
    if (not _plan_from_existing) and _math_disabled and round(total_hours, 2) in _preset_totals and abs(total_hours - _subj_sum) > 1e-9:
        _stale_total = total_hours
        total_hours = _subj_sum
        print(colorize(
            f"  [i] 不考数学：总时长已由含数学的预设 {_stale_total:g}h 重算为分科之和 {total_hours:g}h。",
            C.YELLOW))

    plan["total_hours"] = total_hours
    plan["math_hours"] = math_hours
    plan["eng_hours"] = eng_hours
    plan["pol_hours"] = pol_hours
    plan["pro_hours"] = pro_hours
    print(colorize(f"  [√] 每日精力预算锁定: 共 {total_hours} 小时 (数{math_hours}h + 英{eng_hours}h + 政{pol_hours}h + 专{pro_hours}h)\n", C.GREEN))

    # ── 维度 6: 每周/每月休息与调节机制 ──
    print(colorize("【维度 6/7 · 🌿 每周/每月休息与心态调节机制】", C.BOLD))
    if interactive:
        rest_weekly = input(f"  1. 每周放风与休整窗口 [默认: {default_rest_w}]: ").strip() or default_rest_w
        rest_monthly = input(f"  2. 每月模考与复盘节点 [默认: {default_rest_m}]: ").strip() or default_rest_m
    else:
        rest_weekly = default_rest_w
        rest_monthly = default_rest_m

    plan["rest_weekly"] = rest_weekly
    plan["rest_monthly"] = rest_monthly
    print(colorize(f"  [√] 作息周期锁定: 周度休整 + 月度模考复盘已设立！\n", C.GREEN))

    # ── 维度 7: 辅导风格与提分目标矩阵 ──
    print(colorize("【维度 7/7 · 🎯 私教辅导风格与提分目标矩阵】", C.BOLD))
    if interactive:
        print("  --- 请选择您希望 AI 私教采取的辅导风格 ---")
        for k, (s_name, s_desc) in STYLES.items():
            print(f"    [{k}] {s_name}\n        -> {s_desc}")
        style_choice = input("  选择辅导风格 (1/2/3/4) [默认 1]: ").strip() or "1"
        style_name = STYLES.get(style_choice, STYLES["1"])[0]

        # [P16 修复·向导编号跳号] 维度 7 同样存在不考数学时的编号跳号（英语仍显示
        # "2."），此处与维度 3/4/5 统一改为动态连续编号。
        _n_tgt = 1 if m_choice == "none" else 2
        if m_choice != "none":
            math_target = input("  1. 数学目标成绩 [默认: 110+ 分]: ").strip() or "110+ 分"
        else:
            math_target = "不考数学"
        eng_target = input(f"  {_n_tgt}. 英语目标成绩 [默认: 65+ 分]: ").strip() or "65+ 分"
        pol_target = input(f"  {_n_tgt + 1}. 政治目标成绩 [默认: 70+ 分]: ").strip() or "70+ 分"
        pro_target = input(f"  {_n_tgt + 2}. 专业课目标成绩 [默认: 120-130 分]: ").strip() or "120-130 分"
        total_target = input(f"  {_n_tgt + 3}. 初试总分目标 [默认: 370+ 分]: ").strip() or "370+ 分"
    else:
        style_name = plan.get("style_name", STYLES["1"][0])
        math_target = plan.get("math_target", "110+ 分")
        eng_target = plan.get("eng_target", "65+ 分")
        pol_target = plan.get("pol_target", "70+ 分")
        pro_target = plan.get("pro_target", "120-130 分")
        total_target = plan.get("total_target", "370+ 分")

    plan["style_name"] = style_name
    plan["math_target"] = math_target
    plan["eng_target"] = eng_target
    plan["pol_target"] = pol_target
    plan["pro_target"] = pro_target
    plan["total_target"] = total_target
    print(colorize(f"  [√] 总战役目标: 总分 {total_target} · 激活风格: {style_name}\n", C.GREEN))

    # ── 执行固化写入 ──
    print(colorize("正在将个人定制化必考方案全量固化至系统记忆与考纲大盘...", C.CYAN))
    apply_study_plan(plan, interactive=interactive)
    print_study_plan_summary(plan)
    return plan

def generate_expert_diagnostic_strategy(plan):
    """
    内置考研私教专家诊断引擎：
    根据学员 7 大维度真实学情与核心痛点防线，生成深度、务实、直击痛点的备考战略指南
    """
    m_name = plan.get("math_name", "数学")
    e_name = plan.get("eng_name", "英语")
    pro_name = plan.get("pro_name", "专业课")

    m_w = plan.get("math_weakness", "计算失误")
    e_w = plan.get("eng_weakness", "长难句拆解")
    p_w = plan.get("pol_weakness", "马原多选题")
    pro_w = plan.get("pro_weakness", "核心考点掌握")

    # [文科适配·数学战术串味] 不考数学考生此前会收到一整屏数学战术
    # （"【不考数学】专属痛点攻坚战术（针对：无）…求导验算、折半检验法"）。
    # 现跳过数学节，英/政/专序号动态前移。
    math_disabled = (str(plan.get("math_key", "")).lower()
                     in {"none", "no", "不考数学"}
                     or plan.get("math_name") == "不考数学")
    if math_disabled:
        # 数学节替换为一句话说明，英/政/专序号前移为 1/2/3
        _n_math, _n_eng, _n_pol, _n_pro = 0, 1, 2, 3
        math_section = ("### 特别说明：本方案【不考数学】（math_key=none），"
                        "不安排任何数学复习战术，请将精力集中于英语、政治与专业课。\n\n")
    else:
        _n_math, _n_eng, _n_pol, _n_pro = 1, 2, 3, 4
        math_section = f"""### {_n_math}. 【{m_name}】专属痛点攻坚战术（针对：{m_w}）
- **核心病因诊断**：该失分点通常源于基本定理适用条件理解不透彻或草稿推导未分步验证，导致推导卡壳或非智力因素丢分。
- **阶段攻坚处方**：
  1. 梳理核心公式与定理成立边界，强化几何直观与物理背景理解；
  2. 解答题推行「采分点分步书写规范」，关键推导必须标明定理依据，杜绝跳步；
  3. 草稿纸建立「折半检验法」，关键求导与化简实时反向求导验算，将计算失误率压缩至 0。

"""

    strategy_md = f"""{math_section}### {_n_eng}. 【{e_name}】专属痛点攻坚战术（针对：{e_w}）
- **核心病因诊断**：长难句阅读与阅读理解失分主因是语法骨架不清晰、被修饰成分干扰主干抓取，以及受命题人三大典型干扰陷阱（偷换概念、因果倒置、主观过度推断）影响。
- **阶段攻坚处方**：
  1. 执行「四步搭积木拆解法」：先找核心谓语动词，再划从句引导词，括号括起非谓语与介词短语修饰成分，直击主谓宾；
  2. 精析历年真题阅读原文对应句，建立选项「同义替换」与「反向排除」双重证据链，做到选有据、排有理。

### {_n_pol}. 【思想政治理论】专属痛点攻坚战术（针对：{p_w}）
- **核心病因诊断**：多选题得分率低往往由于马原哲学概念边界模糊、混淆不同帽子词（根本原因/根本保证/直接原因/首要前提）与时政核心提法。
- **阶段攻坚处方**：
  1. 梳理唯物辩证法与认识论核心框架图，彻底吃透矛盾同一性与斗争性、真理与价值对立统一；
  2. 刷题严格执行「排谬法」（先剔除本身有知识性错误的选项）与「排异法」（剔除正确但与题干无关的选项），多选题目标稳拿 32~36 分。
"""

    # 针对不同专业课学科类型动态适配诊断与处方
    # [批4·关键词收紧] 原「计算」子串过宽（「计算数学」「计算物理」等专业名会
    # 误命中计算机分支，被灌 C/C++ 代码书写建议），收紧为「计算机」。
    p_lower = pro_name.lower()
    if any(k in p_lower for k in ("408", "计算机", "软件")):
        pro_diag = "专业课核心算法与大题推导失分关键在于算法逻辑书写不规范、缺少必要注释与复杂度分析，导致采分点严重流失。"
        pro_sol = """  1. 严格按照研究生阅卷标准训练三段式解答：① 自然语言设计思想（2~3行说明核心逻辑与数据结构）；② 规范 C/C++ 或核心代码书写（带清晰变量注释）；③ 时间复杂度与空间复杂度推导与结论；
  2. 紧扣官方考纲要求，吃透真题核心高频考点，规范专业术语表述，拒绝口语化答题。"""
    elif any(k in p_lower for k in ("信号", "通信", "电子", "电路", "811", "控制")):
        pro_diag = "专业课核心系统时域/频域/复频域变换（傅里叶变换、拉普拉斯变换、Z变换）与微分/差分方程解算时，概念理解不透或代数推导跳步，导致采分点流失。"
        pro_sol = """  1. 严格遵循自命题大纲核心推导与阅卷采分规范：① 规范写出对应变换公式或微分/差分系统方程；② 详细展开代数化简与收敛域/收敛边界条件判定；③ 最终给出清晰指标或系统响应函数并复核因果稳定性；
  2. 紧扣高校自命题真题风格，吃透高频大题模型，规范专业术语与推导步骤，杜绝计算失误。"""
    elif any(k in p_lower for k in ("法硕", "管理", "经济", "教育", "心理", "中文", "新闻", "历史",
                                        "哲学", "马克思", "政治", "思政", "法学", "文学", "艺术")):
        pro_diag = "专业课论述大题缺乏学科经典理论框架支撑，分析流于表面，缺少学术前沿观点与踩分关键词。"
        pro_sol = """  1. 严格训练高分论述题框架三步法：① 核心概念精准界定；② 多维度理论模型与现实案例结合展开；③ 归纳总结并引申学科前沿发展；
  2. 紧扣目标院校自命题历年真题脉络，熟记专业术语，拒绝空泛口语表达。"""
    else:
        pro_diag = "专业课核心原理理解不够深刻，大题推导过程缺少必要的前置定理依据与边界条件分析，步骤跳步导致扣分。"
        pro_sol = """  1. 严格按照研究生初试阅卷标准训练规范化解答：① 明确列出核心定义公式与物理/数学模型依据；② 规范推导计算步骤并标注中间关键量；③ 给出清晰最终结论并检查单位与量纲；
  2. 紧扣官方大纲与历年自命题真题，吃透高频核心题型，建立章节知识框架图。"""

    strategy_md += f"""

### {_n_pro}. 【{pro_name}】专属痛点攻坚战术（针对：{pro_w}）
- **核心病因诊断**：{pro_diag}
- **阶段攻坚处方**：
{pro_sol}"""
    return strategy_md.strip()

def run_ai_study_plan_generation(plan, interactive=True):
    """
    真正调动考研私教 AI 总教练 / 深度规划引擎生成个人定制化备考方案与破局战术
    """
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    api_key = str(cfg.get("api_key") or "").strip()

    # [文科适配·AI 提示词数学串味] 不考数学时，提示词里的数学预算/白名单/
    # 痛点行全部替换为不考数学声明，并追加硬约束（否则 LLM 照样输出数学战术）。
    _math_off = (str(plan.get("math_key", "")).lower()
                 in {"none", "no", "不考数学"}
                 or plan.get("math_name") == "不考数学")
    if _math_off:
        _math_budget = "数学: 不考数学（0h）"
        _math_books_line = "  * 数学: 不考数学"
        _math_gap_line = "  * 数学：不考数学"
        _math_guide = "（考生不考数学，严禁输出任何数学复习战术与数学时间分配）"
        _step1_text = "正在跳过【数学】（本方案不考数学），聚焦英语/政治/专业课..."
    else:
        _math_budget = f"数学: {plan.get('math_hours')}h"
        _math_books_line = f"  * 数学: {plan.get('math_books')}"
        _math_gap_line = f"  * 数学：摸底 {plan.get('math_baseline')}，核心痛点【{plan.get('math_weakness')}】"
        _math_guide = ""
        _step1_text = f"正在对【数学】薄弱点「{plan.get('math_weakness')}」进行命题归因与提分战术设计..."

    if interactive:
        print(colorize("""
╭────────────────────────────────────────────────────────────────────────╮
│  🤖 考研私教 AI 总教练正在为您生成【个人专属全科备考方案】...        │
│  (正在深入分析您的考纲、摸底分、痛点防线、每日时间预算与作息)          │
╰────────────────────────────────────────────────────────────────────────╯
""", C.CYAN))
        import time
        print(colorize(f"  [1/3] {_step1_text}", C.CYAN))
        time.sleep(0.2)
        print(colorize(f"  [2/3] 正在对【英语/政治/专业课】核心失分盲区进行靶向突破与时间切分...", C.CYAN))
        time.sleep(0.2)
        print(colorize(f"  [3/3] 正在基于白名单真实题源与官方考纲构建首日（Day 1）{subject_count_label(plan)}针对性任务清单...\n", C.CYAN))
        time.sleep(0.1)

    ai_generated_text = None

    if api_key and interactive:
        prompt_text = f"""你是一位深谙中国研究生入学考试命题规律、提分导向的考研全科专属总教练。
学员刚刚完成了 7 大维度个性化学情诊断：
- 目标院校与专业：{plan.get('school')} · {plan.get('major')}
- 初试日期：{plan.get('exam_date')} (倒计时 {plan.get('days_left')} 天)
- 当前阶段：{plan.get('stage_name')}
- 辅导风格：{plan.get('style_name')}
- 每日时间预算：{plan.get('total_hours')} 小时 ({_math_budget} / 英语: {plan.get('eng_hours')}h / 政治: {plan.get('pol_hours')}h / 专业课: {plan.get('pro_hours')}h)
- 科学作息：每周 {plan.get('rest_weekly')}；每月 {plan.get('rest_monthly')}
- 真实备考资料白名单（严禁虚构）：
{_math_books_line}
  * 英语: {plan.get('eng_books')}
  * 政治: {plan.get('pol_books')}
  * 专业课: {plan.get('pro_books')}
- 核心学情与薄弱痛点：
{_math_gap_line}
  * 英语：摸底 {plan.get('eng_baseline')}，核心痛点【{plan.get('eng_weakness')}】
  * 政治：摸底 {plan.get('pol_baseline')}，核心痛点【{plan.get('pol_weakness')}】
  * 专业课：摸底 {plan.get('pro_baseline')}，核心痛点【{plan.get('pro_weakness')}】

请为该学员输出严谨务实、直击痛点的《个人专属考研备考方案与首日突破任务单》{_math_guide}：
1. 【全科提分战略定位与痛点攻坚指南】：针对{('数学【' + str(plan.get('math_weakness')) + '】、') if not _math_off else ''}英语【{plan.get('eng_weakness')}】、政治【{plan.get('pol_weakness')}】、专业课【{plan.get('pro_weakness')}】逐一给出实操破局战术与步骤规范；
2. 【各科阶段推进里程碑与每日时间切分指导】：结合倒计时 {plan.get('days_left')} 天与当前阶段，明确各科在当前阶段的核心突破重点；
3. 【今日（Day 1）{subject_count_label(plan)}针对性任务清单】：精确到具体攻坚知识点、练习题型、预计分钟数与自测动作。
要求：逻辑严密、直击痛点，严禁空泛套话！"""

        try:
            # [K4] 内联流式收敛进统一出口（原为自带 urllib + SSE 解析的实现）。
            try:
                from llm_client import ChatRequest, request_chat
            except ImportError:  # pragma: no cover
                from tools.llm_client import ChatRequest, request_chat  # type: ignore
            raw_base_url = cfg.get("base_url", "https://api.deepseek.com/v1")
            model = cfg.get("model", "deepseek-chat")
            messages = [
                {"role": "system", "content": "你是一位深谙中国研究生入学考试命题规律、提分导向的考研全科专属总教练。请根据学员给出的 7 大维度个性化学情，输出严密、求真务实、直击薄弱项痛点的深度备考战略与任务清单。严禁虚构书籍或空泛套话。"},
                {"role": "user", "content": prompt_text}
            ]
            collected: List[str] = []

            def _on_chunk(token: str) -> None:
                sys.stdout.write(token)
                sys.stdout.flush()
                collected.append(token)

            req = ChatRequest(
                messages=messages,
                model=model,
                temperature=0.4,
                stream=True,
                timeout=60.0,
                api_key=api_key,
                base_url=raw_base_url,
                headers_extra={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Kaoyan-Study-Chain/1.0",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            )
            # [审计 2026-09-30 P1-7] 出站收敛：经 safe_urlopen 发送（原为裸 urlopen）。
            request_chat(req, max_retries=0, on_chunk=_on_chunk,
                         urlopen_fn=safe_urlopen)
            print("\n")
            ai_generated_text = "".join(collected).strip()
        except Exception:
            ai_generated_text = None

    if not ai_generated_text:
        ai_generated_text = generate_expert_diagnostic_strategy(plan)
        if interactive:
            print(colorize("【AI 私教专属全科痛点攻坚战术】", C.BOLD))
            print(ai_generated_text)
            print()

    return ai_generated_text

def _default_active_subject_for_plan(plan):
    """不考数学时给出应默认激活的科目；考数学则返回 None（不改动）。

    [P4 修复·不考数学默认激活数学私教] ``apply_study_plan`` 此前不写
    ``active_subject``，全仓 10+ 处 ``cfg.get("active_subject", "math")`` 都会回退
    成 math，导致文科（不考数学）考生一启动就进入「数学专属私教 · 不考数学 冲刺」，
    还被提示 ``/calc 验算数学``。此处按分科预算取第一个有安排的科目（通常为英语）。
    只改写入端，不动那 10+ 处读取点的默认值。
    """
    math_off = (str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"}
                or plan.get("math_name") == "不考数学")
    if not math_off:
        return None
    for key in ("eng", "pol", "pro"):
        try:
            if float(plan.get(f"{key}_hours", 0) or 0) > 0:
                return key
        except (TypeError, ValueError):
            continue
    return "eng"


def apply_study_plan(plan, interactive=True):
    """将定制化方案持久化到 AGENTS.md、各科文件与配置文件中"""
    agents_path = ROOT / "AGENTS.md"
    if not agents_path.exists():
        return

    # 1. 更新官方大纲
    try:
        # [缺陷修复·导入回退] 与文件上方第 222 行的写法保持一致。
        # 以脚本方式运行且仓库根不在 sys.path 时，`tools` 会被 site-packages 下
        # 同名第三方包劫持，此处若无回退会直接 ImportError，
        # 导致 apply_syllabus_selection 从未执行、考纲文件不被写入。
        try:
            from tools import syllabus_manager
        except ImportError:
            import syllabus_manager
        syllabus_manager.apply_syllabus_selection(
            math_key=plan.get("math_key", "math2"),
            eng_key=plan.get("eng_key", "eng2"),
            pro_type=plan.get("pro_type", "408"),
            pro_name=plan.get("pro_name", "408 计算机学科专业基础"),
            # [G3 修复·丢 pro2_name] 缺此参数则 Mode-B 第二大纲永不生成
            #（GUI settings 传了，CLI/向导路径漏了）。
            pro2_name=plan.get("pro2_name", ""),
            school=plan.get("school", "目标院校"),
            major=plan.get("major", "报考专业"),
            auto_write=True
        )
    except Exception as e:
        print(colorize(f"  [!] 大纲自动更新提示: {e}", C.YELLOW))

    # 2. 真正调动考研私教 AI 总教练 / 深度规划引擎生成定制战役战略
    ai_strategy = run_ai_study_plan_generation(plan, interactive=interactive)

    # 3. 组装根目录 AGENTS.md 第一节
    m_name = plan.get("math_name", "数学")
    e_name = plan.get("eng_name", "英语")
    pro_name = plan.get("pro_name", "专业课")

    content = agents_path.read_text(encoding="utf-8")
    content = re.sub(r"- \*\*目标院校\*\*：.*", f"- **目标院校**：`{plan.get('school', '目标院校')}`", content)
    content = re.sub(r"- \*\*报考专业\*\*：.*", f"- **报考专业**：`{plan.get('major', '报考专业')}`", content)
    _exam_date_str = plan.get("exam_date") or _FALLBACK_EXAM_DATE
    # [G2 修复] 倒计时必须按 exam_date **实时重算**，不能取 plan 里存的 days_left：
    # 存值是上一轮写下的快照，一旦 exam_date 被改过（或配置被占位值冲过），
    # AGENTS.md 就会出现「初试日期：X（倒计时约 N 天）」而 N 与 X 对不上的自相矛盾。
    # [UT4 修复·PLANNER-3] 重算口径收口到 _days_left_now（钳非负 + 快照回退），
    # 与 ensure_subject_today_task 的今日任务刷新共用同一实现。
    _days_left = _days_left_now(plan)
    content = re.sub(r"- \*\*初试日期\*\*：.*",
                     f"- **初试日期**：`{_exam_date_str}` (倒计时约 {_days_left} 天)",
                     content)
    
    # 注入备考阶段
    if "- **当前备考阶段**：" in content:
        content = re.sub(r"- \*\*当前备考阶段\*\*：.*", f"- **当前备考阶段**：`{plan.get('stage_name', STAGES['2'][0])}`", content)
    else:
        content = re.sub(r"(- \*\*初试日期\*\*：.*?\n)", r"\1" + f"- **当前备考阶段**：`{plan.get('stage_name', STAGES['2'][0])}`\n", content)

    content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*", f"- **当前激活辅导风格**：`{plan.get('style_name', STYLES['1'][0])}`", content)

    # 矩阵表格更新
    # [问题9 修复·不考数学行仍写数学策略] 该行此前硬编码数学战术文案，无
    # math_disabled 分支 —— 不考数学考生的根 AGENTS.md 里照样出现「科目一：
    # 不考数学 | 0.0 小时 | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化」。
    # 现按 init_workspace.py 的 P4 先例改写实述（不安排数学学习任务）。
    _math_off_row = (str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"}
                     or plan.get("math_name") == "不考数学")
    if _math_off_row:
        m_row = (f"| **科目一：{m_name}** | 不考数学 | **不考数学** | "
                 f"{plan.get('math_hours', 0.0)} 小时 | 本方案不考数学，不安排数学学习任务 |")
    else:
        m_row = f"| **科目一：{m_name}** | {plan.get('math_baseline','[摸底]')} | **{plan.get('math_target','110+ 分')}** | {plan.get('math_hours',2.5)} 小时 | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |"
    e_row = f"| **科目二：{e_name}** | {plan.get('eng_baseline','[摸底]')} | **{plan.get('eng_target','65+ 分')}** | {plan.get('eng_hours',2.0)} 小时 | 搭积木拆解长难句，定位阅读选项逻辑，固化作文功能句模板 |"
    p_row = f"| **科目三：思想政治理论** | {plan.get('pol_baseline','[摸底]')} | **{plan.get('pol_target','70+ 分')}** | {plan.get('pol_hours',1.0)} 小时 | 单选+多选得分盘（38~42分），帽子词秒杀，后期背诵闭环 |"
    pro_row = f"| **科目四：{pro_name}** | {plan.get('pro_baseline','[摸底]')} | **{plan.get('pro_target','120-130 分')}** | {plan.get('pro_hours',2.5)} 小时 | 权威教材体系+历年真题深度解剖，白名单题源抽题门禁 |"
    # [D8 修复] 合计行摸底总分此前是未替换占位符 [摸底总分]；现按各科摸底文本
    # 汇总（不考数学时不计数学行）。
    _bt_keys = ["eng_baseline", "pol_baseline", "pro_baseline"]
    if not _math_off_row:
        _bt_keys.insert(0, "math_baseline")
    tot_row = f"| **合计** | {baseline_total_label(plan, _bt_keys)} | **{plan.get('total_target','370+ 分')}** | {plan.get('total_hours',8.5)} 小时 | **结构性提分，稳拿基本盘，拒绝偏难怪题** |"

    content = re.sub(r"\|\s*\*\*科目一.*", m_row, content)
    content = re.sub(r"\|\s*\*\*科目二.*", e_row, content)
    content = re.sub(r"\|\s*\*\*科目三.*", p_row, content)
    content = re.sub(r"\|\s*\*\*科目四.*", pro_row, content)
    content = re.sub(r"\|\s*\*\*合计.*", tot_row, content)

    # 注入白名单教辅书目与作息机制 (杜绝虚构书目)
    schedule_section = f"""
### 【个性化学情与作息调节机制】 (系统已锁定)
- **每日时间预算**: 每日投入 `{plan.get('total_hours', 8.5)} 小时` (数学: {plan.get('math_hours', 3.0)}h / 英语: {plan.get('eng_hours', 2.0)}h / 政治: {plan.get('pol_hours', 1.0)}h / 专业课: {plan.get('pro_hours', 2.5)}h)
- **每周休整窗口**: `{plan.get('rest_weekly', '每周日晚 18:00~22:30 放松休整')}`
- **每月模考复盘**: `{plan.get('rest_monthly', '每月最后一个周日全天闭卷模考与全科雷达复盘')}`
- **手头资料白名单 (AI 严守范围)**:
  - 数学: `{plan.get('math_books')}`
  - 英语: `{plan.get('eng_books')}`
  - 政治: `{plan.get('pol_books')}`
  - 专业课: `{plan.get('pro_books')}`
- **核心薄弱诊断与攻坚防线**:
  - 数学薄弱点: `{plan.get('math_weakness', '导数中值定理、计算失误')}`
  - 英语薄弱点: `{plan.get('eng_weakness', '待诊断薄弱点、细节定位')}`
  - 政治薄弱点: `{plan.get('pol_weakness', '马原唯物辩证法、多选题漏选')}`
  - 专业课薄弱点: `{plan.get('pro_weakness', '核心考点掌握与解答步骤规范')}`
"""
    if "### 【个性化学情与作息调节机制】" in content:
        content = re.sub(r"### 【个性化学情与作息调节机制】.*?(?=\n## 1\.|\n### 二、)", schedule_section.strip() + "\n\n", content, flags=re.DOTALL)
    else:
        content = content.replace("### 二、四种私教辅导风格设定", schedule_section + "\n### 二、四种私教辅导风格设定")

    atomic_write_text(agents_path, content)

    # 4. 同步更新各子目录 AGENTS.md
    update_subject_agents(plan)

    # 5. 更新 ky_config.json
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["onboarding_completed"] = True
    cfg["study_plan"] = plan
    # [P0 修复·风格单一真源] coaching_style 与 study_plan.style_name 必须同步写入。
    # 此前向导只写 style_name，而 TUI/CLI 读顶层 coaching_style，三端显示互相矛盾。
    cfg["coaching_style"] = plan.get("style_name", cfg.get("coaching_style", ""))
    # [P4 修复·不考数学默认激活数学私教] 仅在当前未指定 active_subject（或仍停留在
    # math）时兜底为第一个有安排的科目，避免覆盖考生此前手动选定的科目。
    _cur_active = str(cfg.get("active_subject") or "").strip().lower()
    if _cur_active in ("", "math"):
        _fallback_subject = _default_active_subject_for_plan(plan)
        if _fallback_subject:
            cfg["active_subject"] = _fallback_subject
    cfg["relief_mode_active"] = False
    atomic_write_text(CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))

    # 5.1 重置院校监控列表：新身份生成时，监控列表应随目标院校切换，避免残留上一考生数据
    try:
        from tools.intelligence.watcher import AdmissionWatcher
        # [问题3 补修] 显式传 ROOT（与 CONFIG_FILE 同源，支持 KY_WORKSPACE_ROOT
        # 隔离）：无参实例化只认模块级真实 ROOT，测试隔离场景下监控条目会外漏。
        watcher = AdmissionWatcher(workspace_root=ROOT)
        school = plan.get("school", "").strip()
        if school:
            for item in watcher.list_watched():
                code = item.get("chsi_code")
                if code:
                    watcher.remove_watch(code)
            watcher.add_watch(school)
    except Exception as e:
        print(colorize(f"  [!] 监控列表重置提示: {e}", C.YELLOW))

    # 6. 全自动生成各科定制化总规划与今日真实任务清单 (注入定制 AI 攻坚战略)
    generate_plan_and_today_files(plan, ai_strategy=ai_strategy)

    # 6. 自动重新编译自测看板
    # [问题7 根因修复] 统一走 dashboard_build：frozen（exe 版）下旧实现
    # subprocess.run([sys.executable, ...]) 的 sys.executable 是 GUI 主程序，
    # 等于再弹一个主界面。frozen 改进程内执行（见 tools/dashboard_build.py）。
    try:
        try:
            from dashboard_build import run_dashboard_build
        except ImportError:
            from tools.dashboard_build import run_dashboard_build
        # [W13 收口·本地入口分模式] 显式完整模式（与 更新看板.bat / ky build 一致）
        run_dashboard_build(workspace_root=ROOT, snapshot_opt_in=False,
                            capture_output=True)
    except Exception:
        pass

def _safe_write_today_task(task_file: Path, today_str: str, content: str) -> str:
    """
    写今日任务文件，避免覆盖用户已勾选/已编辑的当日任务：
      - 文件不存在 → 写入
      - 文件已存在且首行日期与 today_str 匹配 → 备份为 今日任务_<日期>_backup_<时间>.md 后覆盖
      - 文件已存在但日期不符（昨日/更早任务遗留）→ 直接覆盖为今日
    返回状态描述，便于日志显示。
    """
    import shutil
    if not task_file.exists():
        task_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(task_file, content)
        return "已创建"
    try:
        existing_first = task_file.read_text(encoding="utf-8").splitlines()[0]
    except Exception:
        existing_first = ""
    if f"({today_str})" in existing_first:
        # 同日 → 备份旧版再写新版（保护完成勾选等个性化）
        ts = datetime.now().strftime("%H%M%S")
        backup = task_file.with_name(task_file.stem + f"_backup_{today_str}_{ts}.md")
        try:
            shutil.copy2(task_file, backup)
        except Exception:
            pass
        atomic_write_text(task_file, content)
        return f"已备份到 {backup.name} 后覆盖"
    # 不同日 → 旧文件已被搁置，直接覆盖
    atomic_write_text(task_file, content)
    return "已覆盖（非同日任务）"


#: 报到场景：科目 key → (目录, 任务行比例, 显示名兜底)
_CHECKIN_SUBJECT_SPECS = {
    "math": ("01-数学", (0.20, 0.55, 0.25), "数学"),
    "eng": ("02-英语", (0.25, 0.35, 0.40), "英语"),
    "pol": ("03-思想政治理论", (0.40, 0.40, 0.20), "思想政治理论"),
    "pro": ("04-专业课", (0.30, 0.50, 0.20), "专业课"),
}


def _due_review_task_row(subject_key: str) -> str:
    """[P1 修复·2026-10-08 K6 模板僵化] 生成「到期复测」任务行（无到期错题时返回空串）。

    数据源 = ``error_logger.get_due_reviews``（到期筛选单一真源：到期日由
    ``fsrs_scheduler.compute_next_interval`` 推导）。修复前今日任务模板
    **零引用 FSRS**：到期待复测错题从不进入当日清单，复测被无限推迟。
    查询异常（错题本缺失/解析失败/导入失败）一律按「无到期」处理 ——
    今日任务生成绝不能因复测查询而中断。行格式为标准 4 列表格行，
    与 task_parser.parse_task_lines 解析契约兼容。
    """
    try:
        try:
            from skills import error_logger
        except ImportError:  # pragma: no cover - 包式导入上下文
            from tools.skills import error_logger
        due_n = len(error_logger.get_due_reviews(subject_key, max_count=99))
    except Exception:
        due_n = 0
    if due_n <= 0:
        return ""
    due_min = min(30, max(10, due_n * 5))
    return (f"| 到期复测 | FSRS 到期错题 {due_n} 道（输入 /review 开始盲盒复测） "
            f"| {due_min} 分钟 | [ ] |\n")


def _pacing_hint_line(days_left: int, minutes: int) -> str:
    """[P1 修复·2026-10-08 K6 模板僵化] 配速提示行：剩余天数 / 日均量 / 剩余可投入总量。

    修复前模板只有固定三行任务与倒计时快照，没有任何配速信息；考生无法
    从今日任务感知「还剩多少天、每天要投多少、总共还能投多少」。
    """
    hours_per_day = minutes / 60.0
    total_hours = max(0, int(days_left)) * hours_per_day
    return (f"> 📊 配速提示：距初试还剩 {max(0, int(days_left))} 天 ｜ 日均量 {minutes} 分钟"
            f"（{hours_per_day:g} 小时）｜ 按此配速剩余可投入约 {total_hours:.0f} 小时")


def _count_task_rows(text: str) -> int:
    """统计今日任务行数（单一真源 task_parser；解析不可用时回退 3 的既有常量）。"""
    try:
        try:
            from state.task_parser import parse_task_lines
        except ImportError:  # pragma: no cover - 包式导入上下文
            from tools.state.task_parser import parse_task_lines
        return len(parse_task_lines(text))
    except Exception:  # pragma: no cover - 极端环境下不阻断报到
        return 3


def _canonical_roll_call(subject_key: str) -> str:
    """科目 key → 中文报到口令（单一真源：REPL 路由表 CHINESE_SUBJECT_MAP）。

    [P1 修复·口令不可识别] 今日任务提示此前写「{科目显示名}报到」（如「803 经济学
    综合报到」「308 护理综合报到」），而 REPL 仅对固定口令做精确匹配
    （loop.py ``raw_cmd in CHINESE_SUBJECT_MAP``）—— 考生照做会落入 LLM 自由问答
    路径（实测 92s+ 且产生计费），而非本地秒回报到。现反查路由真源表，保证提示中
    的口令必定可被识别。
    """
    _fallback = {"math": "数学报到", "eng": "英语报到", "pol": "政治报到", "pro": "专业课报到"}
    try:
        try:
            from cli.repl.router import CHINESE_SUBJECT_MAP
        except ImportError:  # pragma: no cover
            from tools.cli.repl.router import CHINESE_SUBJECT_MAP  # type: ignore
    except ImportError:  # pragma: no cover
        return _fallback.get(subject_key, "专业课报到")
    for _cmd, _key in CHINESE_SUBJECT_MAP.items():
        if _key == subject_key and _cmd.endswith("报到"):
            return _cmd
    return _fallback.get(subject_key, "专业课报到")


def ensure_subject_today_task(plan, subject_key, workspace_root=None):
    """报到场景：确保该科目「今日任务」文件存在且为当日版本。

    [缺陷修复·报到后今日任务 0/0] 全仓唯一生成今日任务的入口是建档向导
    （``generate_plan_and_today_files``）；报到/日常使用不会生成 —— 考生报到后
    任务面板永远 0/0、本地也没有任何今日任务文件。本函数补齐该环节。

    与建档向导生成的区别：
      - 只处理报到的那一个科目，不重写总规划与其他科目文件；
      - 文件已存在且为当日 → 原样保留（考生可能已勾选/编辑，绝不覆盖）；
      - 生成走纯模板（不调用 LLM），保证报到响应速度与确定性。

    [P1 修复·2026-10-08 K6 模板僵化] 模板不再固定三行：
      - 有 FSRS 到期错题时，「到期复测」任务行置于当日**第一行**（数据源
        error_logger.get_due_reviews，间隔由 fsrs_scheduler 推导）；
      - 倒计时行后新增「配速提示」行（剩余天数 / 日均量 / 剩余可投入总量）。
      两处均保持 4 列表格与 blockquote 结构，与 task_parser 解析契约兼容。

    返回 ``{"status": created|overwritten|exists|refreshed|disabled, "path": str, "task_count": int}``。
    """
    ws = Path(workspace_root) if workspace_root else ROOT
    spec = _CHECKIN_SUBJECT_SPECS.get(subject_key)
    if not spec:
        return {"status": "disabled", "path": "", "task_count": 0}
    folder, ratios, fallback_name = spec

    plan = plan or {}
    exam_mode = plan.get("exam_mode")
    pro2_n = str(plan.get("pro2_name") or "").strip()
    is_mode_b = exam_mode in ("mode_b", "no_math_dual_pro") or bool(pro2_n)
    is_mode_c = exam_mode in ("mode_c", "mgmt_199") or plan.get("pol_disabled")
    math_disabled = (is_mode_b or is_mode_c
                     or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"}
                     or plan.get("math_name") == "不考数学")
    if subject_key == "math" and math_disabled:
        return {"status": "disabled", "path": "", "task_count": 0}
    if subject_key == "pol" and is_mode_c:
        return {"status": "disabled", "path": "", "task_count": 0}

    today_str = datetime.now().strftime("%Y-%m-%d")
    # [UT4 修复·PLANNER-3] 倒计时按当日与 exam_date 实时重算，不能取 plan 里
    # 的建档日快照：跨日报到重写时旧快照会把建档日的剩余天数原样写回
    # （UT4 西医沙箱 P1-4 实测：09-30 建档写 80，10-01 报到重写后仍 80）。
    days_left = _days_left_now(plan)
    stage_name = plan.get("stage_name", "强化题型攻坚阶段")
    subj_display = str(plan.get(f"{subject_key}_name") or fallback_name)
    try:
        hours = float(plan.get(f"{subject_key}_hours", 2.0) or 2.0)
    except (TypeError, ValueError):
        hours = 2.0
    minutes = max(15, int(hours * 60))
    weakness = str(plan.get(f"{subject_key}_weakness") or "").strip()

    # 任务描述与「建档向导」同款口径（无薄弱点时走首日摸底话术）
    _no_weak = (not weakness or "待首次自测" in weakness or "从零" in weakness
                or weakness == "无")
    if subject_key == "math":
        desc = ("梳理考纲核心高频考点与必背公式，开展首日基础摸底自测"
                if _no_weak else f"攻坚薄弱项【{weakness}】定理条件与构造技巧")
        drill = f"{_material_phrase(plan.get('math_books'), '精选')}对应专题典型真题动笔演练"
    elif subject_key == "eng":
        desc = ("精读真题高频长难句语法骨架，开展首日主干拆解摸底"
                if _no_weak else f"攻坚薄弱项【{weakness}】")
        drill = "精读 1 篇历年真题阅读并定位干扰项逻辑"
    elif subject_key == "pol":
        desc = ("梳理考纲核心考点与帽子词框架，精选高频选择题摸底"
                if _no_weak else f"梳理【{weakness}】知识框架")
        drill = f"{_material_phrase(plan.get('pol_books'), '精做')} 20 道核心选择题自测"
    else:
        _pro_is_nursing = ("护理" in subj_display) or ("308" in subj_display)
        # [问题5 修复] 大纲占位/缺失时不得写「专业课官方考纲」字样
        _pro_ready = _pro_syllabus_ready(ws)
        _pro_scope = ("专业课官方考纲核心知识体系" if _pro_ready
                      else "专业课核心知识体系")
        _pro_scope_short = "专业课考纲" if _pro_ready else "专业课主干知识"
        desc = (f"聚焦{_pro_scope}，完成首日题型规范度摸底"
                if _no_weak else f"聚焦{_pro_scope_short}与【{weakness}】推导")
        drill = (f"{_material_phrase(plan.get('pro_books'), '选取', is_pro=True, workspace_root=ws)}"
                 + ("名词解释、简答题与 1 道病例分析题，动笔完整书写" if _pro_is_nursing
                    else "经典大题 2~3 道动笔完整书写"))

    pro_label = "专业课一" if (subject_key == "pro" and is_mode_b) else "专业课"
    title_label = subj_display if subject_key != "pro" else pro_label
    # [P1 修复·口令不可识别] 提示必须是路由表能精确识别的规范口令，
    # 而非拼接科目显示名（详见 _canonical_roll_call 注释）。
    _roll_call = _canonical_roll_call(subject_key)
    r1, r2, r3 = ratios
    # [P1 修复·2026-10-08 K6 模板僵化] 到期复测列为当日**第一行** + 配速提示行。
    # 修复前模板是固定三行且零引用 FSRS：到期待复测错题不进清单、无配速信息。
    _due_row = _due_review_task_row(subject_key)
    _pacing_line = _pacing_hint_line(days_left, minutes)
    content = f"""# 今日{title_label}任务 ({today_str})

> 研考倒计时：{days_left} 天 ｜ 当前阶段：{stage_name} ｜ 今日目标用时：{minutes} 分钟
{_pacing_line}

| 模块 | 任务内容 | 预计用时 | 完成状态 |
|---|---|---|---|
{_due_row}| 核心精讲 | {desc} | {int(minutes * r1)} 分钟 | [ ] |
| 习题精练 | {drill} | {int(minutes * r2)} 分钟 | [ ] |
| 订正归档 | 在终端输入「交作业」，AI 按步骤采分并自动录入错题队列 | {int(minutes * r3)} 分钟 | [ ] |

> **私教提示**：在输入框发送「{_roll_call}」，私教即可根据今日任务派发第一道针对性试题！
"""
    task_file = ws / folder / "_状态" / "今日任务.md"
    if task_file.exists():
        try:
            old_text = task_file.read_text(encoding="utf-8")
            first_line = old_text.splitlines()[0]
        except Exception:
            old_text, first_line = "", ""
        if today_str in first_line:
            # [P2 修复·「待导入」陈旧文案] 当日文件整体保留（防覆盖勾选/编辑），
            # 但「专业课大纲与真题待导入」是建档当日状态快照：当天 mount 入库/
            # 替换大纲后该文案即失真（三沙箱实测：报到最后仍显示待导入，看板
            # 同步失真）。此处仅在「文件含陈旧标记且当前大纲已就绪」时做外科式
            # 替换：只改「习题精练」行的题源短语，[x] 勾选与其余行原样保留。
            if (subject_key == "pro" and "专业课大纲与真题待导入" in old_text
                    and _pro_syllabus_ready(ws)):
                _new_phrase = _material_phrase(plan.get("pro_books"), "选取",
                                               is_pro=True, workspace_root=ws)
                _patched = old_text.replace(
                    "（⚠️ 专业课大纲与真题待导入）选取", _new_phrase)
                if _patched != old_text:
                    try:
                        atomic_write_text(task_file, _patched)
                        return {"status": "refreshed", "path": str(task_file),
                                "task_count": _count_task_rows(_patched)}
                    except Exception:
                        pass
            return {"status": "exists", "path": str(task_file),
                    "task_count": _count_task_rows(old_text)}
    status_raw = _safe_write_today_task(task_file, today_str, content)
    status = "created" if status_raw == "已创建" else "overwritten"
    return {"status": status, "path": str(task_file),
            "task_count": 3 + (1 if _due_row else 0)}


def refresh_stale_today_tasks(plan=None, workspace_root=None) -> List[Dict]:
    """[审查修复·任务文件跨天不刷新] 读取侧兜底：把非当日的「今日任务.md」重写为当日版本。

    背景：全仓只有两条写入路径 —— 建档向导 ``generate_plan_and_today_files``
    与报到 ``ensure_subject_today_task``（且只处理报到的那一科）。考生当天不报到、
    直接 ``ky today`` / 打开看板时，四科文件仍是**昨天**的：任务面板显示昨天的
    任务、勾选也算进今天的完成率（实测 2026-10-03 读到 2026-10-02 的任务）。
    本函数在读取入口前调用一次即可让「今日」名副其实。

    行为边界（刻意保守）：
      - **文件不存在不动** —— 保持「报到/建档才生成」的既有语义，不给未报到的
        科目凭空造文件；
      - 过期文件走 ``ensure_subject_today_task`` 同款模板重写（不调 LLM、
        当日文件与 [x] 勾选绝不覆盖，禁用科目自动跳过）；
      - 任何单科失败只跳过该科，绝不阻断读取（读取侧兜底不得比不兜底更脆）。

    返回被刷新的科目信息列表（空列表 = 无需刷新）。
    """
    ws = Path(workspace_root) if workspace_root else ROOT
    if not isinstance(plan, dict):
        plan = None
    if plan is None:
        try:
            _cfg = json.loads((ws / "ky_config.json").read_text(encoding="utf-8"))
            plan = _cfg.get("study_plan") if isinstance(_cfg, dict) else {}
        except (OSError, ValueError):
            plan = {}
    if not isinstance(plan, dict):
        plan = {}

    today_str = datetime.now().strftime("%Y-%m-%d")
    refreshed: List[Dict] = []
    for _key, (_folder, _ratios, _name) in _CHECKIN_SUBJECT_SPECS.items():
        task_file = ws / _folder / "_状态" / "今日任务.md"
        if not task_file.exists():
            continue
        first_line = ""
        for _enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                first_line = task_file.read_text(encoding=_enc).splitlines()[0]
                break
            except (OSError, UnicodeDecodeError, IndexError):
                continue
        if today_str in first_line:
            continue
        try:
            info = ensure_subject_today_task(plan, _key, workspace_root=ws)
        except Exception:
            continue
        if str(info.get("status") or "") in ("created", "overwritten", "refreshed"):
            refreshed.append({"subject": _key, **info})
    return refreshed


def _generate_subject_task_content(subject_key: str, subject_display: str, plan: dict, today_str: str, default_template: str, workspace_root=None) -> str:
    """如果配置了大模型，调用 LLM 结合学员考研目标院校、专业与痛点生成深度定制的今日任务清单；未配置或调用失败则优雅降级为内置模板"""
    try:
        try:
            from tools.llm_client import is_llm_configured, chat_completion
        except ImportError:
            from llm_client import is_llm_configured, chat_completion

        ws = Path(workspace_root) if workspace_root else ROOT
        if is_llm_configured(workspace_root=ws):
            stage = plan.get("stage_name", "强化题型攻坚阶段")
            days_left = plan.get("days_left", 90)
            school = plan.get("school", "目标院校")
            major = plan.get("major", "目标专业")
            weakness = plan.get(f"{subject_key}_weakness", "基础薄弱与综合题推导")
            hours = float(plan.get(f"{subject_key}_hours", 2.0))
            minutes = max(15, int(hours * 60))

            prompt = (
                f"你是一位资深中国研究生入学考试专属私教总教练。\n"
                f"学员报考院校：{school}，报考专业：{major}。\n"
                f"初试倒计时：{days_left} 天，当前备考阶段：{stage}。\n"
                f"当前科目：{subject_display}，今日精力预算：{minutes} 分钟。\n"
                f"该科目当前核心薄弱痛点：{weakness}。\n\n"
                f"请为该学员生成今日该科目的专业学习任务 Markdown 内容。\n"
                f"格式规范：\n"
                f"1. 首行必须为：# 今日{subject_display}任务 ({today_str})\n"
                f"2. 次行引用块：> 研考倒计时：{days_left} 天 ｜ 当前阶段：{stage} ｜ 今日目标用时：{minutes} 分钟\n"
                f"3. 任务表格必须为标准 4 列：| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
                f"4. 包含 3 到 4 个具体任务行（针对该痛点的拆解、精练与反思），预计用时之和约为 {minutes} 分钟，状态为 [ ]。\n"
                f"5. 末尾附一两句私教提示与终端口令建议。\n"
                f"请直接输出 Markdown 内容，勿添加其他外层废话。"
            )
            resp = chat_completion(prompt, workspace_root=ws, timeout=12.0)
            if resp and ("# 今日" in resp or "# " in resp) and "| 模块 |" in resp:
                return resp.strip()
    except Exception:
        pass
    return default_template


def generate_plan_and_today_files(plan, ai_strategy=None, workspace_root=None):
    """根据向导结果全自动生成各科总规划文件与今日真实任务清单"""
    ws = Path(workspace_root) if workspace_root else ROOT
    today_str = datetime.now().strftime("%Y-%m-%d")
    days_left = plan.get("days_left", 0)
    stage_name = plan.get("stage_name", "强化题型攻坚阶段")

    if not ai_strategy:
        ai_strategy = generate_expert_diagnostic_strategy(plan)

    m_n = plan.get("math_name", "数学")
    e_n = plan.get("eng_name", "英语")
    pro_n = plan.get("pro_name", "专业课")
    pro2_n = plan.get("pro2_name", "").strip()

    exam_mode = plan.get("exam_mode")
    is_mode_c = exam_mode in ("mode_c", "mgmt_199") or plan.get("pol_disabled") or ("199" in str(pro_n))
    is_mode_b = exam_mode in ("mode_b", "no_math_dual_pro") or bool(pro2_n)

    math_disabled = is_mode_b or is_mode_c or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"} or m_n == "不考数学"
    m_h = 0.0 if math_disabled else float(plan.get("math_hours", 3.0))
    e_h = float(plan.get("eng_hours", 2.0))
    p_h = 0.0 if is_mode_c else float(plan.get("pol_hours", 1.0))
    pro_h = float(plan.get("pro_hours", 2.5))
    pro2_h = float(plan.get("pro2_hours", 2.0))

    school_name = plan.get('school', '目标院校')
    major_name = plan.get('major', '报考专业')

    # 1. 生成 00_考研全科总战役规划.md
    root_plan_file = ws / "00_考研全科总战役规划.md"

    if is_mode_c:
        table_rows = f"""| **{pro_n}** | {plan.get('pro_baseline', '120分')} | **{plan.get('pro_target', '140+ 分')}** | {pro_h} 小时 ({int(pro_h*60)}m) | 攻克【{plan.get('pro_weakness', '初数与逻辑综合')}】，199联考三合一综合能力 |
| **{e_n}** | {plan.get('eng_baseline', '50分')} | **{plan.get('eng_target', '70+ 分')}** | {e_h} 小时 ({int(e_h*60)}m) | 攻克【{plan.get('eng_weakness', '长难句主干拆解')}】，搭积木拆解，定位阅读选项逻辑 |
| **合计** | {baseline_total_label(plan, ['pro_baseline', 'eng_baseline'])} | **{plan.get('total_target', '215+ 分')}** | {plan.get('total_hours', 6.0)} 小时 | **199联考总分300分，初试不考政治与统考数学** |"""
        books_rows = f"""- **199管综权威资料**：`{plan.get('pro_books')}`\n- **英语权威资料**：`{plan.get('eng_books')}`"""
        budget_row = f"- **每日精力预算**：{plan.get('total_hours', 6.0)} 小时 (199管综 {pro_h}h / 英语 {e_h}h)"
    elif is_mode_b:
        # [同族修复·不考数学策略列] mode_b 模板此前策略列写「攻克必考核心题型…」
        # 数学文案，与本文件第 45 行「不安排任何数学复习战术」自相矛盾（与
        # AGENTS.md 主模板 996 行、init_workspace.py P4 先例同族，此处此前遗漏）。
        table_rows = f"""| **科目一：不考数学** | 不考数学 | **不考数学** | 0.0 小时 (0m) | 本方案不考数学，不安排数学学习任务 |
| **{e_n}** | {plan.get('eng_baseline', '50分')} | **{plan.get('eng_target', '65+ 分')}** | {e_h} 小时 ({int(e_h*60)}m) | 攻克【{plan.get('eng_weakness', '长难句主干拆解')}】，搭积木拆解，定位阅读选项逻辑 |
| **思想政治理论** | {plan.get('pol_baseline', '50分')} | **{plan.get('pol_target', '70+ 分')}** | {p_h} 小时 ({int(p_h*60)}m) | 攻克【{plan.get('pol_weakness', '马原多选题')}】，单选拿满，帽子词秒杀 |
| **{pro_n}** | {plan.get('pro_baseline', '80分')} | **{plan.get('pro_target', '120-130 分')}** | {pro_h} 小时 ({int(pro_h*60)}m) | 攻克【{plan.get('pro_weakness', '专业课一核心重点')}】，权威教材体系+真题深度解剖 |
| **{pro2_n or '专业课二'}** | {plan.get('pro2_baseline', '80分')} | **{plan.get('pro2_target', '120-130 分')}** | {pro2_h} 小时 ({int(pro2_h*60)}m) | 攻克【{plan.get('pro2_weakness', '专业课二论述框架')}】，第二门自命题考纲深化推导与背诵闭环 |
| **合计** | {baseline_total_label(plan, ['eng_baseline', 'pol_baseline', 'pro_baseline', 'pro2_baseline'])} | **{plan.get('total_target', '375+ 分')}** | {plan.get('total_hours', 7.0)} 小时 | **稳扎稳打拿牢核心得分盘，拒绝偏难怪题** |"""
        books_rows = f"""- **英语权威资料**：`{plan.get('eng_books')}`\n- **政治权威资料**：`{plan.get('pol_books')}`\n- **专业课一权威资料**：`{plan.get('pro_books')}`\n- **专业课二权威资料**：`{plan.get('pro2_books') or plan.get('pro_books')}`"""
        budget_row = f"- **每日精力预算**：{plan.get('total_hours', 7.0)} 小时 (英语 {e_h}h / 政治 {p_h}h / 专业课一 {pro_h}h / 专业课二 {pro2_h}h)"
    else:
        m_part = f"| **{m_n}** | {plan.get('math_baseline', '60分')} | **{plan.get('math_target', '110+ 分')}** | {m_h} 小时 ({int(m_h*60)}m) | 重点攻克【{plan.get('math_weakness', '计算失误')}】，规避失误，步骤规范化 |\n" if not math_disabled else ""
        _bt_keys = ["eng_baseline", "pol_baseline", "pro_baseline"]
        if not math_disabled:
            _bt_keys.insert(0, "math_baseline")
        table_rows = f"""{m_part}| **{e_n}** | {plan.get('eng_baseline', '50分')} | **{plan.get('eng_target', '65+ 分')}** | {e_h} 小时 ({int(e_h*60)}m) | 攻克【{plan.get('eng_weakness', '长难句主干拆解')}】，搭积木拆解，定位阅读选项逻辑 |
| **思想政治理论** | {plan.get('pol_baseline', '40分')} | **{plan.get('pol_target', '70+ 分')}** | {p_h} 小时 ({int(p_h*60)}m) | 攻克【{plan.get('pol_weakness', '马原多选题')}】，单选拿满，帽子词秒杀 |
| **{pro_n}** | {plan.get('pro_baseline', '80分')} | **{plan.get('pro_target', '120-130 分')}** | {pro_h} 小时 ({int(pro_h*60)}m) | 攻克【{plan.get('pro_weakness', '核心考点与解答步骤')}】，权威教材体系+真题深度解剖 |
| **合计** | {baseline_total_label(plan, _bt_keys)} | **{plan.get('total_target', '370+ 分')}** | {plan.get('total_hours', 8.5)} 小时 | **稳扎稳打拿牢核心得分盘，拒绝偏难怪题** |"""
        m_b = f"- **数学权威资料**：`{plan.get('math_books')}`\n" if not math_disabled else ""
        books_rows = f"""{m_b}- **英语权威资料**：`{plan.get('eng_books')}`\n- **政治权威资料**：`{plan.get('pol_books')}`\n- **专业课权威资料**：`{plan.get('pro_books')}`"""
        budget_row = f"- **每日精力预算**：{plan.get('total_hours', 8.5)} 小时 (英语 {e_h}h / 政治 {p_h}h / 专业课 {pro_h}h)" if math_disabled else f"- **每日精力预算**：{plan.get('total_hours', 8.5)} 小时 (数学 {m_h}h / 英语 {e_h}h / 政治 {p_h}h / 专业课 {pro_h}h)"

    root_plan_content = f"""# {school_name} {major_name} · 考研全科总战役规划与提分矩阵

> 本方案由 AI 私教中枢依据你的学情摸底、权威参考书白名单与备考作息全自动推演生成。
> 严防超纲，白名单出题，数据已同步至各科 AGENTS.md 与看板。

---

## 🎯 一、战役目标与提分矩阵

- **目标院校**：`{school_name}`
- **报考专业**：`{major_name}`
- **备考阶段**：`{stage_name}` (倒计时约 {days_left} 天)
- **研考初试日期**：`{plan.get('exam_date') or _FALLBACK_EXAM_DATE}`
- **初试总分目标**：`{plan.get('total_target', '370+ 分')}`
- **当前激活辅导风格**：`{plan.get('style_name', STYLES['1'][0])}`

| 科目 | 摸底/基准分 | 目标成绩 | 每日时间预算 | 核心薄弱防线与攻坚策略 |
|---|---|---|---|---|
{table_rows}

---

## 📚 二、手头已有备考资料白名单（真实锁定题源，严禁 AI 虚构）

{books_rows}

---

## 🌿 三、科学作息与防内耗调节机制

{budget_row}
- **每周放风休整**：`{plan.get('rest_weekly', '每周六晚或周日半天放风，调节身心')}`
- **每月复盘测试**：`{plan.get('rest_monthly', '每月最后周日进行一次全科阶段性复盘测试')}`
- **防内耗预警线**：若连续 3 天某科用时严重超标或正确率持续走低，自动触发【私教减负疏导】，削减偏难怪题，重回基本盘。

---

## 🤖 四、专属 AI 私教执行战略

{ai_strategy}
"""
    atomic_write_text(root_plan_file, root_plan_content)

    # 2. 生成各科目根目录 00_备考总规划.md
    if not math_disabled:
        math_plan_file = ws / "01-数学" / "00_数学备考总规划.md"
        atomic_write_text(math_plan_file, f"""# {m_n} · 备考总规划与命题得分图谱

- **目标成绩**：`{plan.get('math_target', '110+ 分')}` ｜ **摸底基准**：`{plan.get('math_baseline', '60分')}`
- **每日投入**：`{m_h} 小时 ({int(m_h*60)} 分钟)`
- **核心白名单资料**：`{plan.get('math_books')}`
- **专属薄弱项攻坚**：`{plan.get('math_weakness', '计算失误与综合大题证明')}`

## 一、阶段攻坚路线 (当前处于: {stage_name})
1. **基础地毯式扫盲**：严格依据官方考纲，掌握每一个定理的适用条件；
2. **高频题型模块突破**：攻坚【{plan.get('math_weakness', '计算失误')}】，固化解题套路与步骤采分点；
3. **历年真题全真推演**：近 15 年真题闭卷自测，阅卷人尺度批改；
4. **错题清零与公式默写**：错题彻底归因消灭，公式遮罩自测闭环。
""")

    eng_plan_file = ws / "02-英语" / "00_英语备考总规划.md"
    atomic_write_text(eng_plan_file, f"""# {e_n} · 备考总规划与提分路线图

- **目标成绩**：`{plan.get('eng_target', '65+ 分')}` ｜ **摸底基准**：`{plan.get('eng_baseline', '50分')}`
- **每日投入**：`{e_h} 小时 ({int(e_h*60)} 分钟)`
- **核心白名单资料**：`{plan.get('eng_books')}`
- **专属薄弱项攻坚**：`{plan.get('eng_weakness', '长难句拆解')}`

## 一、阶段攻坚路线 (当前处于: {stage_name})
1. **基础词汇与语法突破**：5500 词汇高频变形，长难句意群切分；
2. **真题阅读精读期**：吃透微观长难句与宏观段落逻辑，错题控制在 1 题/篇；
3. **写作与翻译提分**：固化大、小作文功能句型模板；
4. **全真模考冲刺**：3 小时闭卷全真模拟，演练答题节奏。
""")

    pol_plan_file = ws / "03-思想政治理论" / "00_政治备考总规划.md"
    if is_mode_c:
        atomic_write_text(pol_plan_file, f"""# 思想政治理论 · 规划说明

> 当前报考方案为 199 管理类综合能力，初试不考思想政治理论（无需备考政治）。
""")
    else:
        atomic_write_text(pol_plan_file, f"""# 思想政治理论 · 备考总规划与高分策略

- **目标成绩**：`{plan.get('pol_target', '70+ 分')}` ｜ **摸底基准**：`{plan.get('pol_baseline', '40分')}`
- **每日投入**：`{p_h} 小时 ({int(p_h*60)} 分钟)`
- **核心白名单资料**：`{plan.get('pol_books')}`
- **专属薄弱项攻坚**：`{plan.get('pol_weakness', '马原哲学多选题')}`

## 一、得分盘战略
- **单项选择题**：稳拿 14-16 分，不丢基本常识分；
- **多项选择题**：冲刺 24-28 分，主攻【{plan.get('pol_weakness', '多选漏选错选')}】，强化干扰项排除技巧；
- **分析大题**：30-34 分，原理帽子词对应到位 + 结合材料规范分点答题。
""")

    pro_plan_file = ws / "04-专业课" / "00_专业课备考总规划.md"
    atomic_write_text(pro_plan_file, f"""# {pro_n} · 备考总规划与专业课高分图谱

- **目标成绩**：`{plan.get('pro_target', '120-130 分')}` ｜ **摸底基准**：`{plan.get('pro_baseline', '80分')}`
- **每日投入**：`{pro_h} 小时 ({int(pro_h*60)} 分钟)`
- **核心白名单资料**：`{plan.get('pro_books')}`
- **专属薄弱项攻坚**：`{plan.get('pro_weakness', '核心考点掌握与解答步骤规范')}`

## 一、阶段攻坚路线 (当前处于: {stage_name})
1. **基础构建期**：通读官方指定教材，掌握核心概念定义与底层原理；
2. **专题突破期**：深挖高频大题与核心考点，规范解答推导与步骤采分点；
3. **真题闭卷期**：近 10-15 年真题全真演练，形成考点分值地图；
4. **押题回归期**：回归知识图谱骨架，消除一切薄弱项盲区。
""")

    if is_mode_b and pro2_n:
        pro2_plan_file = ws / "04-专业课" / "00_专业课二备考总规划.md"
        atomic_write_text(pro2_plan_file, f"""# {pro2_n} · 备考总规划与专业课二高分图谱

- **目标成绩**：`{plan.get('pro2_target', '120-130 分')}` ｜ **摸底基准**：`{plan.get('pro2_baseline', '80分')}`
- **每日投入**：`{pro2_h} 小时 ({int(pro2_h*60)} 分钟)`
- **核心白名单资料**：`{plan.get('pro2_books') or plan.get('pro_books')}`
- **专属薄弱项攻坚**：`{plan.get('pro2_weakness', '核心考点记不牢、论述题缺乏框架')}`

## 一、阶段攻坚路线 (当前处于: {stage_name})
1. **基础构建期**：通读第二门自命题考纲与参考书，梳理框架概念；
2. **专题突破期**：深挖高频论述大题与核心定理，规范推导与答题采分点；
3. **真题演练期**：历年真题全真模拟，构建答题模板与知识图谱；
4. **背诵清零期**：精准背诵核心考点，做到提笔成文、条理清晰。
""")

    # 3. 自动生成各科真实今日任务文件
    is_mode_b = plan.get("exam_mode") in ("mode_b", "no_math_dual_pro") or bool(plan.get("pro2_name"))
    is_mode_c = plan.get("exam_mode") in ("mode_c", "mgmt_199") or plan.get("pol_disabled")

    # 3.1 数学
    m_w = plan.get('math_weakness', '')
    m_task_desc = "本次报考不考数学，无需安排数学任务" if math_disabled else ("梳理考纲核心高频考点与必背公式，开展首日基础摸底自测" if (not m_w or '待首次自测' in m_w or '从零' in m_w or '无' in m_w) else f"攻坚薄弱项【{m_w}】定理条件与构造技巧")
    m_task_file = ws / "01-数学" / "_状态" / "今日任务.md"
    m_task_file.parent.mkdir(parents=True, exist_ok=True)
    m_tpl = f"""# 今日数学任务 ({today_str})

> 研考倒计时：{days_left} 天 ｜ 当前阶段：{stage_name} ｜ 今日目标用时：{int(m_h*60)} 分钟

| 模块 | 任务内容 | 预计用时 | 完成状态 |
|---|---|---|---|
| 概念精讲 | {m_task_desc} | {int(m_h*60*0.2)} 分钟 | [ ] |
| 习题精练 | {_material_phrase(plan.get('math_books'), '精选')}对应专题典型真题动笔演练 | {int(m_h*60*0.55)} 分钟 | [ ] |
| 订正归档 | 在 CLI 输入「交作业」，AI 按步骤采分并自动录入错题队列 | {int(m_h*60*0.25)} 分钟 | [ ] |

> **私教提示**：在终端输入 `/math` 或 `数学报到`，私教即可根据今日任务派发第一道针对性试题！
""" if not math_disabled else f"""# 数学任务 ({today_str})

> 当前方案标记为「不考数学」，本日不安排数学学习任务。
"""
    m_content = m_tpl if math_disabled else _generate_subject_task_content("math", m_n, plan, today_str, m_tpl, workspace_root=ws)
    m_status = _safe_write_today_task(m_task_file, today_str, m_content)
    print(f"  - 数学今日任务: {m_status}")

    # 3.2 英语
    e_w = plan.get('eng_weakness', '')
    e_task_desc = "精读真题高频长难句语法骨架，开展首日主干拆解摸底 (输入 /dissect 实战)" if (not e_w or '待首次自测' in e_w or '从零' in e_w) else f"攻坚薄弱项【{e_w}】(输入 /dissect 实战)"
    e_task_file = ws / "02-英语" / "_状态" / "今日任务.md"
    e_task_file.parent.mkdir(parents=True, exist_ok=True)
    e_tpl = f"""# 今日英语任务 ({today_str})

> 研考倒计时：{days_left} 天 ｜ 当前阶段：{stage_name} ｜ 今日目标用时：{int(e_h*60)} 分钟

| 模块 | 任务内容 | 预计用时 | 完成状态 |
|---|---|---|---|
| 词汇破冰 | 快速复习 50 个高频核心真题词汇与派生变形 | {int(e_h*60*0.25)} 分钟 | [ ] |
| 长难句解剖 | {e_task_desc} | {int(e_h*60*0.35)} 分钟 | [ ] |
| 真题阅读 | 精读 1 篇历年真题阅读并定位干扰项逻辑 | {int(e_h*60*0.4)} 分钟 | [ ] |

> **私教提示**：在终端输入 `/eng` 或 `英语报到` 开始今日英语专项训练！
"""
    e_content = _generate_subject_task_content("eng", e_n, plan, today_str, e_tpl, workspace_root=ws)
    e_status = _safe_write_today_task(e_task_file, today_str, e_content)
    print(f"  - 英语今日任务: {e_status}")

    # 3.3 政治
    p_w = plan.get('pol_weakness', '')
    p_task_desc = "梳理考纲核心考点与帽子词框架，精选高频选择题摸底" if (not p_w or '待首次自测' in p_w or '从零' in p_w) else f"梳理【{p_w}】知识框架"
    p_task_file = ws / "03-思想政治理论" / "_状态" / "今日任务.md"
    p_task_file.parent.mkdir(parents=True, exist_ok=True)
    p_tpl = f"""# 今日政治任务 ({today_str})

> 研考倒计时：{days_left} 天 ｜ 当前阶段：{stage_name} ｜ 今日目标用时：{int(p_h*60)} 分钟

| 模块 | 任务内容 | 预计用时 | 完成状态 |
|---|---|---|---|
| 核心考点 | {p_task_desc} | {int(p_h*60*0.4)} 分钟 | [ ] |
| 选择刷题 | {_material_phrase(plan.get('pol_books'), '精做')} 20 道核心选择题自测 | {int(p_h*60*0.4)} 分钟 | [ ] |
| 易混归纳 | 记录做错的帽子词与混淆概念，固化到记忆卡 | {int(p_h*60*0.2)} 分钟 | [ ] |

> **私教提示**：在终端输入 `/pol` 或 `政治报到` 启动今日政治考点抽查！
""" if not is_mode_c else f"""# 政治任务 ({today_str})

> 当前方案为管理类联考 199 模式，初试不考思想政治理论，本日不安排政治学习任务。
"""
    p_content = p_tpl if is_mode_c else _generate_subject_task_content("pol", "思想政治理论", plan, today_str, p_tpl, workspace_root=ws)
    p_status = _safe_write_today_task(p_task_file, today_str, p_content)
    print(f"  - 政治今日任务: {p_status}")

    # 3.4 专业课
    pro_w = plan.get('pro_weakness', '')
    # [P2-12 修复·护理话术] 专业课话术随 pro_name 切换：护理综合 (308) 的题型以
    # 名词解释 / 简答 / 病例分析为主，通用「经典大题推导」话术会把备考方向带偏。
    _pro_is_nursing = ("护理" in str(pro_n)) or ("308" in str(pro_n))
    if _pro_is_nursing:
        pro_task_desc = (
            "梳理基础护理学与内、外科护理学核心考点，完成首日题型规范度摸底"
            if (not pro_w or '待首次自测' in pro_w or '从零' in pro_w)
            else f"聚焦【{pro_w}】的病例分析与护理措施推导"
        )
        _pro_practice = "名词解释、简答题与 1 道病例分析题，动笔完整书写"
    else:
        # [问题5 修复] 大纲占位/缺失时不得写「专业课官方考纲」字样
        _pro_ready = _pro_syllabus_ready(ws)
        _pro_scope = ("专业课官方考纲核心知识体系" if _pro_ready
                      else "专业课核心知识体系")
        _pro_scope_short = "专业课考纲" if _pro_ready else "专业课主干知识"
        pro_task_desc = (f"聚焦{_pro_scope}，完成首日题型规范度摸底"
                         if (not pro_w or '待首次自测' in pro_w or '从零' in pro_w)
                         else f"聚焦{_pro_scope_short}与【{pro_w}】推导")
        _pro_practice = "经典大题 2~3 道动笔完整书写"
    pro_task_file = ws / "04-专业课" / "_状态" / "今日任务.md"
    pro_task_file.parent.mkdir(parents=True, exist_ok=True)
    pro_label = "专业课一" if is_mode_b else "专业课"
    pro_tpl = f"""# 今日{pro_label}任务 ({today_str})

> 研考倒计时：{days_left} 天 ｜ 当前阶段：{stage_name} ｜ 今日目标用时：{int(pro_h*60)} 分钟

| 模块 | 任务内容 | 预计用时 | 完成状态 |
|---|---|---|---|
| 核心知识点 | {pro_task_desc} | {int(pro_h*60*0.3)} 分钟 | [ ] |
| 习题精练 | {_material_phrase(plan.get('pro_books'), '选取', is_pro=True, workspace_root=ws)}{_pro_practice} | {int(pro_h*60*0.5)} 分钟 | [ ] |
| AI 阅卷批改 | 将草稿或解答输入 CLI (可用 /img 上传草稿照片)，逐行诊断丢分点 | {int(pro_h*60*0.2)} 分钟 | [ ] |

> **私教提示**：在终端输入 `/pro` 或 `专业课报到` 开始今日专业课攻坚！
"""
    pro_content = _generate_subject_task_content("pro", pro_n, plan, today_str, pro_tpl, workspace_root=ws)
    pro_status = _safe_write_today_task(pro_task_file, today_str, pro_content)
    print(f"  - 专业课今日任务: {pro_status}")

    # 3.5 专业课二 (用于双自命题模式)
    if is_mode_b:
        pro2_name_eff = pro2_n or "专业课二"
        pro2_w = plan.get("pro2_weakness", "论述题缺乏框架与深度")
        pro2_task_file = ws / "04-专业课" / "_状态" / "今日任务_专业课二.md"
        pro2_tpl = f"""# 今日专业课二任务 ({today_str})

> 研考倒计时：{days_left} 天 ｜ 当前阶段：{stage_name} ｜ 今日目标用时：{int(pro2_h*60)} 分钟

| 模块 | 任务内容 | 预计用时 | 完成状态 |
|---|---|---|---|
| 框架构建 | 聚焦【{pro2_name_eff}】核心考点，攻坚薄弱点【{pro2_w}】 | {int(pro2_h*60*0.35)} 分钟 | [ ] |
| 真题深挖 | 研读并书写历年代表性综合论述大题 1~2 道 | {int(pro2_h*60*0.45)} 分钟 | [ ] |
| 错因复盘 | 总结采分失分点，固化答题逻辑模块 | {int(pro2_h*60*0.2)} 分钟 | [ ] |

> **私教提示**：双自命题专业课复习权重极高，请务必每天按时完成论述框架演练！
"""
        pro2_content = _generate_subject_task_content("pro2", pro2_name_eff, plan, today_str, pro2_tpl, workspace_root=ws)
        pro2_status = _safe_write_today_task(pro2_task_file, today_str, pro2_content)
        print(f"  - 专业课二今日任务: {pro2_status}")

    # 7. 建档学情状态初始化（UT3 P2 批次）：痛点注入雷达 + 学员档案个性化。
    # 独立 try：初始化失败不得阻断建档主流程（规划/任务/AGENTS 已写完）。
    try:
        init_state_profiles_from_plan(plan, workspace_root=ws)
        seed_radars_from_plan(plan, workspace_root=ws)
    except Exception as e:
        print(colorize(f"  [!] 学情状态初始化提示: {e}", C.YELLOW))


# ═══════════════ 建档学情状态初始化（UT3 P2 批次） ═══════════════

def _weakness_is_real(raw) -> bool:
    """痛点文本是否为考生真实填写（排除空值与各类默认占位话术）。"""
    w = str(raw or "").strip()
    if not w or w == "无":
        return False
    return not any(p in w for p in ("待首次自测", "待摸底", "从零"))


def _first_table_block(lines):
    """返回第一个表格块 [start, end] 行号区间（连续以 | 开头的行），无则 None。"""
    start = end = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("|"):
            if start is None:
                start = i
            end = i
        elif start is not None:
            break
    return (start, end) if start is not None else None


def _render_profile_from_template(tgt_text: str, tpl_text: str,
                                  replacements: Dict[str, str]) -> str:
    """行级模板渲染：仅替换「与模板对应行逐字一致」的行。

    [P2 修复·绝不覆盖人工内容] 考生或私教编辑过的行不匹配模板行集合，
    原样保留；仅模板态行（占位符 / 硬编码默认值）替换为 plan 真实值。
    """
    tpl_line_set = {l.strip() for l in tpl_text.splitlines()}
    out = []
    for ln in tgt_text.splitlines():
        s = ln.strip()
        if s in tpl_line_set and s in replacements:
            out.append(replacements[s])
        else:
            out.append(ln)
    text = "\n".join(out)
    if tgt_text.endswith("\n"):
        text += "\n"
    return text


def _replace_table_body_if_template(tgt_text: str, tpl_text: str,
                                    rows: List[str]) -> str:
    """若第一个表格块的数据行全部仍是模板行，则整体替换为 rows。

    用于专业课学情档案的章节雷达：模板骨架（信号/系统类示例）在非对应
    学科下是误导，且无法自动生成学科贴合章节 —— 替换为占位行（UT3
    三沙箱 D-03 期望口径：「明确标注待生成」）。已编辑过表体的文件不动。
    """
    tgt_lines = tgt_text.splitlines()
    blk = _first_table_block(tgt_lines)
    if not blk:
        return tgt_text
    start, end = blk
    body = tgt_lines[start + 2:end + 1]  # 表头 + 分隔行之后的数据行
    # 「建档摸底核心卡点」是 seed_radars_from_plan 的注入行：不参与模板态
    # 判据，替换后原样保留 —— 两个初始化函数以任意顺序被调用时互不干扰。
    keep_rows = [l for l in body if "建档摸底核心卡点" in l]
    body = [l for l in body if "建档摸底核心卡点" not in l]
    tpl_line_set = {l.strip() for l in tpl_text.splitlines()}
    if not body or not all(l.strip() in tpl_line_set for l in body):
        return tgt_text
    new_lines = tgt_lines[:start + 2] + list(rows) + keep_rows + tgt_lines[end + 1:]
    text = "\n".join(new_lines)
    if tgt_text.endswith("\n"):
        text += "\n"
    return text


def _init_one_profile(tpl_fp: Path, tgt_fp: Path, title_re: str, title_new: str,
                      replacements: Dict[str, str], table_rows=None) -> None:
    """单份档案的模板态渲染（文件缺失时以模板为底稿生成）。"""
    try:
        tpl = tpl_fp.read_text(encoding="utf-8")
    except Exception:
        return
    try:
        base = tgt_fp.read_text(encoding="utf-8")
    except Exception:
        base = tpl
    text = re.sub(title_re, title_new, base, count=1, flags=re.MULTILINE)
    text = _render_profile_from_template(text, tpl, replacements)
    if table_rows is not None:
        text = _replace_table_body_if_template(text, tpl, table_rows)
    if text != base:
        atomic_write_text(tgt_fp, text)


def init_state_profiles_from_plan(plan, workspace_root=None):
    """建档收尾：把学情摸底数据初始化进各科「学员档案/学情档案」。

    [P2 修复·档案模板残留] copy_templates 只做纯拷贝，建档收集的
    科目/目标分/时长/院校/阶段从未写入档案 —— 三沙箱实测：数学档案保留
    「[数学一 / 数学 / 数学三]」字面量、英语档案「120 分钟」（考生实际 90）、
    政治档案「70+ 分」（考生实际 68+）、专业课档案保留「[你的目标院校]」
    与「（模板）」标题、章节雷达为信号/系统类通用骨架。档案是 AI 会话启动
    读取的外置记忆，残留会直接误导私教与考生。渲染只覆盖「仍与模板逐字
    一致」的行，绝不触碰人工编辑过的内容。
    """
    ws = Path(workspace_root) if workspace_root else ROOT
    plan = plan or {}
    m_n = str(plan.get("math_name") or "数学")
    e_n = str(plan.get("eng_name") or "英语")
    pro_n = str(plan.get("pro_name") or "专业课")
    school = str(plan.get("school") or "目标院校")
    stage_name = str(plan.get("stage_name") or "强化题型攻坚阶段")
    exam_mode = plan.get("exam_mode")
    is_mode_c = exam_mode in ("mode_c", "mgmt_199") or plan.get("pol_disabled")
    math_disabled = (is_mode_c
                     or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"}
                     or plan.get("math_name") == "不考数学")

    def _minutes(key: str, default_h: float) -> int:
        try:
            h = float(plan.get(f"{key}_hours", default_h) or default_h)
        except (TypeError, ValueError):
            h = default_h
        return max(0, int(h * 60))

    # ── 数学学员档案 ──
    try:
        if math_disabled:
            _init_one_profile(
                ws / "01-数学" / "_状态" / "学员档案.template.md",
                ws / "01-数学" / "_状态" / "学员档案.md",
                r"^# 数学学员档案$", "# 数学学员档案",
                {
                    "- **考试科目**：[数学一 / 数学 / 数学三]": "- **考试科目**：`不考数学`",
                    "- **目标分数**：[待填，如：110 分]": "- **目标分数**：`不考数学`",
                    "- **每日时间**：[待填，如：150 分钟]": "- **每日时间**：`0 分钟`",
                    "- **核心题源**：[待填，如：经典习题册 + 历年真题]": "- **核心题源**：`不考数学`",
                })
        else:
            repl = {
                "- **考试科目**：[数学一 / 数学 / 数学三]": f"- **考试科目**：`{m_n}`",
                "- **目标分数**：[待填，如：110 分]": f"- **目标分数**：`{plan.get('math_target', '110+ 分')}`",
                "- **每日时间**：[待填，如：150 分钟]": f"- **每日时间**：`{_minutes('math', 2.0)} 分钟`",
                "- **核心题源**：[待填，如：经典习题册 + 历年真题]": f"- **核心题源**：`{plan.get('math_books') or '暂未放置实体资料'}`",
            }
            if _weakness_is_real(plan.get("math_weakness")):
                repl["- **高频薄弱点**：[待通过每日刷题逐步沉淀]"] = \
                    f"- **高频薄弱点**：`{plan.get('math_weakness')}`"
            _init_one_profile(
                ws / "01-数学" / "_状态" / "学员档案.template.md",
                ws / "01-数学" / "_状态" / "学员档案.md",
                r"^# 数学学员档案$", f"# {m_n}学员档案", repl)
    except Exception as e:
        print(colorize(f"  [!] 数学学员档案初始化提示: {e}", C.YELLOW))

    # ── 英语学员档案 ──
    try:
        _init_one_profile(
            ws / "02-英语" / "_状态" / "学员档案.template.md",
            ws / "02-英语" / "_状态" / "学员档案.md",
            r"^# 英语学员档案$", f"# {e_n}学员档案",
            {
                "- **考试科目**：[英语一 / 英语]": f"- **考试科目**：`{e_n}`",
                "- **目标分数**：[待填，如：65 分]": f"- **目标分数**：`{plan.get('eng_target', '65+ 分')}`",
                "- **每日用时**：120 分钟": f"- **每日用时**：`{_minutes('eng', 2.0)} 分钟`",
            })
    except Exception as e:
        print(colorize(f"  [!] 英语学员档案初始化提示: {e}", C.YELLOW))

    # ── 政治学员档案 ──
    try:
        if not is_mode_c:
            _init_one_profile(
                ws / "03-思想政治理论" / "_状态" / "学员档案.template.md",
                ws / "03-思想政治理论" / "_状态" / "学员档案.md",
                r"^# 政治学员档案$", "# 政治学员档案",
                {
                    "- **目标成绩**：70+ 分": f"- **目标成绩**：`{plan.get('pol_target', '70+ 分')}`",
                    "- **每日投入**：60 分钟": f"- **每日投入**：`{_minutes('pol', 1.0)} 分钟`",
                })
    except Exception as e:
        print(colorize(f"  [!] 政治学员档案初始化提示: {e}", C.YELLOW))

    # ── 专业课学情档案（根级 + _状态/ 两份，与 error_logger 雷达候选同源）──
    pro_repl = {
        "- **目标院校**：[你的目标院校]": f"- **目标院校**：`{school}`",
        "- **专业课名称**：[你的专业课名称与代码]": f"- **专业课名称**：`{pro_n}`",
        "- **目标成绩**：120 - 130 分 / 满分 150 分":
            f"- **目标成绩**：`{plan.get('pro_target', '120-130 分')}` / 满分 150 分",
        "- **当前阶段**：阶段一：基础概念与课后习题过关": f"- **当前阶段**：`{stage_name}`",
    }
    pro_rows = ["| — | 待私教按考纲与学习进度生成 | — | — | — |"]
    for rel_dir in ("", "_状态"):
        try:
            sub = f"{rel_dir}/" if rel_dir else ""
            _init_one_profile(
                ws / "04-专业课" / sub / "学情档案.template.md",
                ws / "04-专业课" / sub / "学情档案.md",
                r"^# 专业课学情档案(?:（模板）)?$", f"# {pro_n}学情档案",
                pro_repl, table_rows=pro_rows)
        except Exception as e:
            print(colorize(f"  [!] 专业课学情档案初始化提示: {e}", C.YELLOW))


def _minimal_radar_md(title: str, header: str) -> str:
    """[UT4 修复·PLANNER-1] 无模板兜底：生成最小可用的雷达/档案骨架文件。

    仅含与该科注入行（row_fmt）列数一致的首个表格块（表头 + 分隔行）与
    通用的「错因五分类」表 —— 数据行由 seed_radars_from_plan 的注入逻辑
    紧随其后写入；查漏/看板据此可读到建档卡点，不再静默丢数据。
    """
    ncol = max(header.count("|") - 1, 1)
    sep = "|" + "---|" * ncol
    return (
        f"# {title}（建档最小骨架）\n\n"
        f"> 该文件在建档时缺失且无同名模板，已由建档流程生成最小骨架；"
        f"建议运行 `py tools/init_workspace.py` 重建完整模板体系。\n\n"
        f"{header}\n{sep}\n\n"
        "## 错因五分类\n\n"
        "| # | 错因 | 典型表现 | 改进动作 | 次数 |\n"
        "|---|---|---|---|---|\n"
        "| 1 | 计算失误 | 符号/代数/化简失误 | 每步只做一件事并回代验算 | 0 |\n"
        "| 2 | 概念漏洞 | 定义不准/条件漏用 | 追溯课本定义与反例 | 0 |\n"
        "| 3 | 公式记错 | 混淆公式符号/适用范围 | 默写卡强化记忆 | 0 |\n"
        "| 4 | 审题偏差 | 遗漏限制条件/看错目标 | 圈画题干关键词 | 0 |\n"
        "| 5 | 书写丢分 | 跳步/无结论/表述不严谨 | 严格按考研真题采分点步骤书写 | 0 |\n"
    )


def seed_radars_from_plan(plan, workspace_root=None):
    """把建档痛点作为「建档摸底核心卡点」行注入各科雷达（占位态专用）。

    [P2 修复·痛点未注入雷达] 建档向导宣称痛点已注入薄弱项雷达，但旧实现
    只写 plan/AGENTS.md —— 雷达（copy_templates 纯拷贝）始终「待首次自测
    评估」，查漏因此显示「暂无已登记的薄弱项」（三沙箱实测）。现注入一行
    「建档摸底核心卡点」，查漏/看板即可读到考生自述卡点；注入幂等（先移除
    旧行再写）；痛点未填或禁用科目跳过。雷达载体与查漏同源：04 用学情档案。

    [UT4 修复·PLANNER-1] 非交互/preset 建档不跑 init_workspace.copy_templates()，
    01/02/03 各科 `_状态/薄弱点雷达.md` 只有 .template 未实例化，旧实现对
    `not fp.exists()` 静默 continue → 建档痛点丢失、doctor 必红（UT4 理论
    物理沙箱 BUG-1，P1）。现分两档兜底：① 同名 .template.md 存在 → 先拷贝
    实例化再注入（与完整向导产物一致）；② 模板也缺 → 生成最小可用骨架并
    显式告警。04 专业课学情档案的幂等注入行为保持不变。
    """
    import shutil
    ws = Path(workspace_root) if workspace_root else ROOT
    plan = plan or {}
    exam_mode = plan.get("exam_mode")
    is_mode_c = exam_mode in ("mode_c", "mgmt_199") or plan.get("pol_disabled")
    math_disabled = (is_mode_c
                     or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"}
                     or plan.get("math_name") == "不考数学")

    specs = [
        ("math", math_disabled, ["01-数学/_状态/薄弱点雷达.md"],
         "数学模块掌握度雷达", "| 模块名称 | 预估分值 | 当前评级 | 核心卡点与错因 |",
         "| 建档摸底核心卡点 | — | 未测 | 建档卡点：{w}（自述） |"),
        ("eng", False, ["02-英语/_状态/薄弱点雷达.md"],
         "英语能力雷达与长难句卡片", "| 题型模块 | 熟练评级 | 主要失分原因 |",
         "| 建档摸底核心卡点 | 未测 | 建档卡点：{w}（自述） |"),
        ("pol", is_mode_c, ["03-思想政治理论/_状态/薄弱点雷达.md"],
         "政治模块掌握度雷达", "| 模块 | 考题形式 | 目标分 | 当前评级 | 易混卡点 |",
         "| 建档摸底核心卡点 | — | — | 未测 | 建档卡点：{w}（自述） |"),
        ("pro", False, ["04-专业课/学情档案.md", "04-专业课/_状态/学情档案.md"],
         "专业课学情档案", "| 章节 | 掌握度 | 薄弱点 | 优先级 | 备注 |",
         "| 建档摸底核心卡点 | 建档卡点：{w}（自述） | — | — | — |"),
    ]
    for key, disabled, rel_paths, min_title, min_header, row_fmt in specs:
        if disabled:
            continue
        w = str(plan.get(f"{key}_weakness") or "").strip()
        for rel in rel_paths:
            fp = ws / rel
            if not fp.exists():
                # [UT4 修复·PLANNER-1] 工作文件缺失不再静默跳过（见 docstring）。
                _tpl = fp.with_suffix(".template.md")
                try:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    if _tpl.exists():
                        shutil.copy2(_tpl, fp)
                        print(colorize(f"  [i] 已从模板实例化缺失文件: {rel}", C.YELLOW))
                    else:
                        atomic_write_text(fp, _minimal_radar_md(min_title, min_header))
                        print(colorize(
                            f"  [!] {rel} 缺失且无同名模板，已生成最小可用骨架；"
                            f"建议运行 py tools/init_workspace.py 重建完整模板体系。", C.YELLOW))
                except Exception as e:
                    print(colorize(f"  [!] 建档状态文件兜底失败 ({rel}): {e}", C.YELLOW))
                    continue
            try:
                text = fp.read_text(encoding="utf-8")
                lines = [l for l in text.splitlines() if "建档摸底核心卡点" not in l]
                if _weakness_is_real(w):
                    blk = _first_table_block(lines)
                    row = row_fmt.format(w=w)
                    if blk:
                        lines.insert(blk[1] + 1, row)
                    else:
                        lines.append(row)
                new_text = "\n".join(lines)
                if text.endswith("\n"):
                    new_text += "\n"
                if new_text != text:
                    atomic_write_text(fp, new_text)
            except Exception as e:
                print(colorize(f"  [!] 薄弱点雷达注入提示 ({rel}): {e}", C.YELLOW))


#: [UT4 修复·PLANNER-2] 04-专业课/AGENTS.md 缺失时的重建骨架（内容中性：
#: 院校/科目名/白名单等由 update_subject_agents 的既有 re.sub 按 plan 回填）。
#: UT4 三沙箱实测：骨架/隐私清理副本缺该文件时建档静默跳过，doctor 持续报
#: 「缺失关键状态文件: AGENTS.md」，根 AGENTS.md 的「专业课报到」路由悬空。
#: 骨架的「学员自定义配置区」行格式与仓库自带模板一致，保证回填正则可命中。
_PRO_AGENTS_SKELETON = """# AGENTS.md —— 专业课通用私教系统协议模板

> 本文件是通用专业课私教系统协议。本模板适用于全国统考（如 408计算机综合、311教育学等）与全国各高校自命题专业课。

## 0. 你的身份与教学原则
你是一位具有丰富考研专业课命题与阅卷经验的专属私教老师。你的原则是：
1. **真题与考纲导向**：严格依据报考院校公布的最新考试大纲与历年真题规律组织教学；
2. **拒绝盲目做偏题**：所有派题必须出自权威指定教材课后题或历年真题，严禁自编题；
3. **步骤赋分批改**：解答题必须写明采分点与推导逻辑。

### 学员自定义配置区（使用者自填）
- **目标院校**：`目标院校`
- **专业代码与名称**：`报考专业`
- **专业课科目代码与名称**：`专业课`
- **满分与目标成绩**：`目标 120-130 分` (摸底: [摸底])
- **指定白名单资料**：`暂未放置实体资料（私教按目标院校自命题大纲出题，严禁虚构书目）`

## 1. 交互口令
- 输入 `专业课报到`：调取学情档案并派发今日核心习题；
- 输入 `交作业`：上传解答，AI 分步打分并指出公式和推导漏洞；
- 输入 `查漏`：调取掌握度评级与错题重做队列。
"""


def update_subject_agents(plan, workspace_root=None):
    """将真实白名单与薄弱项同步写入 01~04 各科专属 AGENTS.md (杜绝虚构书目)"""
    ws = Path(workspace_root) if workspace_root else ROOT
    exam_mode = plan.get("exam_mode")
    pro_n = plan.get("pro_name", "专业课")
    is_mode_c = exam_mode in ("mode_c", "mgmt_199") or plan.get("pol_disabled") or ("199" in str(pro_n))
    pro2_n = plan.get("pro2_name", "").strip()
    is_mode_b = exam_mode in ("mode_b", "no_math_dual_pro") or bool(pro2_n)
    math_disabled = is_mode_b or is_mode_c or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"} or plan.get("math_name") == "不考数学"

    # [缺陷修复·None 字面量泄漏] GUI 向导产出的 study_plan 不含 *_books 键，
    # 旧实现直接把 plan.get('math_books')（=None）经 f-string 写成字面量
    # ``None`` 落进各科 AGENTS.md 白名单行（实测复现），agent 读到的白名单
    # 是一句 "None" 而非规范占位说明。现统一归一化为与根 AGENTS.md 同款文案。
    plan = dict(plan)
    _book_defaults = {
        "math_books": "不考数学" if math_disabled else str(plan.get("math_name") or "数学"),
        "eng_books": str(plan.get("eng_name") or "英语"),
        "pol_books": "政治",
        "pro_books": str(pro_n or "专业课"),
    }

    # [问题5 修复·专业课无大纲却谎称按纲出题] 专业课为院校自命题：占位文案
    # 按大纲真实状态分档（判定与文案收口在 syllabus_manager，见
    # pro_books_placeholder_text）—— AGENTS.md 是 Agent 的常驻上下文，
    # 源头写下的虚假承诺会被 LLM 原样复述给学员（实测）。
    _pro_placeholder_text = None
    try:
        try:
            from tools import syllabus_manager as _syl_mgr
        except ImportError:
            import syllabus_manager as _syl_mgr
        _pro_placeholder_text = _syl_mgr.pro_books_placeholder_text(
            ws, str(pro_n or "专业课"))
    except Exception:
        _pro_placeholder_text = None

    for _bk, _blabel in _book_defaults.items():
        if not str(plan.get(_bk) or "").strip():
            if _bk == "pro_books" and _pro_placeholder_text:
                plan[_bk] = _pro_placeholder_text
            elif _bk == "math_books" and is_custom_math_key(plan.get("math_key")):
                # [UT4 修复·PLANNER-6] 自命题数学无全国统考大纲，不得写
                # 「按官方考纲出题」（UT4 理论物理沙箱 BUG-5 同族）。
                plan[_bk] = "暂未放置实体资料（私教按目标院校自命题大纲出题，严禁虚构书目）"
            else:
                plan[_bk] = f"暂未放置实体资料（私教严格按【{_blabel}】官方考纲出题，严禁虚构书目）"

    # 数学
    m_file = ws / "01-数学" / "AGENTS.md"
    if m_file.exists():
        t = m_file.read_text(encoding="utf-8")
        if math_disabled:
            t = re.sub(r"- \*\*考试科目\*\*：.*", "- **考试科目**：`不考数学`", t)
            t = re.sub(r"- \*\*目标分数\*\*：.*", "- **目标分数**：`不考数学`", t)
            t = re.sub(r"- \*\*每日投入\*\*：.*", "- **每日投入**：`0.0 小时`", t)
        else:
            t = re.sub(r"- \*\*考试科目\*\*：.*", f"- **考试科目**：`{plan.get('math_name', '数学')}`", t)
            t = re.sub(r"- \*\*目标分数\*\*：.*", f"- **目标分数**：`{plan.get('math_target', '110+ 分')}` (摸底: {plan.get('math_baseline', '60分')})", t)
            t = re.sub(r"- \*\*每日投入\*\*：.*", f"- **每日投入**：`{plan.get('math_hours', 2.5)} 小时`", t)
        t = re.sub(r"- \*\*核心教材.*", f"- **核心教材与白名单**：`{plan.get('math_books')}` (严禁虚构未有资料！)", t)
        if "- **核心薄弱点**：" in t:
            t = re.sub(r"- \*\*核心薄弱点\*\*：.*", f"- **核心薄弱点**：`{plan.get('math_weakness', '待首次自测诊断 (从零建立学情雷达)')}`", t)
        else:
            t = t.replace("- **核心教材与白名单**：", f"- **核心薄弱点**：`{plan.get('math_weakness', '待首次自测诊断 (从零建立学情雷达)')}`\n- **核心教材与白名单**：")
        atomic_write_text(m_file, t)

    # 英语
    e_file = ws / "02-英语" / "AGENTS.md"
    if e_file.exists():
        t = e_file.read_text(encoding="utf-8")
        t = re.sub(r"- \*\*考试科目\*\*：.*", f"- **考试科目**：`{plan.get('eng_name', '英语')}`", t)
        t = re.sub(r"- \*\*目标分数\*\*：.*", f"- **目标分数**：`{plan.get('eng_target', '65+ 分')}` (摸底: {plan.get('eng_baseline', '50分')})", t)
        t = re.sub(r"- \*\*每日投入\*\*：.*", f"- **每日投入**：`{plan.get('eng_hours', 2.0)} 小时`", t)
        t = re.sub(r"- \*\*核心.*白名单.*", f"- **核心资料与白名单**：`{plan.get('eng_books')}`", t)
        if "- **核心薄弱点**：" in t:
            t = re.sub(r"- \*\*核心薄弱点\*\*：.*", f"- **核心薄弱点**：`{plan.get('eng_weakness', '待首次自测诊断 (从零建立长难句与题型雷达)')}`", t)
        else:
            t = t.replace("- **核心资料与白名单**：", f"- **核心薄弱点**：`{plan.get('eng_weakness', '待首次自测诊断 (从零建立长难句与题型雷达)')}`\n- **核心资料与白名单**：")
        atomic_write_text(e_file, t)

    # 政治
    p_file = ws / "03-思想政治理论" / "AGENTS.md"
    if p_file.exists():
        t = p_file.read_text(encoding="utf-8")
        if is_mode_c:
            t = re.sub(r"- \*\*目标分数\*\*：.*", "- **目标分数**：`不考政治（199联考模式）`", t)
            t = re.sub(r"- \*\*核心薄弱点\*\*：.*", "- **核心薄弱点**：`不考政治`", t)
        else:
            if "### 学员配置区" not in t:
                t += f"\n### 学员配置区\n- **目标分数**：`{plan.get('pol_target', '70+ 分')}` (摸底: {plan.get('pol_baseline', '40分')})\n- **每日投入**：`{plan.get('pol_hours', 1.0)} 小时`\n- **核心书目**：`{plan.get('pol_books')}`\n- **核心薄弱点**：`{plan.get('pol_weakness', '马原哲学原理')}`\n"
            else:
                # [P1 修复·学情漏更新] 旧实现仅在「学员配置区」不存在时写入 目标分数/
                # 每日投入，存在时（模板自带该区，属常态路径）只更新 核心书目/薄弱点
                # —— 考生个性化目标与摸底被跳过，模板残留「70+ 分」「摸底40分」等
                # 凭空数据（三科对照实测：仅政治有此缺陷）。照数学/英语分支补全四行。
                t = re.sub(r"- \*\*目标分数\*\*：.*", f"- **目标分数**：`{plan.get('pol_target', '70+ 分')}` (摸底: {plan.get('pol_baseline', '40分')})", t)
                t = re.sub(r"- \*\*每日投入\*\*：.*", f"- **每日投入**：`{plan.get('pol_hours', 1.0)} 小时`", t)
                t = re.sub(r"- \*\*核心书目\*\*：.*", f"- **核心书目**：`{plan.get('pol_books')}`", t)
                t = re.sub(r"- \*\*核心薄弱点\*\*：.*", f"- **核心薄弱点**：`{plan.get('pol_weakness')}`", t)
        atomic_write_text(p_file, t)

    # 专业课
    pro_file = ws / "04-专业课" / "AGENTS.md"
    if not pro_file.exists():
        # [UT4 修复·PLANNER-2] 缺失时按通用协议骨架重建并告警，不再静默跳过
        # （UT4 三沙箱实测：骨架副本缺该文件 → 建档后永久缺失、doctor 必红）。
        # 文件存在时维持现行为：绝不覆盖考生自改内容。重建后的配置区行紧接
        # 被下方既有 re.sub 按 plan 回填（目标院校/科目名/白名单等）。
        try:
            atomic_write_text(pro_file, _PRO_AGENTS_SKELETON)
            print(colorize(
                "  [!] 04-专业课/AGENTS.md 缺失，已按通用协议骨架重建；"
                "如你曾自定义该文件内容，请手动回填。", C.YELLOW))
        except Exception as e:
            print(colorize(f"  [!] 04-专业课/AGENTS.md 重建失败: {e}", C.YELLOW))
    if pro_file.exists():
        t = pro_file.read_text(encoding="utf-8")
        t = re.sub(r"- \*\*目标院校\*\*：.*", f"- **目标院校**：`{plan.get('school', '目标院校')}`", t)
        t = re.sub(r"- \*\*专业代码与名称\*\*：.*", f"- **专业代码与名称**：`{plan.get('major', '报考专业')}`", t)
        disp_pro = f"{pro_n} 与 {pro2_n}" if pro2_n else pro_n
        t = re.sub(r"- \*\*专业课科目代码与名称\*\*：.*", f"- **专业课科目代码与名称**：`{disp_pro}`", t)
        t = re.sub(r"- \*\*满分与目标成绩\*\*：.*", f"- **满分与目标成绩**：`目标 {plan.get('pro_target', '120-130 分')}` (摸底: {plan.get('pro_baseline', '80分')})", t)
        books_str = plan.get('pro_books', '')
        if pro2_n and plan.get('pro2_books'):
            books_str += f" ｜ 专业课二: {plan.get('pro2_books')}"
        t = re.sub(r"- \*\*指定.*白名单.*", f"- **指定白名单资料**：`{books_str}`", t)
        atomic_write_text(pro_file, t)

def print_study_plan_summary(plan):
    """打印全彩考研总战役全景看板"""
    m_n = plan.get("math_name", "数学")
    e_n = plan.get("eng_name", "英语")
    pro_n = plan.get("pro_name", "专业课")
    m_h = float(plan.get("math_hours", 3.0))
    e_h = float(plan.get("eng_hours", 2.0))
    p_h = float(plan.get("pol_hours", 1.0))
    pro_h = float(plan.get("pro_hours", 2.5))
    math_disabled = (plan.get("math_key") == "none")

    summary_text = f"""
{C.GREEN}╭────────────────────────────────────────────────────────────────────────╮
│  🎉 恭喜！您的考研必考专属战役方案已全量设计并锁定完毕！              │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
{C.BOLD}【总战役基本盘】{C.RESET}
  • 目标院校 / 专业: {C.CYAN}{plan.get('school', '目标院校')} · {plan.get('major', '报考专业')}{C.RESET}
  • 初试目标日期:    {C.MAGENTA}{plan.get('exam_date') or _FALLBACK_EXAM_DATE}{C.RESET} (研考倒计时: {C.MAGENTA}{plan.get('days_left', 0)} 天{C.RESET})
  • 当前备考阶段:    {C.YELLOW}{plan.get('stage_name', STAGES['2'][0])}{C.RESET}
  • 私教辅导风格:    {C.GREEN}{plan.get('style_name', STYLES['1'][0])}{C.RESET}
  • 初试总分目标:    {C.RED}{C.BOLD}{plan.get('total_target', '370+ 分')}{C.RESET} (每日总精力预算: {C.CYAN}{plan.get('total_hours', 8.5)} 小时{C.RESET})

{C.BOLD}【{subject_count_label(plan)}提分矩阵与薄弱项雷达】{C.RESET}"""

    # [P1 修复·math_key=none 贯穿] 不考数学时跳过数学科目显示
    subject_index = 1
    if not math_disabled:
        summary_text += f"""
  {subject_index}. {C.BOLD}{m_n}{C.RESET}:
     - 目标分 / 摸底分: {C.GREEN}{plan.get('math_target', '110+ 分')}{C.RESET} / {C.DIM}{plan.get('math_baseline', '60分')}{C.RESET}  (每日投入: {plan.get('math_hours', 3.0)}h)
     - 痛点防线: {C.YELLOW}{plan.get('math_weakness', '导数中值定理、计算失误')}{C.RESET}
     - 白名单书目: {plan.get('math_books', '同济教材+基础讲义+真题')}"""
        subject_index += 1

    summary_text += f"""
  {subject_index}. {C.BOLD}{e_n}{C.RESET}:
     - 目标分 / 摸底分: {C.GREEN}{plan.get('eng_target', '65+ 分')}{C.RESET} / {C.DIM}{plan.get('eng_baseline', '50分')}{C.RESET}  (每日投入: {plan.get('eng_hours', 2.0)}h)
     - 痛点防线: {C.YELLOW}{plan.get('eng_weakness', '待诊断薄弱点、细节定位')}{C.RESET}
     - 白名单书目: {plan.get('eng_books', '黄皮书历年真题+必考词汇')}
  {subject_index + 1}. {C.BOLD}思想政治理论{C.RESET}:
     - 目标分 / 摸底分: {C.GREEN}{plan.get('pol_target', '70+ 分')}{C.RESET} / {C.DIM}{plan.get('pol_baseline', '40分')}{C.RESET}  (每日投入: {plan.get('pol_hours', 1.0)}h)
     - 痛点防线: {C.YELLOW}{plan.get('pol_weakness', '马原唯物辩证法、多选题漏选')}{C.RESET}
     - 白名单书目: {plan.get('pol_books', '核心考案+精选1000题+冲刺卷')}
  {subject_index + 2}. {C.BOLD}{pro_n}{C.RESET}:
     - 目标分 / 摸底分: {C.GREEN}{plan.get('pro_target', '120-130 分')}{C.RESET} / {C.DIM}{plan.get('pro_baseline', '80分')}{C.RESET}  (每日投入: {plan.get('pro_hours', 2.5)}h)
     - 痛点防线: {C.YELLOW}{plan.get('pro_weakness', '核心考点掌握、解答题推导步骤')}{C.RESET}
     - 白名单书目: {plan.get('pro_books', '官方教材+历年真题')}"""

    summary_text += f"""

{C.BOLD}【科学作息与防疲劳减压机制】{C.RESET}
  • 每周放风休整: {C.CYAN}{plan.get('rest_weekly', '每周日晚放松休整')}{C.RESET}
  • 每月模考复盘: {C.CYAN}{plan.get('rest_monthly', '每月最后一个周日全真模考')}{C.RESET}
{C.CYAN}╭── 📋 今日首日{subject_count_label(plan)}任务清单 (已全自动写入各科 _状态/今日任务.md) ───────╮{C.RESET}"""

    # [P1 修复·math_key=none 贯穿] 不考数学时不显示数学任务行
    if not math_disabled:
        summary_text += f"""
{C.CYAN}│{C.RESET}  • {C.BOLD}{m_n:<18}{C.RESET} ({int(m_h*60)}分钟) : 攻克【{plan.get('math_weakness','导数中值定理')}】定理与核心题型"""

    summary_text += f"""
{C.CYAN}│{C.RESET}  • {C.BOLD}{e_n:<18}{C.RESET} ({int(e_h*60)}分钟) : 拆解【{plan.get('eng_weakness','真题长难句')}】与阅读定位
{C.CYAN}│{C.RESET}  • {C.BOLD}思想政治理论       {C.RESET} ({int(p_h*60)}分钟) : 攻坚【{plan.get('pol_weakness','马原哲学与帽子词')}】框架梳理
{C.CYAN}│{C.RESET}  • {C.BOLD}{pro_n:<18}{C.RESET} ({int(pro_h*60)}分钟) : 突破【{plan.get('pro_weakness','核心考点推导')}】与解答规范
{C.CYAN}╰────────────────────────────────────────────────────────────────────────╯{C.RESET}

👉 {C.BOLD}下一步直接在终端输入指令开始学习：{C.RESET}"""

    # [P1 修复·math_key=none 贯穿] 不考数学时不显示数学报到指令
    if not math_disabled:
        summary_text += f"""
   - {C.GREEN}/math{C.RESET} 或 {C.GREEN}数学报到{C.RESET}  ➔ 启动数学私教今日精选真题训练"""

    summary_text += f"""
   - {C.GREEN}/eng{C.RESET}  或 {C.GREEN}英语报到{C.RESET}  ➔ 启动英语长难句与阅读精析
   - {C.GREEN}/pol{C.RESET}  或 {C.GREEN}政治报到{C.RESET}  ➔ 启动政治考点与帽子词自测
   - {C.GREEN}/pro{C.RESET}  或 {C.GREEN}专业课报到{C.RESET}➔ 启动专业课高频大题推导演练
   - {C.YELLOW}/today{C.RESET}                ➔ 随时查看今日{subject_count_label(plan)}任务与完成状态
   - {C.YELLOW}/plan{C.RESET}                 ➔ 随时动态调整备考方案与作息

✨ 所有规划文件已在各科目目录下生成完毕！
"""

    print(summary_text)

def _guard_config_write(op: str, target) -> None:
    """对 ``ky_config.json`` 的写入前，统一过一遍既有的写权限闸门 ``ky_io.guard_write``。

    [R2-D1 修复] **为什么选这一层**：``record_daily_completion`` 是「写
    ky_config.json」这件事的唯一归属点（调用方有 SessionEnd 钩子、test_ky_suite
    等），把闸门放在函数内部，任何现在与未来的调用方都自动受保护，不必在每个
    调用点各写一遍 mode 判断 —— 本仓库已经因为「同一件事两处实现、只修一处」
    被坑过两次（P25 隐私策略、R2-C1 打包路径）。

    **为什么不能只靠本模块导入的 ``atomic_write_text``**：本项目存在双导入 ——
    ``ky_io`` 与 ``tools.ky_io`` 是两个独立模块对象，各自持有一份
    ``_READ_ONLY_MODE``。``tools/cli/dispatch.py:189`` 用 ``from tools import
    ky_io`` 设标志，而本模块历史上 ``from ky_io import atomic_write_text`` 取的
    是另一个别名，两者不互通，于是 safe 模式下本函数的写盘闸门形同虚设。
    这里**不重写 mode 判断**，只保证任一别名报告只读都会被拦住 —— 只读判定的
    单一事实源仍然是 ky_io 自己的全局标志。

    只读时抛 ``PermissionDeniedError``（与 ky_io.guard_write 一致），
    调用方必须显式处理，不得静默吞掉。
    """
    for _name in ("tools.ky_io", "ky_io"):
        _mod = sys.modules.get(_name)
        _fn = getattr(_mod, "guard_write", None) if _mod is not None else None
        if callable(_fn):
            _fn(op, target)

def record_daily_completion(rate: float, total: int = 0, completed: int = 0, date_str: str = None):
    """记录指定日期的任务完成率到 ky_config.json"""
    d_str = date_str or datetime.now().strftime("%Y-%m-%d")
    cfg_path = ROOT / "ky_config.json"
    if not cfg_path.exists():
        return
    # [R2-D1] 严格只读模式下拒绝写盘，异常冒泡给调用方显式处理（不得静默跳过）
    _guard_config_write("记录今日完成度", cfg_path)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}

    hist = cfg.get("completion_history", {})
    hist[d_str] = {
        "rate": float(rate),
        "total": int(total),
        "completed": int(completed),
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    cfg["completion_history"] = hist
    atomic_write_text(cfg_path, json.dumps(cfg, ensure_ascii=False, indent=2))

def _day_has_learning_action(record) -> bool:
    """[P1 修复·2026-10-08 K4 假疲劳警报] 该日记录是否含真实学习动作。

    只把「显式记录 completed<=0」的日子判为无学习动作（打开会话但一道题
    都没勾选，或全部任务未完成）并排除在疲劳判定之外 —— 这类 0% 记录是
    空转快照，不是「任务全线未完成」。历史记录缺 completed 字段时无法
    判定，保守视为有动作（保持既有行为，兼容只写 rate 的旧数据）。
    """
    if not isinstance(record, dict):
        return True  # 结构未知：不排除（保守）
    if "completed" not in record:
        return True
    try:
        return int(record.get("completed") or 0) >= 1
    except (TypeError, ValueError):
        return True


def check_fatigue_alert(cfg: dict = None) -> dict:
    """
    检查是否连续 2 天今日任务完成率低于 60%
    兑现 AGENTS.md 减负保障机制与豆包/阿福防疲劳设计

    [P1 修复·2026-10-08 K4 假疲劳警报] 判定只在「有真实学习动作」的日子上进行：
    只开会话、一道题都没勾选（completed=0）的日子整体排除 —— 它们既不
    触发警报，也不参与「连续」判定。修复前连续 2 天 0% 即弹防疲劳减负
    面板（建议 /relieve 降 25% 任务量），空转会话被误判为过度疲劳。
    """
    if cfg is None:
        cfg_path = ROOT / "ky_config.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        else:
            cfg = {}

    hist = cfg.get("completion_history", {})
    # 只保留有真实学习动作的日期（见 _day_has_learning_action 说明）
    active_dates = [d for d in sorted(hist.keys())
                    if _day_has_learning_action(hist.get(d))]
    if len(active_dates) < 2:
        return {
            "alert": False,
            "consecutive_low_days": 0,
            "recent_rates": [hist[d].get("rate", 0.0) for d in active_dates],
            "message": "暂无连续低完成率记录，复习节奏保持良好！"
        }

    last_two_dates = active_dates[-2:]
    # 校验日期连续性：两日期间隔必须严格为 1 天，跨周或非连续日期不应判定为连续疲劳
    try:
        d1 = datetime.strptime(last_two_dates[0], "%Y-%m-%d").date()
        d2 = datetime.strptime(last_two_dates[1], "%Y-%m-%d").date()
        is_consecutive = ((d2 - d1).days == 1)
    except Exception:
        # 解析失败时必须保守判「非连续」。原实现默认 True，与上一行注释
        # 「跨周或非连续日期不应判定为连续疲劳」正好相反：一条非法日期就会
        # 把两个不相邻的日子误判为连续，叠加完成率 <60% 后误触发「防疲劳减负」，
        # 错误下调学员 25% 任务量。
        is_consecutive = False

    rates = [hist[d].get("rate", 0.0) for d in last_two_dates]
    all_below_60 = all(r < 60.0 for r in rates) and is_consecutive

    if all_below_60:
        avg_r = round(sum(rates) / len(rates), 1)
        msg = (
            f"⚠️ 【防疲劳保障提醒】：检测到您最近连续 2 天 ({last_two_dates[0]}, {last_two_dates[1]}) "
            f"任务完成率均低于 60% (均值 {avg_r}%)！\n"
            f"💡 科学备考铁律：过度疲劳会导致学习吸收率断崖式下滑。\n"
            f"👉 建议输入 /relieve 启动【智能减负模式】：自动下调任务量 25%，并切换为温和启发辅导风格。"
        )
        return {
            "alert": True,
            "consecutive_low_days": 2,
            "recent_dates": last_two_dates,
            "recent_rates": rates,
            "avg_rate": avg_r,
            "message": msg
        }

    return {
        "alert": False,
        "consecutive_low_days": 0,
        "recent_rates": rates,
        "message": "复习节奏健康稳健！"
    }

def apply_relief_mode(scale: float = 0.75, keep_style: bool = False) -> dict:
    """
    一键启动减负模式：下调每日各科复习投入时间，并将辅导风格切换为「温和启发·减负鼓励型」
    具有幂等保护，防止多次调用累积下调时间。

    :param keep_style: 为 True 时只降时长、不碰辅导风格（P2-7：`ky relieve --keep-style`）。
    """
    cfg_path = ROOT / "ky_config.json"
    if not cfg_path.exists():
        return {"success": False, "message": "未找到配置文件"}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "message": str(e)}

    plan = cfg.get("study_plan", {})
    relief_style = "温和启发·减负鼓励型 (Encouraging Mentor)"

    # [P2-7 修复·取值顺序] 原风格必须在覆盖前读取：旧实现先写 plan["style_name"]
    # 再读它，导致 style_before_relief 记的永远是新风格，恢复入口形同虚设。
    prev_style = cfg.get("coaching_style") or plan.get("style_name") or "(未记录)"

    # 幂等检查：若减负模式已处于激活状态，避免重复按 scale 比例递归缩小时间
    if cfg.get("relief_mode_active"):
        curr_hours = plan.get("total_hours", 8.5)
        return {
            "success": True,
            "old_hours": plan.get("baseline_total_hours", curr_hours),
            "new_hours": curr_hours,
            "style": cfg.get("coaching_style", plan.get("style_name", relief_style)),
            "message": f"减负模式已处于激活状态（每日投入 {curr_hours}h），无需重复启动。"
        }

    old_hours = plan.get("total_hours", 8.5)
    plan["baseline_total_hours"] = old_hours
    plan["baseline_math_hours"] = plan.get("math_hours", 3.0)
    plan["baseline_eng_hours"] = plan.get("eng_hours", 2.0)
    plan["baseline_pol_hours"] = plan.get("pol_hours", 1.0)
    plan["baseline_pro_hours"] = plan.get("pro_hours", 2.5)

    new_hours = round(old_hours * scale, 1)

    plan["total_hours"] = new_hours
    plan["math_hours"] = round(plan.get("baseline_math_hours", 3.0) * scale, 1)
    plan["eng_hours"] = round(plan.get("baseline_eng_hours", 2.0) * scale, 1)
    plan["pol_hours"] = round(plan.get("baseline_pol_hours", 1.0) * scale, 1)
    plan["pro_hours"] = round(plan.get("baseline_pro_hours", 2.5) * scale, 1)

    if not keep_style:
        plan["style_name"] = relief_style
    # [P3 修复·D12] 减负模式会连带改写辅导风格（coaching_style / AGENTS.md）。
    # 旧实现静默覆盖，考生在 ky status 里发现风格被改却不知何时被改、如何改回。
    # 现在：① 留存原风格供回滚；② 返回值显式声明「风格已被修改」及原风格名称；
    # ③ --keep-style 只降时长不碰风格；④ restore_relief_mode() 可一键恢复。
    # [P2-7 修复] prev_style 已在覆盖前读取（见函数顶部），此处不再重读。
    style_changed = (str(prev_style) != relief_style) and not keep_style
    if style_changed:
        # 仅记录首次被减负覆盖前的风格，避免二次覆盖把「原风格」冲成减负风格
        cfg.setdefault("style_before_relief", prev_style)
        cfg["coaching_style"] = relief_style
    cfg["study_plan"] = plan
    cfg["relief_mode_active"] = True
    atomic_write_text(cfg_path, json.dumps(cfg, ensure_ascii=False, indent=2))

    agents_file = ROOT / "AGENTS.md"
    if agents_file.exists():
        content = agents_file.read_text(encoding="utf-8", errors="ignore")
        if style_changed:
            content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*", f"- **当前激活辅导风格**：`{relief_style}`", content)
        content = re.sub(r"每日投入 `[\d\.]+ 小时`", f"每日投入 `{new_hours} 小时`", content)
        atomic_write_text(agents_file, content)

    if keep_style:
        shown_style = plan.get("style_name", prev_style)
        style_notice = f"（辅导风格保持「{shown_style}」不变，未切换）"
    else:
        style_notice = (
            f"（已连带将辅导风格由「{prev_style}」改为「{relief_style}」，"
            f"改回请运行 ky style，或 ky relieve --off 一键恢复时长+风格）"
            if style_changed else
            f"（辅导风格已是「{relief_style}」，未发生变更）"
        )
        shown_style = relief_style
    return {
        "success": True,
        "old_hours": old_hours,
        "new_hours": new_hours,
        "style": shown_style,
        "previous_style": prev_style,
        "style_changed": style_changed,
        "message": (f"减负模式已成功启动！每日总投入已调整为 {new_hours}h (原 {old_hours}h)。"
                    f"私教辅导风格：{shown_style} {style_notice} 保持好心情，轻装上阵！")
    }


def restore_relief_mode() -> dict:
    """[P2-7 修复·撤销入口] 一键退出减负模式：按 baseline_* 恢复各科时长，
    按 style_before_relief 恢复辅导风格（含 AGENTS.md），清除激活标记。"""
    cfg_path = ROOT / "ky_config.json"
    if not cfg_path.exists():
        return {"success": False, "message": "未找到配置文件"}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "message": str(e)}
    if not cfg.get("relief_mode_active"):
        return {"success": False, "message": "减负模式未激活，无需恢复。"}

    plan = cfg.get("study_plan", {})
    plan["total_hours"] = plan.get("baseline_total_hours", plan.get("total_hours", 8.5))
    plan["math_hours"] = plan.get("baseline_math_hours", plan.get("math_hours", 3.0))
    plan["eng_hours"] = plan.get("baseline_eng_hours", plan.get("eng_hours", 2.0))
    plan["pol_hours"] = plan.get("baseline_pol_hours", plan.get("pol_hours", 1.0))
    plan["pro_hours"] = plan.get("baseline_pro_hours", plan.get("pro_hours", 2.5))

    restored_style = cfg.pop("style_before_relief", None)
    if restored_style:
        plan["style_name"] = restored_style
        cfg["coaching_style"] = restored_style
    cfg["study_plan"] = plan
    cfg["relief_mode_active"] = False
    for k in ("baseline_total_hours", "baseline_math_hours", "baseline_eng_hours",
              "baseline_pol_hours", "baseline_pro_hours"):
        plan.pop(k, None)
    atomic_write_text(cfg_path, json.dumps(cfg, ensure_ascii=False, indent=2))

    agents_file = ROOT / "AGENTS.md"
    if agents_file.exists():
        content = agents_file.read_text(encoding="utf-8", errors="ignore")
        # [keep-style 恢复修复] keep-style 减负不记录 style_before_relief，
        # restored_style 为 None 时也要把预算行从减负值恢复；风格行按
        # 「记录的原风格 → 当前配置 → plan」顺序兜底。
        style_to_write = restored_style or cfg.get("coaching_style") or plan.get("style_name")
        if style_to_write:
            content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*",
                             f"- **当前激活辅导风格**：`{style_to_write}`", content)
        content = re.sub(r"每日投入 `[\d\.]+ 小时`",
                         f"每日投入 `{plan['total_hours']} 小时`", content)
        atomic_write_text(agents_file, content)
    return {
        "success": True,
        "new_hours": plan["total_hours"],
        "style": plan.get("style_name", ""),
        "message": (f"已退出减负模式，每日投入恢复为 {plan['total_hours']}h"
                    + (f"，辅导风格恢复为「{restored_style}」。" if restored_style else "。"))
    }

if __name__ == "__main__":
    run_study_plan_wizard(interactive=True)
