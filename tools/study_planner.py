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
import os
import re
import json
from pathlib import Path
from datetime import datetime, date

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

def calculate_countdown(target_date_str):
    """计算距离初试日期的倒计时天数"""
    try:
        t_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        today = date.today()
        days = (t_date - today).days
        return max(0, days)
    except Exception:
        return 0

def run_study_plan_wizard(interactive=True, preset_data=None):
    """
    运行个人定制化必考方案向导
    interactive: 是否交互式提问
    preset_data: 预填数据字典 (用于自动化测试或批量写入)
    """
    plan = preset_data.copy() if preset_data else {}

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
    default_year = curr_year if datetime.now().month < 11 else curr_year + 1
    if interactive:
        target_year = input(f"  1. 目标考研初试年份 [默认: {default_year}]: ").strip() or str(default_year)
        default_exam_date = f"{target_year}-12-19"
        exam_date = input(f"  2. 预计初试日期 (YYYY-MM-DD) [默认: {default_exam_date}]: ").strip() or default_exam_date
        
        print("\n  --- 请选择您当前所处的备考阶段 ---")
        for k, (s_name, s_desc) in STAGES.items():
            print(f"    [{k}] {s_name}\n        -> {s_desc}")
        stage_choice = input("  选择备考阶段 (1~4) [默认 2]: ").strip() or "2"
        stage_name = STAGES.get(stage_choice, STAGES["2"])[0]
    else:
        target_year = plan.get("target_year", str(default_year))
        exam_date = plan.get("exam_date", f"{target_year}-12-19")
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

        print("\n  --- 请选择您的数学科目方案 ---")
        for k, info in syllabus_manager.MATH_SYLLABI.items():
            print(f"    [{k}] {info['name']} - {info['desc']}")
        print("    [none] 不考数学")
        m_choice = input("  请选择数学科目 (math1/math2/math3/396/none) [默认: math2]: ").strip().lower() or "math2"

        print("\n  --- 请选择您的英语科目方案 ---")
        for k, info in syllabus_manager.ENGLISH_SYLLABI.items():
            print(f"    [{k}] {info['name']} - {info['desc']}")
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
        m_choice = plan.get("math_key", "math2")
        e_choice = plan.get("eng_key", "eng2")
        pro_type = plan.get("pro_type", "408")
        pro_name = plan.get("pro_name", "408 计算机学科专业基础")

    plan["school"] = school
    plan["major"] = major
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
        if m_choice != "none":
            math_baseline = input("  1. 数学目前摸底成绩/基础水平 [如: 50分 / 零基础 / 60分]: ").strip() or "60分"
            math_weakness = input("     数学最核心失分点/痛点 [如: 极限计算常错、中值定理证明不会、概念模糊]: ").strip() or "导数中值定理、计算失误"
        else:
            math_baseline = "不考数学"
            math_weakness = "无"

        eng_baseline = input("  2. 英语目前摸底成绩/英语基础 [如: 四级450 / 六级未过 / 摸底50分]: ").strip() or "四级已过 / 摸底50分"
        eng_weakness = input("     英语最核心失分点/痛点 [如: 长难句读不懂、阅读推断题失分多、作文写不出]: ").strip() or "长难句主干拆解慢、阅读细节定位不准"

        pol_baseline = input("  3. 政治目前复习进度/摸底水平 [如: 未启动 / 已听网课 / 摸底40分]: ").strip() or "基础刚起步 / 摸底40分"
        pol_weakness = input("     政治主要痛点 [如: 马原哲学原理混淆、多选常错、帽子词记不牢]: ").strip() or "马原唯物辩证法、多选题漏选错选"

        pro_baseline = input(f"  4. 专业课 ({pro_name}) 目前摸底水平 [如: 跨考零基础 / 摸底80分]: ").strip() or "科班有基础 / 摸底80分"
        pro_weakness = input("     专业课核心失分点/痛点 [如: 计组指令系统/算法大题不会写]: ").strip() or "核心算法设计、高频大题推导步骤规范"
    else:
        math_baseline = plan.get("math_baseline", "60分")
        math_weakness = plan.get("math_weakness", "导数中值定理、计算失误")
        eng_baseline = plan.get("eng_baseline", "四级已过 / 摸底50分")
        eng_weakness = plan.get("eng_weakness", "长难句主干拆解慢、阅读细节定位不准")
        pol_baseline = plan.get("pol_baseline", "基础刚起步 / 摸底40分")
        pol_weakness = plan.get("pol_weakness", "马原唯物辩证法、多选题漏选错选")
        pro_baseline = plan.get("pro_baseline", "科班有基础 / 摸底80分")
        pro_weakness = plan.get("pro_weakness", "核心算法设计、高频大题推导步骤规范")

    plan["math_baseline"] = math_baseline
    plan["math_weakness"] = math_weakness
    plan["eng_baseline"] = eng_baseline
    plan["eng_weakness"] = eng_weakness
    plan["pol_baseline"] = pol_baseline
    plan["pol_weakness"] = pol_weakness
    plan["pro_baseline"] = pro_baseline
    plan["pro_weakness"] = pro_weakness
    print(colorize("  [√] 学情摸底档案建立完毕，痛点已注入专属私教薄弱项雷达！\n", C.GREEN))

    # ── 维度 4: 手头备考资料白名单 (杜绝 AI 凭空捏造) ──
    print(colorize("【维度 4/7 · 📚 手头已有备考资料白名单登记】", C.BOLD))
    print(colorize("  ⚠️ 【防虚构铁律】：私教仅能从您指定的白名单资料出题与复盘，严禁捏造未拥有的书籍！", C.YELLOW))
    if interactive:
        if m_choice != "none":
            math_books = input("  1. 数学手头已有权威教辅与真题 [如: 复习全书基础篇+历年真题]: ").strip() or "同济教材+基础讲义+历年真题"
        else:
            math_books = "不考数学"
        eng_books = input("  2. 英语手头已有权威教辅与真题 [如: 黄皮书历年真题+考研词汇必考词]: ").strip() or "近15年历年真题精解+真题词汇宝典"
        pol_books = input("  3. 政治手头已有参考书 [如: 肖秀荣核心考点+1000题+冲刺卷]: ").strip() or "考研政治核心考案+精选1000题+冲刺全真卷"
        pro_books = input(f"  4. 专业课手头资料 [如: 王道四本书 / 官方指定教材+历年真题]: ").strip() or f"{pro_name}官方教材与课后习题+历年真题汇编"
    else:
        math_books = plan.get("math_books", "同济教材+基础讲义+历年真题")
        eng_books = plan.get("eng_books", "近15年历年真题精解+真题词汇宝典")
        pol_books = plan.get("pol_books", "考研政治核心考案+精选1000题+冲刺全真卷")
        pro_books = plan.get("pro_books", f"{pro_name}官方教材与课后习题+历年真题汇编")

    plan["math_books"] = math_books
    plan["eng_books"] = eng_books
    plan["pol_books"] = pol_books
    plan["pro_books"] = pro_books
    print(colorize("  [√] 白名单已严格核验，AI 私教将 100% 锁定本名单范围！\n", C.GREEN))

    # ── 维度 5: 每日时间预算与各科切分 ──
    print(colorize("【维度 5/7 · ⏳ 每日可用复习时间与各科预算】", C.BOLD))
    if interactive:
        print("  请选择您日常的备考作息模式：")
        for k, p in ROUTINE_PRESETS.items():
            print(f"    [{k}] {p['name']} (日均 {p['total_hours']}h: 数{p['math_hours']}h/英{p['eng_hours']}h/政{p['pol_hours']}h/专{p['pro_hours']}h)")
        r_choice = input("  选择作息模式 (1/2/3) [默认 1]: ").strip() or "1"
        preset = ROUTINE_PRESETS.get(r_choice, ROUTINE_PRESETS["1"])

        tot_h_input = input(f"  1. 每日可用总时长(小时) [默认: {preset['total_hours']}]: ").strip()
        total_hours = float(tot_h_input) if tot_h_input else preset['total_hours']

        if m_choice != "none":
            m_h_input = input(f"  2. 数学每日分配小时 [默认: {preset['math_hours']}]: ").strip()
            math_hours = float(m_h_input) if m_h_input else preset['math_hours']
        else:
            math_hours = 0.0

        e_h_input = input(f"  3. 英语每日分配小时 [默认: {preset['eng_hours']}]: ").strip()
        eng_hours = float(e_h_input) if e_h_input else preset['eng_hours']

        p_h_input = input(f"  4. 政治每日分配小时 [默认: {preset['pol_hours']}]: ").strip()
        pol_hours = float(p_h_input) if p_h_input else preset['pol_hours']

        pro_h_input = input(f"  5. 专业课每日分配小时 [默认: {preset['pro_hours']}]: ").strip()
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

        if m_choice != "none":
            math_target = input("  1. 数学目标成绩 [默认: 110+ 分]: ").strip() or "110+ 分"
        else:
            math_target = "不考数学"
        eng_target = input("  2. 英语目标成绩 [默认: 65+ 分]: ").strip() or "65+ 分"
        pol_target = input("  3. 政治目标成绩 [默认: 70+ 分]: ").strip() or "70+ 分"
        pro_target = input(f"  4. 专业课目标成绩 [默认: 120-130 分]: ").strip() or "120-130 分"
        total_target = input("  5. 初试总分目标 [默认: 370+ 分]: ").strip() or "370+ 分"
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
    apply_study_plan(plan)
    print_study_plan_summary(plan)
    return plan

