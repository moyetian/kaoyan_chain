# -*- coding: utf-8 -*-
"""D2 判卷明细结构化留痕（grading_trace）回归测试。

覆盖：
  * mock llm_client（三评审 + 仲裁）→ traces JSONL 生成、字段齐全、
    divergence / flipped / final 实测正确；
  * trace_enabled=False → 不落盘；
  * 落盘失败两条路径（grading_trace 内部目录不可建 / open_grader 侧 record
    抛异常）→ 判分结果正常返回，绝不中断；
  * trace_include_answer=False → 无 student_answer 字段；
  * 早退路径（空作答 / 未启用 / 无有效评审）不落痕（设计取舍）；
  * 隐私：data/grading/ 在 .gitignore 与发布排除清单全部出口中，
    且有阴性对照（公开派生库仍发布、兄弟目录不误伤）。

全部使用合成数据（示例题/示例作答），不含任何真实身份信息。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sync_publish as sp  # noqa: E402  （公开副本中为占位实现，见用例内守卫）
from privacy_policy import is_local_artifact, should_publish  # noqa: E402
from tools.skills import open_grader as og  # noqa: E402

GQ = "示例题干：试述生产力与生产关系的辩证关系。"
GA = "示例作答：生产力决定生产关系，生产关系对生产力具有反作用。"
TRACE_META = {"paper_id": "", "question_id": "7"}

pytestmark = pytest.mark.skipif(og._grading_trace is None,
                                reason="grading_trace 模块不可导入")


def _client_factory(review_scores=None, judge_score=8.0):
    """固定回放：rubric 一次 + 三评审（按端点名取分）+ 仲裁。"""
    review_scores = review_scores or {"A": 8.0, "B": 8.0, "C": 8.0}

    def _client(messages, endpoint):
        content = messages[-1]["content"]
        if "生成评分要点" in content:
            return json.dumps({"rubric": [{"id": 1, "point": "P", "score": 10.0}],
                               "derived_from": "reference"}, ensure_ascii=False)
        if "多位阅卷人" in content:
            return json.dumps({"total": judge_score,
                               "rubric_hits": [{"id": 1, "hit": "full"}],
                               "mistake_type": "无", "confidence": 0.9,
                               "reason": "仲裁"}, ensure_ascii=False)
        name = endpoint.get("name")
        hit = "partial" if review_scores[name] < 6 else "full"
        return json.dumps({"total": review_scores[name],
                           "rubric_hits": [{"id": 1, "hit": hit}],
                           "mistake_type": "无", "confidence": 0.9,
                           "reason": f"复核{name}"}, ensure_ascii=False)
    return _client


def _cfg(**over):
    cfg = {"enabled": True, "cache_rubric": False, "max_retries": 0,
           "reviewers": [{"name": n, "base_url": "http://m/v1", "api_key": "k",
                          "model": "m", "weight": 1.0, "same_source": True}
                         for n in "ABC"],
           "judge": {"name": "judge", "base_url": "http://m/v1", "api_key": "k",
                     "model": "m", "weight": 2.0, "enabled": True}}
    cfg.update(over)
    return cfg


@pytest.fixture()
def ws(monkeypatch, tmp_path, _isolate_grading_traces):
    """隔离工作区：KY_WORKSPACE_ROOT 指向 tmp（grading_trace 调用时解析）。

    conftest 的 ``_isolate_grading_traces`` 会话级关闭留痕
    （``KY_GRADING_TRACE=0``），本 fixture 显式解除 —— 本文件测的就是留痕功能
    本身，落盘目标是 tmp 工作区而非真实工作区。依赖该 fixture 以保证解除
    发生在关闭之后（fixture 依赖序确定）。
    """
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("KY_WORKSPACE_ROOT", str(root))
    monkeypatch.delenv("KY_GRADING_TRACE", raising=False)
    return root


def _trace_lines(ws):
    path = ws / "data" / "grading" / "traces" / \
        f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    if not path.exists():
        return path, []
    return path, [json.loads(line) for line in
                  path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ════════════════════════════════════════════════════════════════
# 第 1 层：真实判分路径落痕 —— 字段齐全 + divergence/flipped 正确
# ════════════════════════════════════════════════════════════════

def test_trace_recorded_with_all_fields(ws):
    r = og.grade_open_question(
        GQ, GA, subject="pro", config=_cfg(),
        llm_client=_client_factory({"A": 4.0, "B": 8.0, "C": 8.0}, judge_score=8.0),
        trace_meta=TRACE_META)
    assert r.match_level == 2
    path, lines = _trace_lines(ws)
    assert path.exists(), "真实判分路径必须落一条 trace"
    assert len(lines) == 1
    t = lines[0]
    assert t["question_id"] == "7" and t["paper_id"] == ""
    assert t["prompt_version"] == "v1"
    assert t["subject"] == "pro"
    assert t["stage_rubric"] == {"count": 1, "hit_point_ids": [1],
                                 "derived_from_question": False, "model": "m"}
    assert t["stage_review"] == [
        {"reviewer": "A", "score": 4.0, "mistake_type": "无"},
        {"reviewer": "B", "score": 8.0, "mistake_type": "无"},
        {"reviewer": "C", "score": 8.0, "mistake_type": "无"},
    ]
    assert t["divergence"] == 4.0
    # judge 8.0 与评审均分 6.67 偏离 1.33 ≥ 1.0 → flipped
    assert t["arbitration"]["used"] is True
    assert t["arbitration"]["flipped"] is True
    assert t["arbitration"]["final_score"] == 8.0
    assert t["final"]["score"] == 7.2
    assert t["final"]["match_level"] == 2
    assert t["final"]["degraded"] is False
    assert t["question"] == GQ
    assert t["student_answer"] == GA
    datetime.fromisoformat(t["ts"])  # ts 必须是可解析的 ISO 本地时间


def test_flipped_false_when_judge_agrees(ws):
    og.grade_open_question(
        GQ, GA, subject="pro", config=_cfg(),
        llm_client=_client_factory({"A": 8.0, "B": 8.0, "C": 8.0}, judge_score=8.0),
        trace_meta=TRACE_META)
    _, lines = _trace_lines(ws)
    assert len(lines) == 1
    assert lines[0]["divergence"] == 0.0
    assert lines[0]["arbitration"]["used"] is True
    assert lines[0]["arbitration"]["flipped"] is False


def test_trace_include_answer_false_omits_answer(ws):
    og.grade_open_question(
        GQ, GA, subject="pro", config=_cfg(trace_include_answer=False),
        llm_client=_client_factory(), trace_meta=TRACE_META)
    _, lines = _trace_lines(ws)
    assert len(lines) == 1
    assert "student_answer" not in lines[0]
    assert lines[0]["question"] == GQ


def test_trace_appends_multiple_lines(ws):
    for _ in range(2):
        og.grade_open_question(GQ, GA, subject="pro", config=_cfg(),
                               llm_client=_client_factory(), trace_meta=TRACE_META)
    _, lines = _trace_lines(ws)
    assert len(lines) == 2, "同日多次判分必须追加而非覆盖"


# ════════════════════════════════════════════════════════════════
# 第 2 层：开关与失败降级 —— 判分绝不中断
# ════════════════════════════════════════════════════════════════

def test_trace_disabled_writes_nothing(ws):
    r = og.grade_open_question(GQ, GA, subject="pro",
                               config=_cfg(trace_enabled=False),
                               llm_client=_client_factory(), trace_meta=TRACE_META)
    assert r.match_level == 2
    path, lines = _trace_lines(ws)
    assert not path.exists() and lines == []


def test_record_failure_does_not_break_grading(ws, monkeypatch):
    def _boom(payload):
        raise RuntimeError("模拟落盘失败")
    monkeypatch.setattr(og._grading_trace, "record_grading_trace", _boom)
    r = og.grade_open_question(GQ, GA, subject="pro", config=_cfg(),
                               llm_client=_client_factory(), trace_meta=TRACE_META)
    assert r.match_level == 2, "留痕失败绝不允许影响判分结果"


def test_unwritable_trace_dir_silently_degrades(ws):
    # ws/data 是文件而非目录 → mkdir 必然失败 → record 静默返回 None
    (ws / "data").write_text("占位文件", encoding="utf-8")
    assert og._grading_trace.record_grading_trace({"x": 1}) is None
    r = og.grade_open_question(GQ, GA, subject="pro", config=_cfg(),
                               llm_client=_client_factory(), trace_meta=TRACE_META)
    assert r.match_level == 2


# ════════════════════════════════════════════════════════════════
# 第 3 层：早退路径不落痕（设计取舍：非真实判分不审计）
# ════════════════════════════════════════════════════════════════

def test_empty_answer_early_exit_no_trace(ws):
    r = og.grade_open_question(GQ, "", subject="pro", config=_cfg(),
                               llm_client=_client_factory(), trace_meta=TRACE_META)
    assert r.match_level == 0
    path, lines = _trace_lines(ws)
    assert not path.exists()


def test_not_enabled_early_exit_no_trace(ws):
    r = og.grade_open_question(GQ, GA, subject="pro", config=_cfg(enabled=False),
                               llm_client=_client_factory(), trace_meta=TRACE_META)
    assert r.match_level == 1
    path, lines = _trace_lines(ws)
    assert not path.exists()


def test_no_valid_review_early_exit_no_trace(ws):
    def _bad_client(messages, endpoint):
        content = messages[-1]["content"]
        if "生成评分要点" in content:
            return json.dumps({"rubric": [{"id": 1, "point": "P", "score": 10.0}],
                               "derived_from": "reference"}, ensure_ascii=False)
        return "这不是 JSON"  # 三评审全部弃权
    r = og.grade_open_question(GQ, GA, subject="pro", config=_cfg(),
                               llm_client=_bad_client, trace_meta=TRACE_META)
    assert r.match_level == 1
    path, lines = _trace_lines(ws)
    assert not path.exists()


# ════════════════════════════════════════════════════════════════
# 第 4 层：隐私 —— data/grading/ 必须进全部发布出口排除清单
# ════════════════════════════════════════════════════════════════

def test_data_grading_excluded_from_publish_policy():
    """策略单一事实源：data/grading/ 整棵不发布（sync 文件级 / build staging /
    骨架部署三条出口共用 should_publish；隐私策略模块在公开副本中原样保留）。"""
    rel = "data/grading/traces/2026-10-06.jsonl"
    assert should_publish(rel) is False
    assert is_local_artifact(rel) is True
    assert should_publish("data/grading/") is False
    # 阴性对照：公开派生库仍发布；兄弟目录不被误伤
    assert should_publish("data/universities/registry.json") is True
    assert should_publish("data/universities/national_institutions.json") is True
    assert should_publish("data/gradings/other.json") is True


def test_data_grading_excluded_from_sync_publish_gates():
    """sync_publish 目录级与文件级闸门同口径排除 data/grading/。

    公开副本里 sync_publish 是刻意保留的占位实现（无 dir_should_exclude），
    本用例跳过 —— 该出口由私有侧 CI 覆盖。
    """
    if not hasattr(sp, "dir_should_exclude"):
        pytest.skip("公开副本：sync_publish 为占位实现（私有侧 CI 覆盖）")
    assert sp.dir_should_exclude(["data"], "grading") is True
    assert sp.file_should_exclude(["data", "grading", "traces"],
                                  "2026-10-06.jsonl") is True


def test_gitignore_protects_data_grading():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "\ndata/grading/\n" in text, "data/grading/ 必须进 .gitignore"
