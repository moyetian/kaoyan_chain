# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 生命周期拦截钩子系统 (Lifecycle Hooks System)
核心哲学:
- Skill 是“让模型知道怎么做” (Prompt/指导层)
- Hook 是“系统强制必须做某件事” (Runtime/拦截层)

标准事件:
1. SessionStart:   会话开启时 (初始化状态、三级记忆同步)
2. PreToolUse:     工具执行前 (沙箱硬阻断、数二/英二考纲超纲硬拦截)
3. PostToolUse:    工具执行后 (语法自检、错题入库联动、错误反馈追加)
4. BeforeCompact:  上下文压缩前 (自动提取关键决策沉淀到 decisions.md)
5. AfterCompact:   上下文压缩后 (合规性校验)
6. SessionEnd:     会话退出时 (学习进度与任务落盘)
"""

import sys
import re
from typing import Dict, Any, Callable, List, Tuple, Optional
from pathlib import Path

# ── [K6] 校名归一辅助（school_scope_guard 专用） ──────────────────────────
#: 校名后缀（「大学 / 学院」可互换归一到主干）。
_SCHOOL_SUFFIXES = ("大学", "学院")


def _school_aliases(name: str) -> set:
    """生成校名的等价写法集合（用于「是否同一所学校 / 学员是否提及」判定）。

    规则（刻意宽松：误放行 ≫ 误阻断，宁可多放行也不误伤正常跨校提问）：
      1. 全名（去空白）；
      2. 「大学 / 学院」后缀的主干（长度 ≥ 3 才收 —— 避免「北京」这类
         2 字主干把「北京师范大学」误判为提及「北京大学」）；
      3. 常见简称：「主干前缀 + 学科首字 + 大」（合成农业 → 合成农大）。
    """
    n = re.sub(r"\s+", "", str(name or ""))
    if not n:
        return set()
    alts = {n}
    for suf in _SCHOOL_SUFFIXES:
        if n.endswith(suf) and len(n) > len(suf):
            stem = n[: -len(suf)]
            if len(stem) >= 3:
                alts.add(stem)
                alts.add(stem[:-2] + stem[-2] + "大")
            break
    return alts


def _same_school(a: str, b: str) -> bool:
    """两个校名是否指同一所学校（别名归一后取交集比对）。"""
    aa = _school_aliases(a)
    bb = _school_aliases(b)
    if not aa or not bb:
        return False
    return bool(aa & bb)


def _school_mentioned(school: str, text: str) -> bool:
    """文本中是否提及该校（含常见简称归一）。"""
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return False
    return any(a and a in t for a in _school_aliases(school))


class HookEvent:
    SESSION_START = "SessionStart"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    BEFORE_COMPACT = "BeforeCompact"
    AFTER_COMPACT = "AfterCompact"
    SESSION_END = "SessionEnd"
    # ── [K7-U2] RunLoop 扩展点（无注册 = 恒等，零行为变化） ──
    #: 每轮 Agent 迭代开始前（step 递增后）；hook 可返回改写后的 messages。
    PREPARE_NEXT_TURN = "PrepareNextTurn"
    #: 每次 LLM 请求发出前（紧邻 _call_llm）；hook 可返回改写后的 messages。
    PREPARE_REQUEST = "PrepareRequest"
    #: 每轮迭代结束（LLM 响应处理完毕的任一出口）；通知型。
    FINISH_TURN = "FinishTurn"
    #: 整个 run 收尾（返回 final_answer 前）；通知型。
    FINISH_RUN = "FinishRun"

class HookManager:
    def __init__(self, workspace_root: Optional[Path] = None, memory_manager=None):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.memory_manager = memory_manager
        self.hooks: Dict[str, List[Tuple[int, Callable]]] = {
            HookEvent.SESSION_START: [],
            HookEvent.PRE_TOOL_USE: [],
            HookEvent.POST_TOOL_USE: [],
            HookEvent.BEFORE_COMPACT: [],
            HookEvent.AFTER_COMPACT: [],
            HookEvent.SESSION_END: [],
            HookEvent.PREPARE_NEXT_TURN: [],
            HookEvent.PREPARE_REQUEST: [],
            HookEvent.FINISH_TURN: [],
            HookEvent.FINISH_RUN: [],
        }
        self._register_builtin_hooks()

    def register_hook(self, event: str, func: Callable, priority: int = 100):
        """注册钩子函数，priority 越小优先级越高"""
        if event not in self.hooks:
            self.hooks[event] = []
        self.hooks[event].append((priority, func))
        self.hooks[event].sort(key=lambda x: x[0])

    def trigger_session_start(self, context: Dict[str, Any]):
        for _, func in self.hooks[HookEvent.SESSION_START]:
            try:
                func(context)
            except Exception as e:
                print(f"\033[93m[Hook Warning] SessionStart: {e}\033[0m")

    def trigger_pre_tool_use(self, tool_name: str, tool_args: Dict[str, Any], context: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """
        PreToolUse 拦截链:
        返回 (allow: bool, reason: str, modified_args: dict)
        只要有一个 hook 返回 False，则立即阻断该工具调用！
        """
        curr_args = tool_args
        for _, func in self.hooks[HookEvent.PRE_TOOL_USE]:
            try:
                allow, reason, mod_args = func(tool_name, curr_args, context)
                if not allow:
                    return False, reason, curr_args
                if mod_args:
                    curr_args = mod_args
            except Exception as e:
                return False, f"PreToolUse Hook 异常拦截: {e}", curr_args
        return True, "ok", curr_args

    def trigger_post_tool_use(self, tool_name: str, tool_args: Dict[str, Any], tool_result: str, context: Dict[str, Any]) -> str:
        """
        PostToolUse 审计链:
        允许 Hook 检查结果、记录日志或追加后置提示
        """
        curr_result = tool_result
        for _, func in self.hooks[HookEvent.POST_TOOL_USE]:
            try:
                feedback = func(tool_name, tool_args, curr_result, context)
                if feedback and isinstance(feedback, str):
                    curr_result = f"{curr_result}\n\n[System Hook Feedback]: {feedback}"
            except Exception as e:
                print(f"\033[93m[Hook Warning] PostToolUse: {e}\033[0m")
        return curr_result

    def trigger_before_compact(self, messages: List[Dict[str, Any]], context: Dict[str, Any]):
        for _, func in self.hooks[HookEvent.BEFORE_COMPACT]:
            try:
                func(messages, context)
            except Exception as e:
                print(f"\033[93m[Hook Warning] BeforeCompact: {e}\033[0m")

    def trigger_after_compact(self, messages: List[Dict[str, Any]], context: Dict[str, Any]):
        """[K7-U5] 压缩完成后的合规校验扩展点（context_engine.compact_context 调用）。

        此前 HookEvent.AFTER_COMPACT 已定义但**无任何触发方法**（死事件）；
        现补上触发链，注册方可在压缩结果上做校验/审计。无注册 = 恒等。
        """
        for _, func in self.hooks[HookEvent.AFTER_COMPACT]:
            try:
                func(messages, context)
            except Exception as e:
                print(f"\033[93m[Hook Warning] AfterCompact: {e}\033[0m")

    # ── [K7-U2] RunLoop 扩展点（无注册 = 恒等，零行为变化） ──

    def trigger_prepare_next_turn(self, messages: List[Dict[str, Any]],
                                  context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """每轮 Agent 迭代开始前的扩展点。

        注册的 hook 可返回改写后的 messages（返回 None = 保持不变）。
        无注册时原样返回入参（恒等）。
        """
        for _, func in self.hooks[HookEvent.PREPARE_NEXT_TURN]:
            try:
                out = func(messages, context)
                if out is not None:
                    messages = out
            except Exception as e:
                print(f"\033[93m[Hook Warning] PrepareNextTurn: {e}\033[0m")
        return messages

    def trigger_prepare_request(self, messages: List[Dict[str, Any]],
                                context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """每次 LLM 请求发出前的扩展点（紧邻 _call_llm）。

        与 PrepareNextTurn 同契约：hook 返回列表则替换 messages，None 保持不变。
        """
        for _, func in self.hooks[HookEvent.PREPARE_REQUEST]:
            try:
                out = func(messages, context)
                if out is not None:
                    messages = out
            except Exception as e:
                print(f"\033[93m[Hook Warning] PrepareRequest: {e}\033[0m")
        return messages

    def trigger_finish_turn(self, messages: List[Dict[str, Any]], context: Dict[str, Any]):
        """每轮迭代结束（LLM 响应处理完毕的任一出口）的通知型扩展点。"""
        for _, func in self.hooks[HookEvent.FINISH_TURN]:
            try:
                func(messages, context)
            except Exception as e:
                print(f"\033[93m[Hook Warning] FinishTurn: {e}\033[0m")

    def trigger_finish_run(self, context: Dict[str, Any]):
        """整个 run 收尾（返回 final_answer 前）的通知型扩展点。"""
        for _, func in self.hooks[HookEvent.FINISH_RUN]:
            try:
                func(context)
            except Exception as e:
                print(f"\033[93m[Hook Warning] FinishRun: {e}\033[0m")

    def trigger_session_end(self, context: Dict[str, Any]):
        for _, func in self.hooks[HookEvent.SESSION_END]:
            try:
                func(context)
            except Exception as e:
                print(f"\033[93m[Hook Warning] SessionEnd: {e}\033[0m")

    def _register_builtin_hooks(self):
        """注册考研学习链内置强制级钩子"""

        # ── 1. 考纲超纲红线强制拦截 Hook (PreToolUse) ──
        def _is_negation_or_note_context(text: str, forb: str) -> bool:
            """判断考点词是否属于笔记归纳、复盘总结、考纲禁区陈述或否定语境（如'数二不考三重积分'）"""
            neg_words = (
                r"不考|绝不考|严禁|严防|无需|除外|排除|非考点|非考查|不涉及|不包含|不要求"
                r"|不用|不必|不需|禁用|避免|勿|略过|跳过"
                # [C1 补漏] 口语否定词。「别 / 不要 / 不再 / 不做」是日常对话中最常见的
                # 否定形式，P19 补了「不用 / 禁用 / 避免 / 不必」却漏了这四类 ——
                # 实测「别讲曲面积分」「不要复习概率论」会被误判为超纲派题而**硬阻断**
                # （误伤正常教学对话，正是 P19 要解决的同类问题）。
                # [审查 P3 修复] 否定词与动词之间常插副词/助词（「不要**再**讲」
                # 「别**又**练」「别**给我**安排」），旧实现「不再」只在连写时能匹配、
                # 插「再」即失配 —— 实测「不要再讲三重积分」「别再讲三重积分」被误判
                # 超纲而硬阻断。现设计：① 单独列出「不要/不再/不做」；②「别」前瞻允许
                # 最多两个副词/助词后接动词（与 adv_gap 同构）；③「别」加后视排除
                # 「特/区/分/个/告/差/级/类/性/派」等含「别」词 —— 顺带修复旧实现
                # 「特别讲三重积分」「个别讲三重积分」被误判否定语境而漏拦的问题。
                r"|不要|不再|不做"
                r"|(?<![特区分个告差级类性派])别"
                r"(?=(?:\s{0,2}(?:再|又|还|给我|帮我|跟我|替我|去)){0,2}\s{0,2}"
                r"(?:讲|讲评|做|考|练|训练|安排|派发|布置|出|给|涉及|包含|要求"
                r"|复习|看|刷|要|掌握|学|背))"
            )
            # 否定词与考点之间常夹动词，如「不用**讲**三重积分」「避免**派发**伯努利方程」；
            # 动词还可连缀多个，如「不需要**掌握**格林公式」，中间亦可出现一个冒号，
            # 如「非考点：欧拉方程」。此处刻意使用「可重复的具体动词组 + 可选冒号」，
            # 而不是「任意字符」通配：后者会把「需要掌握格林公式」这类正向语境一并放行。
            gap_verb = (
                r"(?:(?:讲|讲评|做|考|练|训练|安排|派发|布置|出|给|涉及|包含|要求"
                r"|复习|看|刷|要|掌握|学|背)\s{0,2})*[:：]?\s{0,2}"
            )
            # 否定词与动词之间的副词/助词（「不要再**给我**讲」允许两级连缀）。
            adv_gap = r"(?:\s{0,2}(?:再|又|还|给我|帮我|跟我|替我|去)){0,2}"
            patterns = [
                r"(?:" + neg_words + r")\s{0,4}" + adv_gap + gap_verb + re.escape(forb),
                re.escape(forb) + r"\s{0,4}(?:不考|绝不考|已移出|已剔除|不作要求|超纲|禁区|不讲|不做|不练)",
                r"(?:决策|红线|禁区|对比|考纲说明|备考建议|非考点|非考查)\s*[:：].*?" + re.escape(forb),
            ]
            for pat in patterns:
                if re.search(pat, text, re.IGNORECASE):
                    return True
            return False

        def syllabus_guard_hook(tool_name: str, tool_args: Dict[str, Any], context: Dict[str, Any]):
            """
            根据当前数学科目类型（math1/2/3/396）判断红线：
              - math2 严禁：三重积分 / 曲线积分 / 曲面积分 / 格林公式 / 高斯公式 / 无穷级数 /
                傅里叶级数 / 向量代数与空间解析几何 / 欧拉方程 / 伯努利方程 / 概率论
              - math3 不允许曲线曲面积分与空间解析几何，但允许无穷级数与概率论（与数二不同）
              - math1 / math396 范围最广，几乎全部允许（仅把"超出考纲"情况作为软警告）
            """
            subj = context.get("active_subject", "math")
            if subj != "math":
                return True, "ok", tool_args

            # 兼容两种来源：context 显式注入 / cfg 隐式查找
            math_key = (
                context.get("math_key")
                or (self.memory_manager and getattr(self.memory_manager, "_current_math_key", None))
                or "math2"  # 默认按最严的 math2 处理
            )

            arg_text = str(tool_args).lower()
            base_reason = ""

            if math_key in ("math2", "math3"):
                # 数二、数三都不允许：曲线曲面积分 / 格林 / 高斯 / 斯托克斯 / 三重积分 /
                # 空间解析几何 / 欧拉方程 / 伯努利方程
                # [R2-B1 修复] 本清单此前漏了「斯托克斯公式」，而 syllabus_manager 生成的
                # 数二考纲正文（tools/syllabus_manager.py:124）与 01-数学/考试大纲.md 的
                # 「绝不超纲铁律」明确把它列为禁区 —— 守卫清单比考纲松，等于开了后门。
                # 现已与该权威清单逐项对齐（补：斯托克斯公式）。
                forbidden_core = [
                    "三重积分", "曲线积分", "曲面积分", "格林公式", "高斯公式",
                    "斯托克斯公式", "空间解析几何", "欧拉方程", "伯努利方程",
                ]
                # 仅数二不允许：无穷级数 / 傅里叶级数 / 概率论（数三含概率论与数理统计）
                forbidden_math2_only = ["无穷级数", "傅里叶级数", "概率论"]

                banned = forbidden_core + (forbidden_math2_only if math_key == "math2" else [])
                for forb in banned:
                    if forb in arg_text and not _is_negation_or_note_context(arg_text, forb):
                        base_reason = (
                            f"【🚨 考纲红线强制拦截 (Hook 触发)】：当前数学科目为 {math_key.upper()}，"
                            f"官方大纲明确规定【绝不考{forb}】！"
                        )
                        break
            elif math_key in ("math1", "math396"):
                # 数一、数三(396) 范围宽广；这里只记录为提示，不强制拦截
                # （若未来需要细粒度控制可在此处扩展）
                pass
            else:
                # 未知科目编码：保守按 math2 红线处理
                for forb in [
                    "三重积分", "曲线积分", "曲面积分", "格林公式", "高斯公式", "斯托克斯公式",
                    "无穷级数", "傅里叶级数", "概率论", "空间解析几何", "欧拉方程", "伯努利方程",
                ]:
                    if forb in arg_text and not _is_negation_or_note_context(arg_text, forb):
                        base_reason = (
                            f"【🚨 考纲红线保守拦截 (未识别科目 {math_key})】：疑似超纲【{forb}】，请确认。\n"
                        )
                        break

            if base_reason:
                return (
                    False,
                    base_reason
                    + "系统已强制阻断该工具调用。严禁让学员做超纲偏难怪题！"
                    "请立即切换至当前数学科目考纲内的题型。",
                    tool_args
                )
            return True, "ok", tool_args

        self.register_hook(HookEvent.PRE_TOOL_USE, syllabus_guard_hook, priority=10)

        # ── 1.5 院校范围守卫 Hook (PreToolUse) ──
        def school_scope_guard_hook(tool_name: str, tool_args: Dict[str, Any], context: Dict[str, Any]):
            """[K6] 防止模型脱离学员目标院校，自行侦察/比对无关院校。

            仅约束「院校级」工具（scout_school / diff_syllabus）：
              - 目标校未配置（空 / 未指定）→ 完全 no-op（零行为变化）；
              - 工具参数 school 与目标校一致（别名归一）→ 放行；
              - 学员本轮输入中显式提及该校 → 放行（PostToolUse 追加澄清）；
              - 其余（模型自创校名 / 换校且学员未提及）→ 阻断并引导回目标校。
            """
            if tool_name not in ("scout_school", "diff_syllabus", "compare_school", "compare_schools"):
                return True, "ok", tool_args
            target = str(context.get("target_school") or "").strip()
            if not target or target == "未指定":
                return True, "ok", tool_args
            if not isinstance(tool_args, dict):
                return True, "ok", tool_args
            school = str(tool_args.get("school") or "").strip()
            if not school or _same_school(school, target):
                return True, "ok", tool_args
            if _school_mentioned(school, context.get("user_input", "")):
                return True, "ok", tool_args
            return (
                False,
                f"【🎯 院校范围守卫】你的目标院校为「{target}」，"
                f"本次调用侦察/比对的是「{school}」——学员本轮并未提及该校。"
                f"请围绕目标院校「{target}」组织侦察；"
                f"若确需扩展到「{school}」，先向学员确认。",
                tool_args
            )

        self.register_hook(HookEvent.PRE_TOOL_USE, school_scope_guard_hook, priority=20)

        # ── 2. 工具结果后置自检与错题入库联动 Hook (PostToolUse) ──
        def post_audit_hook(tool_name: str, tool_args: Dict[str, Any], tool_result: str, context: Dict[str, Any]):
            # 若调用了 log_mistake 归档错题
            if tool_name == "log_mistake" and "Success" in tool_result:
                if self.memory_manager:
                    title = tool_args.get("title", "重点错题")
                    m_type = tool_args.get("mistake_type", "计算失误")
                    self.memory_manager.append_memory(
                        "session",
                        f"已沉淀错题: [{title}] · 错因分类: {m_type} · 进入 FSRS 待复测"
                    )
                return "已联动更新 Session 记忆与 FSRS 复测排期！"
            return ""

        self.register_hook(HookEvent.POST_TOOL_USE, post_audit_hook, priority=50)

        # ── 2.5 院校范围澄清 Hook (PostToolUse) ──
        def school_scope_clarify_hook(tool_name: str, tool_args: Dict[str, Any], tool_result: str, context: Dict[str, Any]):
            """[K6] 学员显式要求侦察非目标校时，在结果末尾追加范围澄清。

            仅在「放行但非目标校」场景（= 学员本轮提及了该校）追加提示，
            让模型与学员都清楚该情报属于对照参考，避免与目标校信息混淆。
            与 PreToolUse 的 school_scope_guard 同判定、纯函数重算（无状态）。
            """
            if tool_name not in ("scout_school", "diff_syllabus", "compare_school", "compare_schools"):
                return ""
            target = str(context.get("target_school") or "").strip()
            if not target or target == "未指定":
                return ""
            if not isinstance(tool_args, dict):
                return ""
            school = str(tool_args.get("school") or "").strip()
            if not school or _same_school(school, target):
                return ""
            if _school_mentioned(school, context.get("user_input", "")):
                return (
                    f"注意：本次侦察/比对的院校为「{school}」，与学员的目标院校"
                    f"「{target}」不同——以上情报仅作对照参考，请勿与目标院校信息混淆。"
                )
            return ""

        self.register_hook(HookEvent.POST_TOOL_USE, school_scope_clarify_hook, priority=55)

        # ── 2.7 拦截连续计数与引导升级 Hook (PostToolUse) ──
        def block_streak_guard_hook(tool_name: str, tool_args: Dict[str, Any],
                                    tool_result: str, context: Dict[str, Any]):
            """[K7-U3] 同类拦截连续计数 → 引导升级（原 loop 内联逻辑原位抽取）。

            动机（W11 实证）：安全拦截（run_command 白名单）与引擎直抓拦截
            （fetch_url）各自计数，连续达到阈值（默认 3）后文案升级为「停止
            试探」级警告；成功执行同类工具即重置（「连续」语义，非历史累计）。

            context 契约（缺任一键 → 完全 no-op，兼容其他调用方）：
              - ``block_streaks``：dict{"safety": int, "search": int}，就地更新；
              - ``block_streak_decisions``：list，把 (guide, kind) 决策 append
                进去，由 loop 在原位置（tool 消息入列之后）消费 —— hook 触发
                早于 tool 消息入列，故**不在此处**直接改 active_messages，
                以此保证消息顺序与事件顺序与抽取前逐字一致；
              - ``block_escalate_threshold``：int，升级阈值（默认 3）。
            """
            streaks = context.get("block_streaks")
            decisions = context.get("block_streak_decisions")
            if not isinstance(streaks, dict) or not isinstance(decisions, list):
                return ""
            try:
                threshold = int(context.get("block_escalate_threshold", 3))
            except (TypeError, ValueError):
                threshold = 3

            result_text = str(tool_result)
            block_kind = None
            if tool_name == "fetch_url" and "已拦截搜索引擎直抓" in result_text:
                block_kind = "search"
            elif tool_name == "run_command" and (
                    "安全拦截：" in result_text
                    or "PermissionDenied:" in result_text):
                block_kind = "safety"

            if not block_kind:
                # 成功执行同类工具 → 重置该类连续计数（「连续」语义）。
                if tool_name == "fetch_url":
                    streaks["search"] = 0
                elif tool_name == "run_command":
                    streaks["safety"] = 0
                return ""

            streaks[block_kind] = int(streaks.get(block_kind, 0)) + 1
            streak = streaks[block_kind]
            escalated = streak >= threshold
            if block_kind == "search":
                if escalated:
                    guide = (f"（系统提示）你已连续 {streak} 次尝试直抓搜索"
                             f"引擎且均被拦截。此路径在本环境已被彻底禁用"
                             f"——更换搜索引擎、猜测站内 URL、编写脚本抓取"
                             f"都不会成功。唯一有效的检索方式是 web_search"
                             f" 工具，请立即调用 web_search"
                             f"(query=\"你要搜索的关键词\")，"
                             f"不要再用 fetch_url 打开任何搜索引擎地址。")
                    decisions.append((guide, "search_guard_escalation"))
                else:
                    guide = ("（系统提示）搜索引擎直抓已被拦截。请立即调用 "
                             "web_search 工具完成检索"
                             "（如 web_search(query=\"你要搜索的关键词\")），"
                             "不要再尝试其他搜索引擎，也不要猜测站内 URL 路径。")
                    decisions.append((guide, "search_guard_nudge"))
            elif escalated:
                # safety 类 1-2 次不注入（拦截文案本身已含替代路径提示，
                # 保持既有行为）；达到阈值才注入「停止试探」警告。
                guide = (f"（系统提示）你已连续 {streak} 次触发命令安全拦截。"
                         f"本环境的命令执行已按白名单严格限制——更换命令、"
                         f"编写脚本、调整参数都会同样被拒，继续尝试只会"
                         f"浪费步数并可能导致任务超时。请立即停止命令试探，"
                         f"改用内置工具完成任务：读文件 read_file"
                         f"（PDF 自动提取文本）、搜索 grep / search_files、"
                         f"写产物 write_file / edit_file、真题抽题"
                         f"read_exam_paper。")
                decisions.append((guide, "safety_guard_escalation"))
            return ""

        self.register_hook(HookEvent.POST_TOOL_USE, block_streak_guard_hook, priority=60)

        # ── 3. 上下文压缩前记忆提取沉淀 Hook (BeforeCompact) ──
        def compaction_saver_hook(messages: List[Dict[str, Any]], context: Dict[str, Any]):
            if not self.memory_manager:
                return
            # 扫描即将被压缩的历史消息，提炼决策性语句
            decisions_found = []
            for msg in messages:
                if msg.get("role") == "user":
                    txt = msg.get("content", "")
                    if any(k in txt for k in ("我决定", "不要考", "只看", "我不擅长", "以后优先")):
                        decisions_found.append(txt[:80])
            for d in decisions_found:
                self.memory_manager.append_memory("decisions", f"[学员自主决策]: {d}")

        self.register_hook(HookEvent.BEFORE_COMPACT, compaction_saver_hook, priority=20)

        # ── 4. 会话结束日终复盘与 IM 自动推送 Hook (SessionEnd) ──
        def session_end_debrief_hook(context: Dict[str, Any]):
            """
            S3-1: 每日收工时自动生成当日复盘卡片并保存/推送
            """
            from datetime import datetime, date
            today_str = datetime.now().strftime("%Y-%m-%d")

            summary_lines = [
                f"🌙 **考研全科 AI 私教 · 今日学习复盘简报 ({today_str})**",
                f"--------------------------------------------------"
            ]

            # 1. 读取今日任务完成情况
            total_tasks = 0
            done_tasks = 0
            for d_name in ("01-数学", "02-英语", "03-思想政治理论", "04-专业课"):
                t_file = self.workspace_root / d_name / "_状态" / "今日任务.md"
                if not t_file.exists():
                    continue
                try:
                    text = t_file.read_text(encoding="utf-8", errors="replace")
                    # [修复] 此前这里漏掉了本行 splitlines 循环，导致下方 l 未定义、
                    # 抛 NameError 被 except 静默吞掉，达成率恒为 0.0% (0/0)。
                    for l in text.splitlines():
                        # 去除空格后判定是否为 Markdown 表格分隔符，避免虚增任务总量
                        if ("|" in l and not l.replace(" ", "").startswith("|---|")
                                and "完成状态" not in l and "模块" not in l):
                            total_tasks += 1
                            if "[x]" in l.lower():
                                done_tasks += 1
                except Exception as e:
                    # 不再静默吞错：统计失败会让达成率失真，必须让用户/日志看得见
                    print(f"[warn] 今日任务统计失败 ({t_file.name}): "
                          f"{type(e).__name__}: {e}", file=sys.stderr)

            rate = round(done_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0.0
            summary_lines.append(f"📋 今日任务达成率: **{rate}%** ({done_tasks}/{total_tasks} 项完成)")

            # 2. 统计到期待复测错题
            due_total = 0
            try:
                from skills import error_logger
                if error_logger:
                    for sk in ("math", "eng", "pol", "pro"):
                        due_items = error_logger.get_due_reviews(sk, max_count=10)
                        due_total += len(due_items)
            except Exception:
                pass
            summary_lines.append(f"🎯 明日待复测错题: **{due_total}** 道 (FSRS 队列自动监控中)")

            # 3. 记录到 daily completion
            try:
                import study_planner
                study_planner.record_daily_completion(rate=rate, total=total_tasks, completed=done_tasks, date_str=today_str)
            except Exception as e:
                # [R2-D1] 严格只读模式下被权限闸门拒绝是**正确行为**，但绝不能
                # 静默跳过 —— 静默跳过正是 P6 当初的病根（用户以为存了、其实没存）。
                # 这里只做「异常类型 -> 可读中文提示」的翻译，权限判定仍由
                # study_planner.record_daily_completion 内部的 ky_io 闸门负责。
                if type(e).__name__ == "PermissionDeniedError":
                    print(f"\033[93m[i] 严格只读模式 (--permission=safe)：已跳过「今日完成度」"
                          f"写入，ky_config.json 保持原样。\033[0m")
                else:
                    print(f"[warn] 今日完成度写入失败: {type(e).__name__}: {e}", file=sys.stderr)

            # 4. 尝试向已配置的 IM 推送日终简报
            cfg_path = self.workspace_root / "ky_config.json"
            if cfg_path.exists():
                try:
                    import json
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    webhooks = cfg.get("webhooks", {})
                    if any(webhooks.values()):
                        debrief_text = "\n".join(summary_lines) + "\n\n保持节奏，今日复习圆满收工！🎓"
                        try:
                            import ky_cli
                            ky_cli.broadcast_briefing(cfg, custom_msg=debrief_text)
                        except Exception:
                            pass
                except Exception:
                    pass

            context["debrief_summary"] = "\n".join(summary_lines)

        self.register_hook(HookEvent.SESSION_END, session_end_debrief_hook, priority=10)
