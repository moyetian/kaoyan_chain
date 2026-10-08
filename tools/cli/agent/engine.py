# -*- coding: utf-8 -*-
"""
Agent 核心交互引擎 (engine.py)
系统提示词组装、流式打字输出、网络重试与统一问答
"""

import json
import os
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # [B1 同类] LLM 请求经安全通道发送（双导入路径兼容）
    from net_guard import safe_urlopen
except ImportError:  # pragma: no cover
    from tools.net_guard import safe_urlopen  # type: ignore

try:  # [K4] 统一 LLM 出口（双导入路径兼容）
    from llm_client import (
        DEFAULT_LLM_TIMEOUT,
        DEFAULT_MAX_TOKENS,
        DIALOGUE_TEMPERATURE,
        ChatRequest,
        LLMEmptyStreamError,
        LLMError,
        chat_completion,
        normalize_openai_url,
        request_chat,
    )
    import output_budget
except ImportError:  # pragma: no cover
    from tools.llm_client import (  # type: ignore
        DEFAULT_LLM_TIMEOUT,
        DEFAULT_MAX_TOKENS,
        DIALOGUE_TEMPERATURE,
        ChatRequest,
        LLMEmptyStreamError,
        LLMError,
        chat_completion,
        normalize_openai_url,
        request_chat,
    )
    from tools import output_budget  # type: ignore

try:
    from tools.cli.shared import ROOT, SUBJECT_DIRS, load_config, read_text_safe
except ImportError:
    from cli.shared import ROOT, SUBJECT_DIRS, load_config, read_text_safe

try:
    from tools.cli.repl.renderer import C, colorize
except ImportError:
    from cli.repl.renderer import C, colorize

try:  # [P2 修复·2026-10-08 spinner 清除宽度] 显示宽度取 TUI/看板同源实现（CJK 双宽感知）
    from tools.tui.terminal import display_width as _display_width
except ImportError:  # pragma: no cover
    from tui.terminal import display_width as _display_width  # type: ignore

try:
    from skills import error_logger, math_verifier
except ImportError:
    try:
        from tools.skills import error_logger, math_verifier
    except ImportError:
        error_logger = None
        math_verifier = None

# [缺陷修复·兜底告警静默失效] 原 marker 仅有连续字符串「扣分」，而真实批改输出
# 多为「扣 **1分**」「[-1分]」「扣 2 分」这类带空格/加粗/括号的写法，导致
# _warn_if_mistake_not_archived 长期不触发 —— 当模型未调用 log_mistake 归档时，
# 用户既没落库、也没被告知，错题闭环断裂且无感知（实测 mimo-v2.5 判分即如此）。
# 现补充常见失分措辞，并新增正则覆盖「扣 N 分」「[-N分]」与警示符号。
_MISTAKE_FAIL_MARKERS = (
    "扣分", "未通过", "失分", "不规范", "错误", "待复核", "不达标", "需重新加固",
    "不够完整", "不完整", "遗漏", "漏掉", "缺少", "有误", "瑕疵", "不完全正确",
)
_MISTAKE_FAIL_PATTERNS = (
    re.compile(r"扣\s*\d*\s*分"),                     # 扣1分 / 扣 1 分 / 扣分
    re.compile(r"[\[【]\s*[-−]\s*\d+\s*分\s*[\]】]"),   # [-1分] / 【-1分】
    # [多角色实测·误报修复] 裸警示符号会命中教学语「⚠️ 三个高频陷阱」并触发
    # 「批改未归档」误报。收紧为「符号 + 同行 12 字符内失分动词」共现；
    # 纯符号（教学注意点/陷阱提示）不再单独算作失分。
    re.compile(
        r"[⚠❌✗×][^\n]{0,12}(?:扣|失分|错误|遗漏|漏掉|缺少|不完整|不规范|有误|瑕疵|待复核|不达标)"
    ),
)
_MISTAKE_GRADE_MARKERS = ("采分点", "错因", "得分")
# [多角色实测·误报修复] 出题预告语（「我按阅卷采分点逐项赋分并做错因五分类归档」）
# 与教学表头（「关键词 / 得分点」）会命中 GRADE_MARKERS，但那是「预告批改」而非
# 「已批改」。真实批改回复必有实际得分陈述（「得分：6/10」「总分 8 分」「6/10 分」），
# 据此区分——无「交作业」的答疑/出题场景不再误报。
_MISTAKE_SCORE_STATEMENT = re.compile(
    r"(?:得分|评分|总分|得分率)\s*[:：]?\s*\d+|\d+\s*/\s*\d+\s*分"
)

