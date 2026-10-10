# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 上下文引擎与压缩系统 (Context Engine & Compaction)
职责:
1. 组装多层次系统协议 (AGENTS.md + 学科协议 + 学情档案 + 资料白名单 + 工具指令)
2. 维护多轮对话历史 (User, Assistant, Tool Calls, Tool Results)
3. Context Compaction: 超过 Token 阈值时自动压缩早期 Tool 执行记录，杜绝爆 Context
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

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

#: 占位大纲标记（单一真源：syllabus_manager.PRO_PLACEHOLDER_MARKER）。
#: [问题5 修复·专业课无大纲却谎称按纲出题] 系统为自命题科目生成的骨架大纲
#: 含此标记；挂载时必须如实告知 AI「这是待填骨架」，否则 LLM 会向学员
#: 宣称「已按考纲出题」。导入失败时退回字面量（与真源同值，由测试钉住）。
try:
    try:
        from syllabus_manager import PRO_PLACEHOLDER_MARKER as _SYLLABUS_PLACEHOLDER_MARKER
    except ImportError:  # pragma: no cover - 包式导入上下文
        from tools.syllabus_manager import PRO_PLACEHOLDER_MARKER as _SYLLABUS_PLACEHOLDER_MARKER
except Exception:  # pragma: no cover - 极端环境下不阻断上下文组装
    _SYLLABUS_PLACEHOLDER_MARKER = "【待自填"


#: [P2 修复·2026-10-08 系统提示组装无预算/优先级] 系统提示总长预算（字符）。
#: 超预算时按优先级从低到高截断「数据类」段落（保留头部并插入显式截断标记），
#: 「规范类」关键段（总控/科目协议、工具与作答契约）永不截断 —— 这是**软上限**：
#: 仅关键段本身超预算时允许整体超出（宁可超长，不可静默丢行为契约）。
#: 40k 字符按本仓启发式系数（0.5–1.5 token/字符）≈ 20–60k token，低于默认
#: 128k 窗口的压缩水位 84k；实测常规工作区整段约 14k 字符（约 3 倍余量）。
SYSTEM_PROMPT_CHAR_BUDGET = 40000

#: 非关键段被截断时的最少保留字符数（保头部；尾部内容可由 read_file 按需现读）。
_MIN_KEEP_CHARS = 2000

