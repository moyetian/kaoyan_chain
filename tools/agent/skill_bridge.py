# -*- coding: utf-8 -*-
"""K5 技能桥接器（Skill Bridge）—— 把「只有 CLI/TUI/GUI 直调入口」的领域技能
转换为 Agent 模型可达的工具。

背景（已核实）：``tools/agent/tools_impl.py`` 的 24 个内置工具覆盖了文件/检索/
执行/基础考研能力，但 6 项领域能力此前只有各端直调入口、Agent 模型不可达：

  * ``exam_composer``   —— 整卷判分（经 ``grade_exam_paper`` 门面）
  * ``open_grader``     —— 开放题多模型判分
  * ``vision_solver``   —— 手写草稿/试卷图片批改
  * ``wechat_searcher`` —— 微信公众号文章检索
  * ``knowledge_map``   —— 考纲知识点图谱与掌握度映射
  * ``exam_diagnoser``  —— 整卷级失分聚类诊断

设计约束（K5 批次，逐条可核）：
1. **集中声明表**：6 个技能的桥接定义全部写在本文件（``build_skill_specs``），
   **不改 6 个技能模块本身** —— 桥接细节不分散到各技能里；
2. **懒加载 + 不崩**：技能模块在 handler 被调用时才导入；模块缺失（如公开副本
   未发布某些技能）时 handler 返回「Error: 未加载 xxx 技能」，注册表照常构建、
   工具照常出现在 schema 里（模型拿到可读错误而不是「未知工具」）；
3. **配置读取不另起炉灶**：LLM 配置统一走 ``llm_client.get_llm_config(工作区根)``，
   本文件不新增任何配置读取实现；
4. **路径过沙箱**：``solve_vision`` 的图片路径必须过
   ``registry._resolve_read_path``（工作区外读取授权闸门），与 read_file /
   read_exam_paper 同规则；``grade_exam_paper`` 的试卷**路径形态**参数同样过闸。
"""

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:  # 双导入路径兼容（tools.agent.* / agent.*）
    from .permissions import PermissionLevel
except ImportError:  # pragma: no cover - 脚本式直跑兼容
    from permissions import PermissionLevel  # type: ignore

try:
    from .kaoyan_context import SUPPORTED_SUBJECTS, normalize_subject_key
except ImportError:  # pragma: no cover
    from tools.agent.kaoyan_context import SUPPORTED_SUBJECTS, normalize_subject_key  # type: ignore

#: 技能桥接工具的档位：低频/带副作用能力，不常驻 essential schema（见 tools_impl）
SKILL_TOOL_TIER = "extended"
#: ToolDefinition.source 取值：技能桥接工具（与 builtin / mcp 并列，供审计）
SKILL_TOOL_SOURCE = "skill"

#: 「路径形态」判定阈值：与 exam_grading 的 ``"\n" not in s and len(s) < 260``
#: 同口径（同一字符串既可能是内联正文、也可能是文件路径时的判别线）。
_PATH_FORM_MAX_CHARS = 260


def load_skill_module(module_name: str):
    """按项目既有双导入约定加载技能模块；不可用返回 ``None``（绝不抛）。

    ``skills.*`` 优先（tools_impl 在导入期已把 ``<root>/tools`` 放进 sys.path，
    与该文件既有的 ``from skills import ...`` 主路径一致），失败再试
    ``tools.skills.*`` —— 顺序固定可避免同一个技能被两套包名各加载一份。
    """
    for pkg in ("skills", "tools.skills"):
        try:
            return importlib.import_module(f"{pkg}.{module_name}")
        except ImportError:
            continue
        except Exception:  # noqa: BLE001 - 技能模块自身初始化异常同样按"不可用"处理
            continue
    return None


@dataclass(frozen=True)
class SkillToolSpec:
    """一个「技能 → Agent 工具」的桥接声明（集中声明表的最小单元）。"""

    name: str
    skill_id: str
    desc: str
    params_schema: Dict[str, Any]
    #: int 或 Callable[[dict], int]（按调用参数动态定级），与 ToolDefinition.level 同语义
    level: Any
    tier: str = SKILL_TOOL_TIER
    handler: Optional[Callable[..., str]] = None
    #: 模型参数 → handler 关键字参数的适配器（模型侧参数名与底层签名不同时才需要）
    arg_adapter: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None


