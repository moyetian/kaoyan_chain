# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 一键构建并同步脚本 (跨平台)
用法：
  python tools/update_dashboard.py          # 编译看板并尝试 git push
  python tools/update_dashboard.py --local  # 仅本地编译看板，不提交 git
"""

import sys
import subprocess
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

def main():
    print("=" * 65)
    print(" 考研学习链 (Kaoyan AI Study Chain) · 看板更新与同步")
    print("=" * 65)

    # 1. 运行 build.py
    build_script = ROOT / "05-考研看板" / "build.py"
    if not build_script.exists():
        print("[-] 错误: 未找到 05-考研看板/build.py")
        sys.exit(1)

    print("\n[1/3] 正在解析四科状态并生成 Web 看板...")
    res = subprocess.run([sys.executable, str(build_script)], cwd=str(ROOT / "05-考研看板"))
    if res.returncode != 0:
        print("[!] 构建失败，请检查 Python 环境或语法。")
        sys.exit(1)

    if "--local" in sys.argv or "-l" in sys.argv:
        print("\n[OK] 本地构建完成（已跳过 Git 提交与推送）。")
        return

    # 2. Git 提交
    print("\n[2/3] 正在暂存并提交更新...")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    subprocess.run(["git", "add", "-A"], cwd=str(ROOT))
    commit_res = subprocess.run(["git", "commit", "-m", f"study-chain update {ts}"], cwd=str(ROOT))
    if commit_res.returncode != 0:
        print("  -> 本地无增量变更或已是最新状态。")

    # 3. Git 推送
    print("\n[3/3] 正在推送到远程仓库...")
    push_res = subprocess.run(["git", "push"], cwd=str(ROOT))
    if push_res.returncode == 0:
        print("\n[√] 成功推送至 GitHub！GitHub Pages 将在 1-2 分钟内自动刷新。")
    else:
        print("\n[!] 推送未执行成功。如尚未关联远程仓库，请先运行：")
        print("    git remote add origin https://github.com/<你的用户名>/<你的仓库>.git")
        print("    git branch -M main")
        print("    git push -u origin main")

    print("\n" + "=" * 65)

if __name__ == "__main__":
    main()
