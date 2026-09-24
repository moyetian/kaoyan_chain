# -*- coding: utf-8 -*-
"""
核心学习与测评命令模块 (study.py)
包含 exam / exam-submit / review / diagnose / variant
"""

import sys
from pathlib import Path
from typing import List

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.shared import SUBJECT_DIRS, _looks_like_path, interpreter_hint, load_config
    from tools.cli.repl.renderer import C, colorize
except ImportError:
    from cli.dispatch import Command, register
    from cli.shared import SUBJECT_DIRS, _looks_like_path, interpreter_hint, load_config
    from cli.repl.renderer import C, colorize


def _cmd_exam(args: List[str]) -> int:
    target_subj = load_config().get("active_subject", "math")
    count = 3
    save_flag = False
    allow_placeholder = False
    unknown_flags = []
    _SUBJ_ALIAS = {
        "math": "math", "eng": "eng", "pol": "pol", "pro": "pro",
        "数": "math", "英": "eng", "政": "pol", "专": "pro",
    }
    argv = args[1:]
    for idx, a in enumerate(argv):
        if a.startswith("--count="):
            try:
                count = int(a.split("=")[1])
            except (ValueError, TypeError):
                pass
        elif a == "--count":
            if idx + 1 < len(argv):
                try:
                    count = int(argv[idx + 1])
                except (ValueError, TypeError):
                    pass
        elif a.startswith("--subject="):
            target_subj = _SUBJ_ALIAS.get(a.split("=", 1)[1].strip().lower(), target_subj)
        elif a == "--subject":
            nxt = argv[idx + 1].strip().lower() if idx + 1 < len(argv) else ""
            target_subj = _SUBJ_ALIAS.get(nxt, target_subj)
        elif a in ("--save", "-s"):
            save_flag = True
        elif a == "--allow-placeholder":
            # [P0 修复·白名单门禁] 占位题只在显式开关下产出，见 exam_composer.compose_exam_paper
            allow_placeholder = True
        else:
            matched = False
            for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                if sk in a.lower() or sv in a:
                    target_subj = sk
                    matched = True
                    break
            if not matched and a.startswith("-"):
                unknown_flags.append(a)
    if unknown_flags:
        print(colorize(
            "[!] 已忽略无法识别的参数: " + " ".join(unknown_flags) + "\n"
            "    ky exam 支持的参数: [math|eng|pol|pro] (或 数/英/政/专) · "
            "--subject=<科目> · --count=N · --save · --allow-placeholder", C.YELLOW))

    try:
        from tools.skills import exam_composer
    except ImportError:
        try:
            from skills import exam_composer
        except ImportError:
            exam_composer = None

    if not exam_composer:
        print("exam_composer 技能模块未载入")
        return 1

    res = exam_composer.compose_exam_paper(target_subj, count=count, save_file=save_flag,
                                           allow_placeholder=allow_placeholder)
    if res.get("success") is False:
        # 没有任何真实题源：不产出试卷，只给可执行的上手引导（退出码 2 = 未组卷，供脚本判定）
        print(colorize(res.get("formatted_paper", ""), C.YELLOW))
        return 2

    print(res.get("formatted_paper", ""))
    if res.get("saved_path"):
        print(colorize(f"\n[√ 试卷已成功保存至]: {res['saved_path']}\n", C.GREEN))
    else:
        # [缺陷修复·闭环断点] 不加 --save 时试卷只打印不落盘，学员随后拿一个不存在的
        # 路径去 exam-submit 会得到「未读取到本卷答案密钥」——看起来像判分坏了，
        # 实际是没有可提交的文件。此处把下一步说清楚。
        print(colorize(
            "\n[i] 本次试卷未落盘（仅打印）。要作答后判分，请加 --save 生成试卷文件，"
            "或把上方内容原样保存为 .md 后运行 ky exam-submit <试卷路径> <作答>。\n", C.DIM))
    return 0


