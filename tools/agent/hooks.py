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

class HookEvent:
    SESSION_START = "SessionStart"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    BEFORE_COMPACT = "BeforeCompact"
    AFTER_COMPACT = "AfterCompact"
    SESSION_END = "SessionEnd"

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
            HookEvent.SESSION_END: []
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

    def trigger_session_end(self, context: Dict[str, Any]):
        for _, func in self.hooks[HookEvent.SESSION_END]:
            try:
                func(context)
            except Exception as e:
                print(f"\033[93m[Hook Warning] SessionEnd: {e}\033[0m")

    def _register_builtin_hooks(self):
        """注册考研学习链内置强制级钩子"""

        # ── 1. 考纲超纲红线强制拦截 Hook (PreToolUse) ──
        def syllabus_guard_hook(tool_name: str, tool_args: Dict[str, Any], context: Dict[str, Any]):
            """
            根据当前数学科目类型（math1/2/3/396）判断红线：
              - math2 严禁：三重积分 / 曲面积分 / 曲线积分 / 格林公式 / 高斯公式 / 无穷级数 / 傅里叶级数 / 向量代数与空间解析几何 / 欧拉方程 / 伯努利方程
              - math3 不允许曲线曲面积分，但允许无穷级数（与数二不同）
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
                # 数二、数三都不允许：曲线曲面积分 / 格林 / 高斯 / 三重积分
                forbidden_core = ["三重积分", "曲线积分", "曲面积分", "格林公式", "高斯公式"]
                # 仅数二不允许：无穷级数 / 傅里叶级数
                forbidden_math2_only = ["无穷级数", "傅里叶级数"]

                banned = forbidden_core + (forbidden_math2_only if math_key == "math2" else [])
                for forb in banned:
                    if forb in arg_text:
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
                for forb in ["三重积分", "曲线积分", "曲面积分", "格林公式", "高斯公式", "无穷级数", "傅里叶级数"]:
                    if forb in arg_text:
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

        # ── 2. 工具结果后置自检与错题入库联动 Hook (PostToolUse) ──
        def post_audit_hook(tool_name: str, tool_args: Dict[str, Any], tool_result: str, context: Dict[str, Any]):
            # 若调用了 log_mistake 归档错题
            if tool_name == "log_mistake" and "Success" in tool_result:
                if self.memory_manager:
                    title = tool_args.get("title", "重点错题")
                    m_type = tool_args.get("mistake_type", "计算失误")
                    self.memory_manager.append_memory(
                        "session",
                        f"已沉淀错题: [{title}] · 错因分类: {m_type} · 进入艾宾浩斯待复测"
                    )
                return "已联动更新 Session 记忆与艾宾浩斯复测排期！"
            return ""

        self.register_hook(HookEvent.POST_TOOL_USE, post_audit_hook, priority=50)

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
                if t_file.exists():
                    try:
                        lines = t_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                        for l in lines:
                            if "|" in l and not l.startswith("|---|") and "完成状态" not in l and "模块" not in l:
                                total_tasks += 1
                                if "[x]" in l.lower():
                                    done_tasks += 1
                    except Exception:
                        pass

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
            summary_lines.append(f"🎯 明日待复测错题: **{due_total}** 道 (艾宾浩斯队列自动监控中)")

            # 3. 记录到 daily completion
            try:
                import study_planner
                study_planner.record_daily_completion(rate=rate, total=total_tasks, completed=done_tasks, date_str=today_str)
            except Exception:
                pass

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
