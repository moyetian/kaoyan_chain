# -*- coding: utf-8 -*-
"""[R3 波动收敛] LLM 侧能力波动收敛到 ≤15% —— 回归测试。

背景（R3 全矩阵仿真实测）
------------------------
54 格 / 610 条记录里，同一操作在多轮之间出现两处**确定性**波动：

1. **输出长度 8998 → 4927 字符（−45%）**
   ``cli/agent/engine.stream_chat``（CLI 非 Agent 路径）**不传** ``max_tokens``，
   而 Agent 路径 ``tools/agent/loop.py`` 固定 4096 → 两条路径的输出长度行为
   结构性不同，观测到的收缩即来自此。

2. **耗时 84s → 511s（+506%）**
   a) ``tools/agent/runtime.py`` 的 ``max_seconds`` 缺省 0（= 不限），且
      ``ky_config.json`` 缺 ``agent.runtime`` 段 → ``_check_time_budget()``
      **从不触发**，Agent 主循环无总时长熔断；
   b) 产物闸门（W8-C）``extend_step_budget`` 每次 +6 步、最多 2 次（8→20 步），
      却**只抬步数、不抬时间** → 步数与时间预算脱钩，耗时上限实际由
      「步数 × 单步耗时」决定，无界。

3. **同一操作三处超时互不一致**：CLI 流式 120s、网关问答 55s、GUI 侧 90s。

本文件覆盖
----------
A. 超时口径单一真源（三处引用同一常量）；
B. ``max_tokens`` / ``temperature`` 单一真源（CLI 与 Agent 路径同值）；
C. ``runtime.max_seconds`` 有限缺省 + 显式配置优先（含显式 0）；
D. 产物闸门抬高步数时**同步抬高时间预算**；
E. **核心护栏**：同一 prompt 的两次调用，``llm_call`` 事件条数与 ``attempts``
   不得因产物闸门而放大（用 session jsonl 事件断言）；
F. 稳定性优先链路（判卷 / 摘要压缩 / 采分点补全 / 变式抽取）显式关采样。

全程离线：mock ``loop.safe_urlopen`` 喂SSE 假响应，绝不联网；
时间推进用假monotonic，**不做真实 sleep**。
"""

import json
import sys
import time as _real_time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(TESTS_DIR), str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tools.agent.loop as loop_module  # noqa: E402
import tools.output_budget as output_budget  # 输出预算档位单一真源（R4 新增）
import tools.llm_client as llm_client  # noqa: E402
from tools.agent.loop import AgentRunner  # noqa: E402
from tools.agent.runtime import (  # noqa: E402
    DEFAULT_RUN_SECONDS,
    RunBudgetExceeded,
    RunLimits,
    RunRuntime,
    RunState,
)
from tools.agent.session_log import load_events  # noqa: E402
from test_w8_streaming_client import (  # noqa: E402
    FakeStreamResponse,
    _make_runner,
    delta,
    finish,
    sse_body,
    tool_delta,
)


# ── 任务措辞（逐字复刻 KaoyanBench 侧 prompt 的硬性要求段）──────────────

#: 触发产物闸门：含「实际写入 output/report.json」。
PROMPT_GATE = (
    "这是硬性要求：必须用 write_file 工具实际写入 output/report.json"
    "（仅把内容写在回复文本里不算完成；结束前请确认文件已落盘）"
)


# ── mock 设施 ───────────────────────────────────────────────────────────


def _text_body(text: str) -> bytes:
    """无工具调用的普通回复（主循环应在此终止）。"""
    return sse_body(delta(text), finish("stop"))


def _scripted_urlopen(specs):
    """按调用次序返回 SSE 假响应；返回 ``(fake, calls)``。

    每次调用都新建 ``FakeStreamResponse``（同一响应对象被二次read 只会拿到
    空流），脚本耗尽后沿用最后一项。
    """
    calls = []

    def fake(req, timeout=None):
        calls.append({"timeout": timeout,
                      "body": json.loads(req.data.decode("utf-8"))})
        idx = min(len(calls) - 1, len(specs) - 1)
        return FakeStreamResponse(specs[idx])

    return fake, calls


def _llm_calls(tmp_path):
    """本工作区全部会话的 ``llm_call`` 事件 payload 列表。"""
    events = []
    for p in (tmp_path / ".memory" / "sessions").glob("*.jsonl"):
        events.extend(load_events(p))
    return [e.get("payload") or {}
            for e in events if e.get("type") == "llm_call"]


