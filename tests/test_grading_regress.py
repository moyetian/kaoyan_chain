# -*- coding: utf-8 -*-
"""ky grade-regress（判卷 prompt 版本回归对比）测试。

范围（全部离线：不联网、不调 LLM、不写工作区真实文件）：
  1. 命令注册与 --help（dispatch 可查、可跑；write=False，报告先行）；
  2. ``compare_prompt_versions`` 三指标差值 / verdict（pass / warn / fail / 边界）；
  3. 阈值覆盖参数与快照版本校验（``check_prompt_version_match`` 的 fail-closed 口径）；
  4. 快照版本不匹配 / 评测不可行 → 明确报错（``GradingRegressError``；CLI 退出码 2，
     绝不静默给出错误指标）；
  5. CLI 端到端（mock 评测层）：对比表 / --json / 门禁开关（``regress_gate``）。

判定阈值只影响 grade-regress 的显示与退出码（**报告先行**），本文件不锁定任何
「正式达标线」——锁定的只是「口径与机制正确」。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.benchmarks import grading_judge as gj  # noqa: E402
from tools.cli import dispatch  # noqa: E402
from tools.cli.commands import grading as grading_cmd  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 工具：构造固定指标 / mock 评测层（不真调 LLM）
# ════════════════════════════════════════════════════════════════

def _metrics(hit_jaccard=0.85, total_mae=0.60, mistake_agreement=0.60, *, samples=30):
    return {"samples": samples, "hit_jaccard": hit_jaccard, "hit_recall": hit_jaccard,
            "hit_precision": hit_jaccard, "total_mae": total_mae,
            "mistake_agreement": mistake_agreement,
            "degraded_rate": 0.0, "arbitrated_rate": 0.9}


def _eval_result(metrics, *, evaluable=True, exit_code=0, reason=""):
    return {"evaluable": evaluable, "samples": metrics.get("samples", 0),
            "scored_samples": metrics.get("samples", 0), "metrics": metrics,
            "by_quality": {}, "by_subject": {}, "passed": exit_code == 0,
            "exit_code": exit_code, "alert_threshold": 0.6, "report": {},
            "details": [], "invalid": [], "reason": reason, "disclaimer": ""}


def _patch_eval(monkeypatch, by_version):
    """按 ``prompt_version`` 返回固定评测结果的假评测层。"""
    def fake_eval(cases_path=None, snapshots_path=None, *, min_samples=20,
                  prompt_version=None):
        key = str(prompt_version or "")
        if key not in by_version:
            raise AssertionError(f"未预期的 prompt_version: {key!r}")
        return by_version[key]

    monkeypatch.setattr(gj, "evaluate_grading_pilot", fake_eval)


# ════════════════════════════════════════════════════════════════
# 第 1 层：命令注册与 --help
# ════════════════════════════════════════════════════════════════

def test_command_registered():
    dispatch._init_all_commands()
    cmd = dispatch.get_command("grade-regress")
    assert cmd is not None, "grade-regress 未注册（dispatch 查不到）"
    assert cmd.name == "grade-regress"
    assert cmd.write is False, "评测命令不写工作区（报告先行，不阻断）"
    assert "--prompt" in cmd.usage and "--baseline" in cmd.usage
    assert "回归对比" in cmd.desc


def test_command_help_runs(capsys):
    with pytest.raises(SystemExit) as ei:
        dispatch.main(["grade-regress", "--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "grade-regress" in out


# ════════════════════════════════════════════════════════════════
# 第 2 层：compare_prompt_versions 判定（mock 评测层）
# ════════════════════════════════════════════════════════════════

def test_compare_pass_within_thresholds(monkeypatch):
    _patch_eval(monkeypatch, {
        "v1": _eval_result(_metrics(0.85, 0.60, 0.60)),
        "v2": _eval_result(_metrics(0.84, 0.65, 0.58)),
    })
    res = gj.compare_prompt_versions("v2", "v1")
    assert res["verdict"] == "pass"
    assert res["deltas"]["hit_jaccard"] == pytest.approx(-0.01, abs=1e-6)
    assert res["deltas"]["mae"] == pytest.approx(0.05, abs=1e-6)
    assert res["deltas"]["mistake_type_rate"] == pytest.approx(-0.02, abs=1e-6)
    assert res["candidate"]["prompt_version"] == "v2"
    assert res["baseline"]["prompt_version"] == "v1"
    assert res["samples"] == 30 and res["live"] is False


@pytest.mark.parametrize("cand_metrics,keyword", [
    (_metrics(0.85, 1.20, 0.60), "MAE"),      # MAE 上升 0.6 > 0.5
    (_metrics(0.75, 0.60, 0.60), "命中"),      # Jaccard 下降 0.10 > 0.05
    (_metrics(0.85, 0.60, 0.45), "错因"),      # 错因一致率下降 0.15 > 0.10
])
def test_compare_fail_on_each_metric(monkeypatch, cand_metrics, keyword):
    _patch_eval(monkeypatch, {
        "v1": _eval_result(_metrics(0.85, 0.60, 0.60)),
        "v2": _eval_result(cand_metrics),
    })
    res = gj.compare_prompt_versions("v2", "v1")
    assert res["verdict"] == "fail"
    assert any(keyword in r for r in res["reasons"]), res["reasons"]


def test_compare_threshold_boundary_is_warn_not_fail(monkeypatch):
    """恰好达到阈值（不超）→ 不判 fail（warn 提示），边界口径钉死。"""
    _patch_eval(monkeypatch, {
        "v1": _eval_result(_metrics(0.85, 0.50, 0.60)),
        "v2": _eval_result(_metrics(0.80, 1.00, 0.50)),
    })
    res = gj.compare_prompt_versions("v2", "v1")
    assert res["verdict"] == "warn"
    assert res["checks"]["mae"] == "warn"
    assert res["checks"]["hit_jaccard"] == "warn"
    assert res["checks"]["mistake_type_rate"] == "warn"


def test_compare_improvement_is_pass(monkeypatch):
    _patch_eval(monkeypatch, {
        "v1": _eval_result(_metrics(0.80, 1.00, 0.50)),
        "v2": _eval_result(_metrics(0.90, 0.50, 0.70)),
    })
    res = gj.compare_prompt_versions("v2", "v1")
    assert res["verdict"] == "pass"
    assert res["deltas"]["mae"] < 0 and res["deltas"]["hit_jaccard"] > 0


def test_compare_threshold_override(monkeypatch):
    """阈值可通过参数覆盖（默认 0.5 判 fail 的 MAE 上升，放宽到 1.0 后仅 warn）。"""
    _patch_eval(monkeypatch, {
        "v1": _eval_result(_metrics(0.85, 0.60, 0.60)),
        "v2": _eval_result(_metrics(0.85, 1.20, 0.60)),
    })
    assert gj.compare_prompt_versions("v2", "v1")["verdict"] == "fail"
    res = gj.compare_prompt_versions("v2", "v1", thresholds={"mae_delta": 1.0})
    assert res["verdict"] == "warn"
    assert res["thresholds"]["mae_delta"] == 1.0


# ════════════════════════════════════════════════════════════════
# 第 3 层：快照版本校验与「测不了」的明确报错（不静默）
# ════════════════════════════════════════════════════════════════

def test_check_prompt_version_match_fail_closed():
    snaps = {"a": {"prompt_version": "v1"}, "b": {"prompt_version": "v1"}}
    assert gj.check_prompt_version_match(snaps, "v1") == []
    assert gj.check_prompt_version_match(snaps, "v2") == [("a", "v1"), ("b", "v1")]
    # 记录版本的快照 + 请求 latest（空）→ 不匹配（无法确认 latest 就是该版本）
    assert gj.check_prompt_version_match(snaps, None) == [("a", "v1"), ("b", "v1")]
    # 未记录版本的历史快照：请求 latest 匹配；请求具体版本不匹配（fail-closed）
    legacy = {"a": {"calls": {}}}
    assert gj.check_prompt_version_match(legacy, None) == []
    assert gj.check_prompt_version_match(legacy, "") == []
    assert gj.check_prompt_version_match(legacy, "v1") == [("a", "")]


def test_compare_replay_mismatch_raises_with_actionable_message(monkeypatch):
    mismatch_res = _eval_result(_metrics(samples=0), evaluable=False, exit_code=2,
                                reason="prompt 版本与冻结快照不匹配")
    mismatch_res["prompt_version_mismatch"] = [{"id": "c-01", "snapshot_version": ""}]
    monkeypatch.setattr(gj, "evaluate_grading_pilot", lambda *a, **kw: mismatch_res)
    with pytest.raises(gj.GradingRegressError) as ei:
        gj.compare_prompt_versions("v2", "v1")
    msg = str(ei.value)
    assert "不匹配" in msg and "无法回放" in msg and "--live" in msg


def test_compare_not_evaluable_raises(monkeypatch):
    """invalid / 样本不足 → 明确报错（不静默给出指标）。"""
    bad = _eval_result(_metrics(samples=0), evaluable=False, exit_code=2,
                       reason="存在 3 条无效样本（数据损坏 / 快照缺失）")
    monkeypatch.setattr(gj, "evaluate_grading_pilot", lambda *a, **kw: bad)
    with pytest.raises(gj.GradingRegressError) as ei:
        gj.compare_prompt_versions("v2", "v1")
    assert "不可行" in str(ei.value) and "不静默" in str(ei.value)


def test_evaluate_grading_pilot_version_mismatch_end_to_end(tmp_path):
    """真实评测函数：快照记录版本 vs 请求版本不一致 → 不可评测 + mismatch 明细。"""
    cases = [json.loads(line) for line in
             gj.CASES_FILE.read_text(encoding="utf-8").splitlines() if line.strip()][:3]
    cp = tmp_path / "cases.jsonl"
    cp.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
                  encoding="utf-8")
    sp = tmp_path / "snaps.jsonl"
    sp.write_text("\n".join(
        json.dumps({"id": c["id"], "prompt_version": "v1", "calls": {}},
                   ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")

    res = gj.evaluate_grading_pilot(cases_path=cp, snapshots_path=sp,
                                    min_samples=1, prompt_version="v2")
    assert res["evaluable"] is False and res["exit_code"] == 2
    assert "不匹配" in res["reason"]
    assert len(res["prompt_version_mismatch"]) == len(cases)

    # 请求 latest 而快照记录了 v1 → 同样不匹配（fail-closed）
    res2 = gj.evaluate_grading_pilot(cases_path=cp, snapshots_path=sp, min_samples=1)
    assert res2["evaluable"] is False
    assert res2["requested_prompt_version"] == ""

    # 请求 v1 → 版本校验通过（无 mismatch 字段；空 calls 快照另有 miss 语义）
    res3 = gj.evaluate_grading_pilot(cases_path=cp, snapshots_path=sp,
                                     min_samples=1, prompt_version="v1")
    assert not res3.get("prompt_version_mismatch")


def _perfect_calls(case):
    """按预标构造「完美链路」响应（与 test_c4 同口径）。"""
    scores = {k["id"]: k["score"] for k in case["key_points"]}
    levels = {h["id"]: h["level"] for h in case["expected"]["hit_points"]}
    hits = [{"id": i, "hit": levels.get(i, "none"), "evidence": "e"} for i in sorted(scores)]
    resp = json.dumps({"total": case["expected"]["total"], "rubric_hits": hits,
                       "mistake_type": case["expected"]["mistake_type"],
                       "confidence": 0.9, "reason": "完美回放"}, ensure_ascii=False)
    calls = {f"review:{n}": {"ok": True, "response": resp} for n in "ABC"}
    calls["judge"] = {"ok": True, "response": resp}
    return calls


def test_compare_replay_positive_path_with_matching_version(tmp_path):
    """真实评测链路端到端：带匹配版本记录的完美快照 → 自比较 deltas 全 0 / pass。"""
    all_cases = [json.loads(line) for line in
                 gj.CASES_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [c for c in all_cases if c["quality"] != "blank"][:3]
    cp = tmp_path / "cases.jsonl"
    cp.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
                  encoding="utf-8")
    sp = tmp_path / "snaps.jsonl"
    sp.write_text("\n".join(
        json.dumps({"id": c["id"], "prompt_version": "v2", "calls": _perfect_calls(c)},
                   ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")

    res = gj.compare_prompt_versions("v2", "v2", cases_path=cp,
                                     snapshots_path=sp, min_samples=1)
    assert res["verdict"] == "pass"
    assert res["deltas"] == {"hit_jaccard": 0.0, "mae": 0.0, "mistake_type_rate": 0.0}
    assert res["candidate"]["exit_code"] == 0
    assert res["samples"] == 3


# ════════════════════════════════════════════════════════════════
# 第 4 层：CLI 端到端（mock 评测层，不真调 LLM）
# ════════════════════════════════════════════════════════════════

def _fake_compare_result(verdict="pass", reasons=None):
    def _side(version, hit_jaccard, total_mae, mistake_agreement):
        return {"prompt_version": version, "hit_jaccard": hit_jaccard,
                "hit_recall": hit_jaccard, "hit_precision": hit_jaccard,
                "total_mae": total_mae, "mistake_agreement": mistake_agreement,
                "samples": 30, "exit_code": 0, "evaluable": True,
                "degraded_rate": 0.0, "arbitrated_rate": 0.9}
    return {
        "candidate": _side("v2", 0.84, 0.65, 0.58),
        "baseline": _side("v1", 0.85, 0.60, 0.60),
        "deltas": {"hit_jaccard": -0.01, "mae": 0.05, "mistake_type_rate": -0.02},
        "verdict": verdict,
        "checks": {"hit_jaccard": "ok", "mae": "ok", "mistake_type_rate": "ok"},
        "reasons": list(reasons or []),
        "thresholds": dict(gj.REGRESS_THRESHOLDS),
        "live": False,
        "samples": 30,
        "cases_path": str(gj.CASES_FILE),
        "disclaimer": "AI 预标（用户抽检）下的「与预标一致率」；报告先行",
    }


def _patch_options(monkeypatch, *, gate=False, thresholds=None):
    monkeypatch.setattr(grading_cmd, "_load_regress_options",
                        lambda: {"regress_gate": gate,
                                 "thresholds": dict(thresholds or {})})


def test_cli_requires_prompt_and_baseline(capsys):
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(["grade-regress"])
    assert ei.value.code == 1
    assert "用法" in capsys.readouterr().out


def test_cli_unknown_option_rejected(capsys):
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(["grade-regress", "--bogus"])
    assert ei.value.code == 1
    assert "未知参数" in capsys.readouterr().out


def test_cli_prints_comparison_table(monkeypatch, capsys):
    _patch_options(monkeypatch)
    monkeypatch.setattr(gj, "compare_prompt_versions",
                        lambda *a, **kw: _fake_compare_result())
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(
            ["grade-regress", "--prompt", "v2", "--baseline", "v1"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "回归对比" in out and "v2" in out and "v1" in out
    assert "pass" in out and "门禁" in out


def test_cli_json_output(monkeypatch, capsys):
    _patch_options(monkeypatch)
    monkeypatch.setattr(gj, "compare_prompt_versions",
                        lambda *a, **kw: _fake_compare_result())
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(
            ["grade-regress", "--prompt=v2", "--baseline=v1", "--json"])
    assert ei.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "pass"
    assert payload["deltas"]["mae"] == 0.05


def test_cli_gate_off_reports_fail_without_blocking(monkeypatch, capsys):
    """报告先行：门禁关闭时 verdict=fail 也只报告，退出码 0。"""
    _patch_options(monkeypatch, gate=False)
    monkeypatch.setattr(gj, "compare_prompt_versions",
                        lambda *a, **kw: _fake_compare_result("fail", ["总分 MAE 上升 +0.60"]))
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(
            ["grade-regress", "--prompt", "v2", "--baseline", "v1"])
    assert ei.value.code == 0
    assert "fail" in capsys.readouterr().out


def test_cli_gate_on_blocks_fail(monkeypatch, capsys):
    """门禁开启且 verdict=fail → 退出码 2。"""
    _patch_options(monkeypatch, gate=True)
    monkeypatch.setattr(gj, "compare_prompt_versions",
                        lambda *a, **kw: _fake_compare_result("fail", ["总分 MAE 上升 +0.60"]))
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(
            ["grade-regress", "--prompt", "v2", "--baseline", "v1"])
    assert ei.value.code == 2
    assert "门禁" in capsys.readouterr().out


def test_cli_mismatch_is_loud_and_exit_2(monkeypatch, capsys):
    """快照不匹配 → 明确报错 + 退出码 2（不得静默给出错误指标）。"""
    _patch_options(monkeypatch)

    def _raise(*a, **kw):
        raise gj.GradingRegressError(
            "prompt 版本 v2 与冻结快照不匹配（快照记录版本: 未记录；"
            "快照响应绑定采集时的 prompt 文本），无法回放；"
            "请加 --live 真实跑（计费，≈21 分钟/30 份）")

    monkeypatch.setattr(gj, "compare_prompt_versions", _raise)
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(
            ["grade-regress", "--prompt", "v2", "--baseline", "v1"])
    assert ei.value.code == 2
    out = capsys.readouterr().out
    assert "无法对比" in out and "不匹配" in out and "--live" in out


def test_cli_mismatch_json_error(monkeypatch, capsys):
    _patch_options(monkeypatch)

    def _raise(*a, **kw):
        raise gj.GradingRegressError("快照不匹配；请加 --live")

    monkeypatch.setattr(gj, "compare_prompt_versions", _raise)
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(
            ["grade-regress", "--prompt", "v2", "--baseline", "v1", "--json"])
    assert ei.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] is None and "不匹配" in payload["error"]


def test_cli_live_prints_billing_warning(monkeypatch, capsys):
    """--live 必须打印计费警告（真实调用 LLM），并传入 llm_config。"""
    _patch_options(monkeypatch)
    seen = {}

    def _fake_compare(cand, base, **kw):
        seen.update(kw)
        return _fake_compare_result()

    monkeypatch.setattr(grading_cmd, "_load_llm_config",
                        lambda: {"base_url": "http://x/v1", "api_key": "k", "model": "m"})
    monkeypatch.setattr(gj, "compare_prompt_versions", _fake_compare)
    with pytest.raises(SystemExit) as ei:
        grading_cmd._cmd_grade_regress(
            ["grade-regress", "--prompt", "v2", "--baseline", "v1", "--live",
             "--cases", "x/cases.jsonl"])
    assert ei.value.code == 0
    assert "计费" in capsys.readouterr().out
    assert seen.get("live") is True
    assert seen.get("llm_config", {}).get("model") == "m"
    assert str(seen.get("cases_path")).endswith("cases.jsonl")
