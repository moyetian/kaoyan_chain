# -*- coding: utf-8 -*-
"""
交互式 REPL 主循环 (loop.py)
会话启动、终端打字交互、快捷键派发、自主智能体内核调度
"""

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from tools.cli.shared import (
        ROOT, SUBJECT_DIRS, COACHING_STYLES,
        interpreter_hint,
        load_config, save_config, read_text_safe,
        mark_today_task_done, detect_repl_safe_mode_violation,
        resolve_major_keyword, subject_display_name, is_math_disabled
    )
except ImportError:
    from cli.shared import (
        ROOT, SUBJECT_DIRS, COACHING_STYLES,
        interpreter_hint,
        load_config, save_config, read_text_safe,
        mark_today_task_done, detect_repl_safe_mode_violation,
        resolve_major_keyword, subject_display_name, is_math_disabled
    )

try:
    from tools.cli.repl.renderer import (
        C, colorize, print_welcome, print_command_palette,
        print_status_summary, print_today_tasks_summary, print_followup_toolbar
    )
except ImportError:
    from cli.repl.renderer import (
        C, colorize, print_welcome, print_command_palette,
        print_status_summary, print_today_tasks_summary, print_followup_toolbar
    )

try:
    from tools.cli.repl.session import grab_clipboard_image, get_clipboard_text, ReplSession
except ImportError:
    from cli.repl.session import grab_clipboard_image, get_clipboard_text, ReplSession

try:
    from tools.cli.repl.router import (
        build_homework_menu, build_weakness_scan_report, CHINESE_SUBJECT_MAP
    )
except ImportError:
    from cli.repl.router import (
        build_homework_menu, build_weakness_scan_report, CHINESE_SUBJECT_MAP
    )

try:
    from tools.cli.agent.engine import (
        build_system_prompt, stream_chat, query_llm_reply,
        _count_error_records, _warn_if_mistake_not_archived
    )
except ImportError:
    from cli.agent.engine import (
        build_system_prompt, stream_chat, query_llm_reply,
        _count_error_records, _warn_if_mistake_not_archived
    )

try:
    from tools.cli.gateway import start_background_live_server, append_live_message, show_bridge_guide
except ImportError:
    from cli.gateway import start_background_live_server, append_live_message, show_bridge_guide

try:
    from tools.cli.config import (
        interactive_config, manage_syllabi_cli, run_wechat_clawbot_install
    )
except ImportError:
    from cli.config import (
        interactive_config, manage_syllabi_cli, run_wechat_clawbot_install
    )

try:
    from tools.cli.notify import broadcast_briefing
except ImportError:
    from cli.notify import broadcast_briefing

try:
    from tools.shared import manage_coaching_style
except ImportError:
    try:
        from tools.cli.shared import manage_coaching_style
    except ImportError:
        from cli.shared import manage_coaching_style

try:
    from skills import (
        vision_solver, math_verifier, english_dissector, socratic_tutor,
        error_logger, latex_beautifier, list_skills,
        exam_composer, variant_retriever, knowledge_map, exam_diagnoser, school_scout
    )
except ImportError:
    try:
        from tools.skills import (
            vision_solver, math_verifier, english_dissector, socratic_tutor,
            error_logger, latex_beautifier, list_skills,
            exam_composer, variant_retriever, knowledge_map, exam_diagnoser, school_scout
        )
    except ImportError:
        vision_solver = None
        math_verifier = None
        english_dissector = None
        socratic_tutor = None
        error_logger = None
        latex_beautifier = None
        list_skills = lambda: {}
        exam_composer = None
        variant_retriever = None
        knowledge_map = None
        exam_diagnoser = None
        school_scout = None


def _get_pdf_extractor():
    """惰性获取 PDF 抽取技能（`/pdf` 才用到）。

    [C2 修复] 原实现把 pdf_extractor 放在上面的 eager 导入列表里，而本模块经
    `vision_solver -> ky_cli -> cli.repl.loop` 被间接导入，于是"惰性化 pdf_extractor"
    被这条循环链彻底抵消 —— 每次 CLI 启动仍然会拉起 pypdf + cryptography。
    改为按需获取后，只有真正执行 `/pdf` 时才加载它。
    """
    try:
        from skills import pdf_extractor
    except ImportError:
        try:
            from tools.skills import pdf_extractor
        except ImportError:
            return None
    return pdf_extractor

try:
    import intelligence
except ImportError:
    try:
        from tools import intelligence
    except ImportError:
        intelligence = None

try:
    from agent import AgentRunner
except ImportError:
    try:
        from tools.agent import AgentRunner
    except ImportError:
        AgentRunner = None

try:
    import ky_io
except ImportError:
    try:
        from tools import ky_io
    except ImportError:
        ky_io = None

