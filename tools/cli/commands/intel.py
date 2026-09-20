# -*- coding: utf-8 -*-
"""
招考情报与考纲分析命令模块 (intel.py)
包含 scout / compare / admission / watch / fetch / render_syllabus_diff / run_syllabus_diff
"""

import sys
import time
from pathlib import Path
from typing import List

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.shared import (
        ROOT,
        _discover_new_syllabus,
        load_config,
        resolve_major_keyword,
        resolve_profile_schools,
    )
    from tools.cli.repl.renderer import C, colorize
    from tools.cli.agent.engine import build_demo_syllabus_text
except ImportError:
    from cli.dispatch import Command, register
    from cli.shared import (
        ROOT,
        _discover_new_syllabus,
        load_config,
        resolve_major_keyword,
        resolve_profile_schools,
    )
    from cli.repl.renderer import C, colorize
    from cli.agent.engine import build_demo_syllabus_text


def _get_intelligence_module():
    try:
        from tools import intelligence
        return intelligence
    except ImportError:
        try:
            import intelligence
            return intelligence
        except ImportError:
            return None


def _cmd_scout(args: List[str]) -> None:
    school_name = ""
    major_name = ""
    include_social = True
    save_flag = False
    apply_flag = False

    pos_args = []
    for a in args[1:]:
        if a in ("--no-social", "-ns"):
            include_social = False
        elif a in ("--save", "-s"):
            save_flag = True
        elif a in ("--apply", "-a"):
            apply_flag = True
        elif not a.startswith("-"):
            pos_args.append(a)

    if pos_args:
        school_name = pos_args[0]
        if len(pos_args) > 1:
            major_name = pos_args[1]
    else:
        cfg = load_config()
        school_name = cfg.get("study_plan", {}).get("school", "")
        major_name = cfg.get("study_plan", {}).get("major", "")

    if "--help" in args or "-h" in args or (not school_name or school_name == "目标院校"):
        print(colorize("用法: ky scout <高校名> [专业名] [--no-social] [--save] [--apply]\n示例: ky scout 华中科技大学 计算机 --save\n说明: 定向侦察目标院校研究生院官网招生简章、自命题大纲、拟招人数，并聚合知乎/B站/小红书口碑与避坑指南。", C.YELLOW))
        sys.exit(0 if ("--help" in args or "-h" in args) else 1)

    try:
        from tools.skills import school_scout
    except ImportError:
        try:
            from skills import school_scout
        except ImportError:
            school_scout = None

    if school_scout:
        print(colorize(f"\n[🎯 正在启动考研目标院校与社媒情报侦察: 【{school_name}】{major_name}]", C.CYAN))
        print("  • 官方研招检索: 研招网 (yz.chsi.com.cn) + 高校研究生院官网 (.edu.cn)")
        if include_social:
            print("  • 社交舆情聚合: 知乎就读体验 + 哔哩哔哩备考贴 + 小红书避坑与压分")
        print("  • 正在提取核心指标与生成情报研报...\n")

        res = school_scout.scout_school(
            school=school_name,
            major=major_name,
            include_social=include_social,
            save_report=save_flag,
            apply_to_config=apply_flag,
            use_llm=True
        )
        print(res.get("formatted_report", ""))

        if res.get("saved_path"):
            print(colorize(f"\n[√ 情报研报已成功落盘至]: {res['saved_path']}", C.GREEN))
        if res.get("applied"):
            print(colorize(f"[√ 目标高校与专业已一键同步至 ky_config.json]", C.GREEN))
        print()
    else:
        print("school_scout 技能模块未载入")


