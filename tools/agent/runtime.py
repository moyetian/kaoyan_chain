"""Small runtime control plane for an agent run.

The existing loop owns model/tool behavior.  This module owns lifecycle
state, budgets, and a bounded trace so callers can inspect a run without
parsing console output or session-log implementation details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
from typing import Any, Dict, List, Optional


class RunState(str, Enum):
    INIT = "init"
    PROMPT = "prompt"
    MODEL = "model"
    TOOL = "tool"
    OBSERVE = "observe"
    FINALIZE = "finalize"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"


class RunBudgetExceeded(RuntimeError):
    """Raised when a configured run budget is exhausted."""


_TERMINAL = frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.STOPPED})

#: [R3 波动收敛·根因 2] 单次 run 的**默认总时长熔断**（秒）。
#:
#: 此前 :attr:`RunLimits.max_seconds` 的缺省值是 ``0``，而 ``0`` 的语义是
#: 「不限」——于是 ``ky_config.json`` 里没有 ``agent.runtime`` 段（绝大多数
#: 用户）时，:meth:`RunRuntime._check_time_budget` **从不触发**：Agent 主循环
#: 完全没有总时长熔断。叠加产物闸门 :meth:`RunRuntime.extend_step_budget`
#: （每次 +6 步、最多 2 次，可把 8 步抬到 20 步）却不抬高任何时间约束，
#: 耗时上限实际由「步数 × 单步耗时」决定 → R3 实测同一操作 84s → 511s
#: （+506%），且无任何机制能把它压回去。
#:
#: 取 900s 的依据：与 :mod:`tools.intelligence.agentic_research` 的
#: ``budget_s``（整轮总预算，默认 240s）同属「整轮/整run 总预算」这一层，
#: 取同一量级并略放宽（Agent run 步骤更多、每步含工具往返）；单请求层仍由
#: ``llm_client.DEFAULT_LLM_TIMEOUT``（90s）负责。两层不耦合：单请求不得
#: 无限等，整 run 也有自己的天花板。
DEFAULT_RUN_SECONDS = 900.0


def _nonneg_float(value: Any, default: float) -> float:
    """解析「可为 0（=不限）」的浮点上限；非法/负值回落 ``default``。

    语义：用户写 ``max_seconds: 0`` 就是明确表达「这一维度不限」，不能被
    悄悄改写成默认熔断值（否则「既有配置以配置为准」的向后兼容契约就破了）。
    缺省（键不存在）不走本函数，由调用方取 :data:`DEFAULT_RUN_SECONDS`。
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


_ALLOWED = {
    RunState.INIT: {RunState.PROMPT, RunState.FAILED, RunState.STOPPED},
    RunState.PROMPT: {RunState.MODEL, RunState.FINALIZE, RunState.FAILED, RunState.STOPPED},
    RunState.MODEL: {RunState.TOOL, RunState.OBSERVE, RunState.FINALIZE,
                     RunState.FAILED, RunState.STOPPED},
    RunState.TOOL: {RunState.TOOL, RunState.OBSERVE, RunState.MODEL, RunState.FAILED,
                    RunState.STOPPED},
    RunState.OBSERVE: {RunState.TOOL, RunState.MODEL, RunState.FINALIZE, RunState.FAILED,
                       RunState.STOPPED},
    RunState.FINALIZE: {RunState.SUCCEEDED, RunState.FAILED, RunState.STOPPED},
    RunState.SUCCEEDED: set(),
    RunState.FAILED: set(),
    RunState.STOPPED: set(),
}


