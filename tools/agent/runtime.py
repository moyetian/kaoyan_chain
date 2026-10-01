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
    """Optional per-run limits. Zero means unlimited for that dimension."""

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

        def _positive_float(value: Any, default: float) -> float:
            try:
                parsed = float(value)
                return parsed if parsed > 0 else default
            except (TypeError, ValueError):
                return default

        return cls(
            max_steps=_positive_int(runtime.get("max_steps"), _positive_int(fallback_steps, 0)),
            max_seconds=_positive_float(runtime.get("max_seconds"), 0.0),
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
        """Raise the per-run step ceiling for an explicit recovery nudge."""
        try:
            target = int(max_steps)
        except (TypeError, ValueError):
            return
        if target <= self.limits.max_steps:
            return
        self.limits = RunLimits(
            max_steps=target,
            max_seconds=self.limits.max_seconds,
            max_tool_calls=self.limits.max_tool_calls,
            max_total_tokens=self.limits.max_total_tokens,
        )
        self._record("budget_extended", max_steps=target)

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


__all__ = ["RunBudgetExceeded", "RunLimits", "RunRuntime", "RunState"]
