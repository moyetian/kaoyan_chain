# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 核心智能体执行循环 (Agent Loop)
标准工作流:
User ➔ LLM ➔ 判断是否需要 Tool ➔ Tool 执行 ➔ Tool Result ➔ LLM ➔ ... ➔ Final Answer
"""

import re
import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from .sandbox import Sandbox
from .permissions import PermissionManager
from .tools_impl import ToolRegistry
from .context_engine import ContextEngine
from .kaoyan_context import KaoyanContext
from .memory import MemoryManager
from .hooks import HookManager
from .mcp_client import MCPClientManager
from .turn_recovery import DoomLoopBreaker
from .runtime import RunBudgetExceeded, RunLimits, RunRuntime, RunState
from .session_log import (
    SessionLog,
    RESUME_TAIL_MESSAGES,
    TOOL_RESULT_MAX_CHARS,
    compose_history,
    load_events,
    rebuild_history,
    EVENT_SESSION_START,
    EVENT_USER,
    EVENT_ASSISTANT,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_LLM_CALL,
    EVENT_COMPACT,
    EVENT_SESSION_END,
)

try:  # 网络访问安全（双导入路径兼容）；[K4] 读取/解压上限随 SSE 设施进 llm_client，
    # 但 ``decompress_limited`` 保留在本模块命名空间：既有测试桩点
    # （test_w1_llm_telemetry）按 ``loop_module.decompress_limited`` 注入。
    from net_guard import decompress_limited, safe_urlopen
except ImportError:  # pragma: no cover
    from tools.net_guard import decompress_limited, safe_urlopen  # type: ignore

try:  # [B3b] 压缩摘要头部常量的唯一实现处在 compaction（loop 不再保留私有副本）
    from .compaction import COMPACT_SUMMARY_PREFIX
except ImportError:  # pragma: no cover
    from tools.agent.compaction import COMPACT_SUMMARY_PREFIX  # type: ignore


try:  # [K4] 统一 LLM 出口：SSE 基础设施 / 结构化异常 / URL 归一（双导入路径兼容）
    from llm_client import (
        ChatRequest,
        LLMDeterministicError,
        LLMResponseTooLargeError,
        LLMRetryExhausted,
        _SSE_READ_CHUNK,
        _STREAM_STALL_TIMEOUT,
        normalize_openai_url,
        request_chat,
    )
except ImportError:  # pragma: no cover
    from tools.llm_client import (  # type: ignore
        ChatRequest,
        LLMDeterministicError,
        LLMResponseTooLargeError,
        LLMRetryExhausted,
        _SSE_READ_CHUNK,
        _STREAM_STALL_TIMEOUT,
        normalize_openai_url,
        request_chat,
    )

#: [K4] ``normalize_openai_url`` 已收敛为 ``llm_client`` 单一实现（本模块 re-export）。
#: 兼容既有导入路径：doctor.py / school_scout.py / agentic_research.py /
#: vision_solver.py / cli.agent.__init__ 等仍从本模块或 engine 取该函数。
#: [K4] ``_SSE_READ_CHUNK`` / ``_STREAM_STALL_TIMEOUT`` 常量随 SSE 基础设施搬入
#: ``llm_client``，此处 re-export 保住 ``tests/test_w8_streaming_client.py`` 的
#: 直接导入；``_stream_timeout()`` 的超时策略仍留在本模块（调用方决定）。


#: [B3a] ``COMPACT_SUMMARY_PREFIX``（从 compaction 导入）用于识别「本次
#: compact_context 是否真的发生了压缩」—— 只有真的插入了新摘要，才写 compact
#: 事件并更新 resume 用的摘要。[B3b] 私有副本已删除，避免两处字面量各自漂移。


# ── [W8] 流式（SSE）客户端 ──────────────────────────────────────────────
# 背景（KaoYanBench core50 实测 + 探针复现）：网关对**非流式**请求有 ~60s 硬超时
# （60.5s 被 RemoteDisconnected 掐断），12/50 题因此产出空答案；同内容改流式可完整
# 跑满 96.8s（首块 3.5s、2460 个 SSE 块）。故两个 LLM 调用默认改走 stream=true，
# 逐块累积重建出与非流式完全一致的 ``choices[0].message`` 结构。
# [K4] 解析设施（``_StreamAccumulator`` / ``_consume_sse`` / ``_post_chat`` 等）
# 已整体搬入 ``llm_client``；本模块只保留调用策略：超时计算（``_stream_timeout``）、
# spinner/on_open 观感、error_kind 遥测与返回 None 契约。

#: 思维链标签（模型偶发把思考块标签泄漏进正文 content）
_THINK_BLOCK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
_THINK_TAG_RE = re.compile(r"</?think(?:ing)?>?", re.IGNORECASE)


def _strip_think_tags(text: str) -> str:
    """移除模型输出中偶发泄漏的思维链标签（``<think…`` / ``</think>`` 等）。

    [P2 修复·思维链标签泄漏] 三沙箱实测：批改输出在工具调用转场处泄漏 1 次
    ``</think>``（ASCII，非终端显示伪影）——模型把思考块的收尾标签混进了正文
    ``content``，而 content 会直接 print 给考生、写入对话历史与交付档案。
    此处统一清洗：先删除完整的 ``<think…>…</think…>`` 块，再清除残余的
    孤立开/闭标签（含被截断的无 ``>`` 尾巴，如行尾的 ``</think``）；确实
    发生过清除时顺带收敛空行，避免标签原位留下多行空白。
    """
    s = str(text or "")
    if "<think" not in s.lower() and "</think" not in s.lower():
        return s
    new = _THINK_TAG_RE.sub("", _THINK_BLOCK_RE.sub("", s))
    if new != s:
        new = re.sub(r"\n{3,}", "\n\n", new)
    return new


class AgentRunner:
    def __init__(
        self,
        config: Dict[str, Any],
        workspace_root=None,
        permission_mode: str = "ask",
        max_steps: int = 10,
        stream_callback: Optional[Callable[[str], None]] = None,
        step_callback: Optional[Callable[[str], None]] = None,
        live_callback: Optional[Callable[[str, str], None]] = None,
        request_timeout: Optional[float] = None,
        # GUI 场景设 True：不在 stdout 打字机输出（否则控制台与界面各刷一份）
        quiet: bool = False,
        # [A3b] 调用方显式提供的审批通道（如 GUI 弹窗 GuiApproval）；
        # None 时回落 agent.approval.select_channel 的默认 TTY/headless 判定。
        approval_channel=None,
        # [B3b] 指定既有会话 id：resume（ky session resume）/ GUI 跨消息复用同一
        # 个 .jsonl。None = 新建会话（沿用 B3a 行为）。
        session_id: Optional[str] = None,
    ):
        self.config = config
        self.workspace_root = workspace_root
        self.max_steps = max_steps
        self.runtime = RunRuntime(
            limits=RunLimits.from_config(config, fallback_steps=max_steps)
        )
        self.stream_callback = stream_callback
        self.step_callback = step_callback
        self.live_callback = live_callback
        self.quiet = bool(quiet)
        # [P2 修复·GUI 卡死] 上游对话请求此前硬编码 120s 超时，GUI 端点击一次
        # 若上游无响应会「转圈」两分钟且无任何反馈。现允许调用方覆盖，
        # 并支持通过配置项 request_timeout / GUI 传入值调低。
        # [健壮性] 配置值可能被用户写成非数字（如 "60s"），此处做安全解析，
        # 解析失败一律回落到 120，绝不因一个可选配置让整个 AgentRunner 构造崩溃。
        self.request_timeout = self._resolve_timeout(request_timeout, config)
        
        # 1. 初始化三级记忆引擎并预装考研默认偏好
        self.memory_manager = MemoryManager(workspace_root=self.workspace_root)
        self.memory_manager.init_defaults_from_config(self.config)

        # 2. 初始化生命周期拦截钩子系统
        self.hooks = HookManager(workspace_root=self.workspace_root, memory_manager=self.memory_manager)

        # 3. 初始化外部 MCP 客户端管理器并尝试加载配置
        self.mcp_manager = MCPClientManager(workspace_root=self.workspace_root)
        if "mcp_servers" in self.config and isinstance(self.config["mcp_servers"], dict):
            self.mcp_manager.load_from_config(self.config["mcp_servers"])

        # 4. 初始化沙箱、权限与工具库
        # [B2b] allowed_extra_paths：工作区外长期授权目录（配置内目录直接放行、
        # 不弹卡），来源 ky_config.json 的 agent.allowed_extra_paths。
        self.sandbox = Sandbox(workspace_root=self.workspace_root,
                               allowed_extra_paths=self._resolve_extra_paths(self.config))
        # config 一并传入：headless（GUI / 网关 / 管道）下的写操作策略
        # `agent.headless_write_policy` 由 PermissionManager 从配置解析。
        # approval_channel 一并透传：GUI 传入自己的弹窗通道后，Level 4-5 会真的
        # 弹审批框，而不是走 headless 默认拒绝（A3b）。
        self.permissions = PermissionManager(mode=permission_mode, workspace_root=self.workspace_root,
                                             config=self.config, approval_channel=approval_channel)
        self.tool_registry = ToolRegistry(
            sandbox=self.sandbox,
            permissions=self.permissions,
            memory_manager=self.memory_manager
        )
        # 挂载外部 MCP 工具
        self.tool_registry.register_mcp_tools(self.mcp_manager)

        # 5. 初始化上下文引擎 (挂载三级分层记忆 + 按模型解析上下文预算)
        self.context_engine = ContextEngine(
            workspace_root=self.sandbox.workspace_root,
            active_subject=self.config.get("active_subject", "math"),
            memory_manager=self.memory_manager,
            model=self.config.get("model", ""),
            config=self.config,
        )

        self.history: List[Dict[str, Any]] = []
        # [B3a] 会话持久化：日志惰性创建（首次有效 run 才落盘），事件流写入
        # .memory/sessions/<session_id>.jsonl；history 的截断/摘要插入与
        # session_log.rebuild_history 共用同一 compose_history 语义。
        # [B3b] _session_id 由调用方指定（resume / GUI 复用）；None = 新建会话。
        self._session_id: Optional[str] = session_id
        self._session_log: Optional[SessionLog] = None
        self._session_started = False        # SessionStart 钩子只触发一次
        self._session_start_logged = False   # session_start 事件只写一次
        self._history_summary: Optional[str] = None  # 最近一次压缩摘要（resume 重建用）
        self._closed = False

        # [K6] 考研统一上下文（科目/数学编码/目标校/专业/初试日）：构造一次；
        # run() 开头按最新配置重建（学员中途切换科目/院校即时生效）。
        self.kaoyan_ctx = KaoyanContext.from_config(
            self.config,
            workspace_root=self.sandbox.workspace_root,
            session_id=self._session_id,
        )
        # [K8] 最近一次 LLM 失败的分流提示（"overflow" / "auth" / None）——
        # 由 _call_llm 的失败路径设置、成功路径清空；run 主循环据此分流。
        self._last_llm_error_hint: Optional[str] = None

    @staticmethod
    def _resolve_timeout(explicit, config) -> float:
        """解析请求超时秒数：显式参数 > 配置项 > 默认 120；非法值一律回落默认。"""
        for cand in (explicit, (config or {}).get("request_timeout")):
            if cand is None or cand == "":
                continue
            try:
                val = float(cand)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                continue
        return 120.0

    @staticmethod
    def _resolve_extra_paths(config) -> list:
        """[B2b] 解析 ``agent.allowed_extra_paths``（工作区外长期授权目录清单）。

        只收非空字符串；配置缺失 / 类型不对 / 任何异常一律返回 ``[]``（fail-safe：
        宁可少授权，绝不因配置写错而放开沙箱）。
        """
        try:
            agent_cfg = (config or {}).get("agent")
            raw = agent_cfg.get("allowed_extra_paths") if isinstance(agent_cfg, dict) else None
            if not isinstance(raw, (list, tuple)):
                return []
            return [str(p) for p in raw if isinstance(p, str) and p.strip()]
        except Exception:
            return []

    def set_subject(self, subject: str):
        self.config["active_subject"] = subject
        self.context_engine.set_subject(subject)
        # [K6] 同步统一上下文（math_key 按新科目归一：非数学科目置 None）
        self.kaoyan_ctx = self.kaoyan_ctx.with_subject(subject)

    # ── [B3a] 会话持久化辅助 ─────────────────────────────────────────────

    def _restore_history_from_log(self) -> None:
        """[B3b] resume：若指定 session_id 的日志已有事件，则从事件流恢复会话。

        * 仅在 ``self._session_id`` 非空（resume / GUI 复用）且尚未恢复过时执行；
          普通新建会话（id 为 None）零开销直接返回；
        * 恢复 ``history``（``rebuild_history``，与实时维护同语义）与
          ``_history_summary``（最后一条 compact 的摘要），并把会话标记为
          「已开始 / 已写过 session_start」—— 源文件里已经有过 SessionStart，
          resume 不得重复触发钩子、不得重复写 session_start 事件；
        * 读盘失败 / 文件不存在一律静默降级（绝不抛），文件不存在 = 全新会话。
        """
        if not self._session_id or self._session_started or self.history:
            return
        try:
            log = SessionLog(workspace_root=self.sandbox.workspace_root,
                             session_id=self._session_id)
            events = load_events(log.path)
            if not events:
                return
            self.history = rebuild_history(events)
            self._session_started = True
            self._session_start_logged = True
            for evt in reversed(events):
                if not isinstance(evt, dict) or evt.get("type") != EVENT_COMPACT:
                    continue
                payload = evt.get("payload")
                text = payload.get("summary") if isinstance(payload, dict) else None
                if isinstance(text, str) and text.strip():
                    self._history_summary = text
                break
        except Exception:
            return

    def _ensure_session_log(self) -> Optional[SessionLog]:
        """惰性创建会话日志（首次有效 run 才落盘）；构造失败一律降级为纯内存。

        [B3b] 指定 ``session_id``（resume / GUI 复用）且文件已有事件时，顺带
        恢复 history 与压缩摘要（见 :meth:`_restore_history_from_log`）。
        """
        if self._session_log is None and not self._closed:
            try:
                self._session_log = SessionLog(workspace_root=self.sandbox.workspace_root,
                                               session_id=self._session_id)
                self._restore_history_from_log()
            except Exception as e:
                print(f"\033[93m[warn] 会话日志初始化失败，本会话降级为纯内存: "
                      f"{type(e).__name__}: {e}\033[0m")
                self._session_log = None
        return self._session_log

    def _append_event(self, event_type: str, payload=None, parent=None) -> Optional[str]:
        """写一条会话事件；任何失败都不得中断对话（SessionLog 内部已降级）。"""
        if self._session_log is None:
            return None
        try:
            return self._session_log.append(event_type, payload, parent=parent)
        except Exception:
            return None

    def _log_compact_if_happened(self, before: List[Dict[str, Any]],
                                 after: List[Dict[str, Any]]) -> None:
        """检测 compact_context 是否真的压缩了；是则记 compact 事件并更新摘要。

        判据：压缩后的消息里出现**新**的摘要 system 消息（``render_summary``
        的头部是固定字面量，可稳定识别；输入里已有同一条则说明本轮没压缩）。
        更新后的 ``_history_summary`` 会被 run 末尾的 history 重组带上，
        使实时上下文与 resume 重建结果保持一致。
        """
        summary_text = None
        for msg in after:
            if not isinstance(msg, dict) or msg.get("role") != "system":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.startswith(COMPACT_SUMMARY_PREFIX):
                summary_text = content
        if not summary_text:
            return
        for msg in before:
            if isinstance(msg, dict) and msg.get("content") == summary_text:
                return
        self._history_summary = summary_text
        self._append_event(EVENT_COMPACT, {
            "summary": summary_text,
            "before_messages": len(before),
            "after_messages": len(after),
        })

    def close(self) -> None:
        """会话收尾（幂等）：写 session_end 事件并关闭日志句柄。

        [B3a] 供 REPL / GUI 调用方在退出时收尾。**不触发** SessionEnd 钩子 ——
        钩子仍由既有调用点 ``hooks.trigger_session_end`` 负责，避免日终复盘
        （写盘 + IM 推送都有副作用）被重复触发。从未有效 run 过的会话不产生日志文件。
        """
        if self._closed:
            return
        self._closed = True
        self._append_event(EVENT_SESSION_END,
                           {"active_subject": self.config.get("active_subject", "")})
        if self._session_log is not None:
            try:
                self._session_log.close()
            except Exception:
                pass

    def run(self, user_input: str, interactive: bool = True) -> str:
        """运行完整的 Agent Loop 交互循环。"""
        self.runtime.start(user_input, session_id=self._session_id or "")
        try:
            self.runtime.transition(RunState.PROMPT)
        except ValueError:
            pass
        runtime_budget_exhausted = False
        # [K6] 每轮按最新配置重建统一上下文（热切换生效），导出 hooks 消费的
        # ctx：active_subject/math_key 供考纲红线区分 math1/2/3/396，
        # target_school 供 school_scope_guard 判定院校范围。
        self.kaoyan_ctx = KaoyanContext.from_config(
            self.config,
            workspace_root=self.sandbox.workspace_root,
            session_id=self._session_id,
        )
        # [K9] 配置热切换时同步上下文引擎；否则 hooks 已切到新科目，
        # 但系统提示仍会继续挂载上一次运行的学科协议与状态文件。
        try:
            self.context_engine.set_subject(self.kaoyan_ctx.active_subject or "math")
        except Exception:
            pass
        ctx = self.kaoyan_ctx.hook_ctx(user_input)
        # [B3b] resume 恢复必须先于 SessionStart 判定：恢复成功的会话在源文件里
        # 已经触发过 SessionStart 钩子，本次不得重复触发（`_session_started`
        # 会被置 True）。普通新建会话（session_id 为 None）零开销直接返回。
        self._restore_history_from_log()
        # [B3a 生命周期统一] SessionStart 只在**会话首次** run 时触发一次；
        # 旧实现在每轮 run 都触发（一个会话只有一次开始，语义不对）。
        if not self._session_started:
            self._session_started = True
            self.hooks.trigger_session_start(ctx)

        api_key = self.config.get("api_key", "").strip()
        if not api_key:
            err_msg = "[!] 错误: 未配置大模型 API Key！请在终端输入 /config 进行配置。"
            print(f"\033[91m{err_msg}\033[0m")
            self.runtime.fail("missing_api_key")
            return err_msg

        # [B3a] 有效会话开始：惰性创建日志并记录 session_start
        # （写盘失败自动降级为纯内存，见 SessionLog / _append_event）。
        self._ensure_session_log()
        if not self._session_start_logged:
            self._session_start_logged = True
            self._append_event(EVENT_SESSION_START, {
                "active_subject": ctx.get("active_subject"),
                "user_input": user_input[:500],
            })

        # 1. 组装对话上下文（[K6] 目标校等统一走 kaoyan_ctx，与 hook 同源）
        sys_prompt = self.context_engine.build_system_prompt(kaoyan_ctx=self.kaoyan_ctx)
        
        # 构建当前请求的消息列表
        active_messages: List[Dict[str, Any]] = [{"role": "system", "content": sys_prompt}]
        active_messages.extend(self.history)
        active_messages.append({"role": "user", "content": user_input})
        self._append_event(EVENT_USER, {"content": user_input})

        # 2. 上下文防爆压缩 (联动 BeforeCompact 自动提炼决策记忆)
        # [B3a] 调用前后各留一份，用于识别「本次是否真的发生了压缩」并写 compact 事件
        before_compact = list(active_messages)
        active_messages = self.context_engine.compact_context(active_messages, hook_manager=self.hooks)
        self._log_compact_if_happened(before_compact, active_messages)

        # [D0] 写入批次：一次 run（一个用户回合）内的所有文件写入共享同一快照
        # 批次 —— 出问题时可整批回滚，也可用 `ky rollback --file` 精确回滚单文件。
        try:
            _sid = getattr(self._session_log, "session_id", "") or ""
            self.permissions.begin_write_batch(label=f"run:{_sid}" if _sid else "run")
        except Exception:
            pass

        # 3. Agent 循环 (最多 max_steps 步)
        step = 0
        final_answer = ""
        # [W11 拦截引导升级] 同类拦截连续计数（回合级作用域：GUI 每条消息新建
        # runner、CLI 会话级复用 runner，都应以「一次 run」为计数窗口）。
        _block_streaks = {"safety": 0, "search": 0}
        # [K7-U3] 计数与文案生成已迁入 hooks.block_streak_guard（PostToolUse,
        # priority 60）：状态与决策通道经 ctx 传递；决策由 loop 原位消费
        # （tool 消息入列之后），消息顺序与事件顺序与抽取前逐字一致。
        ctx["block_streaks"] = _block_streaks
        ctx["block_streak_decisions"] = []
        ctx["block_escalate_threshold"] = self._BLOCK_ESCALATE_THRESHOLD
        # [K8] Doom-loop 熔断器（run 级作用域；每轮 run 重置计数）。
        self._doom_loop = DoomLoopBreaker()
        # [收尾答案] 模型若每一步都在调工具，循环会因步数耗尽而退出、final_answer
        # 保持空串（评测实测 50/50 题如此）。用两个标记支撑收尾恢复：
        #   last_assistant_text —— 最后一条非空 assistant 文本（兜底回退用）；
        #   api_failed —— API 硬失败（含重试后仍失败）时置位：跳过收尾请求
        #   （网络已断，再发只会白等一次超时），但仍回退模型失败前留下的非空
        #   文本；两者皆无才返回空串，让 GUI/REPL 走各自的诊断提示。
        last_assistant_text = ""
        api_failed = False
        # [K8 错误分类分流] auth 失败（401/403，key 无效重试必败）→ 跳过收尾链；
        # overflow（400/413 上下文超长）→ 强制压缩后重试一次（恰一次）。
        auth_failed = False
        overflow_retried = False

        # [W8-C 产物落盘闸门] 评测实测（RES-002）：prompt 已硬性要求「必须用 write_file
        # 实际写入 output/report.json」，模型仍可能零 write_file 调用、收尾时**幻觉声称**
        # 「written: output/report.json」→ file_exists 检查直接判负。仅靠提示词无法根治，
        # 故加机械闸门：主循环/收尾结束后校验产物是否**真实存在**，缺失则追加一条 user
        # 指令并重入主循环（此时工具可用，模型会去调 write_file），nudge 硬上限 2 次。
        # 保守解析：prompt 无明确 output/ 产物措辞 → required_outputs 为空 → 闸门完全
        # 不生效（零行为变化）；解析/校验/追加任一步异常都静默跳过闸门。
        required_outputs = self._parse_required_outputs(user_input)
        deliverable_nudges = 0
        step_budget = self.max_steps

        while True:      # [W8-C] 闸门重入：产物缺失时最多再进 2 次主循环（每次 +6 步）
            while step < step_budget:
                try:
                    self.runtime.step()
                except RunBudgetExceeded:
                    runtime_budget_exhausted = True
                    final_answer = "本轮已达到 Agent 运行预算，已停止继续调用模型。"
                    break
                step += 1
                try:
                    self.runtime.transition(RunState.MODEL)
                except ValueError:
                    pass
                if self.step_callback and step == 1:
                    self.step_callback("⏳ [私教审阅中] 正在分析题干要求与教学规划...")

                # [K7-U2] 每轮迭代开始扩展点（无注册 = 恒等；hook 可返回改写
                # 后的 messages，返回 None 保持不变）。
                active_messages = self.hooks.trigger_prepare_next_turn(active_messages, ctx)

                # 向 LLM 请求（带 tools 参数）
                # [K7-U2] 请求前扩展点（紧邻 _call_llm；无注册 = 恒等）。
                active_messages = self.hooks.trigger_prepare_request(active_messages, ctx)
                response_data = self._call_llm(active_messages)
                if not response_data:
                    # [K8 错误分类分流] overflow（400/413 上下文超长）→ 强制
                    # 压缩后重试一次；auth（401/403）→ 标记跳过收尾链；其余
                    # 维持现状（W7 的 api_failed 收尾尝试不变）。
                    hint = getattr(self, "_last_llm_error_hint", None)
                    if hint == "overflow" and not overflow_retried:
                        overflow_retried = True
                        before_compact = list(active_messages)
                        active_messages = self.context_engine.compact_context(
                            active_messages, hook_manager=self.hooks, force=True)
                        self._log_compact_if_happened(before_compact, active_messages)
                        if self.step_callback:
                            self.step_callback(
                                "🗜️ [上下文超限] 已强制压缩历史消息并重试本轮请求")
                        continue
                    api_failed = True
                    if hint == "auth":
                        auth_failed = True
                    # [K7-U2] 迭代末扩展点（空响应出口）。
                    self.hooks.trigger_finish_turn(active_messages, ctx)
                    break

                try:
                    self.runtime.record_usage(response_data.get("usage"))
                except RunBudgetExceeded:
                    runtime_budget_exhausted = True
                    final_answer = "本轮已达到 Agent Token 预算，已停止继续调用模型。"
                    break

                choice = response_data.get("choices", [{}])[0]
                message = choice.get("message", {})
                # [P2 修复·思维链标签泄漏] 统一出口清洗：content 会流向
                # print / 对话历史 / last_assistant_text / final_answer 四处。
                content = _strip_think_tags(message.get("content") or "")
                tool_calls = message.get("tool_calls") or []
                reasoning = message.get("reasoning_content") or message.get("reasoning")
                if reasoning and self.step_callback:
                    self.step_callback(f"🧠 [私教深度思考]\n{str(reasoning).strip()}")

                # ── 检查是否包含 XML 格式的 Fallback Tool Call ──
                if not tool_calls and "<tool_call>" in content:
                    fallback_calls = self._parse_fallback_tool_calls(content)
                    if fallback_calls:
                        tool_calls = fallback_calls
                        # 剔除掉 tool_call 标签纯文本
                        content = content.split("<tool_call>")[0].strip()

                # ── 情形 A: 模型要求调用外部工具 (Tool Call) ──
                if tool_calls:
                    # [收尾答案] 记录最后一条非空 assistant 文本（模型边调工具边写的
                    # 分析说明），步数耗尽时作为最终答复的兜底回退。
                    if content and content.strip():
                        last_assistant_text = content
                    # 将 assistant 带 tool_calls 的消息记入上下文
                    assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
                    active_messages.append(assistant_msg)

                    if content and not self.quiet:
                        print(content)

                    for tc in tool_calls:
                        tc_id = tc.get("id", f"call_{int(time.time()*1000)}")
                        fn_info = tc.get("function", {})
                        fn_name = fn_info.get("name", "")
                        try:
                            self.runtime.transition(RunState.TOOL)
                            self.runtime.tool_call(fn_name)
                        except (RunBudgetExceeded, ValueError):
                            exec_result = "RuntimeStopped: tool-call budget exhausted"
                            active_messages.append({
                                "role": "tool", "tool_call_id": tc_id,
                                "name": fn_name, "content": exec_result,
                            })
                            continue
                        fn_args_raw = fn_info.get("arguments", "{}")

                        if isinstance(fn_args_raw, str):
                            try:
                                fn_args = json.loads(fn_args_raw)
                            except Exception:
                                fn_args = {}
                        else:
                            fn_args = fn_args_raw

                        # [B3a] 记 tool_call 事件；其事件 id 作为配对 tool_result 的 parent
                        call_event_id = self._append_event(EVENT_TOOL_CALL, {
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "arguments": fn_args if isinstance(fn_args, dict) else {"raw": fn_args},
                        })

                        # 优雅的高科技状态行显示
                        args_summary = ", ".join(f"{k}='{v}'" if len(str(v))<40 else f"{k}='...'" for k, v in fn_args.items())
                        if not self.quiet:
                            print(f"\n\033[96m🛠️  [Agent Tool] 智能私教正在调用: \033[1m{fn_name}\033[0m\033[96m({args_summary})\033[0m")
                        if self.step_callback:
                            self.step_callback(f"🛠️ [调用工具] {fn_name}({args_summary})")

                        # 触发 PreToolUse 钩子 (沙箱与考纲红线硬拦截)
                        allow, hook_reason, mod_args = self.hooks.trigger_pre_tool_use(fn_name, fn_args, ctx)
                        fused = False
                        if not allow:
                            if not self.quiet:
                                print(f"   \033[91m↳ [考纲红线拦截]: {hook_reason}\033[0m")
                            if self.step_callback:
                                self.step_callback(f"   ↳ [考纲红线拦截]: {hook_reason}")
                            exec_result = f"HookBlocked: {hook_reason}"
                        else:
                            # [K8] Doom-loop 熔断：同签名（工具名+参数）连续 ≥3 次
                            # → 不再执行，合成 tool 结果提示改道（事件 kind=
                            # doom_loop_fused）。hook 拦截的调用不计数（只观察
                            # 真正要执行的调用，避免与 W11 升级计数相互干扰）。
                            fused, streak = self._doom_loop.observe(fn_name, mod_args)
                            if fused:
                                exec_result = self._doom_loop.fused_result(fn_name, streak)
                                if self.step_callback:
                                    self.step_callback(
                                        f"   ↳ [死循环熔断] {fn_name} 连续 {streak} "
                                        f"次同签名调用，已跳过执行")
                            else:
                                # 执行工具（call_id 供输出超预算落盘命名）
                                exec_result = self.tool_registry.execute_tool(
                                    fn_name, mod_args, interactive=interactive, call_id=tc_id)
                                # 触发 PostToolUse 钩子 (自检与联动)
                                exec_result = self.hooks.trigger_post_tool_use(fn_name, mod_args, exec_result, ctx)

                        # 简短结果提示
                        res_preview = str(exec_result)[:80].replace("\n", " ")
                        is_err = ("Error" in exec_result or "PermissionDenied" in exec_result
                                  or "HookBlocked" in exec_result or "DoomLoopFused" in exec_result)
                        if not self.quiet:
                            if is_err:
                                print(f"   \033[93m↳ 结果: {res_preview}...\033[0m")
                            else:
                                print(f"   \033[92m↳ 完成: {res_preview}...\033[0m")
                        if self.step_callback:
                            self.step_callback(f"   ↳ {'异常: ' if is_err else '完成: '}{res_preview}...")

                        # 追加 tool 结果回包
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "content": exec_result
                        }
                        active_messages.append(tool_msg)
                        try:
                            self.runtime.transition(RunState.OBSERVE)
                        except ValueError:
                            pass

                        # [W10 检索行为引导] 搜索引擎直抓被拦 → 在对话流内追加明确的
                        # 用户消息，把模型拉回 web_search。实测：系统提示级引导 + 工具
                        # 描述强化 + 工具置顶，对 flash 级模型仍压不住「搜索=fetch 引擎」
                        # 的强惯性（被拦后换引擎重试、猜站内 URL，也不调用 web_search）；
                        # 在工具回包后立即追加一条直白指令是最后一道有效引导。
                        #
                        # [W11 拦截引导升级] 对「安全拦截」（run_command 白名单 / 脚本
                        # 闸门）与「引擎直抓拦截」做同类连续计数：连续达到阈值后文案升级
                        # 为「停止试探」级警告。实证动机：PLAN-003 模型连续 4 种变体试探
                        # 合并脚本（~3 分钟）直至任务超时——单条重复文案对 flash 级模型
                        # 惯性无效时，需要更强的信号。成功执行同类工具即重置计数
                        # （「连续」语义，而非历史累计）。
                        # [K7-U3] 计数与文案生成已原位抽取到 hooks.block_streak_guard
                        # （PostToolUse, priority 60）；本处原位消费其决策（追加消息 +
                        # 写事件），位置与抽取前一致 → 消息/事件顺序逐字不变。
                        _decisions = ctx.get("block_streak_decisions")
                        if _decisions:
                            for guide, nudge_kind in _decisions:
                                active_messages.append({"role": "user", "content": guide})
                                self._append_event(EVENT_USER, {"content": guide,
                                                                "kind": nudge_kind})
                            _decisions.clear()

                        # [B3a] 记 tool_result 事件（parent 串到对应 tool_call）。
                        # 超长结果按 TOOL_RESULT_MAX_CHARS 截断存储并在 payload 标注；
                        # resume 重建不依赖 tool 事件，故截断不破坏 rebuild 语义。
                        # [K8] 熔断调用在 payload 标注 kind=doom_loop_fused。
                        result_text = str(exec_result)
                        _result_payload = {
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "content": result_text[:TOOL_RESULT_MAX_CHARS],
                            "truncated": len(result_text) > TOOL_RESULT_MAX_CHARS,
                            "original_chars": len(result_text),
                        }
                        if fused:
                            _result_payload["kind"] = "doom_loop_fused"
                        self._append_event(EVENT_TOOL_RESULT, _result_payload,
                                           parent=call_event_id)

                    # 工具回包可能包含大文件或多轮结果，在循环内动态防爆压缩
                    before_compact = list(active_messages)
                    active_messages = self.context_engine.compact_context(active_messages, hook_manager=self.hooks)
                    self._log_compact_if_happened(before_compact, active_messages)

                    # [K7-U2] 迭代末扩展点（工具往返出口）。
                    self.hooks.trigger_finish_turn(active_messages, ctx)

                    # 继续下一轮循环，让 LLM 拿到工具结果进行最终综合分析
                    continue

                # ── 情形 B: 模型输出最终答案 (Final Answer) ──
                final_answer = content
                # 打字机流式输出给学员
                self._display_final_answer(final_answer)
                # [K7-U2] 迭代末扩展点（最终答案出口）。
                self.hooks.trigger_finish_turn(active_messages, ctx)
                break

            # 3.5 [收尾答案] 步数耗尽 / 模型空回复 → 再要一次「禁用工具的最终答复」。
            # [W7 收尾强化] API 硬失败时**不再跳过收尾**：网络可能只是瞬断，先试
            # 一次低成本的极简收尾（短消息、快请求），失败再试全量——原来直接跳过
            # 是空答案题（19/50）的主要失分路径（见 _recover_final_answer）。
            # [K8 错误分类分流] auth 硬失败（401/403，key 无效）例外：收尾请求
            # 同样必败，跳过（省一次注定失败的请求与等待）；有兜底文本仍回退。
            if not final_answer and not runtime_budget_exhausted:
                if auth_failed:
                    final_answer = last_assistant_text or ""
                else:
                    final_answer = self._recover_final_answer(
                        active_messages, last_assistant_text, api_failed=api_failed)

            # [W8-C 产物闸门] 产物是否真的落盘？缺文件才 nudge；网络硬失败且毫无产出时
            # 跳过——那种情况再发请求只会白等超时，不可能写出文件。
            try:
                missing = []
                if required_outputs and (not api_failed or final_answer.strip()):
                    missing = self._missing_required_outputs(required_outputs)
                if not missing or deliverable_nudges >= self._DELIVERABLE_MAX_NUDGES:
                    if missing:
                        # [W9] nudge 用尽仍缺失：把 final_answer 中可解析的 JSON
                        # 机械落盘（内容完全来自模型输出，零伪造；解析不出则跳过）。
                        self._autosave_deliverables(missing, final_answer)
                    break
                deliverable_nudges += 1
                step_budget = step + self._DELIVERABLE_NUDGE_STEPS
                self.runtime.extend_step_budget(step_budget)
                api_failed = False
                final_answer = ""
                nudge_text = self._deliverable_nudge_text(missing)
                active_messages.append({"role": "user", "content": nudge_text})
                self._append_event(EVENT_USER, {"content": nudge_text,
                                                "kind": "deliverable_nudge"})
                if self.step_callback:
                    self.step_callback(
                        f"📎 [产物闸门] {', '.join(missing)} 尚未真实落盘，"
                        f"已要求模型立即补写（{deliverable_nudges}/"
                        f"{self._DELIVERABLE_MAX_NUDGES}）")
            except Exception:
                # 闸门任何一步出问题（含 step_callback 抛错）→ 静默跳过，按现有逻辑收尾
                break

        # [W10 JSON 交付自检] 疑似 JSON 答案但语法非法（内容完整、括号/引号/
        # 键名错位）→ 一次性语法修复，只修语法不改内容；失败保留原答案。
        # [K8] auth 硬失败（401/403）跳过：修复请求同样必败，不烧注定失败的调用。
        if not auth_failed:
            final_answer = self._repair_json_answer(final_answer, active_messages)

        # [B3a] 更新历史：与 session_log.rebuild_history 共用同一 compose_history
        # 语义（最近一条压缩摘要 + 最后 RESUME_TAIL_MESSAGES 条消息），
        # 因此「resume 上下文 == 实时上下文」可逐条断言。
        msgs = [m for m in self.history
                if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        msgs.append({"role": "user", "content": user_input})
        msgs.append({"role": "assistant", "content": final_answer})
        self.history = compose_history(self._history_summary, msgs, limit=RESUME_TAIL_MESSAGES)

        self._append_event(EVENT_ASSISTANT, {"content": final_answer})

        # [W4 引用接线] 引用产出：从最终答案提取 URL 引用 + 本轮工具证据核验，
        # 落盘 .memory/last_citations.json（评测适配器/下游如实读取）。
        self._emit_citations(final_answer, active_messages)

        # 同步推送到网页伴侣
        if self.live_callback:
            self.live_callback("user", user_input)
            self.live_callback("assistant", final_answer)

        # [K7-U2/U4] run 收尾扩展点（收尾链末端；无注册 = 恒等）。收尾链本身
        # （_recover_final_answer / _repair_json_answer / _emit_citations）不
        # 搬迁 —— 有大量测试钉住其行为，本批仅补扩展点。
        ctx["final_answer"] = final_answer
        self.runtime.complete(final_answer)
        ctx["runtime"] = self.runtime.snapshot()
        self.hooks.trigger_finish_run(ctx)

        # [D0] 关闭写入批次：下一次 run 的文件写入另起一个快照批次。
        # （异常路径由下一次 begin_write_batch 兜底重置，不会串批。）
        try:
            self.permissions.end_write_batch()
        except Exception:
            pass
        return final_answer

    #: [收尾答案] 步数耗尽时追加的收尾指令：明确要求模型直接作答、禁用工具。
    FINALIZE_INSTRUCTION = (
        "（系统提示）本轮工具调用步数已用尽。请立即基于以上已获取到的全部信息，"
        "直接输出给学员的最终完整答复：不要再调用任何工具，也不要再请求获取新信息；"
        "若部分信息确实未能获取到，请在答复中如实说明。"
        "【硬性要求】你的最终答复不能为空——即使信息不完整、任务未全部完成，"
        "也必须输出你能给出的最佳结果（如按题目要求生成完整的 JSON / 计划 / 报告）；"
        "“信息不足”“无法完成”不能作为空回复的理由。"
    )

    #: [收尾答案·重试] 第一次收尾请求返回空时的第二次指令：换一个角度激发输出。
    #: 评测实测：长工具链（10+ 次调用）后部分模型会对「总结」类指令返回空
    #: content；换用「已获得什么信息」的具体化指令重试一次可显著恢复产出。
    FINALIZE_RETRY_INSTRUCTION = (
        "（系统提示）你上一条回复为空。请务必输出内容：若题目要求生成类结果"
        "（计划 / JSON / 报告 / 分析），请直接基于已有信息生成最佳版本并输出；"
        "否则用一段话列出你已完成的工作与已获得的关键信息。不要调用任何工具，"
        "不要输出空内容。"
    )

    #: [W7 收尾强化] 第三次收尾：配合极简消息（系统提示 + 任务 + 最近工具结果
    #: 摘要）使用。评测实测：长工具链（含拦截历史、12+ 次调用）后全量消息的
    #: 收尾请求返回空 content 的比例较高；把上下文压缩到「任务 + 工具结果摘要」
    #: 再要一次，成功率显著提高（消息体积小、响应快、不易超时）。
    FINALIZE_MINIMAL_INSTRUCTION = (
        "（系统提示）请基于以上任务要求与工具结果摘要，立即输出本任务的最终"
        "完整答复。若题目要求 JSON / 计划 / 报告等结构化结果，请直接输出完整"
        "内容本体；若题目要求把产物写入文件，请如实说明当前完成状态。"
        "不要调用任何工具，不要输出空内容。"
    )

    def _build_minimal_finalize_messages(
            self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """[W7 收尾强化] 构造极简收尾消息：系统提示（截断）+ 首条任务 + 最近工具结果摘要。

        保留要素：① 系统提示前 6000 字符（人设与作答背景）；② 首条 user 消息
        （评测任务与作答契约）；③ 最近 5 条工具结果（每条截断 600 字符）。
        丢弃要素：全部中间 assistant 文本、早期工具结果、拦截历史 —— 这些正是
        「长上下文拖慢 API、拦截历史挫伤模型输出意愿」的来源。
        """
        system_text: Optional[str] = None
        task_text: Optional[str] = None
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "system" and system_text is None:
                system_text = str(msg.get("content") or "")
            elif msg.get("role") == "user" and task_text is None:
                task_text = str(msg.get("content") or "")
        tool_summaries: List[str] = []
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "tool":
                text = str(msg.get("content") or "")[:600]
                tool_summaries.append(f"[{msg.get('name') or 'tool'}] {text}")
                if len(tool_summaries) >= 5:
                    break
        tool_summaries.reverse()
        out: List[Dict[str, Any]] = []
        if system_text:
            out.append({"role": "system", "content": system_text[:6000]})
        if task_text:
            out.append({"role": "user", "content": task_text})
        if tool_summaries:
            out.append({"role": "system",
                        "content": "【已获取的工具结果摘要】\n" + "\n---\n".join(tool_summaries)})
        return out

    # ── [W8-C] 产物落盘机械闸门 ──────────────────────────────────────────

    #: [W8-C] nudge 硬上限（保证不死循环）与每次放宽的步数预算。
    _DELIVERABLE_MAX_NUDGES = 2
    _DELIVERABLE_NUDGE_STEPS = 6

    #: [W11 拦截引导升级] 同类拦截（安全拦截 / 引擎直抓拦截）连续达到该次数 →
    #: 对话内文案升级为「停止试探」级警告（见 run() 工具执行段）。
    _BLOCK_ESCALATE_THRESHOLD = 3

    #: [W8-C] 从任务文本推导「必须实际落盘产物」的保守正则（按序尝试）。
    #: 路径字符集刻意收成 ASCII（``[\w.\-/]+`` 在 Python 3 里是 Unicode 语义，
    #: 会把紧跟路径的中文词一起吞进来 → 误判出并不存在的产物名）。
    _REQUIRED_OUTPUT_PATTERNS = (
        re.compile(r"实际写入\s+(output/[A-Za-z0-9._\-/]+)"),
        re.compile(r"写入\s+(output/[A-Za-z0-9._\-/]+\.(?:json|md|txt|csv|html?|xlsx?))"),
    )

    @classmethod
    def _parse_required_outputs(cls, text: str) -> List[str]:
        """[W8-C] 从任务文本保守解析必须实际落盘的 ``output/`` 产物路径。

        只认两种明确措辞（按序尝试、去重、最多 3 个）；匹配不到返回 ``[]``
        —— 闸门随即完全关闭（零行为变化）。任何异常一律返回 ``[]``。
        """
        try:
            text = str(text or "")
            if "output/" not in text:
                return []
            found: List[str] = []
            for pattern in cls._REQUIRED_OUTPUT_PATTERNS:
                for match in pattern.finditer(text):
                    path = match.group(1).strip().rstrip(".,;:，。；：、）)")
                    if path and path not in found:
                        found.append(path)
                    if len(found) >= 3:
                        return found
            return found
        except Exception:
            return []

    def _missing_required_outputs(self, required: List[str]) -> List[str]:
        """[W8-C] 返回**尚未真实落盘**的产物相对路径。

        以工作区根为基准判断存在性（工具写入即落在同一根下）；任何异常都
        返回 ``[]``（= 视为无缺失），闸门静默关闭，绝不因此拦住 run()。
        """
        try:
            if not required:
                return []
            root = Path(getattr(self.sandbox, "workspace_root", None)
                        or self.workspace_root or ".")
            missing: List[str] = []
            for rel in required:
                try:
                    if not (root / rel).exists():
                        missing.append(rel)
                except Exception:
                    continue
            return missing
        except Exception:
            return []

    @staticmethod
    def _deliverable_nudge_text(missing: List[str]) -> str:
        """[W8-C] 产物缺失时的补写指令（追加为 user 消息后重入主循环）。"""
        paths = "、".join(missing)
        return (
            "（系统提示·产物落盘闸门）本任务要求把结果实际写入以下文件，"
            f"但系统检测到它们**尚不存在**：{paths}。\n"
            "请**立即**调用 write_file 工具落盘，不要再继续检索或补充资料："
            "把你**当前已有**的最佳结果直接写入上述路径（数据不完整也要写，"
            "缺失字段用 null 或标注“未核实”，绝不留空文件）；"
            "若任务要求 JSON 就写合法 JSON。写完简要说明即可结束。"
            "**不要在回复里声称已写入而实际没写；继续检索而不落盘同样判为未完成。**"
        )

    def _autosave_deliverables(self, missing: List[str],
                               final_answer: str) -> None:
        """[W9] 闸门兜底：nudge 用尽后仍缺失时，把 ``final_answer`` 中**可解析的
        JSON** 机械落盘到缺失路径（内容完全来自模型输出，零伪造）。

        仅处理 ``.json`` 后缀产物；解析不出 JSON / 写入失败一律静默跳过——
        绝不因兜底动作把 run() 弄崩，也绝不写入非模型产出的内容。
        """
        try:
            text = str(final_answer or "").strip()
            if not text:
                return
            payload: Any = None
            try:
                payload = json.loads(text)
            except Exception:
                pass
            if payload is None:
                m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
                if m:
                    try:
                        payload = json.loads(m.group(1).strip())
                    except Exception:
                        pass
            if payload is None:
                starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
                if starts:
                    try:
                        payload, _ = json.JSONDecoder().raw_decode(text[min(starts):])
                    except Exception:
                        pass
            if payload is None:
                return
            root = Path(getattr(self.sandbox, "workspace_root", None)
                        or self.workspace_root or ".")
            saved: List[str] = []
            for rel in missing:
                p = root / rel
                if p.suffix.lower() != ".json":
                    continue
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
                    saved.append(rel)
                except Exception:
                    continue
            if saved:
                self._append_event(EVENT_TOOL_RESULT, {
                    "tool_call_id": None,
                    "name": "write_file",
                    "content": "deliverable_autosave: " + ", ".join(saved),
                    "kind": "deliverable_autosave",
                })
        except Exception:
            return

    def _recover_final_answer(self, messages: List[Dict[str, Any]],
                              last_assistant_text: str,
                              api_failed: bool = False) -> str:
        """[收尾答案] 步数耗尽 / 模型空回复时，尽量恢复出非空的最终答复。

        恢复顺序：
        1. 收尾请求链（禁用工具的纯文本请求，追加 :data:`FINALIZE_INSTRUCTION` 等指令）：
           - 正常路径：全量消息 FINALIZE → 全量消息 RETRY → **极简消息 MINIMAL**；
           - ``api_failed``（主循环 API 硬失败）：先试**极简消息**（体积小、请求快、
             网络瞬断后恢复概率不低），失败再用全量消息试一次 —— 旧实现在此
             直接放弃收尾，是空答案题的主要失分路径；
        2. 仍无内容时，回退「最后一条非空 assistant 文本」（模型边调工具边写的
           分析说明）；
        3. 都没有则返回空串 —— 绝不伪造答案。

        整个恢复过程尽力而为：任何异常（含收尾请求网络失败）都静默回退，
        绝不让收尾环节把已经跑完的 run() 弄崩。
        """
        content = ""
        if api_failed:
            content = self._finalize_request(
                self._build_minimal_finalize_messages(messages),
                self.FINALIZE_MINIMAL_INSTRUCTION)
            if not (content or "").strip():
                content = self._finalize_request(messages, self.FINALIZE_INSTRUCTION)
        else:
            content = self._finalize_request(messages, self.FINALIZE_INSTRUCTION)
            if not (content or "").strip():
                content = self._finalize_request(messages, self.FINALIZE_RETRY_INSTRUCTION)
            if not (content or "").strip():
                content = self._finalize_request(
                    self._build_minimal_finalize_messages(messages),
                    self.FINALIZE_MINIMAL_INSTRUCTION)
        if isinstance(content, str):
            # 模型可能仍模拟输出 <tool_call> 降级标签：取标签前文本，
            # 避免把工具调用语法当成答案回传。
            if "<tool_call>" in content:
                content = content.split("<tool_call>")[0].strip()
            if content.strip():
                self._display_final_answer(content)
                return content
        if last_assistant_text:
            self._display_final_answer(last_assistant_text)
            return last_assistant_text
        return ""

    def _finalize_request(self, messages: List[Dict[str, Any]],
                          instruction: str) -> str:
        """[收尾答案] 发一次「禁用工具」的收尾请求，返回 content（异常/空皆为空串）。"""
        try:
            tail = list(messages)
            tail.append({"role": "user", "content": instruction})
            data = self._call_llm(tail, allow_tools=False)
            try:
                self.runtime.record_usage((data or {}).get("usage"))
            except RunBudgetExceeded:
                return ""
            choice = ((data or {}).get("choices") or [{}])[0] or {}
            message = choice.get("message") or {}
            # [P2 修复·思维链标签泄漏] 收尾答案同样要过清洗（最终答复出口）
            return _strip_think_tags(message.get("content") or "")
        except Exception:
            return ""

    # ── [W10 JSON 交付自检] ──────────────────────────────────────────────

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """剥掉整体包裹的 markdown 代码围栏（格式归一化，不改内容）。"""
        t = (text or "").strip()
        if not t.startswith("```"):
            return t
        lines = t.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
        return stripped or t

    def _repair_json_answer(self, final_answer: str,
                            messages: Optional[List[Dict[str, Any]]] = None) -> str:
        """[W10 JSON 交付自检] 疑似 JSON 答案语法非法时，一次性修复（只修语法、不改内容）。

        评测实测（SEARCH-008）：模型输出的答案以 ``{`` 开头、内容完整，但对象里
        混入了无键名的裸字符串元素 → 整体非法 JSON → 判分的 json_schema 检查
        解析失败、整题失分。这类「内容正确、语法非法」的失分与检索/推理能力
        无关，可用一次低成本的**语法修复请求**挽回：

        * 仅在答案首字符为 ``{`` / ``[`` 且 ``json.loads`` 失败时触发；
        * 修复请求禁用工具，附上原答案与解析错误，指令明确要求**仅修复语法**、
          不得增删或改变任何实质内容；
        * 修复结果（剥代码围栏后）仍非法、或请求失败 → **保留原答案**（绝不伪造）；
        * 成功时返回修复版并记 ``json_answer_repair`` 事件（不重复展示）。

        成本：仅触发时多一次 LLM 调用（禁用工具、输出即答案，开销小）。
        """
        text = (final_answer or "").strip()
        if text[:1] not in ("{", "["):
            return final_answer
        try:
            json.loads(text)
            return final_answer
        except json.JSONDecodeError as exc:
            err = f"{exc.msg}（第 {exc.lineno} 行第 {exc.colno} 列，字符 {exc.pos}）"
        except Exception:
            return final_answer
        instruction = (
            "（系统提示）你上一条最终回复以 { 或 [ 开头，本应是完整 JSON，"
            f"但存在语法错误：{err}\n"
            "请**仅修复 JSON 语法**（补齐或修正括号、引号、逗号、键名等），"
            "**不得增删或改变任何实质内容**——所有事实、数字、链接、文字必须"
            "保持原样。直接输出修复后的完整 JSON 本体：不要任何解释，不要用 "
            "``` 代码块包裹。\n\n"
            "=== 你上一条回复的原文 ===\n" + text
        )
        context: List[Dict[str, Any]] = []
        for msg in (messages or []):
            if isinstance(msg, dict) and msg.get("role") == "system":
                context.append({"role": "system",
                                "content": str(msg.get("content") or "")[:6000]})
                break
        repaired = self._finalize_request(context, instruction)
        cand = self._strip_code_fence(repaired)
        if "<tool_call>" in cand:
            cand = cand.split("<tool_call>")[0].strip()
        if cand[:1] not in ("{", "["):
            return final_answer
        try:
            json.loads(cand)
        except Exception:
            return final_answer
        self._append_event(EVENT_TOOL_RESULT, {
            "tool_call_id": None,
            "name": "json_repair",
            "content": "json_answer_repair: 修复成功（仅语法，内容未改）",
            "kind": "json_answer_repair",
        })
        return cand

    # ── [W8] 流式请求基础设施 ────────────────────────────────────────────

    def _stream_timeout(self) -> float:
        """[W8] 流式请求的有效 socket 超时 = ``min(request_timeout, 90s)``。

        流式下该超时是**单次读超时**（每收到数据即重置），所以：
        ① 健康慢流（总时长 >100s、块持续到达）能成功；
        ② 完全停滞的流在 ~90s 内抛错，交给既有重试/退避逻辑。
        用户显式调低 ``request_timeout`` 时从严取小值。
        """
        try:
            val = float(self.request_timeout)
        except (TypeError, ValueError):  # pragma: no cover - 构造期已净化
            return _STREAM_STALL_TIMEOUT
        return min(val, _STREAM_STALL_TIMEOUT) if val > 0 else _STREAM_STALL_TIMEOUT

    #: [K8] overflow 分流的 body 特征词（HTTP 400/413 响应体命中即判定为
    #: 「上下文超长」——各家网关措辞不一，取低误报的通用短语）。
    _OVERFLOW_BODY_HINTS = (
        "context length", "context_length", "maximum context",
        "context window", "too long", "reduce the length",
        "exceeds the maximum", "max_tokens",
    )

    @classmethod
    def _classify_error_hint(cls, status: Any, err_text: str = "") -> Optional[str]:
        """[K8] 错误分流提示：``"overflow"`` / ``"auth"`` / None。

        独立于 llm_call 事件的 error_kind 遥测（遥测值逐字不变）；仅用于
        run 主循环的分流决策：
          - auth（401/403）：key 无效，重试必败 → 跳过收尾链；
          - overflow（400/413 且 body 含上下文长度特征）：强制压缩重试一次。
        """
        try:
            code = int(status)
        except (TypeError, ValueError):
            return None
        if code in (401, 403):
            return "auth"
        if code in (400, 413):
            body = str(err_text or "").lower()
            if any(t in body for t in cls._OVERFLOW_BODY_HINTS):
                return "overflow"
        return None

    def _call_llm(self, messages: List[Dict[str, Any]],
                  allow_tools: bool = True,
                  tools_subset: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """调用兼容 OpenAI tools 规范的模型 API。

        [W8] 默认走**流式**（``stream: true``）以绕开网关 ~60s 硬超时；回包统一
        重建成与非流式同形的 dict（见 ``llm_client._StreamAccumulator``）。

        ``allow_tools=False`` 时不携带 ``tools`` / ``tool_choice`` 字段 ——
        [收尾答案] 步数耗尽后的收尾请求专用：明确要求模型直接作答、不再规划
        新的工具调用（见 :meth:`_recover_final_answer`）。

        ``tools_subset`` 非空时只携带清单内工具的 schema（收尾兜底场景用）。

        [K4] 发送 / 分类重试 / SSE 解析已收敛进 ``llm_client.request_chat``
        （重试与退避公式逐字保持：max_retries=2，429/5xx 与网络类各自的
        退避+抖动，Retry-After 优先）。本方法只保留：payload 组装、
        spinner/on_open 观感、error_kind 遥测（too_large / http_400_downgrade /
        http_{code} / network / invalid_response / exception 逐字不变）与
        「失败返回 None」契约。
        """
        raw_base_url = self.config.get("base_url", "https://api.deepseek.com/v1")
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "deepseek-chat")

        # [W1 埋点] 每次 LLM 调用的耗时 / 上游真实 usage / 错误分类 → session jsonl
        # （llm_call 事件）。usage 只透传上游返回值，缺失即 None，**绝不伪造**。
        # [W7b 诊断] 附 prompt_chars / message_count：评测实测「60s 网关墙」型失败
        # 与请求内容无关（PLAN-002 第 1 次调用即 184s 全败），此埋点用于下轮区分
        # 「生成量」与「服务端拥塞」两个假设。
        started = time.monotonic()
        try:
            _prompt_chars = sum(len(str(m.get("content") or ""))
                                for m in messages if isinstance(m, dict))
        except Exception:
            _prompt_chars = -1

        def _emit_llm_call(ok: bool, error_kind: Optional[str] = None,
                           usage: Any = None, attempts: int = 1,
                           hint: Optional[str] = None) -> None:
            # [K8] 分流提示：成功即清空；失败由调用方显式给出（HTTP 类经
            # _emit_http_failure 依 body 分类），未给出时置 None（不残留上次）。
            self._last_llm_error_hint = None if ok else hint
            self._append_event(EVENT_LLM_CALL, {
                "model": model,
                "allow_tools": bool(allow_tools),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "attempts": int(attempts),
                "ok": bool(ok),
                "error_kind": error_kind,
                "usage": usage if isinstance(usage, dict) else None,
                "prompt_chars": _prompt_chars,
                "message_count": len(messages),
            })

        # [W8] 流式请求声明 ``Accept-Encoding: identity``：压缩流会被中间代理缓冲，
        # 且增量 SSE 无法边收边解压（实测探针以 identity 跑满 96.8s 成功）。
        # 若上游仍压缩，llm_client._parse_llm_response 会走整体读取 + 有上限
        # 解压的回退路径。
        tools_list = self.tool_registry.get_openai_tools(tools_subset) if allow_tools else []
        req = ChatRequest(
            messages=messages,
            model=model,
            temperature=self.config.get("temperature", 0.3),
            max_tokens=self.config.get("max_tokens", 4096),
            # [W8] 默认流式：绕开网关 ~60s 硬超时（非流式 60.5s 被掐断的实测根因）。
            stream=True,
            tools=tools_list or None,
            tool_choice="auto" if tools_list else None,
            # 尽力而为：网关支持时在末尾块回传真 usage；不支持则由 _post_chat 摘除重发。
            stream_options={"include_usage": True},
            timeout=self._stream_timeout(),
            api_key=api_key,
            base_url=raw_base_url,
            headers_extra={
                "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Agent/1.0",
                "Connection": "close",
                "Accept-Encoding": "identity",
            },
        )

        import threading

        stop_spinner = threading.Event()

        def spinner_task():
            if self.quiet:
                return
            if not sys.stdout.isatty():
                sys.stdout.write("  \033[96m*\033[0m \033[2m[考研私教正在审阅题干与规划工具调用...]\033[0m\n")
                sys.stdout.flush()
                return
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            idx = 0
            while not stop_spinner.is_set():
                frame = frames[idx % len(frames)]
                sys.stdout.write(f"\r  \033[96m{frame}\033[0m \033[2m[考研私教正在审阅题干与规划工具调用...]\033[0m")
                sys.stdout.flush()
                idx += 1
                time.sleep(0.08)
            sys.stdout.write("\r" + " " * 52 + "\r")
            sys.stdout.flush()

        spinner_thread = threading.Thread(target=spinner_task, daemon=True)
        spinner_thread.start()

        def _on_response_open() -> None:
            """响应头已到达：立刻停掉 spinner（保持与非流式时代一致的观感）。"""
            stop_spinner.set()
            spinner_thread.join(timeout=0.2)

        # [W7 收尾强化] 网络类瞬时故障重试：max_retries 1→2（共 3 次尝试）+
        # 指数退避 + 抖动。评测实测：长上下文下单次请求可慢至 100s+，60s 超时下
        # 双重超时后 api_failed 直接跳过收尾 → 空答案（19/50 题的失分主因之一）。
        # 多一次尝试 + 退避可显著降低此类失败；退避带抖动避免重试风暴同频。
        # [W7b 拥塞避让] 退避 0.5/1.5s→2/6s：实测失败呈「波动性拥塞」特征
        # （10 分钟桶失败率 0-41% 起伏、同题连败 6 分钟而同期他题成功），
        # 短退避的重试仍落在同一拥塞窗口内 → 三连败；拉长退避以错过窗口。
        # [K4] 上述公式已随请求逻辑收敛进 ``llm_client.compute_backoff``（逐字保持）。
        stats: Dict[str, Any] = {}

        def _stop_spinner() -> None:
            stop_spinner.set()
            spinner_thread.join(timeout=0.2)

        def _emit_http_failure(status: Any, err_msg: str) -> None:
            print(f"\n\033[91m[API 错误 {status}]: {err_msg}\033[0m\n")
            if self.step_callback:
                self.step_callback(f"❌ [API 响应异常 HTTP {status}]: {err_msg}")
            _emit_llm_call(False, f"http_{status}", attempts=stats.get("attempts", 1),
                           hint=self._classify_error_hint(status, err_msg))

        try:
            # [B1 同类·跳转泄漏 Bearer] 经 safe_urlopen 发送：SSRF 逐跳复核 +
            # 跨域剥离 Authorization。UnsafeURLError 由下方通用 except 收口。
            # [P2 修复] 响应读取/解压都有体积上限（解压炸弹防护）。
            resp_data = request_chat(
                req,
                max_retries=2,
                on_open=_on_response_open,
                stats=stats,
                sleep_fn=time.sleep,
                urlopen_fn=safe_urlopen,
                decompress_fn=decompress_limited,
            )
            _emit_llm_call(True, usage=resp_data.get("usage"),
                           attempts=stats.get("attempts", 1))
            return resp_data
        except LLMResponseTooLargeError:
            _stop_spinner()
            print("\n\033[91m[响应过大] 上游响应解压后超过安全体积上限，已拒绝处理。\033[0m\n")
            if self.step_callback:
                self.step_callback("❌ [响应过大] 上游响应解压后超过安全体积上限，已拒绝处理。")
            _emit_llm_call(False, "too_large")
            return None
        except LLMRetryExhausted as e:
            # 可重试故障重试耗尽：5xx/429 → http_{code}；网络类 → network
            _stop_spinner()
            if e.status is not None:
                _emit_http_failure(e.status, e.body or str(e))
            else:
                print(f"\n\033[91m[连接异常]: {e.last_error or e}\033[0m\n")
                _emit_llm_call(False, "network", attempts=stats.get("attempts", 1))
            return None
        except LLMDeterministicError as e:
            # 某些端点或反代对 tools、tool_choice、schema 敏感而报 400
            if e.kind == "tools_unsupported":
                _stop_spinner()
                if self.step_callback:
                    self.step_callback("⚡ [自动兼容] 检测到端点对工具调用敏感 (HTTP 400)，已平滑切换为纯文本对话模式...")
                _emit_llm_call(False, "http_400_downgrade")
                return self._call_llm_without_tools(messages)
            _stop_spinner()
            if e.status is not None:
                _emit_http_failure(e.status, e.body or str(e))
            else:
                # 空流/坏包（LLMEmptyStreamError 等）→ invalid_response（与旧
                # ValueError 分类一致）；其余非 HTTP 确定性错误 → exception。
                print(f"\n\033[91m[连接异常]: {e}\033[0m\n")
                _emit_llm_call(False, "invalid_response" if isinstance(e, ValueError) else "exception")
            return None
        except Exception as e:
            _stop_spinner()
            print(f"\n\033[91m[连接异常]: {e}\033[0m\n")
            _emit_llm_call(False, "invalid_response" if isinstance(e, ValueError) else "exception")
            return None

    def _call_llm_without_tools(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """降级纯文本请求 (针对不支持 tools 字段或对 payload 敏感的轻量/非标模型)

        [K4] 发送已收敛进 ``llm_client.request_chat``（``max_retries=0``，
        与旧实现一致的单次尝试）；保留：400+system/role 的「系统指令合并进
        首条消息」一次性重试、error_kind 遥测（http_{code} / invalid_response /
        exception）与返回 None 契约。
        """
        raw_base_url = self.config.get("base_url", "https://api.deepseek.com/v1")
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "deepseek-chat")

        # [W1 埋点] 降级路径同样落 llm_call 事件（allow_tools=False 可辨识）。
        started = time.monotonic()

        def _emit(ok: bool, error_kind: Optional[str] = None, usage: Any = None,
                  hint: Optional[str] = None) -> None:
            # [K8] 分流提示（与 _call_llm 同契约：成功清空、失败不残留）
            self._last_llm_error_hint = None if ok else hint
            self._append_event(EVENT_LLM_CALL, {
                "model": model,
                "allow_tools": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "attempts": 1,
                "ok": bool(ok),
                "error_kind": error_kind,
                "usage": usage if isinstance(usage, dict) else None,
            })

        def _make_req(cur_msgs: List[Dict[str, Any]]) -> ChatRequest:
            """[W8] 同 _call_llm：流式 + identity 编码（增量 SSE 不可边收边解压）。"""
            return ChatRequest(
                messages=cur_msgs,
                model=model,
                temperature=self.config.get("temperature", 0.3),
                max_tokens=self.config.get("max_tokens", 4096),
                # [W8] 收尾链同样走流式，避免 60s 网关墙把「最后一根救命稻草」掐断。
                stream=True,
                stream_options={"include_usage": True},
                timeout=self._stream_timeout(),
                api_key=api_key,
                base_url=raw_base_url,
                headers_extra={
                    "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Agent/1.0",
                    "Connection": "close",
                    "Accept-Encoding": "identity",
                },
            )

        def _send(cur_msgs: List[Dict[str, Any]]) -> Dict[str, Any]:
            """[B1 同类] 同 _call_llm：安全通道发送（调用方通用 except 收口）。
            [P2 修复] 读取与解压都加上体积上限（在 llm_client 内）。"""
            return request_chat(_make_req(cur_msgs), max_retries=0,
                                sleep_fn=time.sleep, urlopen_fn=safe_urlopen,
                                decompress_fn=decompress_limited)

        try:
            result = _send(messages)
            _emit(True, usage=result.get("usage") if isinstance(result, dict) else None)
            return result
        except LLMDeterministicError as e2:
            err_text = e2.body or str(e2)
            # 若某些特定模型拒绝 system 消息，将系统提示词合并进首个 user 消息重试
            if e2.status == 400 and ("system" in err_text.lower() or "role" in err_text.lower()):
                new_msgs = []
                sys_prefix = ""
                for m in messages:
                    if m.get("role") == "system":
                        sys_prefix += f"[系统指令: {m.get('content', '')}]\n\n"
                    else:
                        new_msgs.append(dict(m))
                if new_msgs and sys_prefix:
                    new_msgs[0]["content"] = sys_prefix + str(new_msgs[0].get("content", ""))
                try:
                    result = _send(new_msgs)
                    _emit(True, usage=result.get("usage") if isinstance(result, dict) else None)
                    return result
                except Exception:
                    pass
            if e2.status is not None:
                print(f"\n\033[91m[降级纯文本请求错误 HTTP {e2.status}]: {err_text}\033[0m\n")
                _emit(False, f"http_{e2.status}",
                      hint=self._classify_error_hint(e2.status, err_text))
            else:
                # 响应过大 / 空流等坏包（继承 ValueError）→ invalid_response，
                # 与旧实现的降级路径错误分类保持一致。
                print(f"\n\033[91m[纯文本对话异常]: {err_text}\033[0m\n")
                _emit(False, "invalid_response" if isinstance(e2, ValueError) else "exception")
            return None
        except LLMRetryExhausted as e2:
            if e2.status is not None:
                print(f"\n\033[91m[降级纯文本请求错误 HTTP {e2.status}]: {e2.body}\033[0m\n")
                _emit(False, f"http_{e2.status}",
                      hint=self._classify_error_hint(e2.status, e2.body or ""))
            else:
                print(f"\n\033[91m[纯文本对话异常]: {e2.last_error or e2}\033[0m\n")
                _emit(False, "exception")
            return None
        except Exception as exc:
            print(f"\n\033[91m[纯文本对话异常]: {exc}\033[0m\n")
            _emit(False, "invalid_response" if isinstance(exc, ValueError) else "exception")
            return None

    def _parse_fallback_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """从纯文本中解析 <tool_call>...</tool_call> 降级标签"""
        import re
        calls = []
        pattern = r"<tool_call>(.*?)</tool_call>"
        matches = re.findall(pattern, content, re.DOTALL)
        for idx, m in enumerate(matches):
            try:
                data = json.loads(m.strip())
                calls.append({
                    "id": f"call_fallback_{idx}_{int(time.time())}",
                    "type": "function",
                    "function": {
                        "name": data.get("name"),
                        "arguments": data.get("arguments", {})
                    }
                })
            except Exception:
                continue
        return calls

    def _display_final_answer(self, text: str):
        """流式打字机逐字输出给终端学员。

        [S3 改善] ``quiet=True``（GUI 场景）时不再往 stdout 打字机输出，避免控制台与
        界面各刷一份；有 ``stream_callback`` 时仍逐字符推送给调用方（GUI 的 chunk_signal）
        ，并且把「每次 1 个字符 + sleep 2ms」改为**按小片段推送**：原实现对上千字答案会
        产生上千次跨线程信号，GUI 主线程事件循环被刷爆，表现为界面卡顿。
        """
        if not text:
            return
        if self.stream_callback:
            step = 12                       # 每 12 字推送一次，兼顾手感与主线程压力
            for i in range(0, len(text), step):
                self.stream_callback(text[i:i + step])
                if not self.quiet:
                    sys.stdout.write(text[i:i + step])
                    sys.stdout.flush()
                    # [审计 2026-09-30 · 中影响] sleep 只服务终端打字机手感；
                    # quiet（GUI）场景不再制造 1000 字 ≈ 1.7s 的人为延迟。
                    time.sleep(0.02)
        elif not self.quiet:
            for char in text:
                sys.stdout.write(char)
                sys.stdout.flush()
                time.sleep(0.002)

        if not self.quiet:
            print()

    #: [W4 引用接线] 引用产出文件名（相对工作区）：收尾后把本轮答案中的
    #: URL 引用与工具证据核验结果写入，供评测适配器与下游如实读取。
    CITATIONS_FILE = ".memory/last_citations.json"

    #: 单次产出的引用条数上限（防 URL 洪水）。
    CITATIONS_MAX = 20

    @staticmethod
    def _extract_page_quote_refs(text: str) -> List[Dict[str, Any]]:
        """[W7b] 从答案 JSON 中提取 {page, quote} 型引用（本地资料场景）。

        评测实测：PDF 类任务的引用要求是「文件名 + 页码 + 原文引用」而非 URL
        （PDF-005 的 fields 每条含 page/quote），只认 URL 的提取器会让 citation
        维度成建制 0 分。这里剥代码块后解析 JSON，递归收集同时含页码与引文的
        条目（兼容中文键：页码/原文/引用；顶层 source_file 作为来源文件名）。
        """
        stripped = str(text or "").strip()
        if not stripped:
            return []
        # 剥 ```json ... ``` 代码块包裹
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        try:
            data = json.loads(stripped)
        except (ValueError, TypeError):
            return []
        source_file = ""
        if isinstance(data, dict):
            source_file = str(data.get("source_file")
                              or data.get("来源文件") or "").strip()
        out: List[Dict[str, Any]] = []

        def _walk(node: Any) -> None:
            if len(out) >= 50:
                return
            if isinstance(node, dict):
                page = node.get("page", node.get("页码"))
                quote = node.get("quote", node.get("原文",
                                                  node.get("引用")))
                if page is not None and isinstance(quote, str) and quote.strip():
                    out.append({"page": page, "quote": quote.strip(),
                                "source_file": source_file})
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for value in node:
                    _walk(value)

        _walk(data)
        return out

    def _emit_citations(self, final_answer: str,
                        messages: List[Dict[str, Any]]) -> None:
        """[W4 引用接线] 从最终答案提取引用，核验工具证据后落盘。

        诚实性约定（与评测适配器的 sources 提取同构）：
        * 只提取**答案中真实出现**的引用 —— 模型未写出的绝不补造；
        * ``supported=True`` 仅当该引用（URL / 引文）在本轮工具结果中出现过
          （有检索/读取证据）；否则如实标 ``supported=False`` 并写明原因
          （未核验 ≠ 伪造通过）；
        * 任何异常静默降级 —— 引用落盘绝不把已完成的 run() 弄崩。
        """
        try:
            import re as _re
            text = str(final_answer or "")
            if not text.strip():
                return
            url_re = _re.compile(r"https?://[^\s\"'<>（）()【】\[\]]+")
            evidence_text = "\n".join(
                str(m.get("content") or "") for m in messages
                if isinstance(m, dict) and m.get("role") == "tool")
            citations: List[Dict[str, Any]] = []
            seen = set()
            for match in url_re.finditer(text):
                url = match.group(0).rstrip(".,;:!?，。；：！？")
                if url in seen:
                    continue
                seen.add(url)
                # claim：URL 所在行的文本（折叠空白、截断 300 字符）
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end == -1:
                    line_end = len(text)
                claim = " ".join(text[line_start:line_end].split())[:300]
                in_evidence = url in evidence_text
                citations.append({
                    "citation_id": f"cit{len(citations) + 1}",
                    "claim": claim,
                    "source_ref": url,
                    "supported": bool(in_evidence),
                    "unsupported_reason": (
                        None if in_evidence
                        else "该 URL 未在本轮工具结果中出现（未经检索证据核验）"),
                    "judge": "agent_reported",
                })
                if len(citations) >= self.CITATIONS_MAX:
                    break
            # [W7b] 页码 + 原文型引用（本地资料场景：PDF / 镜像站 HTML）
            if len(citations) < self.CITATIONS_MAX:
                for ref in self._extract_page_quote_refs(text):
                    if len(citations) >= self.CITATIONS_MAX:
                        break
                    quote = str(ref.get("quote") or "")
                    dedup = (ref.get("source_file"), ref.get("page"), quote[:80])
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    # 引文核验：取前 40 字符在工具结果中查找（容忍尾部差异）
                    probe = quote[:40]
                    in_evidence = bool(probe) and probe in evidence_text
                    src_file = str(ref.get("source_file") or "").strip()
                    source_ref = (
                        f"{src_file} 第 {ref.get('page')} 页"
                        if src_file else f"第 {ref.get('page')} 页")
                    citations.append({
                        "citation_id": f"cit{len(citations) + 1}",
                        "claim": quote[:300],
                        "source_ref": source_ref,
                        "supported": bool(in_evidence),
                        "unsupported_reason": (
                            None if in_evidence
                            else "引文未在本轮工具结果中出现（未经原文核验）"),
                        "judge": "agent_reported",
                    })
            path = Path(self.sandbox.workspace_root) / self.CITATIONS_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(citations, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass
