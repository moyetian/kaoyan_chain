# -*- coding: utf-8 -*-
"""
考研学习链 · 统一大语言模型客户端 (LLM Client)
提供极简、高鲁棒的 OpenAI 兼容端点调用与模型探查功能，零第三方重度依赖。
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

_LOG = logging.getLogger(__name__)


_ERROR_SECRET_RE = re.compile(
    r"(?i)(bearer\s+|(?:authorization|api[_-]?key|access[_-]?token|token|secret|password)"
    r"\s*[\"']?\s*[:=]\s*[\"']?\s*)"
    r"[A-Za-z0-9._~+/=-]{8,}")


def _redact_error_text(text: str) -> str:
    """错误体进入异常/日志前去掉常见凭证值，保留字段名便于诊断。"""
    return _ERROR_SECRET_RE.sub(lambda m: f"{m.group(1)}[REDACTED]", str(text or ""))

ROOT = resolve_workspace_root(__file__)

try:  # 解压体积上限与安全网络访问（双导入路径兼容）
    from net_guard import (  # noqa: E402
        MAX_DECOMPRESSED_BYTES,
        MAX_HTTP_RESPONSE_BYTES,
        TRUNCATION_MARKER,
        UnsafeURLError,
        decompress_limited,
        safe_urlopen,
        zlib_limited,
    )
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    from tools.net_guard import (  # type: ignore
        MAX_DECOMPRESSED_BYTES,
        MAX_HTTP_RESPONSE_BYTES,
        TRUNCATION_MARKER,
        UnsafeURLError,
        decompress_limited,
        safe_urlopen,
        zlib_limited,
    )


def _llm_urlopen(req: urllib.request.Request, timeout: float = 12.0):
    """LLM 请求专用安全通道（B1 修复）。

    经 ``net_guard.safe_urlopen`` 发送：初始 URL 做 SSRF 校验（fail-closed，
    拦回环/私网/保留地址并 pin DNS 防重绑定），每次 3xx 跳转逐跳复核，
    跨主机跳转剥离 Authorization（防恶意 base_url 用 302 收割 API Key）。
    体积上限与解压保护仍由调用方的 ``resp.read(MAX_...)`` +
    ``_decompress_response_bytes`` 承担。
    """
    return safe_urlopen(req, timeout=timeout)

# 确保在各种导入路径与 pytest mock 环境下 tools.llm_client 与 llm_client 指向同一模块对象
_MODULE = sys.modules[__name__]
sys.modules.setdefault("tools.llm_client", _MODULE)
sys.modules.setdefault("llm_client", _MODULE)
sys.modules["tools.llm_client"] = _MODULE
sys.modules["llm_client"] = _MODULE

# [修复] 别名必须**同时挂到父包属性**上，否则 mock.patch("tools.llm_client.X") 会崩。
#
# 现场（公开副本 CI，Python 3.10 实测 16 个用例变红）：
#   tools/gui/services/settings.py 的兜底导入顺序是 `from llm_client import ...`
#   在前、`from tools.llm_client import ...` 在后；而不少测试会把 ``tools/``
#   放进 sys.path，于是本模块**先以顶层名 llm_client 完成导入**。CPython 只在
#   ``_find_and_load`` 真正执行时才把子模块挂到父包属性上，这里是模块体内手工
#   写 sys.modules，父包属性从未被赋值；此后 ``from tools.llm_client import X``
#   命中 sys.modules 直接返回，也不会补挂。
#   最终 ``sys.modules`` 里明明有 tools.llm_client，``getattr(tools, "llm_client")``
#   却是 AttributeError —— unittest.mock 的 ``_dot_lookup`` 正是「先 getattr、
#   失败再 __import__、再 getattr」，第二步命中缓存后依旧拿不到属性，于是 patch
#   抛 AttributeError。（3.11+ 的 mock 改走 sys.modules 回退，故不复现。）
try:  # pragma: no cover - 防御性：任何异常都不得影响正常导入
    _parent = sys.modules.get("tools")
    if _parent is None:
        import importlib

        _parent = importlib.import_module("tools")
    if getattr(_parent, "llm_client", None) is not _MODULE:
        setattr(_parent, "llm_client", _MODULE)
except Exception:
    pass


def _decompress_response_bytes(raw_bytes: bytes, headers: Any = None) -> str:
    """智能解压 HTTP 响应或错误载荷（支持 gzip, deflate, brotli 及 magic bytes 探测），并解码为文本字符串。

    支持：
    1. Content-Encoding: gzip 或以 \x1f\x8b 魔数开头的 GZIP 流；
    2. Content-Encoding: deflate 或标准 zlib 检验/解压，若失败则尝试 raw deflate (-zlib.MAX_WBITS)；
    3. Content-Encoding: br / brotli（若可用）；
    4. 纯文本解码：utf-8 优先，gbk 回退，兜底 errors="replace"。

    [P2 修复] 解压全部改为**带体积上限**（``net_guard.decompress_limited``）：
    旧实现直接用 ``gzip.decompress`` / ``zlib.decompress`` / ``brotli.decompress``，
    几十 KB 的「解压炸弹」可膨胀成几十 MB 直接撑爆内存（实测 30KB → 31MB 无拦截）。
    超限时截断并追加 ``TRUNCATION_MARKER``。
    """
    if not raw_bytes:
        return ""
    if isinstance(raw_bytes, str):
        return raw_bytes

    enc = ""
    if headers is not None:
        try:
            enc = (getattr(headers, "get", lambda *_: "")("Content-Encoding") or "").lower()
        except Exception:
            pass

    decompressed: bytes = raw_bytes
    truncated = False

    # 1~3. gzip / deflate / brotli 检测与**带限**解压（含 magic bytes 嗅探）
    decompressed, truncated = decompress_limited(decompressed, enc)

    # 4. 文本解码：utf-8 -> raw deflate fallback -> gbk -> utf-8 errors="replace"
    try:
        text = decompressed.decode("utf-8")
    except UnicodeDecodeError:
        text = None
        try:
            import zlib
            # require_eof=True：raw deflate 是最后兜底，必须严格判定，
            # 否则「非法字节恰好被 decompressobj 静默解成空串」会被误判为成功。
            raw2, tr2 = zlib_limited(
                decompressed, -zlib.MAX_WBITS, MAX_DECOMPRESSED_BYTES, require_eof=True)
            text = raw2.decode("utf-8")
            truncated = truncated or tr2
        except Exception:
            text = None
        if text is None:
            try:
                text = decompressed.decode("gbk")
            except UnicodeDecodeError:
                text = decompressed.decode("utf-8", errors="replace")
    return text + TRUNCATION_MARKER if truncated else text


def normalize_openai_url(base_url: str, endpoint: str = "chat/completions") -> str:
    """智能规范化 OpenAI 兼容接口地址。
    兼容 /v1, /v2, /v3, /v4, /v1beta/openai 等端点及反代。
    """
    b = (base_url or "https://api.deepseek.com/v1").strip().rstrip("/")
    ep = (endpoint or "chat/completions").strip().lstrip("/")
    # 如果给定的 URL 已经以 chat/completions 结尾，但我们请求的是 models 或其他 endpoint
    if b.endswith("/chat/completions"):
        if ep == "chat/completions":
            return b
        b = b[:-len("/chat/completions")].rstrip("/")

    if b.endswith("/" + ep):
        return b
    if re.search(r"/v\d+[a-z]*(?:/.*)?$", b) or b.endswith("/openai"):
        return f"{b}/{ep}"
    return f"{b}/v1/{ep}"


# ══════════════════════════════════════════════════════════════════════
# [K4] 统一 LLM 出口：结构化异常 / 错误分类 / 退避 / 请求
# ══════════════════════════════════════════════════════════════════════
# 收敛前全仓并存 6 套 LLM HTTP 客户端（llm_client.chat_completion /
# agent.loop._call_llm / cli.agent.engine.stream_chat / open_grader /
# vision_solver / study_planner），退避公式与错误分类各自为政。
# 本段把「发请求 + 重试分类 + SSE 解析」收敛为单一实现，各调用方只保留
# 自己的**展示与降级策略**（spinner、error_kind 遥测、400 自适应 payload、
# 空回复重试等），行为契约逐字保持（详见各调用点的注释）。

# ── [R3 波动收敛] 采样温度：稳定优先 vs 对话 ───────────────────────────
#: 对话链路（私教讲题、Agent 主循环、收尾链）的默认温度：保留多样性，
#:但**必须显式声明**（不得依赖调用方忘传导致的隐式 0.3）。
DIALOGUE_TEMPERATURE = 0.3
#: 稳定性优先链路（判卷 open_grader、采分点补全、切片识别、抽取/分类类
#: 任务）的温度：**关闭采样**。
#:
#: 口径依据（业界通行）：抽取 / 分类 / 翻译 / 代码生成这类「输入相同则应得
#: 到相同输出」的任务，要么关采样（temperature=0），要么固定全部随机因素
#:（seed + top_p=1）；否则输出不可复现，轮间对比失去可比性——同一份材料
#: 两次抽取得到不同采分点，判分与统计都会被噪声污染。对话/创作类则相反：
#: 关采样会导致措辞僵硬重复，故保留低温度多样性。
STABLE_TEMPERATURE = 0.0

#: [R3 波动收敛] 单次请求的默认输出上限（token）。三条链路（Agent 主循环 /
#: Agent 收尾链 / CLI 流式）此前不一致：CLI 非Agent 路径**根本不传**
#: ``max_tokens``，由上游自行决定输出上限 → 同一操作在两条路径上的输出长度
#: 结构性不同（R3 实测 8998 → 4927 字符，−45%）。此处定一份，两侧共用。
DEFAULT_MAX_TOKENS = 4096


class LLMError(Exception):
    """LLM 调用错误基类。

    统一携带（全部可选，缺省 None）：
      * ``status``      —— HTTP 状态码（网络类异常无）；
      * ``kind``        —— 语义分类（http_retryable / network / auth / not_found /
                           bad_request / tools_unsupported / too_large / empty_stream）；
      * ``retry_after`` —— 网关 Retry-After 解析值（秒）；
      * ``last_error``  —— 底层原始异常或上一次错误；
      * ``body``        —— HTTP 错误响应体（已解压解码的文本，供调用方嗅探）。
    """

    def __init__(self, message: str = "", *, status: Optional[int] = None,
                 kind: Optional[str] = None, retry_after: Optional[float] = None,
                 last_error: Optional[BaseException] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.retry_after = retry_after
        self.last_error = last_error
        self.body = body


class LLMRetryableError(LLMError):
    """可重试的瞬时故障（网络抖动 / 429 限流 / 5xx 网关抖动）。"""


class LLMDeterministicError(LLMError):
    """确定性失败：重试无意义（鉴权 / 参数 / 工具不支持 / 端点不存在…）。"""


class LLMResponseTooLargeError(LLMDeterministicError, ValueError):
    """上游响应（累积/解压后）超过安全体积上限 —— 确定性，绝不重试。

    同时继承 ``ValueError``：沿用旧 ``agent.loop._ResponseTooLargeError`` 的
    设计（``_call_llm_without_tools`` 的通用 except 把 ValueError 归为
    ``invalid_response``，与降级路径的历史错误分类一致）。
    """


class LLMEmptyStreamError(LLMDeterministicError, ValueError):
    """上游流式回包为空或无法解析（确定性坏包，不伪造成「模型空回复」）。

    同时继承 ``ValueError``：``agent.loop`` 的既有分类把 ValueError 归为
    ``invalid_response``（与旧 ``_consume_sse`` 抛 ValueError 的语义一致）。
    """


class LLMRetryExhausted(LLMRetryableError):
    """可重试故障在重试次数耗尽后仍失败（``last_error`` 为最后一次底层错误）。"""


#: 可重试的 HTTP 状态码（408/429 为限流/超时；409/425 沿用 open_grader 既有集合）
_HTTP_RETRYABLE_STATUS = (408, 409, 425, 429)

#: 400 且报错体含以下关键词 → 判定为「端点/模型不支持 tools 字段」的确定性错误
_TOOLS_UNSUPPORTED_KEYWORDS = (
    "tool", "function", "support", "param", "extra", "unknown", "invalid",
)


def classify_http_error(status: Any, body: Any = "") -> Tuple[bool, str]:
    """按 HTTP 状态码与错误体文本分类 → ``(retryable, kind)``。

    * 429/408（及 409/425）与全部 5xx → 可重试 ``http_retryable``；
    * 400 且报错体含 tools/参数类关键词 → 不可重试 ``tools_unsupported``
      （调用方据此降级为纯文本对话）；
    * 401/403 → ``auth``；404 → ``not_found``；其余 4xx → ``bad_request``。
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False, "unknown"
    text = str(body or "").lower()
    if code in _HTTP_RETRYABLE_STATUS or code >= 500:
        return True, "http_retryable"
    if code == 400 and any(k in text for k in _TOOLS_UNSUPPORTED_KEYWORDS):
        return False, "tools_unsupported"
    if code in (401, 403):
        return False, "auth"
    if code == 404:
        return False, "not_found"
    return False, "bad_request"