#: 段落优先级（build_system_prompt 组装标签）：
#:   0 = 规范类关键段（行为契约，永不截断）
#:   1 = 重要数据段（超预算时保头部 + 截断标记）
#:   2 = 辅助数据段（超预算时整段让位 + 省略标记）
_PRIORITY_CRITICAL = 0
_PRIORITY_IMPORTANT = 1
_PRIORITY_AUXILIARY = 2


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

    def build_system_prompt(self, tools_description: str = "", kaoyan_ctx=None) -> str:
        """多层次组装系统提示词

        [K6] ``kaoyan_ctx``（KaoyanContext / 鸭子类型对象）：非 None 时
        ``target_school`` 直接取 ctx（不再读盘 ky_config.json），保证 Agent
        提示与 hook 判定使用**同一份**目标校；None 保持旧行为（读工作区配置）。

        [P2 修复·2026-10-08 无预算/优先级] 各段带优先级标签（0 规范类关键段 /
        1 重要数据 / 2 辅助数据），统一经 :meth:`_assemble_system_prompt` 组装
        并执行总长预算（见 ``SYSTEM_PROMPT_CHAR_BUDGET``）。
        """
        sys_parts: List[Tuple[int, str]] = []

        # 0. 三级分层记忆挂载（数据类：重要）
        if self.memory_manager:
            mem_text = self.memory_manager.load_all_memory()
            if mem_text:
                sys_parts.append((_PRIORITY_IMPORTANT, mem_text))

        # 1. 顶层总控协议 AGENTS.md（规范类：关键，不截断）
        root_agents = self.workspace_root / "AGENTS.md"
        if root_agents.exists():
            sys_parts.append((_PRIORITY_CRITICAL,
                              "=== 【顶层最高总控协议 AGENTS.md】 ===\n" + self._read_safe(root_agents)))

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
            sys_parts.append((_PRIORITY_CRITICAL,
                              f"\n=== 【当前学科专项协议：{name}】 ===\n" + self._read_safe(subj_agents)))

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
                    if label == "考试大纲":
                        txt = self._annotate_syllabus(txt)
                    state_snippets.append(f"--- [{label}] ({p.name}) ---\n{txt}")

        if state_snippets:
            sys_parts.append((_PRIORITY_IMPORTANT,
                              f"\n=== 【当前学员学情档案与记忆状态 ({name})】 ===\n" + "\n\n".join(state_snippets)))

        # 3.5 [P1 修复·2026-10-08 K5 学情不进上下文] 学情速览：完成率 / 到期复测 / 错因分布
        # 实时统计注入。修复前这些统计从不进 agent 上下文，私教完全看不到
        # 「学得怎么样」的数据（完成率、到期复测、错因分布均无），无法回答
        # 学情类问题。数据源全部为现成单一真源（见 _build_study_overview）。
        overview = self._build_study_overview()
        if overview:
            sys_parts.append((_PRIORITY_IMPORTANT, overview))

        # 4. 扫描参考资料白名单（递归含子目录；与 material_scanner 同源实现）
        # [问题5 根因修复·子目录资料不识别] 旧实现只 iterdir() 单层：学员把
        # 资料整理进子目录（如 参考资料/英语真题/2020.pdf）后此处扫描不到，
        # Agent 回复「未放置资料」。现递归并显示相对路径（同名文件可区分）。
        mat_dir = s_dir / "参考资料"
        mat_files = []
        try:
            try:
                from skills.material_scanner import iter_material_files, relative_label
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.skills.material_scanner import iter_material_files, relative_label
            mat_files = [relative_label(mat_dir, f)
                         for f in iter_material_files(mat_dir)]
        except Exception:
            mat_files = []

        if mat_files:
            sys_parts.append((
                _PRIORITY_IMPORTANT,
                f"\n=== 📚【本地真题与资料白名单清单 ({name})】===\n"
                f"本地「参考资料/」目录下实际存放的文件为：{', '.join(mat_files)}。\n"
                "【重要能力指令】：当学员要求从上述参考资料中抽题或查阅试卷时，你拥有真正的外部工具 (read_exam_paper / read_file)！"
                "严禁回答“由于技术限制我无法读取本地文件”，你必须直接调用 read_exam_paper 或 read_file 工具提取真题原题，然后展示给学员并批改！"
            ))
        else:
            sys_parts.append((
                _PRIORITY_IMPORTANT,
                f"\n=== 【本地暂未放入参考资料】===\n"
                f"当前「参考资料/」暂无本地文件。若任务不依赖本地资料（如生成计划、"
                f"整理内容、直接作答），请直接完成，不要因缺少参考资料而拒绝作答或"
                f"输出空回复；若学员指定真题题目或从外部输入，严格针对学员输入解答，"
                f"绝不虚构题目来自未核验的书籍！"
            ))

        # 4.5 目标院校招考情报、考纲变动与社媒真实经验档案动态挂载
        intel_snippets = []
        target_school = ""
        # [K6] 优先取统一上下文（与 hooks.school_scope_guard 同源）；
        # kaoyan_ctx 为 None 时保持旧行为：从工作区 ky_config.json 读取。
        if kaoyan_ctx is not None:
            target_school = str(getattr(kaoyan_ctx, "target_school", "") or "").strip()
        else:
            cfg_file = self.workspace_root / "ky_config.json"
            if cfg_file.exists():
                try:
                    import json
                    cfg_obj = json.loads(self._read_safe(cfg_file))
                    sp = cfg_obj.get("study_plan", {})
                    target_school = sp.get("school", "").strip()
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
            sys_parts.append((
                _PRIORITY_AUXILIARY,
                f"\n=== 🎯【目标院校考情与社媒口碑实证档案 ({target_school})】===\n"
                "以下为系统通过研招情报与社媒降噪过滤算法沉淀的真实考情与考纲变动分析，请在向学员做院校分析、答疑和制定复习策略时充分应用：\n"
                + "\n\n".join(intel_snippets)
            ))

        # 5. Agent Loop 工具调用行为规范：真实工作区用考研教练版；评测/空
        # workspace 用通用版（避免 read_exam_paper / log_mistake 等考研术语
        # 干扰无关任务作答——评测实测模型因此类干扰对生成类任务空回复）。
        if (self.workspace_root / "AGENTS.md").exists():
            # 5. Agent Loop 工具调用行为规范（规范类：关键，不截断）
            sys_parts.append((
                _PRIORITY_CRITICAL,
                "\n=== 🤖【Agent 智能体工具调用行为规范 (Claude Code / Codex 标准)】 ===\n"
                "你不是被动的普通聊天机器人，你拥有自主规划与执行工具链的能力：\n"
                "1. 当需要获取真题题干、阅读本地考研文件时，立即调用 read_exam_paper 或 read_file；\n"
                "2. 当需要为学员记录错题时，立即调用 log_mistake 工具写入错题本；\n"
                "3. 当需要高精度验算微积分、微分方程(ODE)、二次型正定性时，立即调用 verify_math 杜绝计算幻觉；\n"
                "4. 当学员表示卡壳毫无思路时，调用 socratic_hint 分级给微步骤提示，绝不剧透终极答案；\n"
                "5. 当任务需要多个步骤时，自主分步调用工具；但请保持高效：若无需外部"
                "信息即可完成（如生成计划、整理内容、直接作答），请直接给出结果，"
                "不要为了“确认”而反复浏览目录或搜索文件；\n"
                "6. 任务完成后，必须在最终回复中给出完整结果——不能为空、不能只说"
                "“已完成”或“文件已写入”。"
            ))

        else:
            sys_parts.append((
                _PRIORITY_CRITICAL,
                "\n=== 【工具使用与作答规范】 ===\n"
                "你是一个具备工具调用能力的 AI 助手，请高效完成任务：\n"
                "1. 优先直接作答：若任务不依赖外部信息（如生成计划、整理内容、"
                "编写报告），直接生成并输出完整结果，不要反复浏览目录或搜索文件；\n"
                "2. 需要外部信息时才调用工具，并在得到足够信息后立即收敛作答；\n"
                "3. 若题目要求产出文件（写入 output/ 等），必须用 write_file 工具"
                "真正写出文件；\n"
                "4. 任务完成后，必须在最终回复中给出完整结果——不能为空、不能只说"
                "“已完成”或“文件已写入”。"
            ))

        if (self.workspace_root / "AGENTS.md").exists():
            # 6. 学员报到与会话启动交互规范 (Onboarding & Daily Greeting Protocol)
            # （规范类：关键，不截断）
            sys_parts.append((
                _PRIORITY_CRITICAL,
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
            ))

        if (self.workspace_root / "AGENTS.md").exists():
            # 7. 学员作答与“交作业”批改规范（规范类：关键，不截断）
            sys_parts.append((
                _PRIORITY_CRITICAL,
                "\n=== 📝【学员作答与“交作业”批改规范】 ===\n"
                "当学员提交了题目答案、推导草稿或输入“交作业”时：\n"
                "1. 严格按照考研阅卷人标准分步骤批改：在推导每个关键步骤明确标注采分点（如 [+2分]、[-1分]）；\n"
                "2. 若有失误，坚决指出错因五分类（概念漏洞/审题偏差/公式记错/计算失误/书写丢分），并给出针对性改进处方；\n"
                "3. 若学员答错或部分失误，必须主动调用 log_mistake 工具，将本题题干、失误点、错因、正确解答记录到错题本，并纳入 FSRS 复测队列！"
            ))

        # 8. [W4/W5 作答契约] 引用规范与结构化产出规范 —— 无条件注入（教练版与
        # 通用版均适用）。评测实测：引用维度 0 分（引用链路断）、json_schema
        # 成建制失败（模型输出散文而非 JSON / 把要求的 array 写成 object）、
        # 产物文件缺失（模型未调用 write_file）——三者都能靠「把硬性要求写进
        # 系统提示」低成本修复。
        # [W7b 增补] 基于 v3.1.0-w4w6 轮实测：① 本地资料引用（页码/引文）此前
        # 无规范，PDF 类任务的引用维度全灭；② 产物落盘需前置到「完成前自检」
        # （RES-001 答案全对但没写 analysis.json → 10 分）；③ 输出精简：服务端
        # 有 60s 生成时限，超长输出（撞墙即整请求失败）应主动约束；④ 长 PDF
        # 续读提示（PDF-003 只读了前 7 页就作答）。
        # （规范类：关键，不截断）
        sys_parts.append((
            _PRIORITY_CRITICAL,
            "\n=== 📎【作答契约与引用规范（硬性要求）】 ===\n"
            "1. 【引用】当你的回答引用了检索结果、网页或工具获取的资料时，"
            "必须在该论断处或文末给出**完整 URL**（每条一行，形如 `来源：https://…`）；"
            "引用本地文件/PDF 时给出**文件名 + 页码**（形如 `来源：references/xx.pdf 第 7 页`）；"
            "未实际获取到来源的论断请如实标注「未核实」，严禁编造 URL；\n"
            "2. 【结构化输出】若任务要求输出 JSON / 清单 / 表格等结构化结果，"
            "直接输出**完整内容本体**：JSON 场景第一字符必须是 { 或 [，"
            "不要用 ``` 代码块包裹、不要只写“已生成”或“详见文件”；"
            "并**严格遵循任务给定的 schema**——要求数组就输出数组（不得改成对象/键值对），"
            "要求字段名就原样使用；\n"
            "3. 【产物落盘】若任务要求产出文件（如 report.json / analysis.json / schedule.json），"
            "必须调用 write_file 工具真正写入题目指定的相对路径（如 output/analysis.json）；"
            "**仅在回复文本中给出内容不算完成**——这是最易失分项，"
            "请在任务结束前（工具步数耗尽前）优先完成写入；\n"
            "4. 【收尾自检】结束前对照题目要求逐项自检：要求的文件是否已写入、"
            "要求的字段是否已给出、要求的引用是否已标注。若产物文件未写入，"
            "**不得结束任务**，应立即补写；\n"
            "5. 【输出精简】最终答案请保持紧凑（正文 ≤ 1200 字 / 结构化 JSON ≤ 80 行），"
            "服务端有生成时限，冗长输出可能被截断导致整次请求失败；\n"
            "6. 【长文档续读】read_file 对 PDF 的 offset 参数为起始页（1-based）、"
            "支持分次续读：长文档请按需多次调用读完全部相关页面，不要只凭前几页作答。\n"
            "7. 【联网检索】检索信息时**优先使用 web_search 工具**（多源联邦、"
            "返回带真实链接与来源标注的结果）；**不要手动拼接搜索引擎 URL 再用 "
            "fetch_url 抓取**——主流搜索引擎对直接抓取普遍反爬（验证码/无关页），"
            "既浪费步数又拿不到有效结果；本环境已对搜索引擎直抓做**拦截**，被拦后"
            "请直接改用 web_search，不要更换其他搜索引擎重试。需要打开具体网页"
            "验证时再用 fetch_url。若任务要求给出某文件的直链（PDF/附件/下载），"
            "请专门做一轮文件形态检索（如「<主题> pdf」「<主题> 文件下载」）；"
            "找到的链接即使来自非官方源也应给出并标注来源性质，确实无任何可得"
            "直链时才如实说明。\n"
            "8. 【数据可得性表述】若某项数据/官方信息确实未公开、未发布（如次年"
            "招生简章尚未发布），如实说明时请使用规范表述——「尚未公布」「未发布」"
            "「暂未公开」，不要用模糊说法（如「查不到」「没有找到」）；规范表述"
            "能让学员明确这是官方发布节奏，而非检索失败。\n"
            "9. 【JSON 语法自查】输出 JSON 前自查语法：括号/引号配对、逗号位置、"
            "**每个对象元素必须有键名与冒号**（不得混入无键名的裸字符串）；"
            "确认结构完整后再提交。"
        ))

        return self._assemble_system_prompt(sys_parts)

    def _assemble_system_prompt(self, parts: List[Tuple[int, str]]) -> str:
        """[P2 修复·2026-10-08 系统提示组装无预算/优先级] 按优先级组装并执行总长预算。

        预算内：与旧实现逐字节一致（``"\\n\\n".join``，本修复不改变常规输出）。
        超预算：把非关键段按「优先级低 → 高、同优先级长 → 短」的顺序截断 ——
        优先级 2（辅助数据）整段让位，优先级 1（重要数据）至少保头部
        ``_MIN_KEEP_CHARS`` 字符；被截/被省段落插入显式标记（说明完整内容可
        用 read_file 读取），优先级 0（规范类行为契约）永不截断。
        关键段本身超预算时允许整体超出（软上限：宁可超长，不可静默丢契约）。
        """
        total = sum(len(t) for _p, t in parts) + 2 * max(0, len(parts) - 1)
        if total <= SYSTEM_PROMPT_CHAR_BUDGET:
            return "\n\n".join(t for _p, t in parts)

        keep = [len(t) for _p, t in parts]
        order = sorted(
            (i for i, (prio, _t) in enumerate(parts) if prio > 0),
            key=lambda i: (-parts[i][0], -len(parts[i][1])))
        for i in order:
            if total <= SYSTEM_PROMPT_CHAR_BUDGET:
                break
            over = total - SYSTEM_PROMPT_CHAR_BUDGET
            prio, _text = parts[i]
            cur = keep[i]
            if prio >= _PRIORITY_AUXILIARY:
                target = 0
            else:
                target = max(_MIN_KEEP_CHARS, cur - over)
            target = min(target, cur)
            if target < cur:
                keep[i] = target
                total -= (cur - target)

        out: List[str] = []
        for i, (_prio, text) in enumerate(parts):
            k = keep[i]
            if k >= len(text):
                out.append(text)
            elif k <= 0:
                out.append("（本段因系统提示总长预算超限已省略）")
            else:
                out.append(text[:k] + "\n…（本段因系统提示总长预算超限已截断，"
                                       "完整内容可在工作区对应文件中读取）")
        return "\n\n".join(out)

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
                        focus: Optional[str] = None, force: bool = False) -> List[Dict[str, Any]]:
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

        [K8] ``force=True``：跳过 Token 水位判定（仅保留「消息数 > 6」这一
        可压缩前提），供 run 主循环在**上游报上下文超长**（overflow）时强制
        压缩后重试；默认 False 行为不变。
        """
        cur_tokens = self.estimate_tokens(messages)
        if len(messages) <= 6 or (not force and cur_tokens <= self.compact_watermark):
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

        # [审计 2026-10-10 A#10] 回溯归零（cut == 0）= 没有可压缩的历史：
        # keep_tail 已含全部消息，再插一条空摘要不会减少任何消息，反而多出
        # 一条无内容摘要、并让 loop 的 _log_compact_if_happened 误报「已压缩」。
        # 直接原样返回（与上方水位未达的早退同语义：压缩无意义时不动作）。
        if cut == 0:
            return messages

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

        # [K7-U5] 压缩完成后的扩展点：HookEvent.AFTER_COMPACT 此前已定义但
        # 无任何触发链（死事件）；现补上调用，注册方可在压缩结果上做校验/审计。
        if hook_manager:
            hook_manager.trigger_after_compact(
                compacted, {"active_subject": self.active_subject})

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

    def _annotate_syllabus(self, txt: str) -> str:
        """如实标注考试大纲状态：占位/骨架大纲必须显式告知 AI。

        [问题5 修复·专业课无大纲却谎称按纲出题] 自命题科目由系统生成的骨架
        大纲含 ``PRO_PLACEHOLDER_MARKER``。此前原样挂载，LLM 读后向学员宣称
        「已按考纲出题 / 已按大纲安排复习」——实际文件里只有【待自填】。
        现附加显式状态说明，把「按纲出题」的宣称条件收紧为真实大纲。
        """
        if _SYLLABUS_PLACEHOLDER_MARKER and _SYLLABUS_PLACEHOLDER_MARKER in txt:
            return (
                "⚠️【大纲状态：待填骨架·非真实考纲】本文件为系统生成的占位骨架，"
                "正文含【待自填】标记，尚未包含目标院校的真实考点。\n"
                "严禁向学员宣称「已按考纲出题 / 已按大纲复习 / 大纲已导入」；"
                "如需按纲出题，必须先提示学员从目标院校研究生院官网下载真实大纲，"
                "替换 04-专业课/考试大纲.md 后再执行。\n\n"
                + txt
            )
        return txt

    #: [P1 修复·2026-10-08 K5] 学情速览用的科目短名（目录/显示名与四端一致）
    _OVERVIEW_SUBJECTS = (
        ("math", "01-数学", "数学"),
        ("eng", "02-英语", "英语"),
        ("pol", "03-思想政治理论", "政治"),
        ("pro", "04-专业课", "专业课"),
    )

    def _build_study_overview(self) -> str:
        """[P1 修复·2026-10-08 K5 学情不进上下文] 生成「学情速览」段：完成率 / 到期复测 / 错因分布。

        数据源全部为现成单一真源：
          - 完成率 ← ``state.task_parser.parse_task_lines``（与四端看板同源）；
          - 到期复测 ← ``error_logger.get_due_reviews``（FSRS 推导的到期筛选）；
          - 错因分布 ← ``error_logger.scan_error_records``（错题本累计）。

        任一步失败只降级该行；无任何数据时返回空串（整段不输出）。学情统计
        属增强信息，绝不允许阻断系统提示词组装。
        """
        lines: List[str] = []

        # ① 今日任务完成率（四科；无任务文件/无任务的科目跳过）
        try:
            try:
                from state.task_parser import parse_task_lines, pct
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.state.task_parser import parse_task_lines, pct
            _rate_parts = []
            for _key, _folder, _label in self._OVERVIEW_SUBJECTS:
                f = self.workspace_root / _folder / "_状态" / "今日任务.md"
                if not f.exists():
                    continue
                items = parse_task_lines(self._read_safe(f))
                if not items:
                    continue
                _done = sum(1 for it in items if it.done)
                _rate_parts.append(f"{_label} {_done}/{len(items)}（{pct(_done, len(items))}%）")
            if _rate_parts:
                lines.append("今日任务完成率：" + " ｜ ".join(_rate_parts))
        except Exception:
            pass

        # ② 到期复测 + ③ 错因分布（error_logger 单一真源；导入失败则两行都跳过）
        try:
            try:
                from skills import error_logger
            except ImportError:  # pragma: no cover - 包式导入上下文
                from tools.skills import error_logger
        except Exception:  # pragma: no cover
            error_logger = None

        if error_logger is not None:
            _short = {k: lbl for k, _f, lbl in self._OVERVIEW_SUBJECTS}
            try:
                due_items = error_logger.get_due_reviews(None, max_count=999)
                if due_items:
                    _by_subj: Dict[str, int] = {}
                    for it in due_items:
                        k = str(it.get("subject") or "")
                        _by_subj[k] = _by_subj.get(k, 0) + 1
                    _detail = " ｜ ".join(
                        f"{_short.get(k, k)} {v}" for k, v in sorted(_by_subj.items()))
                    lines.append(f"到期复测错题：共 {len(due_items)} 道（{_detail}）")
            except Exception:
                pass
            try:
                _subj = self.active_subject if self.active_subject in _short else None
                records = error_logger.scan_error_records(_subj)
                _counts: Dict[str, int] = {}
                for r in records:
                    t = str(r.get("error_type") or "").strip() or "未知"
                    _counts[t] = _counts.get(t, 0) + 1
                if _counts:
                    _scope = _short.get(_subj, "全科") if _subj else "全科"
                    _top = sorted(_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
                    _detail = " · ".join(f"{t} {n}" for t, n in _top)
                    lines.append(
                        f"错因分布（{_scope}错题本累计 {sum(_counts.values())} 条）：{_detail}")
            except Exception:
                pass

        if not lines:
            return ""
        return ("\n=== 【学情速览 · 实时统计（回答学员学情/进度问题时以此为准）】 ===\n"
                + "\n".join(f"- {l}" for l in lines))


# 别名兼容
ContextCompactor = ContextEngine

