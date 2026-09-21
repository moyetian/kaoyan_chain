# -*- coding: utf-8 -*-
"""G2 回归：初试日期不得被当身份改写；AGENTS.md 的倒计时必须按 exam_date 实时重算。

**事故（实测）**：公开副本里 ``AGENTS.md`` 写「初试日期：2027-12-26（倒计时约 90 天）」，
而 ``ky status`` 的「初试首日」是实时算出的 ``2026-12-19`` —— 同一屏出现两个互相
矛盾的初试日期。两个独立根因：

1. ``privacy_policy.STATIC_IDENTITY_SUBSTITUTIONS`` 里有一条
   ``(r"2026-12-19", "2027-12-26")``，把初试日期当身份改写了（**已删除**；
   日期不是隐私，且 .py 侧本就豁免、.md 侧未豁免，口径本就不一致）。
2. ``study_planner.apply_study_plan()`` 回写 AGENTS.md 时，倒计时取的是
   ``plan["days_left"]`` 这个**存储快照**，而不是按 ``exam_date`` 现算 ——
   于是 exam_date 一改，日期与天数就对不上（**已改为实时重算**）。

阴性对照：
  * 把日期规则加回 ``STATIC_IDENTITY_SUBSTITUTIONS`` →
    ``test_privacy_identity_rules.test_exam_date_is_never_rewritten_as_identity`` 变红；
  * 把 ``apply_study_plan`` 的倒计时改回 ``plan.get("days_left", 0)`` →
    本文件的两个用例变红。
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest

import tools.study_planner as sp

AGENTS_TEMPLATE = (
    "# 总控协议\n"
    "- **目标院校**：`目标院校`\n"
    "- **报考专业**：`报考专业`\n"
    "- **初试日期**：`2026-12-19` (倒计时约 999 天)\n"
    "- **当前备考阶段**：`强化题型攻坚阶段`\n"
    "- **当前激活辅导风格**：`严格把关·保姆提分型 (Strict & Disciplined)`\n"
    "\n## 1. 核心教学原则\n"
)


def _isolate(monkeypatch, tmp_path):
    """把 apply_study_plan 的全部写盘目标隔离到 tmp_path（不碰真实工作区）。"""
    import tools.syllabus_manager as sm

    agents = tmp_path / "AGENTS.md"
    agents.write_text(AGENTS_TEMPLATE, encoding="utf-8")
    cfg = tmp_path / "ky_config.json"
    cfg.write_text(json.dumps({"study_plan": {}}, ensure_ascii=False),
                   encoding="utf-8")

    monkeypatch.setattr(sp, "ROOT", tmp_path)
    monkeypatch.setattr(sp, "CONFIG_FILE", cfg)
    monkeypatch.setattr(sp, "run_ai_study_plan_generation", lambda *a, **k: "策略")
    monkeypatch.setattr(sp, "update_subject_agents", lambda *a, **k: None)
    monkeypatch.setattr(sp, "generate_plan_and_today_files", lambda *a, **k: None)
    monkeypatch.setattr(sm, "apply_syllabus_selection", lambda **k: None)
    return agents


def _exam_date_line(agents) -> str:
    for line in agents.read_text(encoding="utf-8").split("\n"):
        if line.startswith("- **初试日期**"):
            return line
    raise AssertionError("AGENTS.md 里没有初试日期行")


def test_countdown_is_recomputed_from_exam_date(monkeypatch, tmp_path):
    """存储的 days_left 与 exam_date 矛盾时，必须以 exam_date 为准重算。"""
    exam = (date.today() + timedelta(days=100)).isoformat()
    agents = _isolate(monkeypatch, tmp_path)

    sp.apply_study_plan({"exam_date": exam, "days_left": 999,
                         "math_key": "none", "math_name": "不考数学",
                         "style_name": "严格把关型"}, interactive=False)

    line = _exam_date_line(agents)
    assert f"`{exam}`" in line, line
    assert "999" not in line, f"倒计时取了存储快照而非实时重算: {line}"
    m = re.search(r"倒计时约 (\d+) 天", line)
    assert m, line
    assert int(m.group(1)) == 100, line


def test_countdown_tracks_exam_date_change(monkeypatch, tmp_path):
    """改 exam_date 后倒计时必须随之变化（同一个存储值不得复用）。"""
    agents = _isolate(monkeypatch, tmp_path)

    near = (date.today() + timedelta(days=30)).isoformat()
    sp.apply_study_plan({"exam_date": near, "days_left": 999,
                         "math_key": "none", "style_name": "严格把关型"},
                        interactive=False)
    assert "倒计时约 30 天" in _exam_date_line(agents)

    far = (date.today() + timedelta(days=200)).isoformat()
    sp.apply_study_plan({"exam_date": far, "days_left": 999,
                         "math_key": "none", "style_name": "严格把关型"},
                        interactive=False)
    assert "倒计时约 200 天" in _exam_date_line(agents)


def test_bad_exam_date_falls_back_to_stored_value(monkeypatch, tmp_path):
    """exam_date 非法时不得抛异常，退化为存储值（防御性兜底）。"""
    agents = _isolate(monkeypatch, tmp_path)
    sp.apply_study_plan({"exam_date": "不是日期", "days_left": 77,
                         "math_key": "none", "style_name": "严格把关型"},
                        interactive=False)
    assert "倒计时约 77 天" in _exam_date_line(agents)


def test_countdown_is_clamped_to_zero_on_or_after_exam_date(monkeypatch, tmp_path):
    """[回归] 考试当天/过期后倒计时必须钳到 0，不得写出「倒计时约 -3 天」。

    修复前 ``apply_study_plan`` 的实时重算没有 ``max(0, …)``：``(exam_date -
    today).days`` 为负直接写进 AGENTS.md，与 ``calculate_countdown`` /
    ``exam_calendar.countdown_days`` 的钳零口径不一致。

    阴性对照：去掉那处 ``max(0, …)``，本用例两条断言均变红。
    """
    agents = _isolate(monkeypatch, tmp_path)

    past = (date.today() - timedelta(days=3)).isoformat()
    sp.apply_study_plan({"exam_date": past, "days_left": 999,
                         "math_key": "none", "style_name": "严格把关型"},
                        interactive=False)
    line = _exam_date_line(agents)
    assert "倒计时约 0 天" in line, line
    assert "-3" not in line, line

    # 考试当天同样为 0（而非 0 天以外的负值/异常）
    today = date.today().isoformat()
    sp.apply_study_plan({"exam_date": today, "days_left": 999,
                         "math_key": "none", "style_name": "严格把关型"},
                        interactive=False)
    assert "倒计时约 0 天" in _exam_date_line(agents)


def test_stored_negative_days_left_is_clamped(monkeypatch, tmp_path):
    """[回归] 非法日期回退到存储快照时，负数同样必须钳到 0。"""
    agents = _isolate(monkeypatch, tmp_path)
    sp.apply_study_plan({"exam_date": "不是日期", "days_left": -5,
                         "math_key": "none", "style_name": "严格把关型"},
                        interactive=False)
    line = _exam_date_line(agents)
    assert "倒计时约 0 天" in line, line
    assert "-5" not in line, line