class _FakeClock:
    """可手工推进的 monotonic 替身（**不做真实 sleep**）。

    通过给 ``tools.agent.runtime.time`` 装代理实现：只替换 ``monotonic``，
    其余属性委托真 ``time`` 模块。
    """

    def __init__(self, real):
        self._real = real
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now

    def __getattr__(self, name):
        return getattr(self._real, name)


def _install_clock(monkeypatch):
    """把 runtime 模块的 ``time`` 换成假时钟，返回该时钟对象。

    打桩打在 ``tools.agent.runtime.time`` 上（本模块 ``import time`` 后按
    ``time.monotonic()`` 取用），因此只影响 runtime 的时间源，不影响同进程
    内其他线程/模块的真实时间。
    """
    import tools.agent.runtime as runtime_mod
    clock = _FakeClock(_real_time)
    monkeypatch.setattr(runtime_mod, "time", clock)
    return clock


def _capture_open_grader_payload(endpoint, monkeypatch):
    """跑一次 ``OpenAICompatClient.chat``，返回它实际发出的请求 payload。

    在 ``open_grader`` 模块命名空间里把 ``request_chat`` 换成捕获桩（该模块是
    ``from llm_client import request_chat`` 导入的，桩必须打在模块属性上）。
    桩在捕获后立刻抛错终止 —— 本用例只关心出参前的 payload，不关心响应处理。
    """
    from tools.skills import open_grader as G

    seen = {}
    orig = G.request_chat

    def _spy(req, **kw):
        seen["payload"] = llm_client._build_payload(req)
        raise RuntimeError("stop-after-capture")

    G.request_chat = _spy
    try:
        G.OpenAICompatClient(endpoint, timeout=5.0).chat(
            [{"role": "user", "content": "hi"}])
    except Exception:
        pass
    finally:
        G.request_chat = orig
    assert seen, "桩未生效：open_grader 走的是别的 request_chat 引用"
    return seen["payload"]


# ══════════════════════════════════════════════════════════════════════
# A. 超时口径单一真源
# ══════════════════════════════════════════════════════════════════════


def test_default_llm_timeout_is_90s_single_source():
    """单请求超时真源 = 90s，且与流式块间停滞上限同值（两者本就是同一约束）。"""
    assert llm_client.DEFAULT_LLM_TIMEOUT == 90.0
    assert llm_client.DEFAULT_LLM_TIMEOUT == llm_client._STREAM_STALL_TIMEOUT


def test_chat_request_defaults_come_from_single_source():
    """``ChatRequest`` 的缺省 temperature / timeout 必须引用具名真源，
    不得回退成裸字面量（那正是「两条路径各自漂移」的起点）。"""
    assert llm_client.ChatRequest.__dataclass_fields__["temperature"
                                                       ].default == llm_client.DIALOGUE_TEMPERATURE
    assert llm_client.ChatRequest.__dataclass_fields__["timeout"
                                                       ].default == llm_client.DEFAULT_LLM_TIMEOUT
    # 具名真源的取值本身也被钉住（防止有人「顺手调了一下」）
    assert llm_client.DIALOGUE_TEMPERATURE == 0.3
    assert llm_client.DEFAULT_MAX_TOKENS == 4096


def test_loop_resolve_timeout_defaults_to_single_source():
    """AgentRunner 的 request_timeout 缺省 = 90s（单一真源）。"""
    assert AgentRunner._resolve_timeout(None, {}) == llm_client.DEFAULT_LLM_TIMEOUT
    assert AgentRunner._resolve_timeout(None, None) == llm_client.DEFAULT_LLM_TIMEOUT
    # 非法值回落默认；显式/配置值优先（用户从严意图不得被覆盖）
    assert AgentRunner._resolve_timeout(None, {"request_timeout": "60s"}) \
        == llm_client.DEFAULT_LLM_TIMEOUT
    assert AgentRunner._resolve_timeout(30, {"request_timeout": 60}) == 30.0
    assert AgentRunner._resolve_timeout(None, {"request_timeout": 60}) == 60.0


def test_stream_chat_sends_single_source_timeout(tmp_path, monkeypatch, capsys):
    """CLI 流式讲题的 socket 超时 = 单一真源 90s（收敛前是 120s）。"""
    import tools.cli.agent.engine as engine

    fake, calls = _scripted_urlopen([_text_body("讲题完毕。")])
    monkeypatch.setattr(engine, "safe_urlopen", fake)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)

    reply = engine.stream_chat(
        [{"role": "user", "content": "讲一下这题"}],
        {"api_key": "sk-test-fake", "model": "样本模型",
         "base_url": "https://api.test.com/v1"},
    )

    assert reply == "讲题完毕。"
    assert len(calls) == 1
    assert calls[0]["timeout"] == llm_client.DEFAULT_LLM_TIMEOUT, (
        "CLI 流式超时口径未收敛到 DEFAULT_LLM_TIMEOUT")


