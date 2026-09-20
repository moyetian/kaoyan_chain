# -*- coding: utf-8 -*-
"""
日常备考命令模块 (daily.py)
包含 today / done / map / calc / wechat
"""

import json
import sys
from pathlib import Path
from typing import List

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.shared import load_config, mark_today_task_done
    from tools.cli.repl.renderer import C, colorize, print_today_tasks_summary
except ImportError:
    from cli.dispatch import Command, register
    from cli.shared import load_config, mark_today_task_done
    from cli.repl.renderer import C, colorize, print_today_tasks_summary


def _cmd_today(args: List[str]) -> None:
    as_json = "--json" in args or "-j" in args
    show_flash = "--no-flash" not in args
    print_today_tasks_summary(as_json=as_json, show_flash=show_flash)


def _cmd_done(args: List[str]) -> None:
    if len(args) < 2:
        print(colorize("用法: ky done <任务关键词>\n示例: ky done 导数中值定理", C.YELLOW))
        sys.exit(1)
    kw = " ".join(args[1:])
    ok, msg = mark_today_task_done(kw)
    print(colorize(f"[{msg}]", C.GREEN if ok else C.YELLOW))
    sys.exit(0 if ok else 1)


def _cmd_map(args: List[str]) -> None:
    target_subj = load_config().get("active_subject", "math")
    as_json = "--json" in args or "-j" in args
    for a in args[1:]:
        if a in ("--json", "-j"):
            continue
        for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
            if sk in a.lower() or sv in a:
                target_subj = sk
                break
    try:
        from tools.skills import knowledge_map
    except ImportError:
        try:
            from skills import knowledge_map
        except ImportError:
            knowledge_map = None
    if knowledge_map:
        if as_json:
            data = knowledge_map.build_knowledge_map(target_subj)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(knowledge_map.format_knowledge_map_table(target_subj))
    else:
        print("knowledge_map 技能模块未载入")


def _cmd_calc(args: List[str]) -> None:
    if len(args) < 2 or "--help" in args or "-h" in args:
        print(colorize("""
考研数学符号高精度验算引擎 (ky calc / verify)
用法：
  ky calc <数学表达式>
示例：
  ky calc "limit (ln(1+x)-x)/x^2 as x->0"
  ky calc "diff x^3 * sin(x)"
  ky calc "int x * exp(x) dx"
  ky calc "ode y'' + 4*y = 0"
  ky calc "det [[1,2],[3,4]]"
说明：
  基于 SymPy 高精度符号计算库，杜绝大模型计算幻觉，提供 100% 精确的推导验算与 LaTeX 渲染。
""", C.YELLOW))
        sys.exit(0 if ("--help" in args or "-h" in args) else 1)
    expr = " ".join(args[1:])
    try:
        from tools.skills import math_verifier as mv
    except ImportError:
        try:
            from skills import math_verifier as mv
        except ImportError:
            mv = None
    if mv:
        print(colorize("\n[📐 正在运行数学符号验算引擎...]\n", C.CYAN))
        res = mv.run_math_query(expr)
        print(res + "\n")
    else:
        print("math_verifier 技能模块未载入")


