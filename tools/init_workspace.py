# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 本地工作区初始化脚本
跨平台支持：Windows / macOS / Linux
无需任何第三方 pip 依赖，Python 3.8+ 标准库即可运行。
"""

import os
import sys
import shutil
import re
from pathlib import Path
from datetime import datetime

# Windows 控制台编码安全配置
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

def print_banner():
    print("=" * 65)
    print(" 考研学习链 (Kaoyan AI Study Chain) · 本地工作区初始化向导")
    print("=" * 65)

def copy_templates():
    """将所有 .template.md 拷贝为对应的 .md 工作文件（若已存在则不覆盖）"""
    print("\n[步骤 1/3] 扫描并初始化各科本地学情状态文件...")
    count = 0
    skipped = 0
    for p in ROOT.rglob("*.template.md"):
        if ".git" in p.parts:
            continue
        target = p.with_name(p.name.replace(".template.md", ".md"))
        if not target.exists():
            shutil.copy2(p, target)
            rel_path = target.relative_to(ROOT)
            print(f"  [+] 创建: {rel_path}")
            count += 1
        else:
            skipped += 1

    print(f"  -> 初始化完成: 新建 {count} 个状态文件，保持 {skipped} 个已有文件不变。")
    print("  -> 所有生成的 .md 个人学情文件均受 .gitignore 严密保护，绝不上传 GitHub！")

def configure_profile(interactive=True):
    """配置个人考研基本盘参数"""
    print("\n[步骤 2/3] 配置个人报考目标与基本盘...")
    agents_path = ROOT / "AGENTS.md"
    if not agents_path.exists():
        print("  [!] 未找到根目录 AGENTS.md，跳过配置更新。")
        return

    content = agents_path.read_text(encoding="utf-8")

    if not interactive:
        print("  -> 已使用默认配置（您可随时在 AGENTS.md 中手动修改）。")
        return

    print("  提示：直接回车可保留默认/待填值，随时可以在 AGENTS.md 中直接修改。\n")
    school = input("  1. 目标院校 [默认: 目标院校]: ").strip() or "目标院校"
    major = input("  2. 报考专业代码与名称 [如: 085400 电子信息]: ").strip() or "报考专业"
    year = input(f"  3. 考研年份 [默认: {datetime.now().year + 1}]: ").strip() or str(datetime.now().year + 1)
    math_target = input("  4. 数学目标分数 [默认: 110+ 分]: ").strip() or "110+ 分"
    eng_target = input("  5. 英语目标分数 [默认: 65+ 分]: ").strip() or "65+ 分"
    pol_target = input("  6. 政治目标分数 [默认: 70+ 分]: ").strip() or "70+ 分"
    pro_target = input("  7. 专业课目标分数 [默认: 120-130 分]: ").strip() or "120-130 分"

    content = re.sub(r"- \*\*目标院校\*\*：.*", f"- **目标院校**：`{school}`", content)
    content = re.sub(r"- \*\*报考专业\*\*：.*", f"- **报考专业**：`{major}`", content)
    content = re.sub(r"- \*\*初试日期\*\*：.*", f"- **初试日期**：`{year}-12-19`", content)
    
    content = re.sub(r"\|\s*\*\*科目一：数学\*\*.*", f"| **科目一：数学** | [摸底] 分 | **{math_target}** | 2.5 小时 (150分) | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |", content)
    content = re.sub(r"\|\s*\*\*科目二：英语\*\*.*", f"| **科目二：英语** | [摸底] 分 | **{eng_target}** | 2.0 小时 (120分) | 搭积木拆解长难句，定位阅读选项逻辑，固化作文功能句模板 |", content)
    content = re.sub(r"\|\s*\*\*科目三：政治\*\*.*", f"| **科目三：政治** | [摸底] 分 | **{pol_target}** | 1.0 小时 (60分) | 单选+多选得分盘（38~42分），帽子词秒杀，后期背诵闭环 |", content)
    content = re.sub(r"\|\s*\*\*科目四：专业课\*\*.*", f"| **科目四：专业课** | [摸底] 分 | **{pro_target}** | 3.0 小时 (180分) | 权威教材体系+历年真题深度解剖，白名单题源抽题门禁 |", content)

    agents_path.write_text(content, encoding="utf-8")
    print(f"  [√] 已将目标更新至 AGENTS.md ({school} / {major})")

def build_dashboard():
    """编译看板并生成 docs/index.html"""
    print("\n[步骤 3/3] 编译个人移动端自测看板...")
    build_script = ROOT / "05-考研看板" / "build.py"
    if build_script.exists():
        import subprocess
        res = subprocess.run([sys.executable, str(build_script)], cwd=str(ROOT / "05-考研看板"), capture_output=True, text=True, encoding="utf-8")
        if res.returncode == 0:
            print(f"  [√] 看板生成成功！输出路径: {ROOT / 'docs' / 'index.html'}")
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
    configure_profile(interactive=interactive)
    build_dashboard()

    print("\n" + "=" * 65)
    print(" [√] 本地工作区初始化完成！")
    print("=" * 65)
    print(" 下一步操作指南：")
    print(" 1. 打开本地看板预览: 双击 docs/index.html")
    print(" 2. 启动 AI 私教对话: 在编辑器/终端中打开本目录，输入「数学报到」或「英语报到」")
    print(" 3. 每日晚间更新看板: 运行 python tools/update_dashboard.py 或双击 更新看板.bat")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
