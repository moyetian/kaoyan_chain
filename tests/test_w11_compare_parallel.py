# -*- coding: utf-8 -*-
"""[W11 compare 两校并行] 双校研究从串行改并行（ThreadPoolExecutor）。

背景（多角色实测）
------------------
compare 卡死修复（v3 递归三层防御）后真机两校 80.7s 完成（此前 600s+ 卡死）。
每校在线研究预算 200s 且两校串行 → 最坏墙钟 400s；并行后总墙钟 ≈ 单校最坏。

安全性核实：递归守卫 ``_RESEARCH_DEPTH`` 是 threading.local——两校各自新线程
均从 depth=0 起，天然隔离；``SchoolComparator`` 实例无运行期可变状态
（registry/chsi 仅 __init__ 赋值）。单校异常回落与在线研究同源的本地降级引擎
（dynamic_fallback_profile），不拖垮整体。

全程离线：假 ``_get_school_profile`` / 本地降级引擎，不发起网络请求。
"""

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def _comparator():
    from tools.intelligence.comparator import SchoolComparator
    return SchoolComparator()


# ── 1. 并行性：两校研究时间区间必须重叠 ────────────────────────────────


def test_two_profiles_run_in_parallel(monkeypatch):
    from tools.intelligence.comparator import SchoolComparator

    intervals = {}

    def fake_profile(self, school_name, entity, major_keyword, api_config=None):
        t0 = time.monotonic()
        time.sleep(0.35)
        intervals[school_name] = (t0, time.monotonic())
        return {"name": school_name}

    monkeypatch.setattr(SchoolComparator, "_get_school_profile", fake_profile)
    cmp = _comparator()

    info1, info2 = cmp._get_two_profiles("甲校", None, "乙校", None, "计算机")

    assert info1["name"] == "甲校"
    assert info2["name"] == "乙校"
    (s1, e1), (s2, e2) = intervals["甲校"], intervals["乙校"]
    overlap = min(e1, e2) - max(s1, s2)
    assert overlap > 0.05, (
        f"两校研究时间区间应重叠（并行），实际 overlap={overlap:.3f}s —— "
        f"若为负值说明仍是串行")


def test_compare_output_order_is_fixed(monkeypatch):
    """并行不得打乱 school1/school2 的对应关系（即便第二校先完成）。"""
    from tools.intelligence.comparator import SchoolComparator

    def fake_profile(self, school_name, entity, major_keyword, api_config=None):
        if school_name == "甲校":
            time.sleep(0.25)          # 甲校慢、乙校快 —— 完成顺序与参数顺序相反
        return {"name": school_name}

    monkeypatch.setattr(SchoolComparator, "_get_school_profile", fake_profile)
    cmp = _comparator()

    info1, info2 = cmp._get_two_profiles("甲校", None, "乙校", None, "计算机")
    assert info1["name"] == "甲校"
    assert info2["name"] == "乙校"


# ── 2. 单校异常回落（不拖垮整体） ──────────────────────────────────────


def test_single_school_failure_falls_back_to_local(monkeypatch):
    from tools.intelligence.comparator import SchoolComparator

    def fake_profile(self, school_name, entity, major_keyword, api_config=None):
        if school_name == "坏校":
            raise RuntimeError("模拟在线研究崩溃")
        return {"name": school_name, "catalog_source": "[RESEARCH_VERIFIED]"}

    monkeypatch.setattr(SchoolComparator, "_get_school_profile", fake_profile)
    cmp = _comparator()

    info_bad, info_ok = cmp._get_two_profiles("坏校", None, "好校", None, "计算机")

    # 好校不受影响
    assert info_ok["catalog_source"] == "[RESEARCH_VERIFIED]"
    # 坏校回落本地降级：结构化画像且 name 正确（绝不虚构在线数据）
    assert info_bad["name"] == "坏校"
    assert "catalog_source" in info_bad or "score_trend" in info_bad


def test_fallback_engine_failure_returns_minimal_dict(monkeypatch):
    """连本地降级引擎都失败时，仍有最小结构化画像（compare 不崩）。"""
    from tools.intelligence import comparator as cmp_mod
    from tools.intelligence.comparator import SchoolComparator

    def fake_profile(self, school_name, entity, major_keyword, api_config=None):
        raise RuntimeError("模拟在线研究崩溃")

    class _BoomEngine:
        def dynamic_fallback_profile(self, school_name, major_keyword=""):
            raise RuntimeError("模拟降级引擎崩溃")

    monkeypatch.setattr(SchoolComparator, "_get_school_profile", fake_profile)
    monkeypatch.setattr("tools.intelligence.agentic_research.get_research_engine",
                        lambda: _BoomEngine())
    cmp = _comparator()

    info1, info2 = cmp._get_two_profiles("甲校", None, "乙校", None, "计算机")

    for info, name in ((info1, "甲校"), (info2, "乙校")):
        assert info["name"] == name
        assert "FALLBACK" in info["catalog_source"]


# ── 3. 完整 compare() 集成（下游格式化能消费并行结果） ─────────────────


def test_compare_end_to_end_with_parallel_profiles(monkeypatch):
    from tools.intelligence.comparator import SchoolComparator

    def fake_profile(self, school_name, entity, major_keyword, api_config=None):
        return {"name": school_name, "majors": [], "catalog_source": ""}

    monkeypatch.setattr(SchoolComparator, "_get_school_profile", fake_profile)
    cmp = _comparator()

    result = cmp.compare("甲校", "乙校", "计算机", save_report=False)

    assert result["school1"] == "甲校"
    assert result["school2"] == "乙校"
    assert result["info1"]["name"] == "甲校"
    assert result["info2"]["name"] == "乙校"
    assert result["markdown_report"], "完整 compare 应产出研报文本"