def test_query_llm_reply_timeout_defaults_to_single_source(monkeypatch):
    """网关/群聊问答的超时缺省 = 单一真源 90s（收敛前硬编码 55s）。"""
    import tools.cli.agent.engine as engine

    seen = {}

    def _fake_chat(messages, **kw):
        seen.update(kw)
        return "网关答复"

    monkeypatch.setattr(engine, "chat_completion", _fake_chat)
    monkeypatch.setattr(engine, "load_config",
                        lambda: {"api_key": "sk-test", "model": "m",
                                 "base_url": "https://api.test.com/v1"})
    monkeypatch.delenv("KY_LLM_TIMEOUT", raising=False)

    assert engine.query_llm_reply("讲一下这题") == "网关答复"
    assert seen["timeout"] == llm_client.DEFAULT_LLM_TIMEOUT, (
        "网关问答超时口径未收敛到 DEFAULT_LLM_TIMEOUT")


def test_query_llm_reply_env_override_still_wins(monkeypatch):
    """``KY_LLM_TIMEOUT`` 环境变量仍可临时覆盖（运维/排障口子不得堵死）。"""
    import tools.cli.agent.engine as engine

    seen = {}

    def _fake_chat(messages, **kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(engine, "chat_completion", _fake_chat)
    monkeypatch.setattr(engine, "load_config",
                        lambda: {"api_key": "sk-test", "model": "m",
                                 "base_url": "https://api.test.com/v1"})
    monkeypatch.setenv("KY_LLM_TIMEOUT", "42")

    engine.query_llm_reply("x")
    assert seen["timeout"] == 42.0
    # 非法环境变量值回落真源，不得抛
    monkeypatch.setenv("KY_LLM_TIMEOUT", "not-a-number")
    engine.query_llm_reply("x")
    assert seen["timeout"] == llm_client.DEFAULT_LLM_TIMEOUT


def test_gui_worker_timeout_defaults_to_single_source(tmp_path):
    """GUI worker 的超时缺省 = 单一真源；``request_timeout`` 配置仍优先。"""
    from tools.gui.workers.agent_worker import AgentWorker

    assert AgentWorker({}, "你好", workspace_root=tmp_path).timeout \
        == llm_client.DEFAULT_LLM_TIMEOUT
    assert AgentWorker({"request_timeout": 45}, "你好",
                       workspace_root=tmp_path).timeout == 45.0
    assert AgentWorker({}, "你好", timeout=30,
                       workspace_root=tmp_path).timeout == 30.0


# ══════════════════════════════════════════════════════════════════════
# B. max_tokens / temperature 单一真源（根因 1）
# ══════════════════════════════════════════════════════════════════════


def test_stream_chat_sends_max_tokens(monkeypatch, capsys):
    """根因 1：CLI 非 Agent 路径必须显式带 max_tokens（收敛前完全不发该字段）。"""
    import tools.cli.agent.engine as engine

    fake, calls = _scripted_urlopen([_text_body("答复。")])
    monkeypatch.setattr(engine, "safe_urlopen", fake)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)

    engine.stream_chat([{"role": "user", "content": "x"}],
                       {"api_key": "sk-test-fake", "model": "m",
                        "base_url": "https://api.test.com/v1"})

    assert len(calls) == 1
    assert calls[0]["body"].get("max_tokens") == output_budget.max_tokens_for({}), (
        "CLI 路径未发送 max_tokens → 输出长度交给上游默认决定")


def test_stream_chat_and_agent_path_agree_on_max_tokens_and_temperature(
        tmp_path, monkeypatch):
    """核心断言：CLI 与 Agent 两条路径对同一操作必须发出**相同的**
    max_tokens 与 temperature —— 收敛前CLI 侧两者都不显式声明。"""
    import tools.cli.agent.engine as engine

    # ── CLI 路径 ──
    fake_cli, cli_calls = _scripted_urlopen([_text_body("答复。")])
    monkeypatch.setattr(engine, "safe_urlopen", fake_cli)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    engine.stream_chat([{"role": "user", "content": "x"}],
                       {"api_key": "sk-test-fake", "model": "m",
                        "base_url": "https://api.test.com/v1"})

    # ── Agent 路径 ──
    fake_agent, agent_calls = _scripted_urlopen([_text_body("答复。")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake_agent)
    _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])

    cli_body = cli_calls[0]["body"]
    agent_body = agent_calls[0]["body"]
    assert cli_body["max_tokens"] == agent_body["max_tokens"] \
        == output_budget.max_tokens_for({})
    assert cli_body["temperature"] == agent_body["temperature"] \
        == llm_client.DIALOGUE_TEMPERATURE