def _count_error_records(subject: str) -> int:
    """当前科目错题库中的记录条数"""
    try:
        return len(error_logger.scan_error_records(subject)) if error_logger else -1
    except Exception:
        return -1

def _warn_if_mistake_not_archived(user_input: str, reply: str, subject: str, before_count: int) -> None:
    """批改失分却未落库时给出确定性告警"""
    if before_count < 0 or error_logger is None or not reply:
        return
    looks_grading = ("交作业" in (user_input or "")) or (
        any(k in reply for k in _MISTAKE_GRADE_MARKERS)
        and bool(_MISTAKE_SCORE_STATEMENT.search(reply))
    )
    has_failure = any(k in reply for k in _MISTAKE_FAIL_MARKERS) or any(
        p.search(reply) for p in _MISTAKE_FAIL_PATTERNS
    )
    if not (looks_grading and has_failure):
        return
    after = _count_error_records(subject)
    if after > before_count:
        return
    print(colorize(
        "\n[!] 提醒：本轮批改显示存在失分/未通过项，但错题**未能写入错题本**，"
        "FSRS 复测队列未发生变更。\n"
        "    原因通常是批改由对话模型完成、未调用归档工具（非交互或权限受限时尤甚）。\n"
        "    补救路径：① 组卷走确定性判分链路（会强制归档）："
        f"`ky exam {subject} --count 3 --save` 然后 `ky exam-submit <试卷> <作答>`；\n"
        "              ② 或在交互终端重跑「交作业」（ask 模式可弹审批），"
        "信任本机工作区时可用 `ky --permission=auto`；\n"
        # [UT4 修复·CLI-3] 补第三条不依赖工具链的手动兜底：UT4 实测 ask 非交互
        # 管道下写操作全被拦截、推荐路径（exam→exam-submit）又因切片缺陷不可用，
        # 补救链断裂且旧文案未提示 --permission=auto。三条路至少一条可达。
        "              ③ 或手动将错题录入本科「错题本/」目录，并在「学情档案.md」"
        "补记错因与处方。\n", C.YELLOW))

def infer_subject_from_text(text: str, fallback: str = "math") -> str:
    """[W11] 从用户输入推断科目代码（无明确关键词时回落 fallback）。

    多角色实测：工科用户问 408 内容但 REPL 默认激活英语时，「批改未归档」
    告警建议 `ky exam eng` 科目错位。本函数复用 query_llm_reply 的既有推断
    逻辑（此前仅网关路径使用），供告警等场景选择正确科目。
    """
    t = str(text or "")
    if "英语" in t or "/eng" in t:
        return "eng"
    if "政治" in t or "/pol" in t:
        return "pol"
    if "专业课" in t or "/pro" in t:
        return "pro"
    if "数学" in t or "/math" in t:
        return "math"
    return fallback

def format_subject_hint(inferred: str, active: str) -> str:
    """[W11] 提问科目 ≠ 激活科目时的轻提示文案；相同时返回空串。

    只做提示不做切换：`_switch_subject` 会清空 history 与当前测验项
    （active_quiz_item），自动切换丢上下文。由 loop.py 在每轮回答后打印。
    """
    if not inferred or not active or inferred == active:
        return ""
    active_name = SUBJECT_DIRS.get(active, (active, active))[1]
    inferred_name = SUBJECT_DIRS.get(inferred, (inferred, inferred))[1]
    return (f"💡 提示：本问题看起来属于【{inferred_name}】内容，"
            f"当前激活【{active_name}】——如需切换请输 /{inferred}。")

