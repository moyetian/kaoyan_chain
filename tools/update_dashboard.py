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

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from datetime import datetime

# Windows 控制台编码重配置
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = resolve_workspace_root(__file__)

# 双导入路径兼容（脚本直跑 / pytest / 包内导入）：内容级脱敏与残留自检的
# 引擎/规则表都在 privacy_policy（单一事实源，与 sync_publish 同源复用）。
try:  # pragma: no cover - 取决于运行方式
    import privacy_policy as _pp
except ImportError:  # pragma: no cover
    from tools import privacy_policy as _pp  # type: ignore

try:  # pragma: no cover - 取决于运行方式
    from ky_io import atomic_write_text
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # type: ignore

#: 随 GitHub Pages 发布的产物白名单（git add/commit 与推送前脱敏共用同一份）。
PUSH_PRODUCTS = ("docs/index.html", "docs/live.html", "docs/assets/",
                 "docs/state_snapshot.json")

#: 推送前内容级脱敏的对象：**本次构建产出的页面与快照**。
#: 刻意不含静态 assets —— 它们不是构建产物，改写会污染工作区、并与
#: build_svg_assets.py 等生成脚本漂移（测试钉住「配图与源脚本同源」）；
#: 静态资产里若出现**当前身份**，由残留自检（全树）兜底阻断。
_SANITIZE_PRODUCT_RELS = ("docs/index.html", "docs/live.html",
                          "docs/state_snapshot.json")


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
    if sanitized:
        env.pop("KY_DASHBOARD_OUTPUT_DIR", None)
    else:
        # 完整快照含私人学情，普通本地构建固定写入未跟踪目录，避免被误发布。
        env["KY_DASHBOARD_OUTPUT_DIR"] = "docs/.local"
    res = subprocess.run([sys.executable, str(build_script)],
                         cwd=str(ROOT / "05-考研看板"), env=env)
    return res.returncode


def _sanitize_publish_products(root: Path) -> int:
    """[P1-11 修复·审计 2026-09-30] 对将发布的构建产物做**内容级脱敏**。

    直推链路（``--push``）此前只做「脱敏构建」（剥离私人学习记录正文），不做
    内容级身份替换 —— 卡片正面手写的校名 / 科目组合会明文进 GitHub Pages。
    本函数复用 privacy_policy 的引擎与 ``sync_publish.sanitize_markdown_files``
    同一套口径（同一规则表、同一 ``*.py`` 规则边界），作用对象是
    ``_SANITIZE_PRODUCT_RELS``（本次构建的页面与快照）。

    与 sync_publish 的两点显式差异（均为场景所迫，不是口径漂移）：
      * 作用范围收窄到构建产物 —— 静态 assets 会被推送流程原样保留（改写它们
        会污染工作区并与生成脚本漂移）；静态资产里的当前身份由全树自检兜底；
      * ``docs/state_snapshot.json`` 被显式纳入 —— 它是 Pages 部署的真相源、
        会被推送（而 sync_publish 镜像时整文件排除它），实测其卡片正面与
        科目名可残留真实身份；它不是公开数据库，不触碰「``*.json`` 不脱敏」
        这条为 ``data/universities`` 设立的红线。

    返回被改写的文件数。
    """
    md_rules = _pp.build_substitutions(root)
    py_rules = _pp.build_py_substitutions(root)
    changed = 0
    for rel in _SANITIZE_PRODUCT_RELS:
        f = root / rel
        if not f.is_file():
            continue
        patterns, line_rules = (py_rules, False) if f.suffix == ".py" else (md_rules, True)
        new = _pp.sanitize_file_text(f, patterns, line_rules=line_rules)
        if new is not None:
            atomic_write_text(f, new, encoding="utf-8")
            changed += 1
            print(f"[sanitize] {rel}")
    return changed


def _scan_publish_residuals(root: Path) -> list:
    """[P1-11 修复] 推送前残留自检：列出 ``docs/`` 公开面里仍含真实身份字面量的文件。

    与 sync_publish 导出后自检调用同一函数（``privacy_policy.scan_residual_identity``），
    ``include_pii=True`` 亦同 —— ``docs/`` 里没有第三方源码树，通用 PII 正则不会误报。
    扫描覆盖整个 ``docs/`` 树（git 全量跟踪 = Pages 公开面）：脱敏只处理构建产物，
    静态资产里的当前身份靠这一步兜底拦截。返回相对 ``docs/`` 的 posix 路径列表；
    非空时调用方必须阻断推送。
    """
    docs = root / "docs"
    if not docs.is_dir():
        return []
    return _pp.scan_residual_identity(docs, root, include_pii=True)


def _assert_identity_rules_effective(root: Path) -> None:
    """发布前阻断动态身份脱敏静默失效。"""
    if not (root / "ky_config.json").exists():
        # 公开副本/CI 没有考生身份时，动态规则为空是正常的 no-op。
        return
    ok, reason = _pp.identity_rules_effective(root)
    if not ok:
        raise RuntimeError(
            "[隐私闸门] ky_config.json 存在，但动态身份脱敏规则无效："
            f"{reason}。请先修复 study_plan（school / major / pro_name）后再发布。"
        )


def main():
    print("=" * 65)
    print(" 考研学习链 (Kaoyan AI Study Chain) · 看板更新与同步")
    print("=" * 65)

    push_mode = ("--push" in sys.argv and "--local" not in sys.argv
                 and "-l" not in sys.argv)

    if not push_mode:
        print("\n[1/3] 正在解析各科状态并生成 Web 看板（本地完整模式）...")
        if _run_build(sanitized=False) != 0:
            print("[!] 构建失败，请检查 Python 环境或语法。")
            sys.exit(1)
        print("\n[OK] 本地构建完成（输出位于 docs/.local，已跳过 Git 提交与推送）。")
        return

    # [W13 验收修复·发布链路] --push 必须先以脱敏模式构建发布产物（私人学习
    # 记录不得随 Pages 公开），提交/推送完成后在 finally 里恢复本地完整模式。
    try:
        _assert_identity_rules_effective(ROOT)
    except RuntimeError as exc:
        print(f"\n[!] {exc}")
        sys.exit(3)

    print("\n[1/3] 正在以脱敏模式构建发布用 Web 看板...")
    if _run_build(sanitized=True) != 0:
        print("[!] 构建失败，请检查 Python 环境或语法。")
        sys.exit(1)

    push_ok = False
    try:
        # [P1-11 修复·审计 2026-09-30] 推送前内容级脱敏 + 残留自检：直推链路此前
        # 完全绕过 sync_publish 的这两道工序，卡片正面手写校名会明文进 Pages。
        # 发现残留 → 阻断提交与推送（产物不离开本机，Pages 不受影响）。
        sanitized_n = _sanitize_publish_products(ROOT)
        if sanitized_n:
            print(f"  -> 已对 {sanitized_n} 个发布产物文件执行内容级脱敏。")
        residual = _scan_publish_residuals(ROOT)
        if residual:
            print("\n[!] 阻断推送：docs/ 公开面中仍含真实身份字面量，已取消提交与推送。")
            for rel in residual[:20]:
                print(f"    - docs/{rel}")
            if len(residual) > 20:
                print(f"    ... 其余 {len(residual) - 20} 项省略")
            print("    请检查脱敏规则覆盖范围（或手工清理后重跑）；产物未推送，Pages 不受影响。")
            sys.exit(3)

        # 2. Git 提交
        print("\n[2/3] 正在暂存并提交更新...")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        # [P0 修复] 显式白名单，杜绝隐私文件被顺带提交
        allowed = list(PUSH_PRODUCTS)
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