def parse_retry_after(headers: Any) -> Optional[float]:
    """解析响应头 ``Retry-After``（整数/小数秒或 HTTP-date 差值，秒）。

    非法/缺失/无法解析一律返回 ``None``（绝不抛异常）。
    """
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    except Exception:
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        val = float(text)
        return max(0.0, val) if math.isfinite(val) else None
    except (TypeError, ValueError):
        pass
    try:
        import datetime as _datetime
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(text)
        if dt is None:
            return None
        now = (_datetime.datetime.now(dt.tzinfo) if dt.tzinfo is not None
               else _datetime.datetime.now())
        return max(0.0, (dt - now).total_seconds())
    except Exception:
        return None


def compute_backoff(kind: Optional[str], attempt: int,
                    retry_after: Optional[float] = None) -> float:
    """退避时长（秒）= base + 抖动；公式**逐字保持 loop 收敛前的实现**。

    * ``http_retryable``（429/5xx）：``base = 2.0 + attempt * 4.0``，抖动 0..1.0s；
    * ``network``：``base = (2.0, 6.0)[min(attempt, 1)]``，抖动 0..0.5s；
    * 网关给出 ``Retry-After`` 时 ``base = max(base, retry_after)``。
    """
    try:
        att = max(0, int(attempt))
    except (TypeError, ValueError):
        att = 0
    if kind == "network":
        base = (2.0, 6.0)[min(att, 1)]
        jitter = random.random() * 0.5
    else:
        base = 2.0 + att * 4.0
        jitter = random.random() * 1.0
    if retry_after is not None:
        try:
            base = max(base, float(retry_after))
        except (TypeError, ValueError):
            pass
    return base + jitter