def run_repl(permission_mode: str = "ask", gateway_host: str = "127.0.0.1", gateway_token: str = "",
             webhook_token: str = "") -> None:
    """启动交互式考研全科专属私教终端 (ky-cli)

    [S1 修复] 新增 ``webhook_token`` 形参：后台伴侣网关此前不透传 ``/webhook``
    专用回调密钥，只能靠环境变量/配置兜底。默认空串时 ``start_background_live_server``
    内部仍会按「环境变量 > ky_config.json」解析，故既有调用方不受影响。
    """
    cfg = load_config()

    live_port = start_background_live_server(8088, host=gateway_host, token=gateway_token,
                                             webhook_token=webhook_token) or 8088
    print_welcome(live_port=live_port)

    # [P2-8 修复·拒绝后每次仍问] 用户明确拒绝（n）后必须记住选择，否则每次启动
    # 都问一遍。拒绝只记录标记不打断流程，随时可用 /plan 或 ky plan 重开。
    if not cfg.get("onboarding_completed") and not cfg.get("onboarding_declined"):
        print(f"""
{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮
│  🎓 欢迎使用考研全科 AI 私人教师中枢！                                 │
│  检测到您尚未进行个人专属定制化必考方案设计。                          │
│  💡 仅需 2~3 分钟即可完成时间倒计时、官方考纲、已有资料白名单、        │
│     当前学情痛点摸底与每日复习黄金作息个性化建档！                     │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")
        try:
            init_plan = input("是否立即启动【个人定制化必考方案设计向导】? (y/n) [y]: ").strip().lower()
            if init_plan != "n":
                import study_planner
                study_planner.run_study_plan_wizard(interactive=True)
                cfg = load_config()
            else:
                cfg["onboarding_declined"] = True
                try:
                    save_config(cfg)
                except Exception:
                    pass
                print(colorize("💡 提示：您可以随时在终端输入 /plan 或运行 ky plan 重新启动向导。\n", C.DIM))
        except (EOFError, KeyboardInterrupt):
            pass

    if not cfg.get("api_key"):
        print(colorize("当前使用本地功能。需要 AI 讲解时，可输入 /config 配置模型。", C.YELLOW))

    curr_subj = cfg.get("active_subject", "math")
    history: List[Dict[str, Any]] = []
    active_quiz_item: Optional[Dict[str, Any]] = None

    agent_runner = None
    if AgentRunner:
        effective_perm = permission_mode if permission_mode != "ask" else (cfg.get("permission_mode") or "ask")
        agent_runner = AgentRunner(
            config=cfg,
            workspace_root=ROOT,
            permission_mode=effective_perm,
            live_callback=append_live_message
        )
        agent_runner.set_subject(curr_subj)

    def _persist_config_or_deny() -> bool:
        """写回 ky_config.json；严格只读模式下拒绝写盘并返回 False。

        [P7 修复·safe 模式堆栈泄漏] 此前科目切换分支直接调用 save_config，
        而 safe 模式下 atomic_write_text 会抛 PermissionDeniedError；该异常一路
        冒泡到顶层，用户看到完整堆栈且进程以 1 退出。此处统一收口，只读模式下
        给一句可理解的中文提示，绝不抛堆栈。
        """
        try:
            save_config(cfg)
            return True
        except Exception as e:
            # [双导入兼容] 本项目同时存在 `ky_io` 与 `tools.ky_io` 两个模块对象
            # （tools 目录也在 sys.path 上），二者的 PermissionDeniedError 并非同一个
            # 类，isinstance 会漏判。与 loop.py 既有做法保持一致，按类名判定。
            if isinstance(e, getattr(ky_io, "PermissionDeniedError", ())) or \
                    type(e).__name__ == "PermissionDeniedError":
                return False
            raise

    def _switch_subject(target_subj: str) -> bool:
        """切换当前科目并持久化；只读模式下拒绝切换（返回 False，状态不变）。"""
        nonlocal curr_subj, history, active_quiz_item
        if target_subj == curr_subj:
            return True
        prev_subj = curr_subj
        curr_subj = target_subj
        cfg["active_subject"] = target_subj
        if not _persist_config_or_deny():
            curr_subj = prev_subj
            cfg["active_subject"] = prev_subj
            print(colorize(
                "\n[!] 严格只读模式 (--permission=safe) 下不可切换科目"
                "（切换需写入 ky_config.json）。\n"
                f"    请以默认权限重启后再切换：{interpreter_hint()} tools/ky_cli.py\n", C.YELLOW))
            return False
        history = []
        active_quiz_item = None
        if agent_runner:
            agent_runner.set_subject(target_subj)
        return True

    def get_prompt_tag():
        _, s_name = SUBJECT_DIRS.get(curr_subj, ("01-数学", "数学"))
        plan = cfg.get("study_plan", {})
        if curr_subj == "math": target = f"{plan.get('math_target', '110+ 分')} 冲刺"
        elif curr_subj == "eng": target = f"{plan.get('eng_target', '65+ 分')} 突破"
        elif curr_subj == "pol": target = f"{plan.get('pol_target', '70+ 分')} 稳拿"
        elif curr_subj == "pro": target = f"{plan.get('pro_target', '120-130 分')} 拔高"
        else: target = "冲刺"
        return f"\n{C.CYAN}╭─{C.RESET} [ {C.BOLD}{s_name}{C.RESET} · {C.YELLOW}{target}{C.RESET} ] {C.DIM}──────────────────────────────────────────{C.RESET}\n{C.CYAN}╰─❯{C.RESET} "

    # [R2-A4 修复] 不考数学时不得再提示「/calc 验算数学」。
    _extra = "" if is_math_disabled(cfg) else "，/calc 验算数学"
    print(colorize(f"当前已激活：{SUBJECT_DIRS[curr_subj][1]}。直接输入问题/题目，或使用 /img 批改草稿{_extra}。", C.DIM))
    if live_port:
        print(colorize(f"🌐 [实时 LaTeX 网页伴侣已就绪]: http://localhost:{live_port}/live (随时输入 /view 自动打开浏览器对照排版)\n", C.CYAN))

    while True:
        try:
            user_input = input(get_prompt_tag()).strip()
        except (KeyboardInterrupt, EOFError):
            if agent_runner and hasattr(agent_runner, "hooks"):
                agent_runner.hooks.trigger_session_end({"active_subject": curr_subj})
            print("\n再见！保持节奏，一战成硕！🎓")
            break

        if not user_input:
            continue

        # ── 数字快捷操作响应 ──
        if user_input == "/1":
            calc_expr = input(colorize("请输入待精确验算的数学式 (如 ode y''+4*y=0, quad [[2,1],[1,2]], limit (sin(x)-x)/x^3 as x->0): ", C.YELLOW)).strip()
            if calc_expr: user_input = f"/calc {calc_expr}"
            else: continue
        elif user_input == "/2" or user_input == "/save" or user_input.startswith("/save "):
            # [B-01 修复] 操作手册 219/466/556 行与 SETUP 72 行均宣称 `/save`
            # 可「一键将当前题干与错因记入错题本」，但代码里从未注册该斜杠指令
            # （只有数字快捷键 `/2`），用户照做会落到「未知指令」分支。
            # 二者功能本就同源，故并列为同一分支的别名。
            # [F4 修复·容忍尾随备注] `/save 补充说明` 这类带尾随文本的写法此前
            # 落进「未知指令」；现与 `/calc <表达式>` 等指令同口径，按「首 token
            # 定路由」处理，多余文本忽略（归档内容仍以批改上下文为准，不吃备注）。
            last_resp = history[-1]["content"] if history and history[-1]["role"] == "assistant" else ""
            last_q = ""
            for h in reversed(history):
                if h.get("role") == "user" and not h.get("content", "").startswith("/"):
                    last_q = h.get("content", "")
                    break
            # [B-01 补修·空上下文守卫] 此前本分支无条件落盘：会话里还没有任何
            # 批改内容时，detail 会退化成占位串「做题记录」、question 为空，于是
            # 写出一条无题干、错因被兜底成「概念漏洞」的垃圾卡片——它既会进
            # FSRS 复测队列，也会污染薄弱点雷达的错因五分类统计。
            # 同族的 `/5` 早有守卫；这里按「必须有可归档的批改内容」补齐，并去掉
            # 那个会产出垃圾记录的占位串默认值。
            # 只卡 last_resp、不卡 last_q：`/dissect`、`/batch`、`/hint` 这类以
            # 斜杠指令发起的分析同样值得归档（它们的 user 条目以 "/" 开头，会被
            # last_q 的扫描跳过），强行要求题干会把它们误拒。
            if not last_resp.strip():
                print(colorize(
                    "\n[!] 暂无可归档的内容：请先提交一次作业（输入「交作业」或 /img 批改草稿），"
                    "再使用 /save 或快捷键 [2]\n", C.YELLOW))
                continue
            err_type = "需强化复练"
            for et in ("概念漏洞", "审题偏差", "公式记错", "计算失误", "书写丢分"):
                if et in last_resp: err_type = et; break
            try:
                res = error_logger.log_error_record(
                    subject=curr_subj,
                    title=f"{SUBJECT_DIRS[curr_subj][1]}重点错题复盘",
                    error_type=err_type,
                    detail=last_resp[:400] + ("..." if len(last_resp) > 400 else ""),
                    prescription="已载入 FSRS 盲盒复测队列（间隔由记忆稳定性自适应给出）。",
                    question=last_q
                )
                print(colorize(f"\n[√] {res}\n", C.GREEN))
            except Exception as _e:
                print(colorize(f"\n[!] 错题未归档：{_e}\n", C.YELLOW))
            continue
        elif user_input == "/3":
            user_input = "/view"
        elif user_input == "/4":
            print(colorize(f"\n[🔄 正在根据上一题考点与易错陷阱为您抽取同类变式真题...]\n", C.CYAN))
            user_input = "请根据上一题的核心考点与命题陷阱，为我抽取一道难度相当的考研真题同类变式题。要求：只给题干背景与设问，不要直接贴答案，让我先独立作答。"
        elif user_input == "/5":
            last_q = ""
            for h in reversed(history):
                if h.get("role") == "user" and not h.get("content", "").startswith("/"):
                    last_q = h.get("content", ""); break
            if not last_q:
                print(colorize("\n[!] 暂无上一题上下文，请直接输入：/hint <题目内容>\n", C.YELLOW))
                continue
            user_input = f"/hint {last_q}"

        if user_input in ("/", "/help", "/h", "help", "？", "?"):
            print_command_palette()
            continue

        raw_cmd = user_input.strip()

        # ── 中文原生口令路由 ──
        if raw_cmd in CHINESE_SUBJECT_MAP:
            # [P7 修复] 科目切换统一走 _switch_subject：safe 模式下会拒绝并提示，
            # 不再抛出未捕获的 PermissionDeniedError 堆栈。
            if not _switch_subject(CHINESE_SUBJECT_MAP[raw_cmd]):
                continue

            plan = cfg.get("study_plan", {})
            subj_name = subject_display_name(cfg, curr_subj)
            hours = plan.get(f"{curr_subj}_hours", 2.0)
            target = plan.get(f"{curr_subj}_target", "高分冲刺")
            weak = plan.get(f"{curr_subj}_weakness", "核心考点攻坚")

            print(colorize(f"\n🎓 【{subj_name} · 私教报到就绪】", C.BOLD + C.GREEN))
            print(f"• 今日规划投入: {C.CYAN}{hours} 小时{C.RESET} ｜ 战役目标: {C.YELLOW}{target}{C.RESET}")
            print(f"• 核心薄弱防线: 【{C.BOLD}{weak}{C.RESET}】")

            due_items = error_logger.get_due_reviews(curr_subj, max_count=3) if error_logger else []
            if due_items:
                active_quiz_item = due_items[0]
                print(colorize(f"\n🔔 检测到您有 {len(due_items)} 道 FSRS 到期错题！根据战役 SOP，私教已为您抽取首题启动盲盒复测：\n", C.YELLOW))
                quiz_card = error_logger.generate_blind_quiz(active_quiz_item)
                print(quiz_card + "\n")
                print(colorize("👉 请在草稿纸上推演作答，输入答案即可核对 (输入 cancel 退出复测，输入 /hint 获取微步骤)：\n", C.CYAN))
            else:
                t_file = ROOT / SUBJECT_DIRS[curr_subj][0] / "_状态" / "今日任务.md"
                task_lines = []
                if t_file.exists():
                    txt = read_text_safe(t_file)
                    for l in txt.splitlines():
                        l_s = l.strip()
                        if re.match(r"^-\s*\[ \]", l_s) or ("|" in l_s and "[ ]" in l_s):
                            task_lines.append(l_s)
                if task_lines:
                    print(colorize(f"\n📋 今日攻坚任务清单（前 2 项）：", C.CYAN))
                    for tl in task_lines[:2]:
                        print(f"  {tl}")
                print(colorize(f"\n💡 私教提示：可直接输入题目或题干提问，输入 /hint 启发破题，或输入 /exam 生成专项自测卷！\n", C.GREEN))
            continue
        elif raw_cmd in ("查漏", "查漏补缺"):
            print(colorize("\n" + build_weakness_scan_report() + "\n", C.RESET))
            continue
        elif raw_cmd in ("更新看板", "刷新看板"):
            build_py = ROOT / "05-考研看板" / "build.py"
            if build_py.exists():
                print(colorize("\n[正在更新并重新编译自测看板...]", C.CYAN))
                import subprocess
                subprocess.run([sys.executable, str(build_py)], cwd=str(ROOT / "05-考研看板"))
            print()
            continue
        elif raw_cmd in ("交作业", "对答案"):
            print(colorize(build_homework_menu(), C.CYAN))
            continue
        elif raw_cmd.startswith("打卡") or raw_cmd.startswith("完成"):
            kw = raw_cmd.replace("打卡", "").replace("完成", "").strip()
            if kw:
                ok, msg = mark_today_task_done(kw, curr_subj)
                tag = C.GREEN if ok else C.YELLOW
                print(colorize(f"\n[{msg}]\n", tag))
                continue
        elif raw_cmd in ("组卷", "反向组卷", "生成试卷") or raw_cmd.startswith("组卷 "):
            sub_target = curr_subj
            c_parts = raw_cmd.split()
            if len(c_parts) > 1:
                for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                    if sk in c_parts[1] or sv in c_parts[1]:
                        sub_target = sk; break
            if exam_composer:
                print(colorize(f"\n[📝 正在基于错题库与高频易错考点为您靶向组卷...]\n", C.CYAN))
                res = exam_composer.compose_exam_paper(sub_target, count=3, save_file=True)
                print(res.get("formatted_paper", ""))
                if res.get("saved_path"):
                    print(colorize(f"[√ 试卷已归档至]: {res['saved_path']}\n", C.GREEN))
            else:
                print(colorize("[!] exam_composer 技能模块未载入", C.RED))
            continue
        elif raw_cmd.startswith("变式") or raw_cmd in ("变式题", "找变式"):
            topic = raw_cmd.replace("变式", "").replace("题", "").replace("找", "").strip()
            if not topic: topic = "导数中值定理" if curr_subj == "math" else "核心高频考点"
            if variant_retriever:
                print(colorize(f"\n[🔍 正在四科白名单题源中检索【{topic}】同类真题变式...]\n", C.CYAN))
                res = variant_retriever.search_real_variant(subject=curr_subj, keyword=topic)
                print(variant_retriever.format_variant_output(res))
            else:
                print(colorize("[!] variant_retriever 技能模块未载入", C.RED))
            continue
        elif raw_cmd in ("知识图谱", "考纲图谱", "知识点图谱") or raw_cmd.startswith("知识图谱 "):
            sub_target = curr_subj
            c_parts = raw_cmd.split()
            if len(c_parts) > 1:
                for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                    if sk in c_parts[1] or sv in c_parts[1]:
                        sub_target = sk; break
            if knowledge_map:
                print(knowledge_map.format_knowledge_map_table(sub_target))
            else:
                print(colorize("[!] knowledge_map 技能模块未载入", C.RED))
            continue
        elif raw_cmd in ("整卷诊断", "试卷诊断") or raw_cmd.startswith("整卷诊断 ") or raw_cmd.startswith("试卷诊断 "):
            text_arg = raw_cmd.replace("整卷诊断", "").replace("试卷诊断", "").strip()
            if not text_arg:
                print(colorize("用法: 整卷诊断 <答题卡文本或文件路径>\n示例: 整卷诊断 1-5: A B C D A", C.YELLOW))
                continue
            if exam_diagnoser:
                content = text_arg
                if Path(text_arg).exists():
                    content = Path(text_arg).read_text(encoding="utf-8", errors="ignore")
                res = exam_diagnoser.diagnose_mock_exam(subject=curr_subj, exam_input=content)
                print(exam_diagnoser.format_diagnosis_report(res))
            else:
                print(colorize("[!] exam_diagnoser 技能模块未载入", C.RED))
            continue
        elif raw_cmd in ("减负", "启动减负", "减负模式"):
            try:
                import study_planner
                res = study_planner.apply_relief_mode()
                print(colorize(f"\n[√ {res.get('message')}]\n" if res.get("success") else f"\n[!] 启动减负失败: {res.get('message')}\n", C.GREEN if res.get("success") else C.RED))
            except Exception as e:
                print(f"启动减负异常: {e}")
            continue
        elif raw_cmd in ("疲劳检查", "防疲劳"):
            try:
                import study_planner
                info = study_planner.check_fatigue_alert()
                if info.get("alert"):
                    print(colorize(f"\n[⚠️ 疲劳警报触发] 连续 {info.get('consecutive_low_days')} 天低完成率 (均值 {info.get('avg_rate')}%):", C.YELLOW))
                    print(info.get("message"))
                    print(colorize("\n💡 提示：输入 减负 或 /relieve 可立即一键启动减负模式。\n", C.CYAN))
                else:
                    print(colorize(f"\n[√ 复习节奏正常] {info.get('message')}\n", C.GREEN))
            except Exception as e:
                print(f"检查疲劳异常: {e}")
            continue

        # ── 智能图片输入检测 ──
        clean_input = user_input.strip().strip('"').strip("'")
        img_pattern = r'([a-zA-Z]:[\\/][^\r\n"\'<>|?*]+?\.(?:png|jpg|jpeg|webp|bmp)|\b[^\s"\'<>|?*]+?\.(?:png|jpg|jpeg|webp|bmp))\b'
        img_match = re.search(img_pattern, user_input, re.IGNORECASE)
        found_img_path = None
        extra_question = ""

        if Path(clean_input).exists() and clean_input.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            found_img_path = clean_input
        elif img_match:
            candidate = img_match.group(1).strip().strip('"').strip("'")
            if Path(candidate).exists():
                found_img_path = candidate
                extra_question = user_input.replace(img_match.group(0), "").strip()
            else:
                cand_name = Path(candidate).name
                search_dirs = [
                    ROOT / "tools" / "scratch" / "uploads",
                    Path.home() / "Desktop",
                    Path.home() / "Downloads",
                    Path(os.environ.get("TEMP", "")) if os.environ.get("TEMP") else None
                ]
                for sd in search_dirs:
                    if sd and (sd / cand_name).exists():
                        found_img_path = str(sd / cand_name)
                        extra_question = user_input.replace(img_match.group(0), "").strip()
                        break

        if not found_img_path and ("[图片" in user_input or "截图" in user_input):
            clip_img = grab_clipboard_image()
            if clip_img:
                found_img_path = str(clip_img)
                extra_question = re.sub(r'\[图片[^\]]*\]', '', user_input).strip()
                print(colorize(f"\n[📸 检测到图片引用，已自动从剪贴板抓取截图: {Path(found_img_path).name}！]", C.GREEN))
            else:
                print(colorize("\n[!] 提示：检测到图片引用，但未找到对应文件或剪贴板截图。\n", C.YELLOW))
                continue

        if found_img_path:
            print(colorize(f"\n[📸 检测到题目/草稿图片: {Path(found_img_path).name}，正在调起考研视觉解题技能...]\n", C.CYAN))
            reply = vision_solver.solve_image_with_model(found_img_path, extra_question, cfg, stream=True)
            if reply:
                append_live_message("user", f"[图片: {Path(found_img_path).name}] {extra_question}")
                append_live_message("assistant", reply)
                history.append({"role": "user", "content": f"[图片批改: {Path(found_img_path).name}] {extra_question}"})
                history.append({"role": "assistant", "content": reply})
                print_followup_toolbar()
            continue

        # ── 斜杠指令与技能分发 ──
        if user_input.startswith("/"):
            cmd_parts = user_input.split(maxsplit=1)
            cmd = cmd_parts[0].lower()
            arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""

            if ky_io is not None and ky_io.is_read_only_mode():
                _viol = detect_repl_safe_mode_violation(cmd, arg)
                if _viol:
                    print(colorize(f"\n[✘ 已拒绝] 严格只读模式 (--permission=safe) 下禁止执行: {_viol}\n", C.RED))
                    continue

            if cmd == "/skills":
                print(colorize("\n=== 🧩 考研专有智能体技能中心 (Skills Registry) ===", C.BOLD))
                for sk_id, sk in list_skills().items():
                    print(f"\n  {sk['name']} [{colorize(sk['status'], C.GREEN)}]\n    - 功能: {sk['desc']}\n    - 指令: {colorize(sk['command'], C.YELLOW)}")
                print()
                continue
            elif cmd in ("/paste", "/clip", "/v"):
                clip_img = grab_clipboard_image()
                if not clip_img:
                    print(colorize("\n[!] 当前剪贴板未检测到截图！请先截图后输入 /paste 批改。\n", C.YELLOW))
                    continue
                print(colorize(f"\n[📸 读取剪贴板截图: {clip_img.name}，正在分析...]\n", C.CYAN))
                reply = vision_solver.solve_image_with_model(str(clip_img), arg, cfg, stream=True)
                if reply:
                    append_live_message("user", f"[剪贴板截图: {clip_img.name}] {arg}")
                    append_live_message("assistant", reply)
                    history.append({"role": "user", "content": f"[剪贴板批改: {clip_img.name}] {arg}"})
                    history.append({"role": "assistant", "content": reply})
                    print_followup_toolbar()
                continue
            elif cmd in ("/img", "/ocr"):
                img_p = ""
                extra = ""
                if not arg:
                    clip_img = grab_clipboard_image()
                    if clip_img: img_p = str(clip_img)
                    else:
                        print(colorize("用法: /img <图片路径> [补充要求]\n", C.YELLOW))
                        continue
                else:
                    parts = arg.split(maxsplit=1)
                    img_p = parts[0].strip('"').strip("'")
                    extra = parts[1] if len(parts) > 1 else ""
                if not Path(img_p).exists():
                    print(colorize(f"\n[!] 未找到图片: {img_p}\n", C.RED))
                    continue
                print(colorize(f"\n[📸 正在调起多模态视觉技能分析: {Path(img_p).name}...]\n", C.CYAN))
                reply = vision_solver.solve_image_with_model(img_p, extra, cfg, stream=True)
                if reply:
                    append_live_message("user", f"[图片: {Path(img_p).name}] {extra}")
                    append_live_message("assistant", reply)
                    history.append({"role": "user", "content": f"[图片批改: {Path(img_p).name}] {extra}"})
                    history.append({"role": "assistant", "content": reply})
                    print_followup_toolbar()
                continue
            elif cmd in ("/calc", "/verify"):
                if not arg:
                    print(colorize("用法: /calc <数学表达式>\n示例: /calc limit (sin(x)-x)/x^3 as x->0", C.YELLOW))
                    continue
                print(colorize(f"\n[📐 正在运行数学符号验算引擎...]\n", C.CYAN))
                if math_verifier:
                    res = math_verifier.run_math_query(arg)
                    print(res + "\n")
                else:
                    print("math_verifier 未载入")
                print_followup_toolbar()
                continue
            elif cmd in ("/review", "/quiz"):
                target_subj = curr_subj
                if arg:
                    for s_k, s_v in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                        if s_k in arg.lower() or s_v in arg: target_subj = s_k; break
                due_items = error_logger.get_due_reviews(target_subj, max_count=5) if error_logger else []
                if not due_items:
                    print(colorize(f"\n[🎉 恭喜] {SUBJECT_DIRS[target_subj][1]} 当前没有到期需要 FSRS 复测的错题！掌握度优良！\n", C.GREEN))
                    continue
                active_quiz_item = due_items[0]
                quiz_card = error_logger.generate_blind_quiz(active_quiz_item)
                print(quiz_card + "\n")
                print(colorize("👉 请直接在下方输入推导步骤或答案进行核对 (输入 cancel 退出复测)：\n", C.CYAN))
                continue
            elif cmd in ("/hint", "/tishi"):
                target_q = arg
                if not target_q:
                    for h in reversed(history):
                        if h.get("role") == "user" and not h.get("content", "").startswith("/"):
                            target_q = h.get("content", ""); break
                if not target_q:
                    print(colorize("用法: /hint <题目内容> 或做题卡壳时直接输入 /hint\n", C.YELLOW))
                    continue
                hint_lvl = 1
                for h in history[-4:]:
                    c = h.get("content", "")
                    if "【第 1 级启发性提示】" in c: hint_lvl = 2
                    if "【第 2 级启发性提示】" in c: hint_lvl = 3
                print(colorize(f"\n[💡 苏格拉底导师正在为您构建 Level {hint_lvl} 微步骤启发...]\n", C.CYAN))
                hint_prompt = socratic_tutor.build_hint_prompt(target_q, hint_level=hint_lvl) if socratic_tutor else target_q
                messages = [
                    {"role": "system", "content": "你是一位深谙苏格拉底式启发教学理念的考研专属私教总教练。"},
                    {"role": "user", "content": hint_prompt}
                ]
                reply = stream_chat(messages, cfg)
                if reply:
                    history.append({"role": "user", "content": f"/hint {target_q}"})
                    history.append({"role": "assistant", "content": f"【第 {hint_lvl} 级启发性提示】\n{reply}"})
                    print_followup_toolbar()
                continue
            elif cmd == "/submit":
                # [B-01 修复] README / 操作手册宣称的 `/submit` 斜杠指令此前在代码里
                # 不存在（只有中文口令「交作业」/「对答案」生效）。这里与中文口令
                # 复用同一处理器 build_homework_menu，实现与文档对齐。
                print(colorize(build_homework_menu(), C.CYAN))
                continue
            elif cmd in ("/batch", "/answers"):
                if not arg:
                    print(colorize("用法: /batch <你的选项序列> [标准答案序列]\n", C.YELLOW))
                    continue
                print(colorize(f"\n[📊 正在核对客观题答题卡并统计正答率与错题考点...]\n", C.CYAN))
                batch_prompt = f"请批量核对以下客观选择题并统计正误、标明考纲考点：\n```text\n{arg}\n```"
                messages = [{"role": "system", "content": "你是一位考研答题卡批改专家。"}, {"role": "user", "content": batch_prompt}]
                reply = stream_chat(messages, cfg)
                if reply:
                    history.append({"role": "user", "content": f"/batch {arg}"})
                    history.append({"role": "assistant", "content": reply})
                    print_followup_toolbar()
                continue
            elif cmd in ("/dissect", "/chai"):
                if not arg:
                    print(colorize("用法: /dissect <英语长难句>\n", C.YELLOW))
                    continue
                dissect_prompt = english_dissector.build_dissection_prompt(arg) if english_dissector else arg
                messages = [{"role": "system", "content": "你是一位考研英语长难句拆解专家。"}, {"role": "user", "content": dissect_prompt}]
                print(colorize(f"\n[🧱 正在执行长难句搭积木分层切分...]\n", C.CYAN))
                reply = stream_chat(messages, cfg)
                if reply:
                    history.append({"role": "user", "content": f"/dissect {arg}"})
                    history.append({"role": "assistant", "content": reply})
                continue
            elif cmd == "/pdf":
                pdf_extractor = _get_pdf_extractor()
                if pdf_extractor is None:
                    print(colorize("\n[!] PDF 抽取技能不可用（缺少 pypdf）。"
                                   "请运行: pip install pypdf\n", C.YELLOW))
                    continue
                if arg:
                    print(colorize(f"\n[📚 检索关键词: {arg}...]\n", C.CYAN))
                    matches = pdf_extractor.search_text_in_materials(arg)
                    for m in matches[:10]: print("  " + m)
                else:
                    mats = pdf_extractor.list_materials()
                    print(colorize("\n[📚 四科「参考资料/」文献清单]:", C.CYAN))
                    for s, flist in mats.items(): print(f"  - {s}: {', '.join(flist) if flist else '暂无文件'}")
                print()
                continue
            elif cmd in ("/math", "/shuxue"):
                if _switch_subject("math"):
                    print(colorize(f"\n[已切换至：{SUBJECT_DIRS['math'][1]}] 上下文已重载。\n", C.GREEN))
                continue
            elif cmd in ("/eng", "/yingyu"):
                if _switch_subject("eng"):
                    print(colorize(f"\n[已切换至：{SUBJECT_DIRS['eng'][1]}] 上下文已重载。\n", C.GREEN))
                continue
            elif cmd in ("/pol", "/zhengzhi"):
                if _switch_subject("pol"):
                    print(colorize(f"\n[已切换至：{SUBJECT_DIRS['pol'][1]}] 上下文已重载。\n", C.GREEN))
                continue
            elif cmd in ("/pro", "/zhuanye"):
                if _switch_subject("pro"):
                    print(colorize(f"\n[已切换至：{SUBJECT_DIRS['pro'][1]}] 上下文已重载。\n", C.GREEN))
                continue
            elif cmd == "/style":
                new_style, changed = manage_coaching_style(arg)
                if changed: print(colorize(f"\n[√ 辅导风格切换成功] 当前已激活：{new_style}\n", C.GREEN))
                else:
                    cur_s, _ = manage_coaching_style()
                    print(colorize(f"\n=== 🎯 当前私教辅导风格: {cur_s} ===", C.BOLD))
                    for k, (name, desc) in COACHING_STYLES.items():
                        mark = colorize(" [当前激活]", C.GREEN) if name == cur_s else ""
                        print(f"  [{k}] {name}{mark}\n      {desc}")
                    print("切换命令示例: /style 1 或 /style 2\n")
                continue
            elif cmd == "/done":
                if not arg:
                    print(colorize("用法: /done <任务关键词>\n", C.YELLOW))
                    continue
                ok, msg = mark_today_task_done(arg, curr_subj)
                print(colorize(f"\n[{msg}]\n", C.GREEN if ok else C.YELLOW))
                continue
            elif cmd == "/doctor":
                try:
                    import doctor
                    doctor.run_doctor()
                except Exception as e:
                    print(colorize(f"\n[!] 执行 doctor 异常: {e}\n", C.RED))
                continue
            elif cmd == "/clear":
                history = []
                print(colorize("\n[已清空当前会话上下文]\n", C.YELLOW))
                continue
            elif cmd == "/config":
                interactive_config()
                cfg = load_config()
                if agent_runner and hasattr(agent_runner, "config"): agent_runner.config.update(cfg)
                continue
            elif cmd == "/notify":
                broadcast_briefing(cfg)
                continue
            elif cmd == "/build":
                print(colorize("\n[正在重新编译移动端看板...]", C.CYAN))
                build_py = ROOT / "05-考研看板" / "build.py"
                if build_py.exists():
                    import subprocess
                    subprocess.run([sys.executable, str(build_py)], cwd=str(ROOT / "05-考研看板"))
                print()
                continue
            elif cmd in ("/view", "/live"):
                import webbrowser
                target_url = f"http://localhost:{live_port or 8088}/live"
                webbrowser.open(target_url)
                print(colorize(f"\n[已在默认浏览器中打开实时可视化伴侣: {target_url}]\n", C.GREEN))
                continue
            elif cmd in ("/today", "/tasks", "/task"):
                print_today_tasks_summary()
                continue
            elif cmd in ("/exit", "/quit", "exit", "quit"):
                if agent_runner and hasattr(agent_runner, "hooks"):
                    agent_runner.hooks.trigger_session_end({"active_subject": curr_subj})
                print("\n再见！保持节奏，一战成硕！🎓")
                break
            # 别名取 `/tui` 而非 `/nav`：与 CLI 侧 `ky menu` 的别名集对齐
            # （misc.py:289 → ("menu", "--menu", "tui", "--tui")），
            # 避免同一功能在两端长出第三套名字。
            elif cmd in ("/menu", "/tui"):
                # [B-01 修复] 操作手册 566 行宣称 `/menu` 可「退出交互 REPL 并打开
                # TUI 终端全景导航面板」，AGENTS.md 113 行路由表同样列了 `/menu`，
                # 但 REPL 内从未注册该分支。这里与 `ky menu`（commands/misc.py:204）
                # 复用同一入口：先按 /exit 的口径触发会话收尾钩子，再惰性导入
                # tui_navigator（会拉入 Textual 等重依赖，不宜进模块顶层），
                # 启动后 break 退出 REPL，与文档「退出并打开」的语义一致。
                if agent_runner and hasattr(agent_runner, "hooks"):
                    agent_runner.hooks.trigger_session_end({"active_subject": curr_subj})
                try:
                    from tools import tui_navigator
                except ImportError:
                    import tui_navigator
                tui_navigator.run_tui_loop()
                break
            elif cmd in ("/plan", "/profile", "/blueprint"):
                try:
                    import study_planner
                    study_planner.run_study_plan_wizard(interactive=True)
                    cfg = load_config()
                except Exception as e:
                    print(f"方案设计提示: {e}")
                continue
            elif cmd in ("/subject", "/syllabus"):
                manage_syllabi_cli(cfg)
                continue
            elif cmd in ("/exam", "/compose"):
                sub_target = curr_subj
                exam_count = 3
                if arg:
                    for part in arg.split():
                        if part.startswith("--count="):
                            try: exam_count = int(part.split("=")[1])
                            except (ValueError, TypeError): pass
                        elif part.isdigit(): exam_count = int(part)
                        else:
                            for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                                if sk in part.lower() or sv in part: sub_target = sk; break
                if exam_composer:
                    print(colorize(f"\n[📝 正在组卷 ({exam_count}题)...]\n", C.CYAN))
                    res = exam_composer.compose_exam_paper(sub_target, count=exam_count, save_file=True)
                    print(res.get("formatted_paper", ""))
                    if res.get("saved_path"):
                        print(colorize(f"[√ 试卷已归档至]: {res['saved_path']}\n", C.GREEN))
                continue
            elif cmd in ("/scout", "/yuanxiao", "/school"):
                parts = arg.strip().split()
                target_school = parts[0] if parts else ""
                target_major = parts[1] if len(parts) > 1 else ""
                if not target_school:
                    target_school = cfg.get("study_plan", {}).get("school", "")
                    target_major = cfg.get("study_plan", {}).get("major", "")
                if not target_school or target_school == "目标院校":
                    print(colorize("用法: /scout <高校名> [专业名]\n", C.YELLOW))
                    continue
                if school_scout:
                    print(colorize(f"\n[🎯 正在对【{target_school}】侦察...]\n", C.CYAN))
                    res = school_scout.scout_school(school=target_school, major=target_major, include_social=True, save_report=True, apply_to_config=False, use_llm=True)
                    print(res.get("formatted_report", ""))
                continue
            elif cmd in ("/admission", "/admit", "/zs"):
                parts = arg.strip().split()
                target_school = parts[0] if parts else ""
                target_major = parts[1] if len(parts) > 1 else ""
                if not target_school:
                    target_school = cfg.get("study_plan", {}).get("school", "")
                    target_major = cfg.get("study_plan", {}).get("major", "")
                if not target_school or target_school == "目标院校":
                    print(colorize("用法: /admission <高校名> [专业]\n", C.YELLOW))
                    continue
                if intelligence:
                    engine = intelligence.get_intelligence_engine()
                    res = engine.query(school_query=target_school, major_query=target_major, save_report=True)
                    print(res.get("markdown_report", ""))
                continue
            elif cmd in ("/watch", "/jk"):
                parts = arg.strip().split()
                if intelligence:
                    watcher = intelligence.AdmissionWatcher()
                    if not parts or parts[0] in ("list", "-l"):
                        watched = watcher.list_watched()
                        print(colorize(f"\n[📡 监控高校 ({len(watched)} 所)]:", C.CYAN))
                        for w in watched: print(f"  • {w['name']} ｜ 最近检查: {w.get('last_check', '未检查')}")
                        print()
                    elif parts[0] in ("check", "-c"):
                        findings = watcher.check_updates()
                        for f in findings: print(f"  - {f['school']}: {f.get('status')}")
                continue
            elif cmd in ("/compare", "/vs", "/pk", "/duibi"):
                parts = arg.strip().split()
                if len(parts) < 2:
                    print(colorize("用法: /compare <高校1> <高校2> [专业关键词]\n", C.YELLOW))
                    continue
                s1, s2 = parts[0], parts[1]
                major_kw = parts[2] if len(parts) > 2 else resolve_major_keyword(cfg)
                if intelligence:
                    comparator = intelligence.SchoolComparator()
                    res = comparator.compare(school1_query=s1, school2_query=s2, major_keyword=major_kw, save_report=True)
                    print(res.get("terminal_report", ""))
                continue
            elif cmd in ("/variant", "/bianshi"):
                topic = arg.strip() or ("导数中值定理" if curr_subj == "math" else "核心高频考点")
                if variant_retriever:
                    res = variant_retriever.search_real_variant(subject=curr_subj, keyword=topic)
                    print(variant_retriever.format_variant_output(res))
                continue
            elif cmd in ("/map", "/tupu"):
                sub_target = curr_subj
                if arg:
                    for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                        if sk in arg.lower() or sv in arg: sub_target = sk; break
                if knowledge_map:
                    print(knowledge_map.format_knowledge_map_table(sub_target))
                continue
            elif cmd in ("/diagnose", "/zhenduan"):
                if not arg:
                    print(colorize("用法: /diagnose <答题卡文本或文件路径>\n", C.YELLOW))
                    continue
                if exam_diagnoser:
                    content = Path(arg).read_text(encoding="utf-8", errors="ignore") if Path(arg).exists() else arg
                    res = exam_diagnoser.diagnose_mock_exam(subject=curr_subj, exam_input=content)
                    print(exam_diagnoser.format_diagnosis_report(res))
                continue
            elif cmd in ("/diff", "/kaogang", "/dagang"):
                try:
                    from tools.cli.commands.intel import run_syllabus_diff
                except ImportError:
                    from cli.commands.intel import run_syllabus_diff
                run_syllabus_diff(arg)
                continue
            elif cmd in ("/ingest", "/slice", "/qiepian"):
                try:
                    from tools.cli.commands.material import run_material_ingest
                except ImportError:
                    from cli.commands.material import run_material_ingest
                run_material_ingest(arg)
                continue
            elif cmd == "/fatigue":
                try:
                    import study_planner
                    info = study_planner.check_fatigue_alert()
                    print(info.get("message"))
                except Exception as e:
                    print(f"检查疲劳异常: {e}")
                continue
            elif cmd in ("/relieve", "/jianfu"):
                try:
                    import study_planner
                    res = study_planner.apply_relief_mode()
                    print(colorize(f"\n[√ {res.get('message')}]\n", C.GREEN))
                except Exception as e:
                    print(f"启动减负异常: {e}")
                continue
            elif cmd in ("/clawbot",):
                run_wechat_clawbot_install()
                continue
            elif cmd in ("/gui", "/ky-gui"):
                try:
                    import ky_gui
                    ky_gui.main()
                except ImportError:
                    print(colorize("[!] 启动 GUI 失败，请安装 PySide6", C.RED))
                continue
            elif cmd in ("/wechat", "/wx"):
                try:
                    from tools.cli.commands.daily import cmd_wechat_search
                except ImportError:
                    from cli.commands.daily import cmd_wechat_search
                cmd_wechat_search(arg.split() if arg else [])
                continue
            elif cmd in ("/bridge", "/bot", "/webhook"):
                show_bridge_guide()
                continue
            elif cmd in ("/rollback", "/restore"):
                try:
                    from tools.agent import PermissionManager
                    pm = PermissionManager(workspace_root=ROOT)
                    res = pm.restore_last_checkpoint()
                    print(colorize(f"\n[√ {res.get('message')}]\n" if res.get("success") else f"\n[!] 失败: {res.get('message')}\n", C.GREEN if res.get("success") else C.YELLOW))
                except Exception as e:
                    print(f"回滚失败: {e}")
                continue
            elif cmd.startswith("/memory"):
                parts = user_input.strip().split()
                sub = parts[1].lower() if len(parts) > 1 else "status"
                try:
                    from tools.agent import MemoryManager
                    mem_mgr = MemoryManager(workspace_root=ROOT)
                    if sub in ("status", "health"):
                        health = mem_mgr.get_memory_health()
                        print(f"总 Tokens: {health['total_tokens']} | 字符数: {health['total_chars']}")
                    elif sub in ("prune", "trim"):
                        res = mem_mgr.prune_memory(scope="session", max_items=50)
                        print(colorize(f"\n[√ 记忆修剪完成]\n", C.GREEN))
                except Exception as e:
                    print(f"记忆管理失败: {e}")
                continue
            elif cmd == "/status":
                print_status_summary()
                continue
            else:
                print(colorize(f"未知指令 {cmd}，输入 /skills 查看可用技能，或输入 /math /eng /pol /pro", C.RED))
                continue

        # ── FSRS 错题盲盒作答判定 ──
        if active_quiz_item and not user_input.startswith("/"):
            if user_input.lower() in ("cancel", "/cancel", "退出", "放弃"):
                active_quiz_item = None
                print(colorize("\n[已退出当前错题盲盒复测]\n", C.YELLOW))
                continue

            print(colorize(f"\n[🎯 正在对您的盲盒复测作答进行智能采分...]\n", C.CYAN))
            quiz_eval_prompt = (
                f"你是一位考研全真阅卷专家。学员正在对以下历史错题进行【FSRS 盲盒复测】：\n\n"
                f"【原题设问与题干】：\n{active_quiz_item['question']}\n\n"
                f"【学员作答】：\n{user_input}\n\n"
                "请按真题采分点严格判定并指出是否通过。"
            )
            messages = [{"role": "system", "content": "你是一位考研真题阅卷主考官。"}, {"role": "user", "content": quiz_eval_prompt}]
            reply = stream_chat(messages, cfg)
            if reply:
                if "【复测通过·已掌握】" in reply or "复测通过" in reply:
                    ok, msg = error_logger.mark_error_status(active_quiz_item["subject"], active_quiz_item["file_name"], active_quiz_item["title"], new_status="已掌握")
                    print(colorize(f"\n🎉 [FSRS 系统判定]: {msg}，已从复测队列出库！\n", C.GREEN))
                else:
                    print(colorize("\n⚠️ [FSRS 系统判定]: 复测仍有失误，保持在待测队列！\n", C.YELLOW))
                history.append({"role": "user", "content": f"[错题复测作答: {active_quiz_item['title']}] {user_input}"})
                history.append({"role": "assistant", "content": reply})
                active_quiz_item = None
                print_followup_toolbar()
            continue

        # ── LLM 交互 ──
        append_live_message("user", user_input)
        print(colorize(f"\n[{SUBJECT_DIRS[curr_subj][1]} 正在思考并规划解答...]\n", C.DIM))

        _err_count_before = _count_error_records(curr_subj)
        reply = ""
        if agent_runner and cfg.get("api_key"):
            try:
                reply = agent_runner.run(user_input, interactive=True)
            except Exception as _agent_err:
                if _agent_err.__class__.__name__ == "PermissionDeniedError":
                    print(colorize(f"\n[✘ 已拒绝] {_agent_err}", C.RED))
                    continue
                raise
        else:
            sys_prompt = build_system_prompt(curr_subj)
            messages = [{"role": "system", "content": sys_prompt}]
            for h in history[-6:]: messages.append(h)
            messages.append({"role": "user", "content": user_input})
            reply = stream_chat(messages, cfg)
            if reply:
                append_live_message("assistant", reply)
                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": reply})

            if latex_beautifier and any(sym in reply for sym in ("\\(", "\\[", "\\int", "\\frac", "\\lim", "\\sum", "$$")):
                beautified = latex_beautifier.prettify_latex_for_terminal(reply)
                print(colorize("\n" + "─" * 58, C.DIM))
                print(colorize(" 📐 【终端数学公式与推导步骤美化视图】", C.BOLD))
                print(colorize("─" * 58, C.DIM))
                print(beautified)
                print(colorize("─" * 58, C.DIM))
                print(colorize(" 💡 提示: 输入 /view 可在浏览器中对照查看印刷级 KaTeX 排版！\n", C.YELLOW))
            else:
                print()
            print_followup_toolbar()

        _warn_if_mistake_not_archived(user_input, reply or "", curr_subj, _err_count_before)
