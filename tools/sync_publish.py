"""
Sync the D: workspace to a publishable copy on Desktop, with sanitization.
Usage: python tools/sync_publish.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

SRC = Path("D:/测试/考研学习chain")
DST = Path("C:/Users/29652/Desktop/考研学习chain")

# Directories/files to skip during robocopy mirror
EXCLUDE_DIRS = [
    ".git",
    ".github",
    ".memory",
    ".workbuddy",
    "__pycache__",
    ".pytest_cache",
    ".tmp.driveupload",
    ".tmp.drivedownload",
    "kaoyan_study_chain.egg-info",
    "rust_ext",
    "错题本",
    "每日作业",
    "scratch",  # development/debug scratch scripts, not for publish
]
EXCLUDE_FILES = [
    "state_snapshot.json",
    "ky_config.json",
]

# Personal/sensitive reports to exclude (kept out of publish copy)
EXCLUDE_REPORTS = [
    "D盘实测_用户视角全功能运行报告.md",
    "D盘实测_第二轮全功能运行报告.md",
    "D盘实测_第三轮_2027考生全流程CLI-TUI-GUI贯通测试报告.md",
    "D盘实测_第四轮_2027天工大考生_三端全流程核对报告.md",
]

# Filename patterns (basename) to rename for desensitization (NOT deleted)
RENAME_NAME_PATTERNS = [
    (re.compile(r"^目标院校情报_.*\.md$"), "目标院校情报_目标院校_目标专业.md"),
    (re.compile(r"^双校考情对比_.*\.md$"), "双校考情对比_目标院校_VS_对比院校B_目标专业.md"),
]


def dir_should_exclude(rel_parts, name):
    """Decide whether a directory should be excluded from the publish copy."""
    if name in EXCLUDE_DIRS:
        return True
    # never descend into sensitive/secret dirs regardless of depth
    if name in {".git", ".github", ".memory", ".workbuddy", "__pycache__",
                ".pytest_cache", ".tmp.driveupload", ".tmp.drivedownload",
                "kaoyan_study_chain.egg-info", "rust_ext", "错题本", "每日作业", "scratch"}:
        return True
    return False


def file_should_exclude(rel_parts, name):
    if name in EXCLUDE_FILES:
        return True
    if name in EXCLUDE_REPORTS:
        return True
    if name.endswith(".pyc"):
        return True
    # ignore compiled/qt artifacts
    if name in {"state_snapshot.json"}:
        return True
    return False


#: 同步产物标记文件：用于区分「本脚本生成的发布副本」与「用户同名的真实目录」
DST_MARKER = ".sync_publish_marker"

#: 预览模式开关：为 True 时所有删除/写入操作只打印计划，不产生副作用。
#: [安全修复] 默认值必须是 True。此前默认 False（即「真删」），安全性完全依赖
#: 「只会从 main() 进入」这一无保护的约定 —— 任何 `import sync_publish` 后直接
#: 调用镜像/清理函数（测试、脚本、REPL、未来重构）都会绕过 main() 里的 --force
#: 闸门，对本机硬编码的 DST 执行真实写入与 rmtree。
#: 现在：安全是默认值，真实执行只能由 main() 显式打开（--force）。
DRY_RUN = True


def dst_is_safe_to_purge() -> bool:
    """判断 DST 是否可以安全清理。

    [P1 修复] purge/cleanup 会对 DST 下的 .git、.memory、错题本 等目录执行
    rmtree，且 DST 为硬编码路径。若桌面上恰好存在同名的真实工作区，
    直接清理将造成不可逆数据丢失。这里要求 DST 满足以下之一才允许清理：
      1. 不存在（即将新建）；2. 为空目录；3. 含本脚本写入的标记文件。
    """
    if not DST.exists():
        return True
    if (DST / DST_MARKER).exists():
        return True
    try:
        return not any(DST.iterdir())
    except OSError:
        return False


def python_mirror():
    """Mirror SRC -> DST purely in Python, skipping sensitive dirs/files.

    受 DRY_RUN 守卫：预览模式下**只枚举将要复制的文件**，不创建目录、不写任何字节。
    （此前该函数完全不检查 DRY_RUN，是「预览开关形同虚设」的主要缺口。）
    """
    if DRY_RUN:
        planned: list = []

        def dry_walk(src_dir, rel):
            for entry in os.scandir(src_dir):
                rel_parts = rel + [entry.name]
                if entry.is_dir(follow_symlinks=False):
                    if dir_should_exclude(rel_parts, entry.name):
                        continue
                    dry_walk(entry.path, rel_parts)
                elif entry.is_file(follow_symlinks=False):
                    if file_should_exclude(rel_parts, entry.name):
                        continue
                    planned.append(str(Path(*rel_parts)))

        dry_walk(SRC, [])
        print(f"[copy·预览] 将复制 {len(planned)} 个文件到 {DST}（未写入任何内容）")
        for rel in planned[:20]:
            print(f"  + {rel}")
        if len(planned) > 20:
            print(f"  ... 其余 {len(planned) - 20} 个文件省略")
        return

    if not DST.exists():
        DST.mkdir(parents=True, exist_ok=True)
    copied = 0

    def walk(src_dir, rel):
        for entry in os.scandir(src_dir):
            rel_parts = rel + [entry.name]
            if entry.is_dir(follow_symlinks=False):
                if dir_should_exclude(rel_parts, entry.name):
                    print(f"[skip-dir] {entry.name}")
                    continue
                dst_sub = DST / Path(*rel_parts)
                dst_sub.mkdir(parents=True, exist_ok=True)
                walk(entry.path, rel_parts)
            elif entry.is_file(follow_symlinks=False):
                if file_should_exclude(rel_parts, entry.name):
                    print(f"[skip-file] {entry.name}")
                    continue
                dst_file = DST / Path(*rel_parts)
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry.path, dst_file)
                copied += 1

    walk(SRC, [])
    # 写入标记，使后续轮次能识别该目录为本脚本的发布副本
    try:
        atomic_write_text((DST / DST_MARKER),
            "generated by tools/sync_publish.py; safe to purge\n", encoding="utf-8")
    except OSError as e:
        print(f"[warn] 无法写入同步标记 {DST_MARKER}: {e}")
    print(f"[copy] python mirror done, {copied} files copied")


def purge_residual_excluded_dirs():
    """Delete any excluded dirs that may already exist under DST (best effort)."""
    names = [".git", ".github", ".memory", ".workbuddy", "__pycache__",
             ".pytest_cache", ".tmp.driveupload", ".tmp.drivedownload",
             "kaoyan_study_chain.egg-info", "rust_ext", "错题本", "每日作业", "scratch"]
    for n in names:
        p = DST / n
        if p.exists():
            if DRY_RUN:
                print(f"[purge·预览] 将删除 {p}")
                continue
            try:
                # 不使用 ignore_errors=True：那会让删除失败时既不抛异常也不进 except，
                # 随后打印 "removed" 给出成功假象，而目录实际仍然存在。
                shutil.rmtree(p)
                print(f"[purge] removed {p}")
            except Exception as e:
                print(f"[purge] 未删除 {p}: {type(e).__name__}: {e}")


def robocopy_mirror():
    """Deprecated alias retained for reference; replaced by python_mirror."""
    python_mirror()


CONFIG_TEMPLATE = {
    "api_provider": "custom",
    "base_url": "https://your-api-endpoint.example.com",
    "api_key": "YOUR_API_KEY_HERE",
    "model": "gpt-4o-mini",
    "temperature": 0.3,
    "active_subject": "math",
    "webhooks": {
        "wechat": "",
        "qq_onebot": "",
        "qq_target_id": "",
        "dingtalk": "",
        "dingtalk_secret": "",
        "feishu": "",
    },
    "onboarding_completed": False,
    "study_plan": {
        "target_year": "2027",
        "exam_date": "2027-12-26",
        "stage_name": "基础筑基阶段",
        "days_left": 365,
        "school": "目标院校",
        "major": "目标专业 (专业代码-方向)",
        "math_key": "math2",
        "eng_key": "eng2",
        "pro_type": "custom",
        "pro_name": "专业课名称 代码",
        "math_name": "数学二 (302)",
        "eng_name": "英语二 (204)",
        "math_baseline": "待摸底",
        "math_weakness": "待诊断",
        "eng_baseline": "待摸底",
        "eng_weakness": "待诊断",
        "pol_baseline": "待摸底",
        "pol_weakness": "待诊断",
        "pro_baseline": "待摸底",
        "pro_weakness": "待诊断",
        "math_books": "[请放入本地参考资料后填写白名单书目]",
        "eng_books": "[请放入本地参考资料后填写白名单书目]",
        "pol_books": "[请放入本地参考资料后填写白名单书目]",
        "pro_books": "[请放入本地参考资料后填写白名单书目]",
        "total_hours": 8.5,
        "math_hours": 3.0,
        "eng_hours": 2.0,
        "pol_hours": 1.0,
        "pro_hours": 2.5,
        "rest_weekly": "每周日晚放松休整",
        "rest_monthly": "每月最后一个周日全天闭卷模考与全科雷达复盘",
        "style_name": "严格把关·保姆提分型 (Strict & Disciplined)",
        "math_target": "待设定",
        "eng_target": "待设定",
        "pol_target": "待设定",
        "pro_target": "待设定",
        "total_target": "待设定",
    },
    "coaching_style": "严格把关·保姆提分型 (Strict & Disciplined)",
    "completion_history": {},
    "relief_mode_active": False,
}


def write_config_template():
    config_path = DST / "ky_config.json"
    atomic_write_text(config_path,
        json.dumps(CONFIG_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[config] wrote sanitized template -> {config_path}")


SUBSTITUTIONS = [
    (r"天津工业大学", "目标院校"),
    (r"医学电子信息工程 \(085400-01\)", "目标专业 (专业代码-方向)"),
    (r"医学电子信息工程", "目标专业"),
    (r"华南理工大学", "目标院校"),
    (r"中山大学", "对比院校B"),
    (r"华中科技大学", "对比院校B"),
    (r"武汉大学", "对比院校B"),
    (r"天津大学", "对比院校B"),
    (r"长沙理工大学", "对比院校B"),
    (r"人工智能 \(085400\)", "目标专业 (专业代码-方向)"),
    (r"华工", "目标院校简称"),
    (r"天工大", "目标院校简称"),
    (r"2026-12-19", "2027-12-26"),
    (r"https://www\.fhl\.mom", "https://your-api-endpoint.example.com"),
    (r"sk-42a6f94d4350c0459b1369219b3d162338996dab1195b1a6ca8449dbc002b578", "YOUR_API_KEY_HERE"),
    (r"gpt-5\.4-mini", "gpt-4o-mini"),
    # Personal contact / account data
    (r"296528868", "您的QQ号"),
    (r"moyetian@foxmail\.com", "your-email@example.com"),
    (r"moyetian", "your-name"),
    # URL-encoded forms (so embedded search/share links are also anonymized)
    (r"%E5%A4%A9%E6%B4%A5%E5%B7%A5%E4%B8%9A%E5%A4%A7%E5%AD%A6", "%E7%9B%AE%E6%A0%87%E9%99%A2%E6%A0%A1"),  # 天津工业大学
    (r"%E5%8D%8E%E5%8D%97%E7%90%86%E5%B7%A5%E5%A4%A7%E5%AD%A6", "%E7%9B%AE%E6%A0%87%E9%99%A2%E6%A0%A1"),  # 华南理工大学
    (r"%E4%B8%AD%E5%B1%B1%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),  # 中山大学
    (r"%E5%8D%8E%E4%B8%AD%E7%A7%91%E6%8A%80%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),  # 华中科技大学
    (r"%E6%AD%A6%E6%B1%89%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),  # 武汉大学
    (r"%E5%A4%A9%E6%B4%A5%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),  # 天津大学
    (r"%E9%95%BF%E6%B2%99%E7%90%86%E5%B7%A5%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),  # 长沙理工大学
    (r"%E5%8C%BB%E5%AD%A6%E7%94%B5%E5%AD%90%E4%BF%A1%E6%81%AF%E5%B7%A5%E7%A8%8B", "%E7%9B%AE%E6%A0%87%E4%B8%93%E4%B8%9A"),  # 医学电子信息工程
]

# Sensitive per-line patterns that should be replaced with placeholders
LOCAL_WHITELIST_RE = re.compile(r"\[本地资料库已就绪\]:.*")


def sanitize_text(text: str) -> str:
    for pat, repl in SUBSTITUTIONS:
        text = re.sub(pat, repl, text)
    # Replace concrete local whitelist book lists with a placeholder
    text = LOCAL_WHITELIST_RE.sub("[本地资料库已就绪]: 请放入本地参考资料后填写白名单书目", text)
    return text


def sanitize_markdown_files():
    """Sanitize Markdown, HTML and SVG files under DST (except LICENSE)."""
    skip = {DST / "LICENSE"}
    for ext in ("*.md", "*.html", "*.svg"):
        for f in DST.rglob(ext):
            if f in skip:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            atomic_write_text(f, sanitize_text(text))
            print(f"[sanitize] {f}")


# School/major name fragments that may leak into file names
FILENAME_SCHOOL_MAP = [
    ("天津工业大学", "目标院校"),
    ("华南理工大学", "目标院校"),
    ("中山大学", "对比院校B"),
    ("华中科技大学", "对比院校B"),
    ("武汉大学", "对比院校B"),
    ("天津大学", "对比院校B"),
    ("医学电子信息工程", "目标专业"),
    ("人工智能", "目标专业"),
]


def rename_sensitive_files():
    """Rename personalized scouting-report files to neutral names (no deletion)."""
    for path in DST.rglob("*.md"):
        for pat, new_name in RENAME_NAME_PATTERNS:
            if pat.match(path.name):
                new_path = path.with_name(new_name)
                # Avoid clobbering: append a counter if needed
                counter = 1
                while new_path.exists():
                    stem = new_path.stem
                    new_path = new_path.with_name(f"{stem}_{counter}{new_path.suffix}")
                    counter += 1
                path.rename(new_path)
                print(f"[rename] {path.name} -> {new_path.name}")
                break
    # docs/experiences files: rename to neutral name
    exp_dir = DST / "docs" / "experiences"
    if exp_dir.exists():
        for f in exp_dir.glob("*.md"):
            new_path = f.with_name("目标院校_目标专业_社媒经验档案.md")
            counter = 1
            while new_path.exists():
                new_path = f.with_name(f"目标院校_目标专业_社媒经验档案_{counter}.md")
                counter += 1
            f.rename(new_path)
            print(f"[rename] {f.name} -> {new_path.name}")
    # Generic: neutralize school/major fragments appearing in any file name
    for path in list(DST.rglob("*")):
        if path.is_file():
            new_name = path.name
            for frag, repl in FILENAME_SCHOOL_MAP:
                if frag in new_name:
                    new_name = new_name.replace(frag, repl)
            if new_name != path.name:
                new_path = path.with_name(new_name)
                counter = 1
                base = new_path.stem
                while new_path.exists():
                    new_path = new_path.with_name(f"{base}_{counter}{new_path.suffix}")
                    counter += 1
                path.rename(new_path)
                print(f"[rename] {path.name} -> {new_path.name}")


def neutralize_sync_script():
    """Overwrite the publish-copy sync script with a non-sensitive placeholder."""
    target = DST / "tools" / "sync_publish.py"
    if target.exists():
        placeholder = (
            "# This file is intentionally left as a non-sensitive placeholder in the\n"
            "# publishable copy. The real synchronization/sanitization script lives\n"
            "# only in the private working workspace and must not be shared.\n"
            "# Replace with your own publish/deploy pipeline as needed.\n"
        )
        atomic_write_text(target, placeholder)
        print(f"[neutralize] {target}")


def ensure_gitignore_protects():
    gitignore = DST / ".gitignore"
    if not gitignore.exists():
        return
    text = gitignore.read_text(encoding="utf-8")
    needed = [
        ".memory/",
        ".workbuddy/",
        "ky_config.json",
        "state_snapshot.json",
        "错题本/",
        "每日作业/",
        "*.log",
    ]
    changed = False
    for line in needed:
        if line not in text:
            text += f"\n{line}"
            changed = True
    if changed:
        atomic_write_text(gitignore, text)
        print("[gitignore] added privacy guards")


def cleanup_residual_hidden_dirs():
    """Remove residual excluded dirs (leftovers from prior sync rounds) in DST."""
    names = [
        ".git", ".github", ".memory", ".workbuddy", "__pycache__",
        ".pytest_cache", ".tmp.drivedownload", ".tmp.driveupload",
        "kaoyan_study_chain.egg-info", "rust_ext", "错题本", "每日作业", "scratch",
    ]
    if DRY_RUN:
        for n in names:
            p = DST / n
            if p.exists():
                print(f"[cleanup·预览] 将递归删除 {p}")
        return
    ps = "; ".join(
        f"$p=Join-Path '{DST}' '{n}'; if (Test-Path $p) {{ Get-ChildItem $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Force -Recurse -Confirm:$false -ErrorAction SilentlyContinue; Remove-Item $p -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue }}"
        for n in names
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        shell=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print("[cleanup] powershell stderr:", proc.stderr)
    else:
        print("[cleanup] residual excluded dirs removed")


def sanitize_source_examples():
    """Sanitize hardcoded example school names in source docstrings/comments.

    [P1 修复] 原先仅硬编码替换「华南理工大学」，而当前目标院校已是天津工业大学，
    且 04-专业课 实际还存在华中科技大学、武汉大学、长沙理工大学等校名，
    单条替换会导致发布副本泄露真实报考信息。现统一复用 SUBSTITUTIONS 名单。
    """
    target = DST / "tools/skills/material_scanner.py"
    if target.exists():
        text = target.read_text(encoding="utf-8")
        text = sanitize_text(text)
        atomic_write_text(target, text)
        print(f"[sanitize-src] {target}")


def main():
    global DRY_RUN
    import argparse
    ap = argparse.ArgumentParser(
        description="将工作区同步为可发布的脱敏副本（默认仅预览，不写入也不删除）")
    ap.add_argument("--force", action="store_true",
                    help="真正执行同步与清理；默认 dry-run 仅打印计划")
    args = ap.parse_args()
    DRY_RUN = not args.force

    if not SRC.exists():
        print(f"source not found: {SRC}")
        sys.exit(1)

    print(f"[plan] SRC = {SRC}")
    print(f"[plan] DST = {DST}")

    # [P1 修复] 安全校验：拒绝清理疑似真实工作区的目标目录
    if not dst_is_safe_to_purge():
        print(f"\n[拒绝执行] 目标目录 {DST} 既不是空目录，也不含同步标记 {DST_MARKER}。")
        print("           它很可能是你的真实工作区而非本脚本生成的发布副本，")
        print("           若继续清理将不可逆地删除其中的 .git/.memory/错题本 等数据。")
        print("           请确认后手动移走该目录，或修改脚本中的 DST 路径后重试。")
        sys.exit(2)

    if DRY_RUN:
        print("\n[dry-run] 预览模式：以上为执行计划，未写入也未删除任何文件。")
        print("[dry-run] 确认无误后，加 --force 参数重新运行以真正执行。")
        return

    purge_residual_excluded_dirs()
    python_mirror()
    write_config_template()
    sanitize_markdown_files()
    rename_sensitive_files()
    sanitize_source_examples()
    neutralize_sync_script()
    ensure_gitignore_protects()
    purge_residual_excluded_dirs()
    print("\n[done] publish copy ready at", DST)


if __name__ == "__main__":
    main()
