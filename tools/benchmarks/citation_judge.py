# -*- coding: utf-8 -*-
"""C2 引文忠实度评测集的 judge —— 按 ``kind`` 分发到各被测对象。

为什么单独成文件（而不是内联进 ``evaluate_pipeline``）：
  * 分发有 5 个分支，内联会把评测流水线撑成第二个巨石（复评报告 P0-1 的教训）；
  * 元测试需要**独立**调用 judge 注入变异用例（阴性对照），独立模块可直接导入。

被测对象与规划 C2 五类的对应关系：
  * ``citation``     → ``citation_engine.verify_citations``（无据引用）
  * ``evidence``     → ``evidence_engine.build_evidence``（过期数据 / 二手源冒充）
  * ``conflict``     → ``evidence_engine.resolve_conflicts``（跨校混淆·多源冲突仲裁）
  * ``extract``      → ``DocumentExtractor.extract_from_html``（跨校混淆·全链路）
  * ``source_label`` → ``DocumentExtractor._source_label``（伪造 URL / 域名归属）

完全离线、确定性、无网络、无 LLM。judge 抛异常由 runner 记为失败（不中断整批）。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

__all__ = ["build_judge", "KINDS"]

#: 评测集支持的 kind 全集（数据契约测试据此校验）
KINDS = ("citation", "evidence", "conflict", "extract", "source_label")

try:  # 脚本式路径：``tools/`` 已在 sys.path（evaluate_pipeline 的直接调用）
    from intelligence.citation_engine import UngroundedCitation, verify_citations
    from intelligence.evidence_engine import build_evidence, resolve_conflicts
    from intelligence.extractor import DocumentExtractor
except ImportError:  # 包内导入：``tools.benchmarks.citation_judge``
    from tools.intelligence.citation_engine import UngroundedCitation, verify_citations
    from tools.intelligence.evidence_engine import build_evidence, resolve_conflicts
    from tools.intelligence.extractor import DocumentExtractor


# ════════════════════════════════════════════════════════════════
# 各 kind 的判定
# ════════════════════════════════════════════════════════════════

def _judge_citation(case: Dict[str, Any]) -> Tuple[bool, str]:
    """引文溯源闸门：该拦的拦、该放的放。"""
    try:
        verify_citations(case["answer"], case["documents"])
        accepted, why = True, "溯源通过"
    except UngroundedCitation as e:
        accepted, why = False, str(e)
    expected = bool(case["expect_accept"])
    ok = accepted == expected
    return ok, (
        f"期望{'放行' if expected else '拦截'}，实际{'放行' if accepted else '拦截'}"
        f"（{why[:80]}）"
    )


def _judge_evidence(case: Dict[str, Any]) -> Tuple[bool, str]:
    """证据状态机：status / 信源级别 / 置信度上限。"""
    ev = build_evidence(
        field_name=case.get("field_name", "拟招生人数"),
        value=case.get("value"),
        unit=case.get("unit", ""),
        exam_year=case["exam_year"],
        source_type=case["source_type"],
        source_name=case.get("source_name", "示例来源"),
        source_url=case.get("source_url", "https://example.org/x"),
        published_at=case.get("published_at"),
        target_year=case["target_year"],
        ssl_verified=case.get("ssl_verified", True),
        quote=case.get("quote"),
        source_text=case.get("source_text"),
    )
    problems = []
    if ev.status != case["expect_status"]:
        problems.append(f"status 期望 {case['expect_status']}，实际 {ev.status}")
    if "expect_level" in case and ev.source.level != case["expect_level"]:
        problems.append(f"level 期望 {case['expect_level']}，实际 {ev.source.level}")
    if "expect_confidence_max" in case and ev.confidence > case["expect_confidence_max"]:
        problems.append(
            f"confidence 期望 ≤{case['expect_confidence_max']}，实际 {ev.confidence}"
        )
    if problems:
        return False, "；".join(problems)
    return True, f"{ev.status}（{ev.source.level} 级，置信度 {ev.confidence}）"


def _judge_conflict(case: Dict[str, Any]) -> Tuple[bool, str]:
    """多源冲突仲裁：同字段同年份值不一致必须标 CONFLICT。"""
    evs = [build_evidence(target_year=case["target_year"], **e) for e in case["evidences"]]
    resolved = resolve_conflicts(evs)
    if case.get("expect_empty"):
        return len(resolved) == 0, f"期望空结果，实际 {len(resolved)} 条"
    statuses = sorted({ev.status for ev in resolved})
    if case.get("expect_no_conflict"):
        # 边界：年份锁定（OUTDATED）与冲突仲裁的交互 —— 不同年份本就分组不同，
        # 不得因值不同而误判冲突。
        ok = "CONFLICT" not in statuses
        return ok, f"期望无 CONFLICT，实际 {statuses}"
    expected = case["expect_status"]
    ok = statuses == [expected]
    return ok, f"期望全部 {expected}，实际 {statuses}"


def _judge_extract(case: Dict[str, Any]) -> Tuple[bool, str]:
    """跨校混淆·全链路：页面域名不属于目标院校时，任何证据都不得标官方。"""
    evs = DocumentExtractor().extract_from_html(
        html_text=case["html_text"],
        page_url=case["page_url"],
        school_name=case["school_name"],
        target_year=case["target_year"],
    )
    if not evs:
        return False, "未产出任何证据（夹具页应至少产出 1 条）"
    names = [ev.source.name for ev in evs]
    if case["expect_official"]:
        ok = any("官方" in n for n in names)
        return ok, f"期望至少一条含官方标签，实际 {names}"
    bad = [n for n in names if "官方" in n]
    if bad:
        return False, f"跨校页面不得标官方，违规 {bad}"
    return True, f"全部为外部来源（{len(names)} 条）"


def _judge_source_label(case: Dict[str, Any]) -> Tuple[bool, str]:
    """伪造 URL / 域名归属：只有目标院校官方域才可冠「官方」。"""
    label = DocumentExtractor._source_label(
        case["page_url"], case["school_name"], case.get("official_kind", "官方公告")
    )
    problems = []
    want = case.get("expect_label_contains")
    if want and want not in label:
        problems.append(f"应含 {want!r}")
    forbid = case.get("expect_label_not_contains")
    if forbid and forbid in label:
        problems.append(f"不应含 {forbid!r}")
    if problems:
        return False, f"{'；'.join(problems)} ｜ 实际标签: {label!r}"
    return True, label


_DISPATCH = {
    "citation": _judge_citation,
    "evidence": _judge_evidence,
    "conflict": _judge_conflict,
    "extract": _judge_extract,
    "source_label": _judge_source_label,
}


def build_judge():
    """返回评测集 judge：``judge(case) -> (passed, detail)``。

    未知 ``kind`` 记为失败（而非崩溃）—— 数据损坏应由 runner 的 invalid 通道
    或数据契约测试暴露，而不是让整批判定中断。
    """

    def judge(case: Dict[str, Any]) -> Tuple[bool, str]:
        fn = _DISPATCH.get(case.get("kind"))
        if fn is None:
            return False, f"未知 kind: {case.get('kind')!r}（应为 {list(KINDS)} 之一）"
        return fn(case)

    return judge