def _cmd_admission(args: List[str]) -> None:
    intel = _get_intelligence_module()
    school_name = ""
    major_name = ""
    year = (intel.current_exam_year() if intel and hasattr(intel, "current_exam_year") else (time.localtime().tm_year + 1))
    save_flag = False
    pos_args = []
    for a in args[1:]:
        if a in ("--save", "-s"):
            save_flag = True
        elif a.startswith("--year="):
            try:
                year = int(a.split("=")[1])
            except Exception:
                pass
        elif not a.startswith("-"):
            pos_args.append(a)

    if pos_args:
        school_name = pos_args[0]
        if len(pos_args) > 1:
            major_name = pos_args[1]
    else:
        cfg = load_config()
        school_name = cfg.get("study_plan", {}).get("school", "")
        major_name = cfg.get("study_plan", {}).get("major", "")

    if "--help" in args or "-h" in args or (not school_name or school_name == "目标院校"):
        print(colorize("用法: ky admission <高校名> [专业代码/名] [--year=2027] [--save]\n示例: ky admission 华中科技大学 085404 --save\n说明: 基于教育部研招网 (S级) 与高校官方站点 (A级) 权威提取初试科目、院系所、招生人数与证据链。", C.YELLOW))
        sys.exit(0 if ("--help" in args or "-h" in args) else 1)

    if intel:
        print(colorize(f"\n[🏛️ KaoYan Intelligence: 正在调取【{school_name}】{major_name} 研招网与官方站点证据链...]\n", C.CYAN))
        engine = intel.get_intelligence_engine()
        res = engine.query(school_query=school_name, major_query=major_name, exam_year=year, save_report=save_flag)
        print(res.get("markdown_report", ""))
        if res.get("saved_path"):
            print(colorize(f"\n[√ 考情证据研报已归档至]: {res['saved_path']}\n", C.GREEN))
    else:
        print(colorize("[!] intelligence 考情引擎模块未载入", C.RED))


def _cmd_watch(args: List[str]) -> None:
    intel = _get_intelligence_module()
    if not intel:
        print(colorize("[!] intelligence 考情引擎模块未载入", C.RED))
        return

    watcher = intel.AdmissionWatcher()
    sub = args[1] if len(args) > 1 else ""
    if sub in ("--help", "-h"):
        print(colorize("用法: ky watch [高校名] [--check] [--list] [--remove <高校名>]\n示例:\n  ky watch 华中科技大学       # 添加华科至监控雷达\n  ky watch --check           # 立即检查所有监控高校最新简章动态\n  ky watch --list            # 查看已监控高校清单", C.YELLOW))
        sys.exit(0)
    elif sub in ("--check", "-c", "check"):
        print(colorize("\n[📡 正在轮询监控高校研究生院与研招办最新简章公告...]\n", C.CYAN))
        findings = watcher.check_updates()
        for f in findings:
            if f.get("status") == "UPDATED":
                print(colorize(f"  🔥 [发现新动态] {f['school']}:", C.GREEN))
                for t in f.get("alert_titles", []):
                    print(f"     - {t}")
            elif f.get("status") == "UNCHANGED":
                print(colorize(f"  ✓ {f['school']}: 站点指纹正常，暂无新增简章", C.BLUE))
            else:
                print(colorize(f"  ⚠️ {f['school']}: {f.get('msg', '请求超时')}", C.YELLOW))
        print()
    elif sub in ("--list", "-l", "list") or not sub:
        watched = watcher.list_watched()
        if not watched:
            print(colorize("当前暂未配置监控高校。添加监控示例: ky watch 华中科技大学", C.YELLOW))
        else:
            print(colorize(f"\n[📡 当前动态监控高校雷达 ({len(watched)} 所)]:", C.CYAN))
            for w in watched:
                print(f"  • {w['name']} (代码: {w['chsi_code']}) ｜ 最近检查: {w.get('last_check', '未检查')}")
            print(colorize("\n提示: 运行 ky watch --check 立即比对最新简章变动", C.CYAN))
    elif sub in ("--remove", "--rm", "-d", "remove"):
        target = args[2] if len(args) > 2 else ""
        if watcher.remove_watch(target):
            print(colorize(f"[√ 已取消对【{target}】的动态监控]", C.GREEN))
        else:
            print(colorize(f"[!] 未找到监控目标【{target}】", C.YELLOW))
    else:
        if sub.startswith("-"):
            print(colorize(
                f"[!] 未知参数: {sub}\n"
                f"    支持: ky watch <高校名> | --check | --list | --remove <高校名> | --help",
                C.YELLOW))
        else:
            res = watcher.add_watch(sub)
            if res.get("success"):
                print(colorize(f"[√ {res.get('msg')}]: 官方入口 {res.get('url')}", C.GREEN))
            else:
                print(colorize(f"[!] 添加失败: {res.get('msg')}", C.RED))


