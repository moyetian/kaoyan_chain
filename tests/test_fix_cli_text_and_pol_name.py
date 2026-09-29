# -*- coding: utf-8 -*-
"""P16 / P20 / P1-8 残留缺陷回归测试。

- P16：``ky plan`` 的 EOF 提示不得写死向导输入项数（该数目随报考画像变化，
  例如不考数学的文科考生会少若干数学问项），否则必然漂移。
- P20：``ky mount`` 写回白名单时，政治科目名在 ``study_plan.pol_name`` 缺失时
  必须回退到规范名「思想政治理论」，而不是 ``SUBJECT_FOLDER_MAP`` 的 label
  「政治」（与方案向导 / 看板口径不一致）。[P1-8] 该补齐逻辑已下沉到
  ``material_scanner.scan_and_mount_materials`` 的内存规范化。
- P1-8：``ky mount`` 默认只读盘点（scan-only，零写盘、零联网），仅 ``--apply``
  显式写回（写前打印 diff 并确认，``-y`` 跳过确认）。
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


def _seed_mount_workspace(tmp_path, plan):
    """最小工作区：ky_config.json + 带白名单四行的 AGENTS.md + 空资料目录。"""
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(
        json.dumps({"study_plan": dict(plan)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(
        "# 顶层总控协议\n\n- **手头资料白名单 (AI 严守范围)**:\n"
        "  - 数学: `旧数学`\n"
        "  - 英语: `旧英语`\n"
        "  - 政治: `旧政治`\n"
        "  - 专业课: `旧专业课`\n",
        encoding="utf-8",
    )
    for folder in ("01-数学", "02-英语", "03-思想政治理论", "04-专业课"):
        (tmp_path / folder / "参考资料").mkdir(parents=True, exist_ok=True)
    return cfg_file


# ── P1-8：ky mount 默认只读盘点，--apply 显式写回 ──────────────────────

def test_scan_only_default_does_not_write(tmp_path):
    """P1-8：scan_and_mount_materials 默认（apply=False）零写盘，只返回变更预览。"""
    from tools.skills import material_scanner

    cfg_file = _seed_mount_workspace(tmp_path, {"school": "", "major": ""})
    before_cfg = cfg_file.read_bytes()
    before_agents = (tmp_path / "AGENTS.md").read_bytes()

    res = material_scanner.scan_and_mount_materials(tmp_path, auto_scout_school=False)

    assert res["success"] is True and res["applied"] is False
    assert res["mode"] == "scan-only"
    assert res["changes"], "只读盘点应给出将发生的变更预览"
    assert cfg_file.read_bytes() == before_cfg, "scan-only 不得改写 ky_config.json"
    assert (tmp_path / "AGENTS.md").read_bytes() == before_agents, "scan-only 不得改写 AGENTS.md"
    assert not (tmp_path / ".memory").exists(), "scan-only 不得落任何备份/目录"


def test_scan_apply_seeds_canonical_pol_name(tmp_path):
    """P20（下沉后）：apply 写回时 pol_name 缺失必须补齐规范名「思想政治理论」。"""
    from tools.skills import material_scanner

    cfg_file = _seed_mount_workspace(tmp_path, {"school": "", "major": ""})
    res = material_scanner.scan_and_mount_materials(tmp_path, auto_scout_school=False, apply=True)

    assert res["success"] is True and res["applied"] is True
    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["study_plan"]["pol_name"] == "思想政治理论"
    assert any(c["field"] == "study_plan.pol_name" for c in res["changes"])


def test_scan_apply_preserves_existing_pol_name(tmp_path):
    """P20（下沉后）：已有 pol_name（含考生自定义）不得被覆盖，eng_name 行为不变。"""
    from tools.skills import material_scanner

    cfg_file = _seed_mount_workspace(tmp_path, {"pol_name": "政治", "eng_name": "英语一 (201)"})
    material_scanner.scan_and_mount_materials(tmp_path, auto_scout_school=False, apply=True)

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["study_plan"]["pol_name"] == "政治"
    assert data["study_plan"]["eng_name"] == "英语一 (201)"


def _fake_scanner_factory(calls, payload=None):
    """构造记录 apply 实参的假扫描器；默认返回一条待写变更。"""
    def _fake(workspace_root=None, auto_scout_school=True, apply=False):
        calls.append(apply)
        base = {
            "success": True, "total_files": 0, "details": {},
            "changes": [{"target": "ky_config.json", "field": "study_plan.pro_books",
                         "old": None, "new": "暂未放置实体资料"}],
            "would_watch": False, "would_scout": "", "school_watch": "",
        }
        if payload:
            base.update(payload)
        return base
    return _fake


def test_mount_default_is_scan_only(monkeypatch, capsys):
    """P1-8：ky mount 默认只读 —— scanner 以 apply=False 调用，输出提示 --apply。"""
    from tools.cli.commands import material
    from tools.skills import material_scanner

    calls = []
    monkeypatch.setattr(material_scanner, "scan_and_mount_materials", _fake_scanner_factory(calls))

    material._cmd_mount(["mount"])

    assert calls == [False], "默认模式必须以只读方式调用扫描器"
    out = capsys.readouterr().out
    assert "只读盘点" in out
    assert "--apply" in out


def test_mount_apply_confirms_before_write(monkeypatch):
    """P1-8：ky mount --apply 写前确认；输入 y 后以 apply=True 写回。"""
    from tools.cli.commands import material
    from tools.skills import material_scanner

    calls = []
    monkeypatch.setattr(material_scanner, "scan_and_mount_materials", _fake_scanner_factory(calls))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

    material._cmd_mount(["mount", "--apply"])

    assert calls == [False, True]


def test_mount_apply_cancel_writes_nothing(monkeypatch, capsys):
    """P1-8：确认被拒绝（n）时不得写回。"""
    from tools.cli.commands import material
    from tools.skills import material_scanner

    calls = []
    monkeypatch.setattr(material_scanner, "scan_and_mount_materials", _fake_scanner_factory(calls))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")

    material._cmd_mount(["mount", "--apply"])

    assert calls == [False], "取消后不得再以 apply=True 调用扫描器"
    assert "已取消" in capsys.readouterr().out


def test_mount_apply_yes_flag_skips_confirmation(monkeypatch):
    """P1-8：-y 跳过确认直接写回（不得调用 input）。"""
    from tools.cli.commands import material
    from tools.skills import material_scanner

    calls = []
    monkeypatch.setattr(material_scanner, "scan_and_mount_materials", _fake_scanner_factory(calls))
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("不应调用 input"))

    material._cmd_mount(["mount", "--apply", "-y"])

    assert calls == [False, True]


def test_mount_apply_no_pending_changes_skips_write(monkeypatch, capsys):
    """P1-8：无任何变更时 --apply 直接跳过，不进入写回。"""
    from tools.cli.commands import material
    from tools.skills import material_scanner

    calls = []
    monkeypatch.setattr(material_scanner, "scan_and_mount_materials",
                        _fake_scanner_factory(calls, payload={"changes": []}))

    material._cmd_mount(["mount", "--apply", "-y"])

    assert calls == [False]
    assert "无任何变更" in capsys.readouterr().out


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