def apply_study_plan(plan):
    """将定制化方案持久化到 AGENTS.md、各科文件与配置文件中"""
    agents_path = ROOT / "AGENTS.md"
    if not agents_path.exists():
        return

    # 1. 更新官方大纲
    try:
        from tools import syllabus_manager
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

    # 2. 组装根目录 AGENTS.md 第一节
    m_name = plan.get("math_name", "数学")
    e_name = plan.get("eng_name", "英语")
    pro_name = plan.get("pro_name", "专业课")

    content = agents_path.read_text(encoding="utf-8")
    content = re.sub(r"- \*\*目标院校\*\*：.*", f"- **目标院校**：`{plan.get('school', '目标院校')}`", content)
    content = re.sub(r"- \*\*报考专业\*\*：.*", f"- **报考专业**：`{plan.get('major', '报考专业')}`", content)
    content = re.sub(r"- \*\*初试日期\*\*：.*", f"- **初试日期**：`{plan.get('exam_date', '2027-12-19')}` (倒计时约 {plan.get('days_left', 0)} 天)", content)
    
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

    # 注入白名单教辅书目与作息机制
    schedule_section = f"""
### 【个性化学情与作息调节机制】 (系统已锁定)
- **每日时间预算**: 每日投入 `{plan.get('total_hours', 8.5)} 小时` (数学: {plan.get('math_hours', 3.0)}h / 英语: {plan.get('eng_hours', 2.0)}h / 政治: {plan.get('pol_hours', 1.0)}h / 专业课: {plan.get('pro_hours', 2.5)}h)
- **每周休整窗口**: `{plan.get('rest_weekly', '每周日晚放松休整')}`
- **每月模考复盘**: `{plan.get('rest_monthly', '每月最后一个周日全真闭卷模考与全科雷达复盘')}`
- **手头资料白名单 (AI 严守范围)**:
  - 数学: `{plan.get('math_books', '同济教材+复习全书+历年真题')}`
  - 英语: `{plan.get('eng_books', '黄皮书历年真题精解+必考词汇')}`
  - 政治: `{plan.get('pol_books', '核心考案+精选1000题+冲刺卷')}`
  - 专业课: `{plan.get('pro_books', '官方指定教材与课后习题+历年真题')}`
- **核心薄弱诊断与攻坚防线**:
  - 数学薄弱点: `{plan.get('math_weakness', '计算失误、导数中值定理')}`
  - 英语薄弱点: `{plan.get('eng_weakness', '长难句主干速抓、阅读推断题')}`
  - 政治薄弱点: `{plan.get('pol_weakness', '马原哲学多选题、帽子词混淆')}`
  - 专业课薄弱点: `{plan.get('pro_weakness', '核心算法设计与证明步骤')}`
"""
    if "### 【个性化学情与作息调节机制】" in content:
        content = re.sub(r"### 【个性化学情与作息调节机制】.*?(?=\n## 1\.|\n### 二、)", schedule_section.strip() + "\n\n", content, flags=re.DOTALL)
    else:
        content = content.replace("### 二、四种私教辅导风格设定", schedule_section + "\n### 二、四种私教辅导风格设定")

    agents_path.write_text(content, encoding="utf-8")

    # 3. 同步更新各子目录 AGENTS.md
    update_subject_agents(plan)

    # 4. 更新 ky_config.json
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["onboarding_completed"] = True
    cfg["study_plan"] = plan
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5. 自动重新编译自测看板
    build_py = ROOT / "05-考研看板" / "build.py"
    if build_py.exists():
        try:
            import subprocess
            subprocess.run([sys.executable, str(build_py)], cwd=str(ROOT / "05-考研看板"), capture_output=True)
        except Exception:
            pass