@dataclass(frozen=True)
class RunLimits:
    """每个 run 的可选上限。某一维度为 0 表示该维度不限（**仅显式构造时**
    才是「不限」；从配置解析时缺省会取 :data:`DEFAULT_RUN_SECONDS`）。

    ``RunLimits()`` / ``RunLimits(max_steps=8)`` 这类**直接构造**仍保留
    「0 = 不限」的历史语义（既有测试与内部调用依赖它）；``from_config``
    才是生产路径，那里缺省会落到有限默认值。
    """

    max_steps: int = 0
    max_seconds: float = 0.0
    max_tool_calls: int = 0
    max_total_tokens: int = 0

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]], fallback_steps: int = 0) -> "RunLimits":
        cfg = config if isinstance(config, dict) else {}
        agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
        runtime = agent.get("runtime") if isinstance(agent.get("runtime"), dict) else {}

        def _positive_int(value: Any, default: int) -> int:
            try:
                parsed = int(value)
                return parsed if parsed > 0 else default
            except (TypeError, ValueError):
                return default

        return cls(
            max_steps=_positive_int(runtime.get("max_steps"), _positive_int(fallback_steps, 0)),
            # [R3 波动收敛·根因 2] 缺省（无 ``agent.runtime`` 段 / 无
            # ``max_seconds`` 键 / 值非法）一律取 DEFAULT_RUN_SECONDS，
            # 「不限」只能通过**显式**写 ``max_seconds: 0`` 表达。
            # 向后兼容：已显式配置该键的用户一律以配置为准（含显式 0）。
            max_seconds=(
                _nonneg_float(runtime["max_seconds"], DEFAULT_RUN_SECONDS)
                if "max_seconds" in runtime
                else DEFAULT_RUN_SECONDS
            ),
            max_tool_calls=_positive_int(runtime.get("max_tool_calls"), 0),
            max_total_tokens=_positive_int(
                runtime.get("max_total_tokens", runtime.get("token_budget")), 0),
        )


