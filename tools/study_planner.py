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
import json
from pathlib import Path
from datetime import datetime, date

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

# [根因修复·日期硬编码] 初试日期统一由 exam_calendar 提供，替代本文件内
# `f"{year}-12-19"` 与 `plan.get('exam_date', '2026-12-19' / '2027-12-19')` 等多处硬编码
try:
    import exam_calendar  # noqa: E402
except ImportError:  # pragma: no cover
    from tools import exam_calendar  # type: ignore  # noqa: E402

# plan 中缺失 exam_date 时的兜底初试日：按日历推算，绝不写死年份
_FALLBACK_EXAM_DATE = exam_calendar.resolve_exam_date({})[0].isoformat()

ROOT = Path(__file__).resolve().parent.parent
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
    返回: 文件名列表（过滤 .gitkeep, .gitignore, readme 等占位文件）
    """
    mat_dir = ROOT / subject_rel_path / "参考资料"
    if not mat_dir.exists():
        return []
    valid_exts = {".pdf", ".doc", ".docx", ".epub", ".txt", ".md", ".png", ".jpg", ".jpeg"}
    files = []
    try:
        for p in mat_dir.iterdir():
            if p.name.startswith(".") or p.name.lower().startswith("readme"):
                continue
            if p.is_file() and (p.suffix.lower() in valid_exts or not p.suffix):
                files.append(p.name)
            elif p.is_dir():
                files.append(f"{p.name}/")
    except Exception:
        pass
    return sorted(files)

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


def _material_phrase(raw, verb: str) -> str:
    """今日任务「题源出处」短语：有真实白名单时引用，无资料时不引用白名单。

    [P17 修复·无资料却派白名单刷题] 白名单为空时其默认值是一句
    「暂未放置实体资料（私教严格按【…】官方考纲出题，严禁虚构书目）」的说明，
    旧实现把整句塞进「精做白名单【…】20 道核心选择题自测」，读起来自相矛盾，
    还等于让考生去刷一份不存在的资料。无资料时改为「按官方考纲{verb}」。
    """
    s = re.sub(r"^\[本地资料库已就绪\]:\s*", "", str(raw or "")).strip()
    if not s or s == "不考数学" or s.startswith("暂未放置实体资料"):
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
│  (7 大核心维度：时间 · 考纲 · 资料白名单 · 学情摸底 · 每日投入 · 作息)  │
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
        m_choice = input("  请选择数学科目 (math1/math2/math3/396/none) [默认: math2]: ").strip().lower() or "math2"

        print("\n  --- 请选择您的英语科目方案 ---")
        for k, info in syllabus_manager.ENGLISH_SYLLABI.items():
            e_desc = info.get('features') or info.get('scope') or ''
            print(f"    [{k}] {info['name']} - {e_desc}")
        e_choice = input("  请选择英语科目 (eng1/eng2) [默认: eng2]: ").strip().lower() or "eng2"

        print("\n  --- 请选择您的专业课方案 ---")
        print("    [1] 全国统考 408 计算机学科专业基础")
        print("    [2] 全国统考 199 管理类综合能力")
        print("    [3] 院校自命题专业课")
        p_sel = input("  请选择专业课类型 (1/2/3) [默认: 1]: ").strip() or "1"
        if p_sel == "1":
            pro_type = "408"
            pro_name = "408 计算机学科专业基础"
        elif p_sel == "2":
            pro_type = "199"
            pro_name = "199 管理类综合能力"
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
    plan["pro_baseline"] = pro_baseline
    plan["pro_weakness"] = pro_weakness
    print(colorize("  [√] 学情摸底档案建立完毕，痛点已注入专属私教薄弱项雷达！\n", C.GREEN))

    # ── 维度 4: 手头备考资料白名单 (真实扫描与核验，杜绝 AI 凭空捏造) ──
    print(colorize("【维度 4/7 · 📚 手头已有备考资料白名单真实核验】", C.BOLD))
    print(colorize("  ⚠️ 【防虚构铁律】：私教仅能从您指定的真实白名单出题，严禁捏造未拥有的书籍！", C.YELLOW))

    local_math_files = scan_local_materials("01-数学")
    local_eng_files = scan_local_materials("02-英语")
    local_pol_files = scan_local_materials("03-思想政治理论")
    local_pro_files = scan_local_materials("04-专业课")

    def make_default_book_label(files, subj_name):
        if files:
            return f"[本地资料库已就绪]: {', '.join(files)}"
        return f"暂未放置实体资料（私教严格按【{subj_name}】官方考纲出题，严禁虚构书目）"

    def_m = make_default_book_label(local_math_files, m_title)
    def_e = make_default_book_label(local_eng_files, e_title)
    def_p = make_default_book_label(local_pol_files, "思想政治理论")
    def_pro = make_default_book_label(local_pro_files, pro_name)

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
    p_lower = pro_name.lower()
    if any(k in p_lower for k in ("408", "计算", "软件")):
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

    api_key = cfg.get("api_key", "").strip()

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
        print(colorize(f"  [3/3] 正在基于白名单真实题源与官方考纲构建首日（Day 1）四科针对性任务清单...\n", C.CYAN))
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
3. 【今日（Day 1）四科针对性任务清单】：精确到具体攻坚知识点、练习题型、预计分钟数与自测动作。
要求：逻辑严密、直击痛点，严禁空泛套话！"""

        try:
            import urllib.request
            from ky_cli import normalize_openai_url
            raw_base_url = cfg.get("base_url", "https://api.deepseek.com/v1")
            url = normalize_openai_url(raw_base_url, "chat/completions")
            model = cfg.get("model", "deepseek-chat")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Kaoyan-Study-Chain/1.0",
                "Accept": "application/json",
                "Connection": "close"
            }
            messages = [
                {"role": "system", "content": "你是一位深谙中国研究生入学考试命题规律、提分导向的考研全科专属总教练。请根据学员给出的 7 大维度个性化学情，输出严密、求真务实、直击薄弱项痛点的深度备考战略与任务清单。严禁虚构书籍或空泛套话。"},
                {"role": "user", "content": prompt_text}
            ]
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.4,
                "stream": True
            }
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            collected = []
            with urllib.request.urlopen(req, timeout=60) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    d_str = line[5:].strip()
                    if d_str == "[DONE]":
                        break
                    try:
                        delta = json.loads(d_str)
                        token = delta.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if token:
                            sys.stdout.write(token)
                            sys.stdout.flush()
                            collected.append(token)
                    except Exception:
                        pass
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
    content = re.sub(r"- \*\*初试日期\*\*：.*", f"- **初试日期**：`{plan.get('exam_date') or _FALLBACK_EXAM_DATE}` (倒计时约 {plan.get('days_left', 0)} 天)", content)
    
    # 注入备考阶段
    if "- **当前备考阶段**：" in content:
        content = re.sub(r"- \*\*当前备考阶段\*\*：.*", f"- **当前备考阶段**：`{plan.get('stage_name', STAGES['2'][0])}`", content)
    else:
        content = re.sub(r"(- \*\*初试日期\*\*：.*?\n)", r"\1" + f"- **当前备考阶段**：`{plan.get('stage_name', STAGES['2'][0])}`\n", content)

    content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*", f"- **当前激活辅导风格**：`{plan.get('style_name', STYLES['1'][0])}`", content)

    # 矩阵表格更新
    m_row = f"| **科目一：{m_name}** | {plan.get('math_baseline','[摸底]')} | **{plan.get('math_target','110+ 分')}** | {plan.get('math_hours',2.5)} 小时 | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |"
    e_row = f"| **科目二：{e_name}** | {plan.get('eng_baseline','[摸底]')} | **{plan.get('eng_target','65+ 分')}** | {plan.get('eng_hours',2.0)} 小时 | 搭积木拆解长难句，定位阅读选项逻辑，固化作文功能句模板 |"
    p_row = f"| **科目三：思想政治理论** | {plan.get('pol_baseline','[摸底]')} | **{plan.get('pol_target','70+ 分')}** | {plan.get('pol_hours',1.0)} 小时 | 单选+多选得分盘（38~42分），帽子词秒杀，后期背诵闭环 |"
    pro_row = f"| **科目四：{pro_name}** | {plan.get('pro_baseline','[摸底]')} | **{plan.get('pro_target','120-130 分')}** | {plan.get('pro_hours',2.5)} 小时 | 权威教材体系+历年真题深度解剖，白名单题源抽题门禁 |"
    tot_row = f"| **合计** | [摸底总分] | **{plan.get('total_target','370+ 分')}** | {plan.get('total_hours',8.5)} 小时 | **结构性提分，稳拿基本盘，拒绝偏难怪题** |"

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
  - 专业课薄弱点: `{plan.get('pro_weakness', '核心算法设计与证明步骤')}`
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
        watcher = AdmissionWatcher()
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
    build_py = ROOT / "05-考研看板" / "build.py"
    if build_py.exists():
        try:
            import subprocess
            subprocess.run([sys.executable, str(build_py)], cwd=str(ROOT / "05-考研看板"), capture_output=True)
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
| **合计** | [摸底总分] | **{plan.get('total_target', '215+ 分')}** | {plan.get('total_hours', 6.0)} 小时 | **199联考总分300分，初试不考政治与统考数学** |"""
        books_rows = f"""- **199管综权威资料**：`{plan.get('pro_books')}`\n- **英语权威资料**：`{plan.get('eng_books')}`"""
        budget_row = f"- **每日精力预算**：{plan.get('total_hours', 6.0)} 小时 (199管综 {pro_h}h / 英语 {e_h}h)"
    elif is_mode_b:
        table_rows = f"""| **科目一：不考数学** | 不考数学 | **不考数学** | 0.0 小时 (0m) | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |
