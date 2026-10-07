# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 上下文压缩摘要引擎 (Structured Compaction Summaries)

本模块是「压缩时保留什么、怎么渲染」的**唯一实现处**（B1 批次），
供 ``agent.context_engine.ContextEngine.compact_context`` 调用。

[B1 修复·早期约束被静默丢弃] 旧实现把被压缩的历史逐条压成
``学员此前曾提问: {content[:100]}`` 之类的行，且**只保留最后 10 行** ——
早期出现的考纲约束（如「矩阵题不考秩的证明」）、错因与复习计划会随行数上限
被整段丢弃，学员与私教都不知情。

现改为**结构化摘要**：
1. ``goal`` / ``progress`` / ``file_ops`` / ``pending`` 做常规抽取；
2. ``key_info`` 是核心保护对象：考纲约束、错因、待复习、来源与引文四类**整条保留**
   （单条上限 400 字符，而非 100），并有独立的分区与渲染上限；
3. 摘要渲染本身有界（每分区最多 12 条，省略时写明条数），不会反过来撑爆上下文。

两种摘要模式（``agent.compact_mode``，见 :func:`resolve_compact_mode`）：
* ``rule_only``（默认）：纯规则抽取，零网络、零依赖、确定性；
* ``llm``：调用大模型产出结构化 JSON；**任何失败都返回 None**，由调用方降级
  回 ``rule_only`` —— 配置了 llm 不等于每次压缩都必须成功。
"""

import json
import re
from typing import Any, Callable, Dict, List, Optional

# ── 常量 ────────────────────────────────────────────────────────────────

#: 压缩摘要消息的固定头部（``render_summary`` 渲染时以它开头）。
#: [B3b] 从 ``loop.py`` 的私有副本抽取为公开常量：loop 用它识别「本轮是否真的
#: 发生了压缩」，两处字面量从此只有一个事实源。
COMPACT_SUMMARY_PREFIX = "【历史上下文压缩摘要"

#: 结构化摘要的五个分区（LLM 模式返回的 JSON 必须同时含这五个键）。
SUMMARY_KEYS = ("goal", "progress", "key_info", "file_ops", "pending")

#: 压缩模式：规则摘要 / 大模型摘要。非法值一律回落 ``rule_only``。
COMPACT_MODES = ("rule_only", "llm")

# [R3 波动收敛] 稳定性优先链路的采样温度（关采样）。延迟导入 + 兜底值，
# 避免 agent 包在导入期就牵出 llm_client（与 _default_llm_fn 的延迟导入
# 理由一致：本模块须能被单独导入而不加载网络栈）。
try:  # pragma: no cover - 双导入路径
    from llm_client import STABLE_TEMPERATURE
except ImportError:  # pragma: no cover
    try:
        from tools.llm_client import STABLE_TEMPERATURE  # type: ignore
    except ImportError:
        STABLE_TEMPERATURE = 0.0
DEFAULT_COMPACT_MODE = "rule_only"

#: key_info 的保护类别。前三类为考研教学语义；[W4] 新增 ``citations``：
#: 来源 URL 与引文行必须存活到压缩之后 —— 评测实测压缩会把网页正文与 URL
#: 整段丢弃，模型只能凭记忆作答（引用 0 分 + 幻觉的直接机制之一）。
KEY_INFO_CATEGORIES = ("syllabus", "mistakes", "review", "citations")
KEY_INFO_LABELS = {"syllabus": "考纲约束", "mistakes": "错因", "review": "待复习",
                   "citations": "来源与引文"}

#: citations 类别的抽取条数上限（web_search 一次可返回 10+ URL，避免 URL
#: 洪水把摘要块撑爆；其余类别无此限制，由渲染上限统一收口）。
CITATION_MAX_ITEMS = 8

#: 渲染时每个分区最多保留的条目数；超出部分以「（另有 N 条已省略）」收尾。
MAX_SECTION_ITEMS = 12

#: key_info 单条上限：**不得**退回 100 字符 —— 那正是早期约束被截断的根因。
KEY_INFO_ITEM_MAX_CHARS = 400

#: goal / progress / pending 单条上限。
ITEM_MAX_CHARS = 300

#: 各类抽取条数上限（摘要字典本身也要有界）。
GOAL_MAX_ITEMS = 3
PROGRESS_MAX_ITEMS = 3
PENDING_MAX_ITEMS = 3
FILE_OPS_MAX_ITEMS = 12

#: 三类关键信息的命中模式（按行匹配）。「约束」的写法刻意宽容：
#: 「约束-甲：…」「约束：…」「约束 3：…」都是学员/私教记录考纲边界的常见写法，
#: 保护语义下宁可多保留一行，也不能漏掉一条真约束。
_SYLLABUS_PATTERN = re.compile(
    r"考纲|大纲|不考|必考|考察范围|重点考|考点要求|超纲|禁区"
    r"|约束(?:\s*[-—–－]?\s*[甲乙丙丁戊己庚辛壬癸0-9]+)?\s*[:：]?"
)
_MISTAKE_PATTERN = re.compile(r"错因|错误原因|易错|失误|卡点|踩坑")
_REVIEW_PATTERN = re.compile(r"待复习|需复习|复习计划|复盘|背诵清单|再练")
#: [W4] 来源与引文保护：URL 行、出处/来源标注、引文标注整行保留。
#: 宁可多保留一行，也不让「模型引用过的 URL」在压缩中蒸发。
_CITATION_PATTERN = re.compile(
    r"https?://"
    r"|来源\s*[:：]|出处\s*[:：]|引自|原文\s*[:：]"
    r"|据[^，。；：]{0,16}(?:报道|公告|通知|文件|官网|简章)"
)

#: tool 消息 content 里的文件路径（抽不到就只记工具名）。
_FILE_PATH_PATTERN = re.compile(
    r"[^\s，。；：、\"'（）()\[\]【】]+\.(?:md|py|json|txt|pdf|docx|csv|ya?ml|toml)",
    re.IGNORECASE,
)

#: LLM 提示词：把历史压缩成结构化 JSON。约束/错因/复习计划/来源引文要求原文保留。
_LLM_PROMPT_TEMPLATE = """你是考研私教系统的上下文压缩器。请把下面的历史对话压缩成结构化 JSON 摘要。

