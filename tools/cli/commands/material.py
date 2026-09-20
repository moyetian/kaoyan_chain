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
    from tools.cli.shared import load_config, save_config
    from tools.cli.repl.renderer import C, colorize
except ImportError:
    from cli.dispatch import Command, register
    from cli.shared import load_config, save_config
    from cli.repl.renderer import C, colorize


def _cmd_ingest(args: List[str]) -> None:
    if len(args) < 2 or "--help" in args or "-h" in args:
        print(colorize("""
考研试题与备考资料智能切片入库管道 (ky ingest)
用法：
  ky ingest <试题文件路径.md/.txt/.pdf> [--subject=pro/math/eng/pol] [--source=题源出处]
示例：
  ky ingest 2024年408统考真题.txt --subject=pro --source="2024统考408真题"
  ky ingest 历年数学二中值定理题集.md --subject=math --source="数二历年证明题精选"
说明：
  自动分块切片单题，识别题型 (选择/填空/大题)，提取步骤采分点并格式化为标准白名单题目卡片，
  自动归档入对应科目的 参考资料/ 目录。
""", C.YELLOW))
        sys.exit(0 if ("--help" in args or "-h" in args) else 1)

    target_file = None
    target_subject = "pro"
    source_title = ""
    for a in args[1:]:
        if a.startswith("--subject=") or a.startswith("-s="):
            target_subject = a.split("=", 1)[1].strip()
        elif a.startswith("--source="):
            source_title = a.split("=", 1)[1].strip()
        elif not a.startswith("-"):
            if target_file is None:
                target_file = a

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
        res = pipe.ingest_file(p, subject=target_subject, source_name=source_title or p.stem)
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
    for t in tokens:
        if t.startswith("--subject=") or t.startswith("-s="):
            subject = t.split("=", 1)[1].strip() or "pro"
        elif t.startswith("--source="):
            source_title = t.split("=", 1)[1].strip()
        elif not t.startswith("-") and not target_file:
            target_file = t
    if not target_file:
        print(colorize("用法: /ingest <试题文件路径> [--subject=pro/math/eng/pol] [--source=题源出处]\n"
                       "示例: /ingest 2024年408统考真题.txt --subject=pro --source=\"2024统考408真题\"", C.YELLOW))
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
    res = pipe.ingest_file(p, subject=subject, source_name=source_title or p.stem)
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

    # [P20 修复] scan_and_mount_materials 写白名单时以 study_plan["pol_name"] 作为
    # 政治科目名，缺失则回退到 SUBJECT_FOLDER_MAP 的 label「政治」，会把方案向导与
    # 看板使用的规范名「思想政治理论」漂移成「政治」。扫描写回前先补齐规范名；
    # 其余科目维持原行为不变（math/eng/pro 的 label 即其规范名）。
    try:
        _cfg = load_config()
        _plan = _cfg.get("study_plan")
        if isinstance(_plan, dict) and not str(_plan.get("pol_name") or "").strip():
            _plan["pol_name"] = "思想政治理论"
            save_config(_cfg)
    except Exception:
        pass

    print(colorize("\n[🔍 正在智能扫描本地四科 参考资料/ 目录与考研资料库...]\n", C.CYAN))
    mount_res = material_scanner.scan_and_mount_materials()
    if mount_res.get("success"):
        print(colorize(f"✔ 资料挂载完成！共扫描到 {mount_res['total_files']} 份本地参考资料与历年真题：", C.GREEN))
        for k, flist in mount_res["details"].items():
            label = {"math": "数学", "eng": "英语", "pol": "政治", "pro": "专业课"}.get(k, k)
            if flist:
                print(f"  • 【{label}】: {len(flist)} 份实体资料 -> {', '.join(flist)}")
            else:
                print(f"  • 【{label}】: 暂无本地资料 (私教遵循官方考纲出题)")
        if mount_res.get("school_watch"):
            print(colorize(f"\n[📡 研招联动]: {mount_res['school_watch']}", C.CYAN))
        print(colorize("\n🎉 参考资料白名单与目标院校雷达已同步写回 ky_config.json 与 AGENTS.md！\n", C.GREEN))
    else:
        print(colorize(f"[!] 资料挂载失败: {mount_res.get('msg')}", C.RED))


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
register(Command('mount', ("mount", "scan", "--mount", "--scan"), '[目录]', '挂载 / 扫描本地资料目录（白名单题源门禁扫描）', handler=_cmd_mount, write=True))
register(Command('key', ("key", "--key", "keys", "--keys"), '[list|set] <试卷编号> [题号] ["标准答案"]', '管理自测卷的加密标准答案（判卷自动采分依赖它）', handler=_cmd_key, write=True))
