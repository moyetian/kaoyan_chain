# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 上下文引擎与压缩系统 (Context Engine & Compaction)
职责:
1. 组装多层次系统协议 (AGENTS.md + 学科协议 + 学情档案 + 资料白名单 + 工具指令)
2. 维护多轮对话历史 (User, Assistant, Tool Calls, Tool Results)
3. Context Compaction: 超过 Token 阈值时自动压缩早期 Tool 执行记录，杜绝爆 Context
"""

from pathlib import Path
from typing import List, Dict, Any, Optional

from .compaction import (
    build_structured_summary,
    llm_summarize,
    render_summary,
    resolve_compact_mode,
)
from .tokenizer import Tokenizer, heuristic_count_messages, make_budget, resolve_budget

try:
    import ky_rust_ext as _rust
    _HAS_RUST_EXT = True
except ImportError:
    _rust = None
    _HAS_RUST_EXT = False


class ContextEngine:
    def __init__(self, workspace_root: Path, active_subject: str = "math",
                 max_context_tokens: Optional[int] = None, memory_manager=None,
                 model: str = "", config: Optional[Dict[str, Any]] = None):
        self.workspace_root = Path(workspace_root).resolve()
        self.active_subject = active_subject
        self.memory_manager = memory_manager
        self.messages: List[Dict[str, Any]] = []
        # 配置留给 compact_context 读 agent.compact_mode / 供 LLM 摘要复用；
        # 非 dict 一律收敛为空 dict，后续解析函数自己兜底（fail-safe）。
        self.config: Dict[str, Any] = config if isinstance(config, dict) else {}

        # [P0-1 修复·硬编码 48000] 预算解析：显式参数 > config['context']['max_tokens']
        # > 模型查表 > 保守默认。`max_context_tokens` 语义为**模型窗口**，
        # 压缩触发线是 (窗口 − 输出预留) × 水位，见 tokenizer.Budget。
        if max_context_tokens is None:
            budget = resolve_budget(model, config)
        else:
            budget = make_budget(max_context_tokens, "explicit")
        self.model = model or ""
        self.budget = budget
        self.max_context_tokens = budget.window
        self.compact_watermark = budget.watermark
        self.tokenizer = Tokenizer(self.model)

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

        # 4.5 目标院校招考情报、考纲变动与社媒真实经验档案动态挂载
        intel_snippets = []
        target_school = ""
        target_major = ""
        cfg_file = self.workspace_root / "ky_config.json"
        if cfg_file.exists():
            try:
                import json
                cfg_obj = json.loads(self._read_safe(cfg_file))
                sp = cfg_obj.get("study_plan", {})
                target_school = sp.get("school", "").strip()
                target_major = sp.get("major", "").strip()
            except Exception:
                pass

        if target_school and target_school != "未指定":
            # 检索 .memory/experiences/<学校>_*.md 或 docs/experiences/<学校>_*.md
            exp_dir = self.workspace_root / ".memory" / "experiences"
            if not exp_dir.exists():
                exp_dir = self.workspace_root / "docs" / "experiences"
            if exp_dir.exists():
                for exp_file in exp_dir.glob("*.md"):
                    if target_school in exp_file.stem:
                        txt = self._read_safe(exp_file)
                        if txt.strip():
                            intel_snippets.append(f"--- [目标院校社媒就读与避坑经验 ({exp_file.name})] ---\n{txt[:1800]}")
                            break

            # 检索 04-专业课/考纲变动分析_*.md
            pro_dir = self.workspace_root / "04-专业课"
            if pro_dir.exists():
                diff_files = sorted(list(pro_dir.glob("考纲变动分析_*.md")), key=lambda p: p.stat().st_mtime, reverse=True)
                if diff_files:
                    latest_diff = diff_files[0]
                    diff_txt = self._read_safe(latest_diff)
                    if diff_txt.strip():
                        intel_snippets.append(f"--- [最新专业课考纲动荡与变动分析 ({latest_diff.name})] ---\n{diff_txt[:1500]}")

        if intel_snippets:
            sys_parts.append(
                f"\n=== 🎯【目标院校考情与社媒口碑实证档案 ({target_school})】===\n"
                "以下为系统通过研招情报与社媒降噪过滤算法沉淀的真实考情与考纲变动分析，请在向学员做院校分析、答疑和制定复习策略时充分应用：\n"
                + "\n\n".join(intel_snippets)
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

        # 6. 学员报到与会话启动交互规范 (Onboarding & Daily Greeting Protocol)
        sys_parts.append(
            "\n=== 📋【学员“报到”口令核心响应规范 (必读必遵)】 ===\n"
            "当学员输入“报到”、“<科目>报到”（如“英语报到”“政治报到”“专业课报到”，"
            "不考数学的方案不出现“数学报到”）或会话首次启动时：\n"
            "【第一阶段：全景学情战况汇报与今日规划】\n"
            "1. 首先明确读取并向学员汇报学员的基本盘信息：目标院校、报考专业、考试科目、目标分数、初试倒计时、每日时间预算；\n"
            "2. 汇报今日该科目的复习攻坚路线图（根据今日任务与学员薄弱点，分段规划：如概念梳理 XX 分钟、真题实战 XX 分钟、订正归档 XX 分钟）；\n"
            "3. 明确通报当前本地已就绪的白名单实体参考资料（如张宇1000题、历年真题等）；\n"
            "【第二阶段：主动派发今日实战第 1 题】\n"
            "4. 汇报完规划后，主动从本地真题或对应考点库中派发今日第 1 道针对性真题或自测题（展示清晰题干、分值、考查重点）；\n"
            "5. 提示学员在草稿纸上动笔演算，完成后直接在输入框提交作答或拍照上传（/img），由私教按考研采分点逐步赋分并归因错题！\n"
            "严禁一上来完全不汇报学员信息与整体规划就自说自话地去调特定冷门题！"
        )

        # 7. 学员作答与“交作业”批改规范
        sys_parts.append(
            "\n=== 📝【学员作答与“交作业”批改规范】 ===\n"
            "当学员提交了题目答案、推导草稿或输入“交作业”时：\n"
            "1. 严格按照考研阅卷人标准分步骤批改：在推导每个关键步骤明确标注采分点（如 [+2分]、[-1分]）；\n"
            "2. 若有失误，坚决指出错因五分类（概念漏洞/审题偏差/公式记错/计算失误/书写丢分），并给出针对性改进处方；\n"
            "3. 若学员答错或部分失误，必须主动调用 log_mistake 工具，将本题题干、失误点、错因、正确解答记录到错题本，并纳入 FSRS 复测队列！"
        )

        return "\n\n".join(sys_parts)

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """估算消息 Token 量（tiktoken 精确 > Rust 极速启发式 > Python 启发式）。

        [P0-1 修复·假 tokenizer] 旧实现是 `int(total_chars * 0.6)`：对所有字符
        一律 0.6，而实测中英密度差 3 倍以上（汉字 ≈0.52、英文 ≈0.18 token/字符），
        英文/代码会话因此被高估 3 倍、压缩过晚。现走 `tokenizer` 模块的五类字符
        启发式；Rust 侧与 Python 侧逐位一致（整数运算）。

        `_force_python` 沿用全仓约定（见 `intelligence/extractor.py`、
        `skills/material_ingestion.py`）：**跳过加速实现、走纯 Python 回退**。
        Rust 侧只能做启发式，故此处它同时跳过 tiktoken 精确路径 —— 否则
        `test_new_features.py` 的双模一致性校验会拿 tiktoken 去比 Rust 启发式。
        """
        force_python = getattr(self, "_force_python", False)
        # 1) tiktoken 精确路径：装了可选依赖就用精确值
        if self.tokenizer.has_encoder and not force_python:
            return self.tokenizer.count_messages(messages)
        # 2) Rust 极速路径
        if _HAS_RUST_EXT and not force_python:
            try:
                return _rust.estimate_tokens(messages)
            except Exception:
                pass
        # 3) Python 启发式
        return heuristic_count_messages(messages)

    def compact_context(self, messages: List[Dict[str, Any]], hook_manager=None,
                        focus: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Context Compaction 算法:
        当估算 Token 超过**压缩水位**（可用预算 = 窗口 − 输出预留，取其 70%；
        见 `tokenizer.Budget`）时，保留 System Prompt 与最近 4 轮交互，
        将早期消息压缩为**结构化摘要**（见 `agent.compaction`），释放上下文空间。

        [B1 修复·早期约束被静默丢弃] 旧实现只保留最后 10 行
        `学员此前曾提问: {content[:100]}`，早期出现的考纲约束、错因会随行数上限
        整段丢失。现改为结构化摘要：考纲约束/错因/待复习整条保留（上限 400 字符）。
        摘要模式由 `agent.compact_mode` 决定（`rule_only` / `llm`，非法回落前者）；
        `llm` 模式失败时**静默降级**回规则摘要，绝不因摘要失败而中断会话。
        `focus` 为可选关注点：规则模式下命中的消息在 goal/progress 抽取时优先保留。
        """
        cur_tokens = self.estimate_tokens(messages)
        if cur_tokens <= self.compact_watermark or len(messages) <= 6:
            return messages

        # 触发 BeforeCompact Hook 提取关键记忆
        if hook_manager:
            hook_manager.trigger_before_compact(messages, {"active_subject": self.active_subject})

        # 保持第 0 项 (System Prompt)
        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
        non_system = messages[1:] if system_msg else messages

        # 安全切分：保证 assistant(tool_calls) 与其匹配的 tool 结果成对保留，杜绝孤儿消息
        total_len = len(non_system)
        cut = max(0, total_len - 6)

        # 若 cut 指向 tool 消息，优先向前追溯至发起调用的 assistant
        step_back_cut = cut
        while step_back_cut > 0 and non_system[step_back_cut].get("role") == "tool":
            step_back_cut -= 1
        if step_back_cut > 0 and non_system[step_back_cut - 1].get("role") == "assistant" and non_system[step_back_cut - 1].get("tool_calls"):
            step_back_cut -= 1

        # 若向前回溯导致保留全部消息且历史很长，则向后越过当前 tool 消息组
        if step_back_cut == 0 and total_len > 8:
            step_forward_cut = cut
            while step_forward_cut < total_len and non_system[step_forward_cut].get("role") == "tool":
                step_forward_cut += 1
            cut = step_forward_cut if step_forward_cut < total_len else 0
        else:
            cut = step_back_cut

        keep_tail = non_system[cut:]
        history_to_compress = non_system[:cut]

        # 摘要生成：先按 compact_mode 决定路径，LLM 失败即降级规则摘要
        mode = resolve_compact_mode(self.config)
        summary = None
        if mode == "llm":
            summary = llm_summarize(
                history_to_compress,
                focus=focus,
                config=self.config,
                workspace_root=self.workspace_root,
            )
            if summary is None:
                print(f"\033[93m[压缩] LLM 结构化摘要不可用（未配置 / 调用失败 / 返回非法），"
                      f"本次已降级为规则摘要\033[0m")
        if summary is None:
            summary = build_structured_summary(history_to_compress, focus=focus)
            if mode != "llm":
                self._notice_rule_summary_once()

        summary_text = render_summary(summary)

        compacted = []
        if system_msg:
            compacted.append(system_msg)
        compacted.append({"role": "system", "content": summary_text})
        compacted.extend(keep_tail)

        return compacted

    def _notice_rule_summary_once(self) -> None:
        """规则摘要的首次启用提示：每实例只提示一次，避免每轮压缩刷屏。"""
        if getattr(self, "_rule_summary_notice_shown", False):
            return
        self._rule_summary_notice_shown = True
        print("\033[93m[压缩] 会话超长，已启用规则摘要（考纲约束 / 错因 / 待复习整条保留）；"
              "如需更高质量摘要可在 ky_config.json 设置 agent.compact_mode=llm\033[0m")

    def _read_safe(self, p: Path) -> str:
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return p.read_text(encoding=enc)
            except Exception:
                continue
        return ""


# 别名兼容
ContextCompactor = ContextEngine

