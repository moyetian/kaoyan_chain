# -*- coding: utf-8 -*-
"""
Milestone M4: LLM Tool-Calling Admissions Deep Research Engine Automated Test Suite

覆盖范围:
  1. 6 大 OpenAI-compatible Tool JSON Schema 完整性与规范性
  2. ToolDispatcher 工具安全调度执行
  3. 未收录高校（目标院校、对比院校B）真实画像研究与 0 离线伪数据兜底断言
  4. 双校对标 (SchoolComparator) 真实考情对标与差异化分析
  5. Mock LLM 多轮自主 Tool-Calling 循环与真实工具调用写回
  6. 缺失 API Key 优雅降级与高对比度引导卡片
  7. Watcher 未收录高校字典键冲突修复验证
  8. CHSI 研招连接器全国学科门类目录扩展与 目标专业 (专业代码)验证
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
        res_yz = dispatcher.dispatch("yanzhao_lookup", school_name="目标院校", major_keyword="马克思主义理论")
        assert res_yz.get("chsi_code") in ["10466", "10464"]
        assert "郑州" in res_yz.get("region", "") or "河南" in res_yz.get("region", "")

        # 2. web_search
        with patch("tools.search.service.SearchService.search") as mock_search:
            mock_item = MagicMock()
            mock_item.title = "测试招生简章"
            mock_item.snippet = "2026年拟招统考硕士生若干名"
            mock_item.url = "https://example.com/zsml"
            mock_item.provider = "bing"
            mock_search.return_value = [mock_item]

            res_web = dispatcher.dispatch("web_search", query="目标院校 考研简章", limit=2)
            assert isinstance(res_web, list)
            assert len(res_web) == 1
            assert res_web[0]["title"] == "测试招生简章"

        # 3. wechat_search
        with patch("tools.skills.wechat_searcher.WeChatSearchEngine.search") as mock_wx:
            mock_wx.return_value = [{
                "title": "考研上岸经验分享",
                "source_account": "学长学姐说考研",
                "publish_date": "2026-03-20",
                "url": "https://mp.weixin.qq.com/test"
            }]
            res_wx = dispatcher.dispatch("wechat_search", query="目标院校 马理论 经验", limit=2)
            assert isinstance(res_wx, list)
            assert len(res_wx) == 1
            assert res_wx[0]["account"] == "学长学姐说考研"

        # 4. scout_school
        with patch("tools.intelligence.scout_engine.ScoutEngine.query") as mock_scout:
            mock_scout.return_value = {
                "school": "目标院校",
                "site_graph": {"chsi_code": "10466", "level": "省属重点", "domains": {}},
                "evidences": []
            }
            res_scout = dispatcher.dispatch("scout_school", school_name="目标院校")
            assert res_scout.get("school") == "目标院校"
            assert res_scout.get("chsi_code") == "10466"

        # 5. compare_schools
        with patch("tools.intelligence.comparator.SchoolComparator.compare") as mock_comp:
            mock_comp.return_value = {
                "name1": "目标院校",
                "name2": "对比院校B",
                "differences": {"subject_diff": "科目相似"}
            }
            res_comp = dispatcher.dispatch("compare_schools", school1="目标院校", school2="对比院校B", major_keyword="马克思主义理论")
            assert res_comp.get("name1") == "目标院校"
            assert "differences" in res_comp

        # 6. watch_admissions
        with patch("tools.intelligence.watcher.AdmissionWatcher.check_updates") as mock_watch:
            mock_watch.return_value = [{"school": "目标院校", "status": "UP_TO_DATE"}]
            res_watch = dispatcher.dispatch("watch_admissions", school_name="目标院校", action="check")
            assert "updates" in res_watch

        # 7. unknown tool error handling
        res_err = dispatcher.dispatch("non_existent_tool")
        assert "error" in res_err


class TestUnlistedUniversitiesProfiling:
    """2. 测试未收录高校（目标院校、对比院校B）真实画像与 0 伪数据兜底"""

    def test_henan_agricultural_university_profile(self):
        engine = AgenticResearchEngine()
        # 强制走 dynamic_fallback_profile 验证底层事实库
        prof = engine.dynamic_fallback_profile("目标院校", "马克思主义理论")

        assert prof.get("code") in ["10466", "10464"]
        assert "郑州" in prof.get("region", "") or "河南" in prof.get("region", "")
        assert "省部共建" in prof.get("level", "") or "特色骨干" in prof.get("level", "")
        assert "henau.edu.cn" in prof.get("official", "")
        assert "gra.henau.edu.cn" in prof.get("graduate", "") or "henau.edu.cn" in prof.get("graduate", "")

        majors = prof.get("majors", [])
        assert isinstance(majors, list)
        assert len(majors) >= 2
        # 必须包含真实专业课与统考代码 (自命题科目1 / 自命题科目2 / 101 / 201)
        majors_str = " ".join(majors)
        assert "618" in majors_str or "马克思主义基本原理" in majors_str
        assert "823" in majors_str or "中国化马克思主义" in majors_str
        assert "101" in majors_str or "思想政治理论" in majors_str

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
        prof = engine.dynamic_fallback_profile("对比院校B", "马克思主义理论")

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
    """3. 测试双校横向对比 (河南农大 VS 湖南农大) 研报真实性与无 OFFLINE_BASELINE"""

    def test_comparator_henan_vs_hunan_agri(self):
        comp = SchoolComparator()
        # 显式使用兜底事实画像（无外部 API Key 依赖），确保单元测试纯粹、秒级执行
        res = comp.compare("目标院校", "对比院校B", "马克思主义理论", save_report=False, api_config={"api_key": ""})

        term_report = res.get("terminal_report", "")
        md_report = res.get("markdown_report", "")

        # 核心断言：两份研报中均绝对不可出现 [OFFLINE_BASELINE 离线通用基准]
        assert "[OFFLINE_BASELINE 离线通用基准]" not in term_report
        assert "[OFFLINE_BASELINE 离线通用基准]" not in md_report
        assert "OFFLINE_BASELINE" not in term_report
        assert "OFFLINE_BASELINE" not in md_report

        # 真实代码与地区断言
        assert ("10466" in term_report or "10464" in term_report)
        assert "10537" in term_report
        assert "郑州" in term_report
        assert "长沙" in term_report

        # 差异分析断言
        diffs = res.get("differences", {}) or res.get("analysis", {})
        assert "subject_diff" in diffs
        assert "当前证据不足" not in diffs["subject_diff"]
        assert ("618" in diffs["subject_diff"] or "目标院校" in diffs["subject_diff"])
        assert ("622" in diffs["subject_diff"] or "对比院校B" in diffs["subject_diff"])

    def test_comparator_henan_vs_hunan_with_mock_llm(self):
        comp = SchoolComparator()
        mock_prof = {
            "name": "目标院校",
            "code": "10466",
            "level": "省部共建",
            "region": "河南郑州",
            "official": "https://www.henau.edu.cn",
            "graduate": "https://gra.henau.edu.cn",
            "majors": ["(101)思想政治理论", "(201)英语(一)", "(618)马克思主义基本原理", "(823)中国化马克思主义理论与实践"],
            "catalog_source": "[RESEARCH_VERIFIED 深度研招检索]",
            "score_trend": "国家线",
            "ratio": "良好",
            "protect": "保护一志愿",
            "reputation": "农业院校前列",
            "pitfalls": "无"
        }
        with patch("tools.intelligence.agentic_research.research_university_profile", return_value=mock_prof):
            res = comp.compare("目标院校", "对比院校B", "马克思主义理论", save_report=False, api_config={"api_key": "sk-mock-valid-key"})
            assert res["name1"] == "目标院校"
            assert "10466" in res["terminal_report"]


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
                            "arguments": json.dumps({"school_name": "目标院校", "major_keyword": "马克思主义理论"})
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
                        '  "name": "目标院校",\n'
                        '  "code": "10466",\n'
                        '  "region": "河南郑州",\n'
                        '  "level": "省部共建高校 / 河南省特色骨干大学",\n'
                        '  "official_web": "https://www.henau.edu.cn",\n'
                        '  "graduate_web": "https://gra.henau.edu.cn",\n'
                        '  "majors": ["(101)思想政治理论", "(201)英语(一)", "(618)马克思主义基本原理", "(823)中国化马克思主义理论与实践"],\n'
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
            "api_key": "YOUR_API_KEY_HERE",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat"
        }

        with patch("urllib.request.urlopen", mock_urlopen):
            res_profile = engine.research_university_profile(
                "目标院校",
                "马克思主义理论",
                api_config=mock_api_config
            )

        assert res_profile.get("name") == "目标院校"
        assert res_profile.get("code") in ["10466", "10464"]
        assert "郑州" in res_profile.get("region")
        assert any("618" in m for m in res_profile.get("majors", []))
        assert res_profile.get("catalog_source") == "[RESEARCH_VERIFIED 深度研招检索]"


class TestMissingAPIKeyGracefulDegradation:
    """5. 测试未配置 API Key 时的优雅降级与引导卡片"""

    def test_missing_api_key_status(self):
        engine = AgenticResearchEngine()
        # 传入未配置或占位符配置
        empty_cfg = {"api_key": "", "base_url": "", "model": ""}
        placeholder_cfg = {"api_key": "sk-xxxx123456", "base_url": "", "model": ""}

        res1 = engine.research_university_profile("目标院校", "马克思主义理论", api_config=empty_cfg)
        assert res1.get("code") in ["10466", "10464"]
        assert "OFFLINE_BASELINE" not in res1.get("catalog_source")

        res2 = engine.research_university_profile("对比院校B", "马克思主义理论", api_config=placeholder_cfg)
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

