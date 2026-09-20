# -*- coding: utf-8 -*-
"""P16 / P20 残留缺陷回归测试。

- P16：``ky plan`` 的 EOF 提示不得写死向导输入项数（该数目随报考画像变化，
  例如不考数学的文科考生会少若干数学问项），否则必然漂移。
- P20：``ky mount`` 写回白名单时，政治科目名在 ``study_plan.pol_name`` 缺失时
  必须回退到规范名「思想政治理论」，而不是 ``SUBJECT_FOLDER_MAP`` 的 label
  「政治」（与方案向导 / 看板口径不一致）。
"""

import json

import pytest


def test_plan_eof_hint_has_no_hardcoded_item_count(monkeypatch, capsys):
    """P16：EOF 提示里不再出现写死的项数。"""
    import tools.study_planner as planner
    from tools.cli.commands import system

    def _raise_eof(*_args, **_kwargs):
        raise EOFError

    monkeypatch.setattr(planner, "run_study_plan_wizard", _raise_eof)
    system._cmd_plan(["plan"])

    out = capsys.readouterr().out
    assert "35" not in out
    assert "ky plan" in out


def _run_mount_with_fake_scanner(monkeypatch, tmp_path, initial_plan):
    """在 tmp 配置上跑 ``_cmd_mount``，返回扫描器实际读到的 study_plan。"""
    import tools.cli.shared as shared
    from tools.cli.commands import material
    from tools.skills import material_scanner

    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(
        json.dumps({"study_plan": dict(initial_plan)}, ensure_ascii=False),
        encoding="utf-8",
    )
    # conftest 已把 CONFIG_FILE 重定向到 tmp_path，这里再显式钉死一次，确保断言对象一致
    monkeypatch.setattr(shared, "CONFIG_FILE", cfg_file, raising=False)

    seen = {}

    def _fake_scan(workspace_root=None, auto_scout_school=True):
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        seen["plan"] = data.get("study_plan", {})
        return {"success": True, "total_files": 0, "details": {}, "school_watch": ""}

    monkeypatch.setattr(material_scanner, "scan_and_mount_materials", _fake_scan)
    material._cmd_mount(["mount"])
    return seen["plan"]


def test_mount_seeds_canonical_pol_name_when_missing(monkeypatch, tmp_path):
    """P20：pol_name 缺失时，写回前补齐规范名「思想政治理论」。"""
    plan = _run_mount_with_fake_scanner(monkeypatch, tmp_path, {"school": "示例院校A"})
    assert plan.get("pol_name") == "思想政治理论"


def test_mount_preserves_existing_pol_name(monkeypatch, tmp_path):
    """P20：已有 pol_name（含考生自定义）不得被覆盖，其它科目行为不变。"""
    plan = _run_mount_with_fake_scanner(
        monkeypatch, tmp_path, {"pol_name": "政治", "eng_name": "英语一 (201)"}
    )
    assert plan.get("pol_name") == "政治"
    assert plan.get("eng_name") == "英语一 (201)"


# ── R2-A4：不考数学时 CLI 文案/路由必须跟随 ──────────────────────

def _cfg(math_key="none", math_name="不考数学"):
    return {"study_plan": {"math_key": math_key, "math_name": math_name}}


def test_is_math_disabled_covers_none_and_other_modes():
    """R2-A4：判定单源覆盖 none / 中文名 / mode_b 双专业课 / 政治停考。"""
    from tools.cli import shared
    assert shared.is_math_disabled(_cfg("none")) is True
    assert shared.is_math_disabled(_cfg("", "不考数学")) is True
    assert shared.is_math_disabled({"study_plan": {"exam_mode": "mode_b"}}) is True
    assert shared.is_math_disabled({"study_plan": {"pro2_name": "823 X"}}) is True
    assert shared.is_math_disabled({"study_plan": {"pol_disabled": True}}) is True
    # 数学二正常方案不得被误判
    assert shared.is_math_disabled(_cfg("math2", "数学二 (302)")) is False
    assert shared.is_math_disabled({}) is False


def test_recommended_checkin_switches_with_math():
    from tools.cli import shared
    assert shared.recommended_checkin_command(_cfg("none")) == "英语报到"
    assert shared.recommended_checkin_command(_cfg("math2", "数学二 (302)")) == "数学报到"


def _seed_today_files(root):
    for folder in ("01-数学", "02-英语", "03-思想政治理论", "04-专业课"):
        d = root / folder / "_状态"
        d.mkdir(parents=True, exist_ok=True)
        (d / "今日任务.md").write_text(
            "# 今日任务\n\n| 模块 | 内容 | 时长 | 状态 |\n|---|---|---|---|\n"
            "| 核心 | 示例任务 | 30 分钟 | [ ] |\n",
            encoding="utf-8")


def test_today_summary_hides_math_segment_when_math_none(monkeypatch, capsys, tmp_path):
    """R2-A4：不考数学时 ky today 不得输出空的【数学】段，口令指向英语。"""
    from tools.cli.repl import renderer
    _seed_today_files(tmp_path)
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    monkeypatch.setattr(renderer, "load_config", lambda: _cfg("none"))
    monkeypatch.setattr(renderer, "get_today_tasks_data",
                        lambda: {"subjects": {"eng": {}, "pol": {}, "pro": {}}})

    renderer.print_today_tasks_summary()

    out = capsys.readouterr().out
    assert "【数学】" not in out
    assert "数学报到" not in out
    assert "如「英语报到」" in out


def test_today_summary_keeps_math_segment_when_math2(monkeypatch, capsys, tmp_path):
    """R2-A4 防过度修复：数学二方案下【数学】段与「数学报到」必须照常出现。"""
    from tools.cli.repl import renderer
    _seed_today_files(tmp_path)
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    monkeypatch.setattr(renderer, "load_config", lambda: _cfg("math2", "数学二 (302)"))
    monkeypatch.setattr(renderer, "get_today_tasks_data",
                        lambda: {"subjects": {"math": {}, "eng": {}, "pol": {}, "pro": {}}})

    renderer.print_today_tasks_summary()

    out = capsys.readouterr().out
    assert "【数学】" in out
    assert "如「数学报到」" in out


def test_welcome_and_palette_hide_math_when_math_none(monkeypatch, capsys):
    """R2-A4：欢迎横幅与指令面板不得再列 /math 与「数学报到」。"""
    from tools.cli.repl import renderer
    monkeypatch.setattr(renderer, "load_config", lambda: _cfg("none"))

    renderer.print_command_palette(_cfg("none"))
    palette = capsys.readouterr().out
    assert "/math" not in palette
    assert "数学报到" not in palette

    renderer.print_command_palette(_cfg("math2", "数学二 (302)"))
    palette_math = capsys.readouterr().out
    assert "/math" in palette_math
    assert "数学报到" in palette_math


def test_agent_system_prompt_does_not_hardcode_math_first():
    """R2-A4：报到规范提示词不再把「数学报到」列在科目首位。"""
    import tools.cli.agent.engine as engine
    import tools.agent.context_engine as context_engine
    import inspect

    for mod in (engine, context_engine):
        src = inspect.getsource(mod)
        assert "“报到”、“数学报到”" not in src, f"{mod.__name__} 仍硬编码数学报到在首位"

