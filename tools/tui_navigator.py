# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan Study Chain) · 终端全景交互中枢 (TUI Navigator v2.5)

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
  [0] 🚪 安全退出 · 退出系统
"""

import sys
import os
import re
import json
import argparse
import unicodedata
from pathlib import Path
from datetime import datetime, date

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "ky_config.json"


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
    if sys.stdout.isatty():
        return f"{color}{text}{Colors.RESET}"
    return text


def is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF or
        0x2600 <= cp <= 0x27BF or
        0x2300 <= cp <= 0x23FF or
        0x2B50 <= cp <= 0x2B55
    )


def display_width(s: str) -> int:
    """计算字符串在终端的实际打印宽度 (去除 ANSI，精准判定全角汉字与 Emoji)"""
    clean_s = re.sub(r'\033\[[0-9;]*m', '', str(s))
    clean_s = clean_s.replace('\ufe0f', '').replace('\u200d', '')
    w = 0
    for ch in clean_s:
        if is_emoji_char(ch) or unicodedata.east_asian_width(ch) in ('W', 'F'):
            w += 2
        else:
            w += 1
    return w


def pad_display(s: str, target_w: int) -> str:
    """按终端可见宽度填充空格"""
    w = display_width(s)
    if w < target_w:
        return s + ' ' * (target_w - w)
    return s


def render_box_line(text: str, total_w: int = 84, align: str = 'left', border: str = '│', pad: int = 1) -> str:
    """渲染带边框的单行，并严格校准右边框对其"""
    w = display_width(text)
    rem = max(0, total_w - 2 - w)
    if align == 'center':
        lp = rem // 2
        rp = rem - lp
        return f"{border}{' ' * lp}{text}{' ' * rp}{border}"
    else:
        return f"{border}{' ' * pad}{text}{' ' * max(0, rem - pad)}{border}"


def render_sec_header(title: str, total_w: int = 84, color: str = '') -> str:
    w = display_width(title)
    rem = max(0, total_w - 5 - w)
    return colorize(f"╭── {title} " + "─" * rem + "╮", color)


def render_sec_footer(total_w: int = 84, color: str = '') -> str:
    return colorize("╰" + "─" * (total_w - 2) + "╯", color)


def render_progress_bar(pct: float, width: int = 12) -> str:
    filled = int(round(width * max(0.0, min(100.0, pct)) / 100))
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct:.1f}%"


# ════════════════════════════════════════════════════════════════
# 学情与考情指标聚合器 (State Aggregator)
# ════════════════════════════════════════════════════════════════

def get_countdown_days() -> int:
    try:
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            exam_str = cfg.get("exam_date", "2026-12-19")
        else:
            exam_str = "2026-12-19"
        exam_d = datetime.strptime(exam_str, "%Y-%m-%d").date()
        diff = (exam_d - date.today()).days
        return max(0, diff)
    except Exception:
        return 104


def get_config_summary() -> dict:
    if not CONFIG_FILE.exists():
        return {
            "school": "未指定", "major": "未指定", "stage": "强化阶段",
            "style": "严格把关型", "hours": 8.5
        }
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        sp = cfg.get("study_plan", {})
        school = sp.get("school") or cfg.get("target_school") or "未指定"
        major = sp.get("major") or cfg.get("target_major") or "未指定"
        stage = cfg.get("stage", "强化题型攻坚阶段")
        style = cfg.get("coaching_style", "严格把关·保姆提分型 (Strict)")
        style_short = style.split("·")[0] if "·" in style else style
        hours = float(cfg.get("daily_budget_hours", 8.5))
        return {
            "school": school, "major": major, "stage": stage,
            "style": style_short, "hours": hours
        }
    except Exception:
        return {
            "school": "未指定", "major": "未指定", "stage": "强化阶段",
            "style": "严格把关型", "hours": 8.5
        }


def get_today_progress() -> tuple[int, int]:
    """统计今日四科总任务数与已完成数"""
    total = 0
    done = 0
    subjs = ["01-数学", "02-英语", "03-思想政治理论", "04-专业课"]
    for s in subjs:
        t_file = ROOT / s / "_状态" / "今日任务.md"
        if t_file.exists():
            try:
                txt = t_file.read_text(encoding="utf-8")
                for line in txt.splitlines():
                    if re.match(r"^\s*-\s*\[x\]", line, re.IGNORECASE):
                        total += 1
                        done += 1
                    elif re.match(r"^\s*-\s*\[ \]", line):
                        total += 1
            except Exception:
                pass
    return done, total


def get_intel_ribbon() -> list[str]:
    """提取考情雷达关键情报条目"""
    ribbon = []
    # 1. 监控状态
    watch_file = ROOT / ".memory" / "admission_watch.json"
    if watch_file.exists():
        try:
            w_data = json.loads(watch_file.read_text(encoding="utf-8"))
            if w_data:
                names = [v.get("school", k) for k, v in list(w_data.items())[:2]]
                ribbon.append(f"📡 简章监控: {len(w_data)} 所院校 ({', '.join(names)}) 巡检中")
        except Exception:
            pass
    if not ribbon:
        ribbon.append("📡 简章监控: 暂未配置高校 (可输入 8 开启监控)")

    # 2. 考纲最新变动
    pro_dir = ROOT / "04-专业课"
    if pro_dir.exists():
        diff_files = sorted(list(pro_dir.glob("考纲变动分析_*.md")), key=lambda p: p.stat().st_mtime, reverse=True)
        if diff_files:
            txt = diff_files[0].read_text(encoding="utf-8", errors="ignore")
            vol_m = re.search(r"考点动荡率[^\d]*(\d+\.?\d*)%", txt)
            vol = vol_m.group(1) if vol_m else "0.0"
            ribbon.append(f"📑 考纲变动: 波动率 {vol}% (已生成逐级处方)")

    # 3. 社媒经验贴
    exp_dir = ROOT / "docs" / "experiences"
    if exp_dir.exists():
        exp_files = list(exp_dir.glob("*.md"))
        if exp_files:
            ribbon.append(f"💬 社媒口碑: 已沉淀 {len(exp_files)} 篇院校实名经验档案")

    return ribbon


# ════════════════════════════════════════════════════════════════
# 菜单定义与分类架构 (Categorized Menu)
# ════════════════════════════════════════════════════════════════

MENU_GROUPS = [
    (
        "⚡ 核心备考与实战攻坚 (Core Preparation)",
        Colors.YELLOW,
        [
            ("1", "今日任务 (Daily Tasks)", "📋", "查看四科任务量、打卡推进与行动指引", "today"),
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
            ("0", "安全退出 (Exit System)", "🚪", "保存状态并平稳退出终端导航器", "exit"),
        ]
    )
]

# 扁平化映射列表，供快速查询与测试断言
MENU_OPTIONS = [(k, n, d, alias) for _, _, items in MENU_GROUPS for k, n, icon, d, alias in items]

TOTAL_PANEL_WIDTH = 84


# ════════════════════════════════════════════════════════════════
# 渲染器实现 (Renderers)
# ════════════════════════════════════════════════════════════════

def render_header() -> str:
    days = get_countdown_days()
    info = get_config_summary()
    done_tasks, total_tasks = get_today_progress()
    today_pct = (done_tasks / total_tasks * 100) if total_tasks > 0 else 0.0

    total_study_days = 134
    passed_days = max(1, total_study_days - days)
    calendar_pct = (passed_days / total_study_days * 100)

    W = TOTAL_PANEL_WIDTH
    lines = []
    lines.append(colorize("╭" + "─" * (W - 2) + "╮", Colors.CYAN))
    lines.append(render_box_line(colorize("🎯 考研学习链 (Kaoyan Study Chain) · 终端全景智能中枢 v2.5", Colors.BOLD + Colors.CYAN), W, 'center'))
    lines.append(colorize("├" + "─" * (W - 2) + "┤", Colors.CYAN))

    # 第一行：倒计时与备考历程进度条
    row1 = (
        f"{colorize('⏳ 2026 初试倒计时: ' + str(days) + ' 天', Colors.BOLD + Colors.YELLOW)}  │  "
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

    # 第三行：辅导风格与今日四科任务打卡进度
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
    W = TOTAL_PANEL_WIDTH
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

    lines.append(colorize("💡 提示：输入操作序号 [0-9] 或指令别名 (如 1 / today / compose) 即可启动模块", Colors.DIM + Colors.CYAN))
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# 动作分发与业务执行器 (Action Dispatcher)
# ════════════════════════════════════════════════════════════════

def execute_action(action_key: str, interactive: bool = True) -> bool:
    """
    分发执行菜单动作。返回 False 表示退出循环，True 表示继续。
    """
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
        print(colorize(f"\n[!] 未知操作编号: '{key}'，请输入 0-9 之间的选项。", Colors.RED))
        return True

    opt_key, opt_name, cmd_alias = matched
    print(colorize(f"\n▶ 正在启动模块：{opt_name} ...", Colors.BOLD + Colors.CYAN))

    try:
        if cmd_alias == "today":
            from ky_cli import print_today_tasks_summary
            print_today_tasks_summary(as_json=False)
        elif cmd_alias == "compose":
            info = get_config_summary()
            sub = "pro"
            subj_input = input(f"请输入组卷科目 [math/eng/pol/pro，默认 pro]: ").strip() if interactive else ""
            if subj_input:
                sub = subj_input
            kw_input = input("请输入重点考点关键词 (回车跳过): ").strip() if interactive else ""
            from skills import exam_composer
            res = exam_composer.compose_exam(sub, topic_filter=kw_input or None, total_questions=5)
            print(exam_composer.format_exam_paper(res))
        elif cmd_alias == "variant":
            kw = input("请输入需要寻找变式题的考点关键词: ").strip() if interactive else "二叉树"
            if not kw:
                kw = "二叉树"
            from skills import variant_retriever
            res = variant_retriever.retrieve_variants("pro", kw)
            print(f"\n检索到 {len(res)} 道同源变式题。")
            if res:
                print(f"推荐变式 [难度 {res[0].get('difficulty', '中等')}]: {res[0].get('stem', '')[:100]}...")
        elif cmd_alias == "diff":
            old_p = input("旧大纲路径 [回车使用 04-专业课/考试大纲.md]: ").strip() if interactive else ""
            new_p = input("新大纲路径 [回车使用 04-专业课/考试大纲.md]: ").strip() if interactive else ""
            from intelligence.syllabus_diff import run_syllabus_diff_flow
            diff_res = run_syllabus_diff_flow(
                old_path=Path(old_p) if old_p else None,
                new_path=Path(new_p) if new_p else None
            )
            print(colorize(f"\n[+] 考纲 Diff 报告生成完毕 (考点动荡率: {diff_res['metrics']['volatility_rate']:.1f}%):", Colors.GREEN))
            print(f"    生成路径: {diff_res['report_path']}")
        elif cmd_alias == "ingest":
            f_path = input("请输入要切片的真题/讲义 Markdown 路径: ").strip() if interactive else ""
            if not f_path:
                print("未指定文件路径，已返回。")
            else:
                from skills import material_ingestion
                ing_res = material_ingestion.ingest_material_file(f_path)
                print(colorize(f"\n[+] 切片入库成功：识别 {ing_res['total_parsed']} 道题目，生成题库切片：{ing_res['saved_path']}", Colors.GREEN))
        elif cmd_alias == "scout":
            info = get_config_summary()
            school = input(f"请输入目标高校 [默认 {info['school']}]: ").strip() if interactive else ""
            if not school:
                school = info["school"]
            major = input(f"请输入专业 [默认 {info['major']}]: ").strip() if interactive else ""
            if not major:
                major = info["major"]
            from skills import school_scout
            res = school_scout.scout_school(school=school, major=major, include_social=True, save_report=True)
            print(colorize(f"\n[+] 院校研报生成完毕: {res.get('saved_path')}", Colors.GREEN))
            if res.get("experience_dossier_path"):
                print(colorize(f"[+] 社媒真实经验档案沉淀完毕: {res.get('experience_dossier_path')}", Colors.GREEN))
        elif cmd_alias == "compare":
            s1 = input("请输入第一所对标高校: ").strip() if interactive else "华中科技大学"
            s2 = input("请输入第二所对标高校: ").strip() if interactive else "浙江大学"
            if not s1: s1 = "华中科技大学"
            if not s2: s2 = "浙江大学"
            from intelligence.comparator import compare_two_schools, format_comparison_report
            comp_res = compare_two_schools(s1, s2)
            print("\n" + format_comparison_report(comp_res))
        elif cmd_alias == "watch":
            from intelligence.watcher import AdmissionWatcher
            watcher = AdmissionWatcher()
            watched = watcher.list_watched()
            if not watched:
                info = get_config_summary()
                s = info.get("school")
                if s and s not in ("未指定", "目标院校"):
                    watcher.add_watch(s)
            findings = watcher.check_updates()
            print(colorize(f"\n[+] 招考动态巡检完成，已监控 {len(watcher.list_watched())} 所高校：", Colors.GREEN))
            for f in findings:
                status_color = Colors.GREEN if f.get("status") == "UPDATED" else Colors.CYAN
                print(f"  • {f.get('school')}: {colorize(f.get('msg', '正常'), status_color)}")
        elif cmd_alias == "build":
            b_script = ROOT / "05-考研看板" / "build.py"
            if b_script.exists():
                import subprocess
                subprocess.run([sys.executable, str(b_script)], check=False)
                print(colorize("\n[+] 考研看板已构建完成！可打开 docs/index.html 查看。", Colors.GREEN))
            else:
                print(colorize("\n[!] 未找到 05-考研看板/build.py 脚本", Colors.RED))

    except Exception as e:
        print(colorize(f"\n[!] 执行过程中发生异常: {e}", Colors.RED))

    return True


def run_tui_loop():
    """交互式主循环"""
    if sys.platform == "win32":
        try:
            os.system("color")
        except Exception:
            pass

    while True:
        if sys.stdout.isatty():
            os.system("cls" if os.name == "nt" else "clear")

        print("\n" + render_header())
        print(render_menu())
        try:
            prompt_str = colorize("⌨️  请输入操作序号 [0-9] 或指令别名: ", Colors.BOLD + Colors.YELLOW)
            choice = input(prompt_str).strip()
            keep_running = execute_action(choice, interactive=True)
            if not keep_running:
                break
            input(colorize("\n按 Enter 键返回主菜单...", Colors.DIM + Colors.WHITE))
        except (KeyboardInterrupt, EOFError):
            print(colorize("\n👋 操作中断，已安全返回。\n", Colors.YELLOW))
            break


def main():
    parser = argparse.ArgumentParser(description="考研学习链 (Kaoyan Study Chain) · 终端交互中枢 (TUI)")
    parser.add_argument("--action", "-a", type=str, default="", help="直接执行指定编号动作 (非交互模式)")
    parser.add_argument("--list", "-l", action="store_true", help="打印可用菜单并退出")
    args = parser.parse_args()

    if args.list:
        print(render_header())
        print(render_menu())
        return

    if args.action:
        execute_action(args.action, interactive=False)
        return

    run_tui_loop()


if __name__ == "__main__":
    main()