| **{e_n}** | {plan.get('eng_baseline', '50分')} | **{plan.get('eng_target', '65+ 分')}** | {e_h} 小时 ({int(e_h*60)}m) | 攻克【{plan.get('eng_weakness', '长难句主干拆解')}】，搭积木拆解，定位阅读选项逻辑 |
| **思想政治理论** | {plan.get('pol_baseline', '50分')} | **{plan.get('pol_target', '70+ 分')}** | {p_h} 小时 ({int(p_h*60)}m) | 攻克【{plan.get('pol_weakness', '马原多选题')}】，单选拿满，帽子词秒杀 |
| **{pro_n}** | {plan.get('pro_baseline', '80分')} | **{plan.get('pro_target', '120-130 分')}** | {pro_h} 小时 ({int(pro_h*60)}m) | 攻克【{plan.get('pro_weakness', '专业课一核心重点')}】，权威教材体系+真题深度解剖 |
| **{pro2_n or '专业课二'}** | {plan.get('pro2_baseline', '80分')} | **{plan.get('pro2_target', '120-130 分')}** | {pro2_h} 小时 ({int(pro2_h*60)}m) | 攻克【{plan.get('pro2_weakness', '专业课二论述框架')}】，第二门自命题考纲深化推导与背诵闭环 |
| **合计** | [摸底总分] | **{plan.get('total_target', '375+ 分')}** | {plan.get('total_hours', 7.0)} 小时 | **稳扎稳打拿牢核心得分盘，拒绝偏难怪题** |"""
        books_rows = f"""- **英语权威资料**：`{plan.get('eng_books')}`\n- **政治权威资料**：`{plan.get('pol_books')}`\n- **专业课一权威资料**：`{plan.get('pro_books')}`\n- **专业课二权威资料**：`{plan.get('pro2_books') or plan.get('pro_books')}`"""
        budget_row = f"- **每日精力预算**：{plan.get('total_hours', 7.0)} 小时 (英语 {e_h}h / 政治 {p_h}h / 专业课一 {pro_h}h / 专业课二 {pro2_h}h)"
    else:
        m_part = f"| **{m_n}** | {plan.get('math_baseline', '60分')} | **{plan.get('math_target', '110+ 分')}** | {m_h} 小时 ({int(m_h*60)}m) | 重点攻克【{plan.get('math_weakness', '计算失误')}】，规避失误，步骤规范化 |\n" if not math_disabled else ""
        table_rows = f"""{m_part}| **{e_n}** | {plan.get('eng_baseline', '50分')} | **{plan.get('eng_target', '65+ 分')}** | {e_h} 小时 ({int(e_h*60)}m) | 攻克【{plan.get('eng_weakness', '长难句主干拆解')}】，搭积木拆解，定位阅读选项逻辑 |
