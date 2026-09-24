# -*- coding: utf-8 -*-
"""
REPL 渲染与盒绘制层 (renderer.py)
包含 ANSI 色彩管理、CJK 文本列宽自适应、欢迎看板、状态大盘与指令面板
"""

import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

try:
    from tools.cli.shared import (
        ROOT, SUBJECT_DIRS, COACHING_STYLES, load_config, read_text_safe,
        get_today_tasks_data, is_math_disabled, recommended_checkin_command
    )
except ImportError:
    from cli.shared import (
        ROOT, SUBJECT_DIRS, COACHING_STYLES, load_config, read_text_safe,
        get_today_tasks_data, is_math_disabled, recommended_checkin_command
    )

try:
    import exam_calendar
except ImportError:
    try:
        from tools import exam_calendar
    except ImportError:
        exam_calendar = None

try:
    from skills import list_skills
except ImportError:
    try:
        from tools.skills import list_skills
    except ImportError:
        def list_skills(): return {}

try:
    import intelligence
except ImportError:
    try:
        from tools import intelligence
    except ImportError:
        intelligence = None

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"

def colorize(text: str, color_code: str) -> str:
    """对字符串追加 ANSI 色彩转义字符（Windows 传统 cmd 容错处理）"""
    if os.name == "nt" and "WT_SESSION" not in os.environ and "TERM" not in os.environ:
        return text
    return f"{color_code}{text}{C.RESET}"

def cjk_width(s: str) -> int:
    """计算考虑中文字符宽度的显示列数（去除 ANSI 逃逸码）"""
    clean = re.sub(r'\033\[[0-9;]*m', '', s)
    w = 0
    for ch in clean:
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ('W', 'F') else 1
    return w

