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
import random
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from .sandbox import Sandbox
from .permissions import PermissionManager
from .tools_impl import ToolRegistry
from .context_engine import ContextEngine
from .memory import MemoryManager
from .hooks import HookManager
from .mcp_client import MCPClientManager
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

try:  # 网络访问安全与响应体积上限（双导入路径兼容）
    from net_guard import MAX_HTTP_RESPONSE_BYTES, decompress_limited, safe_urlopen
except ImportError:  # pragma: no cover
    from tools.net_guard import MAX_HTTP_RESPONSE_BYTES, decompress_limited, safe_urlopen  # type: ignore

try:  # [B3b] 压缩摘要头部常量的唯一实现处在 compaction（loop 不再保留私有副本）
    from .compaction import COMPACT_SUMMARY_PREFIX
except ImportError:  # pragma: no cover
    from tools.agent.compaction import COMPACT_SUMMARY_PREFIX  # type: ignore


def normalize_openai_url(base_url: str, endpoint: str = "chat/completions") -> str:
    """智能规范化 OpenAI 兼容接口地址 (自动补齐 /v1 容错，并兼容 /v1, /v2, /v3, /v4 等多版本端点与反代)"""
    import re
    b = (base_url or "https://api.deepseek.com/v1").strip().rstrip("/")
    ep = (endpoint or "chat/completions").strip().lstrip("/")
    if b.endswith("/" + ep) or b.endswith("/chat/completions"):
        return b
    # 若已显式包含 API 版本号路径（如 /v1, /v2, /v3, /v4 等）
    if re.search(r"/v\d+(?:/.*)?$", b):
        return f"{b}/{ep}"
    # 针对未带版本号的标准根代理或中转站，补充 /v1
    return f"{b}/v1/{ep}"


#: [B3a] ``COMPACT_SUMMARY_PREFIX``（从 compaction 导入）用于识别「本次
#: compact_context 是否真的发生了压缩」—— 只有真的插入了新摘要，才写 compact
#: 事件并更新 resume 用的摘要。[B3b] 私有副本已删除，避免两处字面量各自漂移。


# ── [W8] 流式（SSE）客户端 ──────────────────────────────────────────────
# 背景（KaoYanBench core50 实测 + 探针复现）：网关对**非流式**请求有 ~60s 硬超时
# （60.5s 被 RemoteDisconnected 掐断），12/50 题因此产出空答案；同内容改流式可完整
# 跑满 96.8s（首块 3.5s、2460 个 SSE 块）。故两个 LLM 调用默认改走 stream=true，
# 逐块累积重建出与非流式完全一致的 ``choices[0].message`` 结构。

#: 单次 ``read`` 的字节数。注意 ``HTTPResponse.read(n)`` 会**攒满 n 字节或 EOF**
#: 才返回（本机流式服务端实测），所以它是解析粒度而非时延保证；停滞检测靠的是
#: socket 单次 recv 超时（见 ``_STREAM_STALL_TIMEOUT``）。
_SSE_READ_CHUNK = 4096

#: [W8] 流式请求的「块间停滞超时」（秒）。流式下 socket 超时天然退化为单次读超时
#: （每收到数据即重置），因此它同时是①健康慢流的上限（块持续到达即可远超此时长）
#: 与②完全停滞流的判死阈值——90s 内无任何数据即抛错，交给既有重试/退避逻辑。
#: 有效超时 = ``min(request_timeout, 本值)``（用户显式调低 request_timeout 时从严）。
_STREAM_STALL_TIMEOUT = 90.0


class _ResponseTooLargeError(ValueError):
    """[W8] 上游响应（累积/解压后）超过安全体积上限。

    继承 ``ValueError``：``_call_llm_without_tools`` 的通用 except 把 ValueError
    归为 ``invalid_response``（与旧实现的降级路径错误分类保持一致）；``_call_llm``
    则在通用 except 之前单独捕获它并落 ``too_large``。
    """