def build_system_prompt(active_subj: str = "math") -> str:
    """组装当前激活学科的私教系统提示词与外置记忆上下文"""
    sys_parts = []

    # 1. 顶层总控协议
    try:
        try:
            from protocol_loader import DEFAULT_PROTOCOL, load_protocol
        except ImportError:
            from tools.protocol_loader import DEFAULT_PROTOCOL, load_protocol
        sys_parts.append("=== 【顶层协议 AGENTS.md】 ===\n" + load_protocol(DEFAULT_PROTOCOL))
    except Exception as e:
        print(colorize(f"[!] 顶层协议加载异常：{e}", C.YELLOW))

    # 2. 当前学科协议与状态
    subj_folder, subj_name = SUBJECT_DIRS.get(active_subj, ("01-数学", "数学专属私教"))
    s_dir = ROOT / subj_folder

    agents_subj = s_dir / "AGENTS.md"
    if agents_subj.exists():
        sys_parts.append(f"\n=== 【当前学科协议：{subj_name}】 ===\n" + read_text_safe(agents_subj))

    state_files = [
        ("今日任务", s_dir / "_状态" / "今日任务.md", s_dir / "_状态" / "今日任务.template.md"),
        ("学员档案", s_dir / "_状态" / "学员档案.md", s_dir / "_状态" / "学员档案.template.md"),
        ("薄弱点雷达", s_dir / "_状态" / "薄弱点雷达.md", s_dir / "_状态" / "薄弱点雷达.template.md"),
        ("专业课学情", s_dir / "学情档案.md", s_dir / "学情档案.template.md"),
        ("考试大纲", s_dir / "考试大纲.md", None),
    ]

    state_context = []
    for label, real_p, tmpl_p in state_files:
        p = real_p if real_p.exists() else (tmpl_p if (tmpl_p and tmpl_p.exists()) else None)
        if p and p.exists():
            txt = read_text_safe(p)
            if txt.strip():
                state_context.append(f"--- [{label}] ({p.name}) ---\n{txt}")

    if state_context:
        sys_parts.append(f"\n=== 【当前学员学情档案与记忆状态 ({subj_name})】 ===\n" + "\n\n".join(state_context))

    # 3. 动态核验参考资料真实性
    mat_dir = s_dir / "参考资料"
    mat_files = []
    if mat_dir.exists():
        for f in mat_dir.iterdir():
            if f.is_file() and f.name.lower() not in ("readme.md", ".gitkeep", ".gitignore"):
                mat_files.append(f.name)

    if mat_files:
        mat_text = (
            f"\n=== 📚【本地真题与资料白名单清单 ({subj_name})】===\n"
            f"本地「参考资料/」目录下实际存放的文件为：{', '.join(mat_files)}。\n"
            "若需抽题或引用，必须严格以以上文件为准，严禁引用上述列表之外的任何书籍！\n"
            "【四大不可违背的真实性铁律】：\n"
            "1. 严禁凭空捏造题目出处！绝对严禁声称“以下题目均来自《李林880》”、“来自《张宇1000》”、“来自《汤家凤1800》”等未核验虚假书名！\n"
            "2. 当学员自主输入题目时：私教只针对学员给出的题目本身进行采分点批改与思路拆解；\n"
            "3. 若在解答后提供类似题供学员巩固，必须如实标明为【私教自拟类似变式训练】，绝对禁止伪称来自某本未核验的出版物！\n"
            "4. 若学员要求从某题册（如李林880）抽题，但本地无该文件且学员未提供题号，必须如实告知：“您本地参考资料库尚未放置该文件，请提供具体题目文字或截图，私教立刻为您解答。”"
        )
    else:
        mat_text = (
            f"\n=== 🚨【最高红线：本地未放入参考资料 · 绝对禁止虚构题源出处】===\n"
            f"系统物理核验结果：当前学科【{subj_name}】的「参考资料/」目录下【尚未放置任何教材或题库文件】！\n"
            "【四大不可违背的真实性铁律】：\n"
            "1. 严禁凭空捏造题目出处！绝对严禁声称“以下题目均来自《李林880》”、“来自《张宇1000》”、“来自《汤家凤1800》”等虚假书名！\n"
            "2. 当学员自主输入题目时：私教只针对学员给出的题目本身进行采分点批改与思路拆解；\n"
            "3. 若在解答后提供类似题供学员巩固，必须如实标明为【私教自拟类似变式训练】，绝对禁止伪称来自某本未核验的出版物！\n"
            "4. 若学员要求从某题册（如李林880）抽题，但本地无该文件且学员未提供题号，必须如实告知：“您本地参考资料库尚未放置该文件，请提供具体题目文字或截图，私教立刻为您解答。”"
        )
    sys_parts.append(mat_text)

    sys_parts.append(
        "\n=== 【CLI 指令与行为规则】 ===\n"
        "1. 严格遵守当前配置的私教辅导风格（严格/秒杀/鼓励/溯源）；\n"
        "2. 所有派题必须来自题源白名单或学员指定题号，坚决杜绝随性自编偏题超纲题；\n"
        "3. 学员交作业时，必须输出明晰的【采分点步骤分】与【错因五分类归因】；\n"
        "4. 输出排版尽量精简、结构清晰、便于终端与手机屏幕阅读。"
    )

    sys_parts.append(
        "\n=== 📋【学员“报到”口令核心响应规范 (必读必遵)】 ===\n"
        "当学员输入“报到”、“<科目>报到”（如“英语报到”“政治报到”“专业课报到”，"
        "不考数学的方案不出现“数学报到”）或会话首次启动时：\n"
        "【第一阶段：全景学情战况汇报与今日规划】\n"
        "1. 首先明确读取并向学员汇报学员的基本盘信息：目标院校、报考专业、考试科目、目标分数、初试倒计时、每日时间预算；\n"
        "2. 汇报今日该科目的复习攻坚路线图（根据今日任务与学员薄弱点，分段规划：如概念梳理 XX 分钟、真题实战 XX 分钟、订正归档 XX 分钟）；\n"
        "3. 明确通报当前本地已就绪的白名单实体参考资料（如真实存在的张宇1000题、历年真题等）；\n"
        "【第二阶段：主动派发今日实战第 1 题】\n"
        "4. 汇报完规划后，主动从本地真题或对应考点库中派发今日第 1 道针对性试题（展示清晰题干、分值、考查考点）；\n"
        "5. 提示学员在草稿纸上动笔演算，完成后直接在输入框提交作答或拍照上传（/img），由私教按考研采分点逐步赋分并归因错题！\n"
        "严禁一上来完全不汇报学员信息与整体规划就自说自话！"
    )

    sys_parts.append(
        "\n=== 📝【学员作答与“交作业”批改规范】 ===\n"
        "当学员提交了题目答案、推导草稿或输入“交作业”时：\n"
        "1. 严格按照考研阅卷人标准分步骤批改：在推导每个关键步骤明确标注采分点（如 [+2分]、[-1分]）；\n"
        "2. 若有失误，坚决指出错因五分类（概念漏洞/审题偏差/公式记错/计算失误/书写丢分），并给出针对性改进处方；\n"
        "3. 【强制归档·必须调用工具】批改完成后，只要存在未通过/失分题目，"
        "你必须**立即调用 log_mistake 工具**逐题写入错题本"
        "（subject 取当前科目代码，title 用题目关键词，mistake_type 取错因五分类之一，"
        "detail 写关键漏洞，question 写题干）。**不要只口头提醒学员去记录**——"
        "口头提醒不会写入任何文件，复测队列将永远为空。\n"
        "4. 【禁止虚假陈述】若 log_mistake 返回 PermissionDenied（非交互环境权限受限），"
        "你必须如实告知学员「错题**未能**归档，请改用 --permission=auto 重跑或手动记录」，"
        "严禁声称“已归档”“已排入复测队列”。\n"
        "5. 只有当工具确实返回成功信息时，才可以说该错题已纳入 FSRS 复测队列。"
    )

    return "\n\n".join(sys_parts)