def render_syllabus_diff(res, school, major, y1, y2) -> None:
    """考纲 Diff 看板渲染，供 REPL /diff 与 CLI 共用同一口径。"""
    m = res["metrics"]
    print(colorize(f"=== 考纲变动全景看板 · {school} ({y1} vs {y2}) ===", C.BOLD))
    print(f"  • 变动等级: {colorize(m['stability_grade'], C.GREEN if m['volatility_percentage'] < 10 else C.YELLOW)} (波动率: {m['volatility_percentage']}%)")
    print(f"  • 考点统计: 基准 {m['total_old']} 项 ➔ 最新 {m['total_new']} 项 ({m['total_new'] - m['total_old']:+d})")
    print(f"  • 🚨 新增考点: {colorize(str(m['added_count']) + ' 处 (当年高危必考点)', C.RED)}")
    print(f"  • 🍃 剔除考点: {colorize(str(m['removed_count']) + ' 处 (已彻底移出考纲，减负止损)', C.GREEN)}")
    print(f"  • ⚠️ 考查微调: {colorize(str(m['modified_count']) + ' 处 (掌握等级升降级调整)', C.YELLOW)}")
    print(f"  • 🔒 稳定考点: {m['unchanged_count']} 处\n")
    if m["added_count"] > 0:
        print(colorize("【🚨 新增考点清单】", C.RED))
        for d in res["diff_items"]:
            if d.change_type == "ADDED":
                p = d.point_new
                print(f"  + [{p.module} / {p.chapter}] [{p.requirement}] {p.text}")
        print()
    if m["removed_count"] > 0:
        print(colorize("【🍃 删减考点清单】", C.GREEN))
        for d in res["diff_items"]:
            if d.change_type == "REMOVED":
                p = d.point_old
                print(f"  - [{p.module} / {p.chapter}] {p.text}")
        print()
    if m["modified_count"] > 0:
        print(colorize("【⚠️ 考查要求微调清单】", C.YELLOW))
        for d in res["diff_items"]:
            if d.change_type == "MODIFIED":
                p = d.point_new or d.point_old
                print(f"  ~ {d.detail} ｜ 考点: {p.text}")
        print()


