# -*- coding: utf-8 -*-
"""[W8-C] 产物落盘机械闸门 —— 回归测试。

背景（KaoyanBench 评测实测 RES-002）
------------------------------------
任务 prompt 已硬性要求「必须用 write_file 工具实际写入 output/report.json」，
被测 Agent 仍可能**零 write_file 调用**：主循环因步数耗尽退出，收尾链（禁用
工具）却在回复文本里**幻觉声称**「written: output/report.json」→ 下游
``file_exists`` 检查直接判负。仅靠提示词无法根治。

修复（tools/agent/loop.py，W8-C 产物闸门）
-----------------------------------------
1. ``_parse_required_outputs`` 从任务文本**保守**解析必须落盘的 output/ 产物
   （仅两种明确措辞、去重、最多 3 个；解析不到 → 闸门完全关闭，零行为变化）；
2. 主循环 + 收尾链结束后 ``_missing_required_outputs`` 校验产物是否**真实
   存在**；缺失则注入一条 user 补写指令（EVENT_USER kind=deliverable_nudge）、
   放宽步数预算（+6 步）并重入主循环（此时工具可用，模型会去调 write_file）；
3. nudge 硬上限 2 次（保证不死循环）；网络硬失败且毫无产出时跳过
   （再发请求只会白等超时，不可能写出文件）；
4. 解析 / 校验 / 注入任一步异常都**静默跳过**，绝不拦住 run()。

本文件覆盖（全程离线：mock ``loop.safe_urlopen`` 喂 SSE 假响应，绝不联网）
-----------------------------------------------------------------------
a) 产物已存在 → 零 nudge，LLM 调用数/回调序列与无闸门基线一致；
b) 缺失 → 恰好 nudge 1 次（注入 user 消息 + 事件 + step_callback 提示），
   次轮 write_file 真实落盘后正常收尾；
c) 两次 nudge 后仍缺失 → 停止，LLM 调用数有界（不死循环）；
d) prompt 无产物措辞 → 闸门不激活（零行为变化）；
e) 两个产物路径 → 都检查；缺失清单只列**仍缺失**的（部分完成场景）；
f) 工作区根异常 / 单条路径校验异常 / run 内校验异常 → 静默跳过不崩；
g) nudge 不污染最终答案（与无闸门基线逐字一致）；
h) 步数耗尽（max_steps=1）→ nudge 放宽 step_budget 后补写仍能发生并落盘；
i) 网络硬失败且毫无产出 → 跳过闸门；
j) ``_parse_required_outputs`` 保守解析边界（去重 / 上限 3 / 尾部标点 /
   中文字符集）。

SSE 构造与 runner 工厂直接复用同批次 ``test_w8_streaming_client`` 的既有
mock 设施，避免两套实现漂移。
"""

import http.client
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(TESTS_DIR), str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tools.agent.loop as loop_module  # noqa: E402
from tools.agent.loop import AgentRunner  # noqa: E402
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

#: 触发闸门：含「实际写入 output/report.json」。
PROMPT_GATE = (
    "这是硬性要求：必须用 write_file 工具实际写入 output/report.json"
    "（仅把内容写在回复文本里不算完成；结束前请确认文件已落盘）"
)
#: 对照措辞：不含任何 output/ 产物要求 → 闸门必须完全关闭。
PROMPT_PLAIN = "这是硬性要求：请直接给出最终报告内容，仅写在回复文本里即可"


# ── mock 设施 ───────────────────────────────────────────────────────────


def _text_body(text: str) -> bytes:
    """无工具调用的普通回复（主循环应在此终止）。"""
    return sse_body(delta(text), finish("stop"))


def _tool_body(name: str, args: dict, call_id: str = "call_w8") -> bytes:
    """带工具调用的回复（模型继续要工具 → 循环继续）。"""
    return sse_body(
        tool_delta(0, call_id=call_id, name=name,
                   args=json.dumps(args, ensure_ascii=False)),
        finish("tool_calls"),
    )


def _write_file_body(path: str, content: str) -> bytes:
    return _tool_body("write_file", {"path": path, "content": content})