def build_demo_syllabus_text(base_text: str, year_label: str) -> str:
    """构造演示样例的新增考纲文本"""
    lines = (base_text or "").splitlines()
    bullets = []
    for ln in lines:
        s = ln.strip()
        if not s.startswith("- ") or len(s) < 12:
            continue
        if s.startswith("- **温馨提示") or s.startswith("- **说明") or s.startswith("- 提示"):
            continue
        if re.search(r"每题|满分|分值|题型分布|试卷结构|考试形式|参考书|共\s*\*\*\d", s):
            continue
        bullets.append(s)
    if not bullets:
        return base_text

    def _core(b: str) -> str:
        c = b[2:].strip()
        c = re.sub(r"^\*\*[^*]{1,12}\*\*\s*[：:]\s*", "", c)
        c = re.sub(r"\s*[（(]\s*要求\s*[：:][^）)]*[）)]\s*$", "", c).strip()
        c = re.sub(r"[（(][^）)]*[）)]", "", c)
        c = re.split(r"[、，,；;]", c)[0].strip().rstrip("：:。.").strip()
        return c or b[2:].strip()

    clean = [b for b in bullets if "、" not in _core(b) and "（" not in _core(b)]
    pool = clean or bullets

    new_text = base_text
    for b in pool[:1]:
        new_text = new_text.replace(
            b, f"- **掌握**：{_core(b)}（{year_label}考查权重上调 · 演示样例）")
    picks = pool[1:2] or pool[:1]
    added = "\n".join(
        f"- **掌握**：{_core(p)}（{year_label}新增 · 演示样例）" for p in picks)
    new_text += f"\n\n### {year_label}新增考纲知识点（演示样例·非官方）\n{added}\n"
    return new_text