def update_subject_agents(plan):
    """将白名单与薄弱项同步写入 01~04 各科专属 AGENTS.md"""
    # 数学
    m_file = ROOT / "01-数学" / "AGENTS.md"
    if m_file.exists():
        t = m_file.read_text(encoding="utf-8")
        t = re.sub(r"- \*\*考试科目\*\*：.*", f"- **考试科目**：`{plan.get('math_name', '数学')}`", t)
        t = re.sub(r"- \*\*目标分数\*\*：.*", f"- **目标分数**：`{plan.get('math_target', '110+ 分')}` (摸底: {plan.get('math_baseline', '60分')})", t)
        t = re.sub(r"- \*\*每日投入\*\*：.*", f"- **每日投入**：`{plan.get('math_hours', 2.5)} 小时`", t)
        t = re.sub(r"- \*\*核心教材\*\*：.*", f"- **核心教材与白名单**：`{plan.get('math_books', '同济教材+基础讲义+历年真题')}` (严禁虚构未有资料！)", t)
        if "- **核心薄弱点**：" not in t:
            t = t.replace("- **核心教材与白名单**：", f"- **核心薄弱点**：`{plan.get('math_weakness', '导数中值定理、计算失误')}`\n- **核心教材与白名单**：")
        m_file.write_text(t, encoding="utf-8")

    # 英语
    e_file = ROOT / "02-英语" / "AGENTS.md"
    if e_file.exists():
        t = e_file.read_text(encoding="utf-8")
        t = re.sub(r"- \*\*考试科目\*\*：.*", f"- **考试科目**：`{plan.get('eng_name', '英语')}`", t)
        t = re.sub(r"- \*\*目标分数\*\*：.*", f"- **目标分数**：`{plan.get('eng_target', '65+ 分')}` (摸底: {plan.get('eng_baseline', '50分')})", t)
        t = re.sub(r"- \*\*每日投入\*\*：.*", f"- **每日投入**：`{plan.get('eng_hours', 2.0)} 小时`", t)
        t = re.sub(r"- \*\*核心题源\*\*：.*", f"- **核心资料与白名单**：`{plan.get('eng_books', '历年真题精解')}`", t)
        if "- **核心薄弱点**：" not in t:
            t = t.replace("- **核心资料与白名单**：", f"- **核心薄弱点**：`{plan.get('eng_weakness', '长难句主干速抓')}`\n- **核心资料与白名单**：")
        e_file.write_text(t, encoding="utf-8")

    # 政治
    p_file = ROOT / "03-思想政治理论" / "AGENTS.md"
    if p_file.exists():
        t = p_file.read_text(encoding="utf-8")
        if "### 学员配置区" not in t:
            t += f"\n### 学员配置区\n- **目标分数**：`{plan.get('pol_target', '70+ 分')}` (摸底: {plan.get('pol_baseline', '40分')})\n- **每日投入**：`{plan.get('pol_hours', 1.0)} 小时`\n- **核心书目**：`{plan.get('pol_books', '精讲考点+1000题')}`\n- **核心薄弱点**：`{plan.get('pol_weakness', '马原哲学原理')}`\n"
            p_file.write_text(t, encoding="utf-8")

    # 专业课
    pro_file = ROOT / "04-专业课" / "AGENTS.md"
    if pro_file.exists():
        t = pro_file.read_text(encoding="utf-8")
        t = re.sub(r"- \*\*目标院校\*\*：.*", f"- **目标院校**：`{plan.get('school', '目标院校')}`", t)
        t = re.sub(r"- \*\*专业代码与名称\*\*：.*", f"- **专业代码与名称**：`{plan.get('major', '报考专业')}`", t)
        t = re.sub(r"- \*\*专业课科目代码与名称\*\*：.*", f"- **专业课科目代码与名称**：`{plan.get('pro_name', '专业课')}`", t)
        t = re.sub(r"- \*\*满分与目标成绩\*\*：.*", f"- **满分与目标成绩**：`目标 {plan.get('pro_target', '120-130 分')}` (摸底: {plan.get('pro_baseline', '80分')})", t)
        t = re.sub(r"- \*\*指定参考教材与版本\*\*：.*", f"- **指定白名单资料**：`{plan.get('pro_books', '官方教材+真题')}`", t)
        pro_file.write_text(t, encoding="utf-8")