def _scripted_urlopen(specs):
    """按调用次序返回 SSE 假响应；返回 ``(fake, calls)``。

    ``specs`` 元素为 SSE 响应体 bytes 或 Exception；脚本耗尽后沿用最后一项
    （便于断言「不会多调」）。与流式测试的 ``_make_urlopen`` 不同：这里
    **每次调用都新建** ``FakeStreamResponse`` —— 同一响应对象被二次 read 只会
    拿到空流（假失败），而闸门场景需要多次连续调用。
    """
    calls = []

    def fake(req, timeout=None):
        calls.append({"timeout": timeout,
                      "body": json.loads(req.data.decode("utf-8"))})
        idx = min(len(calls) - 1, len(specs) - 1)
        item = specs[idx]
        if isinstance(item, Exception):
            raise item
        return FakeStreamResponse(item)

    return fake, calls


def _events(tmp_path, event_type=None):
    """读取本工作区全部会话事件；可按 type 过滤。"""
    events = []
    for p in (tmp_path / ".memory" / "sessions").glob("*.jsonl"):
        events.extend(load_events(p))
    if event_type is None:
        return events
    return [e for e in events if e.get("type") == event_type]


def _nudges(tmp_path):
    """所有产物闸门 nudge 事件（user 事件 kind=deliverable_nudge）。"""
    return [e for e in _events(tmp_path, "user")
            if (e.get("payload") or {}).get("kind") == "deliverable_nudge"]


def _nudge_messages(call):
    """某次 LLM 请求里注入的产物闸门 user 消息。"""
    return [m for m in (call["body"].get("messages") or [])
            if isinstance(m, dict) and m.get("role") == "user"
            and "产物落盘闸门" in str(m.get("content") or "")]


def _gate_steps(steps):
    return [s for s in steps if "📎 [产物闸门]" in s]


# ── a) 产物已存在：零 nudge，与无闸门基线一致 ───────────────────────────


