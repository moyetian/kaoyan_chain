# -*- coding: utf-8 -*-
"""[P1 修复·2026-10-08 深度研究 A 组] R1/R2/R8/R9 四条回归测试。

四条缺陷（全部落在 tools/intelligence/agentic_research.py）：

  * R1 —— 研究循环无收尾轮：``execute_loop`` 在 max_steps 耗尽时返回最后一条
    消息（通常是 tool 消息 —— 工具结果 JSON 被当成「研究结论」交给调用方，
    解析不出 JSON 即整体降级，半成品丢弃）。修复：循环后追加「禁用工具的
    收尾请求」（对照 agent 内核三级收尾链：FINALIZE → RETRY → MINIMAL 极简）。
  * R2 —— 裸 urllib 调用：研究引擎自建裸 urllib 出站（无 max_tokens /
    Retry-After / 流式），R3 波动收敛批次未覆盖此路径。修复：改走
    llm_client.request_chat（max_retries=0 单次尝试 + 外层 deadline 感知重试链）。
  * R8 —— 递归守卫失效：threading.local 守卫在 daemon 工具线程路径失效
    （scout_school 回调链可重入；新线程里 depth 恒 0）。修复：contextvars +
    创建工具线程时 copy_context() 显式播种。
  * R9 —— 无证据充分性检查：「深度研究」实为单轮抽取+检索。修复：轻量证据
    闸门（retrieval_ok/grounded 复用 P0-5 判据单源实现）+ 定向补检索回路
    （至多一轮；失败保留首轮结论）。

全部用例零真实联网：LLM 出站打桩 ar.safe_urlopen（request_chat 注入点），
工具 dispatcher 打桩；身份一律中性化（示例大学 / example.edu.cn）。
阴性对照（修复回退即变红）在各类 docstring 内注明。
"""

import io
import json
import sys
import threading
import time
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.intelligence import agentic_research as ar

_SCHOOL = "示例大学"
_MAJOR = "马克思主义理论"
_API_CFG = {"api_key": "sk-mock-valid-key",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat"}

_HIT_URL = "https://example.edu.cn/zsml2027.html"
_HIT_SNIPPET = "示例大学2027年硕士研究生招生简章：马克思主义理论专业拟招12人。"
_GROUNDED_QUOTE = "马克思主义理论专业拟招12人"
_VERIFIED = "[RESEARCH_VERIFIED 深度研招检索]"
_UNVERIFIED = "[UNVERIFIED 在线生成·未溯源]"


class _FakeResp:
    """safe_urlopen 最小鸭子类型替身（read(*a) 兼容两种读取契约）。"""

    headers = {"Content-Encoding": ""}

    def __init__(self, payload):
        self._payload = payload

    def read(self, *a):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, retry_after=None, msg="stub error"):
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://api.deepseek.com/v1/chat/completions",
                                  code, msg, hdrs, io.BytesIO(b'{"error":"stub"}'))


def _turn_tool_call(name, args):
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": name,
                                     "arguments": json.dumps(args, ensure_ascii=False)}}]}}]}