@pytest.mark.parametrize("bad", [None, "", "abc", 0, -5, [1, 2]])
def test_resolve_max_tokens_never_returns_none(bad):
    """``max_tokens`` 解析器**永不为 None** —— None 会让 llm_client 整个省略
    该字段，正是 R3 观测到输出长度收缩的机制。

    合法值（含小于缺省档的 999）一律按配置走：用户显式收窄输出上限是合理意图，
    解析器只负责「非法 → 缺省档」，不替用户改写合法配置。

    [2026-10-06 输出预算分档] 「缺省」不再是写死的 ``DEFAULT_MAX_TOKENS``，
    而是**缺省档（standard）的上界**——故断言改为与 ``output_budget`` 联动
    （与「功能卡数对账 CARD_ITEMS」同一思路：数字会随档位表演进，写死必然漂移）。
    """
    default_cap = output_budget.max_tokens_for({})
    assert loop_module._resolve_max_tokens({"max_tokens": bad}) == default_cap
    assert loop_module._resolve_max_tokens({}) == default_cap
    assert loop_module._resolve_max_tokens(None) == default_cap
    # 档位联动：切档即换上界
    assert loop_module._resolve_max_tokens({"output_budget": "deep"}) \
        == output_budget.BUDGET_LEVELS["deep"]["max_tokens"]
    assert loop_module._resolve_max_tokens({"output_budget": "brief"}) \
        == output_budget.BUDGET_LEVELS["brief"]["max_tokens"]
    # 非法档位回落缺省，不报错
    assert loop_module._resolve_max_tokens({"output_budget": "bogus"}) == default_cap
    # 显式 max_tokens 仍优先于档位（向后兼容）
    assert loop_module._resolve_max_tokens(
        {"output_budget": "brief", "max_tokens": 999}) == 999
    # 合法值优先
    assert loop_module._resolve_max_tokens({"max_tokens": 2048}) == 2048
    assert loop_module._resolve_max_tokens({"max_tokens": "2048"}) == 2048
    assert loop_module._resolve_max_tokens({"max_tokens": 999}) == 999


@pytest.mark.parametrize("bad", [None, "", "abc", -1, 99])
def test_resolve_temperature_falls_back_to_dialogue(bad):
    """温度解析：非法/越界回落对话口径；合法值（含 0 = 关采样）优先。"""
    assert loop_module._resolve_temperature({"temperature": bad}) \
        == llm_client.DIALOGUE_TEMPERATURE
    assert loop_module._resolve_temperature({}) == llm_client.DIALOGUE_TEMPERATURE
    assert loop_module._resolve_temperature(None) == llm_client.DIALOGUE_TEMPERATURE
    # 显式 0 必须被尊重（稳定性优先链路据此关采样）
    assert loop_module._resolve_temperature({"temperature": 0}) == 0.0
    assert loop_module._resolve_temperature({"temperature": 0.5}) == 0.5


def test_agent_path_resolvers_are_the_only_source_in_loop():
    """Agent 路径必须经由解析器取温度/输出上限，不得内联字面量。

    为什么单靠「CLI 与 Agent 发出相同的 max_tokens」不够：收敛前Agent 侧
    内联的是 ``config.get("max_tokens", 4096)``，与真源同值→ 两条路径比较
    会「通过」，但真源改动时Agent 侧不会跟随（这正是本次要消灭的漂移）。
    故此处直接钉住调用点：``_call_llm`` / ``_call_llm_without_tools`` 的
    ChatRequest 构造必须走 ``_resolve_*``。
    """
    import inspect
    src = inspect.getsource(loop_module)
    body = src[src.index("def _call_llm("):]
    assert "self.config.get(\"temperature\"" not in body, (
        "Agent 路径又出现了内联的温度字面量，绕过了解析器")
    assert "self.config.get(\"max_tokens\"" not in body, (
        "Agent 路径又出现了内联的 max_tokens 字面量，绕过了解析器")
    # 两个调用点都必须显式声明（而非依赖 dataclass 缺省）
    assert body.count("temperature=_resolve_temperature(self.config)") >= 2
    assert body.count("max_tokens=_resolve_max_tokens(self.config)") >= 2


