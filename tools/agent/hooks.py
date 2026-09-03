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
            subj = context.get("active_subject", "math")
            # 当数学科目且为数学二时
            if subj == "math":
                arg_text = str(tool_args).lower()
                # 数二绝不考的红线超纲词
                math2_forbidden = ["三重积分", "曲面积分", "曲线积分", "格林公式", "高斯公式", "无穷级数", "傅里叶级数"]
                for forb in math2_forbidden:
                    if forb in arg_text:
                        return (
                            False,
                            f"【🚨 考纲红线强制拦截 (Hook 触发)】：当前科目为数学二 (302)，官方大纲明确规定【绝不考{forb}】！"
                            f"系统已强制阻断该工具调用。严禁让学员做超纲偏难怪题！请立即调整为数二考纲内题型。",
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