# [K4] ``normalize_openai_url`` 已收敛为 ``llm_client`` 单一实现，此处 re-export
# 保住既有导入路径（``ky_cli`` / ``cli.agent.__init__`` / ``study_planner`` 等）。

def _resolve_temperature(config: Optional[Dict[str, Any]]) -> float:
    """解析请求温度：配置项优先，缺失/非法一律回落到对话链路口径。

    [R3 波动收敛] 存在的意义是**显式化**：原先 ``config.get("temperature", 0.3)``
    把「对话链路用 0.3」这个口径藏在一个字面量里，稳定性优先的调用点无从
    区分自己该不该关采样。现统一走具名常量，需要收紧时改一处即可。
    """
    try:
        val = float((config or {}).get("temperature"))
    except (TypeError, ValueError):
        return DIALOGUE_TEMPERATURE
    return val if 0.0 <= val <= 2.0 else DIALOGUE_TEMPERATURE


def _resolve_max_tokens(config: Optional[Dict[str, Any]]) -> int:
    """解析输出上界：**按输出预算档位**取值（缺省 ``standard`` = 2048）。

    [R3 波动收敛·根因 1] 关键在于**不允许缺省成 None**：``max_tokens=None`` 时
    ``_build_payload`` 会整个省略该字段，输出长度由上游默认值决定——这正是
    R3 观测到的 8998 → 4927 字符收缩的机制。

    [2026-10-06 输出预算分档] 原实现固定回落 :data:`DEFAULT_MAX_TOKENS`（4096），
    对「查个概念」太贵（真机实测 60s+ 才拿到一大段），对「模拟考详解」又太紧。
    现改为按 :mod:`output_budget` 档位取值：**显式 ``config["max_tokens"]`` 仍优先**
    （向后兼容：已写死该值的既有配置行为不变），否则查档位表、缺省 standard。
    真机实测（各 3 次）：standard 档字符 CV 9.38% / 耗时 CV 13.45%，均 ≤15%。
    """
    return output_budget.max_tokens_for(config)