@dataclass
class RunRuntime:
    """Lifecycle state and bounded telemetry for one ``AgentRunner.run``."""

    limits: RunLimits = field(default_factory=RunLimits)
    trace_limit: int = 256
    run_id: str = ""
    state: RunState = RunState.INIT
    step_count: int = 0
    tool_call_count: int = 0
    total_tokens: int = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    stop_reason: str = ""
    trace: List[Dict[str, Any]] = field(default_factory=list)
    _base_limits: RunLimits = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._base_limits = self.limits

    def start(self, user_input: str = "", session_id: str = "") -> str:
        self.limits = self._base_limits
        self.run_id = uuid.uuid4().hex[:16]
        self.state = RunState.INIT
        self.step_count = 0
        self.tool_call_count = 0
        self.total_tokens = 0
        self.started_at = time.monotonic()
        self.finished_at = None
        self.stop_reason = ""
        self.trace.clear()
        self._record("start", user_input=user_input[:500], session_id=session_id)
        return self.run_id

    def transition(self, state: RunState, **data: Any) -> None:
        target = RunState(state)
        if target not in _ALLOWED[self.state]:
            raise ValueError(f"invalid runtime transition: {self.state.value} -> {target.value}")
        self.state = target
        self._record("state", state=target.value, **data)

    def step(self) -> int:
        self._check_time_budget()
        self.step_count += 1
        if self.limits.max_steps and self.step_count > self.limits.max_steps:
            self.stop("max_steps")
            raise RunBudgetExceeded("agent step budget exhausted")
        self._record("step", step=self.step_count)
        return self.step_count

    def extend_step_budget(self, max_steps: int) -> None:
        """为一次显式的恢复动作放宽步数上限，**并同步放宽时间上限**。

        [R3 波动收敛·根因 2] 为什么必须同步：产物闸门（W8-C）在产物缺失时
        追加 nudge 并重入主循环，每次 ``+6`` 步、最多 2 次。收敛前本方法**只**
        抬 ``max_steps``、``max_seconds`` 原样保留 —— 于是「步数上限」与
        「时间上限」两个约束脱钩：步数可以涨到 20 步而时间预算纹丝不动，
        单步慢响应即可把整 run 拖到无界（实测 84s → 511s，+506%）。同步
        放宽后二者保持同一量级的配比，nudge 仍然能用来「多给几步把产物补上」，
        但不会把总时长放到不可控。

        配比按 ``base.max_seconds / base.max_steps``（每步时间配额）线性折算：
        抬到 ``target`` 步 → ``base_seconds × target / base_steps``。若基线
        本身不限（``max_seconds == 0``）或步数基线为 0，则保持「不限」。
        """
        try:
            target = int(max_steps)
        except (TypeError, ValueError):
            return
        if target <= self.limits.max_steps:
            return
        base = self._base_limits
        target_seconds = self.limits.max_seconds
        if target_seconds and base.max_steps > 0 and base.max_seconds > 0:
            target_seconds = base.max_seconds * (target / float(base.max_steps))
        self.limits = RunLimits(
            max_steps=target,
            max_seconds=target_seconds,
            max_tool_calls=self.limits.max_tool_calls,
            max_total_tokens=self.limits.max_total_tokens,
        )
        self._record("budget_extended", max_steps=target,
                     max_seconds=target_seconds)

    def tool_call(self, name: str = "") -> int:
        self._check_time_budget()
        self.tool_call_count += 1
        if (self.limits.max_tool_calls
                and self.tool_call_count > self.limits.max_tool_calls):
            self.stop("max_tool_calls")
            raise RunBudgetExceeded("agent tool-call budget exhausted")
        self._record("tool_call", name=str(name)[:120], count=self.tool_call_count)
        return self.tool_call_count

    def record_usage(self, usage: Optional[Dict[str, Any]]) -> int:
        """Account for provider usage and enforce the optional token budget."""
        if not isinstance(usage, dict):
            return self.total_tokens
        raw_total = usage.get("total_tokens")
        if raw_total is None:
            raw_total = (usage.get("prompt_tokens", 0) or 0) + (usage.get("completion_tokens", 0) or 0)
        try:
            added = max(0, int(raw_total))
        except (TypeError, ValueError):
            added = 0
        self.total_tokens += added
        self._record("usage", tokens=added, total_tokens=self.total_tokens)
        if (self.limits.max_total_tokens
                and self.total_tokens > self.limits.max_total_tokens):
            self.stop("max_total_tokens")
            raise RunBudgetExceeded("agent token budget exhausted")
        return self.total_tokens

    def complete(self, answer: str = "") -> None:
        if self.state not in _TERMINAL:
            if self.state != RunState.FINALIZE:
                self.transition(RunState.FINALIZE)
            self.transition(RunState.SUCCEEDED, answer_chars=len(str(answer or "")))
        self.finished_at = time.monotonic()

    def fail(self, reason: str) -> None:
        self.stop_reason = str(reason or "failure")
        if self.state not in _TERMINAL:
            self.transition(RunState.FAILED, reason=self.stop_reason)
        self.finished_at = time.monotonic()

    def stop(self, reason: str) -> None:
        self.stop_reason = str(reason or "stopped")
        if self.state not in _TERMINAL:
            self.transition(RunState.STOPPED, reason=self.stop_reason)
        self.finished_at = time.monotonic()

    def snapshot(self) -> Dict[str, Any]:
        elapsed = 0.0
        if self.started_at is not None:
            elapsed = (self.finished_at or time.monotonic()) - self.started_at
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "steps": self.step_count,
            "tool_calls": self.tool_call_count,
            "total_tokens": self.total_tokens,
            "elapsed_seconds": round(max(0.0, elapsed), 3),
            "stop_reason": self.stop_reason,
            "trace": list(self.trace),
        }

    def _check_time_budget(self) -> None:
        if (self.limits.max_seconds and self.started_at is not None
                and time.monotonic() - self.started_at >= self.limits.max_seconds):
            self.stop("max_seconds")
            raise RunBudgetExceeded("agent time budget exhausted")

    def _record(self, event: str, **data: Any) -> None:
        item = {"event": event, "ts": round(time.time(), 3), **data}
        self.trace.append(item)
        if self.trace_limit > 0 and len(self.trace) > self.trace_limit:
            del self.trace[:-self.trace_limit]


__all__ = [
    "DEFAULT_RUN_SECONDS",
    "RunBudgetExceeded",
    "RunLimits",
    "RunRuntime",
    "RunState",
]