# ── [K4/W8] 流式（SSE）基础设施（原 tools/agent/loop.py 整体搬入）──────

#: 单次 ``read`` 的字节数。注意 ``HTTPResponse.read(n)`` 会**攒满 n 字节或 EOF**
#: 才返回（本机流式服务端实测），所以它是解析粒度而非时延保证；停滞检测靠的是
#: socket 单次 recv 超时（见 ``_STREAM_STALL_TIMEOUT``）。
_SSE_READ_CHUNK = 4096

#: [W8] 流式请求的「块间停滞超时」（秒）。流式下 socket 超时天然退化为单次读超时
#: （每收到数据即重置），因此它同时是①健康慢流的上限（块持续到达即可远超此时长）
#: 与②完全停滞流的判死阈值——90s 内无任何数据即抛错，交给既有重试/退避逻辑。
#: 有效超时 = ``min(request_timeout, 本值)``（用户显式调低 request_timeout 时从严）。
#: **超时策略留在调用方**（loop 的 ``_stream_timeout()`` 计算后传入 ChatRequest.timeout）。
_STREAM_STALL_TIMEOUT = 90.0

#: [R3 波动收敛] **Agent 对话链路**单次 LLM 请求超时的单一真源（秒）。
#:
#: 收敛前同一操作在三处各写各的：CLI 流式讲题 120s、网关/群聊问答 55s、
#: GUI 侧 90s（``agentic_research`` 也独立写了 90s）。三处不一致的直接后果是
#: 「同一操作在不同入口有不同的等待上限」→ 多轮之间的耗时差异无法归因
#: （到底是模型慢还是超时口径不同？），能力波动无法收敛到 ±15%。
#:
#: 取值 90s 的依据（与 :mod:`tools.intelligence.agentic_research` 的 deadline
#: 范式对齐）：那边把「单请求超时」（``self.timeout``，默认 90s）与「整轮
#: 总预算」（``budget_s``，默认 240s）分成两层——单请求不得无限等，但整轮
#: 仍有自己的天花板。本常量属于**第一层**（单请求），与之取同值；第二层
#: （整轮/整 run 预算）由 ``agentic_research.budget_s`` 与
#: :mod:`tools.agent.runtime` 的 ``max_seconds`` 各自负责，互不耦合。
#:
#: [口径边界·勿再误读] 本常量是**Agent 对话链路**（``agent/loop`` /
#: ``cli/agent/engine`` / ``gui/workers/agent_worker``）单请求超时的真源，
#: **不是全仓所有 LLM 调用的统一上限**。``chat_completion`` 这条薄封装有
#: 自己的默认值（40s），且各调用点按业务性质显式传参——批量入库 90s、
#: 院校研报 60s、组卷 15s、变式检索/大纲 Diff 12s。这些差异是**刻意的**：
#: 后三者在交互路径上等不起（宁可少答也不要卡住 REPL），统一拉齐到 90s
#: 会把「快速失败」变成「长时间卡住」。:class:`ChatRequest` 的类文档
#: (``timeout`` 由调用方决定) 是这条边界的权威说明。
DEFAULT_LLM_TIMEOUT = _STREAM_STALL_TIMEOUT