| **思想政治理论** | {plan.get('pol_baseline', '40分')} | **{plan.get('pol_target', '70+ 分')}** | {p_h} 小时 ({int(p_h*60)}m) | 攻克【{plan.get('pol_weakness', '马原多选题')}】，单选拿满，帽子词秒杀 |
| **{pro_n}** | {plan.get('pro_baseline', '80分')} | **{plan.get('pro_target', '120-130 分')}** | {pro_h} 小时 ({int(pro_h*60)}m) | 攻克【{plan.get('pro_weakness', '核心算法设计')}】，权威教材体系+真题深度解剖 |
| **合计** | [摸底总分] | **{plan.get('total_target', '370+ 分')}** | {plan.get('total_hours', 8.5)} 小时 | **稳扎稳打拿牢核心得分盘，拒绝偏难怪题** |"""
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
- **专属薄弱项攻坚**：`{plan.get('pro_weakness', '核心算法设计与证明步骤')}`

## 一、阶段攻坚路线 (当前处于: {stage_name})
1. **基础构建期**：通读官方指定教材，掌握核心概念定义与底层原理；
2. **专题突破期**：深挖高频大题与核心算法，规范推导与代码书写采分点；
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
    pro_task_desc = "聚焦专业课官方考纲核心知识体系，完成首日题型规范度摸底" if (not pro_w or '待首次自测' in pro_w or '从零' in pro_w) else f"聚焦专业课考纲与【{pro_w}】推导"
    pro_task_file = ws / "04-专业课" / "_状态" / "今日任务.md"
    pro_task_file.parent.mkdir(parents=True, exist_ok=True)
    pro_label = "专业课一" if is_mode_b else "专业课"
    pro_tpl = f"""# 今日{pro_label}任务 ({today_str})