要求：
1. 只输出一个合法 JSON 对象，不要输出任何解释或 Markdown 代码围栏；
2. 必须包含五个键：goal / progress / key_info / file_ops / pending；
3. key_info 是对象，必须包含 syllabus（考纲约束）/ mistakes（错因）/ review（待复习）/ citations（来源与引文）四个数组；
4. 考纲约束、错因、复习计划、来源 URL 与引文必须逐条**原文保留**，不得改写、不得省略；
5. 每个数组元素是字符串，尽量精炼。

JSON 结构示例：
{"goal": ["学员目标"], "progress": ["已讲解内容"], "key_info": {"syllabus": ["考纲约束原文"], "mistakes": ["错因原文"], "review": ["待复习项"], "citations": ["来源：https://…"]}, "file_ops": ["read_file → 路径"], "pending": ["未被回应的诉求"]}
"""

#: LLM 提示词里历史正文的总字符上限与单条上限（压缩的输入也不该无限膨胀）。
_LLM_HISTORY_CHAR_BUDGET = 20_000
_LLM_MSG_CHAR_LIMIT = 600

#: 降级提示的仓库统一配色（黄色）。
_YELLOW = "\033[93m"
_RESET = "\033[0m"


# ── 配置解析 ────────────────────────────────────────────────────────────


def resolve_compact_mode(config: Optional[Dict[str, Any]]) -> str:
    """解析压缩模式：``agent.compact_mode``，合法值仅 ``rule_only`` / ``llm``。

    非法/缺失/类型不对一律回落 ``rule_only``（fail-safe，与
    ``approval.resolve_headless_policy`` 同风格）：摘要模式写错时的失败方向
    只能是「退回纯规则摘要」，绝不能变成"没有摘要"或"意外走网络"。
    """
    cfg = config if isinstance(config, dict) else {}
    agent_cfg = cfg.get("agent")
    raw = agent_cfg.get("compact_mode") if isinstance(agent_cfg, dict) else None
    if isinstance(raw, str) and raw.strip().lower() in COMPACT_MODES:
        return raw.strip().lower()
    return DEFAULT_COMPACT_MODE


# ── 基础工具 ────────────────────────────────────────────────────────────


def _content_of(msg: Any) -> str:
    content = msg.get("content") if isinstance(msg, dict) else None
    return content if isinstance(content, str) else ""


def _clip(text: str, limit: int) -> str:
    """折叠空白后截断到 ``limit`` 字符（超长时补省略号）。

    折叠空白是为了让多行/缩进内容不会把摘要块撑散；截断上限由调用方给出，
    key_info 走 400 而非 100。
    """
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def _pick_with_focus(messages: List[Dict[str, Any]], focus: Optional[str],
                     limit: int) -> List[Dict[str, Any]]:
    """按「focus 命中优先、其余按时间先后」挑选消息（确定性，最终仍按时间序）。

    优先级只决定**选哪几条**，不改变显示顺序 —— 摘要按时间读才顺。
    """
    selected: List[int] = []
    if focus:
        key = str(focus)
        for idx, msg in enumerate(messages):
            if len(selected) >= limit:
                break
            if key in _content_of(msg):
                selected.append(idx)
    for idx in range(len(messages)):
        if len(selected) >= limit:
            break
        if idx not in selected:
            selected.append(idx)
    selected.sort()
    return [messages[i] for i in selected]


# ── 结构化抽取（rule_only） ─────────────────────────────────────────────


def extract_key_info(messages: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """抽取**不可丢失**的关键信息：考纲约束 / 错因 / 待复习 / 来源与引文。

    这是本模块存在的核心理由。按行扫描全部消息（不区分 role —— 约束既可能
    由学员提出，也可能由私教复述），命中即整行保留；单条上限 400 字符，
    **不做 100 字符截断**，保证完整约束句能进摘要。[W4] ``citations``
    类别保护 URL 与出处行（上限 :data:`CITATION_MAX_ITEMS` 条）。
    """
    buckets: Dict[str, List[str]] = {cat: [] for cat in KEY_INFO_CATEGORIES}
    seen: Dict[str, set] = {cat: set() for cat in KEY_INFO_CATEGORIES}
    matchers = (
        ("syllabus", _SYLLABUS_PATTERN),
        ("mistakes", _MISTAKE_PATTERN),
        ("review", _REVIEW_PATTERN),
        ("citations", _CITATION_PATTERN),
    )
    for msg in messages or []:
        text = _content_of(msg)
        if not text.strip():
            continue
        for raw_line in text.splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue
            for cat, pattern in matchers:
                if not pattern.search(line):
                    continue
                item = _clip(line, KEY_INFO_ITEM_MAX_CHARS)
                if item not in seen[cat]:
                    seen[cat].add(item)
                    buckets[cat].append(item)
    # [W4] citations 桶单独设上限：web_search 一次可返回 10+ URL，
    # 不加限会把摘要块撑成 URL 清单；其余类别由渲染上限统一收口。
    if len(buckets["citations"]) > CITATION_MAX_ITEMS:
        buckets["citations"] = buckets["citations"][:CITATION_MAX_ITEMS]
    return buckets


def _extract_goal(messages: List[Dict[str, Any]], focus: Optional[str]) -> List[str]:
    """学员目标：最早的若干条 user 消息（focus 命中的优先入选）。"""
    users = [m for m in messages if m.get("role") == "user" and _content_of(m).strip()]
    return [_clip(_content_of(m), ITEM_MAX_CHARS)
            for m in _pick_with_focus(users, focus, GOAL_MAX_ITEMS)]


def _extract_progress(messages: List[Dict[str, Any]], focus: Optional[str]) -> List[str]:
    """学习进展：最早的若干条有正文的 assistant 消息（focus 命中的优先入选）。"""
    replies = [m for m in messages
               if m.get("role") == "assistant" and _content_of(m).strip()]
    return [_clip(_content_of(m), ITEM_MAX_CHARS)
            for m in _pick_with_focus(replies, focus, PROGRESS_MAX_ITEMS)]


def _extract_file_ops(messages: List[Dict[str, Any]]) -> List[str]:
    """工具与文件操作：``工具名 → 目标文件路径``（抽不到路径就只记工具名）。"""
    ops: List[str] = []
    seen = set()
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        tool_name = str(msg.get("name") or "tool")
        match = _FILE_PATH_PATTERN.search(_content_of(msg))
        path = _clip(match.group(0), 120) if match else ""
        item = f"{tool_name} → {path}" if path else tool_name
        if item not in seen:
            seen.add(item)
            ops.append(item)
        if len(ops) >= FILE_OPS_MAX_ITEMS:
            break
    return ops


def _extract_pending(messages: List[Dict[str, Any]]) -> List[str]:
    """待处理诉求：从末尾往前收集「尚未被回应」的连续 user 消息。

    遇到 assistant / tool 即说明已有回应，立即停止 —— 只取真正悬空的那几条。
    """
    pending: List[str] = []
    for msg in reversed(messages or []):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("assistant", "tool"):
            break
        if role == "user" and _content_of(msg).strip():
            pending.append(_clip(_content_of(msg), ITEM_MAX_CHARS))
    pending.reverse()
    return pending[:PENDING_MAX_ITEMS]


def build_structured_summary(messages: List[Dict[str, Any]],
                             *, focus: Optional[str] = None) -> Dict[str, Any]:
    """rule_only 模式的结构化抽取：纯规则、确定性、零网络。

    ``focus`` 命中的消息在 goal / progress 抽取时优先保留；key_info 的保护
    语义与 focus 无关（约束不因"没提到"就可以丢）。
    """
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    return {
        "goal": _extract_goal(msgs, focus),
        "progress": _extract_progress(msgs, focus),
        "key_info": extract_key_info(msgs),
        "file_ops": _extract_file_ops(msgs),
        "pending": _extract_pending(msgs),
    }


# ── 渲染 ────────────────────────────────────────────────────────────────


def _render_section(lines: List[str], title: str, items: List[str]) -> None:
    """渲染一个分区：空分区不渲染标题；超上限时写明省略条数。"""
    items = [str(it) for it in (items or []) if str(it).strip()]
    if not items:
        return
    lines.append(title)
    for item in items[:MAX_SECTION_ITEMS]:
        lines.append(f"- {item}")
    if len(items) > MAX_SECTION_ITEMS:
        lines.append(f"（另有 {len(items) - MAX_SECTION_ITEMS} 条已省略）")


def render_summary(summary: Dict[str, Any]) -> str:
    """把摘要 dict 渲染成中文摘要块（有界：每分区最多 12 条）。

    头部**必须**保留字面量 ``Context Compaction``：既有回归测试
    （``tools/test_ky_suite.py`` / ``tests/test_tokenizer_budget.py``）据此
    判定"压缩确实发生了"。
    """
    if not isinstance(summary, dict):
        summary = {}
    lines: List[str] = [COMPACT_SUMMARY_PREFIX + " (Context Compaction)】:"]

    _render_section(lines, "[学员目标]", list(summary.get("goal") or []))
    _render_section(lines, "[学习进展]", list(summary.get("progress") or []))

    key_info = summary.get("key_info")
    key_info = key_info if isinstance(key_info, dict) else {}
    key_items: List[str] = []
    for cat in KEY_INFO_CATEGORIES:
        if cat == "citations":
            continue
        for item in (key_info.get(cat) or []):
            if str(item).strip():
                key_items.append(f"[{KEY_INFO_LABELS[cat]}] {item}")
    _render_section(lines, "[关键信息（考纲约束 / 错因 / 待复习）]", key_items)
    # [W4] 来源与引文单独一段：URL/引文不与其他关键信息共享 12 条上限，
    # 保证压缩后模型仍能引用真实来源（引用维度得分与抗幻觉的前置）。
    _render_section(lines, "[来源与引文（可引用）]", list(key_info.get("citations") or []))

    _render_section(lines, "[工具与文件操作]", list(summary.get("file_ops") or []))
    _render_section(lines, "[待处理诉求]", list(summary.get("pending") or []))

    lines.append("（早期工具执行细节已自动精简以节省上下文）")
    return "\n".join(lines)


# ── 结构校验 ────────────────────────────────────────────────────────────


_VALID_ROLES = ("system", "user", "assistant", "tool")


def validate_compacted(messages: List[Dict[str, Any]]) -> List[str]:
    """校验压缩后的消息结构，返回问题列表（空列表 = 合法）。

    至少覆盖三类会直接导致上游 400 的缺陷：
    1. role 非法（上游只认 system/user/assistant/tool）；
    2. tool 消息是孤儿 —— 前面没有发起它的 ``assistant(tool_calls)``；
    3. ``assistant(tool_calls)`` 之后缺少成对的 tool 结果消息。
    """
    problems: List[str] = []
    msgs = list(messages or [])
    total = len(msgs)
    for idx, msg in enumerate(msgs):
        if not isinstance(msg, dict):
            problems.append(f"第 {idx} 条消息不是 dict（{type(msg).__name__}）")
            continue
        role = msg.get("role")
        if role not in _VALID_ROLES:
            problems.append(f"第 {idx} 条消息 role 非法：{role!r}")
            continue

        if role == "tool":
            prev = msgs[idx - 1] if idx > 0 else None
            if not isinstance(prev, dict) or prev.get("role") not in ("assistant", "tool"):
                problems.append(f"第 {idx} 条 tool 消息为孤儿：前一条不是 assistant(tool_calls)/tool")
            elif prev.get("role") == "assistant" and not prev.get("tool_calls"):
                problems.append(f"第 {idx} 条 tool 消息为孤儿：前一条 assistant 未发起工具调用")
            else:
                call_id = msg.get("tool_call_id")
                if call_id:
                    owner = prev
                    if owner.get("role") == "tool":
                        j = idx - 1
                        while j >= 0 and isinstance(msgs[j], dict) and msgs[j].get("role") == "tool":
                            j -= 1
                        owner = msgs[j] if j >= 0 else None
                    ids = set()
                    if isinstance(owner, dict):
                        for tc in (owner.get("tool_calls") or []):
                            if isinstance(tc, dict) and tc.get("id"):
                                ids.add(tc["id"])
                    if ids and call_id not in ids:
                        problems.append(
                            f"第 {idx} 条 tool 的 tool_call_id={call_id!r} 不在发起 assistant 的 tool_calls 中"
                        )

        elif role == "assistant" and msg.get("tool_calls"):
            nxt = msgs[idx + 1] if idx + 1 < total else None
            if not isinstance(nxt, dict) or nxt.get("role") != "tool":
                problems.append(f"第 {idx} 条 assistant 发起 tool_calls 但缺少紧随的 tool 结果消息")
    return problems


# ── LLM 摘要（可选模式） ─────────────────────────────────────────────────


def _default_llm_fn() -> Optional[Callable[..., Optional[str]]]:
    """默认 LLM 入口：延迟导入 ``chat_completion``。

    延迟导入避免 agent 包在导入期就牵出网络栈；双路径都试一次，
    与仓库其它模块（``skills/exam_composer.py``）的兜底顺序一致。
    """
    try:
        try:
            from tools.llm_client import chat_completion
        except ImportError:
            from llm_client import chat_completion
        return chat_completion
    except Exception:
        return None


def _slice_first_object(text: str) -> str:
    """从混杂文本里切出第一个 ``{...}`` 片段（模型偶尔会加前后缀说明）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return ""
    return text[start:end + 1]


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """解析模型返回的 JSON 对象；容忍 ```json 围栏与前后缀说明。"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    for candidate in (text, _slice_first_object(text)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


def _normalize_summary(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把模型返回的 JSON 规范成五分区结构；key_info 非对象时视为非法。"""

    def _str_list(value: Any) -> List[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        out: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(_clip(item, KEY_INFO_ITEM_MAX_CHARS))
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                out.append(str(item))
        return out

    key_info_raw = data.get("key_info")
    if not isinstance(key_info_raw, dict):
        return None
    key_info = {cat: _str_list(key_info_raw.get(cat, [])) for cat in KEY_INFO_CATEGORIES}
    return {
        "goal": _str_list(data.get("goal")),
        "progress": _str_list(data.get("progress")),
        "key_info": key_info,
        "file_ops": _str_list(data.get("file_ops")),
        "pending": _str_list(data.get("pending")),
    }


def _build_llm_prompt(messages: List[Dict[str, Any]], focus: Optional[str]) -> str:
    """拼装 LLM 压缩提示词（历史正文有总量上限，避免把压缩输入撑爆）。"""
    parts = [_LLM_PROMPT_TEMPLATE]
    if focus:
        parts.append(f"\n本次压缩的关注点（相关消息优先保留）：{focus}")
    parts.append("\n【待压缩的历史消息】")
    used = 0
    rendered = 0
    total = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        content = _content_of(msg)
        if not content.strip():
            continue
        total += 1
        line = f"[{msg.get('role')}] {_clip(content, _LLM_MSG_CHAR_LIMIT)}"
        if used + len(line) > _LLM_HISTORY_CHAR_BUDGET:
            break
        parts.append(line)
        used += len(line)
        rendered += 1
    if total > rendered:
        parts.append(f"（另有 {total - rendered} 条消息因长度上限未纳入）")
    return "\n".join(parts)


def llm_summarize(messages: List[Dict[str, Any]], *, focus: Optional[str] = None,
                  config: Optional[Dict[str, Any]] = None,
                  workspace_root: Optional[Any] = None,
                  llm_fn: Optional[Callable[..., Optional[str]]] = None
                  ) -> Optional[Dict[str, Any]]:
    """调用大模型产出结构化摘要；**任何失败一律返回 None** 交给调用方降级。

    ``llm_fn`` 默认取 :func:`_default_llm_fn`（即 ``chat_completion``），可注入
    以便测试完全离线。失败面（抛异常 / 返回 None / 非 JSON / 缺键 / key_info
    不是对象）全部收敛成同一个 ``None``，调用方据此回退 ``rule_only``。
    """
    fn = llm_fn or _default_llm_fn()
    if fn is None:
        return None
    prompt = _build_llm_prompt(messages, focus)
    try:
        raw = fn(
            prompt,
            config=config,
            workspace_root=workspace_root,
            system_prompt="你是严谨的上下文压缩器，只输出 JSON。",
            # [R3 波动收敛·根因 3] 0.1 → 0（STABLE_TEMPERATURE）：摘要压缩是
            # **抽取/整理**类任务——同一段对话应压出同一份摘要。留 0.1 的采样
            # 会让摘要措辞逐轮漂移，而摘要是 resume 上下文的唯一来源（漂移
            # 直接等于「同一会话在不同轮次记得的事不一样」），跨轮对比失真。
            temperature=STABLE_TEMPERATURE,
            timeout=60.0,
            max_tokens=2000,
        )
    except Exception:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    data = _parse_json_object(raw)
    if not isinstance(data, dict):
        return None
    if any(key not in data for key in SUMMARY_KEYS):
        return None
    return _normalize_summary(data)