def cmd_wechat_search(cli_args: list) -> None:
    """微信公众号文章检索与爬虫子命令处理函数"""
    if not cli_args or cli_args[0] in ("-h", "--help"):
        print("""
用法：ky wechat <关键词> [选项]
别名：ky wx

功能：
  检索微信公众号考研文章与上岸经验贴 (支持搜狗/Bing多源检索与本地沉淀)

参数：
  <关键词>                     检索关键词 (如 "408计算机考研经验"、"华科计算机复试")

选项：
  --max=N                      最大检索结果数 (默认 10)
  --save                       自动沉淀抓取文章到本地 .memory/experiences/ (隐私目录，不入库)
  --no-fetch                   仅检索标题与链接，不抓取正文
  --school=<高校名>            联动更新目标高校社媒口碑档案 (school_scout)
  --source=<auto|sogou|bing|local> 检索数据源 (默认 auto 自动降级)

示例：
  ky wechat "408计算机考研经验" --max=5 --save
  ky wx "华科计算机复试" --school=华中科技大学 --save
""")
        return

    keyword = " ".join(a for a in cli_args if not a.startswith("-")).strip()
    max_results = 10
    save_to_local = False
    fetch_content = True
    school_name = ""
    source = "auto"

    for arg in cli_args[1:]:
        if not arg.startswith("-"):
            continue
        if arg.startswith("--max="):
            try:
                max_results = int(arg.split("=", 1)[1])
            except ValueError:
                pass
        elif arg == "--save":
            save_to_local = True
        elif arg == "--no-fetch":
            fetch_content = False
        elif arg.startswith("--school="):
            school_name = arg.split("=", 1)[1].strip()
        elif arg.startswith("--source="):
            source = arg.split("=", 1)[1].strip()

    try:
        from tools.skills.wechat_searcher import denoise_keyword, wechat_search
    except ImportError:
        try:
            from skills.wechat_searcher import denoise_keyword, wechat_search
        except ImportError:
            print("wechat_searcher 技能模块未载入")
            return

    _kw_used = denoise_keyword(keyword)
    if _kw_used != keyword:
        print(colorize(
            f"  [i] 已自动去噪（专业代码/括号会导致检索恒 0 命中）:\n"
            f"      输入: 「{keyword}」\n"
            f"      实际: 「{_kw_used}」", C.YELLOW))

    print(colorize(f"\n▶ 正在多源检索微信公众号考研文章: 「{_kw_used}」 (源: {source}) ...", C.BOLD + C.CYAN))
    res = wechat_search(
        keyword=keyword,
        max_results=max_results,
        fetch_content=fetch_content,
        save_to_local=save_to_local,
        school_name=school_name,
        source=source
    )

    print("\n" + "=" * 62)
    print(colorize("  📱 微信公众号考研文章检索报告", C.BOLD))
    print("=" * 62)
    print(f"关键词: {res['keyword']}  |  总计发现: {res['total']} 篇  |  已抓取正文: {res['fetched']} 篇\n")

    if not res["results"]:
        print("  [i] 未检索到相关文章，建议更换关键词或使用 --source=bing / --source=local 重试。")
        for _err in res.get("source_errors") or []:
            print(colorize(f"  [!] 检索源告警（可能是反爬验证或站点改版，不代表关键词无结果）: {_err}", C.YELLOW))
    else:
        for idx, it in enumerate(res["results"], 1):
            st = "✅ 已抓取" if it.get("fetched") else "📋 仅标题"
            print(f"  [{idx}] {it.get('title')}")
            print(f"      公众号: {it.get('account_display') or it.get('source_account') or '未识别（平台未公开）'}"
                  f"  |  日期: {it.get('date_display') or it.get('publish_date') or '未标注日期'}  |  {st}")
            summary = it.get('summary', '')
            if summary:
                print(f"      摘要: {summary[:80]}...")
            print(f"      链接: {it.get('url')}")
            print()

    if res.get("saved_paths"):
        print(colorize(f"  📥 已沉淀 {len(res['saved_paths'])} 篇优质文章至 .memory/experiences/ (本地隐私目录)", C.GREEN))
        for sp in res["saved_paths"]:
            print(f"     - {Path(sp).name}")
    if res.get("scout_linked"):
        print(colorize(f"  🔗 已成功联动院校侦察引擎 (school_scout) 更新【{school_name}】口碑档案", C.GREEN))
    print()


def _cmd_wechat(args: List[str]) -> None:
    if "--clawbot" in args:
        try:
            from tools.cli.config import run_wechat_clawbot_install
        except ImportError:
            from cli.config import run_wechat_clawbot_install
        run_wechat_clawbot_install()
    else:
        cmd_wechat_search(args[1:])


# 注册日常命令
register(Command('today', ("today", "--today", "tasks", "--tasks"), '[--json] [--no-flash]', '查看今日四科任务清单；加 --json 输出结构化数据；--no-flash 隐藏研招速递', handler=_cmd_today))
register(Command('done', ("done", "--done"), '<关键词>', '快速将包含关键词的今日任务标记为完成并回写状态', handler=_cmd_done, write=True))
register(Command('map', ("map", "--map", "knowledge", "--knowledge"), '[科目] [--json]', '官方考试大纲知识点图谱与掌握度映射', handler=_cmd_map))
register(Command('calc', ("calc", "--calc", "verify", "--verify"), '<表达式>', '基于 SymPy 高精度数学符号验算 (极限/导数/积分/ODE/矩阵，别名: verify)', handler=_cmd_calc))
register(Command('wechat', ("wechat", "--wechat", "wx", "--wx"), '<关键词> [--max=N] [--save]', '多源检索微信公众号考研文章与经验沉淀 (别名: wx)', handler=_cmd_wechat))
