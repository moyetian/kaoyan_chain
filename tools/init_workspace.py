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

STYLES = {
    "1": ("1. 严格把关·保姆提分型 (Strict & Disciplined)", "以真题阅卷人严苛视角分步赋分，计算失误与跳步零容忍，强制错因五分类归因"),
    "2": ("2. 高效应试·高频秒杀型 (High-Yield Hacker)", "以 80/20 法则为最高导向，只抓核心得分盘，教授秒杀口诀与解题模板"),
    "3": ("3. 温和启发·减负鼓励型 (Encouraging Mentor)", "微步提示，通俗生活比喻解释抽象概念，正向激励，降低挫败感"),
    "4": ("4. 深度原理·学霸溯源型 (Deep Conceptual Master)", "追根溯源定理底层证明与几何背景，打通跨学科知识图谱，学霸精研"),
}

AGENTS = {
    "1": ("Google Antigravity", "全能 AI IDE，原生多智能体协同调度与工作区记忆感知 [推荐]"),
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
    print("\n[步骤 1/4] 扫描并初始化本地学情状态文件...")
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
    print("\n[步骤 2/4] 初始化各科学习资料库与考纲挂载目录...")
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
            
            outline_file = s_dir / "考试大纲.md"
            if not outline_file.exists():
                outline_file.write_text(
                    f"# {s} · 核心考纲与知识点清单（模板）\n\n"
                    "> 在此处填入你的官方考试大纲要求（标明：掌握 / 理解 / 了解），AI 教师派题与诊断时将严格遵循此考纲，严防超纲！\n\n"
                    "## 章节考纲重点\n"
                    "- 第一章：[考纲核心要点]\n"
                    "- 第二章：[考纲核心要点]\n",
                    encoding="utf-8"
                )
    print("  [√] 已为数学、英语、政治、专业课创建「参考资料/」与「考试大纲.md」模板。")

def configure_profile(interactive=True):
    """配置个人报考目标、私教风格与主用 Agent 工具"""
    print("\n[步骤 3/4] 个性化参数配置（报考目标、辅导风格、Agent 选择）...")
    agents_path = ROOT / "AGENTS.md"
    if not agents_path.exists():
        print("  [!] 未找到根目录 AGENTS.md，跳过配置更新。")
        return

    content = agents_path.read_text(encoding="utf-8")

    if not interactive:
        print("  -> 已启用默认配置（严格保姆提分流 + 默认目标），可随时在 AGENTS.md 中调整。")
        return

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

    content = re.sub(r"- \*\*目标院校\*\*：.*", f"- **目标院校**：`{school}`", content)
    content = re.sub(r"- \*\*报考专业\*\*：.*", f"- **报考专业**：`{major}`", content)
    content = re.sub(r"- \*\*初试日期\*\*：.*", f"- **初试日期**：`{year}-12-19`", content)
    content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*", f"- **当前激活辅导风格**：`{style_name}`", content)
    
    content = re.sub(r"\|\s*\*\*科目一：数学\*\*.*", f"| **科目一：数学** | [摸底] 分 | **{math_target}** | 2.5 小时 (150分) | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |", content)
    content = re.sub(r"\|\s*\*\*科目二：英语\*\*.*", f"| **科目二：英语** | [摸底] 分 | **{eng_target}** | 2.0 小时 (120分) | 搭积木拆解长难句，定位阅读选项逻辑，固化作文功能句模板 |", content)
    content = re.sub(r"\|\s*\*\*科目三：政治\*\*.*", f"| **科目三：政治** | [摸底] 分 | **{pol_target}** | 1.0 小时 (60分) | 单选+多选得分盘（38~42分），帽子词秒杀，后期背诵闭环 |", content)
    content = re.sub(r"\|\s*\*\*科目四：专业课\*\*.*", f"| **科目四：专业课** | [摸底] 分 | **{pro_target}** | 3.0 小时 (180分) | 权威教材体系+历年真题深度解剖，白名单题源抽题门禁 |", content)

    agents_path.write_text(content, encoding="utf-8")
    print(f"\n  [√] 已将目标更新至 AGENTS.md:")
    print(f"      - 院校与专业: {school} / {major}")
    print(f"      - 辅导风格: {style_name}")
    print(f"      - 主力 Agent: {agent_name}")

    ask_bot = input("\n  8. 是否需要现在配置微信/QQ/钉钉/飞书等群机器人推送? (y/n) [n]: ").strip().lower()
    if ask_bot == "y":
        try:
            import ky_cli
            ky_cli.configure_webhooks(ky_cli.load_config())
        except Exception as e:
            print(f"  [!] Webhook 配置提示: {e}")

def build_dashboard():
    """编译看板并生成 docs/index.html"""
    print("\n[步骤 4/4] 编译个人移动端自测看板...")
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
    configure_profile(interactive=interactive)
    build_dashboard()

    print("\n" + "=" * 68)
    print(" 🎉 本地工作区全流程初始化成功！")
    print("=" * 68)
    print(" 快速操作指引：")
    print(" 1. 放入学习资料: 将参考教材/大纲放入对应科目的「参考资料/」文件夹")
    print(" 2. 预览移动端看板: 双击打开 docs/index.html")
    print(" 3. 启动私教学习: 在你所选的 Agent 中发送「数学报到」或「英语报到」")
    print(" 4. 每日晚间更新: 运行 python tools/update_dashboard.py 或双击 更新看板.bat")
    print("=" * 68 + "\n")

if __name__ == "__main__":
    main()
