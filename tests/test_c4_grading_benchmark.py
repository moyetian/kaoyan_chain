# -*- coding: utf-8 -*-
"""C4 元测试：开放题判分评测 pilot（与预标一致率）。

背景（升级规划 C4）：开放题判分链路（``open_grader.grade_open_question``）此前
只有 stub 级单测，没有真实 LLM 输出下的度量。C4 引入 pilot：
  * ``tests/benchmarks/grading_pilot.jsonl`` —— 30 份 AI 预标样本（用户抽检）；
  * ``tests/benchmarks/grading_snapshots.jsonl`` —— 真实 LLM 响应快照
    （``--grading-live`` 一次性采集后冻结，回放零网络零成本）；
  * ``tools/benchmarks/grading_judge.py`` —— 三指标：采分点命中一致率 /
    总分 MAE / 错因一致率。

本文件分六层锁定：
  1. 数据集契约（规模 / 分布 / 自洽 / 中性化）；
  2. 调用分类与快照键控（rubric / review:A / judge 三态）；
  3. 回放端到端三指标（完美快照全对齐、偏差快照下降、退出码语义）；
  4. 阴性对照（数据自洽性校验的假阴性、快照裁剪、忠实复现失败）；
  5. **[规划硬约束] 不进 CI 门禁**的元测试钉住（防未来误加）；
  6. ``OpenGradeResult.judge`` 可观测性（C4 新增字段：仲裁详情不再丢失）。

诚实边界：AI 预标下测的是「与预标一致率」（AI 对 AI），不是人工金标准准确率；
正式 MAE / Kappa 目标待人工标注量上来后再定 —— 第 3 层的断言只锁定
「口径与机制正确」，不锁定任何正式达标阈值。
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmarks import grading_judge as gj  # noqa: E402
from skills import open_grader  # noqa: E402

CASES_FILE = ROOT / "tests" / "benchmarks" / "grading_pilot.jsonl"
SNAPSHOTS_FILE = ROOT / "tests" / "benchmarks" / "grading_snapshots.jsonl"


def _load_cases():
    return [json.loads(line) for line in
            CASES_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _perfect_calls(case):
    """按预标构造「完美链路」响应（评测器应给出全对齐指标）。"""
    if case["quality"] == "blank":
        return {}
    scores = {k["id"]: k["score"] for k in case["key_points"]}
    levels = {h["id"]: h["level"] for h in case["expected"]["hit_points"]}
    hits = [{"id": i, "hit": levels.get(i, "none"), "evidence": "e"} for i in sorted(scores)]
    resp = json.dumps({"total": case["expected"]["total"], "rubric_hits": hits,
                       "mistake_type": case["expected"]["mistake_type"],
                       "confidence": 0.9, "reason": "完美回放"}, ensure_ascii=False)
    calls = {f"review:{n}": {"ok": True, "response": resp} for n in "ABC"}
    calls["judge"] = {"ok": True, "response": resp}
    return calls


def _write_snaps(path, cases, calls_fn):
    lines = [json.dumps({"id": c["id"], "captured_at": "2026-01-01T00:00:00",
                         "model": "test-mock", "calls": calls_fn(c)},
                        ensure_ascii=False) for c in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# 第 1 层：数据集契约
# ════════════════════════════════════════════════════════════════

def test_benchmark_file_exists_and_meets_scale():
    assert CASES_FILE.exists(), f"评测集缺失: {CASES_FILE}"
    cases = _load_cases()
    assert len(cases) == 30, f"规划口径 30 份，实际 {len(cases)}"
    assert len(cases) >= gj.MIN_PILOT_SAMPLES


def test_benchmark_subject_distribution():
    cases = _load_cases()
    dist = {}
    for c in cases:
        dist[c["subject"]] = dist.get(c["subject"], 0) + 1
    assert dist == {"pro": 12, "pol": 10, "eng": 8}, dist


def test_benchmark_quality_distribution():
    cases = _load_cases()
    dist = {}
    for c in cases:
        dist[c["quality"]] = dist.get(c["quality"], 0) + 1
    assert dist == {"good": 10, "medium": 12, "poor": 6, "blank": 2}, dist


def test_benchmark_ids_are_unique():
    ids = [c["id"] for c in _load_cases()]
    assert len(ids) == len(set(ids))


def test_benchmark_cases_pass_contract_validation():
    """每份用例都必须过 validate_case（自洽：总分与命中完整度对应）。"""
    problems = []
    for c in _load_cases():
        for p in gj.validate_case(c):
            problems.append(f"{c['id']}: {p}")
    assert not problems, problems


def test_benchmark_has_no_real_identity():
    """数据集必须中性化：无真实院校名 / 联系方式 / 学号等身份痕迹。"""
    suspect = re.compile(
        r"(大学|学院)(?!生)|学号|身份证|@[a-z0-9.-]+\.(com|cn|net)|1[3-9]\d{9}")
    for c in _load_cases():
        text = json.dumps(c, ensure_ascii=False)
        assert not suspect.search(text), f"{c['id']} 疑似身份痕迹: {suspect.search(text).group(0)}"


def test_benchmark_key_points_sum_to_ten():
    for c in _load_cases():
        total = sum(k["score"] for k in c["key_points"])
        assert abs(total - 10.0) < 0.01, f"{c['id']} 采分点合计 {total}"


def test_benchmark_expected_totals_are_internal_consistent():
    """预标总分 == Σ full 分值 + 0.5 × Σ partial 分值（与链路重算口径同源）。"""
    for c in _load_cases():
        scores = {k["id"]: k["score"] for k in c["key_points"]}
        want = sum(scores[h["id"]] * (1.0 if h["level"] == "full" else 0.5)
                   for h in c["expected"]["hit_points"])
        assert abs(want - c["expected"]["total"]) <= 0.06, c["id"]


# ════════════════════════════════════════════════════════════════
# 第 2 层：调用分类与快照键控
# ════════════════════════════════════════════════════════════════

def _user_msg(text):
    return [{"role": "system", "content": "s"}, {"role": "user", "content": text}]


def test_classify_call_three_kinds():
    assert gj.classify_call(_user_msg("请为下列【数学】题目生成评分要点（rubric）。")) == "rubric"
    assert gj.classify_call(_user_msg("多位阅卷人对同一份【数学】作答给出不一致评分，请裁定。")) == "judge"
    assert gj.classify_call(_user_msg("请按评分要点为下列【数学】作答打分。")) == "review"


def test_classify_call_uses_last_user_message():
    msgs = [{"role": "user", "content": "生成评分要点"}, {"role": "assistant", "content": "..."},
            {"role": "user", "content": "请按评分要点为下列作答打分。"}]
    assert gj.classify_call(msgs) == "review"


def test_classify_call_unknown_text_falls_back_to_review():
    """未知提示词兜底为 review（fail-closed：快照读不到 → invalid，不静默错配）。"""
    assert gj.classify_call(_user_msg("随便什么文本")) == "review"
    assert gj.classify_call([]) == "review"


def test_call_key_shape():
    assert gj.call_key(_user_msg("请按评分要点为下列作答打分。"), {"name": "B"}) == "review:B"
    assert gj.call_key(_user_msg("生成评分要点"), {"name": "A"}) == "rubric"
    assert gj.call_key(_user_msg("多位阅卷人对同一份作答给出不一致评分"), {"name": "judge"}) == "judge"


# ════════════════════════════════════════════════════════════════
# 第 3 层：回放端到端三指标
# ════════════════════════════════════════════════════════════════

def test_missing_snapshots_is_not_evaluable(tmp_path):
    res = gj.evaluate_grading_pilot(snapshots_path=tmp_path / "none.jsonl")
    assert res["evaluable"] is False
    assert res["exit_code"] == 2
    assert "快照" in res["reason"]


def test_perfect_replay_yields_aligned_metrics(tmp_path):
    """完美快照（按预标构造）→ 三指标全对齐、退出码 0。"""
    sp = tmp_path / "perfect.jsonl"
    _write_snaps(sp, _load_cases(), _perfect_calls)
    res = gj.evaluate_grading_pilot(snapshots_path=sp)
    m = res["metrics"]
    assert res["evaluable"] is True and res["exit_code"] == 0
    assert res["samples"] == 30
    assert m["hit_jaccard"] == 1.0 and m["hit_recall"] == 1.0 and m["hit_precision"] == 1.0
    assert m["total_mae"] == 0.0
    assert m["mistake_agreement"] == 1.0
    # blank 档不发起调用 → 30 份里 2 份不仲裁
    assert m["arbitrated_rate"] == pytest.approx(28 / 30, abs=1e-3)
    assert m["degraded_rate"] == 0.0


def test_drifted_replay_degrades_metrics(tmp_path):
    """注入偏差（命中集错一个点 + 总分降 3 + 错因全错）→ 指标下降且跌破预警线。"""
    cases = _load_cases()

    def drifted(case):
        calls = _perfect_calls(case)
        if not calls or not case["expected"]["hit_points"]:
            return calls
        first = min(h["id"] for h in case["expected"]["hit_points"])
        levels = {h["id"]: h["level"] for h in case["expected"]["hit_points"]}
        hits = [{"id": k["id"], "hit": ("none" if k["id"] == first else levels.get(k["id"], "none")),
                 "evidence": "e"} for k in case["key_points"]]
        resp = json.dumps({"total": max(0.0, case["expected"]["total"] - 3.0),
                           "rubric_hits": hits, "mistake_type": "计算失误",
                           "confidence": 0.9, "reason": "偏差注入"}, ensure_ascii=False)
        return {f"review:{n}": {"ok": True, "response": resp} for n in "ABC"} | \
               {"judge": {"ok": True, "response": resp}}

    sp = tmp_path / "drift.jsonl"
    _write_snaps(sp, cases, drifted)
    res = gj.evaluate_grading_pilot(snapshots_path=sp)
    m = res["metrics"]
    assert m["hit_jaccard"] < 1.0
    assert m["total_mae"] > 0.0
    assert m["mistake_agreement"] < 1.0
    assert res["exit_code"] == 1, "偏差注入应跌破探索性预警线"


def test_snapshot_gap_is_invalid_not_silent(tmp_path):
    """快照缺 1 份 → invalid（退出码 2），绝不把数据缺失伪装成模型弃权。"""
    sp = tmp_path / "gap.jsonl"
    cases = _load_cases()
    _write_snaps(sp, cases[:-1], _perfect_calls)
    res = gj.evaluate_grading_pilot(snapshots_path=sp)
    assert res["exit_code"] == 2
    assert any(i["id"] == cases[-1]["id"] for i in res["invalid"])


def test_insufficient_samples_is_not_evaluable(tmp_path):
    sp = tmp_path / "tiny.jsonl"
    _write_snaps(sp, _load_cases()[:5], _perfect_calls)
    res = gj.evaluate_grading_pilot(snapshots_path=sp)
    assert res["evaluable"] is False and res["exit_code"] == 2


def test_captured_failure_is_replayed_faithfully(tmp_path):
    """快照记录 ok=False（采集时模型失败）→ 回放复现弃权，**不算快照缺口**。"""
    cases = _load_cases()
    snap_calls = _perfect_calls(cases[0])
    snap_calls["review:C"] = {"ok": False, "error": "HTTP 500"}
    sp = tmp_path / "fail.jsonl"
    _write_snaps(sp, cases[:1], lambda _c: snap_calls)
    res = gj.evaluate_grading_pilot(snapshots_path=sp, min_samples=1)
    assert res["scored_samples"] == 1
    assert res["details"][0]["snapshot_misses"] == []
    assert res["details"][0]["degraded"] is True


def test_replay_client_miss_is_recorded():
    misses = []
    client = gj.build_replay_client({}, miss_sink=misses)
    assert client(_user_msg("请按评分要点为下列作答打分。"), {"name": "A"}) is None
    assert misses == ["review:A"]


def test_replay_client_returns_frozen_response():
    client = gj.build_replay_client(
        {"review:A": {"ok": True, "response": "R"}})
    assert client(_user_msg("请按评分要点为下列作答打分。"), {"name": "A"}) == "R"


# ════════════════════════════════════════════════════════════════
# 第 4 层：阴性对照
# ════════════════════════════════════════════════════════════════

def _base_case():
    return {
        "id": "t-01", "subject": "pro", "quality": "medium", "question": "Q",
        "reference_answer": "R",
        "key_points": [{"id": 1, "point": "P1", "score": 4.0},
                       {"id": 2, "point": "P2", "score": 3.0},
                       {"id": 3, "point": "P3", "score": 3.0}],
        "student_answer": "A",
        "expected": {"hit_points": [{"id": 1, "level": "full"}, {"id": 2, "level": "partial"}],
                     "total": 5.5, "mistake_type": "概念漏洞"},
    }


def test_validate_case_accepts_base_case():
    assert gj.validate_case(_base_case()) == []


@pytest.mark.parametrize("mutate,keyword", [
    (lambda c: c["expected"].update(total=9.9), "不自洽"),
    (lambda c: c["key_points"][0].update(score=9.0), "分值合计"),
    (lambda c: c["expected"].update(mistake_type="心情不好"), "mistake_type"),
    (lambda c: c["expected"]["hit_points"][0].update(level="half"), "level"),
    (lambda c: c["expected"]["hit_points"][0].update(id=99), "不在 key_points"),
    (lambda c: c.update(subject="math"), "subject"),
    (lambda c: c.update(quality="perfect"), "quality"),
    (lambda c: c.update(question="  "), "question"),
])
def test_validate_case_rejects_mutations(mutate, keyword):
    """阴性对照：每类契约破坏都必须被 validate_case 拦下。"""
    case = _base_case()
    mutate(case)
    problems = gj.validate_case(case)
    assert problems, "变异未被拦下（假阴性）"
    assert any(keyword in p for p in problems), (keyword, problems)


def test_validate_case_blank_must_have_empty_answer():
    case = _base_case()
    case["quality"] = "blank"
    assert any("blank" in p for p in gj.validate_case(case))
    case["student_answer"] = ""
    assert gj.validate_case(case) == []


def test_validate_case_key_points_count_bounds():
    case = _base_case()
    case["key_points"] = case["key_points"][:2]
    assert any("3~6 条" in p for p in gj.validate_case(case))


def test_dataset_mutation_is_caught_end_to_end(tmp_path):
    """把数据集里某份的 total 改坏 → 该 case 进 invalid，评测退出码 2。"""
    cases = _load_cases()
    broken = json.loads(json.dumps(cases))
    broken[0]["expected"]["total"] = 9.9
    sp = tmp_path / "broken.jsonl"
    _write_snaps(sp, broken, _perfect_calls)
    # 把坏数据集写进临时用例文件（评测读 cases_path）
    cp = tmp_path / "cases.jsonl"
    cp.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in broken) + "\n",
                  encoding="utf-8")
    res = gj.evaluate_grading_pilot(cases_path=cp, snapshots_path=sp)
    assert res["exit_code"] == 2
    assert any(i["id"] == broken[0]["id"] for i in res["invalid"])


def test_aggregate_hit_set_majority_semantics():
    """票值加权聚合：三评审全 partial（0.5）判命中；全 none 判未命中。"""
    rubric = [{"id": 1, "point": "P", "score": 10.0}]
    def rv(name, hit):
        return open_grader.ReviewResult(name=name, total=5.0, weight=1.0,
                                        rubric_hits=[{"id": 1, "hit": hit}])
    assert gj.aggregate_hit_set(rubric, [rv(n, "partial") for n in "ABC"]) == {1}
    assert gj.aggregate_hit_set(rubric, [rv(n, "none") for n in "ABC"]) == set()
    # 1 full + 2 none = 1/3 < 0.5 → 不命中
    assert gj.aggregate_hit_set(rubric, [rv("A", "full"), rv("B", "none"), rv("C", "none")]) == set()
    # 主审权重 2.0：judge full + 两评审 none = 2/4 = 0.5 → 命中
    judge = open_grader.ReviewResult(name="judge", total=5.0, weight=2.0,
                                     rubric_hits=[{"id": 1, "hit": "full"}])
    assert gj.aggregate_hit_set(rubric, [rv("A", "none"), rv("B", "none"), judge]) == {1}


def test_empty_sets_count_as_agreement():
    """blank 档：链路与预标命中集都为空 → Jaccard 记 1.0（都空 = 一致）。"""
    case = next(c for c in _load_cases() if c["quality"] == "blank")
    result = open_grader.grade_open_question(
        case["question"], case["student_answer"], subject=case["subject"],
        key_points=case["key_points"], config={"enabled": True})
    m = gj.case_metrics(case, result)
    assert m["jaccard"] == 1.0 and m["abs_total_diff"] == 0.0
    assert m["mistake_match"] is True


# ════════════════════════════════════════════════════════════════
# 第 5 层：[规划硬约束] pilot 不进 CI 门禁（元测试钉住）
# ════════════════════════════════════════════════════════════════

def test_grading_not_wired_into_ci_gate():
    """C4 规划条款：不得进 CI 门禁 —— ci_evaluate_gate.py 与 test.yml 不得出现 --grading。"""
    gate = (ROOT / "tools" / "ci_evaluate_gate.py").read_text(encoding="utf-8")
    assert "--grading" not in gate, "ci_evaluate_gate.py 混入了 --grading（违反 C4 规划）"
    yml = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "--grading" not in yml, "test.yml 混入了 --grading（违反 C4 规划）"


def test_grading_not_in_default_pipeline_run():
    """默认运行（无参数）只跑 srs/ragas/syllabus —— grading 是 pilot，需显式开启。"""
    src = (ROOT / "tools" / "evaluate_pipeline.py").read_text(encoding="utf-8")
    # 行尾允许注释（回退验证曾用 `args.grading = True  # 回退` 绕过过本断言）
    m = re.search(r"if not \(([^)]*)\):\n((?:\s+args\.\w+ = True[^\n]*\n)+)", src)
    assert m, "未找到默认运行分支"
    # 条件表达式里出现 grading 是正确的（表示"显式传了就不走默认"），
    # 关键是默认分支**赋值为 True** 的只有三件套。
    defaults = set(re.findall(r"args\.(\w+) = True", m.group(2)))
    assert defaults == {"srs", "ragas", "syllabus"}, defaults


def test_grading_live_requires_explicit_flag():
    """--grading-live（计费采集）必须是显式开关，且采集前校验配置。"""
    src = (ROOT / "tools" / "evaluate_pipeline.py").read_text(encoding="utf-8")
    assert "--grading-live" in src
    assert "_run_grading_capture" in src
    # 采集函数必须有配置校验（缺 base_url/api_key/model 时报错而非静默）
    cap = src[src.index("def _run_grading_capture"):]
    cap = cap[:cap.index("# ═══")]
    for key in ("base_url", "api_key", "model"):
        assert key in cap, f"采集缺少 {key} 校验"


def test_snapshot_file_never_contains_api_key():
    """快照文件（若已采集）不得含 api_key / Bearer 等凭据痕迹。"""
    if not SNAPSHOTS_FILE.exists():
        pytest.skip("快照尚未采集（--grading-live 后启用本断言）")
    text = SNAPSHOTS_FILE.read_text(encoding="utf-8")
    assert "api_key" not in text and "Bearer " not in text
    assert "sk-" not in text


def test_snapshot_ids_cover_dataset_when_present():
    """快照（若已采集）必须覆盖数据集全部 id，且回放可评测。"""
    if not SNAPSHOTS_FILE.exists():
        pytest.skip("快照尚未采集（--grading-live 后启用本断言）")
    snaps, invalid = gj.load_snapshots(SNAPSHOTS_FILE)
    assert not invalid, invalid
    missing = [c["id"] for c in _load_cases() if c["id"] not in snaps]
    assert not missing, f"快照缺 {missing}"


# ════════════════════════════════════════════════════════════════
# 第 6 层：OpenGradeResult.judge 可观测性（C4 新增字段）
# ════════════════════════════════════════════════════════════════

def _mock_client(score=8.0, mistake="无"):
    def _client(messages, endpoint):
        content = messages[-1]["content"]
        if "生成评分要点" in content:
            return json.dumps({"rubric": [{"id": 1, "point": "P", "score": 10.0}],
                               "derived_from": "reference"})
        if "多位阅卷人" in content:
            return json.dumps({"total": score, "rubric_hits": [{"id": 1, "hit": "full"}],
                               "mistake_type": mistake, "confidence": 0.9,
                               "reason": "仲裁"}, ensure_ascii=False)
        return json.dumps({"total": score, "rubric_hits": [{"id": 1, "hit": "full"}],
                           "mistake_type": mistake, "confidence": 0.9,
                           "reason": "复核"}, ensure_ascii=False)
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


def test_result_exposes_judge_details_when_arbitrated():
    r = open_grader.grade_open_question("Q", "A", config=_cfg(),
                                        llm_client=_mock_client(8.0, "书写丢分"))
    assert r.arbitrated is True
    assert r.judge is not None, "仲裁详情必须在结果中可见（C4 可观测性）"
    assert r.judge.total == 8.0 and r.judge.mistake_type == "书写丢分"
    d = r.to_dict()
    assert d["judge"] is not None and d["judge"]["total"] == 8.0


def test_result_judge_is_none_without_arbitration():
    """不触发仲裁（非同源、无分歧、3 位有效）时 judge 为 None，to_dict 亦然。"""
    cfg = _cfg(reviewers=[{"name": n, "base_url": "http://m/v1", "api_key": "k",
                           "model": "m", "weight": 1.0} for n in "ABC"])
    r = open_grader.grade_open_question("Q", "A", config=cfg,
                                        llm_client=_mock_client(8.0))
    assert r.arbitrated is False and r.judge is None
    assert r.to_dict()["judge"] is None


def test_pilot_config_shape():
    """评测配置：同源三视角 + 强制仲裁（与本机单模型现状一致）+ 关闭 rubric 缓存。"""
    cfg = gj.build_pilot_config("http://x/v1", "k", "m")
    assert cfg["enabled"] is True and cfg["cache_rubric"] is False
    names = [r["name"] for r in cfg["reviewers"]]
    assert names == ["A", "B", "C"]
    assert all(r.get("same_source") is True for r in cfg["reviewers"])
    assert cfg["judge"]["enabled"] is True and cfg["judge"]["weight"] == 2.0


def test_capture_skips_existing_snapshots(tmp_path, monkeypatch):
    """增量采集：已有快照的 case 跳过（不重复计费）。"""
    cases = _load_cases()[:2]
    sp = tmp_path / "snaps.jsonl"
    _write_snaps(sp, cases[:1], _perfect_calls)
    called = {"n": 0}

    def fake_grade(*a, **kw):
        called["n"] += 1
        return open_grader.OpenGradeResult()

    monkeypatch.setattr(gj.open_grader, "grade_open_question", fake_grade)
    stats = gj.capture_snapshots(cases, sp, base_url="http://x/v1", api_key="k", model="m")
    assert stats["skipped"] == 1 and stats["captured"] == 1
    assert called["n"] == 1, "已采集的 case 不应再次调用 LLM"