def test_existing_deliverable_zero_nudge_matches_baseline(tmp_path, monkeypatch):
    """产物已在 → 闸门零动作；调用数/回调序列与「无闸门基线」完全一致。"""
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "report.json").write_text('{"ok": true}', encoding="utf-8")

    fake1, calls1 = _scripted_urlopen([_text_body("任务完成，报告已就绪。")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake1)
    steps1 = []
    answer1 = _make_runner(tmp_path, step_callback=steps1.append).run(
        PROMPT_GATE, interactive=False)

    fake2, calls2 = _scripted_urlopen([_text_body("任务完成，报告已就绪。")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake2)
    steps2 = []
    answer2 = _make_runner(tmp_path, step_callback=steps2.append).run(
        PROMPT_PLAIN, interactive=False)

    assert answer1 == answer2 == "任务完成，报告已就绪。"
    assert len(calls1) == len(calls2) == 1, "产物已在 → 不得产生任何额外调用"
    assert _nudges(tmp_path) == []
    assert _gate_steps(steps1) == []
    assert steps1 == steps2, "闸门未激活 → 回调序列与基线逐条一致"


# ── b) 缺失 → nudge 1 次 → 补写真实落盘 ─────────────────────────────────


def test_missing_deliverable_nudged_then_written(tmp_path, monkeypatch):
    """缺失 → nudge 1 次（消息/事件/回调）→ 次轮 write_file 真实落盘 → 收尾。"""
    fake, calls = _scripted_urlopen([
        _text_body("已完成，written: output/report.json"),          # 幻觉声称
        _write_file_body("output/report.json", '{"answer": 42}'),   # 补写
        _text_body("已写入 output/report.json。"),
    ])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    steps = []

    answer = _make_runner(tmp_path, step_callback=steps.append).run(
        PROMPT_GATE, interactive=False)

    assert answer == "已写入 output/report.json。"
    assert len(calls) == 3, "1 次幻觉初答 + 1 次补写 + 1 次收尾"
    # 产物必须真实落盘（不是回复文本里的声称）
    report = tmp_path / "output" / "report.json"
    assert report.read_text(encoding="utf-8") == '{"answer": 42}'
    # 事件与回调
    nudges = _nudges(tmp_path)
    assert len(nudges) == 1
    assert "output/report.json" in nudges[0]["payload"]["content"]
    assert len(_gate_steps(steps)) == 1
    # nudge 以 user 消息注入**补写轮**请求，且该轮携带工具（否则无法补写）
    nudge_msgs = _nudge_messages(calls[1])
    assert len(nudge_msgs) == 1
    assert "output/report.json" in nudge_msgs[0]["content"]
    assert calls[1]["body"].get("tools"), "补写轮必须携带工具"


# ── c) nudge 上限：两次后仍缺失 → 停止，调用数有界 ───────────────────────


def test_nudges_capped_no_infinite_loop(tmp_path, monkeypatch):
    """模型始终只声称不写 → 最多 nudge 2 次后停止；LLM 调用数有界。"""
    fake, calls = _scripted_urlopen([_text_body("已完成，written: output/report.json")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    steps = []

    answer = _make_runner(tmp_path, step_callback=steps.append).run(
        PROMPT_GATE, interactive=False)

    assert answer == "已完成，written: output/report.json"
    assert len(calls) == 3, "1 次初答 + 2 次 nudge 后重入；不得无限重入"
    assert len(_nudges(tmp_path)) == 2
    assert len(_gate_steps(steps)) == 2
    assert not (tmp_path / "output" / "report.json").exists()


# ── d) prompt 无产物措辞 → 闸门不激活 ───────────────────────────────────


def test_prompt_without_pattern_gate_inactive(tmp_path, monkeypatch):
    """无 output/ 措辞 → 即使产物缺失也不得激活闸门（零行为变化）。"""
    fake, calls = _scripted_urlopen([_text_body("最终报告内容。")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    steps = []

    answer = _make_runner(tmp_path, step_callback=steps.append).run(
        PROMPT_PLAIN, interactive=False)

    assert answer == "最终报告内容。"
    assert len(calls) == 1
    assert _nudges(tmp_path) == []
    assert _gate_steps(steps) == []
    assert not (tmp_path / "output").exists()


# ── e) 两个产物路径：都检查，缺失清单只列仍缺的 ─────────────────────────


def test_two_deliverables_partial_completion(tmp_path, monkeypatch):
    """两个产物都检查；只补写其一 → 第二次 nudge 只列仍缺失的那个。"""
    prompt = "请实际写入 output/a.json，并实际写入 output/b.md"
    fake, calls = _scripted_urlopen([
        _text_body("两份产物都已完成。"),                  # 幻觉：一份都没写
        _write_file_body("output/a.json", '{"a": 1}'),      # 只补写第一份
        _text_body("a.json 已写入。"),                      # 收尾（b.md 仍缺）
        _write_file_body("output/b.md", "# b"),             # 第二次 nudge 后补写
        _text_body("两份产物均已写入。"),
    ])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    answer = _make_runner(tmp_path).run(prompt, interactive=False)

    assert answer == "两份产物均已写入。"
    assert len(calls) == 5
    nudges = _nudges(tmp_path)
    assert len(nudges) == 2
    first = nudges[0]["payload"]["content"]
    second = nudges[1]["payload"]["content"]
    assert "output/a.json" in first and "output/b.md" in first, \
        "首次缺失清单应列出全部两个产物"
    assert "output/b.md" in second and "output/a.json" not in second, \
        "第二次只应列出仍缺失的 b.md（已落盘的不得再列）"
    assert (tmp_path / "output" / "a.json").read_text(encoding="utf-8") == '{"a": 1}'
    assert (tmp_path / "output" / "b.md").read_text(encoding="utf-8") == "# b"


# ── f) 异常路径：静默跳过，绝不拦住 run() ───────────────────────────────


def test_missing_check_root_error_returns_empty(tmp_path):
    """工作区根取值异常 → ``_missing_required_outputs`` 返回 []（闸门关闭）。"""
    runner = _make_runner(tmp_path)

    class _BadRoot:
        @property
        def workspace_root(self):
            raise RuntimeError("模拟沙箱根异常")

    runner.sandbox = _BadRoot()
    assert runner._missing_required_outputs(["output/report.json"]) == []


def test_missing_check_single_path_error_skipped(tmp_path, monkeypatch):
    """单条路径校验抛错 → 跳过该条，其余照常判定，绝不崩。"""
    runner = _make_runner(tmp_path)
    orig_exists = Path.exists

    def flaky_exists(self, *args, **kwargs):
        if "\x00" in str(self):
            raise OSError("模拟非法路径校验失败")
        return orig_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", flaky_exists)
    missing = runner._missing_required_outputs(
        ["output/ok.json", "output/\x00bad.json"])
    assert missing == ["output/ok.json"], "坏路径静默跳过，正常路径照常判定"


def test_gate_silently_skipped_when_check_raises_in_run(tmp_path, monkeypatch):
    """run() 内校验异常 → 视为无缺失，正常收尾（不 nudge、不重入、不崩）。"""
    fake, calls = _scripted_urlopen([_text_body("任务完成。")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    orig_exists = Path.exists

    def flaky_exists(self, *args, **kwargs):
        if "report.json" in str(self):
            raise OSError("模拟文件系统校验异常")
        return orig_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", flaky_exists)
    steps = []

    answer = _make_runner(tmp_path, step_callback=steps.append).run(
        PROMPT_GATE, interactive=False)

    assert answer == "任务完成。"
    assert len(calls) == 1, "校验异常 → 视为无缺失，不得重入"
    assert _nudges(tmp_path) == []
    assert _gate_steps(steps) == []


# ── g) nudge 不污染最终答案 ─────────────────────────────────────────────


def test_nudge_does_not_pollute_final_answer(tmp_path, monkeypatch):
    """闸门激活全程（nudge + 补写）后，最终答案与无闸门基线逐字一致。"""
    final_text = "最终答复：任务已完成，报告在 output/report.json。"
    fake, calls = _scripted_urlopen([
        _text_body("已完成。"),
        _write_file_body("output/report.json", "{}"),
        _text_body(final_text),
    ])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    answer_gate = _make_runner(tmp_path).run(PROMPT_GATE, interactive=False)

    fake2, calls2 = _scripted_urlopen([_text_body(final_text)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake2)
    answer_base = _make_runner(tmp_path).run(PROMPT_PLAIN, interactive=False)

    assert answer_gate == answer_base == final_text
    assert "产物落盘闸门" not in answer_gate
    assert "尚不存在" not in answer_gate
    assert len(calls) == 3 and len(calls2) == 1


# ── h) 步数耗尽 → nudge 放宽预算，补写仍能发生 ──────────────────────────


def test_step_budget_extended_after_nudge(tmp_path, monkeypatch):
    """max_steps=1：第 1 步耗尽后 nudge 放宽 +6 步，补写仍能发生并落盘。"""
    (tmp_path / "input.txt").write_text("样本输入", encoding="utf-8")
    fake, calls = _scripted_urlopen([
        _tool_body("read_file", {"path": "input.txt"}),            # 第 1 步：耗尽预算
        _text_body("已整理完成，written: output/report.json"),      # 收尾链幻觉
        _write_file_body("output/report.json", '{"done": true}'),   # nudge 后补写
        _text_body("报告已写入 output/report.json。"),
    ])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    steps = []

    answer = _make_runner(tmp_path, max_steps=1, step_callback=steps.append).run(
        PROMPT_GATE, interactive=False)

    assert answer == "报告已写入 output/report.json。"
    assert len(calls) == 4, "1 步工具 + 1 次收尾 + 补写 + 收尾"
    # 第 2 次调用是禁用工具的收尾链请求；第 3、4 次（步数已耗尽仍能发生）
    # 只能由 nudge 放宽 step_budget 带来 —— 否则内层循环条件永假、补写不可能发生。
    assert "tools" not in calls[1]["body"], "收尾链必须禁用工具"
    assert calls[2]["body"].get("tools"), "补写轮必须携带工具"
    assert (tmp_path / "output" / "report.json").read_text(
        encoding="utf-8") == '{"done": true}'
    assert len(_nudges(tmp_path)) == 1
    assert len(_gate_steps(steps)) == 1


# ── i) 网络硬失败且毫无产出 → 跳过闸门 ─────────────────────────────────


def test_api_hard_failure_with_no_output_skips_gate(tmp_path, monkeypatch):
    """全部请求网络失败且无任何产出 → 不 nudge（再发只会白等超时）。"""
    fake, calls = _scripted_urlopen([http.client.RemoteDisconnected("模拟断连")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module.time, "sleep", lambda s: None)

    answer = _make_runner(tmp_path).run(PROMPT_GATE, interactive=False)

    assert answer == ""
    # 主循环 3 次尝试 + 收尾两档（极简/全量）各 3 次尝试
    assert len(calls) == 9
    assert _nudges(tmp_path) == []
    assert all(e["payload"]["error_kind"] == "network"
               for e in _events(tmp_path, "llm_call"))


# ── j) 保守解析边界 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    # 模式一：实际写入 output/...
    ("实际写入 output/report.json", ["output/report.json"]),
    ("实际写入 output/report.json，然后再复查一遍", ["output/report.json"]),
    ("实际写入 output/report.json.", ["output/report.json"]),   # 尾部 ASCII 句点剥掉
    ("实际写入 output/report.json。", ["output/report.json"]),   # 中文标点不在字符集内
    ("实际写入 output/report.json文件", ["output/report.json"]),  # 中文字符不被吞入
    # 模式二：写入 output/xxx.ext（无「实际」也可）
    ("写入 output/summary.md", ["output/summary.md"]),
    ("写入 output/notes.txt", ["output/notes.txt"]),
    # 无 output/ 前缀或无语义措辞 → 保守返回 []（闸门关闭）
    ("写入 report.json", []),
    ("请完成任务并给出最终报告", []),
    ("把结果输出到 output/report.json", []),                    # 无「写入」措辞
    # 去重
    ("实际写入 output/report.json 与 实际写入 output/report.json",
     ["output/report.json"]),
    # 上限 3 个
    ("实际写入 output/a.json 实际写入 output/b.md "
     "实际写入 output/c.txt 实际写入 output/d.csv",
     ["output/a.json", "output/b.md", "output/c.txt"]),
])
def test_parse_required_outputs_conservative(text, expected):
    assert AgentRunner._parse_required_outputs(text) == expected


def test_parse_required_outputs_invalid_input():
    """None / 空串 / 非字符串 → 一律 []（绝不抛）。"""
    assert AgentRunner._parse_required_outputs(None) == []
    assert AgentRunner._parse_required_outputs("") == []
    assert AgentRunner._parse_required_outputs(12345) == []


# ── [W9] autosave 兜底：nudge 用尽后把可解析 JSON 机械落盘 ─────────────


def test_autosave_writes_json_after_nudges_exhausted(tmp_path, monkeypatch):
    """模型 2 次 nudge 后仍不写、但 final_answer 是可解析 JSON →
    兜底机械落盘（内容与模型输出逐字段一致，零伪造）。"""
    payload = {"rows": [{"year": 2025, "min_score": 318, "avg_score": 355.78}],
               "major": "示例专业"}
    body = _text_body(json.dumps(payload, ensure_ascii=False))
    fake, calls = _scripted_urlopen([body])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    answer = _make_runner(tmp_path, step_callback=[].append).run(
        PROMPT_GATE, interactive=False)

    assert len(calls) == 3, "1 次初答 + 2 次 nudge 重入"
    assert len(_nudges(tmp_path)) == 2
    p = tmp_path / "output" / "report.json"
    assert p.exists(), "兜底应把可解析 JSON 落盘"
    assert json.loads(p.read_text(encoding="utf-8")) == payload
    evs = [e for e in _events(tmp_path, "tool_result")
           if (e.get("payload") or {}).get("kind") == "deliverable_autosave"]
    assert len(evs) == 1
    assert "output/report.json" in str(evs[0]["payload"]["content"])
    assert answer.strip() == json.dumps(payload, ensure_ascii=False)


def test_autosave_extracts_fenced_json(tmp_path, monkeypatch):
    """final_answer 是「散文 + ```json 代码块」→ 提取代码块 JSON 落盘。"""
    payload = {"rows": []}
    text = "分析如下：\n```json\n" + json.dumps(payload) + "\n```\n以上。"
    fake, _ = _scripted_urlopen([_text_body(text)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    _make_runner(tmp_path, step_callback=[].append).run(
        PROMPT_GATE, interactive=False)
    p = tmp_path / "output" / "report.json"
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8")) == payload


def test_autosave_skips_unparseable_answer(tmp_path, monkeypatch):
    """final_answer 无任何 JSON → 兜底静默跳过：不写文件、不崩、答案不变。"""
    text = "我无法完成该任务，数据缺失，无法给出表格。"
    fake, calls = _scripted_urlopen([_text_body(text)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    answer = _make_runner(tmp_path, step_callback=[].append).run(
        PROMPT_GATE, interactive=False)
    assert not (tmp_path / "output" / "report.json").exists()
    assert len(calls) == 3
    assert answer == text