def _cmd_exam_submit(args: List[str]) -> None:
    if len(args) < 3:
        print(colorize("用法: ky exam-submit <试卷文件路径> <作答文本或答案文件>\n示例: ky exam-submit paper_123.json '1. A 2. C 3. B'", C.YELLOW))
        sys.exit(1)
    paper_p = args[1]
    # [缺陷修复·误报「密钥不可读」] 第一个参数呈路径形态却不存在时，此前被当成
    # 「试卷内联文本」继续判分：文本里没有 EXAM_PAPER_ID → 三条密钥通道全部落空 →
    # 报「无法判分：未读取到本卷答案密钥」。学员会误以为密钥库坏了，实际只是路径写错
    # 或 ky exam 没加 --save。现直接点名文件不存在并给出下一步。
    if _looks_like_path(paper_p) and not Path(paper_p).is_file():
        print(colorize(f"[!] 找不到试卷文件: {paper_p}", C.RED))
        print(colorize(
            "    提示：ky exam 默认只打印不落盘，请先运行 `ky exam <科目> --count=N --save` 生成试卷文件，\n"
            "    试卷会保存到对应科目的「错题本/」目录（文件名形如 自测卷_<日期>_EXAM-...md）。", C.YELLOW))
        sys.exit(1)
    answers = " ".join(args[2:])
    try:
        is_file = len(answers) < 255 and "\n" not in answers and Path(answers).exists()
    except OSError:
        is_file = False

    if is_file:
        answers = Path(answers).read_text(encoding="utf-8", errors="ignore")

    try:
        from tools.skills import exam_composer
    except ImportError:
        try:
            from skills import exam_composer
        except ImportError:
            exam_composer = None

    if exam_composer:
        res = exam_composer.grade_exam_paper(paper_p, answers)
        if res.get("report"):
            print(res["report"])
        elif res.get("success"):
            print(colorize(f"\n=== 🎯 自测整卷批改得分: {res.get('score')} / {res.get('total_score')} (正答率 {res.get('accuracy')}%) ===\n", C.BOLD))
        else:
            print(colorize(f"[!] 批改失败: {res.get('message', '未识别到有效作答')}", C.RED))
    else:
        print("exam_composer 技能模块未载入")


def _cmd_review(args: List[str]) -> None:
    target_subj = load_config().get("active_subject", "math")
    if len(args) > 1:
        raw_s = args[1].lower()
        for s_k, s_v in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
            if s_k in raw_s or s_v in raw_s:
                target_subj = s_k
                break

    try:
        from tools.skills import error_logger
    except ImportError:
        try:
            from skills import error_logger
        except ImportError:
            error_logger = None

    due_items = error_logger.get_due_reviews(target_subj, max_count=5) if error_logger else []
    if not due_items:
        print(colorize(f"\n[🎉 恭喜] {SUBJECT_DIRS.get(target_subj, ('', target_subj))[1]} 当前没有到期需要 FSRS 复测的错题！\n", C.GREEN))
    else:
        print(colorize(f"\n=== 📚 {SUBJECT_DIRS.get(target_subj, ('', target_subj))[1]} FSRS 待复测错题 ({len(due_items)} 道) ===", C.BOLD))
        for i, it in enumerate(due_items, 1):
            print(f"  {i}. [{it.get('date', '')}] {it.get('title', '')} (错因: {it.get('error_type', '未分类')})")
        print(f"\n💡 提示：在终端运行 {interpreter_hint()} tools/ky_cli.py 启动交互式私教后，输入 /review 即可进入盲盒重测！\n")