def _turn_content(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _patch_llm(monkeypatch, responses, captured=None):
    """打桩 LLM 出站：按队列回放响应；captured 记录每次请求 payload。

    队列空时的额外请求一律回 401（确定性错误，不触发重试链的退避等待）——
    既如实暴露「测试期望外的请求」，又让补检索等尽力而为路径快速失败。
    """
    queue = [json.dumps(r, ensure_ascii=False).encode("utf-8") for r in responses]

    def fake_urlopen(req, timeout=None):
        if captured is not None:
            captured.append(json.loads(req.data.decode("utf-8")))
        if not queue:
            raise _http_error(401)
        return _FakeResp(queue.pop(0))

    monkeypatch.setattr(ar, "safe_urlopen", fake_urlopen)


def _hit_dispatch(name, **kwargs):
    return [{"title": "示例大学2027年硕士研究生招生简章",
             "snippet": _HIT_SNIPPET, "url": _HIT_URL, "provider": "stub"}]


def _cooling_dispatch(name, **kwargs):
    return [{"error": "全部检索源处于反爬冷却期", "all_failed_cooling": True}]


def _online_json(sources=None):
    payload = {"name": _SCHOOL, "code": "99999", "region": "示例省",
               "majors": ["(101)思想政治理论", "(201)英语(一)"]}
    if sources is not None:
        payload["sources"] = sources
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


@pytest.fixture(autouse=True)
def _clean_records():
    """每个用例后清空本线程的工具记录容器（thread-local，防残留串场）。"""
    yield
    ar._TOOL_RECORDS.records = None


# ════════════════════════════════════════════════════════════════
# R1 研究循环无收尾轮
# ════════════════════════════════════════════════════════════════

class TestR1FinalizeChain:
    """max_steps 耗尽 → 三级收尾链（FINALIZE → RETRY → MINIMAL 极简）。"""

    def test_max_steps_exhausted_appends_finalize_request(self, monkeypatch):
        """步数耗尽后追加禁用工具的收尾请求，返回研究总结（而非工具 JSON）。

        修复前：返回最后一条 tool 消息 content（工具结果 JSON 原样当结论）。
        阴性对照：移除收尾链 → 返回值变回工具 JSON → 本用例红。
        """
        captured = []
        _patch_llm(monkeypatch, [
            _turn_tool_call("web_search", {"query": "q1"}),
            _turn_tool_call("web_search", {"query": "q2"}),
            _turn_content("【收尾总结】示例大学拟招 12 人。"),
        ], captured=captured)
        engine = ar.AgenticResearchEngine()
        engine.max_steps = 2
        engine.dispatcher.dispatch = _hit_dispatch

        res = engine.execute_loop("研究指令", api_config=dict(_API_CFG),
                                  deadline=time.monotonic() + 30.0)

        assert res == "【收尾总结】示例大学拟招 12 人。"
        assert len(captured) == 3           # 2 轮主循环 + 1 次收尾请求
        # 收尾请求必须禁用工具（allow_tools=False 语义：不携带 tools 字段），
        # 主循环请求则必须携带。
        assert "tools" in captured[0]
        assert "tools" not in captured[-1]
        # 收尾指令已附加为最后一条 user 消息
        assert "步数已用尽" in captured[-1]["messages"][-1]["content"]

    def test_finalize_chain_retries_on_empty_then_succeeds(self, monkeypatch):
        """第一级收尾返回空 content → 自动升级第二级（换角度指令）并成功。

        阴性对照：只保留单级收尾 → 返回空后直接落到旧兜底（工具 JSON）→ 红。
        """
        captured = []
        _patch_llm(monkeypatch, [
            _turn_tool_call("web_search", {"query": "q1"}),
            _turn_content(""),                        # 第一级收尾：空回复
            _turn_content("第二级收尾成功：已获取关键信息。"),
        ], captured=captured)
        engine = ar.AgenticResearchEngine()
        engine.max_steps = 1
        engine.dispatcher.dispatch = _hit_dispatch

        res = engine.execute_loop("研究指令", api_config=dict(_API_CFG),
                                  deadline=time.monotonic() + 30.0)

        assert res == "第二级收尾成功：已获取关键信息。"
        assert len(captured) == 3
        # 第二级指令与第一级不同（换角度激发输出）
        assert "上一条回复为空" in captured[-1]["messages"][-1]["content"]

    def test_finalize_chain_minimal_fallback_uses_tool_summaries(self, monkeypatch):
        """前两级收尾均为空 → 第三级用极简消息（系统+任务+工具结果摘要）成功。

        阴性对照：移除极简级 → 落到旧兜底（工具 JSON）→ 红。
        """
        captured = []
        _patch_llm(monkeypatch, [
            _turn_tool_call("web_search", {"query": "q1"}),
            _turn_content(""),
            _turn_content(""),
            _turn_content("极简收尾成功。"),
        ], captured=captured)
        engine = ar.AgenticResearchEngine()
        engine.max_steps = 1
        engine.dispatcher.dispatch = _hit_dispatch

        res = engine.execute_loop("研究指令", api_config=dict(_API_CFG),
                                  deadline=time.monotonic() + 30.0)

        assert res == "极简收尾成功。"
        assert len(captured) == 4
        # 第三级请求使用极简消息：含工具结果摘要、不含完整对话历史
        minimal_msgs = captured[-1]["messages"]
        joined = "\n".join(str(m.get("content") or "") for m in minimal_msgs)
        assert "【已获取的工具结果摘要】" in joined
        assert "研究指令" in joined        # 任务文本保留

    def test_no_tool_success_skips_finalize_request(self, monkeypatch):
        """无任何成功工具产出（全源冷却空转）→ 不追加收尾请求，保持旧返回。

        没有可总结的信息时再花一次请求只会产出空洞内容；该场景调用方解析
        失败自然走本地降级（test_fix_20261005_runtime 的冷却用例同契约）。
        阴性对照：去掉 any_tool_ok 前提 → 会多发收尾请求 → 请求数变 3 → 红。
        """
        captured = []
        _patch_llm(monkeypatch, [
            _turn_tool_call("web_search", {"query": "q1"}),
            _turn_tool_call("web_search", {"query": "q2"}),
        ], captured=captured)
        engine = ar.AgenticResearchEngine()
        engine.max_steps = 2
        engine.dispatcher.dispatch = _cooling_dispatch

        res = engine.execute_loop("研究指令", api_config=dict(_API_CFG),
                                  deadline=time.monotonic() + 30.0)

        assert len(captured) == 2           # 无收尾请求
        # 旧行为兜底：最后一条 tool 消息 content（冷却错误 JSON）
        assert isinstance(res, str) and res.lstrip().startswith("[")

    def test_expired_deadline_skips_finalize_request(self, monkeypatch):
        """预算已尽 → 不发收尾请求（不发注定被掐断的请求），回退旧返回。

        与既有 test_execute_loop_expired_deadline_breaks_without_llm_call 的
        契约一致（那里钉「循环开头不发起请求」，这里钉「收尾链也不发起」）。
        """
        captured = []
        _patch_llm(monkeypatch, [], captured=captured)
        engine = ar.AgenticResearchEngine()
        engine.max_steps = 2

        res = engine.execute_loop("研究指令", api_config=dict(_API_CFG),
                                  deadline=time.monotonic() - 1.0)

        assert len(captured) == 0
        assert res == "研究指令"            # 旧行为兜底：最后一条消息 content


# ════════════════════════════════════════════════════════════════
# R2 裸 urllib → llm_client.request_chat
# ════════════════════════════════════════════════════════════════

class TestR2LlmClientPath:
    """研究引擎出站收敛到 llm_client（流式 / max_tokens / Retry-After / 熔断）。"""

    def test_request_chat_is_the_single_outbound_path(self, monkeypatch):
        """execute_loop 的 LLM 请求必须经 request_chat（且 payload 对齐主链路）。

        修复前：自建裸 urllib，payload 无 stream / max_tokens。
        阴性对照：改回裸 urllib → request_chat 计数为 0 → 红。
        """
        captured = []
        _patch_llm(monkeypatch, [_turn_content("完成")], captured=captured)
        rc_calls = []
        orig = ar.request_chat

        def wrapped(*a, **k):
            rc_calls.append(1)
            return orig(*a, **k)

        monkeypatch.setattr(ar, "request_chat", wrapped)
        res = ar.AgenticResearchEngine().execute_loop("研究指令", api_config=dict(_API_CFG))

        assert res == "完成"
        assert len(rc_calls) == 1
        payload = captured[0]
        assert payload.get("stream") is True                    # SSE 流式（绕网关墙）
        assert payload.get("max_tokens") == ar.DEFAULT_MAX_TOKENS  # 输出上限单一真源
        assert "tools" in payload and "tool_choice" in payload

    def test_retry_after_is_respected_on_429(self, monkeypatch):
        """429 + Retry-After: 2 → 外层重试等待遵守网关要求（2.0s）。

        修复前：full jitter U(0,1) 随机等待，不读 Retry-After（反爬场景等于
        无视服务端冷却要求）。
        阴性对照：退避改回纯 jitter → 等待值 ≤1.0 → 红。
        """
        queue = [_http_error(429, retry_after=2),
                 _FakeResp(json.dumps(_turn_content("完成")).encode())]

        def fake_urlopen(req, timeout=None):
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(ar, "safe_urlopen", fake_urlopen)
        with patch("time.sleep") as mock_sleep:
            res = ar.AgenticResearchEngine().execute_loop(
                "研究指令", api_config=dict(_API_CFG))
        assert res == "完成"
        delays = [float(c.args[0]) for c in mock_sleep.call_args_list]
        assert any(abs(d - 2.0) < 0.01 for d in delays), delays

    def test_auth_error_fails_fast_without_retry(self, monkeypatch):
        """401（auth）确定性失败 → 不重试（熔断式快速失败），异常上抛。

        修复前：HTTPError 是 URLError 子类，被当网络波动重试 3 次（白等 2 轮
        退避 + 3 次注定失败的请求）。
        阴性对照：错误分类缺失 → 请求 3 次 → 红。
        """
        from tools.llm_client import LLMDeterministicError

        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise _http_error(401)

        monkeypatch.setattr(ar, "safe_urlopen", fake_urlopen)
        with pytest.raises(LLMDeterministicError):
            ar.AgenticResearchEngine().execute_loop("研究指令",
                                                    api_config=dict(_API_CFG))
        assert len(calls) == 1              # 不重试


# ════════════════════════════════════════════════════════════════
# R8 递归守卫跨线程（daemon 工具线程内重入）
# ════════════════════════════════════════════════════════════════

class TestR8GuardAcrossThreads:
    """contextvars 守卫 + copy_context 播种：工具线程重入被拦截。"""

    def test_daemon_tool_thread_reentry_is_blocked(self, monkeypatch):
        """daemon 工具线程内重入 research_university_profile → 被守卫拦截。

        真实链路：execute_loop 的 deadline 分支在 daemon 线程里执行工具，
        scout_school 回调链会在该线程里重入研究入口。修复前 thread-local 在
        新线程里 depth 恒 0 → 嵌套研究真的发起在线请求（递归链每层新建线程）。
        阴性对照：去掉 copy_context 播种（直接 Thread(target=_run_tool)）→
        工具线程读到 depth=0、嵌套消费额外响应 → 请求数变 3 → 红。
        """
        captured = []
        _patch_llm(monkeypatch, [
            _turn_tool_call("scout_school", {"school_name": "示例师范大学"}),
            _turn_content("外层非 JSON 结果（避免 R9 补检索干扰计数）"),
            _turn_content("备用响应（守卫生效时不应被消费）"),
        ], captured=captured)
        engine = ar.AgenticResearchEngine()
        seen = {}

        def reentrant_dispatch(name, **kw):
            seen["depth"] = ar._RESEARCH_DEPTH.get()
            seen["profile"] = engine.research_university_profile(
                "示例师范大学", _MAJOR, api_config=dict(_API_CFG), budget_s=30.0)
            return _hit_dispatch(name, **kw)

        engine.dispatcher.dispatch = reentrant_dispatch
        engine.research_university_profile(_SCHOOL, _MAJOR,
                                           api_config=dict(_API_CFG), budget_s=30.0)

        assert seen["depth"] == 1           # copy_context 播种成功
        assert len(captured) == 2           # 嵌套未发起在线请求
        assert "在线" not in str(seen["profile"].get("catalog_source", ""))

    def test_parallel_research_threads_are_not_blocked(self, monkeypatch):
        """并行双校（comparator 线程模型）：两个线程各自研究互不误伤。

        守卫语义是「调用链嵌套」而非「进程内只允许一个研究」——模块级计数器
        方案会把第二个线程误判为嵌套（B 校永远走降级），contextvars 方案按
        调用链隔离。
        阴性对照：守卫改模块级计数器 → 第二个线程被拦 → loop_calls 变 1 → 红。
        """
        engine = ar.AgenticResearchEngine()
        loop_calls = []

        def fake_loop(*a, **k):
            loop_calls.append(1)
            time.sleep(0.08)                # 让两个线程真正重叠
            return ""

        monkeypatch.setattr(engine, "execute_loop", fake_loop)
        done = {}

        def worker(name):
            done[name] = engine.research_university_profile(
                name, _MAJOR, api_config=dict(_API_CFG), budget_s=30.0)

        t1 = threading.Thread(target=worker, args=("示例甲校",))
        t2 = threading.Thread(target=worker, args=("示例乙校",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(loop_calls) == 2         # 两校都进入在线研究
        assert set(done) == {"示例甲校", "示例乙校"}


# ════════════════════════════════════════════════════════════════
# R9 证据充分性检查 + 定向补检索
# ════════════════════════════════════════════════════════════════

class TestR9EvidenceGate:
    """首轮无证据 → 定向补检索（至多一轮；失败保留首轮结论）。"""

    def test_evidence_gap_triggers_targeted_retrieval(self, monkeypatch):
        """首轮零工具、无 sources → 追加一轮补检索（prompt 含缺口说明）。

        修复前：单轮抽取直接带 UNVERIFIED 返回，没有补一次定向检索的机会。
        阴性对照：移除补检索回路 → 请求数变 1 → 红。
        """
        captured = []
        _patch_llm(monkeypatch, [
            _turn_content(_online_json()),                  # 首轮：无证据
            _turn_content(_online_json()),                  # 补检索轮
        ], captured=captured)
        ar.AgenticResearchEngine().research_university_profile(
            _SCHOOL, _MAJOR, api_config=dict(_API_CFG), budget_s=240.0)

        assert len(captured) == 2
        second_prompt = captured[1]["messages"][-1]["content"]
        assert "证据补检索" in second_prompt
        assert "尚无带来源链接的联网检索成功结果" in second_prompt

    def test_retrieval_round_can_earn_verified_label(self, monkeypatch):
        """补检索轮真实取证（工具命中 + 引文逐字）→ 授予 RESEARCH_VERIFIED。

        修复前的单轮模型：首轮没调工具就永远没有第二次机会（标签锁死
        UNVERIFIED）；补检索回路让「模型忘了取证」可被机械纠正。
        """
        captured = []
        _patch_llm(monkeypatch, [
            _turn_content(_online_json()),
            _turn_tool_call("web_search", {"query": "示例大学 马克思主义理论 招生简章"}),
            _turn_content(_online_json(
                sources=[{"url": _HIT_URL, "quote": _GROUNDED_QUOTE}])),
        ], captured=captured)
        engine = ar.AgenticResearchEngine()
        engine.dispatcher.dispatch = _hit_dispatch

        prof = engine.research_university_profile(
            _SCHOOL, _MAJOR, api_config=dict(_API_CFG), budget_s=240.0)

        assert len(captured) == 3
        assert prof.get("catalog_source") == _VERIFIED

    def test_budget_insufficient_skips_retrieval_round(self, monkeypatch):
        """剩余预算不足（≤ _EVIDENCE_RETRY_MIN_BUDGET）→ 不补检索（轻量边界）。

        补检索至少要一次 LLM 请求 + 一次工具调用的耗时，预算不足只会超时白跑。
        判据：budget_s 取 30（明显低于阈值 60）→ 剩余预算约 30 秒，恒小于阈值
        → 不触发。**不可取 60（与阈值同值）**：此时剩余 = 60 − elapsed，只有
        elapsed > 0 才不触发；CI runner 上单调时钟若出现回退（elapsed ≤ 0），
        判据会翻转、补检索被误触发 → 多发一次请求 → 本断言变红（2026-10-08
        实测 windows-latest 3.12，同 job rerun 通过；以「时钟回退」打桩可稳定复现）。
        """
        captured = []
        _patch_llm(monkeypatch, [_turn_content(_online_json())], captured=captured)
        prof = ar.AgenticResearchEngine().research_university_profile(
            _SCHOOL, _MAJOR, api_config=dict(_API_CFG), budget_s=30.0)

        assert len(captured) == 1
        assert prof.get("catalog_source") == _UNVERIFIED

    def test_budget_gate_immune_to_monotonic_regression(self, monkeypatch):
        """[阴性对照] 单调时钟回退时预算判据不得翻转（补检索不得被误触发）。

        背景：budget_s 若取成与阈值同值（60），剩余 = 60 − elapsed 恰落在
        `> _EVIDENCE_RETRY_MIN_BUDGET` 的边界上，只有 elapsed > 0 才不触发；
        CI runner 上 monotonic 出现回退（elapsed ≤ 0）时判据翻转 → 补检索误
        触发 → 多发一次请求 → 上一条用例变红（2026-10-08 windows-3.12 实测）。
        本用例以「回退时钟」打桩钉住该不变量：budget_s=30（明显低于阈值）时，
        即便时钟回退也不触发补检索。

        阴性对照：把 budget_s 改回 60.0 → 本用例 captured 变 2（判据翻转）。
        """
        class _RegressingClock:
            """每次调用返回更小的值（模拟 monotonic 回退）。"""

            def __init__(self):
                self.t = 10000.0

            def __call__(self):
                self.t -= 0.01
                return self.t

        _clock = _RegressingClock()
        monkeypatch.setattr(time, "monotonic", _clock)

        captured = []
        _patch_llm(monkeypatch, [_turn_content(_online_json())], captured=captured)
        prof = ar.AgenticResearchEngine().research_university_profile(
            _SCHOOL, _MAJOR, api_config=dict(_API_CFG), budget_s=30.0)

        assert len(captured) == 1
        assert prof.get("catalog_source") == _UNVERIFIED

    def test_retrieval_failure_keeps_first_round_result(self, monkeypatch):
        """补检索轮失败（LLM 确定性错误）→ 静默保留首轮结论，绝不丢结果。

        阴性对照：补检索异常未捕获 → 整体走动态降级（code 变 UNLISTED_*）→ 红。
        """
        captured = []
        # 队列只有首轮响应：补检索轮的第 1 次请求会命中 401（fake_urlopen 兜底）
        _patch_llm(monkeypatch, [_turn_content(_online_json())], captured=captured)

        prof = ar.AgenticResearchEngine().research_university_profile(
            _SCHOOL, _MAJOR, api_config=dict(_API_CFG), budget_s=240.0)

        assert len(captured) == 2           # 首轮 + 一次失败的补检索尝试
        assert prof.get("code") == "99999"  # 首轮结论完整保留
        assert prof.get("catalog_source") == _UNVERIFIED
