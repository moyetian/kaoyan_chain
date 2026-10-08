# -*- coding: utf-8 -*-
"""
真题与资料入库命令模块 (material.py)
包含 ingest / mount / key / run_material_ingest
"""

import sys
from pathlib import Path
from typing import List

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.repl.renderer import C, colorize
except ImportError:
    from cli.dispatch import Command, register
    from cli.repl.renderer import C, colorize


def _cmd_ingest(args: List[str]) -> None:
    if len(args) < 2 or "--help" in args or "-h" in args:
        print(colorize("""
考研试题与备考资料智能切片入库管道 (ky ingest)
用法：
  ky ingest <试题文件路径.md/.txt/.pdf> [--subject=pro/math/eng/pol] [--source=题源出处] [--no-llm] [--llm-budget=N]
示例：
  ky ingest 2024年408统考真题.txt --subject=pro --source="2024统考408真题"
  ky ingest 历年数学二中值定理题集.md --subject=math --source="数二历年证明题精选"
  ky ingest 803真题回忆_2024.md --subject=pro --llm-budget=3   （本次最多 3 次 LLM 采分点补全）
说明：
  自动分块切片单题，识别题型 (选择/填空/大题)，提取步骤采分点并格式化为标准白名单题目卡片，
  自动归档入对应科目的 参考资料/ 目录。
  LLM 采分点补全默认封顶 10 次/次入库（调用前会提示候选题数），
  可用 --llm-budget=N 调整上限、--llm-budget=0 或 --no-llm 完全跳过。
""", C.YELLOW))
        sys.exit(0 if ("--help" in args or "-h" in args) else 1)

    target_file = None
    target_subject = "pro"
    source_title = ""
    llm_enrich = None
    llm_budget = None
    for a in args[1:]:
        if a.startswith("--subject=") or a.startswith("-s="):
            target_subject = a.split("=", 1)[1].strip()
        elif a.startswith("--source="):
            source_title = a.split("=", 1)[1].strip()
        elif a == "--no-llm":
            llm_enrich = False
        elif a.startswith("--llm-budget="):
            try:
                llm_budget = int(a.split("=", 1)[1].strip())
            except ValueError:
                print(colorize("[!] --llm-budget 需为整数（如 --llm-budget=3；0=跳过；负数=不限）", C.RED))
                sys.exit(1)
        elif not a.startswith("-"):
            if target_file is None:
                target_file = a
            else:
                # [P2 修复·2026-10-08 静默丢弃] 第二个位置参数此前被无声忽略（`ky ingest a.md b.md`
                # 只入库 a.md，用户以为两份都进了）。现明确报错，避免误操作。
                print(colorize(f"[!] 多余的参数: {a}（ky ingest 只接受一个试题文件路径）", C.RED))
                sys.exit(1)

    if not target_file:
        print(colorize("[!] 请提供待切片入库的试题文件路径", C.RED))
        sys.exit(1)

    p = Path(target_file)
    if not p.exists():
        print(colorize(f"[!] 找不到文件: {target_file}", C.RED))
        sys.exit(1)

    try:
        from tools.skills import material_ingestion
    except ImportError:
        try:
            from skills import material_ingestion
        except ImportError:
            material_ingestion = None

    if material_ingestion:
        pipe = material_ingestion.get_material_ingestion_pipeline()
        print(colorize(f"\n[📥 正在对试题文档【{p.name}】执行智能分块与采分点切片入库...]\n", C.CYAN))
        res = pipe.ingest_file(p, subject=target_subject, source_name=source_title or p.stem,
                               llm_enrich=llm_enrich, llm_budget=llm_budget)
        if res.get("success"):
            print(colorize(f"  ✓ {res.get('summary')}", C.GREEN))
            print(colorize(f"  • 白名单题目卡片集已生成至: {res.get('target_path')}\n", C.BOLD))
        else:
            print(colorize(f"  [!] 切片入库未完成: {res.get('msg')}\n", C.YELLOW))
    else:
        print(colorize("[!] material_ingestion 模块未载入", C.RED))


