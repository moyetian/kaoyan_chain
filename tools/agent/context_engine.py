# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 上下文引擎与压缩系统 (Context Engine & Compaction)
职责:
1. 组装多层次系统协议 (AGENTS.md + 学科协议 + 学情档案 + 资料白名单 + 工具指令)
2. 维护多轮对话历史 (User, Assistant, Tool Calls, Tool Results)
3. Context Compaction: 超过 Token 阈值时自动压缩早期 Tool 执行记录，杜绝爆 Context
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Any

class ContextEngine:
    def __init__(self, workspace_root: Path, active_subject: str = "math", max_context_tokens: int = 48000, memory_manager=None):
        self.workspace_root = Path(workspace_root).resolve()
        self.active_subject = active_subject
        self.max_context_tokens = max_context_tokens
        self.memory_manager = memory_manager
        self.messages: List[Dict[str, Any]] = []

    def set_subject(self, subject: str):
        self.active_subject = subject

    def build_system_prompt(self, tools_description: str = "") -> str:
        """多层次组装系统提示词"""
        sys_parts = []

        # 0. 三级分层记忆挂载
        if self.memory_manager:
            mem_text = self.memory_manager.load_all_memory()
            if mem_text:
                sys_parts.append(mem_text)

        # 1. 顶层总控协议 AGENTS.md
        root_agents = self.workspace_root / "AGENTS.md"
        if root_agents.exists():
            sys_parts.append("=== 【顶层最高总控协议 AGENTS.md】 ===\n" + self._read_safe(root_agents))

        # 2. 科目子协议
        subj_map = {
            "math": ("01-数学", "数学专属私教"),
            "eng": ("02-英语", "英语专属私教"),
            "pol": ("03-思想政治理论", "政治专属私教"),
            "pro": ("04-专业课", "专业课专属私教"),
        }
        folder, name = subj_map.get(self.active_subject, ("01-数学", "数学专属私教"))
        s_dir = self.workspace_root / folder

        subj_agents = s_dir / "AGENTS.md"
        if subj_agents.exists():
            sys_parts.append(f"\n=== 【当前学科专项协议：{name}】 ===\n" + self._read_safe(subj_agents))

        # 3. 学情档案与记忆状态
        state_files = [
            ("今日任务", s_dir / "_状态" / "今日任务.md", s_dir / "_状态" / "今日任务.template.md"),
            ("学员档案", s_dir / "_状态" / "学员档案.md", s_dir / "_状态" / "学员档案.template.md"),
            ("薄弱点雷达", s_dir / "_状态" / "薄弱点雷达.md", s_dir / "_状态" / "薄弱点雷达.template.md"),
            ("专业课学情", s_dir / "学情档案.md", s_dir / "学情档案.template.md"),
            ("考试大纲", s_dir / "考试大纲.md", None),
        ]
        state_snippets = []
        for label, real_p, tmpl_p in state_files:
            p = real_p if real_p.exists() else (tmpl_p if tmpl_p and tmpl_p.exists() else None)
            if p and p.exists():
                txt = self._read_safe(p)
                if txt.strip():
                    state_snippets.append(f"--- [{label}] ({p.name}) ---\n{txt}")

        if state_snippets:
            sys_parts.append(f"\n=== 【当前学员学情档案与记忆状态 ({name})】 ===\n" + "\n\n".join(state_snippets))

        # 4. 扫描参考资料白名单
        mat_dir = s_dir / "参考资料"
        mat_files = []
        if mat_dir.exists():
            for f in mat_dir.iterdir():
                if f.is_file() and f.name.lower() not in ("readme.md", ".gitkeep", ".gitignore"):
                    mat_files.append(f.name)

        if mat_files:
            sys_parts.append(
                f"\n=== 📚【本地真题与资料白名单清单 ({name})】===\n"
                f"本地「参考资料/」目录下实际存放的文件为：{', '.join(mat_files)}。\n"
                "【重要能力指令】：当学员要求从上述参考资料中抽题或查阅试卷时，你拥有真正的外部工具 (read_exam_paper / read_file)！"
                "严禁回答“由于技术限制我无法读取本地文件”，你必须直接调用 read_exam_paper 或 read_file 工具提取真题原题，然后展示给学员并批改！"
            )
        else:
            sys_parts.append(
                f"\n=== 🚨【本地暂未放入参考资料】===\n"
                f"当前「参考资料/」暂无本地文件。若学员指定真题题目或从外部输入，严格针对学员输入解答，绝不虚构题目来自未核验的书籍！"
            )

        # 5. Agent Loop 工具调用行为规范
        sys_parts.append(
            "\n=== 🤖【Agent 智能体工具调用行为规范 (Claude Code / Codex 标准)】 ===\n"
            "你不是被动的普通聊天机器人，你拥有自主规划与执行工具链的能力：\n"
            "1. 当需要获取真题题干、阅读本地考研文件时，立即调用 read_exam_paper 或 read_file；\n"
            "2. 当需要为学员记录错题时，立即调用 log_mistake 工具写入错题本；\n"
            "3. 当需要高精度验算微积分、微分方程(ODE)、二次型正定性时，立即调用 verify_math 杜绝计算幻觉；\n"
            "4. 当学员表示卡壳毫无思路时，调用 socratic_hint 分级给微步骤提示，绝不剧透终极答案；\n"
            "5. 当任务需要多个步骤时，自主分步调用工具，直到拿到最终结果再向学员汇报！"
        )

        return "\n\n".join(sys_parts)

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """粗略估算消息 Token 量 (中英混合 1 字符约 0.6 token)"""
        total_chars = 0
        for m in messages:
            content = m.get("content") or ""
            total_chars += len(content)
            if "tool_calls" in m:
                total_chars += len(str(m["tool_calls"]))
        return int(total_chars * 0.6)

    def compact_context(self, messages: List[Dict[str, Any]], hook_manager=None) -> List[Dict[str, Any]]:
        """
        Context Compaction 算法:
        当估算 Token 超过阈值时，保留 System Prompt 与最近 4 轮交互，
        将早期冗长 Tool Results 压缩为精简摘要，释放大量上下文空间。
        """
        cur_tokens = self.estimate_tokens(messages)
        if cur_tokens <= self.max_context_tokens or len(messages) <= 6:
            return messages

        # 触发 BeforeCompact Hook 提取关键记忆
        if hook_manager:
            hook_manager.trigger_before_compact(messages, {"active_subject": self.active_subject})

        # 保持第 0 项 (System Prompt)
        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
        non_system = messages[1:] if system_msg else messages

        # 保留最后 6 条消息
        keep_tail = non_system[-6:]
        history_to_compress = non_system[:-6]

        compressed_summary_lines = []
        for msg in history_to_compress:
            role = msg.get("role")
            if role == "user":
                compressed_summary_lines.append(f"学员此前曾提问: {msg.get('content', '')[:100]}")
            elif role == "tool":
                tool_name = msg.get("name", "tool")
                compressed_summary_lines.append(f"智能体执行了工具 [{tool_name}] 并获取了数据")
            elif role == "assistant" and msg.get("content"):
                compressed_summary_lines.append(f"私教给出了辅导要点: {msg.get('content', '')[:100]}")

        summary_text = (
            "【历史上下文压缩摘要 (Context Compaction)】:\n" +
            "\n".join(compressed_summary_lines[-10:]) +
            "\n(早期工具执行细节已自动精简以节省上下文)"
        )

        compacted = []
        if system_msg:
            compacted.append(system_msg)
        compacted.append({"role": "system", "content": summary_text})
        compacted.extend(keep_tail)

        return compacted

    def _read_safe(self, p: Path) -> str:
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return p.read_text(encoding=enc)
            except Exception:
                continue
        return ""