def _llm_config(workspace_root) -> Dict[str, Any]:
    """LLM 配置唯一真源：``llm_client.get_llm_config``（不新增配置读取实现）。"""
    try:
        from llm_client import get_llm_config
    except ImportError:  # pragma: no cover - 包式导入兼容
        from tools.llm_client import get_llm_config  # type: ignore
    try:
        return dict(get_llm_config(workspace_root) or {})
    except Exception:  # noqa: BLE001 - 配置读取失败不得让工具调用崩
        return {}


def _gate_optional_path(registry, raw, interactive: bool) -> str:
    """「可能是文件路径」的参数先过沙箱 + 外部读取授权闸门。

    仅当字符串**形态像路径**（单行、长度 < 260，与 exam_grading 的判定同口径）
    且该文件确实存在时校验；内联正文（含换行/超长）一律直接放行 —— 否则把
    「试卷正文全文」当路径解析会误伤主用法。

    返回空串 = 放行；否则返回应回给模型的错误文案（含 SecurityError 前缀）。
    """
    s = str(raw or "")
    if not s or "\n" in s or len(s) >= _PATH_FORM_MAX_CHARS:
        return ""
    try:
        if not Path(s).is_file():
            return ""
    except (OSError, ValueError):
        return ""
    try:
        registry._resolve_read_path(s, interactive)
    except Exception as e:  # noqa: BLE001 - 统一转可读文案，交 execute_tool 返回
        return f"SecurityError: {e}"
    return ""


def _read_answer_input(registry, raw, interactive: bool):
    """答案参数支持正文或文件路径；文件路径必须先过同一读取闸门。"""
    value = str(raw or "")
    if not value or "\n" in value or len(value) >= _PATH_FORM_MAX_CHARS:
        return "", value
    try:
        if not Path(value).is_file():
            return "", value
    except (OSError, ValueError):
        return "", value
    try:
        path = registry._resolve_read_path(value, interactive)
        return "", path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"SecurityError: {exc}", value