def run_material_ingest(arg: str = "") -> bool:
    """REPL /ingest 实现：与 CLI ky ingest 同源同口径。"""
    tokens = str(arg or "").split()
    target_file, subject, source_title = "", "pro", ""
    llm_enrich, llm_budget = None, None
    for t in tokens:
        if t.startswith("--subject=") or t.startswith("-s="):
            subject = t.split("=", 1)[1].strip() or "pro"
        elif t.startswith("--source="):
            source_title = t.split("=", 1)[1].strip()
        elif t == "--no-llm":
            llm_enrich = False
        elif t.startswith("--llm-budget="):
            try:
                llm_budget = int(t.split("=", 1)[1].strip())
            except ValueError:
                print(colorize("[!] --llm-budget 需为整数（如 --llm-budget=3；0=跳过；负数=不限）", C.RED))
                return False
        elif not t.startswith("-") and not target_file:
            target_file = t
    if not target_file:
        print(colorize("用法: /ingest <试题文件路径> [--subject=pro/math/eng/pol] [--source=题源出处] [--no-llm] [--llm-budget=N]\n"
                       "示例: /ingest 2024年408统考真题.txt --subject=pro --source=\"2024统考408真题\" --llm-budget=5", C.YELLOW))
        return False
    p = Path(target_file)
    if not p.exists():
        print(colorize(f"[!] 找不到文件: {target_file}", C.RED))
        return False
    try:
        from tools.skills import material_ingestion
    except ImportError:
        try:
            from skills import material_ingestion
        except ImportError:
            material_ingestion = None
    if not material_ingestion:
        print(colorize("[!] material_ingestion 模块未载入", C.RED))
        return False
    pipe = material_ingestion.get_material_ingestion_pipeline()
    print(colorize(f"\n[📥 正在对试题文档【{p.name}】执行智能分块与采分点切片入库...]\n", C.CYAN))
    res = pipe.ingest_file(p, subject=subject, source_name=source_title or p.stem,
                           llm_enrich=llm_enrich, llm_budget=llm_budget)
    if res.get("success"):
        print(colorize(f"  ✓ {res.get('summary')}", C.GREEN))
        for card in (res.get("cards") or [])[:10]:
            title = card.get("title") or card.get("stem", "")[:40]
            print(f"  • [{card.get('question_type', '题目')}] {title}")
        saved = res.get("saved_path") or res.get("output_path")
        if saved:
            print(colorize(f"  [√ 归档路径]: {saved}", C.GREEN))
        print()
        return True
    print(colorize(f"[!] 切片入库失败: {res.get('msg') or res.get('error') or res}", C.RED))
    return False


