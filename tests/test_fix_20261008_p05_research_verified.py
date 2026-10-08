# -*- coding: utf-8 -*-
"""[P0-5 修复·2026-10-08] RESEARCH_VERIFIED 信任标签与证据链挂钩回归测试。

缺陷形态：``_research_university_profile_impl`` 旧实现对信任标签无条件授予 ——
只要 LLM 输出能解析出 code+majors 就硬编码
``parsed["catalog_source"] = "[RESEARCH_VERIFIED 深度研招检索]"``：
不检查任何工具调用是否成功、无来源 URL、无引文。``comparator`` 以该子串判定
「可追溯来源」（两校全需命中），于是模型一次工具都没调也能让双校对比走出
「证据不足」分支。

修复闸门（两条件同时成立才授予）：
  ① retrieval_ok：至少一次联网检索工具（web_search / wechat_search）成功
     且结果含 http(s) URL；
  ② grounded：LLM 的 sources[].quote 至少一条在成功工具结果文本池中逐字
     命中（复用 citation_engine.verify_citation_excerpt）。
否则降级 ``[UNVERIFIED 在线生成·未溯源]``。

阴性对照：把闸门退回「无条件授予」，本文件「无工具调用 / 编造引文 /
缺 sources / 无 URL」等用例变红；反向对照（旧模块零工具调用仍授予）已由
C:/tmp/ky_p05_probe.py 独立加载快照 .bak 实证。

全部用例零真实联网：LLM 出站打桩 safe_urlopen，工具 dispatcher 打桩返回
固定命中；身份一律中性化（示例大学 / example.edu.cn）。
"""

import json
import sys
import threading
from pathlib import Path

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
_GROUNDED_QUOTE = "马克思主义理论专业拟招12人"        # 逐字来自 _HIT_SNIPPET
_FABRICATED_QUOTE = "马克思主义理论专业拟招99人"      # 数字被改写，必不命中

_VERIFIED = "[RESEARCH_VERIFIED 深度研招检索]"
_UNVERIFIED = "[UNVERIFIED 在线生成·未溯源]"
_MISSING = object()


class _FakeResp:
    """最小响应替身：read_response_limited 兼容只实现 read() 的鸭子类型。"""

    headers = {"Content-Encoding": ""}

    def __init__(self, payload):
        self._payload = payload

    def read(self, *args):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _turn_tool_call(name, args):
    return {"choices": [{"message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": name,
                                     "arguments": json.dumps(args, ensure_ascii=False)}}]}}]}