> 研考倒计时：{days_left} 天 ｜ 当前阶段：{stage_name} ｜ 今日目标用时：{int(pro_h*60)} 分钟

| 模块 | 任务内容 | 预计用时 | 完成状态 |
|---|---|---|---|
| 核心知识点 | {pro_task_desc} | {int(pro_h*60*0.3)} 分钟 | [ ] |
| 习题精练 | {_material_phrase(plan.get('pro_books'), '选取')}经典大题 2~3 道动笔完整书写 | {int(pro_h*60*0.5)} 分钟 | [ ] |
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


def update_subject_agents(plan, workspace_root=None):
    """将真实白名单与薄弱项同步写入 01~04 各科专属 AGENTS.md (杜绝虚构书目)"""
    ws = Path(workspace_root) if workspace_root else ROOT
    exam_mode = plan.get("exam_mode")
    pro_n = plan.get("pro_name", "专业课")
    is_mode_c = exam_mode in ("mode_c", "mgmt_199") or plan.get("pol_disabled") or ("199" in str(pro_n))
    pro2_n = plan.get("pro2_name", "").strip()
    is_mode_b = exam_mode in ("mode_b", "no_math_dual_pro") or bool(pro2_n)
    math_disabled = is_mode_b or is_mode_c or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"} or plan.get("math_name") == "不考数学"

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
                t = re.sub(r"- \*\*核心书目\*\*：.*", f"- **核心书目**：`{plan.get('pol_books')}`", t)
                t = re.sub(r"- \*\*核心薄弱点\*\*：.*", f"- **核心薄弱点**：`{plan.get('pol_weakness')}`", t)
        atomic_write_text(p_file, t)

    # 专业课
    pro_file = ws / "04-专业课" / "AGENTS.md"
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

{C.BOLD}【四科提分矩阵与薄弱项雷达】{C.RESET}"""

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
     - 痛点防线: {C.YELLOW}{plan.get('pro_weakness', '核心算法设计、解答题推导步骤')}{C.RESET}
     - 白名单书目: {plan.get('pro_books', '官方教材+历年真题')}"""

    summary_text += f"""

{C.BOLD}【科学作息与防疲劳减压机制】{C.RESET}
  • 每周放风休整: {C.CYAN}{plan.get('rest_weekly', '每周日晚放松休整')}{C.RESET}
  • 每月模考复盘: {C.CYAN}{plan.get('rest_monthly', '每月最后一个周日全真模考')}{C.RESET}
{C.CYAN}╭── 📋 今日首日四科任务清单 (已全自动写入各科 _状态/今日任务.md) ───────╮{C.RESET}"""

    # [P1 修复·math_key=none 贯穿] 不考数学时不显示数学任务行
    if not math_disabled:
        summary_text += f"""
{C.CYAN}│{C.RESET}  • {C.BOLD}{m_n:<18}{C.RESET} ({int(m_h*60)}分钟) : 攻克【{plan.get('math_weakness','导数中值定理')}】定理与核心题型"""

    summary_text += f"""
{C.CYAN}│{C.RESET}  • {C.BOLD}{e_n:<18}{C.RESET} ({int(e_h*60)}分钟) : 拆解【{plan.get('eng_weakness','真题长难句')}】与阅读定位
{C.CYAN}│{C.RESET}  • {C.BOLD}思想政治理论       {C.RESET} ({int(p_h*60)}分钟) : 攻坚【{plan.get('pol_weakness','马原哲学与帽子词')}】框架梳理
{C.CYAN}│{C.RESET}  • {C.BOLD}{pro_n:<18}{C.RESET} ({int(pro_h*60)}分钟) : 突破【{plan.get('pro_weakness','核心算法推导')}】与解答规范
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
   - {C.YELLOW}/today{C.RESET}                ➔ 随时查看今日四科任务与完成状态
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

def check_fatigue_alert(cfg: dict = None) -> dict:
    """
    检查是否连续 2 天今日任务完成率低于 60%
    兑现 AGENTS.md 减负保障机制与豆包/阿福防疲劳设计
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
    sorted_dates = sorted(hist.keys())
    if len(sorted_dates) < 2:
        return {
            "alert": False,
            "consecutive_low_days": 0,
            "recent_rates": [hist[d].get("rate", 0.0) for d in sorted_dates],
            "message": "暂无连续低完成率记录，复习节奏保持良好！"
        }

    last_two_dates = sorted_dates[-2:]
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
    if agents_file.exists() and restored_style:
        content = agents_file.read_text(encoding="utf-8", errors="ignore")
        content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*",
                         f"- **当前激活辅导风格**：`{restored_style}`", content)
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