#: [P2 修复·2026-10-08 spinner 清除宽度] 流式等待状态文案。含 20 个 CJK 字符（双宽），
#: 连同前缀「两空格 + 1 帧 + 空格」整行实际 49 显示列；清除行按
#: ``_display_width`` 计算，不再写死 48（曾致行尾残留 1 字符）。
_SPINNER_LABEL = "[考研私教正在审阅题干关键采分点与推导步骤...]"
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def stream_chat(messages: List[Dict[str, Any]], config: Dict[str, Any]) -> str:
    """向 OpenAI 兼容 API 发起流式请求并打字机式打印

    [K4] 内联 SSE 解析已删除，改由 ``llm_client.request_chat(stream=True,
    on_chunk=...)`` 统一承担；本函数只保留 spinner 观感、
    ``_MAX_ATTEMPTS=2`` 空回复重试与「失败/无内容返回 ""」契约。
    """
    raw_base_url = config.get("base_url", "https://api.deepseek.com/v1")
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "deepseek-chat")

    if not api_key:
        print(colorize("\n[!] 错误: 未配置 API Key！请先运行 /config 设置您的模型密钥。\n", C.RED))
        return ""

    def spinner_task():
        if not sys.stdout.isatty():
            sys.stdout.write(f"  {C.CYAN}* [考研私教正在审阅题干与思考推导步骤...]{C.RESET}\n")
            sys.stdout.flush()
            return
        frames = _SPINNER_FRAMES
        label = _SPINNER_LABEL
        idx = 0
        while not stop_spinner.is_set():
            frame = frames[idx % len(frames)]
            sys.stdout.write(f"\r  {C.CYAN}{frame}{C.RESET} {C.DIM}{label}{C.RESET}")
            sys.stdout.flush()
            idx += 1
            time.sleep(0.08)
        # [P2 修复·2026-10-08 清除宽度写死] 此前写死 `" " * 48`，而该行实际 49 显示列
        # （CJK 文案双宽）→ 每轮结束在行尾残留 1 个字符。按真实宽度清除。
        sys.stdout.write("\r" + " " * _display_width(f"  {frames[0]} {label}") + "\r")
        sys.stdout.flush()

    req = ChatRequest(
        messages=messages,
        model=model,
        # [R3 波动收敛·根因 1] 本函数（CLI 非 Agent 路径）此前**既不传
        # max_tokens、也不显式传 temperature**：输出长度完全由上游默认决定，
        # 而 Agent 路径固定 4096 → 同一操作在两条路径上的输出长度行为**结构性
        # 不同**（R3 全矩阵实测 8998 → 4927 字符，−45%）。此处与 Agent 路径对齐：
        #   * max_tokens：取 llm_client.DEFAULT_MAX_TOKENS（与 loop.py 同一真源）；
        #   * temperature：讲题属对话链路，显式取 DIALOGUE_TEMPERATURE 而非
        #     依赖 config 缺省时的隐式 0.3——显式声明才能在需要收紧时一处改全。
        max_tokens=_resolve_max_tokens(config),
        temperature=_resolve_temperature(config),
        stream=True,
        timeout=DEFAULT_LLM_TIMEOUT,
        api_key=api_key,
        base_url=raw_base_url,
        headers_extra={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Kaoyan-Study-Chain/1.0",
            "Accept": "application/json, text/event-stream",
            "Accept-Encoding": "identity",
        },
    )

    _MAX_ATTEMPTS = 2
    _retry_hint = f"\n[!] 上游未返回任何内容（连接可能被中断），正在重试 (1/{_MAX_ATTEMPTS - 1})...\n"
    _empty_hint = "\n[!] 上游未返回任何内容（连接被中断或模型无输出）。本次未拿到结果，请稍后重试。\n"
    for _attempt in range(1, _MAX_ATTEMPTS + 1):
        stop_spinner = threading.Event()
        spinner_thread = threading.Thread(target=spinner_task, daemon=True)
        spinner_thread.start()
        first_token = True

        def _on_chunk(chunk: str) -> None:
            """首个内容块到达时停掉 spinner，并逐块打字机输出。"""
            nonlocal first_token
            if first_token:
                stop_spinner.set()
                spinner_thread.join(timeout=0.2)
                first_token = False
            sys.stdout.write(chunk)
            sys.stdout.flush()

        try:
            # [B1 同类·跳转泄漏 Bearer] 安全通道：SSRF 逐跳复核 + 跨域剥离鉴权头。
            data = request_chat(req, max_retries=0, on_chunk=_on_chunk,
                                sleep_fn=time.sleep, urlopen_fn=safe_urlopen)
            stop_spinner.set()
            print()
            choices = data.get("choices") or [{}]
            choice0 = choices[0] or {}
            message = choice0.get("message") or {}
            content = message.get("content") or ""
            if content:
                # [输出预算分档] 触达本档硬上界时**显式标注**（判据用上游上报的
                # finish_reason=="length"，不用「长度接近上界」的启发式——后者会在
                # 正常长回答上误报）。静默截断会让考生以为那就是完整答案。
                return content + output_budget.truncation_notice(
                    choice0.get("finish_reason"), config)
            if _attempt < _MAX_ATTEMPTS:
                print(colorize(_retry_hint, C.YELLOW))
                continue
            print(colorize(_empty_hint, C.YELLOW))
            return ""
        except LLMEmptyStreamError:
            # [K4] 空流在统一客户端里是确定性坏包（不伪造空回复）；此处按旧
            # 「上游未返回任何内容」语义处理 → 仍走 _MAX_ATTEMPTS 重试。
            stop_spinner.set()
            print()
            if _attempt < _MAX_ATTEMPTS:
                print(colorize(_retry_hint, C.YELLOW))
                continue
            print(colorize(_empty_hint, C.YELLOW))
            return ""
        except LLMError as e:
            stop_spinner.set()
            if e.status is not None:
                print(colorize(f"\n[API 错误 {e.status}]: {e.body or str(e)}\n", C.RED))
            else:
                print(colorize(f"\n[网络连接异常]: {e}\n", C.RED))
            return ""
        except Exception as e:
            stop_spinner.set()
            print(colorize(f"\n[网络连接异常]: {e}\n", C.RED))
            return ""
        finally:
            stop_spinner.set()
    return ""