class _StreamAccumulator:
    """[W8] 累积 OpenAI 兼容流式响应的 delta，重建非流式响应结构。

    重建目标（与非流式回包逐字段同形）::

        {"choices": [{"index": 0,
                      "message": {"role": "assistant", "content": "...",
                                  "tool_calls": [{"id", "type",
                                                  "function": {"name", "arguments"}}]},
                      "finish_reason": "stop"}],
         "usage": {...}}  # 仅在网关回传时出现

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

    def feed_line(self, line, on_content: Optional[Callable[[str], None]] = None) -> None:
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
        self.feed(obj, on_content=on_content)

    def feed(self, obj: Any, on_content: Optional[Callable[[str], None]] = None) -> None:
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
                if on_content is not None:
                    on_content(content)
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


def _parse_sse_text(raw_text: str,
                    on_chunk: Optional[Callable[[str], None]] = None
                    ) -> Optional[Dict[str, Any]]:
    """把整段 SSE 文本（被代理缓冲/未标 Content-Type 的回包）重建为响应 dict。

    非 SSE（不含 ``data:`` 行或什么都没解析出）返回 ``None``，由调用方走原异常路径。
    """
    if "data:" not in raw_text:
        return None
    acc = _StreamAccumulator()
    for line in raw_text.splitlines():
        acc.feed_line(line, on_content=on_chunk)
    if acc.is_empty():
        return None
    return acc.to_response()


def _read_buffered(resp, max_bytes: int) -> bytes:
    """整体读取回包字节（带上限）。

    只用于非增量（整体读取）路径——增量 SSE 路径必须按块读取。
    ``resp`` 契约与 ``http.client.HTTPResponse`` 一致（``read(n)`` 支持尺寸
    参数）：生产路径的 ``safe_urlopen`` 必返真实 HTTPResponse；注入的鸭子
    类型对象也必须满足该契约，不得无上限读取（见 SSRF/体积上限静态审计）。
    """
    return resp.read(max_bytes)


def _consume_sse(resp, on_chunk: Optional[Callable[[str], None]] = None
                 ) -> Dict[str, Any]:
    """[W8] 逐块读取 SSE 响应体并重建响应 dict。

    读循环里任何网络异常（RemoteDisconnected / 读超时 / IncompleteRead）都会
    向上抛出 → 被 request_chat 的既有网络异常分支捕获并重试；已累积的半截
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
            raise LLMResponseTooLargeError("上游流式回包超过安全体积上限")
        buf += piece
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            acc.feed_line(line, on_content=on_chunk)
    if buf:
        acc.feed_line(buf, on_content=on_chunk)
    if acc.is_empty():
        # 空流 / 全是无法解析的噪声 → 如实报错，绝不伪造成「模型空回复」。
        raise LLMEmptyStreamError("上游流式回包为空或无法解析（未收到任何有效 data 块）")
    return acc.to_response()


