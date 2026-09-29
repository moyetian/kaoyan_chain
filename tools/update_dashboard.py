# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 一键构建并同步脚本 (跨平台)
用法：
  python tools/update_dashboard.py                 # 仅本地编译看板（完整模式）
  python tools/update_dashboard.py --local         # 显式指定仅本地编译
  python tools/update_dashboard.py --push          # 先脱敏构建发布产物，提交推送后再恢复本地完整模式
"""

import os
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


def _run_build(sanitized: bool) -> int:
    """运行 05-考研看板/build.py 并返回其退出码。

    [W13 验收修复·发布链路] 显式传 KY_SNAPSHOT_OPT_IN：
      * False（本地完整模式，入口默认）→ env=0，看板展示今日任务正文；
      * True （发布脱敏模式，--push 专用）→ env=1，剥离私人学习记录。
    此前两处都不传 env，构建走 snapshot_opt_in() 的缺省值（=脱敏），
    本地考生打开看板看不到任务正文 —— 本地体验被发布策略误伤。
    """
    build_script = ROOT / "05-考研看板" / "build.py"
    if not build_script.exists():
        print("[-] 错误: 未找到 05-考研看板/build.py")
        sys.exit(1)
    env = {**os.environ, "KY_SNAPSHOT_OPT_IN": "1" if sanitized else "0"}
    res = subprocess.run([sys.executable, str(build_script)],
                         cwd=str(ROOT / "05-考研看板"), env=env)
    return res.returncode


def main():
    print("=" * 65)
    print(" 考研学习链 (Kaoyan AI Study Chain) · 看板更新与同步")
    print("=" * 65)

    push_mode = ("--push" in sys.argv and "--local" not in sys.argv
                 and "-l" not in sys.argv)

    if not push_mode:
        print("\n[1/3] 正在解析四科状态并生成 Web 看板（本地完整模式）...")
        if _run_build(sanitized=False) != 0:
            print("[!] 构建失败，请检查 Python 环境或语法。")
            sys.exit(1)
        print("\n[OK] 本地构建完成（已跳过 Git 提交与推送）。")
        return

    # [W13 验收修复·发布链路] --push 必须先以脱敏模式构建发布产物（私人学习
    # 记录不得随 Pages 公开），提交/推送完成后在 finally 里恢复本地完整模式。
    print("\n[1/3] 正在以脱敏模式构建发布用 Web 看板...")
    if _run_build(sanitized=True) != 0:
        print("[!] 构建失败，请检查 Python 环境或语法。")
        sys.exit(1)

    push_ok = False
    try:
        # 2. Git 提交
        print("\n[2/3] 正在暂存并提交更新...")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        # [P0 修复] 显式白名单，杜绝隐私文件被顺带提交
        allowed = ["docs/index.html", "docs/live.html", "docs/assets/", "docs/state_snapshot.json"]
        valid_allowed = [p for p in allowed if (ROOT / p).exists()]
        if not valid_allowed:
            # [G8 修复] 无白名单产物时不得构造 ["git","commit","-m",msg,"--"] 这种畸形命令
            print("  -> 未找到任何白名单产物，跳过提交与推送。")
            print("\n" + "=" * 65)
            return
        subprocess.run(["git", "add", *valid_allowed], cwd=str(ROOT), check=True)
        # [G8 修复] commit 必须带 pathspec（-- 之后的白名单），只提交看板产物，
        # 避免把用户此前手动 `git add` 的其它文件一并提交。
        commit_res = subprocess.run(
            ["git", "commit", "-m", f"study-chain update {ts}", "--", *valid_allowed],
            cwd=str(ROOT),
        )
        if commit_res.returncode == 0:
            # 3. Git 推送（仅在 commit 成功时执行）
            print("\n[3/3] 正在推送到远程仓库...")
            push_res = subprocess.run(["git", "push"], cwd=str(ROOT))
            if push_res.returncode == 0:
                push_ok = True
                print("\n[√] 成功推送至 GitHub！GitHub Pages 将在 1-2 分钟内自动刷新。")
            else:
                print("\n[!] 推送未执行成功。如尚未关联远程仓库，请先运行：")
                print("    git remote add origin https://github.com/<你的用户名>/<你的仓库>.git")
                print("    git branch -M main")
                print("    git push -u origin main")
        else:
            # [G8 修复] 无增量（commit 失败）时不再无条件推送
            print("  -> 本地无增量变更或已是最新状态。")
            print("  -> 无增量变更，跳过推送。")
    finally:
        # [W13 验收修复·发布链路] 无论提交/推送结果如何，都把本地产物恢复为
        # 完整模式（发布用的脱敏版已随提交/镜像离开本机，本地回到完整体验）。
        print("\n[4/4] 正在恢复本地完整模式看板...")
        if _run_build(sanitized=False) != 0:
            print("[!] 本地完整模式重建失败，请手动重跑 05-考研看板/build.py。")

    if push_ok:
        print("[√] 已推送脱敏版；本地看板已恢复完整模式。")
    else:
        print("[i] 本地看板已恢复完整模式（提交的 docs/ 为脱敏版）。")
    print("\n" + "=" * 65)

if __name__ == "__main__":
    main()