def query_llm_reply(user_msg: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """网关统一调用私教大模型生成详细讲题回复"""
    latest_cfg = load_config()
    if cfg:
        latest_cfg.update({k: v for k, v in cfg.items() if v})
    cfg = latest_cfg

    active_subj = infer_subject_from_text(user_msg, cfg.get("active_subject", "math"))

    if user_msg.startswith("/calc") or "验算" in user_msg:
        try:
            mv = math_verifier
            if mv is None:
                try:
                    from skills import math_verifier as mv
                except ImportError:
                    from tools.skills import math_verifier as mv
            expr = user_msg.replace("/calc", "").replace("验算", "").strip()
            if expr and mv:
                return mv.run_math_query(expr)
        except Exception:
            pass

    sys_prompt = build_system_prompt(active_subj)
    messages = [{"role": "system", "content": sys_prompt}]

    try:
        from tools.cli.gateway import LIVE_SESSION_MESSAGES
    except ImportError:
        LIVE_SESSION_MESSAGES = []

    for m in LIVE_SESSION_MESSAGES[-6:]:
        c = m.get("content", "")
        if '<img' in c:
            c = re.sub(r'<img[^>]*>', '[学员手写草稿图片]', c)
        if c.strip():
            messages.append({"role": m.get("role", "user"), "content": c})

    if not messages or messages[-1].get("content") != user_msg:
        messages.append({"role": "user", "content": user_msg})

    api_key = cfg.get("api_key", "").strip()
    raw_base_url = cfg.get("base_url", "https://api.deepseek.com/v1")
    if not api_key or api_key == "YOUR_API_KEY_HERE" or "example.com" in raw_base_url:
        return f"🎓【考研私教】收到提问: \"{user_msg}\"\n⚠️ 尚未配置大模型 API Key，请在电脑端终端运行 `ky config` 设置密钥后即可畅享网页端与群聊对话讲题！"

    try:
        # [R3 波动收敛·根因 3] 超时口径收敛为单一真源
        # ``llm_client.DEFAULT_LLM_TIMEOUT``（90s），与 CLI 流式讲题
        # (``stream_chat``) 和 GUI 侧（AgentRunner ``_stream_timeout()``）
        # 同值。收敛前此处的 55s 与另两处的 120s / 90s 互不一致：同一操作
        # 在不同入口有不同的等待上限，网关/群聊比桌面端先放弃，而调用方
        # 无法区分「模型慢」与「超时口径不同」→ 多轮耗时差异无法归因。
        # ``KY_LLM_TIMEOUT`` 环境变量仍可临时覆盖（运维/排障用）；未设置或非法
        # 时一律回落到单一真源（不存在「先赋一个值再立刻覆盖」的中间态）。
        try:
            _inner_timeout = float(os.environ.get("KY_LLM_TIMEOUT",
                                                  str(DEFAULT_LLM_TIMEOUT)))
        except (TypeError, ValueError):
            _inner_timeout = DEFAULT_LLM_TIMEOUT
        # [K9] 网关/网页对话也必须经过统一客户端：SSRF、重定向鉴权剥离、
        # 响应体上限、结构化错误和退避不能只在 AgentRunner 路径生效。
        # [R3 根因 1] 显式补 max_tokens：与 stream_chat / Agent 路径对齐，
        # 避免同一操作因入口不同而输出长度差一截。
        answer = chat_completion(
            messages,
            config=cfg,
            workspace_root=ROOT,
            temperature=_resolve_temperature(cfg),
            timeout=_inner_timeout,
            max_tokens=_resolve_max_tokens(cfg),
            urlopen_fn=safe_urlopen,
        )
        if answer:
            return answer
        return (
            "🎓【考研私教解答异常】：模型服务未返回有效内容。\n"
            "建议：请在终端输入 `ky doctor` 检查 API 连通性与密钥配置。"
        )
    except Exception as e:
        return f"🎓【考研私教网络连接异常】: {e}"