def _parse_llm_response(resp, url: str,
                        on_chunk: Optional[Callable[[str], None]] = None,
                        decompress_fn: Optional[Callable] = None
                        ) -> Dict[str, Any]:
    """[W8] 解析一次 chat/completions 回包（流式 SSE 优先，JSON 回退）。

    * ``Content-Type: text/event-stream``（且未压缩）→ 增量解析 SSE；
    * 其余一律按**原非流式方式**整体读取：解压 → HTML 检查 → ``json.loads``；
      若 JSON 解析失败但正文含 ``data:`` 行，再尝试按被代理缓冲的 SSE 文本
      重建（网关漏标 Content-Type 时的容错）。

    ``decompress_fn``：解压器注入（默认 ``net_guard.decompress_limited``）；
    供调用方沿用其模块级测试桩（如 loop 的 too_large 用例）。
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
        return _consume_sse(resp, on_chunk=on_chunk)
    # [P2 修复] 读取与解压都加上体积上限（解压炸弹防护）。
    # [W8 实测] HTTPResponse.read(n) 对 Content-Length / chunked 两种流式回包
    # 都是「攒满 n 字节或 EOF 才返回」（本机流式服务端验证），因此这里单次
    # 读取即可拿到完整回包；增量解析只走上面的 event-stream 分支。
    raw_bytes = _read_buffered(resp, MAX_HTTP_RESPONSE_BYTES)
    _decomp = decompress_fn if decompress_fn is not None else decompress_limited
    raw_bytes, _truncated = _decomp(raw_bytes, enc)
    if _truncated:
        raise LLMResponseTooLargeError("上游响应解压后超过安全体积上限")
    raw_text = raw_bytes.decode("utf-8", errors="ignore").strip()
    if raw_text.startswith("<!doctype html") or raw_text.startswith("<html"):
        raise ValueError(f"服务端返回了网页 HTML 而非 API JSON 数据 (请求地址: {url})，请检查 base_url 配置")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        data = _parse_sse_text(raw_text, on_chunk=on_chunk)
        if data is None:
            raise
    if not isinstance(data, dict):
        raise ValueError("上游返回了非对象 JSON，无法作为 chat/completions 回包解析")
    return data


def _post_chat(url: str, headers: Dict[str, str], payload: Dict[str, Any],
               timeout: float, on_open: Optional[Callable[[], None]] = None,
               on_chunk: Optional[Callable[[str], None]] = None,
               urlopen_fn: Optional[Callable] = None,
               decompress_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """[W8] 发一次 chat/completions 请求并解析回包。

    ``stream_options``（``include_usage``）属尽力而为：网关若因此报 400，
    **摘掉该字段立即重发一次**（不消耗网络重试次数、不阻断整个调用）。
    其余异常原样抛出，由 request_chat 的分类重试逻辑处理。
    """
    _urlopen = urlopen_fn if urlopen_fn is not None else _llm_urlopen
    for round_no in (0, 1):
        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        try:
            with _urlopen(req, timeout=timeout) as resp:
                if on_open:
                    on_open()
                return _parse_llm_response(resp, url, on_chunk=on_chunk,
                                           decompress_fn=decompress_fn)
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


def _read_http_error_body(e: urllib.error.HTTPError) -> str:
    """读取并解压 HTTP 错误响应体（只读一次，供分类与调用方复用）。"""
    try:
        raw = e.read(MAX_HTTP_RESPONSE_BYTES)
    except Exception:
        return ""
    try:
        return _decompress_response_bytes(raw, getattr(e, "headers", None))
    except Exception:
        return ""


@dataclass
class ChatRequest:
    """统一 chat/completions 请求描述（K4）。

    ``stream=True``（默认）走 SSE 增量解析（绕开网关~60s 硬超时）；
    ``stream=False`` 走整体读取 + JSON 解析（含被代理缓冲的 SSE 容错）。
    ``headers_extra`` 覆盖/补充默认请求头；``timeout`` 由调用方决定
    （loop 传 ``_stream_timeout()``，engine/vision_solver 传
    ``DEFAULT_LLM_TIMEOUT``，study_planner 传 60s，
    chat_completion/open_grader 传各自的既有值）。

    [R3 波动收敛] ``temperature`` / ``max_tokens`` 的默认值为**具名单一真源**
    （:data:`DIALOGUE_TEMPERATURE` / :data:`DEFAULT_MAX_TOKENS`）：调用方
    省略即取对话链路口径，但不再依赖「dataclass 里恰好写着 0.3」这种隐式约定
    ——稳定性优先的调用点必须显式传 :data:`STABLE_TEMPERATURE`。
    """

    messages: List[Dict[str, Any]] = field(default_factory=list)
    model: str = ""
    temperature: float = DIALOGUE_TEMPERATURE
    max_tokens: Optional[int] = None
    stream: bool = True
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    stream_options: Optional[Dict[str, Any]] = None
    timeout: float = DEFAULT_LLM_TIMEOUT
    api_key: str = ""
    base_url: str = ""
    headers_extra: Optional[Dict[str, str]] = None


def _build_headers(req: ChatRequest) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {req.api_key}",
        "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Core/1.0",
        "Connection": "close",
        "Accept-Encoding": "gzip, deflate, identity",
    }
    for key, val in (req.headers_extra or {}).items():
        if val is None:
            headers.pop(key, None)
        else:
            headers[str(key)] = str(val)
    return headers


def _build_payload(req: ChatRequest) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": req.model,
        "messages": req.messages,
        "temperature": req.temperature,
        "stream": bool(req.stream),
    }
    if req.max_tokens is not None and req.max_tokens > 0:
        payload["max_tokens"] = req.max_tokens
    if req.tools:
        payload["tools"] = req.tools
        if req.tool_choice is not None:
            payload["tool_choice"] = req.tool_choice
    if req.stream_options is not None:
        payload["stream_options"] = req.stream_options
    return payload


def request_chat(req: ChatRequest, *, max_retries: int = 2,
                 on_chunk: Optional[Callable[[str], None]] = None,
                 on_open: Optional[Callable[[], None]] = None,
                 payload_adjuster: Optional[Callable[[Dict[str, Any], LLMError],
                                                     Optional[Dict[str, Any]]]] = None,
                 stats: Optional[Dict[str, Any]] = None,
                 sleep_fn: Callable[[float], Any] = time.sleep,
                 urlopen_fn: Optional[Callable] = None,
                 decompress_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """统一发起一次 chat/completions（含分类重试/退避/SSE 解析）。

    * 成功 → 返回响应 dict（与 ``_StreamAccumulator.to_response`` 同形）；
    * 失败 → 抛结构化异常（**绝不返回 None**；None 语义留在调用方包装层）：
      - 确定性错误（鉴权/参数/工具不支持/响应过大/空流）→ ``LLMDeterministicError``
        （或子类 ``LLMResponseTooLargeError`` / ``LLMEmptyStreamError``）；
      - 可重试错误重试耗尽 → ``LLMRetryExhausted``（``last_error`` 携带底层错误）。
    * ``payload_adjuster(payload, err)``：返回 dict 则**就地换 payload 重发**
      （仅允许一轮调整，不消耗重试次数；返回 None 走正常重试/上抛路径）。
    * ``sleep_fn``：退避睡眠注入（单测确定性；禁止在测试里全局 patch time.sleep）。
    * ``stats``：可选字典，回写 ``attempts``（网络尝试次数，调整轮不计）与
      ``last_error_kind``（K8 错误分流用）。
    * ``urlopen_fn``：传输函数注入（默认本模块 ``safe_urlopen`` 通道）。
    * ``decompress_fn``：解压器注入（默认 ``net_guard.decompress_limited``；
      loop 侧测试桩点需要）。
    """
    url = normalize_openai_url(req.base_url, "chat/completions")
    headers = _build_headers(req)
    payload = _build_payload(req)
    _urlopen = urlopen_fn if urlopen_fn is not None else _llm_urlopen
    _sleep = sleep_fn if sleep_fn is not None else time.sleep
    try:
        retries = max(0, int(max_retries))
    except (TypeError, ValueError):
        retries = 0

    def _stats(**kw: Any) -> None:
        if isinstance(stats, dict):
            stats.update(kw)

    adjust_round_used = False
    for attempt in range(retries + 1):
        # 内层循环只服务一件事：payload_adjuster 调整（如 400 自适应降级 /
        # stream_options 摘除），不消耗网络重试次数。
        while True:
            try:
                data = _post_chat(url, headers, payload, req.timeout,
                                  on_open=on_open, on_chunk=on_chunk,
                                  urlopen_fn=_urlopen, decompress_fn=decompress_fn)
                _stats(attempts=attempt + 1)
                return data
            except LLMResponseTooLargeError:
                _stats(attempts=attempt + 1, last_error_kind="too_large")
                raise
            except urllib.error.HTTPError as e:
                status = getattr(e, "code", None)
                body = _read_http_error_body(e)
                retryable, kind = classify_http_error(status, body)
                retry_after = parse_retry_after(getattr(e, "headers", None))
                err_cls = LLMRetryableError if retryable else LLMDeterministicError
                safe_body = _redact_error_text(body)
                err = err_cls(f"HTTP {status}: {safe_body[:200]}" if safe_body else f"HTTP {status}",
                              status=status, kind=kind, retry_after=retry_after,
                              body=safe_body, last_error=e)
                if payload_adjuster is not None and not adjust_round_used:
                    adjusted = payload_adjuster(payload, err)
                    if isinstance(adjusted, dict):
                        adjust_round_used = True
                        payload = adjusted
                        continue
                if retryable and attempt < retries:
                    _sleep(compute_backoff(kind, attempt, retry_after))
                    break       # 落回外层 for → 下一次尝试
                _stats(attempts=attempt + 1, last_error_kind=kind)
                if retryable:
                    raise LLMRetryExhausted(str(err), status=status, kind=kind,
                                            retry_after=retry_after,
                                            body=body, last_error=err) from e
                raise err
            except (urllib.error.URLError, TimeoutError, socket.timeout,
                    ConnectionResetError, http.client.RemoteDisconnected) as e:
                if attempt < retries:
                    _sleep(compute_backoff("network", attempt, None))
                    break       # 落回外层 for → 下一次尝试
                _stats(attempts=attempt + 1, last_error_kind="network")
                # 消息逐字取 ``str(e)``（如 "<urlopen error ...>"）而非
                # "类型名: ..."：loop 侧 ``[连接异常]: {e.last_error or e}``
                # 的终端输出必须与收敛前逐字一致（对照脚本已钉住）。
                last = LLMRetryableError(str(e), kind="network", last_error=e)
                raise LLMRetryExhausted(str(last), kind="network",
                                        last_error=last) from e
    # 理论不可达：最后一圈必在 except 分支 return/raise。
    raise LLMRetryExhausted("重试循环未能收敛")  # pragma: no cover


def get_llm_config(workspace_root: Optional[Path | str] = None) -> Dict[str, Any]:
    """读取本地工作区 ky_config.json 中的 LLM 配置。"""
    ws = Path(workspace_root) if workspace_root else ROOT
    cfg_file = ws / "ky_config.json"
    if not cfg_file.exists():
        return {}
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "api_key": str(data.get("api_key", "")).strip(),
                "base_url": str(data.get("base_url", "https://api.deepseek.com/v1")).strip(),
                "model": str(data.get("model", "deepseek-chat")).strip(),
                "temperature": float(data.get("temperature", 0.3)),
            }
    except Exception as e:
        _LOG.debug("读取 ky_config.json 失败: %s", e)
    return {}


def is_llm_configured(config: Optional[Dict[str, Any]] = None, workspace_root: Optional[Path | str] = None) -> bool:
    """检查是否配置了有效的 LLM API Key 与模型。"""
    cfg = config if config is not None else get_llm_config(workspace_root)
    api_key = cfg.get("api_key", "").strip()
    base_url = cfg.get("base_url", "").strip()
    model = cfg.get("model", "").strip()
    return bool(api_key and base_url and model and not api_key.startswith("sk-placeholder"))


def fetch_upstream_models(api_key: str, base_url: str, timeout: float = 15.0) -> Tuple[bool, List[str], str]:
    """异步请求 GET /models 端点，动态探查上游服务商支持的模型列表。"""
    k = (api_key or "").strip()
    b = (base_url or "").strip()
    if not k:
        return False, [], "缺少 API Key，无法探查上游模型"
    if not b:
        return False, [], "缺少 Base URL，无法探查上游模型"

    url = normalize_openai_url(b, "models")
    headers = {
        "Authorization": f"Bearer {k}",
        "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain/1.0",
        "Accept": "application/json",
        "Connection": "close",
    }

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _llm_urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_HTTP_RESPONSE_BYTES)
            text = _decompress_response_bytes(raw, resp.headers).strip()
            data = json.loads(text)

            models: List[str] = []
            raw_list = []
            if isinstance(data, dict):
                if isinstance(data.get("data"), list):
                    raw_list = data["data"]
                elif isinstance(data.get("models"), list):
                    raw_list = data["models"]
            elif isinstance(data, list):
                raw_list = data

            for item in raw_list:
                if isinstance(item, dict) and "id" in item:
                    models.append(str(item["id"]).strip())
                elif isinstance(item, str):
                    models.append(item.strip())

            if not models:
                return False, [], "上游端点返回模型列表为空"

            # 排序：推荐模型排前面
            def _score_model(m: str) -> int:
                m_low = m.lower()
                if "chat" in m_low or "plus" in m_low or "pro" in m_low:
                    return 0
                if "deepseek" in m_low or "gpt-4" in m_low or "qwen" in m_low or "glm" in m_low:
                    return 1
                return 2

            sorted_models = sorted(list(dict.fromkeys(models)), key=lambda x: (_score_model(x), x))
            return True, sorted_models, f"成功发现 {len(sorted_models)} 个上游可用模型"

    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            raw_err = e.read(MAX_HTTP_RESPONSE_BYTES)
            err_body = _decompress_response_bytes(raw_err, e.headers)
        except Exception:
            pass
        err_msg = ""
        try:
            err_json = json.loads(err_body)
            if isinstance(err_json, dict) and "error" in err_json:
                err_val = err_json["error"]
                err_msg = err_val.get("message", "") if isinstance(err_val, dict) else str(err_val)
            elif isinstance(err_json, dict) and "message" in err_json:
                err_msg = str(err_json["message"])
        except Exception:
            pass
        detail = err_msg or f"HTTP {e.code}"
        if e.code in (401, 403):
            return False, [], f"鉴权失败 (HTTP {e.code})：API Key 无效或过期"
        elif e.code == 404:
            return False, [], f"端点未找到 (HTTP 404)：上游未开放 /models 接口或 Base URL 路径需修正"
        return False, [], f"探查接口返回错误 (HTTP {e.code}): {detail}"
    except UnsafeURLError as e:
        return False, [], f"安全拦截：Base URL 未通过 SSRF 校验，已拒绝请求 ({e})"
    except Exception as e:
        err_str = str(e)
        if "timed out" in err_str.lower():
            return False, [], f"探查超时 ({timeout}s)，网络连接缓慢"
        return False, [], f"网络连接失败: {err_str}"


def chat_completion(
    messages_or_prompt: Union[str, List[Dict[str, Any]]],
    config: Optional[Dict[str, Any]] = None,
    workspace_root: Optional[Path | str] = None,
    system_prompt: Optional[str] = None,
    temperature: float = DIALOGUE_TEMPERATURE,
    timeout: float = 40.0,
    max_tokens: Optional[int] = None,
    urlopen_fn: Optional[Callable] = None,
    sleep_fn: Callable[[float], Any] = time.sleep,
) -> Optional[str]:
    """向 OpenAI 兼容端点发送对话请求并返回模型回复文本。

    [K4] 已收敛为统一出口 ``request_chat`` 的薄封装（``stream=False``、
    ``max_retries=0``，与旧实现同为「1 次首发 + 最多 1 次 400 自适应降级」）。
    签名与返回契约不变：任何失败一律返回 ``None``（**绝不抛异常**）。

    自动容错：若遇 HTTP 400 提示 max_tokens 或 system 角色受限，自动调整后重试一次。

    [R3 波动收敛] 本函数是**抽取 / 判卷 / 补全**等稳定性优先链路的公共出口
    （采分点补全、切片识别、变式抽取、上下文摘要压缩都走这里），因此
    ``temperature`` 的默认值取 :data:`DIALOGUE_TEMPERATURE` 只是**兜底**——
    这些调用点必须显式传 :data:`STABLE_TEMPERATURE`，否则同一份输入会得到
    措辞不同、长度不同的抽取结果，判分与统计被采样噪声污染。
    """
    cfg = config if config is not None else get_llm_config(workspace_root)
    if not is_llm_configured(cfg):
        return None

    api_key = cfg.get("api_key", "").strip()
    base_url = cfg.get("base_url", "").strip()
    model = cfg.get("model", "").strip()

    # 规范化消息列表
    if isinstance(messages_or_prompt, str):
        msgs: List[Dict[str, Any]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": messages_or_prompt})
    else:
        msgs = list(messages_or_prompt)
        if system_prompt and not any(m.get("role") == "system" for m in msgs):
            msgs.insert(0, {"role": "system", "content": system_prompt})

    def _adjust_400(cur_payload: Dict[str, Any], err: LLMError) -> Optional[Dict[str, Any]]:
        """400 自适应降级（旧 chat_completion 内联逻辑逐字保持）。

        1. 报错含 max_tokens/token 类关键词 → 剔除 max_tokens 字段；
        2. 报错含 system/role 关键词 → 系统消息合并进首个 user 消息；
        3. 未精准匹配但原 payload 传了 max_tokens → 兜底剔除后重试一次。
        """
        if err.status != 400:
            return None
        retry_payload = dict(cur_payload)
        err_lower = (err.body or "").lower()
        modified = False

        if any(k in err_lower for k in ("max_tokens", "token", "tokens", "max_completion_tokens", "max_output_tokens")):
            if "max_tokens" in retry_payload:
                retry_payload.pop("max_tokens", None)
                modified = True

        if any(k in err_lower for k in ("system", "role", "系统", "角色")):
            new_msgs: List[Dict[str, Any]] = []
            sys_text = ""
            for m in msgs:
                if m.get("role") == "system":
                    sys_text += f"[系统设定: {m.get('content', '')}]\n"
                else:
                    new_msgs.append(dict(m))
            if sys_text:
                user_msg = next((m for m in new_msgs if m.get("role") == "user"), None)
                if user_msg is not None:
                    user_msg["content"] = sys_text + str(user_msg.get("content", ""))
                elif new_msgs:
                    new_msgs[0]["content"] = sys_text + str(new_msgs[0].get("content", ""))
                else:
                    new_msgs.append({"role": "user", "content": sys_text.strip()})
                retry_payload["messages"] = new_msgs
                modified = True

        if not modified and "max_tokens" in retry_payload:
            retry_payload.pop("max_tokens", None)
            modified = True

        return retry_payload

    req = ChatRequest(
        messages=msgs,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
        timeout=timeout,
        api_key=api_key,
        base_url=base_url,
        headers_extra={
            "User-Agent": "Mozilla/5.0 Kaoyan-Study-Chain-Core/1.0",
            "Accept-Encoding": "gzip, deflate, identity",
        },
    )
    try:
        data = request_chat(
            req,
            max_retries=0,
            payload_adjuster=_adjust_400,
            urlopen_fn=urlopen_fn,
            sleep_fn=sleep_fn,
        )
    except Exception as exc:
        _LOG.debug("chat_completion 请求失败: %s", exc)
        return None

    choices = data.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        content = (msg or {}).get("content") or ""
        return content.strip() or None
    return None


call_llm_sync = chat_completion


__all__ = [
    "ChatRequest",
    "DEFAULT_LLM_TIMEOUT",
    "DEFAULT_MAX_TOKENS",
    "DIALOGUE_TEMPERATURE",
    "LLMDeterministicError",
    "LLMEmptyStreamError",
    "LLMError",
    "LLMResponseTooLargeError",
    "LLMRetryExhausted",
    "LLMRetryableError",
    "STABLE_TEMPERATURE",
    "_decompress_response_bytes",
    "call_llm_sync",
    "chat_completion",
    "classify_http_error",
    "compute_backoff",
    "fetch_upstream_models",
    "get_llm_config",
    "is_llm_configured",
    "normalize_openai_url",
    "parse_retry_after",
    "request_chat",
]