def _cmd_mount(args: List[str]) -> None:
    try:
        from tools.skills import material_scanner
    except ImportError:
        try:
            from skills import material_scanner
        except ImportError:
            material_scanner = None

    if not material_scanner:
        print(colorize("[!] material_scanner 技能模块未载入", C.RED))
        return

    apply_flag = any(a in ("--apply", "--write") for a in args)
    yes_flag = any(a in ("-y", "--yes") for a in args)

    # [修复 2026-10-05·目录参数被静默忽略] `ky mount [目录]` 此前只解析
    # --apply/-y，位置参数被丢弃、恒扫描默认工作区。现解析首个位置参数并
    # 透传给 scan_and_mount_materials(workspace_root=...)（该函数原生支持
    # workspace_root，语义即「以此为工作区根扫描四科 参考资料/」）。
    # 相对路径相对工作区根解析；目录不存在时报可读错误，不再静默回落默认目录。
    target_root = None
    for a in args[1:]:
        if not a.startswith("-"):
            if target_root is not None:
                print(colorize("[!] ky mount 只接受一个目录参数", C.RED))
                sys.exit(1)
            target_root = a
    if target_root:
        try:
            from tools.cli.shared import ROOT as _WS_ROOT
        except ImportError:
            from cli.shared import ROOT as _WS_ROOT
        cand = Path(target_root)
        if not cand.is_absolute():
            cand = _WS_ROOT / cand
        if not cand.exists() or not cand.is_dir():
            print(colorize(f"[!] 目录不存在或不是目录: {target_root}", C.RED))
            sys.exit(1)
        target_root = cand.resolve()

    print(colorize("\n[🔍 正在智能扫描本地 参考资料/ 目录与考研资料库...]\n", C.CYAN))

    # [P1-8 修复·默认只读] 先只读盘点并展示将发生的变更；写回必须显式 --apply。
    # 此前 ky mount（0 份资料）也会把目标高校塞进简章雷达、重写 config 与 AGENTS.md
    # 白名单（实测偷改志愿雷达），默认行为必须无副作用。
    preview = material_scanner.scan_and_mount_materials(
        workspace_root=target_root, apply=False)
    if not preview.get("success"):
        print(colorize(f"[!] 资料扫描失败: {preview.get('msg')}", C.RED))
        return

    print(colorize(f"✔ 共扫描到 {preview['total_files']} 份本地参考资料与历年真题：", C.GREEN))
    for k, flist in preview["details"].items():
        label = {"math": "数学", "eng": "英语", "pol": "政治", "pro": "专业课"}.get(k, k)
        if flist:
            print(f"  • 【{label}】: {len(flist)} 份实体资料 -> {', '.join(flist)}")
        else:
            print(f"  • 【{label}】: 暂无本地资料 (私教遵循官方考纲出题)")

    changes = preview.get("changes") or []
    has_pending = bool(changes or preview.get("would_watch") or preview.get("would_scout"))
    if changes:
        print(colorize("\n[📝 以下变更将在写入时生效]:", C.YELLOW))
        for ch in changes:
            old_disp = ch.get("old")
            old_disp = "（无）" if old_disp in (None, "") else str(old_disp)
            print(f"  · {ch['target']} :: {ch['field']}")
            print(f"      旧: {old_disp}")
            print(f"      新: {ch['new']}")
    if preview.get("would_watch"):
        print(colorize(
            f"  · 研招雷达: 将把目标高校【{preview.get('target_school')}】纳入简章动态指纹监控", C.YELLOW))
    if preview.get("would_scout"):
        print(colorize(f"  · 目标院校情报: 将生成 {preview['would_scout']}", C.YELLOW))

    if not apply_flag:
        if has_pending:
            print(colorize(
                "\n[i] 以上为只读盘点，未写入任何文件。确认无误后运行 ky mount --apply 写回（可加 -y 跳过确认）。\n",
                C.CYAN))
        else:
            print(colorize("\n[i] 只读盘点完成：白名单与雷达均无变更，无需写入。\n", C.CYAN))
        return

    if not has_pending:
        print(colorize("\n[i] 无任何变更需要写入。\n", C.CYAN))
        return

    if not yes_flag:
        try:
            ans = input(colorize("确认按以上预览写入 ky_config.json / AGENTS.md / 简章雷达? (y/N): ", C.YELLOW)).strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print(colorize("[i] 已取消，未写入任何文件。\n", C.YELLOW))
            return

    result = material_scanner.scan_and_mount_materials(
        workspace_root=target_root, apply=True)
    if not result.get("success"):
        print(colorize(f"[!] 资料挂载失败: {result.get('msg')}", C.RED))
        return
    if result.get("school_watch"):
        print(colorize(f"\n[📡 研招联动]: {result['school_watch']}", C.CYAN))
    if result.get("scout_report"):
        print(colorize(f"[📡 研招联动]: 目标院校专属情报已生成: {result['scout_report']}", C.CYAN))
    print(colorize("\n🎉 参考资料白名单与目标院校雷达已按预览同步写回 ky_config.json 与 AGENTS.md！\n", C.GREEN))


