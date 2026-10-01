"""K9 runtime control-plane regression tests."""

from pathlib import Path

import pytest

from tools.agent.runtime import (
    RunBudgetExceeded,
    RunLimits,
    RunRuntime,
    RunState,
)


def test_runtime_tracks_valid_lifecycle_and_snapshot():
    runtime = RunRuntime(RunLimits(max_steps=2))
    run_id = runtime.start("task", session_id="s1")
    assert run_id and runtime.state is RunState.INIT

    runtime.transition(RunState.PROMPT)
    runtime.step()
    runtime.transition(RunState.MODEL)
    runtime.tool_call("read_file")
    runtime.transition(RunState.OBSERVE)
    runtime.complete("answer")

    snapshot = runtime.snapshot()
    assert snapshot["run_id"] == run_id
    assert snapshot["state"] == "succeeded"
    assert snapshot["steps"] == 1
    assert snapshot["tool_calls"] == 1
    assert [item["event"] for item in snapshot["trace"]][-1] == "state"


def test_runtime_rejects_invalid_transition():
    runtime = RunRuntime()
    runtime.start("task")
    with pytest.raises(ValueError, match="invalid runtime transition"):
        runtime.transition(RunState.OBSERVE)


def test_runtime_step_budget_stops_the_run():
    runtime = RunRuntime(RunLimits(max_steps=1))
    runtime.start("task")
    runtime.transition(RunState.PROMPT)
    runtime.step()
    with pytest.raises(RunBudgetExceeded):
        runtime.step()
    assert runtime.state is RunState.STOPPED
    assert runtime.stop_reason == "max_steps"


def test_runtime_limits_are_read_from_agent_config():
    limits = RunLimits.from_config(
        {"agent": {"runtime": {"max_steps": "4", "max_seconds": "2.5"}}},
        fallback_steps=10,
    )
    assert limits == RunLimits(max_steps=4, max_seconds=2.5, max_tool_calls=0)


def test_runtime_allows_multiple_tool_calls_in_one_model_turn():
    runtime = RunRuntime()
    runtime.start("task")
    runtime.transition(RunState.PROMPT)
    runtime.transition(RunState.MODEL)
    runtime.transition(RunState.TOOL)
    runtime.tool_call("read_file")
    runtime.transition(RunState.OBSERVE)
    runtime.transition(RunState.TOOL)
    runtime.tool_call("search")
    runtime.transition(RunState.OBSERVE)
    runtime.complete("answer")
    assert runtime.snapshot()["tool_calls"] == 2


def test_runtime_can_finalize_after_prompt_early_exit():
    runtime = RunRuntime()
    runtime.start("task")
    runtime.transition(RunState.PROMPT)
    runtime.complete("fallback")
    assert runtime.snapshot()["state"] == "succeeded"


def test_runtime_enforces_total_token_budget():
    runtime = RunRuntime(RunLimits(max_total_tokens=3))
    runtime.start("task")
    runtime.transition(RunState.PROMPT)
    runtime.transition(RunState.MODEL)
    with pytest.raises(RunBudgetExceeded, match="token budget"):
        runtime.record_usage({"prompt_tokens": 2, "completion_tokens": 2})
    assert runtime.snapshot()["state"] == "stopped"
    assert runtime.snapshot()["total_tokens"] == 4


def test_runtime_can_extend_recovery_step_budget():
    runtime = RunRuntime(RunLimits(max_steps=1))
    runtime.start("task")
    runtime.extend_step_budget(3)
    runtime.transition(RunState.PROMPT)
    runtime.step()
    runtime.step()
    runtime.step()
    assert runtime.snapshot()["steps"] == 3


def test_runtime_resets_recovery_budget_between_runs():
    runtime = RunRuntime(RunLimits(max_steps=1))
    runtime.start("first")
    runtime.extend_step_budget(3)
    runtime.start("second")
    runtime.transition(RunState.PROMPT)
    runtime.step()
    with pytest.raises(RunBudgetExceeded):
        runtime.step()


def test_agent_runner_exposes_runtime_snapshot(tmp_path: Path, monkeypatch):
    from tools.agent.loop import AgentRunner

    responses = iter([
        {"choices": [{"message": {"content": "done", "tool_calls": []}}]},
    ])
    monkeypatch.setattr(AgentRunner, "_call_llm", lambda self, messages: next(responses))
    runner = AgentRunner(
        config={"api_key": "sk-test-fake", "model": "sample", "active_subject": "pol"},
        workspace_root=tmp_path,
        permission_mode="auto",
        quiet=True,
    )
    captured = {}
    runner.hooks.register_hook(
        "FinishRun", lambda context: captured.update(context), priority=100
    )
    assert runner.run("task", interactive=False) == "done"
    assert captured["runtime"]["state"] == "succeeded"
    assert captured["runtime"]["steps"] == 1
    runner.close()


def test_agent_runner_stops_after_provider_token_budget(tmp_path: Path, monkeypatch):
    from tools.agent.loop import AgentRunner

    response = {
        "choices": [{"message": {"content": "ignored", "tool_calls": []}}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4},
    }
    monkeypatch.setattr(AgentRunner, "_call_llm", lambda self, messages: response)
    runner = AgentRunner(
        config={
            "api_key": "sk-test-fake",
            "model": "sample",
            "active_subject": "pol",
            "agent": {"runtime": {"max_total_tokens": 3}},
        },
        workspace_root=tmp_path,
        permission_mode="auto",
        quiet=True,
    )
    captured = {}
    runner.hooks.register_hook("FinishRun", lambda context: captured.update(context), priority=100)
    answer = runner.run("task", interactive=False)
    assert "Token 预算" in answer
    assert captured["runtime"]["state"] == "stopped"
    assert captured["runtime"]["total_tokens"] == 12
    runner.close()