def run_syllabus_diff(arg: str = "") -> bool:
    """REPL /diff 实现：与 CLI ky fetch diff 同源同口径。"""
    intel = _get_intelligence_module()
    if not intel:
        print(colorize("[!] intelligence 模块未载入", C.RED))
        return False

    tokens = str(arg or "").split()
    old_path, new_path = "", ""
    free = []
    for t in tokens:
        if t.startswith("--old="):
            old_path = t.split("=", 1)[1].strip()
        elif t.startswith("--new="):
            new_path = t.split("=", 1)[1].strip()
        elif t.startswith("--subject=") or t.startswith("-s="):
            continue
        else:
            free.append(t)
    if free and not old_path:
        old_path = free[0]

    cfg = load_config()
    plan = cfg.get("study_plan", {}) or {}
    school = (plan.get("school") or "目标院校").strip() or "目标院校"
    major = (plan.get("major") or "专业课").strip() or "专业课"
    try:
        from intelligence.models import current_exam_year as _cey
        y2 = _cey()
    except Exception:
        y2 = 2027
    y1 = y2 - 1

    diff_gen = intel.get_syllabus_diff_generator()
    print(colorize(f"\n[📊 KaoYan Intelligence: 正在比对【{school}】{major} 大纲考点版本异动 ({y1} vs {y2})...]\n", C.CYAN))

    base_text = ""
    if old_path and Path(old_path).exists():
        base_text = Path(old_path).read_text(encoding="utf-8", errors="ignore")
    elif (ROOT / "04-专业课" / "考试大纲.md").exists():
        base_text = (ROOT / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8", errors="ignore")
    else:
        try:
            import syllabus_manager as sm
            base_text = sm.CS408_SYLLABUS if isinstance(sm.CS408_SYLLABUS, str) else sm.CS408_SYLLABUS.get("content", "")
        except Exception:
            print(colorize("[!] 找不到基准考纲文件（04-专业课/考试大纲.md），请在 /subject 中先维护考纲", C.YELLOW))
            return False

    demo_mode = False
    new_text = base_text
    if new_path and Path(new_path).exists():
        new_text = Path(new_path).read_text(encoding="utf-8", errors="ignore")
    else:
        cand = _discover_new_syllabus()
        if cand:
            try:
                rel_p = str(cand.relative_to(ROOT))
            except Exception:
                rel_p = str(cand)
            print(colorize(f"\n[💡 考纲自动关联] 检测到参考资料库候选新考纲文件: {rel_p}", C.GREEN))
            new_text = cand.read_text(encoding="utf-8", errors="ignore")
        else:
            try:
                from intelligence.models import current_exam_year as _cey
                demo_year = _cey()
            except Exception:
                demo_year = y2
            print(colorize(
                "\n[⚠️ 演示模式] 未提供真实新考纲文件 (可用 --new=<路径> 指定)。\n"
                f"以下对比使用内置演示样例变动，并非官方大纲，结果仅用于了解 Diff 功能，"
                f"严禁作为备考依据！", C.RED))
            new_text = build_demo_syllabus_text(base_text, demo_year)
            demo_mode = True

    res = diff_gen.compare_texts(old_text=base_text, new_text=new_text, school=school, major=major, year_old=y1, year_new=y2)
    res["is_demo"] = demo_mode
    render_syllabus_diff(res, school, major, y1, y2)
    saved_p = diff_gen.save_diff_report(res)
    print(colorize(f"[√ 考纲异动深度研报已生成并归档至]: {saved_p}\n", C.GREEN))
    return True


def _cmd_fetch(args: List[str]) -> None:
    intel = _get_intelligence_module()
    sub = args[1].lower() if len(args) > 1 else ""
    # `ky diff --help` 被 dispatch 转成 `ky fetch diff --help`，此时 --help 落在
    # args[2]。此前只认 args[1]，导致 `ky diff --help` 不打印帮助、反而直接执行并落盘。
    if sub in ("--help", "-h", "") or any(a in ("--help", "-h") for a in args[2:]):
        print(colorize("""
考研招考情报与大纲变动抓取中枢 (ky fetch)
用法：
  ky fetch info <高校名/代码> [专业] [--year=2027] [--save]
  权威调取研招网与高校官方站点招生计划、考试科目与招考证据链
  ky fetch diff [--school=高校] [--major=专业] [--old=旧考纲] [--new=新考纲] [--year1=2026] [--year2=2027] [--save]
  生成大纲考点版本变化对比研报 (逐级比对新增/删减/调整考点，输出突破处方)
  ky fetch watch [--check|--list|add|remove]
  检查或管理目标高校研究生院招生简章与自命题大纲指纹动态
示例：
  ky fetch info 华中科技大学 085404 --save
  ky fetch diff --school 华中科技大学 --major 计算机 --save
  ky fetch watch --check
""", C.YELLOW))
        sys.exit(0)
    elif sub in ("info", "admission"):
        _cmd_admission(["admission"] + args[2:])
    elif sub in ("diff", "--diff"):
        school, major = "目标院校", "专业课"
        old_path, new_path = None, None
        try:
            from intelligence.models import current_exam_year as _cey
            y2 = _cey()
        except Exception:
            y2 = 2027
        y1 = y2 - 1
        pos_args = []
        save_flag = False
        for a in args[2:]:
            if a.startswith("--school="): school = a.split("=", 1)[1].strip()
            elif a.startswith("--major="): major = a.split("=", 1)[1].strip()
            elif a.startswith("--old="): old_path = a.split("=", 1)[1].strip()
            elif a.startswith("--new="): new_path = a.split("=", 1)[1].strip()
            elif a.startswith("--year1="):
                try: y1 = int(a.split("=", 1)[1].strip())
                except (ValueError, TypeError): pass
            elif a.startswith("--year2="):
                try: y2 = int(a.split("=", 1)[1].strip())
                except (ValueError, TypeError): pass
            elif a == "--save": save_flag = True
            elif not a.startswith("-"): pos_args.append(a)

        if pos_args:
            if len(pos_args) >= 1 and school == "目标院校": school = pos_args[0]
            if len(pos_args) >= 2 and major == "专业课": major = pos_args[1]

        cfg = load_config()
        if school in ("", "目标院校") and cfg.get("study_plan", {}).get("school"):
            school = cfg.get("study_plan", {}).get("school")
        if major in ("", "专业课") and cfg.get("study_plan", {}).get("major"):
            major = cfg.get("study_plan", {}).get("major")

        if intel:
            diff_gen = intel.get_syllabus_diff_generator()
            demo_mode = False
            print(colorize(f"\n[📊 KaoYan Intelligence: 正在比对【{school}】{major} 大纲考点版本异动 ({y1} vs {y2})...]\n", C.CYAN))

            if old_path and new_path and Path(old_path).exists() and Path(new_path).exists():
                res = diff_gen.compare_files(old_file=Path(old_path), new_file=Path(new_path), school=school, major=major, year_old=y1, year_new=y2)
            else:
                try:
                    import syllabus_manager as sm
                    base_text = sm.CS408_SYLLABUS if isinstance(sm.CS408_SYLLABUS, str) else sm.CS408_SYLLABUS.get("content", "")
                except Exception:
                    base_text = ""
                if old_path and Path(old_path).exists():
                    base_text = Path(old_path).read_text(encoding="utf-8", errors="ignore")
                elif (ROOT / "04-专业课" / "考试大纲.md").exists():
                    base_text = (ROOT / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8", errors="ignore")

                candidate_new = _discover_new_syllabus()
                new_text = base_text
                if new_path and Path(new_path).exists():
                    new_text = Path(new_path).read_text(encoding="utf-8", errors="ignore")
                elif candidate_new and candidate_new.exists():
                    try: rel_p = str(candidate_new.relative_to(ROOT))
                    except Exception: rel_p = str(candidate_new)
                    print(colorize(f"\n[💡 考纲自动关联] 检测到参考资料库候选新考纲文件: {rel_p}", C.GREEN))
                    new_text = candidate_new.read_text(encoding="utf-8", errors="ignore")
                else:
                    try:
                        from intelligence.models import current_exam_year as _cey
                        demo_year = _cey()
                    except Exception: demo_year = y2
                    print(colorize(
                        "\n[⚠️ 演示模式] 未提供真实新考纲文件 (可用 --new=<路径> 指定)。\n"
                        f"以下对比使用内置演示样例变动，并非官方大纲，结果仅用于了解 Diff 功能，"
                        f"严禁作为备考依据！", C.RED))
                    new_text = build_demo_syllabus_text(base_text, demo_year)
                    demo_mode = True

                res = diff_gen.compare_texts(old_text=base_text, new_text=new_text, school=school, major=major, year_old=y1, year_new=y2)
                res["is_demo"] = demo_mode

            render_syllabus_diff(res, school, major, y1, y2)
            # [缺陷修复] 此前无条件 save_diff_report，帮助里宣称的 [--save] 形同虚设。
            # 现改为默认仅预览，显式 --save 才落盘（与 sync_publish/update_dashboard
            # 的「默认安全、显式动作」约定一致）。
            if save_flag:
                saved_p = diff_gen.save_diff_report(res)
                print(colorize(f"[√ 考纲异动深度研报已生成并归档至]: {saved_p}\n", C.GREEN))
            else:
                print(colorize("[i] 未指定 --save：本次仅预览，未落盘研报（加 --save 归档）。\n", C.DIM))
        else:
            print(colorize("[!] intelligence 模块未载入", C.RED))
    elif sub in ("watch", "--watch"):
        _cmd_watch(["watch"] + args[2:])
    else:
        print(colorize(f"未知 fetch 子命令: {sub}，运行 ky fetch --help 查看用法。", C.YELLOW))


def _cmd_compare(args: List[str]) -> None:
    pos_args = []
    save_flag = False
    for a in args[1:]:
        if a in ("--save", "-s"):
            save_flag = True
        elif not a.startswith("-"):
            pos_args.append(a)

    if "--help" in args or "-h" in args:
        print(colorize("用法: ky compare [高校1] [高校2] [专业关键词] [--save]\n"
                       "示例: ky compare 华中科技大学 武汉大学 --save\n"
                       "      省略高校时自动取档案报考院校/备选院校: ky compare --save\n"
                       "说明: 深度横向对标两所高校的办学层次、自划线、初试科目差异 (408/自命题)、复试线与一志愿保护机制。\n"
                       "      专业关键词可省略，默认取 ky_config.json 中的报考专业。", C.YELLOW))
        sys.exit(0)

    prof_s1, prof_s2 = resolve_profile_schools()
    if len(pos_args) >= 2:
        s1, s2 = pos_args[0], pos_args[1]
    elif len(pos_args) == 1:
        s1 = pos_args[0]
        s2 = prof_s2
        if s2:
            print(colorize(f"[i] 未指定第二所高校，自动采用档案备选院校: {s2}", C.CYAN))
    else:
        s1, s2 = prof_s1, prof_s2
        if s1 and s2:
            print(colorize(f"[i] 未指定对标高校，自动采用档案报考院校/备选院校: {s1} vs {s2}", C.CYAN))
    if not s1 or not s2:
        missing = "第一所高校（档案报考院校为空）" if not s1 else "第二所高校（档案备选院校为空）"
        print(colorize(f"[!] 双校对标缺少{missing}，请显式指定：\n"
                       f"      用法: ky compare <高校1> <高校2> [专业关键词] [--save]\n"
                       f"      示例: ky compare 示例院校A 示例院校B --save", C.YELLOW))
        sys.exit(1)

    major_kw = pos_args[2] if len(pos_args) > 2 else resolve_major_keyword()

    intel = _get_intelligence_module()
    if intel:
        print(colorize(f"\n[⚔️ KaoYan Intelligence: 正在对标【{s1}】与【{s2}】({major_kw}) 招考指标与复试保护...]\n", C.CYAN))
        comparator = intel.SchoolComparator()
        res = comparator.compare(school1_query=s1, school2_query=s2, major_keyword=major_kw, save_report=save_flag)
        print(res.get("terminal_report", ""))
        if res.get("saved_path"):
            print(colorize(f"\n[√ 双校横向对比研报已归档至]: {res['saved_path']}\n", C.GREEN))
    else:
        print(colorize("[!] intelligence 考情引擎模块未载入", C.RED))


# 注册招考情报命令
register(Command('scout', ("scout", "--scout"), '<高校名> [专业名] [--save] [--apply]', '定向侦察目标高校招生简章、考试大纲、报录比与知乎/B站口碑', handler=_cmd_scout))
register(Command('admission', ("admission", "--admission", "admit"), '<高校名> [专业] [--year=YYYY] [--save]', '精准调取研招网与高校官方招考指标与证据链', handler=_cmd_admission))
register(Command('watch', ("watch", "--watch"), '[高校名] [--check] [--list] [--remove]', '高校研究生院最新简章与自命题动态指纹监控雷达', handler=_cmd_watch))
register(Command('fetch', ("fetch", "--fetch"), '[info|diff|watch]', '考研招考情报与考纲变动抓取中枢 (研招网/官网/考纲Diff/监控雷达)', handler=_cmd_fetch))
register(Command('compare', ("compare", "--compare", "vs", "--vs"), '<校1> <校2> [专业] [--save]', '双校招考关键指标横向深度对标 (408/自命题/复试线/保护)', handler=_cmd_compare))