class _StreamAccumulator:
    """[W8] 累积 OpenAI 兼容流式响应的 delta，重建非流式响应结构。

    重建目标（与旧非流式回包逐字段同形）::

        {"choices": [{"index": 0,
                      "message": {"role": "assistant", "content": "...",
                                  "tool_calls": [{"id", "type",
                                                  "function": {"name", "arguments"}}]},
                      "finish_reason": "stop"}],
         "usage": {...}  # 仅在网关回传时出现

    * ``content`` / ``reasoning_content``：按到达顺序拼接；
    * ``tool_calls``：**按 index 归并**——``function.arguments`` 分片拼接成完整
      JSON 字符串，``id`` / ``function.name`` 取首个非空值（部分网关每块重复发送）；
    * ``usage``：尽力而为，取自最后一个带 usage 的块（需请求侧 ``stream_options``）；
    * ``finish_reason``：透传上游所报值；上游未报时按 tool_calls/stop 兜底重建。
    """

    def __init__(self) -> None:
        self.content_parts: List[str] = []
        self.reasoning_parts: List[str] = []
        self.tool_calls: Dict[int, Dict[str, Any]] = {}
        self.finish_reason: Optional[str] = None
        self.usage: Optional[Dict[str, Any]] = None

    # -- 输入 --

    def feed_line(self, line) -> None:
        """喂入一行 SSE 文本（``data: {...}`` / ``data: [DONE]`` / 裸 JSON 容错）。"""
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8", errors="ignore")
            except Exception:  # pragma: no cover - decode 不会抛
                return
        text = str(line).strip()
        if not text:
            return
        if text.startswith("data:"):
            payload = text[len("data:"):].strip()
        elif text.startswith("{"):
            # 容错：个别网关省略 data: 前缀，直接给一行 JSON
            payload = text
        else:
            return
        if payload in ("[DONE]", "[done]"):
            return
        try:
            obj = json.loads(payload)
        except Exception:
            return
        self.feed(obj)

    def feed(self, obj: Any) -> None:
        """喂入一个已解析的流式块（dict）。"""
        if not isinstance(obj, dict):
            return
        usage = obj.get("usage")
        if isinstance(usage, dict):
            self.usage = usage
        choices = obj.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                # 兼容：少数网关在流式块里直接给 message 而非 delta
                delta = choice.get("message") if isinstance(choice.get("message"), dict) else None
            if not delta:
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                self.content_parts.append(content)
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                self.reasoning_parts.append(reasoning)
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    self._feed_tool_call(tc)

    def _feed_tool_call(self, tc: Any) -> None:
        if not isinstance(tc, dict):
            return
        try:
            idx = int(tc.get("index", 0))
        except (TypeError, ValueError):
            idx = 0
        slot = self.tool_calls.setdefault(
            idx, {"id": None, "type": None,
                  "function": {"name": None, "arguments": ""}})
        if tc.get("id") and not slot["id"]:
            slot["id"] = str(tc["id"])
        if tc.get("type") and not slot["type"]:
            slot["type"] = str(tc["type"])
        fn = tc.get("function")
        if not isinstance(fn, dict):
            return
        name = fn.get("name")
        if isinstance(name, str) and name and not slot["function"]["name"]:
            slot["function"]["name"] = name
        args = fn.get("arguments")
        if isinstance(args, str):
            slot["function"]["arguments"] += args
        elif isinstance(args, dict):
            # 容错：少数网关在流式块里直接给对象
            slot["function"]["arguments"] += json.dumps(args, ensure_ascii=False)

    # -- 输出 --

    def is_empty(self) -> bool:
        """是否什么都没累积到（用于判断「整段文本其实不是 SSE」）。"""
        return not (self.content_parts or self.tool_calls
                    or self.finish_reason or self.usage)

    def to_response(self) -> Dict[str, Any]:
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(self.content_parts),
        }
        if self.reasoning_parts:
            message["reasoning_content"] = "".join(self.reasoning_parts)
        if self.tool_calls:
            calls: List[Dict[str, Any]] = []
            for idx in sorted(self.tool_calls):
                slot = self.tool_calls[idx]
                fn = slot.get("function") or {}
                calls.append({
                    "id": slot.get("id") or f"call_stream_{idx}_{int(time.time() * 1000)}",
                    "type": slot.get("type") or "function",
                    "function": {
                        "name": fn.get("name") or "",
                        "arguments": fn.get("arguments") or "{}",
                    },
                })
            message["tool_calls"] = calls
        finish_reason = self.finish_reason
        if not finish_reason:
            finish_reason = "tool_calls" if self.tool_calls else "stop"
        resp: Dict[str, Any] = {"choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }]}
        if self.usage is not None:
            resp["usage"] = self.usage
        return resp


def _parse_sse_text(raw_text: str) -> Optional[Dict[str, Any]]:
    """把整段 SSE 文本（被代理缓冲/未标 Content-Type 的回包）重建为响应 dict。

    非 SSE（不含 ``data:`` 行或什么都没解析出）返回 ``None``，由调用方走原异常路径。
    """
    if "data:" not in raw_text:
        return None
    acc = _StreamAccumulator()
    for line in raw_text.splitlines():
        acc.feed_line(line)
    if acc.is_empty():
        return None
    return acc.to_response()


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
        """运行完整的 Agent Loop 交互循环"""
        # 把当前数学科目编码注入 ctx，便于 hooks.py 的考纲红线区分 math1/2/3/396
        study_plan = self.config.get("study_plan") or {}
        ctx = {
            "active_subject": self.config.get("active_subject", "math"),
            "math_key": study_plan.get("math_key", "math2") if self.config.get("active_subject") == "math" else None,
            "user_input": user_input,
        }
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

        # 1. 组装对话上下文
        sys_prompt = self.context_engine.build_system_prompt()
        
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
        # [收尾答案] 模型若每一步都在调工具，循环会因步数耗尽而退出、final_answer
        # 保持空串（评测实测 50/50 题如此）。用两个标记支撑收尾恢复：
        #   last_assistant_text —— 最后一条非空 assistant 文本（兜底回退用）；
        #   api_failed —— API 硬失败（含重试后仍失败）时置位：跳过收尾请求
        #   （网络已断，再发只会白等一次超时），但仍回退模型失败前留下的非空
        #   文本；两者皆无才返回空串，让 GUI/REPL 走各自的诊断提示。
        last_assistant_text = ""
        api_failed = False

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
                step += 1
                if self.step_callback and step == 1:
                    self.step_callback("⏳ [私教审阅中] 正在分析题干要求与教学规划...")
            
                # 向 LLM 请求（带 tools 参数）
                response_data = self._call_llm(active_messages)
                if not response_data:
                    api_failed = True
                    break

                choice = response_data.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content") or ""
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
                        if not allow:
                            if not self.quiet:
                                print(f"   \033[91m↳ [考纲红线拦截]: {hook_reason}\033[0m")
                            if self.step_callback:
                                self.step_callback(f"   ↳ [考纲红线拦截]: {hook_reason}")
                            exec_result = f"HookBlocked: {hook_reason}"
                        else:
                            # 执行工具
                            exec_result = self.tool_registry.execute_tool(fn_name, mod_args, interactive=interactive)
                            # 触发 PostToolUse 钩子 (自检与联动)
                            exec_result = self.hooks.trigger_post_tool_use(fn_name, mod_args, exec_result, ctx)

                        # 简短结果提示
                        res_preview = str(exec_result)[:80].replace("\n", " ")
                        is_err = "Error" in exec_result or "PermissionDenied" in exec_result or "HookBlocked" in exec_result
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

                        # [B3a] 记 tool_result 事件（parent 串到对应 tool_call）。
                        # 超长结果按 TOOL_RESULT_MAX_CHARS 截断存储并在 payload 标注；
                        # resume 重建不依赖 tool 事件，故截断不破坏 rebuild 语义。
                        result_text = str(exec_result)
                        self._append_event(EVENT_TOOL_RESULT, {
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "content": result_text[:TOOL_RESULT_MAX_CHARS],
                            "truncated": len(result_text) > TOOL_RESULT_MAX_CHARS,
                            "original_chars": len(result_text),
                        }, parent=call_event_id)

                    # 工具回包可能包含大文件或多轮结果，在循环内动态防爆压缩
                    before_compact = list(active_messages)
                    active_messages = self.context_engine.compact_context(active_messages, hook_manager=self.hooks)
                    self._log_compact_if_happened(before_compact, active_messages)

                    # 继续下一轮循环，让 LLM 拿到工具结果进行最终综合分析
                    continue

                # ── 情形 B: 模型输出最终答案 (Final Answer) ──
                final_answer = content
                # 打字机流式输出给学员
                self._display_final_answer(final_answer)
                break

            # 3.5 [收尾答案] 步数耗尽 / 模型空回复 → 再要一次「禁用工具的最终答复」。
            # [W7 收尾强化] API 硬失败时**不再跳过收尾**：网络可能只是瞬断，先试
            # 一次低成本的极简收尾（短消息、快请求），失败再试全量——原来直接跳过
            # 是空答案题（19/50）的主要失分路径（见 _recover_final_answer）。
            if not final_answer:
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
            choice = ((data or {}).get("choices") or [{}])[0] or {}
            message = choice.get("message") or {}
            return message.get("content") or ""
        except Exception:
            return ""

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

    def _consume_sse(self, resp) -> Dict[str, Any]:
        """[W8] 逐块读取 SSE 响应体并重建响应 dict。

        读循环里任何网络异常（RemoteDisconnected / 读超时 / IncompleteRead）都会
        向上抛出 → 被 ``_call_llm`` 的既有网络异常分支捕获并重试；已累积的半截
        内容一律丢弃，绝不把残缺流当成功回包（不伪造）。
        """
        acc = _StreamAccumulator()
        buf = b""
        total = 0
        while True:
            piece = resp.read(_SSE_READ_CHUNK)
            if not piece:
                break
            total += len(piece)
            if total > MAX_HTTP_RESPONSE_BYTES:
                raise _ResponseTooLargeError()
            buf += piece
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                acc.feed_line(line)
        if buf:
            acc.feed_line(buf)
        if acc.is_empty():
            # 空流 / 全是无法解析的噪声 → 如实报错走重试，绝不伪造成「模型空回复」。
            raise ValueError("上游流式回包为空或无法解析（未收到任何有效 data 块）")
        return acc.to_response()

    def _parse_llm_response(self, resp, url: str) -> Dict[str, Any]:
        """[W8] 解析一次 chat/completions 回包（流式 SSE 优先，JSON 回退）。

        * ``Content-Type: text/event-stream``（且未压缩）→ 增量解析 SSE；
        * 其余一律按**原非流式方式**整体读取：解压 → HTML 检查 → ``json.loads``；
          若 JSON 解析失败但正文含 ``data:`` 行，再尝试按被代理缓冲的 SSE 文本
          重建（网关漏标 Content-Type 时的容错）。
        """
        headers_obj = getattr(resp, "headers", None)
        ctype = ""
        enc = ""
        if headers_obj is not None and hasattr(headers_obj, "get"):
            try:
                ctype = str(headers_obj.get("Content-Type", "") or "").lower()
            except Exception:
                ctype = ""
            try:
                enc = str(headers_obj.get("Content-Encoding", "") or "").lower()
            except Exception:
                enc = ""
        # 压缩过的流无法边收边解压 → 只能走整体读取（见下方注释）。
        compressed = enc.strip() not in ("", "identity")
        if "text/event-stream" in ctype and not compressed:
            return self._consume_sse(resp)
        # [P2 修复] 读取与解压都加上体积上限（解压炸弹防护）。
        # [W8 实测] HTTPResponse.read(n) 对 Content-Length / chunked 两种流式回包
        # 都是「攒满 n 字节或 EOF 才返回」（本机流式服务端验证），因此这里单次
        # 读取即可拿到完整回包；增量解析只走上面的 event-stream 分支。
        raw_bytes = resp.read(MAX_HTTP_RESPONSE_BYTES)
        raw_bytes, _truncated = decompress_limited(raw_bytes, enc)
        if _truncated:
            raise _ResponseTooLargeError()
        raw_text = raw_bytes.decode("utf-8", errors="ignore").strip()
        if raw_text.startswith("<!doctype html") or raw_text.startswith("<html"):
            raise ValueError(f"服务端返回了网页 HTML 而非 API JSON 数据 (请求地址: {url})，请检查 base_url 配置")
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            data = _parse_sse_text(raw_text)
            if data is None:
                raise
        if not isinstance(data, dict):
            raise ValueError("上游返回了非对象 JSON，无法作为 chat/completions 回包解析")
        return data

    def _post_chat(self, url: str, headers: Dict[str, str],
                   payload: Dict[str, Any], timeout: float,
                   on_open: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
        """[W8] 发一次 chat/completions 请求并解析回包。

        ``stream_options``（``include_usage``）属尽力而为：网关若因此报 400，
        **摘掉该字段立即重发一次**（不消耗网络重试次数、不阻断整个调用）。
        其余异常原样抛出，由调用方的既有 except 链分类处理。
        """
        for round_no in (0, 1):
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            try:
                with safe_urlopen(req, timeout=timeout) as resp:
                    if on_open:
                        on_open()
                    return self._parse_llm_response(resp, url)
            except urllib.error.HTTPError as e:
                if round_no == 0 and e.code == 400 and "stream_options" in payload:
                    try:
                        e.read(MAX_HTTP_RESPONSE_BYTES)
                    except Exception:
                        pass
                    # 就地摘除：后续（重试）attempt 不再重复踩同一个 400
                    payload.pop("stream_options", None)
                    continue
                raise
        # 理论不可达：round 0 要么 return、要么 continue、要么 raise。
        raise RuntimeError("stream_options 降级重试未能收敛")  # pragma: no cover

    def _call_llm(self, messages: List[Dict[str, Any]],
                  allow_tools: bool = True,
                  tools_subset: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """调用兼容 OpenAI tools 规范的模型 API。

        [W8] 默认走**流式**（``stream: true``）以绕开网关 ~60s 硬超时；回包统一
        重建成与非流式同形的 dict（见 :class:`_StreamAccumulator`）。

        ``allow_tools=False`` 时不携带 ``tools`` / ``tool_choice`` 字段 ——
        [收尾答案] 步数耗尽后的收尾请求专用：明确要求模型直接作答、不再规划
        新的工具调用（见 :meth:`_recover_final_answer`）。

        ``tools_subset`` 非空时只携带清单内工具的 schema（收尾兜底场景用）。
        """
        raw_base_url = self.config.get("base_url", "https://api.deepseek.com/v1")
        url = normalize_openai_url(raw_base_url, "chat/completions")
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
                           usage: Any = None, attempts: int = 1) -> None:
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
        # 若上游仍压缩，_parse_llm_response 会走整体读取 + 有上限解压的回退路径。
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Agent/1.0",
            "Connection": "close",
            "Accept-Encoding": "identity"
        }

        tools_list = self.tool_registry.get_openai_tools(tools_subset) if allow_tools else []
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.config.get("temperature", 0.3),
            "max_tokens": self.config.get("max_tokens", 4096),
            # [W8] 默认流式：绕开网关 ~60s 硬超时（非流式 60.5s 被掐断的实测根因）。
            "stream": True,
            # 尽力而为：网关支持时在末尾块回传真 usage；不支持则由 _post_chat 摘除重发。
            "stream_options": {"include_usage": True},
        }
        if tools_list:
            payload["tools"] = tools_list
            payload["tool_choice"] = "auto"

        _timeout = self._stream_timeout()

        import threading
        import socket
        import http.client

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
        max_retries = 2
        _backoffs = (2.0, 6.0)
        for attempt in range(max_retries + 1):
            # 内层循环只服务一件事：stream_options 被网关 400 拒绝时摘字段重发
            # （不消耗网络重试次数）。其余分支通过 break 落回外层 for 的下一次尝试。
            while True:
                try:
                    # [B1 同类·跳转泄漏 Bearer] 经 safe_urlopen 发送：SSRF 逐跳复核 +
                    # 跨域剥离 Authorization。UnsafeURLError 由下方通用 except 收口。
                    # [P2 修复] 响应读取/解压都有体积上限（解压炸弹防护），
                    # 该逻辑随 [W8] 统一收敛进 _parse_llm_response。
                    resp_data = self._post_chat(
                        url, headers, payload, _timeout, on_open=_on_response_open)
                    _emit_llm_call(True, usage=resp_data.get("usage"), attempts=attempt + 1)
                    return resp_data
                except _ResponseTooLargeError:
                    stop_spinner.set()
                    spinner_thread.join(timeout=0.2)
                    print("\n\033[91m[响应过大] 上游响应解压后超过安全体积上限，已拒绝处理。\033[0m\n")
                    if self.step_callback:
                        self.step_callback("❌ [响应过大] 上游响应解压后超过安全体积上限，已拒绝处理。")
                    _emit_llm_call(False, "too_large")
                    return None
                except urllib.error.HTTPError as e:
                    err_msg = e.read(MAX_HTTP_RESPONSE_BYTES).decode("utf-8", errors="ignore")
                    err_low = err_msg.lower()
                    # 某些端点或反代对 tools、tool_choice、schema 敏感而报 400
                    if e.code == 400 and (
                        "tool" in err_low
                        or "function" in err_low
                        or "support" in err_low
                        or "param" in err_low
                        or "extra" in err_low
                        or "unknown" in err_low
                        or "invalid" in err_low
                    ):
                        stop_spinner.set()
                        spinner_thread.join(timeout=0.2)
                        if self.step_callback:
                            self.step_callback("⚡ [自动兼容] 检测到端点对工具调用敏感 (HTTP 400)，已平滑切换为纯文本对话模式...")
                        _emit_llm_call(False, "http_400_downgrade")
                        return self._call_llm_without_tools(messages)
                    # [W7 收尾强化] 5xx / 429 属瞬时故障（网关抖动 / 限流），退避后重试
                    # [W7b 拥塞避让] 退避 1/3s→2/6s（与网络类退避同量级），错过拥塞窗口
                    if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                        time.sleep(2.0 + attempt * 4.0 + random.random() * 1.0)
                        break       # 落回外层 for → 下一次尝试（等价于原 continue）
                    stop_spinner.set()
                    spinner_thread.join(timeout=0.2)
                    print(f"\n\033[91m[API 错误 {e.code}]: {err_msg}\033[0m\n")
                    if self.step_callback:
                        self.step_callback(f"❌ [API 响应异常 HTTP {e.code}]: {err_msg}")
                    _emit_llm_call(False, f"http_{e.code}", attempts=attempt + 1)
                    return None
                except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionResetError, http.client.RemoteDisconnected) as e:
                    if attempt < max_retries:
                        time.sleep(_backoffs[attempt] + random.random() * 0.5)
                        break       # 落回外层 for → 下一次尝试（等价于原 continue）
                    stop_spinner.set()
                    spinner_thread.join(timeout=0.2)
                    print(f"\n\033[91m[连接异常]: {e}\033[0m\n")
                    _emit_llm_call(False, "network", attempts=attempt + 1)
                    return None
                except Exception as e:
                    stop_spinner.set()
                    spinner_thread.join(timeout=0.2)
                    print(f"\n\033[91m[连接异常]: {e}\033[0m\n")
                    _emit_llm_call(False, "invalid_response" if isinstance(e, ValueError) else "exception")
                    return None
        # [审查 P6] 显式返回：循环体所有路径均已 return/break 到外层，
        # 此处理论上不可达（max_retries=2 时第三圈必在 except 分支返回）；
        # 补显式 None 消除静态检查的隐式返回告警（RET503）。
        return None

    def _call_llm_without_tools(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """降级纯文本请求 (针对不支持 tools 字段或对 payload 敏感的轻量/非标模型)"""
        raw_base_url = self.config.get("base_url", "https://api.deepseek.com/v1")
        url = normalize_openai_url(raw_base_url, "chat/completions")
        api_key = self.config.get("api_key", "").strip()
        model = self.config.get("model", "deepseek-chat")

        # [W8] 同 _call_llm：流式请求 + identity 编码（增量 SSE 不可边收边解压）。
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Agent/1.0",
            "Connection": "close",
            "Accept-Encoding": "identity"
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.config.get("temperature", 0.3),
            "max_tokens": self.config.get("max_tokens", 4096),
            # [W8] 收尾链同样走流式，避免 60s 网关墙把「最后一根救命稻草」掐断。
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        _timeout = self._stream_timeout()

        # [W1 埋点] 降级路径同样落 llm_call 事件（allow_tools=False 可辨识）。
        started = time.monotonic()

        def _emit(ok: bool, error_kind: Optional[str] = None, usage: Any = None) -> None:
            self._append_event(EVENT_LLM_CALL, {
                "model": model,
                "allow_tools": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "attempts": 1,
                "ok": bool(ok),
                "error_kind": error_kind,
                "usage": usage if isinstance(usage, dict) else None,
            })

        def _send(p_data):
            """[W8] 与 _call_llm 共用 _post_chat：流式解析 + stream_options 400 降级。"""
            # [B1 同类] 同 _call_llm：安全通道发送（调用方通用 except 收口）。
            # [P2 修复] 同 _call_llm：读取与解压都加上体积上限（在 _parse_llm_response 内）。
            return self._post_chat(url, headers, p_data, _timeout)

        try:
            result = _send(payload)
            _emit(True, usage=result.get("usage") if isinstance(result, dict) else None)
            return result
        except urllib.error.HTTPError as e2:
            e2_err = e2.read().decode("utf-8", errors="ignore")
            # 若某些特定模型拒绝 system 消息，将系统提示词合并进首个 user 消息重试
            if e2.code == 400 and ("system" in e2_err.lower() or "role" in e2_err.lower()):
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
                    result = _send({"model": model, "messages": new_msgs,
                                    "temperature": self.config.get("temperature", 0.3),
                                    "stream": True,
                                    "stream_options": {"include_usage": True}})
                    _emit(True, usage=result.get("usage") if isinstance(result, dict) else None)
                    return result
                except Exception:
                    pass
            print(f"\n\033[91m[降级纯文本请求错误 HTTP {e2.code}]: {e2_err}\033[0m\n")
            _emit(False, f"http_{e2.code}")
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