def test_agent_path_uses_resolvers_behaviourally(tmp_path, monkeypatch):
    """行为层：配置里塞合法但非缺省的值时，Agent 路径发出的请求必须随之改变
    —— 证明它确实在读配置（而不是硬编码 0.3 / 4096 的另一份拷贝）。"""
    fake, calls = _scripted_urlopen([_text_body("答复。")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    runner.config["temperature"] = 0.11
    runner.config["max_tokens"] = 1234
    runner._call_llm([{"role": "user", "content": "x"}])

    body = calls[0]["body"]
    assert body["temperature"] == 0.11
    assert body["max_tokens"] == 1234


def test_engine_resolvers_mirror_loop_ones():
    """CLI 侧两个解析器与 loop 侧同口径（各端自己解析一份，就必须等价）。"""
    import tools.cli.agent.engine as engine

    for cfg in ({}, None, {"max_tokens": "x"}, {"temperature": "x"},
                {"max_tokens": 2048, "temperature": 0}):
        assert engine._resolve_max_tokens(cfg) == loop_module._resolve_max_tokens(cfg)
        assert engine._resolve_temperature(cfg) == loop_module._resolve_temperature(cfg)


def test_query_llm_reply_sends_max_tokens(monkeypatch):
    """网关问答同样显式带 max_tokens（此前走 chat_completion 且未传该参数）。"""
    import tools.cli.agent.engine as engine

    seen = {}
    monkeypatch.setattr(engine, "chat_completion",
                        lambda messages, **kw: (seen.update(kw), "ok")[1])
    monkeypatch.setattr(engine, "load_config",
                        lambda: {"api_key": "sk-test", "model": "m",
                                 "base_url": "https://api.test.com/v1"})

    engine.query_llm_reply("讲题")
    assert seen["max_tokens"] == output_budget.max_tokens_for({})
    assert seen["temperature"] == llm_client.DIALOGUE_TEMPERATURE


# ══════════════════════════════════════════════════════════════════════
# C. runtime.max_seconds 有限缺省 + 显式配置优先（根因 2a）
# ══════════════════════════════════════════════════════════════════════


def test_default_run_seconds_is_finite():
    """缺省熔断必须是**有限**正值（0 = 不限，正是本次要修的根因）。"""
    assert 0 < DEFAULT_RUN_SECONDS < float("inf")
    assert DEFAULT_RUN_SECONDS == 900.0


@pytest.mark.parametrize("config", [
    {},# 完全无配置
    {"agent": {}},                              # 有 agent 段但无 runtime 段
    {"agent": {"runtime": {}}},                 # runtime 段为空
    {"agent": {"runtime": {"max_steps": 4}}},   # runtime 段有别的键
    None,
])
def test_missing_runtime_section_still_gets_finite_budget(config):
    """``ky_config.json`` 缺 ``agent.runtime`` 段时缺省熔断**仍必须生效**。

    这正是 R3 实测的现场：绝大多数用户的配置里没有这一段 → 熔断从不触发。
    """
    limits = RunLimits.from_config(config, fallback_steps=8)
    assert limits.max_seconds == DEFAULT_RUN_SECONDS, (
        "缺 agent.runtime 段时 max_seconds 回落 0 = 不限，熔断形同虚设")


def test_explicit_max_seconds_config_wins_including_zero():
    """向后兼容：已显式配置该键的用户一律以配置为准 —— **含显式 0**。

    显式写 0 是用户明确表达「这一维度不限」，不能被悄悄改写成默认熔断值。
    """
    assert RunLimits.from_config(
        {"agent": {"runtime": {"max_seconds": 120}}}).max_seconds == 120.0
    assert RunLimits.from_config(
        {"agent": {"runtime": {"max_seconds": "2.5"}}}).max_seconds == 2.5
    assert RunLimits.from_config(
        {"agent": {"runtime": {"max_seconds": 0}}}).max_seconds == 0.0
    # 非法值 → 回落缺省熔断（而非「不限」）
    assert RunLimits.from_config(
        {"agent": {"runtime": {"max_seconds": "abc"}}}).max_seconds \
        == DEFAULT_RUN_SECONDS
    assert RunLimits.from_config(
        {"agent": {"runtime": {"max_seconds": -1}}}).max_seconds \
        == DEFAULT_RUN_SECONDS


def test_time_budget_actually_fires_with_fake_clock(monkeypatch):
    """缺省熔断**真的会触发**：推进假时钟越过上限后 step() 抛
    RunBudgetExceeded、stop_reason=``max_seconds``（不做真实 sleep）。"""
    clock = _install_clock(monkeypatch)
    runtime = RunRuntime(RunLimits.from_config({}, fallback_steps=100))
    runtime.start("task")
    runtime.transition(RunState.PROMPT)

    assert runtime.step() == 1, "未到预算时不应中断"

    clock.advance(DEFAULT_RUN_SECONDS + 1)
    with pytest.raises(RunBudgetExceeded, match="time budget"):
        runtime.step()

    assert runtime.state is RunState.STOPPED
    assert runtime.stop_reason == "max_seconds"


def test_explicit_zero_budget_means_unlimited(monkeypatch):
    """显式 ``max_seconds: 0`` → 不限时（即使时钟推进很远也不中断）。"""
    clock = _install_clock(monkeypatch)
    runtime = RunRuntime(RunLimits.from_config(
        {"agent": {"runtime": {"max_seconds": 0}}}, fallback_steps=100))
    runtime.start("task")
    runtime.transition(RunState.PROMPT)

    clock.advance(10 ** 6)
    for i in range(1, 21):
        assert runtime.step() == i


# ══════════════════════════════════════════════════════════════════════
# D. 产物闸门抬高步数时同步抬高时间预算（根因 2b）
# ══════════════════════════════════════════════════════════════════════


def test_extend_step_budget_also_raises_time_budget():
    """核心护栏（单元层）：抬步数必须同步抬时间，否则两个上限脱钩。

    收敛前 ``extend_step_budget`` 只动 ``max_steps``：产物闸门可把 8 步抬到
    20 步而时间预算纹丝不动 → 耗时上限实际由「步数 × 单步耗时」决定，无界。
    """
    runtime = RunRuntime(RunLimits(max_steps=8, max_seconds=800.0))
    runtime.start("task")

    runtime.extend_step_budget(14)
    assert runtime.limits.max_steps == 14
    # 按每步时间配额（800/8 = 100s）线性折算：14 × 100 = 1400
    assert runtime.limits.max_seconds == pytest.approx(1400.0)


def test_extend_step_budget_scales_proportionally_each_time():
    """连续两次放宽：每次都从**基线**折算（不累乘），避免指数膨胀。"""
    runtime = RunRuntime(RunLimits(max_steps=8, max_seconds=800.0))
    runtime.start("task")

    runtime.extend_step_budget(14)
    runtime.extend_step_budget(20)
    assert runtime.limits.max_steps == 20
    assert runtime.limits.max_seconds == pytest.approx(2000.0)  # 800 × 20/8


def test_extend_step_budget_keeps_unlimited_time_unlimited():
    """基线本身不限时（显式 0）→ 放宽步数不得凭空造出一个时间上限。"""
    runtime = RunRuntime(RunLimits(max_steps=8, max_seconds=0.0))
    runtime.start("task")
    runtime.extend_step_budget(14)
    assert runtime.limits.max_steps == 14
    assert runtime.limits.max_seconds == 0.0


def test_extend_step_budget_records_new_time_budget():
    """trace 必须记录抬高后的**两个**上限（否则遥测里看不到时间被抬了）。"""
    runtime = RunRuntime(RunLimits(max_steps=8, max_seconds=800.0))
    runtime.start("task")
    runtime.extend_step_budget(16)
    evts = [t for t in runtime.snapshot()["trace"]
            if t.get("event") == "budget_extended"]
    assert evts, "budget_extended 事件缺失"
    assert evts[-1]["max_steps"] == 16
    assert evts[-1]["max_seconds"] == pytest.approx(1600.0)


def test_gate_extends_time_budget_end_to_end(tmp_path, monkeypatch):
    """端到端：产物闸门触发 nudge 后，run 的时间上限确实被同步抬高。

    走真实 runner + 真实 SSE mock（离线），观察 ``budget_extended`` 事件。

    ``max_steps=2`` 是必要的：``extend_step_budget`` 在 ``target <= 当前上限``
    时是no-op（这是它「只放宽、不收紧」的既有语义），而闸门抬的是
    ``step + 6``——``max_steps=8`` 时首轮 nudge 只抬到 7 ≤ 8，压根不会触发放宽。
    要观察放宽就必须让上限低到 ``step + 6`` 确实越过它。
    """
    fake, _ = _scripted_urlopen([_text_body("已完成，written: output/report.json")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    runner = _make_runner(tmp_path, max_steps=2)
    baseline_steps = runner.runtime.limits.max_steps
    baseline_seconds = runner.runtime.limits.max_seconds
    runner.run(PROMPT_GATE, interactive=False)

    exts = [t for t in runner.runtime.snapshot()["trace"]
            if t.get("event") == "budget_extended"]
    assert exts, "产物闸门未触发任何预算放宽（max_steps 过大？）"
    assert runner.runtime.limits.max_steps > baseline_steps
    # 时间上限按每步配额同步抬高：baseline_seconds ×新步数 / 基线步数
    assert exts[-1]["max_seconds"] == pytest.approx(
        baseline_seconds * exts[-1]["max_steps"] / baseline_steps)
    assert runner.runtime.limits.max_seconds == pytest.approx(
        baseline_seconds * runner.runtime.limits.max_steps / baseline_steps)


def test_gate_time_budget_coupling_needs_no_op_when_ceiling_not_exceeded(
        tmp_path, monkeypatch):
    """阴性对照：闸门抬到的步数**没越过**当前上限时，``extend_step_budget``
    必须保持 no-op（不得凭空造出时间上限，也不得反向收紧）。

    这条守住「同步抬高」的对称面——它是**放宽**钩子，不是「每次 nudge 都
    按比例放大一次」的重算器；否则 max_steps=8 的常规配置会在两次 nudge 后
    把时间上限抬到远超需要的量。
    """
    runtime = RunRuntime(RunLimits(max_steps=20, max_seconds=2000.0))
    runtime.start("task")
    runtime.extend_step_budget(7)     # 7 < 20 → no-op
    assert runtime.limits.max_steps == 20
    assert runtime.limits.max_seconds == 2000.0
    assert [t for t in runtime.snapshot()["trace"]
            if t.get("event") == "budget_extended"] == []


# ══════════════════════════════════════════════════════════════════════
# E. 核心护栏：事件条数与 attempts 不得因产物闸门而放大
# ══════════════════════════════════════════════════════════════════════


def test_same_prompt_twice_yields_identical_llm_call_shape(tmp_path, monkeypatch):
    """**本次改动的核心护栏**（见任务书）：同一 prompt 连跑两次，
    ``llm_call`` 事件条数与每条的 ``attempts`` 必须完全一致 ——
    不得因产物闸门（及其步数/时间预算放宽）而放大。

    为什么这条能兜住根因 2：产物闸门重入主循环会**成倍**增加 LLM 调用，
    而每次调用都是真实计费 + 真实耗时。若闸门的行为随步数/时间预算变化，
    事件条数就会在多轮之间漂移（84s vs 511s 的一个直接来源）。
    """
    shapes = []
    for _ in range(2):
        work = tmp_path / f"run{len(shapes)}"
        work.mkdir()
        fake, calls = _scripted_urlopen([
            _text_body("已完成，written: output/report.json"),
        ])
        monkeypatch.setattr(loop_module, "safe_urlopen", fake)
        _make_runner(work).run(PROMPT_GATE, interactive=False)
        shapes.append({
            "http_calls": len(calls),
            "llm_events": len(_llm_calls(work)),
            "attempts": [p.get("attempts") for p in _llm_calls(work)],
            "allow_tools": [bool(p.get("allow_tools")) for p in _llm_calls(work)],
        })

    assert shapes[0] == shapes[1], (
        f"同一 prompt 两次调用的llm_call 形状不一致（闸门放大）：{shapes}")
    # 每次成功调用 attempts 恒为 1（无重试放大）
    assert set(shapes[0]["attempts"]) == {1}


def test_gate_does_not_amplify_events_when_deliverable_present(tmp_path, monkeypatch):
    """产物已落盘 → 闸门零动作，事件条数与「无产物措辞」基线逐条一致。

    这是闸门的**阴性对照**：若闸门在不该激活时也放大调用数，本改动前后的
    行为差异就会污染所有非闸门场景。
    """
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "report.json").write_text('{"ok": true}', encoding="utf-8")

    fake, calls = _scripted_urlopen([_text_body("任务完成，报告已就绪。")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    _make_runner(tmp_path).run(PROMPT_GATE, interactive=False)

    assert len(calls) == 1, "产物已在 → 不得产生任何额外调用"
    assert len(_llm_calls(tmp_path)) == 1


def test_llm_call_event_carries_budget_telemetry(tmp_path, monkeypatch):
    """llm_call 事件仍须携带 prompt_chars / message_count / latency_ms /
    attempts —— 波动收敛的判断依赖这些字段做归因，缺一则无法区分
    「生成量」与「服务端拥塞」两个假设。"""
    fake, _ = _scripted_urlopen([_text_body("答复。")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    _make_runner(tmp_path).run("讲一下这题", interactive=False)

    payloads = _llm_calls(tmp_path)
    assert payloads
    for p in payloads:
        # prompt_chars 是**整条请求**的消息总长（含教练系统提示），
        # 因此只能断言「随输入单调增长」，不能钉死具体数值。
        assert isinstance(p.get("prompt_chars"), int)
        assert p["prompt_chars"] >= len("讲一下这题")
        assert isinstance(p.get("message_count"), int)
        assert p["message_count"] >= 2          # system + user
        assert isinstance(p.get("latency_ms"), int)
        assert p.get("attempts") == 1


def test_llm_call_prompt_chars_grows_with_input(tmp_path, monkeypatch):
    """``prompt_chars`` 必须真的随输入变化 —— 否则它无法用来把耗时差异
    归因到「生成量」（短 prompt 慢 = 上游拥塞；长 prompt 慢 = 生成量）。"""
    seen = {}
    for label, question in (("short", "简述"), ("long", "请详细论述" * 200)):
        work = tmp_path / label
        work.mkdir()
        fake, _ = _scripted_urlopen([_text_body("答复。")])
        monkeypatch.setattr(loop_module, "safe_urlopen", fake)
        _make_runner(work).run(question, interactive=False)
        seen[label] = _llm_calls(work)[0]["prompt_chars"]

    # 差值应与两次输入的字符数差同量级（教练系统提示是常量，会被抵消）
    assert seen["long"] - seen["short"] >= len("请详细论述" * 200) - 10, seen


# ══════════════════════════════════════════════════════════════════════
# F. 稳定性优先链路显式关采样
# ══════════════════════════════════════════════════════════════════════


def test_open_grader_default_temperature_is_stable(monkeypatch):
    """判卷缺省温度 = 0（关采样）：同一份作答必须得到同一份评分。

    多轮之间分数若因采样而变，就无法区分「学生表现变了」与「噪声」。
    """
    payload = _capture_open_grader_payload(
        {"base_url": "https://api.test.com/v1", "api_key": "sk-test", "model": "m"},
        monkeypatch)
    assert payload["temperature"] == llm_client.STABLE_TEMPERATURE


def test_open_grader_explicit_temperature_still_wins(monkeypatch):
    """已显式配置 temperature 的评审端点仍以配置为准（不推翻用户的多视角设计）。"""
    payload = _capture_open_grader_payload({
        "base_url": "https://api.test.com/v1", "api_key": "sk-test",
        "model": "m", "temperature": 0.3,
    }, monkeypatch)
    assert payload["temperature"] == 0.3


def test_compaction_summary_uses_stable_temperature():
    """摘要压缩 temperature 0.1 → 0：摘要是 resume 上下文的唯一来源，
    措辞逐轮漂移等于「同一会话在不同轮记得的事不一样」。"""
    from tools.agent import compaction as C

    seen = {}

    def _fake_llm(prompt, **kw):
        seen.update(kw)
        # key_info 必须是「分类 → 条目」的对象（compaction 的既有契约）
        return json.dumps({
            "goal": "g", "progress": ["p"],
            "key_info": {cat: ["k"] for cat in C.KEY_INFO_CATEGORIES},
            "file_ops": [], "pending": [],
        })

    res = C.llm_summarize([{"role": "user", "content": "内容"}], llm_fn=_fake_llm)
    assert res is not None and seen, "摘要桩未生效"
    assert seen["temperature"] == llm_client.STABLE_TEMPERATURE


def test_points_completion_uses_stable_temperature(tmp_path, monkeypatch):
    """采分点补全（抽取类）显式关采样。"""
    import tools.skills.material_ingestion as MI
    import tools.llm_client as lc

    seen = {}

    def _fake_chat(prompt, **kw):
        seen.update(kw)
        return json.dumps({"rubric": ["[+2分] 步骤1"], "points": ["考点1"],
                           "analysis": "解析"}, ensure_ascii=False)

    monkeypatch.setattr(lc, "is_llm_configured", lambda *a, **k: True)
    monkeypatch.setattr(lc, "chat_completion", _fake_chat)

    chunk = MI.QuestionChunk(number=1, q_type="choice", score=5,
                              stem="题干", answer="答案")
    assert MI.MaterialIngestionPipeline(
        workspace_root=tmp_path).enrich_rubric_with_llm(chunk) is True
    assert seen["temperature"] == llm_client.STABLE_TEMPERATURE


def test_variant_retriever_uses_stable_temperature(monkeypatch):
    """变式题命制（抽取/结构化生成）显式关采样。"""
    import tools.skills.variant_retriever as VR
    import tools.llm_client as lc

    seen = {}

    def _fake_chat(prompt, **kw):
        seen.update(kw)
        return "1. 下列关于教育学基本概念的表述中，正确的是（　）。A. 强调产出 B. 无关"

    monkeypatch.setattr(lc, "is_llm_configured", lambda *a, **k: True)
    monkeypatch.setattr(lc, "chat_completion", _fake_chat)

    out = VR._generate_synthetic_variant("pro", "教育学")
    assert out, "应产出带水印的自拟变式题"
    assert seen["temperature"] == llm_client.STABLE_TEMPERATURE