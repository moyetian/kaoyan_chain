# -*- coding: utf-8 -*-
"""K8 批次回归 · 回合恢复：Doom-loop 熔断 + 错误分类分流。

背景（agent 内核修复方案 K8）：两条「回合级」自愈链路 ——
  U1 ``DoomLoopBreaker``：模型（尤其 flash 级）在工具失败后会**原样重发**
     同一次调用（相同工具+相同参数），把步数烧在不可能成功的重复上。
     同签名连续第 3 次 → 熔断跳过执行，合成 ``DoomLoopFused`` 结果引导
     改道；run 级作用域（每轮 run 新建，计数重置）。
  U2 错误分类分流：``_classify_error_hint`` 从 HTTP 状态码 + 响应体识别
     401/403（auth，key 无效）与 400/413+上下文超长特征（overflow）——
     auth 跳过收尾链（注定失败）；overflow 强制压缩后重试**恰一次**。

本文件锁定（不得回退）：
1. 熔断器单元语义：第 3 次熔断、第 4/5 次仍熔、签名变化重置、interactive
   注入键忽略、键序无关、异常参数不崩、阈值可配；
2. loop 集成：熔断调用**不执行工具**、tool_result 事件 kind=doom_loop_fused、
   每轮 run 重置、hook 拦截的调用不计数、签名变化后恢复执行；
3. ``_classify_error_hint`` 分类矩阵（独立于 llm_call 遥测 error_kind）；
4. auth：跳过 ``_recover_final_answer`` 与 ``_repair_json_answer``（零收尾
   请求），回退最后一条非空 assistant 文本；
5. overflow：``compact_context(force=True)`` 恰一次 + 重试；再次 overflow
   不重复压缩，走 api_failed 收尾。

全程离线：monkeypatch ``AgentRunner._call_llm`` 与工具执行，零网络。
夹具使用合成占位串，不含任何真实身份信息。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.turn_recovery import DoomLoopBreaker, FUSE_THRESHOLD  # noqa: E402
from tools.agent.session_log import load_events, EVENT_TOOL_RESULT  # noqa: E402


# ── 1. DoomLoopBreaker 单元语义 ────────────────────────────────────────


def test_signature_key_order_and_injected_keys():
    """签名与键序无关；interactive 为执行层注入键必须忽略。"""
    s1 = DoomLoopBreaker.signature("read_file", {"path": "a.md", "limit": 5})
    s2 = DoomLoopBreaker.signature("read_file", {"limit": 5, "path": "a.md"})
    assert s1 == s2, "签名必须与键序无关"
    s3 = DoomLoopBreaker.signature(
        "read_file", {"path": "a.md", "limit": 5, "interactive": True})
    assert s3 == s1, "interactive 是执行层注入键（非模型参数），必须忽略"
    assert s1 != DoomLoopBreaker.signature("read_file", {"path": "b.md", "limit": 5})


def test_fuses_on_third_identical_call():
    """同签名连续第 3 次熔断（前 2 次放行）。"""
    b = DoomLoopBreaker()
    assert FUSE_THRESHOLD == 3
    assert b.observe("read_file", {"path": "a.md"}) == (False, 1)
    assert b.observe("read_file", {"path": "a.md"}) == (False, 2)
    assert b.observe("read_file", {"path": "a.md"}) == (True, 3)


def test_keeps_fusing_after_threshold():
    """熔断后继续观察：第 4、5 次仍熔（streak 继续累积），直到签名变化。"""
    b = DoomLoopBreaker()
    for _ in range(3):
        b.observe("read_file", {"path": "a.md"})
    assert b.observe("read_file", {"path": "a.md"}) == (True, 4)
    assert b.observe("read_file", {"path": "a.md"}) == (True, 5)


def test_signature_change_resets_streak():
    """签名变化即重置（「连续」语义，非历史累计）。"""
    b = DoomLoopBreaker()
    b.observe("read_file", {"path": "a.md"})
    b.observe("read_file", {"path": "a.md"})
    assert b.observe("read_file", {"path": "b.md"}) == (False, 1)
    assert b.observe("read_file", {"path": "a.md"}) == (False, 1)


def test_threshold_configurable():
    b = DoomLoopBreaker(threshold=2)
    assert b.observe("t", {}) == (False, 1)
    assert b.observe("t", {}) == (True, 2)


def test_signature_never_raises():
    """异常参数（非 dict / 不可 JSON 序列化）不得让熔断器自身抛错。"""
    class Weird:
        def __repr__(self):
            return "<weird>"

    assert DoomLoopBreaker.signature("t", Weird()).startswith("t|")
    assert DoomLoopBreaker.signature("t", {"k": Weird()}).startswith("t|")
    assert DoomLoopBreaker.signature("t", None).startswith("t|")


def test_fused_result_text():
    """熔断结果：DoomLoopFused 前缀（供 is_err 判定）+ 工具名/次数/改道引导。"""
    txt = DoomLoopBreaker().fused_result("read_file", 3)
    assert txt.startswith("DoomLoopFused")
    assert "read_file" in txt and "3" in txt
    assert "改变策略" in txt


# ── 2. loop 集成：熔断接线 ─────────────────────────────────────────────


def _tool_call(cid, path="a.md"):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": cid, "type": "function",
         "function": {"name": "read_file",
                      "arguments": f'{{"path": "{path}"}}'}}]}}]}


def _final(text="最终答复"):
    return {"choices": [{"message": {"content": text, "tool_calls": []}}]}


def _mk_runner(tmp_path, monkeypatch, scripted):
    """scripted: [(response_or_None, hint_or_None), ...]；耗尽后重复末项。

    hint 模拟真实 ``_emit_llm_call`` 的分流提示写入（成功清空、失败设置）。
    """
    from tools.agent.loop import AgentRunner

    calls = {"n": 0}

    def fake_call_llm(self, messages, allow_tools=True, tools_subset=None):
        i = calls["n"]
        calls["n"] += 1
        resp, hint = scripted[min(i, len(scripted) - 1)]
        self._last_llm_error_hint = hint
        return resp

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    return runner, calls


def _patch_tools(runner):
    """替换 execute_tool 记录调用；返回 calls 列表。"""
    calls = []

    def fake_exec(name, args, interactive=True, call_id=""):
        calls.append({"name": name, "args": dict(args), "call_id": call_id})
        return "样本工具结果"

    runner.tool_registry.execute_tool = fake_exec
    return calls


def test_loop_skips_execution_on_fuse(tmp_path, monkeypatch):
    """同签名连续 3 次：第 3 次不执行工具；事件 kind=doom_loop_fused。"""
    scripted = [(_tool_call("c1"), None), (_tool_call("c2"), None),
                (_tool_call("c3"), None), (_final(), None)]
    runner, _ = _mk_runner(tmp_path, monkeypatch, scripted)
    exec_calls = _patch_tools(runner)

    out = runner.run("样本任务", interactive=False)
    assert out == "最终答复"
    assert [c["name"] for c in exec_calls] == ["read_file", "read_file"], \
        "第 3 次同签名调用必须被熔断跳过（不进入 execute_tool）"

    events = load_events(runner._session_log.path)
    results = [e for e in events if e["type"] == EVENT_TOOL_RESULT]
    assert len(results) == 3
    assert [r["payload"].get("kind") for r in results] == \
        [None, None, "doom_loop_fused"]
    assert results[2]["payload"]["content"].startswith("DoomLoopFused")
    runner.close()


def test_fuse_releases_on_new_signature(tmp_path, monkeypatch):
    """第 3 次熔断后模型换参数 → 签名变化 → 恢复执行。"""
    scripted = [(_tool_call("c1"), None), (_tool_call("c2"), None),
                (_tool_call("c3"), None), (_tool_call("c4", path="b.md"), None),
                (_final(), None)]
    runner, _ = _mk_runner(tmp_path, monkeypatch, scripted)
    exec_calls = _patch_tools(runner)

    assert runner.run("样本任务", interactive=False) == "最终答复"
    assert [c["args"]["path"] for c in exec_calls] == ["a.md", "a.md", "b.md"], \
        "签名变化（换参数）后必须恢复执行"
    runner.close()


def test_doom_loop_resets_each_run(tmp_path, monkeypatch):
    """run 级作用域：每轮 run 新建熔断器，两轮各熔断 1 次（共执行 4 次）。"""
    scripted = [(_tool_call("c1"), None), (_tool_call("c2"), None),
                (_tool_call("c3"), None), (_final("第一轮答复"), None),
                (_tool_call("c4"), None), (_tool_call("c5"), None),
                (_tool_call("c6"), None), (_final("第二轮答复"), None)]
    runner, _ = _mk_runner(tmp_path, monkeypatch, scripted)
    exec_calls = _patch_tools(runner)

    assert runner.run("样本任务一", interactive=False) == "第一轮答复"
    assert runner.run("样本任务二", interactive=False) == "第二轮答复"
    assert len(exec_calls) == 4, \
        "每轮 run 独立计数：两轮各执行 2 次（各熔断 1 次）"
    runner.close()


def test_hook_blocked_calls_not_counted(tmp_path, monkeypatch):
    """被 PreToolUse 拦截的调用不参与熔断计数（只观察真正要执行的调用）。"""
    scripted = [(_tool_call("c1"), None), (_tool_call("c2"), None),
                (_tool_call("c3"), None), (_final(), None)]
    runner, _ = _mk_runner(tmp_path, monkeypatch, scripted)
    exec_calls = _patch_tools(runner)

    from tools.agent.hooks import HookEvent
    state = {"blocked": 0}

    def deny_first(tool_name, tool_args, context):
        if tool_name == "read_file" and state["blocked"] == 0:
            state["blocked"] = 1
            return False, "样本拦截", tool_args
        return True, "ok", tool_args

    runner.hooks.register_hook(HookEvent.PRE_TOOL_USE, deny_first, priority=5)

    assert runner.run("样本任务", interactive=False) == "最终答复"
    assert len(exec_calls) == 2, \
        "拦截不计数：第 2/3 次调用必须真实执行（若拦截计数则第 3 次会误熔断）"
    events = load_events(runner._session_log.path)
    results = [e for e in events if e["type"] == EVENT_TOOL_RESULT]
    assert results[0]["payload"]["content"].startswith("HookBlocked")
    assert all(r["payload"].get("kind") is None for r in results), "全程无熔断"
    runner.close()


# ── 3. 错误分类矩阵 ────────────────────────────────────────────────────


def test_classify_error_hint_matrix():
    """_classify_error_hint：auth / overflow / None 三态分类。"""
    from tools.agent.loop import AgentRunner
    c = AgentRunner._classify_error_hint
    # auth：401/403（int 与数字字符串都识别）
    assert c(401) == "auth"
    assert c(403) == "auth"
    assert c("401") == "auth"
    # overflow：400/413 + body 特征词（大小写无关）
    assert c(400, "This model's maximum context length is 8192 tokens") == "overflow"
    assert c(413, "Request too long") == "overflow"
    assert c(400, "context_length exceeded") == "overflow"
    # 400 无特征词 / 非 400/413 / 非法输入 → None
    assert c(400, "invalid parameter: tools") is None
    assert c(400, "") is None
    assert c(500, "context length") is None
    assert c(429, "rate limit") is None
    assert c(None) is None
    assert c("abc") is None


# ── 4. auth 分流：跳过收尾链 ───────────────────────────────────────────


def test_auth_failure_skips_recovery_chain(tmp_path, monkeypatch):
    """401/403：零收尾请求（_recover/_repair 都不调用），回退兜底文本。"""
    scripted = [
        ({"choices": [{"message": {"content": "样本分析文本", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]}}]}, None),
        (None, "auth"),
    ]
    runner, calls = _mk_runner(tmp_path, monkeypatch, scripted)
    _patch_tools(runner)

    recovered, repaired = [], []
    runner._recover_final_answer = (
        lambda messages, last_text, api_failed=False:
        recovered.append(api_failed) or "不应出现")
    runner._repair_json_answer = lambda fa, msgs: repaired.append(1) or fa

    out = runner.run("样本任务", interactive=False)
    assert out == "样本分析文本", "auth 失败必须回退最后一条非空 assistant 文本"
    assert recovered == [], "auth 失败必须跳过收尾请求链"
    assert repaired == [], "auth 失败必须跳过 JSON 修复请求"
    assert calls["n"] == 2, "auth 失败后不再发任何请求"
    runner.close()


# ── 5. overflow 分流：强制压缩重试恰一次 ───────────────────────────────


def _spy_compact(runner):
    """记录 compact_context 每次调用的 force 值；返回记录列表。"""
    calls = []
    orig = runner.context_engine.compact_context

    def spy(messages, hook_manager=None, focus=None, force=False):
        calls.append(force)
        return orig(messages, hook_manager=hook_manager, focus=focus, force=force)

    runner.context_engine.compact_context = spy
    return calls


def test_overflow_forces_compact_and_retries_once(tmp_path, monkeypatch):
    """400/413 上下文超长：force=True 强制压缩恰一次 → 重试成功。"""
    scripted = [(None, "overflow"), (_final("重试成功答复"), None)]
    runner, calls = _mk_runner(tmp_path, monkeypatch, scripted)
    compacts = _spy_compact(runner)

    out = runner.run("样本任务", interactive=False)
    assert out == "重试成功答复"
    assert calls["n"] == 2, "overflow 后必须重试恰一次"
    # 序列 = [run 开头的常规防爆压缩(False), overflow 强制压缩(True)]
    assert compacts == [False, True], "必须触发恰一次 force=True 的强制压缩"
    runner.close()


def test_overflow_retries_only_once_then_recovers(tmp_path, monkeypatch):
    """连续 overflow：不重复压缩；第二次后走 api_failed 收尾（非 auth）。"""
    scripted = [(None, "overflow"), (None, "overflow")]
    runner, calls = _mk_runner(tmp_path, monkeypatch, scripted)
    compacts = _spy_compact(runner)

    recovered, repaired = [], []
    runner._recover_final_answer = (
        lambda messages, last_text, api_failed=False:
        recovered.append(api_failed) or "收尾兜底")
    runner._repair_json_answer = lambda fa, msgs: repaired.append(1) or fa

    out = runner.run("样本任务", interactive=False)
    assert out == "收尾兜底"
    assert calls["n"] == 2, "第二次 overflow 不再重试（无第三次请求）"
    assert compacts == [False, True], "强制压缩恰一次，不重复（False=run 开头常规压缩）"
    assert recovered == [True], "连续 overflow 走 api_failed 收尾（非 auth）"
    runner.close()
