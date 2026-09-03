# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 本地工作区初始化向导
跨平台支持：Windows / macOS / Linux
无需任何第三方 pip 依赖，Python 3.8+ 标准库即可运行。
"""

import os
import sys
import shutil
import re
from pathlib import Path
from datetime import datetime

# Windows 控制台编码重配置
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

# 引入考纲管理模块
sys.path.insert(0, str(ROOT / "tools"))
try:
    import syllabus_manager
except ImportError:
    syllabus_manager = None

STYLES = {
    "1": ("1. 严格把关·保姆提分型 (Strict & Disciplined)", "以真题阅卷人严苛视角分步赋分，计算失误与跳步零容忍，强制错因五分类归因"),
    "2": ("2. 高效应试·高频秒杀型 (High-Yield Hacker)", "以 80/20 法则为最高导向，只抓核心得分盘，教授秒杀口诀与解题模板"),
    "3": ("3. 温和启发·减负鼓励型 (Encouraging Mentor)", "微步提示，通俗生活比喻解释抽象概念，正向激励，降低挫败感"),
    "4": ("4. 深度原理·学霸溯源型 (Deep Conceptual Master)", "追根溯源定理底层证明与几何背景，打通跨学科知识图谱，学霸精研"),
}

AGENTS = {
    "1": ("Google Antigravity", "全能 AI IDE，原生多智能体协同调度与工作区记忆感知"),
    "2": ("Cursor", "AI 代码与文本编辑器，内置 .cursorrules，Composer 模式极速批改"),
    "3": ("Trae (字节跳动)", "自适应 AI IDE，中文语境流畅，Builder 模式交互直观"),
    "4": ("Cherry Studio", "桌面多模型聚合客户端，支持导入本地知识库与自定义系统提示词"),
    "5": ("WorkBuddy", "国内桌面级自动化智能体，支持工作流编排与定时任务触发"),
    "6": ("VS Code (Roo Code / Cline)", "开源插件生态，模型切换自如，支持本地 Ollama / DeepSeek API"),
    "7": ("网页端大模型 (ChatGPT / Claude / DeepSeek)", "零安装门槛，浏览器即开即用，适合手机与平板"),
}

def print_banner():
    print("=" * 68)
    print("  考研学习链 (Kaoyan AI Study Chain) · 本地工作区全能配置向导")
    print("=" * 68)

def copy_templates():
    """将所有 .template.md 拷贝为对应的 .md 工作文件（若已存在则不覆盖）"""
    print("\n[步骤 1/5] 扫描并初始化本地学情状态文件...")
    count = 0
    skipped = 0
    for p in ROOT.rglob("*.template.md"):
        if ".git" in p.parts:
            continue
        target = p.with_name(p.name.replace(".template.md", ".md"))
        if not target.exists():
            shutil.copy2(p, target)
            rel_path = target.relative_to(ROOT)
            print(f"  [+] 初始化: {rel_path}")
            count += 1
        else:
            skipped += 1

    print(f"  -> 初始化完成: 新建 {count} 个状态文件，保持 {skipped} 个已有文件不变。")
    print("  -> 所有生成的 .md 个人学情文件均受 .gitignore 严密保护，绝不上传 GitHub！")

def ensure_material_folders():
    """为各科初始化参考资料目录与考纲模板"""
    print("\n[步骤 2/5] 初始化各科本地资料库架构...")
    subjects = ["01-数学", "02-英语", "03-思想政治理论", "04-专业课"]
    for s in subjects:
        s_dir = ROOT / s
        if s_dir.exists():
            ref_dir = s_dir / "参考资料"
            ref_dir.mkdir(exist_ok=True)
            readme_ref = ref_dir / "README.md"
            if not readme_ref.exists():
                readme_ref.write_text(
                    f"# {s} · 本地参考资料库\n\n"
                    "> [!NOTE]\n"
                    "> 本目录已被 `.gitignore` 全面忽略，大体积教材 PDF、历年真题扫描件、个人笔记资料均可安心存放于此，绝不会泄露至 GitHub！\n\n"
                    "## 建议存放内容：\n"
                    "1. 官方指定教材电子版 / 课后习题答案扫描件\n"
                    "2. 权威大纲解析 / 历年真题试卷\n"
                    "3. 核心公式表或个人提纲\n",
                    encoding="utf-8"
                )
    print("  [√] 已为数学、英语、政治、专业课创建私密「参考资料/」文件夹。")

def choose_exam_subjects_and_syllabi(interactive=True):
    """
    [步骤 3/5] 引导学员精细选择考试科目并自动载入官方标准考纲
    """
    print("\n[步骤 3/5] 🎓 考研科目精细选择与官方考纲自动加载...")
    if not interactive or syllabus_manager is None:
        math_info, eng_info, _ = syllabus_manager.apply_syllabus_selection(
            math_key="math2", eng_key="eng2", pro_type="custom", pro_name="专业课"
        )
        print("  -> 已自动加载默认官方考纲: 数学二 (302) + 英语二 (204) + 101政治。")
        return "math2", "eng2", "custom", "专业课"

    print("  [说明] 选定具体科目后，系统将自动把教育部考试中心发布的【官方权威考纲】载入各科目录，严控出题边界，防止超纲！\n")

    # 1. 数学科目选择
    print("  --- 📐 请选择您的数学考试科目 ---")
    print("    [1] 数学二 (302) [高数78% + 线代22%，严禁概率/级数/曲面积分/三重积分] (专硕主流)")
    print("    [2] 数学一 (301) [高数56% + 线代22% + 概率22%，考查范围最广/全套微积分] (学硕为主)")
    print("    [3] 数学三 (303) [微积分56% + 线代22% + 概率22%，偏重经济应用/差分方程] (经管类)")
    print("    [4] 396 经济类综合能力数学 [微积分+线代+概率，单项选择题与计算题]")
    print("    [5] 院校自主命题数学 / 不考数学")
    m_c = input("  请选择数学科目 (1~5) [默认 1]: ").strip() or "1"
    math_map = {"1": "math2", "2": "math1", "3": "math3", "4": "math396", "5": "custom"}
    math_key = math_map.get(m_c, "math2")

    # 2. 英语科目选择
    print("\n  --- 📖 请选择您的英语考试科目 ---")
    print("    [1] 英语二 (204) [专硕为主，整段段落英译汉 15分 + 图表数据大作文 15分] (专硕主流)")
    print("    [2] 英语一 (201) [学硕为主，5大高难长难句精译 10分 + 图画哲理漫画大作文 20分]")
    print("    [3] 单独命题英语 / 其他外语 (日语/俄语等)")
    e_c = input("  请选择英语科目 (1~3) [默认 1]: ").strip() or "1"
    eng_map = {"1": "eng2", "2": "eng1", "3": "custom"}
    eng_key = eng_map.get(e_c, "eng2")

    # 3. 政治科目
    print("\n  --- 🚩 政治理论科目 ---")
    print("    ✔ 默认自动载入【全国统考 101 思想政治理论】五大模块核心考纲与答题框架。")

    # 4. 专业课科目选择
    print("\n  --- 💻 请选择您的专业课方案 ---")
    print("    [1] 高校自命题专业课 (自主输入科目代码与名称，如 801信号与系统 / 832数据结构等)")
    print("    [2] 全国统考 408 计算机学科专业基础 (自动载入数据结构/计组/OS/计网四大模块大纲)")
    print("    [3] 全国统考 199 管理类综合能力")
    p_c = input("  请选择专业课类别 (1~3) [默认 1]: ").strip() or "1"
    pro_type = "408" if p_c == "2" else ("199" if p_c == "3" else "custom")
    if pro_type == "408":
        pro_name = "408 计算机学科专业基础"
    elif pro_type == "199":
        pro_name = "199 管理类综合能力"
    else:
        pro_name = input("  请输入您的专业课代码与名称 [如 801 信号与系统]: ").strip() or "专业课"

    return math_key, eng_key, pro_type, pro_name

def configure_profile(interactive=True, math_key="math2", eng_key="eng2", pro_type="custom", pro_name="专业课"):
    """
    [步骤 4/5] 配置个人报考目标、辅导风格、Agent 选择与目标分数矩阵
    """
    print("\n[步骤 4/5] 个性化参数配置（报考目标、辅导风格、Agent 选择）...")
    agents_path = ROOT / "AGENTS.md"
    if not agents_path.exists():
        print("  [!] 未找到根目录 AGENTS.md，跳过配置更新。")
        return

    content = agents_path.read_text(encoding="utf-8")

    school = "目标院校"
    major = "报考专业"
    year = str(datetime.now().year + 1)
    style_name = STYLES["1"][0]
    agent_name = AGENTS["1"][0]
    math_target = "110+ 分"
    eng_target = "65+ 分"
    pol_target = "70+ 分"
    pro_target = "120-130 分"

    if interactive:
        print("  [提示] 直接回车可保留括号内的默认建议值：\n")
        school = input("  1. 目标院校 [默认: 目标院校]: ").strip() or "目标院校"
        major = input("  2. 报考专业与代码 [如: 085400 电子信息]: ").strip() or "报考专业"
        year = input(f"  3. 考研年份 [默认: {datetime.now().year + 1}]: ").strip() or str(datetime.now().year + 1)
        
        print("\n  --- 请选择您希望 AI 私教采取的辅导风格 ---")
        for k, (name, desc) in STYLES.items():
            print(f"    [{k}] {name}\n        -> {desc}")
        style_choice = input("  选择风格 (1/2/3/4) [默认 1]: ").strip() or "1"
        style_name = STYLES.get(style_choice, STYLES["1"])[0]

        print("\n  --- 请选择您日常主要使用的 AI Agent 工具 ---")
        for k, (name, desc) in AGENTS.items():
            print(f"    [{k}] {name:<28} : {desc}")
        agent_choice = input("  选择主要 Agent (1~7) [默认 1]: ").strip() or "1"
        agent_name = AGENTS.get(agent_choice, AGENTS["1"])[0]

        math_target = input("\n  4. 数学目标分数 [默认: 110+ 分]: ").strip() or "110+ 分"
        eng_target = input("  5. 英语目标分数 [默认: 65+ 分]: ").strip() or "65+ 分"
        pol_target = input("  6. 政治目标分数 [默认: 70+ 分]: ").strip() or "70+ 分"
        pro_target = input("  7. 专业课目标分数 [默认: 120-130 分]: ").strip() or "120-130 分"

    # 执行考纲写入与各科协议联动更新
    if syllabus_manager:
        math_info, eng_info, updated = syllabus_manager.apply_syllabus_selection(
            math_key=math_key, eng_key=eng_key, pro_type=pro_type, pro_name=pro_name,
            school=school, major=major, auto_write=True
        )
        print(f"\n  [√] 官方考纲已全量绑定到各科目录：")
        print(f"      - 数学考纲: {math_info['name']} -> 01-数学/考试大纲.md")
        print(f"      - 英语考纲: {eng_info['name']} -> 02-英语/考试大纲.md")
        print(f"      - 政治考纲: 101 思想政治理论 -> 03-思想政治理论/考试大纲.md")
        print(f"      - 专业课考纲: {pro_name} -> 04-专业课/考试大纲.md")
    else:
        math_info = {"name": "数学"}
        eng_info = {"name": "英语"}

    # 重新读取并更新根目录 AGENTS.md
    content = agents_path.read_text(encoding="utf-8")
    content = re.sub(r"- \*\*目标院校\*\*：.*", f"- **目标院校**：`{school}`", content)
    content = re.sub(r"- \*\*报考专业\*\*：.*", f"- **报考专业**：`{major}`", content)
    content = re.sub(r"- \*\*初试日期\*\*：.*", f"- **初试日期**：`{year}-12-19`", content)
    content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*", f"- **当前激活辅导风格**：`{style_name}`", content)
    
    m_name = math_info.get("name", "数学")
    e_name = eng_info.get("name", "英语")
    content = re.sub(r"\|\s*\*\*科目一.*", f"| **科目一：{m_name}** | [摸底] 分 | **{math_target}** | 2.5 小时 (150分) | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |", content)
    content = re.sub(r"\|\s*\*\*科目二.*", f"| **科目二：{e_name}** | [摸底] 分 | **{eng_target}** | 2.0 小时 (120分) | 搭积木拆解长难句，定位阅读选项逻辑，固化作文功能句模板 |", content)
    content = re.sub(r"\|\s*\*\*科目三.*", f"| **科目三：思想政治理论** | [摸底] 分 | **{pol_target}** | 1.0 小时 (60分) | 单选+多选得分盘（38~42分），帽子词秒杀，后期背诵闭环 |", content)
    content = re.sub(r"\|\s*\*\*科目四.*", f"| **科目四：{pro_name}** | [摸底] 分 | **{pro_target}** | 3.0 小时 (180分) | 权威教材体系+历年真题深度解剖，白名单题源抽题门禁 |", content)

    agents_path.write_text(content, encoding="utf-8")
    print(f"  [√] 已将目标矩阵更新至 AGENTS.md:")
    print(f"      - 院校与专业: {school} / {major}")
    print(f"      - 选考科目: {m_name} + {e_name} + 政治 + {pro_name}")
    print(f"      - 辅导风格: {style_name}")
    print(f"      - 主力 Agent: {agent_name}")

    if interactive:
        ask_bot = input("\n  8. 是否需要现在配置微信/QQ/钉钉/飞书等群机器人推送? (y/n) [n]: ").strip().lower()
        if ask_bot == "y":
            try:
                import ky_cli
                ky_cli.configure_webhooks(ky_cli.load_config())
            except Exception as e:
                print(f"  [!] Webhook 配置提示: {e}")

def build_dashboard():
    """编译看板并生成 docs/index.html"""
    print("\n[步骤 5/5] 编译个人移动端自测看板...")
    build_script = ROOT / "05-考研看板" / "build.py"
    if build_script.exists():
        import subprocess
        res = subprocess.run([sys.executable, str(build_script)], cwd=str(ROOT / "05-考研看板"), capture_output=True, text=True, encoding="utf-8")
        if res.returncode == 0:
            print(f"  [√] 看板编译成功！输出路径: {ROOT / 'docs' / 'index.html'}")
        else:
            print(f"  [!] 看板生成提示:\n{res.stderr or res.stdout}")
    else:
        print("  [!] 未找到 05-考研看板/build.py，跳过编译。")

def main():
    print_banner()
    interactive = True
    if "--quick" in sys.argv or "-q" in sys.argv or "-y" in sys.argv:
        interactive = False

    copy_templates()
    ensure_material_folders()
    math_key, eng_key, pro_type, pro_name = choose_exam_subjects_and_syllabi(interactive=interactive)
    configure_profile(interactive=interactive, math_key=math_key, eng_key=eng_key, pro_type=pro_type, pro_name=pro_name)
    build_dashboard()

    print("\n" + "=" * 68)
    print(" 🎉 本地工作区全流程初始化成功！")
    print("=" * 68)
    print(" 快速操作指引：")
    print(" 1. 查阅官方考纲: 打开 各科目/考试大纲.md (已自动注入选考科目的权威考点清单)")
    print(" 2. 放入学习资料: 将参考教材/真题放入对应科目的「参考资料/」文件夹")
    print(" 3. 启动私教学习: 在终端运行 ky 或在所选 Agent 中发送「数学报到」")
    print(" 4. 预览自测看板: 双击打开 docs/index.html (支持手机添加到主屏幕)")
    print("=" * 68 + "\n")

if __name__ == "__main__":
    main()