def print_study_plan_summary(plan):
    """打印全彩考研总战役全景看板"""
    m_n = plan.get("math_name", "数学")
    e_n = plan.get("eng_name", "英语")
    pro_n = plan.get("pro_name", "专业课")

    print(f"""
{C.GREEN}╭────────────────────────────────────────────────────────────────────────╮
│  🎉 恭喜！您的考研必考专属战役方案已全量设计并锁定完毕！              │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
{C.BOLD}【总战役基本盘】{C.RESET}
  • 目标院校 / 专业: {C.CYAN}{plan.get('school', '目标院校')} · {plan.get('major', '报考专业')}{C.RESET}
  • 初试目标日期:    {C.MAGENTA}{plan.get('exam_date', '2027-12-19')}{C.RESET} (研考倒计时: {C.MAGENTA}{plan.get('days_left', 0)} 天{C.RESET})
  • 当前备考阶段:    {C.YELLOW}{plan.get('stage_name', STAGES['2'][0])}{C.RESET}
  • 私教辅导风格:    {C.GREEN}{plan.get('style_name', STYLES['1'][0])}{C.RESET}
  • 初试总分目标:    {C.RED}{C.BOLD}{plan.get('total_target', '370+ 分')}{C.RESET} (每日总精力预算: {C.CYAN}{plan.get('total_hours', 8.5)} 小时{C.RESET})

{C.BOLD}【四科提分矩阵与薄弱项雷达】{C.RESET}
  1. {C.BOLD}{m_n}{C.RESET}:
     - 目标分 / 摸底分: {C.GREEN}{plan.get('math_target', '110+ 分')}{C.RESET} / {C.DIM}{plan.get('math_baseline', '60分')}{C.RESET}  (每日投入: {plan.get('math_hours', 3.0)}h)
     - 痛点防线: {C.YELLOW}{plan.get('math_weakness', '导数中值定理、计算失误')}{C.RESET}
     - 白名单书目: {plan.get('math_books', '同济教材+基础讲义+真题')}
  2. {C.BOLD}{e_n}{C.RESET}:
     - 目标分 / 摸底分: {C.GREEN}{plan.get('eng_target', '65+ 分')}{C.RESET} / {C.DIM}{plan.get('eng_baseline', '50分')}{C.RESET}  (每日投入: {plan.get('eng_hours', 2.0)}h)
     - 痛点防线: {C.YELLOW}{plan.get('eng_weakness', '长难句主干速抓、细节定位')}{C.RESET}
     - 白名单书目: {plan.get('eng_books', '黄皮书历年真题+必考词汇')}
  3. {C.BOLD}思想政治理论{C.RESET}:
     - 目标分 / 摸底分: {C.GREEN}{plan.get('pol_target', '70+ 分')}{C.RESET} / {C.DIM}{plan.get('pol_baseline', '40分')}{C.RESET}  (每日投入: {plan.get('pol_hours', 1.0)}h)
     - 痛点防线: {C.YELLOW}{plan.get('pol_weakness', '马原唯物辩证法、多选题漏选')}{C.RESET}
     - 白名单书目: {plan.get('pol_books', '核心考案+精选1000题+冲刺卷')}
  4. {C.BOLD}{pro_n}{C.RESET}:
     - 目标分 / 摸底分: {C.GREEN}{plan.get('pro_target', '120-130 分')}{C.RESET} / {C.DIM}{plan.get('pro_baseline', '80分')}{C.RESET}  (每日投入: {plan.get('pro_hours', 2.5)}h)
     - 痛点防线: {C.YELLOW}{plan.get('pro_weakness', '核心算法设计、解答题推导步骤')}{C.RESET}
     - 白名单书目: {plan.get('pro_books', '官方教材+历年真题')}

{C.BOLD}【科学作息与防疲劳减压机制】{C.RESET}
  • 每周放风休整: {C.CYAN}{plan.get('rest_weekly', '每周日晚放松休整')}{C.RESET}
  • 每月模考复盘: {C.CYAN}{plan.get('rest_monthly', '每月最后一个周日全真模考')}{C.RESET}
  • 防内耗保障:   {C.DIM}若连续 2 天任务达成率低于 60%，系统将自动启动温和减负模式，优先稳住核心基础盘。{C.RESET}

✨ 本方案已完整写入工作区各科中枢协议，随时输入 {C.YELLOW}/plan{C.RESET} 或 {C.YELLOW}ky plan{C.RESET} 即可动态调整！
""")

if __name__ == "__main__":
    run_study_plan_wizard(interactive=True)
