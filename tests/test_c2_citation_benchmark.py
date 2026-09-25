# -*- coding: utf-8 -*-
"""C2 回归测试：引文忠实度评测集 + 反幻觉闸门（可证阶段 · 引文溯源）。

背景（升级规划 C2）：反幻觉闸门此前只有 ``--ragas`` 的 9 条内置用例，
"拦得住多少"不可度量。C2 把它升级为可评测资产：

  * ``tests/benchmarks/citation_faithfulness.jsonl`` —— 109 条评测集
    （无据引用 / 伪造 URL / 过期数据 / 跨校混淆 / 二手源冒充 五类）；
  * ``tools/benchmarks/citation_judge.py`` —— 按 ``kind`` 分发到四个被测对象；
  * ``evaluate_pipeline.py --ragas`` + CI 门禁（拦截侧 100% / 放行侧 ≥98%）。

本文件锁定：评测集的数据契约、judge 的分发契约、端到端双指标，以及
**阴性验证**（新注入的编造引用必须被拦）与**阴性对照**（期望值翻转后门禁
必须判红 —— 证明 100% 通过不是"测试空转"）。

另含 C2 建集时实测发现的**真缺陷回归**：非法字段载体（``document_index="abc"``、
``confidence=None``）此前会被 pydantic 的 ``ValidationError`` 抢先抛出，绕过
``UngroundedCitation`` 契约；现两条路径（pydantic / 内置模型）统一拦截。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BENCH_FILE = ROOT / "tests" / "benchmarks" / "citation_faithfulness.jsonl"

from benchmarks.citation_judge import KINDS, build_judge  # noqa: E402
from benchmarks.runner import (  # noqa: E402
    EXIT_FAIL,
    EXIT_INSUFFICIENT,
    EXIT_OK,
    load_cases,
    run_benchmark,
)
from evaluate_pipeline import (  # noqa: E402
    CITATION_NEG_THRESHOLD,
    CITATION_POS_THRESHOLD,
    evaluate_ragas_faithfulness,
)

REQUIRED_CATEGORIES = ("no_grounding", "fake_url", "cross_school", "outdated", "secondhand")


def _flip_to_allow(case):
    """把一条「该拦」样本的期望翻转为「该放行」（阴性对照用）。"""
    c = dict(case)
    if c["kind"] == "citation":
        c["expect_accept"] = True
    elif c["kind"] == "evidence":
        c["expect_status"] = "VERIFIED"
        c.pop("expect_level", None)
        c.pop("expect_confidence_max", None)
    elif c["kind"] == "conflict":
        c["expect_status"] = "VERIFIED"
        c.pop("expect_no_conflict", None)
    elif c["kind"] == "extract":
        c["expect_official"] = True
    elif c["kind"] == "source_label":
        c["expect_label_contains"] = "官方"
        c["expect_label_not_contains"] = "外部来源"
    return c


def _flip_to_block(case):
    """把一条「该放行」样本的期望翻转为「该拦」（阴性对照用，方向相反）。"""
    c = dict(case)
    if c["kind"] == "citation":
        c["expect_accept"] = False
    elif c["kind"] == "evidence":
        c["expect_status"] = "UNVERIFIED"
    elif c["kind"] == "conflict":
        c["expect_status"] = "CONFLICT"
    elif c["kind"] == "extract":
        c["expect_official"] = False
    elif c["kind"] == "source_label":
        c["expect_label_contains"] = "外部来源"
        c["expect_label_not_contains"] = "官方"
    return c


def _write_cases(path, cases):
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
                    encoding="utf-8", newline="\n")
    return path


# ─────────────────── 第 1 层：评测集的数据契约 ───────────────────

def test_benchmark_file_exists_and_meets_scale():
    """评测集必须存在且 ≥100 条（规划口径：100+ 起步），且无无效行。"""
    assert BENCH_FILE.exists(), f"评测集缺失: {BENCH_FILE}"
    cases, invalid = load_cases(BENCH_FILE)
    assert not invalid, f"评测集含无效行: {invalid[:5]}"
    assert len(cases) >= 100, f"评测集规模不足 100 条: {len(cases)}"


def test_benchmark_covers_five_categories():
    """规划口径的五类必须全部覆盖：无据引用/伪造 URL/过期数据/跨校混淆/二手源冒充。"""
    cases, _ = load_cases(BENCH_FILE)
    cats = {c.get("category") for c in cases}
    missing = [c for c in REQUIRED_CATEGORIES if c not in cats]
    assert not missing, f"缺失规划要求的类别: {missing}（实际: {sorted(cats)}）"


def test_benchmark_ids_are_unique():
    """id 必须唯一（否则失败明细无法定位到具体样本）。"""
    cases, _ = load_cases(BENCH_FILE)
    ids = [c["id"] for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"id 重复: {dupes}"


def test_benchmark_required_fields():
    """每条必须含 id/kind/category/expect_block/note，且 kind 在分发表内。"""
    cases, _ = load_cases(BENCH_FILE)
    for c in cases:
        for key in ("id", "kind", "category", "expect_block", "note"):
            assert key in c, f"{c.get('id', '?')} 缺少字段 {key}"
        assert c["kind"] in KINDS, f"{c['id']} 的 kind 非法: {c['kind']}"
        assert isinstance(c["expect_block"], bool), f"{c['id']} 的 expect_block 非布尔"
        assert str(c["note"]).strip(), f"{c['id']} 的 note 为空"


def test_benchmark_block_ratio():
    """「该拦」样本占比 ≥50% —— 反幻觉评测应以拦截为主（本集实测 65%）。"""
    cases, _ = load_cases(BENCH_FILE)
    blocks = [c for c in cases if c["expect_block"]]
    ratio = len(blocks) / len(cases)
    assert ratio >= 0.50, f"该拦样本占比 {ratio:.1%} < 50%"


def test_benchmark_expect_block_consistency():
    """expect_block 必须与各 kind 的期望字段自洽（防止标注漂移）。"""
    cases, _ = load_cases(BENCH_FILE)
    for c in cases:
        if c["kind"] == "citation":
            expect_block = not bool(c["expect_accept"])
        elif c["kind"] in ("evidence", "conflict"):
            expect_block = c.get("expect_status") != "VERIFIED"
            if c.get("expect_empty") or c.get("expect_no_conflict"):
                expect_block = False
        elif c["kind"] == "extract":
            expect_block = not bool(c["expect_official"])
        else:  # source_label
            expect_block = c.get("expect_label_contains") == "外部来源"
        assert c["expect_block"] is expect_block, (
            f"{c['id']}: expect_block={c['expect_block']} 与期望字段不自洽"
            f"（应为 {expect_block}）")


def test_benchmark_has_no_real_identity():
    """评测集不得含学员真实报考身份（tests/ 中性化约定，导出脱敏会改写它们）。"""
    import privacy_policy as pp

    text = BENCH_FILE.read_text(encoding="utf-8")
    rules = pp.build_py_substitutions(ROOT)
    assert pp.sanitize_text(text, rules) == text, (
        "评测集含真实身份词（会被导出脱敏改写，公开副本断言将自毁）")


# ─────────────────── 第 2 层：judge 的分发契约 ───────────────────

def test_judge_unknown_kind_fails_not_crashes():
    """未知 kind 记失败而非崩溃（数据损坏由 invalid 通道 / 契约测试暴露）。"""
    judge = build_judge()
    passed, detail = judge({"id": "x", "kind": "no-such-kind"})
    assert passed is False and "未知 kind" in detail


def test_judge_handles_every_kind_in_benchmark():
    """评测集里出现的每个 kind，judge 都必须能给出判定（不得靠"未知 kind"兜底）。"""
    cases, _ = load_cases(BENCH_FILE)
    judge = build_judge()
    kinds_seen = set()
    for c in cases:
        passed, detail = judge(c)
        kinds_seen.add(c["kind"])
        assert "未知 kind" not in detail, f"{c['id']}: judge 未分发"
        assert isinstance(passed, bool)
    assert kinds_seen == set(KINDS), f"评测集未触达全部 kind: {kinds_seen}"


# ─────────────────── 第 3 层：端到端双指标 ───────────────────

def test_end_to_end_meets_dual_threshold():
    """端到端：拦截侧 100%、放行侧 ≥98%，退出码 0（当前实测 109/109）。"""
    res = evaluate_ragas_faithfulness()
    assert res["evaluable"] is True, res.get("reason")
    assert res["exit_code"] == EXIT_OK, f"失败明细: {res['negative']['failures'][:5]}"
    neg, pos = res["negative"], res["positive"]
    assert neg["pass_rate"] >= CITATION_NEG_THRESHOLD, \
        f"拦截侧漏放 {neg['failed']} 条（要求 100%）: {neg['failures'][:5]}"
    assert pos["pass_rate"] >= CITATION_POS_THRESHOLD, \
        f"放行侧误拦 {pos['failed']} 条: {pos['failures'][:5]}"


def test_missing_benchmark_file_is_not_evaluable(tmp_path):
    """评测集缺失 → 明确不可评测（退出码 2 语义），绝不编造指标。"""
    res = evaluate_ragas_faithfulness(cases_path=tmp_path / "absent.jsonl")
    assert res["evaluable"] is False
    assert "不存在" in res["reason"]


# ─────────────────── 第 4 层：阴性验证与阴性对照 ───────────────────

@pytest.mark.parametrize("answer", [
    # 新注入的编造引用（不在评测集里）必须被拦
    {"answer": "拟招 8888 人", "confidence": "high",
     "citations": [{"cited_text": "拟招生 8888 人", "document_index": 0}]},
    # 新注入的跨文档拼接
    {"answer": "x", "confidence": "high",
     "citations": [{"cited_text": "拟招生 60 人。复试比例一般不低于 120%",
                    "document_index": 0}]},
])
def test_injected_fabricated_citation_is_blocked(answer):
    """[规划验收] 阴性验证：伪造引用必须 100% 拦截（逐条断言）。"""
    from intelligence.citation_engine import UngroundedCitation, verify_citations

    docs = ["示例院校A2027年硕士研究生招生简章：拟招生 60 人。"]
    with pytest.raises(UngroundedCitation):
        verify_citations(answer, docs)


def test_flipped_block_expectation_fails_gate(tmp_path):
    """[阴性对照] 翻转 1 条「该拦」样本的期望 → 拦截侧跌破 100% → 必须判红。

    拦截侧阈值是 **1.0**（不是 98%）：漏放一条编造引用即红线失守 —— 单条翻转
    就必须让门禁变红，这正是它与放行侧不对称的体现。
    """
    cases, _ = load_cases(BENCH_FILE)
    target = next(c for c in cases if c["expect_block"])
    flipped = [_flip_to_allow(c) if c["id"] == target["id"] else c for c in cases]
    path = _write_cases(tmp_path / "flipped_block.jsonl", flipped)
    res = evaluate_ragas_faithfulness(cases_path=path)
    assert res["negative"]["failed"] == 1, res["negative"]["failures"]
    assert res["exit_code"] == EXIT_FAIL, "拦截侧出现漏放后门禁未判红"


def test_flipped_allow_expectation_fails_gate(tmp_path):
    """[阴性对照] 把 1 条「该放行」样本的期望反转为「该拦」→ 放行侧必须判红。"""
    cases, _ = load_cases(BENCH_FILE)
    target = next(c for c in cases if not c["expect_block"] and c["kind"] == "citation")
    flipped = [_flip_to_block(c) if c["id"] == target["id"] else c for c in cases]
    path = _write_cases(tmp_path / "flipped_allow.jsonl", flipped)
    res = evaluate_ragas_faithfulness(cases_path=path)
    assert res["positive"]["failed"] == 1, res["positive"]["failures"]
    assert res["exit_code"] == EXIT_FAIL, "放行侧出现误判后门禁未判红"


def test_two_sides_are_independent(tmp_path):
    """两侧指标相互独立：翻转放行侧样本时，拦截侧必须仍保持 100%。"""
    cases, _ = load_cases(BENCH_FILE)
    target = next(c for c in cases if not c["expect_block"] and c["kind"] == "citation")
    flipped = [_flip_to_block(c) if c["id"] == target["id"] else c for c in cases]
    path = _write_cases(tmp_path / "independent.jsonl", flipped)
    res = evaluate_ragas_faithfulness(cases_path=path)
    assert res["negative"]["failed"] == 0, res["negative"]["failures"]


def test_broken_data_returns_insufficient(tmp_path):
    """数据损坏（半截行）→ 退出码 2，不得伪装成 0/1。"""
    good, _ = load_cases(BENCH_FILE)
    text = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in good[:10])
    text += '{"id": "broken", "kind": "citat\n'   # 半截 JSON
    path = tmp_path / "broken.jsonl"
    path.write_text(text, encoding="utf-8", newline="\n")
    res = evaluate_ragas_faithfulness(cases_path=path)
    assert res["exit_code"] == EXIT_INSUFFICIENT
    assert res["evaluable"] is False


# ─────────────────── 第 5 层：C2 实测缺陷回归 ───────────────────

@pytest.mark.parametrize("answer", [
    {"answer": "x", "confidence": "high",
     "citations": [{"cited_text": "拟招生", "document_index": "abc"}]},
    {"answer": "x", "confidence": "high",
     "citations": [{"cited_text": "拟招生", "document_index": [0]}]},
    {"answer": "x", "confidence": None, "citations": []},
    {"answer": "x", "confidence": 5, "citations": []},
])
def test_invalid_field_types_are_grounded_citation_errors(answer):
    """[C2 实测缺陷回归] 非法字段载体必须抛 ``UngroundedCitation``。

    修复前：pydantic 在**构造阶段**抛 ``ValidationError``（不在契约内），
    会以未捕获异常逃出反幻觉闸门；无 pydantic 路径行为还不同（静默回落 low）。
    修复后：两条路径统一拦截。
    """
    from intelligence.citation_engine import UngroundedCitation, verify_citations

    with pytest.raises(UngroundedCitation):
        verify_citations(answer, ["示例院校A2027年硕士研究生招生简章：拟招生 60 人。"])


def test_valid_inputs_still_pass_after_contract_fix():
    """回归：修复非法载体拦截后，合法路径不受影响。"""
    from intelligence.citation_engine import verify_citations

    docs = ["示例院校A2027年硕士研究生招生简章：拟招生 60 人。"]
    verify_citations({"answer": "x", "confidence": "high",
                      "citations": [{"cited_text": "拟招生 60 人", "document_index": 0}]}, docs)
    verify_citations({"answer": "推测", "confidence": "low", "citations": []}, docs)
    verify_citations({"answer": "推测", "citations": []}, docs)  # 缺键 → 默认 low


def test_compat_path_without_pydantic_is_consistent():
    """无 pydantic 环境（内置兼容模型）必须与 pydantic 路径**行为一致**。

    用子进程隔离：``sys.modules['pydantic'] = None`` 让 ``import pydantic`` 抛
    ImportError，从而走内置模型分支。历史缺陷下两条路径不一致（pydantic 路径抛
    ValidationError、内置路径静默回落 low），本用例会红。
    """
    probe = (
        "import sys\n"
        "sys.modules['pydantic'] = None\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from intelligence.citation_engine import (\n"
        "    UngroundedCitation, has_pydantic, verify_citations)\n"
        "assert has_pydantic() is False, '未能进入无 pydantic 分支'\n"
        "D = ['示例院校A2027年硕士研究生招生简章：拟招生 60 人。']\n"
        "bad = [\n"
        "    {'answer': 'x', 'confidence': 'high',\n"
        "     'citations': [{'cited_text': '拟招生', 'document_index': 'abc'}]},\n"
        "    {'answer': 'x', 'confidence': None, 'citations': []},\n"
        "    {'answer': 'x', 'confidence': 5, 'citations': []},\n"
        "]\n"
        "for ans in bad:\n"
        "    try:\n"
        "        verify_citations(ans, D)\n"
        "    except UngroundedCitation:\n"
        "        pass\n"
        "    else:\n"
        "        raise SystemExit('未拦截: %r' % (ans,))\n"
        "verify_citations({'answer': 'x', 'confidence': 'high', 'citations':\n"
        "    [{'cited_text': '拟招生 60 人', 'document_index': 0}]}, D)\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe, str(ROOT / "tools")],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert r.returncode == 0, f"无 pydantic 路径不一致: {r.stdout} {r.stderr}"
    assert "OK" in r.stdout