def _cmd_diagnose(args: List[str]) -> None:
    if len(args) < 2:
        print(colorize("用法: ky diagnose <模考答题卡文本或文件路径> [--subject=math/eng/pol/pro]\n示例: ky diagnose 模考记录.txt 或 ky diagnose '1-5: A B C D A'", C.YELLOW))
        sys.exit(1)
    d_subj = None
    d_words = []
    d_explicit_text = None
    d_explicit_file = None
    for a in args[1:]:
        if a.startswith("--subject="):
            d_subj = a.split("=", 1)[1].strip().lower()
        elif a.startswith("--text="):
            d_explicit_text = a.split("=", 1)[1]
        elif a.startswith("--file="):
            d_explicit_file = a.split("=", 1)[1].strip()
        else:
            d_words.append(a)
    raw_target = " ".join(d_words)
    content = raw_target
    is_file = False
    target_file = None

    if d_explicit_file:
        p = Path(d_explicit_file)
        if not p.is_file():
            print(colorize(f"[!] 找不到答题卡文件: {d_explicit_file}", C.RED))
            sys.exit(1)
        is_file, target_file = True, p
    elif d_explicit_text is not None:
        content = d_explicit_text
    elif "\n" not in raw_target and len(raw_target) < 260:
        try:
            p = Path(raw_target)
            if p.is_file():
                is_file = True
                target_file = p
        except (OSError, ValueError):
            pass

    if is_file and target_file:
        content = target_file.read_text(encoding="utf-8", errors="ignore")
    elif d_explicit_file is None and d_explicit_text is None and _looks_like_path(raw_target):
        print(colorize(f"[!] 找不到答题卡文件: {raw_target}", C.RED))
        sys.exit(1)
    if not str(content).strip():
        print(colorize("[!] 答题卡内容不能为空", C.RED))
        sys.exit(1)

    if not d_subj and is_file and target_file:
        p_str = str(target_file)
        for s_k, (s_folder, _) in SUBJECT_DIRS.items():
            if s_folder in p_str:
                d_subj = s_k
                break

    cfg = load_config()
    active_subj = cfg.get("active_subject", "math")
    d_subj = d_subj if d_subj in SUBJECT_DIRS else active_subj
    if d_subj != active_subj:
        print(colorize(f"[i] 已按试卷归属科目诊断: {SUBJECT_DIRS[d_subj][1]} (会话科目为 {SUBJECT_DIRS[active_subj][1]})", C.CYAN))

    try:
        from tools.skills import exam_diagnoser
    except ImportError:
        try:
            from skills import exam_diagnoser
        except ImportError:
            exam_diagnoser = None

    if exam_diagnoser:
        res = exam_diagnoser.diagnose_mock_exam(subject=d_subj, exam_input=content)
        print(exam_diagnoser.format_diagnosis_report(res))
    else:
        print("exam_diagnoser 技能模块未载入")


def _cmd_variant(args: List[str]) -> None:
    if len(args) < 2:
        print(colorize("用法: ky variant <考点关键词或原题干> [--subject=math/eng/pol/pro]\n示例: ky variant 傅里叶变换 --subject=pro", C.YELLOW))
        sys.exit(1)
    v_subj = None
    v_words = []
    for a in args[1:]:
        if a.startswith("--subject="):
            v_subj = a.split("=", 1)[1].strip().lower()
        else:
            v_words.append(a)
    topic = " ".join(v_words)
    cfg = load_config()
    active_subj = cfg.get("active_subject", "math")
    v_subj = v_subj if v_subj in SUBJECT_DIRS else active_subj
    if v_subj != active_subj:
        print(colorize(f"[i] 变式检索科目: {SUBJECT_DIRS[v_subj][1]} (会话科目为 {SUBJECT_DIRS[active_subj][1]}，可用 --subject 调整)", C.CYAN))

    try:
        from tools.skills import variant_retriever
    except ImportError:
        try:
            from skills import variant_retriever
        except ImportError:
            variant_retriever = None

    if variant_retriever:
        res = variant_retriever.search_real_variant(subject=v_subj, keyword=topic)
        print(variant_retriever.format_variant_output(res))
    else:
        print("variant_retriever 技能模块未载入")


# 注册学习测评命令
register(Command('exam', ("exam", "--exam", "compose", "--compose"), '[科目] [--count=N] [--save]', '基于错题库与核心考点反向靶向组卷', handler=_cmd_exam, write=True))
register(Command('exam-submit', ("exam-submit", "--exam-submit", "grade-paper", "--grade-paper"), '<试卷路径> <作答文本>', '自动判卷并输出正答率、采分点与错题归因', handler=_cmd_exam_submit, write=True))
register(Command('review', ("review", "--review", "quiz", "--quiz"), '[math|eng|pol|pro]', '查看 FSRS 待复测错题列表', handler=_cmd_review))
register(Command('diagnose', ("diagnose", "--diagnose"), '<答题卡文本或文件>', '整卷级多题诊断引擎 (章节失分排行与薄弱处方)', handler=_cmd_diagnose))
register(Command('variant', ("variant", "--variant"), '<考点关键词>', '四科白名单同类真题变式检索与防幻觉溯源', handler=_cmd_variant, write=True))