def build_skill_specs(registry) -> List[SkillToolSpec]:
    """构建 6 项技能工具声明（handler 绑定到给定注册表；每次返回全新清单）。"""

    def _missing(skill_id: str) -> str:
        return f"Error: 未加载 {skill_id} 技能"

    # ── 1. 整卷判分（exam_composer.grade_exam_paper 门面） ──────────────
    def _grade_exam_paper(paper: str, answers: str, subject: str = "math",
                          auto_advance: bool = True, interactive: bool = True) -> str:
        mod = load_skill_module("exam_composer")
        if mod is None:
            return _missing("exam_composer")
        gate_err = _gate_optional_path(registry, paper, interactive)
        if gate_err:
            return gate_err
        answer_err, answer_text = _read_answer_input(registry, answers, interactive)
        if answer_err:
            return answer_err
        try:
            res = mod.grade_exam_paper(paper, answer_text, subject=str(subject or "math"),
                                       auto_advance=bool(auto_advance))
        except Exception as e:  # noqa: BLE001 - 工具失败必须转可读文案
            return f"Error 整卷判分失败: {e}"
        if not isinstance(res, dict):
            return f"Error 整卷判分返回异常: {type(res).__name__}"
        if res.get("report"):
            return str(res["report"])
        if res.get("success"):
            return (f"【自测整卷批改】得分 {res.get('score')} / {res.get('total_score')}"
                    f"（正答率 {res.get('accuracy')}%）")
        return (f"Error 整卷判分失败: "
                f"{res.get('msg') or res.get('message') or '未识别到有效作答'}")

    # ── 2. 开放题多模型判分（open_grader） ────────────────────────────
    def _grade_open_question(question: str, student_answer: str, subject: str = "math",
                             reference_answer: str = "") -> str:
        mod = load_skill_module("open_grader")
        if mod is None:
            return _missing("open_grader")
        try:
            # config 不传：open_grader 内部按既有约定读 ky_config.json 的
            # exam_grading 段（与 exam_composer._grade_open_by_llm 同一用法）。
            res = mod.grade_open_question(
                question=str(question or ""), student_answer=str(student_answer or ""),
                subject=str(subject or "math"),
                reference_answer=str(reference_answer or ""))
        except Exception as e:  # noqa: BLE001
            return f"Error 开放题判分失败: {e}"
        level_zh = {2: "通过", 1: "转人工复核", 0: "不通过"}.get(
            getattr(res, "match_level", 1), "未知")
        lines = [
            f"【开放题判分】结论: {level_zh}（match_level={getattr(res, 'match_level', '?')}）",
            (f"得分: {getattr(res, 'score', 0)} / 10 ｜ 置信度: "
             f"{getattr(res, 'confidence', 0)} ｜ 错因: {getattr(res, 'mistake_type', '无')}"),
            f"判定依据: {getattr(res, 'reason', '') or '（无）'}",
        ]
        if getattr(res, "degraded", False):
            lines.append("⚠ 本次判分发生降级（"
                         f"{getattr(res, 'error', '') or '模型弃权/未仲裁'}），"
                         "结论仅供参考，建议人工复核。")
        return "\n".join(lines)

    # ── 3. 视觉看图批改（vision_solver） ─────────────────────────────
    def _solve_vision(image_path: str, prompt: str = "", interactive: bool = True) -> str:
        mod = load_skill_module("vision_solver")
        if mod is None:
            return _missing("vision_solver")
        # [K5 安全] 图片路径过沙箱 + 工作区外读取授权闸门（与 read_file 同规则）；
        # 越界时抛 SecurityException，由 execute_tool 统一转为 SecurityError 文案。
        p = registry._resolve_read_path(image_path, interactive)
        if not p.exists() or not p.is_file():
            return f"Error: 图片文件不存在 [{p}]"
        cfg = _llm_config(registry.sandbox.workspace_root)
        try:
            reply = mod.solve_image_with_model(str(p), str(prompt or ""), cfg, stream=False)
        except Exception as e:  # noqa: BLE001
            return f"Error 图片批改失败: {e}"
        return str(reply or "（视觉引擎未返回内容）")

    # ── 4. 微信公众号文章检索（wechat_searcher） ──────────────────────
    def _search_wechat(keyword: str, max_results: int = 5, fetch_content: bool = False,
                       save_to_local: bool = False) -> str:
        mod = load_skill_module("wechat_searcher")
        if mod is None:
            return _missing("wechat_searcher")
        try:
            n = max(1, min(int(max_results or 5), 20))
        except (TypeError, ValueError):
            n = 5
        try:
            res = mod.wechat_search(keyword=str(keyword or ""), max_results=n,
                                    fetch_content=bool(fetch_content),
                                    save_to_local=bool(save_to_local))
        except Exception as e:  # noqa: BLE001
            return f"Error 微信文章检索失败: {e}"
        if not isinstance(res, dict):
            return f"Error 微信文章检索返回异常: {type(res).__name__}"
        lines = [f"【微信公众号检索 · 「{res.get('keyword', keyword)}」】"
                 f"共发现 {res.get('total', 0)} 篇（已抓取正文 {res.get('fetched', 0)} 篇）"]
        status = res.get("source_status") or []
        if status:
            lines.append("检索源: " + " | ".join(str(s) for s in status))
        items = res.get("results") or []
        for idx, it in enumerate(items, 1):
            if not isinstance(it, dict):
                continue
            lines.append(f"{idx}. {it.get('title') or '（无标题）'}")
            meta = [str(x) for x in (it.get("account_display") or it.get("source_account"),
                                     it.get("date_display") or it.get("publish_date")) if x]
            if meta:
                lines.append("   " + " | ".join(meta))
            if it.get("url"):
                lines.append(f"   链接: {it['url']}")
            if it.get("summary"):
                lines.append(f"   摘要: {str(it['summary'])[:120]}")
        if not items:
            lines.append("未检索到相关文章：可更换关键词，或改用其他检索源（sogou/bing/local）重试。")
            for err in (res.get("source_errors") or [])[:5]:
                lines.append(f"[!] 检索源告警: {err}")
        if res.get("saved_paths"):
            lines.append(f"已沉淀 {len(res['saved_paths'])} 篇至 .memory/experiences/（本地隐私目录）")
        return "\n".join(lines)

    # ── 5. 考纲知识点图谱（knowledge_map） ───────────────────────────
    def _map_knowledge(subject: str = "math") -> str:
        mod = load_skill_module("knowledge_map")
        if mod is None:
            return _missing("knowledge_map")
        subject_key = normalize_subject_key(subject, default="")
        if subject_key not in SUPPORTED_SUBJECTS:
            return (f"Error: 未知科目 [{subject}]。支持的科目键: "
                    + ", ".join(sorted(SUPPORTED_SUBJECTS)))
        try:
            # 渲染走模块既有 formatter（与 CLI `ky map` / REPL `/map` 同源文案；
            # 其内部即调用 build_knowledge_map）。
            return str(mod.format_knowledge_map_table(subject_key))
        except Exception as e:  # noqa: BLE001
            return f"Error 知识图谱生成失败: {e}"

    # ── 6. 整卷级失分诊断（exam_diagnoser） ──────────────────────────
    def _diagnose_exam(exam_input: str, subject: str = "math") -> str:
        mod = load_skill_module("exam_diagnoser")
        if mod is None:
            return _missing("exam_diagnoser")
        subject_key = normalize_subject_key(subject, default="")
        if subject_key not in SUPPORTED_SUBJECTS:
            return (f"Error: 未知科目 [{subject}]。支持的科目键: "
                    + ", ".join(sorted(SUPPORTED_SUBJECTS)))
        try:
            res = mod.diagnose_mock_exam(subject=subject_key,
                                         exam_input=str(exam_input or ""))
            return str(mod.format_diagnosis_report(res))
        except Exception as e:  # noqa: BLE001
            return f"Error 整卷诊断失败: {e}"

    return [
        SkillToolSpec(
            name="grade_exam_paper",
            skill_id="exam_composer",
            desc=("整卷判分（自测卷/模考卷）：按答案密钥逐题比对，给出总分与逐题采分点"
                  "报告，并可自动推进错题 FSRS 复测周期。paper 传试卷文件路径或试卷"
                  "正文全文；answers 传学员作答文本（如 '1. A 2. C 3. 过程略'）或作答"
                  "文件路径。auto_advance=false 时只判分、不改动错题状态（只读）。"),
            params_schema={
                "type": "object",
                "properties": {
                    "paper": {"type": "string", "description": "试卷文件路径或试卷正文全文"},
                    "answers": {"type": "string", "description": "学员作答文本或作答文件路径"},
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro（默认 math）"},
                    "auto_advance": {"type": "boolean",
                                     "description": "是否自动推进错题 FSRS 复测周期（默认 true；false 则只判分不写状态）"},
                },
                "required": ["paper", "answers"],
            },
            # [K5 动态定级] auto_advance=True 会写错题本/FSRS 状态（SAFE_EDIT）；
            # 显式 false 是纯只读判分，Level 0（safe 模式也允许）。
            level=lambda a: (PermissionLevel.READ_ONLY
                             if a.get("auto_advance") is False
                             else PermissionLevel.SAFE_EDIT),
            handler=_grade_exam_paper,
        ),
        SkillToolSpec(
            name="grade_open_question",
            skill_id="open_grader",
            desc=("开放题（论述/推导）多模型判分：要点抽取 → 多模型并行初评 → 分歧仲裁，"
                  "输出 通过/转人工复核/不通过 与逐点判定依据；未启用或异常时如实回落"
                  "人工复核，绝不臆造分数。"),
            params_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "开放题题面文本"},
                    "student_answer": {"type": "string", "description": "学员作答文本"},
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro（默认 math）"},
                    "reference_answer": {"type": "string", "description": "参考答案（可选，缺省由模型推导评分要点）"},
                },
                "required": ["question", "student_answer"],
            },
            level=PermissionLevel.NETWORK,
            handler=_grade_open_question,
        ),
        SkillToolSpec(
            name="solve_vision",
            skill_id="vision_solver",
            desc=("调用多模态视觉引擎批改试卷/手写草稿照片：识别题面与手写步骤，按考研"
                  "阅卷标准逐行给分、归因错因五分类。image_path 须为工作区内图片路径"
                  "（或已授权的工作区外路径）。"),
            params_schema={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "图片文件路径（工作区内相对路径或已授权的外部路径）"},
                    "prompt": {"type": "string", "description": "可选：学员补充疑问或批改要求"},
                },
                "required": ["image_path"],
            },
            level=PermissionLevel.NETWORK,
            handler=_solve_vision,
        ),
        SkillToolSpec(
            name="search_wechat",
            skill_id="wechat_searcher",
            desc=("检索微信公众号考研文章（经验贴/院校解读/考点精讲），返回标题、公众号、"
                  "日期与真实链接；可选择性抓取正文摘要或沉淀到本地经验库（.memory）。"),
            params_schema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "检索关键词（如 '408 计算机考研经验'、'华科计算机复试'）"},
                    "max_results": {"type": "integer", "description": "最大结果数（默认 5，上限 20）"},
                    "fetch_content": {"type": "boolean", "description": "是否抓取文章正文摘要（默认 false，更快）"},
                    "save_to_local": {"type": "boolean", "description": "是否沉淀文章到 .memory/experiences/（默认 false）"},
                },
                "required": ["keyword"],
            },
            level=PermissionLevel.NETWORK,
            handler=_search_wechat,
        ),
        SkillToolSpec(
            name="map_knowledge",
            skill_id="knowledge_map",
            desc=("生成指定科目的考纲知识点图谱与掌握度大盘（考点覆盖、A/B/C/D 等级分布、"
                  "失分风险），用于确定攻坚优先级与查漏补缺。"),
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro（默认 math）"},
                },
            },
            level=PermissionLevel.READ_ONLY,
            handler=_map_knowledge,
        ),
        SkillToolSpec(
            name="diagnose_exam",
            skill_id="exam_diagnoser",
            desc=("整卷级模考诊断：对多题作答/批改报告做章节失分聚类与错因五分类分布统计，"
                  "输出下周复习精力重分配建议。exam_input 传批改报告或"
                  "「章节名/题号 + 错因」记录文本。"),
            params_schema={
                "type": "object",
                "properties": {
                    "exam_input": {"type": "string", "description": "模考答卷文本/批改报告/错题摘要（含题号与错因）"},
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro（默认 math）"},
                },
                "required": ["exam_input"],
            },
            # 已核实 exam_diagnoser 为纯本地聚合（读配置只为科目名，不写盘、不联网）
            level=PermissionLevel.READ_ONLY,
            handler=_diagnose_exam,
        ),
    ]


def register_skill_tools(registry) -> List[str]:
    """把技能桥接工具注册进 ``ToolRegistry``（幂等：已存在的名字跳过）。

    返回本次**新增**的工具名清单（重复调用返回空列表）。技能模块是否可用不影响
    注册：缺失时 handler 返回「Error: 未加载 xxx 技能」，注册表始终可审计。
    """
    added: List[str] = []
    for spec in build_skill_specs(registry):
        if spec.name in registry.tools:
            continue
        handler = spec.handler
        if handler is None:  # pragma: no cover - 声明表写错时跳过而非崩
            continue
        if spec.arg_adapter is not None:
            adapter = spec.arg_adapter
            handler = (lambda _h=handler, _a=adapter, **kwargs: _h(**_a(kwargs)))
        registry.register(
            name=spec.name,
            desc=spec.desc,
            params_schema=spec.params_schema,
            level=spec.level,
            tier=spec.tier,
            source=SKILL_TOOL_SOURCE,
        )(handler)
        added.append(spec.name)
    return added
