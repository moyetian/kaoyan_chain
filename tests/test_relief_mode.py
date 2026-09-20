# -*- coding: utf-8 -*-
"""P2-7 回归：减负模式风格副作用可控（--keep-style / --off / 原风格记录顺序）。"""
import json

import pytest

from tools import study_planner as planner


@pytest.fixture()
def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(planner, "ROOT", tmp_path)
    cfg = {"study_plan": {"total_hours": 8.0, "math_hours": 3.0, "eng_hours": 2.0,
                          "pol_hours": 1.0, "pro_hours": 2.0,
                          "style_name": "严格把关·保姆提分型 (Strict & Disciplined)"},
           "coaching_style": "严格把关·保姆提分型 (Strict & Disciplined)"}
    (tmp_path / "ky_config.json").write_text(json.dumps(cfg, ensure_ascii=False),
                                             encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "- **当前激活辅导风格**：`严格把关`\n每日投入 `8.0 小时`\n", encoding="utf-8")
    return tmp_path


def _load(ws):
    return json.loads((ws / "ky_config.json").read_text(encoding="utf-8"))


def test_relief_records_previous_style_before_overwrite(_ws):
    res = planner.apply_relief_mode()
    assert res["success"] and res["style_changed"] is True
    cfg = _load(_ws)
    # 顺序 bug 回归：记录的必须是原风格，而不是减负风格本身
    assert cfg["style_before_relief"] == "严格把关·保姆提分型 (Strict & Disciplined)"
    assert cfg["study_plan"]["total_hours"] == 6.0
    assert "温和启发" in cfg["coaching_style"]
    agents = (_ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "温和启发" in agents


def test_relief_keep_style_does_not_touch_style(_ws):
    res = planner.apply_relief_mode(keep_style=True)
    assert res["success"] and res["style_changed"] is False
    cfg = _load(_ws)
    assert cfg["study_plan"]["total_hours"] == 6.0
    assert cfg["coaching_style"] == "严格把关·保姆提分型 (Strict & Disciplined)"
    assert "style_before_relief" not in cfg
    agents = (_ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "温和启发" not in agents


def test_relief_restore_reverts_hours_and_style(_ws):
    planner.apply_relief_mode()
    res = planner.restore_relief_mode()
    assert res["success"]
    cfg = _load(_ws)
    assert cfg["study_plan"]["total_hours"] == 8.0
    assert cfg["coaching_style"] == "严格把关·保姆提分型 (Strict & Disciplined)"
    assert cfg["relief_mode_active"] is False
    assert "baseline_total_hours" not in cfg["study_plan"]
    agents = (_ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "严格把关" in agents


def test_relief_is_idempotent(_ws):
    first = planner.apply_relief_mode()
    second = planner.apply_relief_mode()
    assert second["success"]
    assert _load(_ws)["study_plan"]["total_hours"] == first["new_hours"]


def test_restore_when_inactive_fails_cleanly(_ws):
    res = planner.restore_relief_mode()
    assert res["success"] is False
