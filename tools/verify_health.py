#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考研学习链项目健康度与完整性检查工具
用法：python tools/verify_health.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUBJECTS = [
    {
        "code": "01-数学",
        "name": "数学",
        "target": "110分",
        "must_have": [
            "AGENTS.md",
            "_状态/今日任务.md",
            "_状态/学员档案.md",
            "_状态/薄弱点雷达.md",
            "_状态/当前进度.md",
            "错题本",
        ]
    },
    {
        "code": "02-英语",
        "name": "英语",
        "target": "60分(冲65+)",
        "must_have": [
            "AGENTS.md",
            "_状态/今日任务.md",
            "_状态/学员档案.md",
            "_状态/薄弱点雷达.md",
            "_状态/当前进度.md",
            "作文语料库",
        ]
    },
    {
        "code": "03-思想政治理论",
        "name": "思想政治理论",
        "target": "70分",
        "must_have": [
            "AGENTS.md",
            "_状态/今日任务.md",
            "_状态/学员档案.md",
            "_状态/核心速记_帽子词与历史节点.md",
            "_状态/薄弱点雷达.md",
            "错题本",
        ]
    },
    {
        "code": "04-专业课",
        "name": "专业课 (自命题/统考)",
        "target": "120-130分",
        "must_have": [
            "AGENTS.md",
            "学情档案.md",
            "03_题源核验与抽题协议模板.md",
            "错题本",
            "每日作业",
        ]
    },
]

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 65)
    print(" 考研学习链 (Kaoyan Study Chain) - 项目健康度与规范检查")
    print("=" * 65)

    all_passed = True

    # 1. 根目录关键文件
    root_must = ["README.md", "AGENTS.md", "GEMINI.md", "LICENSE", ".gitignore", "docs/index.html"]
    print("\n[1] 检查根目录关键文件与配置:")
    for f in root_must:
        p = PROJECT_ROOT / f
        if p.exists():
            print(f"  [√] {f:<25} 存在 ({p.stat().st_size / 1024:.1f} KB)")
        else:
            print(f"  [X] {f:<25} 缺失!")
            all_passed = False

    # 2. 四科架构与状态文件检查
    print("\n[2] 检查四科私教规范与状态记忆:")
    for sub in SUBJECTS:
        sub_dir = PROJECT_ROOT / sub["code"]
        print(f"\n  >> 科目: {sub['name']} ({sub['code']}) [目标: {sub['target']}]")
        if not sub_dir.exists():
            print(f"     [X] 目录不存在: {sub_dir}")
            all_passed = False
            continue

        for item in sub["must_have"]:
            item_path = sub_dir / item
            found = False
            if item_path.exists():
                kind = "目录" if item_path.is_dir() else "文件"
                print(f"     [√] {item:<30} ({kind})")
                found = True
            elif item.endswith(".md"):
                for ext in (".template.md", ".example.md"):
                    example_path = sub_dir / item.replace(".md", ext)
                    if example_path.exists():
                        print(f"     [√] {item:<30} (模板: {ext})")
                        found = True
                        break
            
            if not found:
                print(f"     [X] 缺失必选资产: {item}")
                all_passed = False

    # 3. 看板可编译性检查
    print("\n[3] 检查看板构建系统:")
    build_script = PROJECT_ROOT / "05-考研看板" / "build.py"
    if build_script.exists():
        print(f"  [√] 05-考研看板/build.py 存在 ({build_script.stat().st_size / 1024:.1f} KB)")
    else:
        print(f"  [X] 05-考研看板/build.py 缺失!")
        all_passed = False

    print("\n" + "=" * 65)
    if all_passed:
        print(" [√ 全部检查通过] 项目结构规范，四科私教记忆与状态完整，可直接推送 GitHub！")
    else:
        print(" [!] 发现缺失项，请检查上述标记为 [X] 的文件。")
    print("=" * 65)

if __name__ == "__main__":
    main()