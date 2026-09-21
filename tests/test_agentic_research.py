# -*- coding: utf-8 -*-
"""
Milestone M4: LLM Tool-Calling Admissions Deep Research Engine Automated Test Suite

覆盖范围:
  1. 6 大 OpenAI-compatible Tool JSON Schema 完整性与规范性
  2. ToolDispatcher 工具安全调度执行
  3. 未收录高校（北京大学、湖南农业大学）真实画像研究与 0 离线伪数据兜底断言
  4. 双校对标 (SchoolComparator) 真实考情对标与差异化分析
  5. Mock LLM 多轮自主 Tool-Calling 循环与真实工具调用写回
  6. 缺失 API Key 优雅降级与高对比度引导卡片
  7. Watcher 未收录高校字典键冲突修复验证
  8. CHSI 研招连接器全国学科门类目录扩展与一级学科目录验证
  9. GUI 研招情报页 (IntelTab) API 引导横幅与动态状态刷新
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.intelligence.agentic_research import (
    RESEARCH_TOOLS_SCHEMA,
    ToolDispatcher,
    AgenticResearchEngine,
    get_guidance_notice,
    get_research_engine,
    research_university_profile
)
from tools.intelligence.registry import get_registry
from tools.intelligence.comparator import SchoolComparator
from tools.intelligence.watcher import AdmissionWatcher
from tools.intelligence.chsi_connector import CHSIConnector, STANDARD_SUBJECTS_CATALOG
from tools.search.providers import SearchProvider


class TestResearchToolsSchemaAndDispatcher:
    """1. 测试 6 大工具 JSON Schema 与 ToolDispatcher 调度"""

    def test_schema_integrity(self):
        assert len(RESEARCH_TOOLS_SCHEMA) == 6
        expected_tools = {
            "yanzhao_lookup",
            "web_search",
            "wechat_search",
            "scout_school",
            "compare_schools",
            "watch_admissions"
        }
        found_names = set()
        for item in RESEARCH_TOOLS_SCHEMA:
            assert item.get("type") == "function"
            func = item.get("function", {})
            name = func.get("name")
            assert name in expected_tools
            found_names.add(name)
            assert len(func.get("description", "")) > 10
            params = func.get("parameters", {})
            assert params.get("type") == "object"
            assert "properties" in params
            assert "required" in params

        assert found_names == expected_tools

    def test_dispatcher_all_tools_execution(self, tmp_path):
        dispatcher = ToolDispatcher(workspace_root=tmp_path)

        # 1. yanzhao_lookup
        res_yz = dispatcher.dispatch("yanzhao_lookup", school_name="北京大学", major_keyword="马克思主义理论")
        assert res_yz.get("chsi_code") == "10001"
        assert "北京" in res_yz.get("region", "")

        # 2. web_search
        # 契约（2026-09 修正）：`SearchService.search` 返回 `SearchResponse`，
        # 条目是 `SearchResult`；其 provenance 字段叫 `engine`（**没有** `provider`）。
        # 旧版这里 mock 成裸 list + `.provider`，恰好把被测代码的形参名/字段名错误
        # 一起「mock 掉」了，于是 P3 缺陷长期逃过 CI。
        with patch("tools.search.service.SearchService.search") as mock_search:
            from tools.search.models import SearchResponse, SearchResult
            mock_search.return_value = SearchResponse(
                query="北京大学 考研简章",
                results=(SearchResult(
                    title="测试招生简章",
                    url="https://example.com/zsml",
                    snippet="2026年拟招统考硕士生若干名",
                    engine="bing",
                ),),
            )

            res_web = dispatcher.dispatch("web_search", query="北京大学 考研简章", limit=2)
            assert isinstance(res_web, list)
            assert len(res_web) == 1
            assert res_web[0]["title"] == "测试招生简章"
            assert res_web[0]["provider"] == "bing"
            assert "error" not in res_web[0]

        # 3. wechat_search
        with patch("tools.skills.wechat_searcher.WeChatSearchEngine.search") as mock_wx:
            mock_wx.return_value = [{
                "title": "考研上岸经验分享",
                "source_account": "学长学姐说考研",
                "publish_date": "2026-03-20",
                "url": "https://mp.weixin.qq.com/test"
            }]
            res_wx = dispatcher.dispatch("wechat_search", query="北京大学 马理论 经验", limit=2)
            assert isinstance(res_wx, list)
            assert len(res_wx) == 1
            assert res_wx[0]["account"] == "学长学姐说考研"

        # 4. scout_school
        with patch("tools.intelligence.scout_engine.ScoutEngine.query") as mock_scout:
            mock_scout.return_value = {
                "school": "北京大学",
                "site_graph": {"chsi_code": "10466", "level": "省属重点", "domains": {}},
                "evidences": []
            }
            res_scout = dispatcher.dispatch("scout_school", school_name="北京大学")
            assert res_scout.get("school") == "北京大学"
            assert res_scout.get("chsi_code") == "10466"

        # 5. compare_schools
        with patch("tools.intelligence.comparator.SchoolComparator.compare") as mock_comp:
            mock_comp.return_value = {
                "name1": "北京大学",
                "name2": "湖南农业大学",
                "differences": {"subject_diff": "科目相似"}
            }
            res_comp = dispatcher.dispatch("compare_schools", school1="北京大学", school2="湖南农业大学", major_keyword="马克思主义理论")
            assert res_comp.get("name1") == "北京大学"
            assert "differences" in res_comp

        # 6. watch_admissions
        with patch("tools.intelligence.watcher.AdmissionWatcher.check_updates") as mock_watch:
            mock_watch.return_value = [{"school": "北京大学", "status": "UP_TO_DATE"}]
            res_watch = dispatcher.dispatch("watch_admissions", school_name="北京大学", action="check")
            assert "updates" in res_watch

        # 7. unknown tool error handling
        res_err = dispatcher.dispatch("non_existent_tool")
        assert "error" in res_err


class TestUnlistedUniversitiesProfiling:
    """2. 测试未收录高校（北京大学、湖南农业大学）真实画像与 0 伪数据兜底"""

    def test_peking_university_profile(self):
        engine = AgenticResearchEngine()
        # 强制走 dynamic_fallback_profile 验证底层事实库
        prof = engine.dynamic_fallback_profile("北京大学", "马克思主义理论")

        assert prof.get("code") == "10001"
        assert "北京" in prof.get("region", "")
        assert "985" in prof.get("level", "") or "211" in prof.get("level", "")
        assert "pku.edu.cn" in prof.get("official", "")
        assert "admission.pku.edu.cn" in prof.get("graduate", "") or "pku.edu.cn" in prof.get("graduate", "")

        majors = prof.get("majors", [])
        assert isinstance(majors, list)
        assert len(majors) >= 2
        # 必须包含统考代码 101 / 201 与院校自命题业务课（走「非河南/湖南」兜底分支）
        majors_str = " ".join(majors)
        assert "101" in majors_str or "思想政治理论" in majors_str
        assert "201" in majors_str or "英语" in majors_str
        assert "自命题" in majors_str or "马克思主义基本原理" in majors_str

        # 严格杜绝全篇伪数据占位符
        assert "OFFLINE_BASELINE" not in prof.get("catalog_source", "")
        assert "待查" not in prof.get("code", "")
        assert "待查" not in prof.get("region", "")

        # [诚信修复 2026-09-19] 未配置 API 时画像只来自本地高校库，
        # 必须如实标注来源，且报录比/一志愿保护/口碑/该校院线一律标未核验；
        # 旧断言要求这里等于「[RESEARCH_VERIFIED 深度研招检索]」并把"未核验"判为失败，
        # 等于把"编造且自称已核验"固化成了规格，现予纠正。
        assert prof.get("catalog_source") == "[LOCAL_DB_VERIFIED 本地高校库实录]"
        assert "RESEARCH_VERIFIED" not in prof.get("catalog_source", "")
        assert "未核验" in prof.get("ratio", "")
        assert "未核验" in prof.get("protect", "")
        assert "未核验" in prof.get("reputation", "")
        assert "未核验" in prof.get("score_trend", "")
        # 阴性测试：无来源的固定好评文案与凭空拼装的官网域名都不得再出现
        assert "4:1" not in prof.get("ratio", "")
        assert "严格保护一志愿" not in prof.get("protect", "")
        assert prof.get("official", "").startswith("http")

    def test_hunan_agricultural_university_profile(self):
        engine = AgenticResearchEngine()
        prof = engine.dynamic_fallback_profile("湖南农业大学", "马克思主义理论")

        assert prof.get("code") == "10537"
        assert "长沙" in prof.get("region", "") or "湖南" in prof.get("region", "")
        assert "一流大学" in prof.get("level", "") or "重点大学" in prof.get("level", "")
        assert "hunau.edu.cn" in prof.get("official", "")

        majors = prof.get("majors", [])
        majors_str = " ".join(majors)
        assert "622" in majors_str or "马克思主义基本原理" in majors_str
        assert "826" in majors_str or "中国化马克思主义" in majors_str

        assert "OFFLINE_BASELINE" not in prof.get("catalog_source", "")
        assert "待查" not in prof.get("code", "")
        assert prof.get("catalog_source") == "[LOCAL_DB_VERIFIED 本地高校库实录]"
        assert "未核验" in prof.get("ratio", "") and "未核验" in prof.get("protect", "")


class TestDualSchoolComparator:
    """3. 测试双校横向对比 (北大 VS 湖南农大) 研报真实性与无 OFFLINE_BASELINE"""

    def test_comparator_peking_vs_hunan_agri(self):
        comp = SchoolComparator()
        # 显式使用兜底事实画像（无外部 API Key 依赖），确保单元测试纯粹、秒级执行
        res = comp.compare("北京大学", "湖南农业大学", "马克思主义理论", save_report=False, api_config={"api_key": ""})

        term_report = res.get("terminal_report", "")
        md_report = res.get("markdown_report", "")

        # 核心断言：两份研报中均绝对不可出现 [OFFLINE_BASELINE 离线通用基准]
        assert "[OFFLINE_BASELINE 离线通用基准]" not in term_report
        assert "[OFFLINE_BASELINE 离线通用基准]" not in md_report
        assert "OFFLINE_BASELINE" not in term_report
        assert "OFFLINE_BASELINE" not in md_report

        # 真实代码与地区断言
        assert "10001" in term_report
        assert "10537" in term_report
        assert "北京" in term_report
        assert "长沙" in term_report

        # 差异分析断言
        diffs = res.get("differences", {}) or res.get("analysis", {})
        assert "subject_diff" in diffs
        assert "当前证据不足" not in diffs["subject_diff"]
        assert ("自命题" in diffs["subject_diff"] or "北京大学" in diffs["subject_diff"])
        assert ("622" in diffs["subject_diff"] or "湖南农业大学" in diffs["subject_diff"])

    def test_comparator_peking_vs_hunan_with_mock_llm(self):
        comp = SchoolComparator()
        mock_prof = {
            "name": "北京大学",
            "code": "10001",
            "level": "985 / 211",
            "region": "北京",
            "official": "https://www.pku.edu.cn",
            "graduate": "https://admission.pku.edu.cn",
            "majors": ["(101)思想政治理论", "(201)英语(一)", "(618/自命题)马克思主义基本原理", "(823/自命题)中国化马克思主义理论与实践"],
            "catalog_source": "[RESEARCH_VERIFIED 深度研招检索]",
            "score_trend": "国家线",
            "ratio": "良好",
            "protect": "保护一志愿",
            "reputation": "综合院校前列",
            "pitfalls": "无"
        }
        with patch("tools.intelligence.agentic_research.research_university_profile", return_value=mock_prof):
            res = comp.compare("北京大学", "湖南农业大学", "马克思主义理论", save_report=False, api_config={"api_key": "sk-mock-valid-key"})
            assert res["name1"] == "北京大学"
            assert "10001" in res["terminal_report"]


class TestMockLLMFunctionCallingLoop:
    """4. 测试大模型 Tool-Calling 多轮自主推理闭环 (OpenAI-compatible)"""

    def test_execute_loop_multi_turn_tool_calling(self):
        engine = AgenticResearchEngine()

        # 构造两轮响应模拟：
        # 第 1 轮返回 tool_calls 调用 yanzhao_lookup
        # 第 2 轮返回最终大模型研报 JSON
        turn1_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_yz_01",
                        "type": "function",
                        "function": {
                            "name": "yanzhao_lookup",
                            "arguments": json.dumps({"school_name": "北京大学", "major_keyword": "马克思主义理论"})
                        }
                    }]
                }
            }]
        }

        turn2_resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": (
                        "```json\n"
                        "{\n"
                        '  "name": "北京大学",\n'
                        '  "code": "10001",\n'
                        '  "region": "北京",\n'
                        '  "level": "985 / 211 / 双一流A类",\n'
                        '  "official_web": "https://www.pku.edu.cn",\n'
                        '  "graduate_web": "https://admission.pku.edu.cn",\n'
                        '  "majors": ["(101)思想政治理论", "(201)英语(一)", "(618/自命题)马克思主义基本原理", "(823/自命题)中国化马克思主义理论与实践"],\n'
                        '  "score_trend": "执行国家一区初试分数线",\n'
                        '  "ratio": "报录比约 5:1",\n'
                        '  "protect": "🌟 严格保护一志愿",\n'
                        '  "reputation": "学术科研氛围良好",\n'
                        '  "pitfalls": "注意自命题论述深度",\n'
                        '  "catalog_source": "[RESEARCH_VERIFIED 深度研招检索]"\n'
                        "}\n"
                        "```"
                    )
                }
            }]
        }

        responses = [
            MagicMock(read=MagicMock(return_value=json.dumps(turn1_resp).encode("utf-8"))),
            MagicMock(read=MagicMock(return_value=json.dumps(turn2_resp).encode("utf-8")))
        ]

        # Context manager __enter__ 返回 responses 中的对应元素
        enter_mock = MagicMock(side_effect=responses)
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__ = enter_mock
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        mock_api_config = {
            "api_key": "sk-mock-valid-key",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat"
        }

        with patch("urllib.request.urlopen", mock_urlopen):
            res_profile = engine.research_university_profile(
                "北京大学",
                "马克思主义理论",
                api_config=mock_api_config
            )

        assert res_profile.get("name") == "北京大学"
        assert res_profile.get("code") == "10001"
        assert "北京" in res_profile.get("region")
        assert any("自命题" in m for m in res_profile.get("majors", []))
        assert res_profile.get("catalog_source") == "[RESEARCH_VERIFIED 深度研招检索]"


class TestMissingAPIKeyGracefulDegradation:
    """5. 测试未配置 API Key 时的优雅降级与引导卡片"""

    def test_missing_api_key_status(self):
        engine = AgenticResearchEngine()
        # 传入未配置或占位符配置
        empty_cfg = {"api_key": "", "base_url": "", "model": ""}
        placeholder_cfg = {"api_key": "sk-xxxx123456", "base_url": "", "model": ""}

        res1 = engine.research_university_profile("北京大学", "马克思主义理论", api_config=empty_cfg)
        assert res1.get("code") == "10001"
        assert "OFFLINE_BASELINE" not in res1.get("catalog_source")

        res2 = engine.research_university_profile("湖南农业大学", "马克思主义理论", api_config=placeholder_cfg)
        assert res2.get("code") == "10537"
        assert "OFFLINE_BASELINE" not in res2.get("catalog_source")

    def test_guidance_notice_card(self):
        notice = get_guidance_notice()
        assert "大模型 Agentic 深度研究引擎就绪" in notice
        assert "多源联网直接检索与教育部权威院校库" in notice
        assert "【设置】->【大模型 API】" in notice


class TestWatcherUnlistedSchoolsNoCollision:
    """6. 测试 Watcher 监控未收录高校时独立建键，杜绝 '待查' 覆盖冲突"""

    def test_watcher_unlisted_collision_prevention(self, tmp_path):
        from tools.intelligence import watcher as watcher_mod

        # 使用隔离的临时监控文件
        test_watch_file = tmp_path / "test_watch.json"
        with patch.object(watcher_mod, "WATCH_FILE", test_watch_file):
            mock_fetcher = MagicMock()
            mock_fetch_res = MagicMock(is_valid=True, content="<html><title>公告列表</title><body>2026年硕士研究生招生简章发布</body></html>")
            mock_fetcher.fetch.return_value = mock_fetch_res

            w = AdmissionWatcher(fetcher=mock_fetcher)

            # 模拟添加两所不在 30 所内置静态库的高校
            res_a = w.add_watch("某某地方测试理工学院")
            res_b = w.add_watch("某某地方测试师范学院")

            assert res_a.get("success") is True
            assert res_b.get("success") is True

            # 严禁两所高校在 watch_data 中使用同一个 "待查" key
            assert "待查" not in w.watch_data
            assert "UNLISTED_某某地方测试理工学院" in w.watch_data
            assert "UNLISTED_某某地方测试师范学院" in w.watch_data
            assert len(w.watch_data) == 2

            # 移除其中一所，另一所完好无损
            w.remove_watch("某某地方测试理工学院")
            assert "UNLISTED_某某地方测试理工学院" not in w.watch_data
            assert "UNLISTED_某某地方测试师范学院" in w.watch_data
            assert len(w.watch_data) == 1


class TestCHSIConnectorDisciplineCatalog:
    """7. 测试研招网连接器学科门类目录扩展与非伪数据声明"""

    def test_chsi_marxism_discipline_expansion(self):
        assert "030500" in STANDARD_SUBJECTS_CATALOG
        m_info = STANDARD_SUBJECTS_CATALOG["030500"]
        assert m_info["name"] == "马克思主义理论"
        assert m_info["degree_type"] == "学硕"
        common = " ".join(m_info["common_subjects"])
        assert "618" in common
        assert "823" in common
        assert "101" in common
        assert "201" in common

    def test_chsi_query_catalog_fallback_no_offline_dummy(self):
        connector = CHSIConnector()
        # 传入未联网或未知院校，触发离线学科指导目录
        evs = connector.query_catalog_offline_baseline("未知测试高校", "马克思主义理论", target_year=2027)
        assert len(evs) >= 1
        ev = evs[0]
        # 验证其来源声明已升级为全国学科指导标准，不再包含旧版 offline_baseline
        assert "DISCIPLINE_CATALOG" in ev.source.name or "学科门类" in ev.source.name
        assert "【离线基准·未联网核验】" not in ev.source.name


class TestGUIIntelTabBanner:
    """8. 测试 GUI 研招情报页 (IntelTab) API 引导横幅逻辑"""

    def test_intel_tab_banner_without_qt(self):
        # 使用 mock 对象测试 update_api_status_banner 的逻辑正确性
        from tools.gui.views.intel_tab import update_api_status_banner

        mock_win = MagicMock()
        mock_win.intel_api_banner = MagicMock()
        mock_win.intel_api_status_label = MagicMock()
        mock_win.intel_btn_setup_api = MagicMock()

        # 模拟未配置 API Key
        with patch("tools.intelligence.agentic_research.AgenticResearchEngine.is_api_configured", return_value=False):
            update_api_status_banner(mock_win)
            mock_win.intel_api_banner.setStyleSheet.assert_called_once()
            call_arg = mock_win.intel_api_banner.setStyleSheet.call_args[0][0]
            assert "rgba(245, 158, 11" in call_arg # 琥珀色/警示提示风格
            lbl_arg = mock_win.intel_api_status_label.setText.call_args[0][0]
            assert "当前未配置大模型 API 密钥" in lbl_arg

        mock_win.reset_mock()

        # 模拟已配置 API Key
        with patch("tools.intelligence.agentic_research.AgenticResearchEngine.is_api_configured", return_value=True):
            update_api_status_banner(mock_win)
            mock_win.intel_api_banner.setStyleSheet.assert_called_once()
            call_arg = mock_win.intel_api_banner.setStyleSheet.call_args[0][0]
            assert "rgba(16, 185, 129" in call_arg # 绿色激活风格
            lbl_arg = mock_win.intel_api_status_label.setText.call_args[0][0]
            assert "已激活" in lbl_arg


class TestYanzhaoLookupUnverifiedPlaceholder:
    """P4 回归：``yanzhao_lookup`` 的未命中结果必须显式标注「未核验」。

    与「检索失败却伪造文章」（web_search / wechat_search 已修）是**不同成因、
    同一危害**：这里是**本地高校库未命中**（本地库仅收录数十所院校，未命中是
    常态，故不能返回 error 结构把「没收录」和「工具坏了」混为一谈）。但旧实现的
    占位字段与命中时形状完全一致 —— ``chsi_code="待查" / region="全国" /
    level=["全国研招单位"]`` —— 模型会把 ``region="全国"``、
    ``level=["全国研招单位"]`` 当成该校的真实属性写进研报。
    """

    def test_unlisted_school_returns_explicit_placeholder(self, tmp_path):
        """未命中：清空一切可能被引用的值 + 结构化标注未核验。

        阴性对照：把 ``tool_yanzhao_lookup`` 的未命中分支改回
        ``{"chsi_code": "待查", "region": "全国", "level": ["全国研招单位"]}``，
        本用例变红。
        """
        dispatcher = ToolDispatcher(workspace_root=tmp_path)
        # 「院校」不含 registry 合成实体的触发词（大学/学院/学校/研究院/研究所/中心），
        # 故 resolve() 返回 None，走真正的未命中分支。
        res = dispatcher.dispatch("yanzhao_lookup", school_name="某某测试院校")

        assert res.get("found") is False
        assert res.get("unverified") is True
        assert res.get("source") == "placeholder"
        # 值本身不得再是「看起来像事实」的占位
        assert res.get("chsi_code") == ""
        assert res.get("region") == ""
        assert res.get("level") == []
        assert "未收录" in res.get("note", "")

        blob = json.dumps(res, ensure_ascii=False)
        assert "全国研招单位" not in blob, blob
        assert "待查" not in blob, blob

        # prompt 模板（工具描述）也必须把该标记的语义告知模型
        desc = next(t["function"]["description"] for t in RESEARCH_TOOLS_SCHEMA
                    if t["function"]["name"] == "yanzhao_lookup")
        assert "unverified" in desc

    def test_synthesized_unlisted_school_is_flagged_unverified(self, tmp_path):
        """registry 会为「名字像高校但未收录」的查询合成实体（chsi_code 回落
        「待查」）。这条路径**实际可达**（任何含"大学/学院"的陌生校名都走它），
        旧实现同样把结果当已核验事实返回，故必须一并标注。

        阴性对照：删掉命中分支里的 ``unverified`` / ``note`` 标注，本用例变红。
        """
        dispatcher = ToolDispatcher(workspace_root=tmp_path)
        res = dispatcher.dispatch("yanzhao_lookup", school_name="虚构测试大学")

        assert res.get("found") is True          # resolve 确实返回了（合成）实体
        assert res.get("unverified") is True, "合成实体的 chsi_code 是占位「待查」，必须标未核验"
        assert res.get("source") == "local_registry"
        assert "未核验" in res.get("note", "")

    def test_listed_school_is_marked_verified(self, tmp_path):
        """命中本地库时 found/unverified 必须**显式**为真/假。

        不能只靠「缺 unverified 字段」推断已核验：模型与下游都容易把缺失读成
        默认值。同时确认真实命中路径不受本次修复影响。
        """
        dispatcher = ToolDispatcher(workspace_root=tmp_path)
        res = dispatcher.dispatch("yanzhao_lookup", school_name="北京大学")

        assert res.get("found") is True
        assert res.get("unverified") is False
        assert res.get("source") == "local_registry"
        assert res.get("chsi_code") == "10001"
        assert "note" not in res, "已核验命中不应附带未核验提示"


class TestWebSearchDispatchRegression:
    """P3/P4 回归：Agent 的 web_search 工具必须真的能返回检索结果。

    修复前实测（stub provider，不联网）：
      ToolDispatcher().tool_web_search("测试", limit=3)
      -> [{'title': '测试 检索结果',
           'snippet': "SearchService.search() got an unexpected keyword argument 'max_results'",
           'url': ''}]
    即：形参名写错 + 未走 default()（去重/重排/缓存全 None）+ 读不存在的
    `r.provider` 字段，三处缺陷被外层 `except Exception` 统一包装成一条
    「看起来像检索结果」的假条目，模型会把它当成来自网络的证据。

    阴性对照：本测试在修复前必然红（既拿不到真实条目，也不会发生去重）。
    """

    QUERY = "北京大学 马克思主义理论 复试线"

    class _StubProvider(SearchProvider):
        """零网络 stub：返回两条仅跳转参数不同的同源 URL + 一条独立 URL。"""

        name = "gen4-stub"
        priority = 1
        engine_type = "general"

        def __init__(self, items):
            self._items = list(items)

        def search(self, query, *, limit=10, time_range=None):
            return list(self._items)

    @staticmethod
    def _stub_items():
        from tools.search.models import SearchResult
        return [
            SearchResult(
                title="北京大学 马克思主义理论 招生简章",
                url="https://admission.pku.edu.cn/zsml.html?from=list",
                snippet="101 思想政治理论 201 英语（一）",
                engine="gen4-stub",
            ),
            SearchResult(
                title="北京大学 马克思主义理论 招生简章",
                url="https://admission.pku.edu.cn/zsml.html",
                snippet="101 思想政治理论 201 英语（一）",
                engine="gen4-stub",
            ),
            SearchResult(
                title="北京大学 马克思主义理论 复试线",
                url="https://admission.pku.edu.cn/fsx.html",
                snippet="复试分数线 马克思主义理论 国家线",
                engine="gen4-stub",
            ),
        ]

    def _patched_env(self, tmp_path):
        """让 default() 装配真实去重/重排，但把 provider 与缓存都换成本地桩。"""
        from functools import partial
        from tools.search import health
        from tools.search.cache import SearchCache as _RealSearchCache
        health.reset()
        provider = self._StubProvider(self._stub_items())
        return (
            patch("tools.search.service.available_providers", return_value=[provider]),
            patch("tools.search.cache.SearchCache",
                  partial(_RealSearchCache, path=tmp_path / "search_cache.json")),
        )

    def test_web_search_returns_real_results_and_dedups(self, tmp_path):
        dispatcher = ToolDispatcher(workspace_root=tmp_path)
        p_providers, p_cache = self._patched_env(tmp_path)

        with p_providers, p_cache:
            res = dispatcher.dispatch("web_search", query=self.QUERY, limit=5)

        # ① 返回的是真实检索条目（而非异常文案）
        assert isinstance(res, list) and res, res
        assert all("error" not in item for item in res), res
        assert all(item.get("url") for item in res), res
        assert all(item.get("title") for item in res), res

        # ② 绝不把 TypeError / AttributeError 文案塞进 snippet
        blob = json.dumps(res, ensure_ascii=False)
        assert "unexpected keyword argument" not in blob, blob
        assert "has no attribute" not in blob, blob
        assert "max_results" not in blob, blob

        # ③ provenance 取的是 engine 字段
        assert all(item["provider"] == "gen4-stub" for item in res), res

        # ④ 去重真的生效：两条仅 ?from=list 不同的同源 URL 只剩一条
        urls = [item["url"] for item in res]
        assert len(urls) == 2, urls
        assert sum(1 for u in urls if "zsml.html" in u) == 1, urls
        assert any("fsx.html" in u for u in urls), urls

    def test_web_search_reports_failure_instead_of_faking_a_result(self, tmp_path):
        """检索链路真炸掉时，必须如实报错，不得伪装成一条 url="" 的「结果」。"""
        dispatcher = ToolDispatcher(workspace_root=tmp_path)

        with patch("tools.search.service.SearchService.default",
                   side_effect=RuntimeError("模拟检索服务不可用")):
            res = dispatcher.dispatch("web_search", query=self.QUERY, limit=3)

        assert isinstance(res, list) and res
        # 失败必须以错误结构呈现，而不是 title/snippet/url 三件套的「结果」
        assert all("error" in item for item in res), res
        assert all("url" not in item for item in res), res

    def test_wechat_search_reports_failure_instead_of_faking_an_article(self, tmp_path):
        """[回归] 微信检索失败时必须如实报错，不得伪装成一条「学长学姐经验」。

        修复前 ``tool_wechat_search`` 的 except 分支返回
        ``{"title": f"{query} 经验分享", "account": "微信考研圈", "date": "近期",
        "url": ""}`` —— 一条凭空捏造的文章，模型会把它当成真实证据写进研报
        （与上方 ``tool_web_search`` 已修好的问题同类，但当时漏改）。

        阴性对照：把该分支改回 ``[{"title": f"{query} 经验分享", ...}]``，本用例变红。
        """
        dispatcher = ToolDispatcher(workspace_root=tmp_path)

        with patch("tools.skills.wechat_searcher.WeChatSearchEngine.search",
                   side_effect=RuntimeError("模拟微信检索不可用")):
            res = dispatcher.dispatch("wechat_search", query="某院校 某专业 经验", limit=2)

        assert isinstance(res, list) and res, res
        # 失败必须以错误结构呈现，而不是 title/account/date 三件套的「文章」
        assert all("error" in item for item in res), res
        assert all("title" not in item for item in res), res
        assert all("account" not in item for item in res), res
        blob = json.dumps(res, ensure_ascii=False)
        assert "经验分享" not in blob, blob
        assert "微信考研圈" not in blob, blob
        assert "模拟微信检索不可用" in blob, blob

    def test_direct_searchservice_construction_warns_dedup_disabled(self, caplog, tmp_path):
        """P4 治本：直连 SearchService() 时，去重被跳过必须留痕（不再静默）。"""
        import logging as _logging
        from functools import partial
        from tools.search.cache import SearchCache as _RealSearchCache
        from tools.search.service import SearchService

        svc = SearchService(providers=[self._StubProvider(self._stub_items())])
        with caplog.at_level(_logging.WARNING, logger="tools.search.service"):
            svc.search(self.QUERY, limit=10)
        assert "去重未启用" in caplog.text, caplog.text

        # 阴性对照：走 default() 装配后，不得再出现该告警
        caplog.clear()
        with patch("tools.search.cache.SearchCache",
                   partial(_RealSearchCache, path=tmp_path / "search_cache.json")):
            svc_ok = SearchService.default(providers=[self._StubProvider(self._stub_items())])
            assert svc_ok._dedup is not None
            with caplog.at_level(_logging.WARNING, logger="tools.search.service"):
                svc_ok.search(self.QUERY, limit=10)
        assert "去重未启用" not in caplog.text, caplog.text