def _turn_content(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _final_json(sources=_MISSING):
    """构造 LLM 最终 JSON。

    故意在 payload 里自我声明 RESEARCH_VERIFIED：闸门必须覆盖它 ——
    信任标签只能由代码依据证据记录授予，模型不得自授。
    """
    payload = {
        "name": _SCHOOL,
        "code": "99999",
        "region": "示例省",
        "level": "示例层次",
        "majors": ["(101)思想政治理论", "(201)英语(一)"],
        "catalog_source": _VERIFIED,
    }
    if sources is not _MISSING:
        payload["sources"] = sources
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _hit_dispatch(name, **kwargs):
    """打桩工具 dispatcher：检索工具返回含 URL 的真实形状命中。"""
    return [{"title": "示例大学2027年硕士研究生招生简章",
             "snippet": _HIT_SNIPPET, "url": _HIT_URL, "provider": "stub"}]


def _patch_llm(monkeypatch, responses):
    """打桩 LLM 出站：按顺序吐出 responses 中每个 dict 的 JSON 响应体。"""
    queue = [json.dumps(r, ensure_ascii=False).encode("utf-8") for r in responses]

    def fake_urlopen(req, timeout=None):
        return _FakeResp(queue.pop(0))

    monkeypatch.setattr(ar, "safe_urlopen", fake_urlopen)


def _run_impl(monkeypatch, responses, dispatch=None):
    """走真实 research_university_profile → execute_loop（仅打桩 LLM 与工具）。"""
    _patch_llm(monkeypatch, responses)
    engine = ar.AgenticResearchEngine()
    if dispatch is not None:
        engine.dispatcher.dispatch = dispatch
    return engine.research_university_profile(
        _SCHOOL, _MAJOR, api_config=dict(_API_CFG), budget_s=60.0)


class TestResearchVerifiedGate:
    """闸门行为：``retrieval_ok and grounded`` 才授予，否则如实降级。"""

    def test_no_tool_calls_degrades_to_unverified(self, monkeypatch):
        """① 零工具调用（LLM 直接输出结论）→ 不得授予信任标签。

        修复前：旧实现无条件授予（反向对照见探针场景 A）。结论本体仍返回，
        只是信任标签如实降级 —— 这是本修复的核心语义。
        """
        prof = _run_impl(monkeypatch, [_turn_content(_final_json(sources=[]))])
        assert prof.get("code") == "99999"
        assert prof.get("catalog_source") == _UNVERIFIED
        # LLM 自我声明的标签必须被覆盖（模型不得自授信任标签）
        assert prof.get("catalog_source") != _VERIFIED

    def test_retrieval_ok_and_grounded_quote_grants_verified(self, monkeypatch):
        """② 检索成功（含 http(s) URL）+ quote 逐字命中工具结果 → 授予。

        这是修复后唯一的授予路径；quote 逐字出自打桩工具返回的 snippet。
        """
        prof = _run_impl(
            monkeypatch,
            [_turn_tool_call("web_search", {"query": "示例大学 马克思主义理论 招生简章"}),
             _turn_content(_final_json(
                 sources=[{"url": _HIT_URL, "quote": _GROUNDED_QUOTE}]))],
            dispatch=_hit_dispatch)
        assert prof.get("catalog_source") == _VERIFIED

    def test_retrieval_ok_but_fabricated_quote_degrades(self, monkeypatch):
        """③ 检索成功但 quote 编造（数字改写、逐字不命中）→ UNVERIFIED。"""
        prof = _run_impl(
            monkeypatch,
            [_turn_tool_call("web_search", {"query": "示例大学 马克思主义理论 招生简章"}),
             _turn_content(_final_json(
                 sources=[{"url": _HIT_URL, "quote": _FABRICATED_QUOTE}]))],
            dispatch=_hit_dispatch)
        assert prof.get("catalog_source") == _UNVERIFIED

    def test_retrieval_ok_but_sources_missing_degrades(self, monkeypatch):
        """检索成功但 LLM 输出没有 sources 字段 → UNVERIFIED（无引文即未溯源）。"""
        prof = _run_impl(
            monkeypatch,
            [_turn_tool_call("web_search", {"query": "示例大学 招生简章"}),
             _turn_content(_final_json())],   # sources 字段整体缺省
            dispatch=_hit_dispatch)
        assert prof.get("catalog_source") == _UNVERIFIED

    def test_sources_non_list_degrades_safely(self, monkeypatch):
        """sources 非 list（模型返回字符串/对象等畸形值）→ 安全降级不崩溃。"""
        for bad_sources in ("https://example.edu.cn/zsml2027.html",
                            {"url": _HIT_URL, "quote": _GROUNDED_QUOTE},
                            [None, "裸字符串", {}, {"quote": ""}, {"quote": None}]):
            prof = _run_impl(
                monkeypatch,
                [_turn_tool_call("web_search", {"query": "示例大学 招生简章"}),
                 _turn_content(_final_json(sources=bad_sources))],
                dispatch=_hit_dispatch)
            assert prof.get("catalog_source") == _UNVERIFIED, bad_sources

    def test_wechat_search_also_counts_as_retrieval(self, monkeypatch):
        """白名单覆盖 wechat_search：微信检索命中 + 引文 → 可授予。"""
        def wechat_dispatch(name, **kwargs):
            return [{"title": "示例大学马理论就读体验：一志愿保护良好",
                     "account": "示例学长", "date": "2026-06-01",
                     "url": "https://mp.weixin.example.cn/a/1"}]

        prof = _run_impl(
            monkeypatch,
            [_turn_tool_call("wechat_search", {"query": "示例大学 马理论 经验"}),
             _turn_content(_final_json(
                 sources=[{"quote": "一志愿保护良好"}]))],
            dispatch=wechat_dispatch)
        assert prof.get("catalog_source") == _VERIFIED

    def test_failed_retrieval_result_does_not_count(self, monkeypatch):
        """检索工具返回 error 结构（工具坏 / 全源冷却）→ 不计检索成功。"""
        def error_dispatch(name, **kwargs):
            return [{"error": "检索失败: 模拟检索不可用", "all_failed_cooling": True}]

        prof = _run_impl(
            monkeypatch,
            [_turn_tool_call("web_search", {"query": "示例大学 招生简章"}),
             _turn_content(_final_json(
                 sources=[{"quote": _GROUNDED_QUOTE}]))],
            dispatch=error_dispatch)
        assert prof.get("catalog_source") == _UNVERIFIED

    def test_result_without_url_does_not_count(self, monkeypatch):
        """结果无 http(s) URL → 不算检索成功（url="" 的空壳命中不是证据）。

        本用例中 quote 确实逐字在工具结果里（grounded 成立），单独把
        retrieval_ok 的 URL 要求钉死：去掉它本用例会变绿为误授。
        """
        def no_url_dispatch(name, **kwargs):
            return [{"title": "示例大学招生简章", "snippet": _HIT_SNIPPET,
                     "url": "", "provider": "stub"}]

        prof = _run_impl(
            monkeypatch,
            [_turn_tool_call("web_search", {"query": "示例大学 招生简章"}),
             _turn_content(_final_json(
                 sources=[{"quote": _GROUNDED_QUOTE}]))],
            dispatch=no_url_dispatch)
        assert prof.get("catalog_source") == _UNVERIFIED


class TestToolRecordMechanism:
    """记录机制本身：thread-local 隔离 + 非研究链路不记录。"""

    def test_records_container_is_thread_local(self):
        """记录容器必须线程隔离（comparator 两线程并行研究两校，不能串扰）。

        修复设计明文要求 thread-local（挂 self 普通属性会竞态）；本用例直接
        钉住容器语义：子线程读不到主线程的记录、写不进主线程的容器。
        """
        ar._TOOL_RECORDS.records = ["主线程标记"]
        seen = {}

        def worker():
            seen["before"] = getattr(ar._TOOL_RECORDS, "records", None)
            ar._TOOL_RECORDS.records = ["子线程标记"]
            seen["after"] = ar._TOOL_RECORDS.records

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert seen["before"] is None            # 子线程看不到主线程的记录
        assert seen["after"] == ["子线程标记"]
        assert ar._TOOL_RECORDS.records == ["主线程标记"]  # 子线程写入不污染主线程
        ar._TOOL_RECORDS.records = None          # 收尾：不把标记留给后续用例

    def test_direct_execute_loop_without_container_does_not_record(self, monkeypatch):
        """非研究链路直调 execute_loop：无容器时跳过记录，不报错、不创建容器。

        守卫实现为 ``getattr(_TOOL_RECORDS, 'records', None) is None → skip``；
        本用例先显式清空容器再直调，验证记录动作不会「顺手」建容器。
        """
        ar._TOOL_RECORDS.records = None
        _patch_llm(monkeypatch, [
            _turn_tool_call("web_search", {"query": "示例大学 招生简章"}),
            _turn_content("完成"),
        ])
        engine = ar.AgenticResearchEngine()
        engine.dispatcher.dispatch = _hit_dispatch

        res = engine.execute_loop("研究指令", api_config=dict(_API_CFG))

        assert res == "完成"   # 循环正常走完（记录动作不打断主流程）
        assert getattr(ar._TOOL_RECORDS, "records", None) is None


class TestDownstreamComparatorBranch:
    """标签语义与 comparator 证据分支联动（「无需改 comparator」的实证）。"""

    @staticmethod
    def _profile(label):
        return {"name": "示例大学", "code": "99999", "level": "示例层次",
                "region": "示例省", "official": "", "graduate": "",
                "majors": ["(101)思想政治理论", "(201)英语(一)"],
                "catalog_source": label, "score_trend": "未核验", "ratio": "未核验",
                "protect": "未核验", "reputation": "", "pitfalls": ""}

    def test_unverified_label_lands_in_insufficient_evidence_branch(self):
        """新降级标签不含任何 *_VERIFIED 子串 → comparator 落入「证据不足」。"""
        from tools.intelligence.comparator import SchoolComparator

        comp = SchoolComparator()
        d = comp._analyze_differences(
            "示例大学甲", self._profile(_UNVERIFIED),
            "示例大学乙", self._profile(_UNVERIFIED), "示例专业")
        assert "当前证据不足" in d["subject_diff"]

    def test_verified_label_lists_subjects(self):
        """对照：闸门授予的标签仍走「逐条列科目」分支（没有误伤授予路径）。"""
        from tools.intelligence.comparator import SchoolComparator

        comp = SchoolComparator()
        d = comp._analyze_differences(
            "示例大学甲", self._profile(_VERIFIED),
            "示例大学乙", self._profile(_VERIFIED), "示例专业")
        assert "当前证据不足" not in d["subject_diff"]
        assert "(101)思想政治理论" in d["subject_diff"]