def _cmd_key(args: List[str]) -> None:
    try:
        from tools.skills import exam_composer
    except ImportError:
        try:
            from skills import exam_composer
        except ImportError:
            exam_composer = None

    if not exam_composer:
        print(colorize("[!] exam_composer 技能模块未载入", C.RED))
        return

    sub = args[1].lower() if len(args) > 1 else "list"
    if sub in ("list", "ls", "--list", "l"):
        pid = args[2].strip() if len(args) > 2 else ""
        r = exam_composer.list_exam_keys(pid)
        if not r.get("success"):
            print(colorize(f"[!] {r.get('msg')}", C.YELLOW))
        else:
            papers = r.get("papers", [])
            if not papers:
                print(colorize("暂无已归档试卷密钥。", C.YELLOW))
            for p in papers:
                if p.get("error"):
                    print(colorize(f"  ✗ {p['paper_id']}: {p['error']}", C.RED))
                    continue
                mark = "√" if p["answered"] == p["total"] and p["total"] else "!"
                color = C.GREEN if mark == "√" else C.YELLOW
                print(colorize(
                    f"  [{mark}] {p['paper_id']}: 答案 {p['answered']}/{p['total']} 题已登记", color))
                for it in p["items"]:
                    flag = "有答案" if it["has_answer"] else "缺答案(将转人工复核)"
                    print(f"        · 第 {it['id']} 题 {it['title']} — {flag}")
            print(colorize(
                "\n提示: 补录标准答案 → ky key set <试卷编号> <题号> \"标准答案正文\"\n"
                "      支持中文/公式文本，写入后自动重新加密归档 (ENC1)。", C.CYAN))
    elif sub in ("set", "add", "upsert", "--set"):
        if len(args) < 5:
            print(colorize('用法: ky key set <试卷编号> <题号> "<标准答案正文>"\n'
                           '示例: ky key set EXAM-PRO-20260910-161500 1 "答案：B，由傅里叶变换可得..."', C.YELLOW))
        else:
            pid = args[2].strip()
            qid = args[3].strip()
            ans = " ".join(args[4:]).strip()
            r = exam_composer.upsert_answer(pid, qid, ans)
            tag = C.GREEN if r.get("success") else C.RED
            print(colorize(f"[{'√' if r.get('success') else '!'}] {r.get('msg')}", tag))
    elif sub in ("--help", "-h", "help"):
        print(colorize(
            "ky key — 试卷答案密钥库管理与标准答案补录\n"
            "用法:\n"
            "  ky key list [试卷编号]                查看试卷及答案登记情况\n"
            "  ky key set <试卷编号> <题号> <答案>     补录/更新单题标准答案\n", C.CYAN))
    else:
        print(colorize(f"[!] 未知子命令: {sub}，可用: list | set", C.YELLOW))


# 注册资料入库命令
register(Command('ingest', ("ingest", "--ingest"), '<试题文件路径> [--subject=pro/math]', '外部真题/试卷智能切片入库管道 (题型识别/采分点提取/白名单归档)', handler=_cmd_ingest, write=True))
# [P1-8 修复] mount 默认只读盘点，仅 --apply 才写回 —— 不再注册为 write=True 整体拦截，
# 改由 shared._SAFE_MODE_WRITE_FLAGS 按 --apply 粒度判定（与 scout 同模式）。
register(Command('mount', ("mount", "scan", "--mount", "--scan"), '[目录] [--apply] [-y]', '扫描本地资料目录（默认只读盘点；--apply 显式写回白名单与研招雷达）', handler=_cmd_mount))
register(Command('key', ("key", "--key", "keys", "--keys"), '[list|set] <试卷编号> [题号] ["标准答案"]', '管理自测卷的加密标准答案（判卷自动采分依赖它）', handler=_cmd_key, write=True))