def print_welcome(live_port: int = 8088, animate: bool = True) -> None:
    """启动横幅欢迎大屏与技能唤醒动画"""
    try:
        _skills_map = list_skills() or {}
    except Exception:
        _skills_map = {}
    skill_count = len(_skills_map)
    _preview = [str(k) for k in list(_skills_map.keys())[:6]]
    skill_preview = " / ".join(_preview) if _preview else "Vision/Math/Composer"
    skill_count_text = f"{skill_count} 项" if skill_count else "全部"
    # [B4] 真实状态统计：此前写死"N项全就绪"，技能缺依赖（sympy/pypdf/API Key）时
    # 横幅仍宣称全就绪，与 /skills 面板的真实状态自相矛盾。现在按 health 档位统计，
    # 只有全部 READY 才说"全就绪"。
    _ready_n = sum(1 for sk in _skills_map.values()
                   if (sk.get("health") or {}).get("status") == "READY")
    if skill_count and _ready_n == skill_count:
        skill_status_text = f"{skill_count}项全就绪"
    elif skill_count:
        skill_status_text = f"{_ready_n}/{skill_count} 项就绪"
    else:
        skill_status_text = "已就绪"

    gradient_ascii = f"""
{C.CYAN}{C.BOLD}  ██╗  ██╗ █████╗  ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗     ██████╗██╗     ██╗{C.RESET}
{C.CYAN}{C.BOLD}  ██║ ██╔╝██╔══██╗██╔═══██╗╚██╗ ██╔╝██╔══██╗████╗  ██║    ██╔════╝██║     ██║{C.RESET}
{C.GREEN}{C.BOLD}  █████═╝ ███████║██║   ██║ ╚████╔╝ ███████║██╔██╗ ██║    ██║     ██║     ██║{C.RESET}
{C.GREEN}{C.BOLD}  ██╔═██╗ ██╔══██║██║   ██║  ╚██╔╝  ██╔══██║██║╚██╗██║    ██║     ██║     ██║{C.RESET}
{C.YELLOW}{C.BOLD}  ██║ ╚██╗██║  ██║╚██████╔╝   ██║   ██║  ██║██║ ╚████║    ╚██████╗███████╗██║{C.RESET}
{C.YELLOW}{C.BOLD}  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝     ╚═════╝╚══════╝╚═╝{C.RESET}
"""
    print(gradient_ascii)

    if animate:
        steps = [
            ("装载考研全科中枢总控协议 (AGENTS.md)...", 0.04),
            (f"唤醒 {skill_count_text}考研专有技能 ({skill_preview})...", 0.04),
            (f"启动 Web 实时可视化伴侣 (:{live_port}/live)...", 0.04)
        ]
        for step, delay in steps:
            sys.stdout.write(f"  {C.CYAN}⠋{C.RESET} {step}")
            sys.stdout.flush()
            time.sleep(delay)
            sys.stdout.write(f"\r  {C.GREEN}✔{C.RESET} {step} {C.GREEN}[就绪]{C.RESET}\n")
            sys.stdout.flush()
        print()

    today = datetime.now().date()
    exam_date = datetime(today.year, 12, 19).date()
    if today > exam_date:
        exam_date = datetime(today.year + 1, 12, 19).date()
    days_left = (exam_date - today).days

    cfg = load_config()
    curr_subj = cfg.get("active_subject", "math")
    subj_name = SUBJECT_DIRS.get(curr_subj, ("01-数学", "数学"))[1]
    provider = cfg.get("api_provider", "deepseek")
    model_name = cfg.get("model", "deepseek-chat")

    style_tag = "严格把关·保姆流"
    agents_root = ROOT / "AGENTS.md"
    if agents_root.exists():
        txt = read_text_safe(agents_root)
        m = re.search(r"- \*\*当前激活辅导风格\*\*：`([^`]+)`", txt)
        if m:
            raw_s = m.group(1).strip().strip("[]")
            m_s = re.search(r"(\d+\.\s*)?([^\s/\]]+(?:·[^\s/\]]+)?)", raw_s)
            if m_s:
                style_tag = re.sub(r"^\d+\.\s*", "", m_s.group(2)).strip()
            else:
                style_tag = "严格把关保姆流"

    subj_short = subj_name.replace("专属私教", "").replace("私教", "").strip()
    style_short = style_tag.split("·")[0] if "·" in style_tag else style_tag
    # [R2-A4 修复] 不考数学的方案不得在快捷指令速查里列 /math（文科考生输入必被拒）。
    _math_shortcut = "" if is_math_disabled(cfg) else f"{C.GREEN}/math{C.RESET} 数学  "

    print(f"""{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}🎓 考研全科 AI 专属私教终端 · Kaoyan CLI (Claude Code / Gemini 体验版){C.RESET}  {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  [ 专属私教: {C.GREEN}{subj_short}{C.RESET} · {C.YELLOW}{style_short}{C.RESET} ]   [ 🎯 研考初试倒计时: {C.MAGENTA}{days_left} 天{C.RESET} ]          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  [ 🧠 模型: {C.BLUE}{provider}/{model_name}{C.RESET} ]   [ 🌐 伴侣: {C.CYAN}:{live_port}/live{C.RESET} ]   [ 🧩 技能: {C.GREEN}{skill_status_text}{C.RESET} ]{C.CYAN}│{C.RESET}
{C.CYAN}├────────────────────────────────────────────────────────────────────────┤{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}快捷指令速查 (随时输入 / 展开完整指令大盘)：                            {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}   {_math_shortcut}{C.GREEN}/eng{C.RESET} 英语  {C.GREEN}/pol{C.RESET} 政治  {C.GREEN}/pro{C.RESET} 专业课  {C.CYAN}/view{C.RESET} 网页伴侣            {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}   {C.YELLOW}/admission{C.RESET} 招考证据  {C.YELLOW}/watch{C.RESET} 简章监控  {C.YELLOW}/exam{C.RESET} 靶向组卷  {C.YELLOW}/variant{C.RESET} 变式检索  {C.CYAN}│{C.RESET}
{C.CYAN}╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")

    try:
        import study_planner
        fatigue_info = study_planner.check_fatigue_alert(cfg)
        if fatigue_info.get("alert"):
            print(f"{C.YELLOW}{C.BOLD}╭── ⚠️ 防疲劳减负保障警报 (Fatigue Protection Alert) ─────────────────╮{C.RESET}")
            for l in fatigue_info.get("message", "").splitlines():
                print(f"{C.YELLOW}│{C.RESET}  {l}")
            print(f"{C.YELLOW}╰────────────────────────────────────────────────────────────────────────╯{C.RESET}\n")
    except Exception:
        pass

def print_command_palette(cfg: Optional[dict] = None) -> None:
    """打印 Claude Code 风格分类指令面板。

    [R2-A4 修复] 不考数学的方案不得再把 /math 与「数学报到」列在最前（文科考生
    照着输入只会被拒），故按 is_math_disabled 动态决定是否展示数学路由。
    """
    if cfg is None:
        try:
            cfg = load_config()
        except Exception:
            cfg = {}
    _math_off = is_math_disabled(cfg)
    _math_route = (
        "" if _math_off else
        f"{C.CYAN}│{C.RESET}    {C.GREEN}/math{C.RESET}      切换数学私教 (或直接输入「数学报到」/「学数学」)                 {C.CYAN}│{C.RESET}\n"
    )
    _native_cmds = (
        "「查漏」「交作业」「更新看板」「打卡」「组卷」「变式」「知识图谱」「整卷诊断」「减负」"
        if _math_off else
        "「数学报到」「查漏」「交作业」「更新看板」「打卡」「组卷」「变式」「知识图谱」「整卷诊断」「减负」"
    )
    print(f"""
{C.CYAN}╭── 🛠️ 考研私教智能终端 · 指令大盘 (Command Palette) ───────────────────────╮{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}🎓 学科专属私教路由与每日任务:{C.RESET}                                           {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.GREEN}/today{C.RESET}     查看四科今日必做任务清单与完成进度打钩 (或直接输入 /done <词>)   {C.CYAN}│{C.RESET}
{_math_route}{C.CYAN}│{C.RESET}    {C.GREEN}/eng{C.RESET}       切换英语私教 (或直接输入「英语报到」/「学英语」)                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.GREEN}/pol{C.RESET}       切换政治私教 (或直接输入「政治报到」/「学政治」)                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.GREEN}/pro{C.RESET}       切换专业课私教 (或直接输入「专业课报到」/「学专业课」)           {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}🧩 考研专有扩展技能 (Skills):{C.RESET}                                            {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/admission <校> [专业]{C.RESET}研招网与高校官方招考事实与证据链核验 (S/A级权威)    {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/watch [高校]{C.RESET}          跟踪目标高校研究生院最新简章与自命题动态指纹监控雷达      {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/compare <校1> <校2>{C.RESET}   双校招考核心指标横向深度对标 (408/自命题/复试线/保护)     {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/scout <高校> [专业]{C.RESET}目标院校招生简章、大纲、招生人数与知乎/B站口碑侦察   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/exam [科目]{C.RESET}  错题反向靶向组卷 (阶段自测盲盒试卷，支持导出与评分)       {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/variant <考点>{C.RESET}考研同类真题变式检索与防伪溯源 (优先白名单真题，严禁伪造)   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/map [科目]{C.RESET}   官方考纲知识点图谱与四维掌握度映射 (大纲/错题薄弱点对齐)  {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/diagnose <文本>{C.RESET}整卷级多题诊断与失分聚类引擎 (章节失分排行与个性化处方)      {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/diff [路径]{C.RESET}    考纲版本异动 Diff 与考点增删看板 (演示样例自动隔离)   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/ingest <路径>{C.RESET}  试题智能切片入库 (分块切片/采分点提取/白名单归档)     {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/review{C.RESET}        FSRS 错题盲盒重测 (隐去原答案，独立重做，通过后出库)   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/hint{C.RESET}          苏格拉底微步骤启发 (拒绝全解剧透，分级引导突破口)         {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/done <词>{C.RESET}     快速将今日任务标记为完成并同步回写文件                   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/batch{C.RESET}         客观题答题卡批量对题 (快速比对选项，统计正确率与错题归因) {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/img <路径>{C.RESET}      上传草稿纸或截图，逐行批改、采分点打分与 LaTeX 题干提取   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/calc <式子>{C.RESET}     数学高精度验算 (微分方程/二次型/级数/极限/微积分/矩阵)     {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/dissect <句>{C.RESET}    英语长难句搭积木解剖 (主干骨架/从句解构/考点词/润色翻译)   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/pdf [关键词]{C.RESET}    全文检索四科资料库中的官方教材与历年真题                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/skills{C.RESET}         查看当前已装载的所有技能详细清单与状态                    {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}🌐 前端联动与外设协同:{C.RESET}                                                  {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.CYAN}/view{C.RESET}          打开实时可视化网页伴侣 (印刷级 KaTeX 排版与双端同步)        {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.CYAN}/notify{C.RESET}        一键向微信、钉钉、飞书、QQ 群广播今日考研晨报与自测卡片    {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.CYAN}/build{C.RESET}         重新编译并刷新本地与手机自测看板 (或直接输入「更新看板」)  {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}⚙️ 终端管理与辅助:{C.RESET}                                                      {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/style [1-4]{C.RESET}     查看或动态切换 4 种私教辅导风格 (严格/秒杀/鼓励/溯源)      {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/doctor{C.RESET}          一键系统健康全链路体检 (环境/依赖/状态/连通性)              {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/fatigue{C.RESET}         查看疲劳度与完成率监控警报                                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/relieve{C.RESET}         一键启动智能减负模式 (任务下调 25%，切换为鼓励型)          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/memory [status|prune]{C.RESET}三级分层记忆健康度查看与滚动修剪                {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/rollback{C.RESET}         快速回滚 Plan Mode 上一次快照备份                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/plan{C.RESET}            个人专属定制化必考方案向导 (时间/考纲/白名单/学情摸底/作息) {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/status{C.RESET}          查看考研总战役大盘态势、倒计时与四科目标矩阵             {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/config{C.RESET}          分类多选管理菜单：配置大模型 API 与机器人 Webhook          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/clear{C.RESET}           清空当前会话上下文                                         {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/exit{C.RESET}            退出私教终端 (落盘记忆与会话钩子)                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.DIM}沙箱: 逻辑隔离（非 OS 沙箱）；读取工作区外文件需在弹卡中授权{C.RESET}            {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  💡 {C.BOLD}中文原生口令{C.RESET}: {_native_cmds} {C.CYAN}│{C.RESET}
{C.CYAN}╰──────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")

def print_status_summary() -> None:
    """打印考研总战役大盘态势、打卡 Streak 与周日休整关怀提示"""
    today_d = date.today()
    today_s = today_d.strftime("%Y-%m-%d")

    cfg = load_config()
    plan = cfg.get("study_plan", {})

    if exam_calendar:
        exam_d, exam_src = exam_calendar.resolve_exam_date(cfg)
    else:
        exam_d, exam_src = datetime(today_d.year, 12, 19).date(), "fallback"
    days_left = (exam_d - today_d).days

    hist = cfg.get("completion_history", {})
    streak = 0
    chk_d = today_d
    if today_s not in hist:
        chk_d = today_d - timedelta(days=1)
    while True:
        ds = chk_d.strftime("%Y-%m-%d")
        if ds in hist and (hist[ds].get("rate", 0.0) >= 60.0 or hist[ds].get("completed", 0) > 0):
            streak += 1
            chk_d -= timedelta(days=1)
        else:
            break

    weekday = today_d.weekday()
    if weekday == 6:
        rest_msg = f"{C.YELLOW}今日为周日！系统预定晚间 18:00~22:30 为休整放松窗口，适度给大脑减压，严防考前倦怠！{C.RESET}"
    else:
        days_to_sun = (6 - weekday) % 7
        rest_msg = f"距下次周日休整窗口（周日晚 18:00~22:30）还有 {C.BOLD}{days_to_sun}{C.RESET} 天，按部就班高效攻坚！"

    print(colorize(f"\n============================================================", C.CYAN))
    print(colorize(f"  🏆 考研总战役态势大盘 · 倒计时 {days_left} 天", C.BOLD))
    if days_left < 0:
        print(colorize(
            f"  ⚠ 配置中的初试日期 {exam_d.isoformat()} 已过期 {-days_left} 天，倒计时已失效。"
            f"请用 `ky plan` 重新确认初试日期。", C.YELLOW))
    elif exam_calendar and exam_src != exam_calendar.SOURCE_CONFIG:
        print(colorize(
            f"  [i] 配置中无明确初试日期，当前按「{exam_src}」取 {exam_d.isoformat()}"
            f"（12 月倒数第二个周六）；如需固定请用 `ky plan` 确认。", C.DIM))
    print(colorize(f"============================================================", C.CYAN))
    print(f"• 今日日期: {today_s} (初试首日: {exam_d.strftime('%Y-%m-%d')})")
    print(f"• 连续打卡: {C.GREEN}{streak} 天 (Streak 保持中){C.RESET}")
    print(f"• 作息节律: {rest_msg}")
    print("-" * 60)

    agents_root = ROOT / "AGENTS.md"
    if agents_root.exists():
        _cfg_school = (plan.get("school") or "").strip()
        _cfg_major = (plan.get("major") or "").strip()
        _override = {}
        if _cfg_school and _cfg_school not in ("目标院校", "未指定"):
            _override["- **目标院校**："] = f"- **目标院校**：`{_cfg_school}`"
        if _cfg_major and _cfg_major not in ("报考专业", "未指定"):
            _override["- **报考专业**："] = f"- **报考专业**：`{_cfg_major}`"

        _status_skip_prefixes = (
            "- **特点**：", "- **行为准则**：",
            "| **Google", "| **Cursor", "| **Trae", "| **Cherry",
            "| **WorkBuddy", "| **VS Code", "| **网页端",
        )
        txt = read_text_safe(agents_root)
        for line in txt.split("\n"):
            clean_l = line.strip()
            clean_l = clean_l.replace("（示例模板）", "").replace("(示例模板)", "")
            if clean_l.startswith(_status_skip_prefixes):
                continue
            for _pre, _repl in _override.items():
                if clean_l.startswith(_pre):
                    clean_l = _repl
                    break
            if clean_l.startswith(("- **", "| **科目", "| 合计", "| **", "- 数学:", "- 英语:", "- 政治:", "- 专业课:", "- 数学薄弱点:", "- 英语薄弱点:", "- 政治薄弱点:", "- 专业课薄弱点:")):
                print("  " + clean_l)
            elif clean_l.startswith(("### 【个性化", "### 一、各科")):
                print("\n  " + colorize(clean_l, C.BOLD))
    print(colorize("============================================================\n", C.CYAN))

def print_today_tasks_summary(as_json: bool = False, show_flash: bool = True) -> None:
    """读取并打印四科今日真实任务清单，支持终端全彩或结构化 JSON"""
    if as_json:
        print(json.dumps(get_today_tasks_data(), ensure_ascii=False, indent=2))
        return

    # [R2-A4 修复·不考数学贯穿] 科目列表改由 dashboard_state 的权威 specs 驱动
    # （与 `ky today --json`、看板同源），不再硬编码四科 —— 否则不考数学的文科
    # 考生会看到空的【数学】段落。取不到状态时回退为四科，保持既有行为。
    _display = {
        "math": ("01-数学", "数学", C.GREEN),
        "eng": ("02-英语", "英语", C.CYAN),
        "pol": ("03-思想政治理论", "思想政治理论", C.RED),
        "pro": ("04-专业课", "专业课", C.YELLOW),
    }
    try:
        _active_keys = list((get_today_tasks_data().get("subjects") or {}).keys())
    except Exception:
        _active_keys = []
    subjs = [_display[k] for k in ("math", "eng", "pol", "pro") if k in _active_keys]
    if not subjs:
        subjs = list(_display.values())
    try:
        # [P2-3 修复·研招速递噪音] 三道闸：① ky_config.json 开关
        # (study_plan.news_flash=false 关闭)；② --no-flash 单次关闭；
        # ③ 年份过滤（>2 年的旧公告直接丢弃）+ 每校最多 5 条。
        _flash_on = show_flash
        try:
            _cfg = load_config() or {}
            if (( _cfg.get("study_plan") or {}).get("news_flash") is False):
                _flash_on = False
        except Exception:
            pass
        if _flash_on and intelligence:
            import re as _re
            from datetime import datetime as _dt
            _keep_year = _dt.now().year - 1
            watcher = intelligence.AdmissionWatcher()
            if watcher.list_watched():
                findings = watcher.check_updates()
                updated_findings = [f for f in findings if f.get("status") == "UPDATED"]
                if updated_findings:
                    print(f"{C.RED}{C.BOLD}╭── 🔥 研招动态突发情报速递 (Admission News Flash) ────────────────────╮{C.RESET}")
                    for uf in updated_findings:
                        _titles = []
                        for tit in uf.get("alert_titles", []):
                            _years = [int(y) for y in _re.findall(r"(?<!\d)(20\d{2})(?!\d)", str(tit))]
                            if _years and max(_years) < _keep_year:
                                continue
                            _titles.append(tit)
                        if not _titles:
                            continue
                        print(f"{C.YELLOW}│{C.RESET}  📢 【{uf['school']}】官方研究生院发布最新招生变动：")
                        for tit in _titles[:5]:
                            print(f"{C.YELLOW}│{C.RESET}     • {tit}")
                        if len(_titles) > 5:
                            print(f"{C.YELLOW}│{C.RESET}     …等共 {len(_titles)} 条（仅展示最新 5 条）")
                    print(f"{C.RED}╰────────────────────────────────────────────────────────────────────────╯{C.RESET}\n")
    except Exception:
        pass

    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{C.CYAN}╭── 📋 今日全科复习任务清单 ({today_str}) ─────────────────────────╮{C.RESET}")
    for dir_name, label, color in subjs:
        task_file = ROOT / dir_name / "_状态" / "今日任务.md"
        if task_file.exists():
            content = read_text_safe(task_file)
            print(f"  {colorize(f'【{label}】', color)}")
            lines = [l.strip() for l in content.splitlines() if "|" in l and not l.replace(" ", "").startswith("|---|") and "完成状态" not in l and "模块" not in l]
            for line in lines:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 3:
                    status = f"{C.GREEN}[√]{C.RESET}" if "[x]" in parts[-1].lower() else f"{C.DIM}[ ]{C.RESET}"
                    print(f"    {status} {parts[0]} ({parts[2]}): {parts[1]}")
            print()
        else:
            print(f"  {colorize(f'【{label}】', color)}: 暂未生成今日任务，输入 /plan 一键生成。\n")
    print(f"{C.CYAN}╰────────────────────────────────────────────────────────────────────────╯{C.RESET}")
    # [R2-A4 修复] 提示口令必须指向真实可用的科目：不考数学时不得再写「数学报到」。
    _checkin = recommended_checkin_command(load_config())
    print(f"💡 开始学习口令: 输入 {C.GREEN}[科目]报到{C.RESET} (如「{_checkin}」) 立即由私教派题；完成输入 {C.YELLOW}交作业{C.RESET} 自动批改打分！\n")

def print_followup_toolbar() -> None:
    """打印输入后的快捷跟随操作工具栏"""
    print(f"\n{C.CYAN}╭──────────────────────────────────────────────────────────────────────────────────╮{C.RESET}")
    print(f"{C.CYAN}│{C.RESET}  {C.BOLD}💡 下一步:{C.RESET} /1 📐 符号验算  /2 📌 记错题  /3 🌐 网页伴侣  /4 🔄 变式演练  /5 💡 启发提示 {C.CYAN}│{C.RESET}")
    print(f"{C.CYAN}╰──────────────────────────────────────────────────────────────────────────────────╯{C.RESET}")
