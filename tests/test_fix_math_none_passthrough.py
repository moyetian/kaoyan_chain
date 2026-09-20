# -*- coding: utf-8 -*-
"""「不考数学未贯穿」一族修复的回归测试：P5 / P9 / P16 / P17 / P24 / active_subject。

全部用 ``tmp_path`` 构造隔离 workspace，绝不触碰主仓库的 ``ky_config.json``、
各科 ``_状态/`` 与 ``docs/``（不跑 update_dashboard.py）。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import types
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB_DIR = REPO / "05-考研看板" / "web"

import tools.study_planner as sp  # noqa: E402


# ───────────────────────── 隔离加载看板 web 包 ─────────────────────────
def _load_web(tmp_path: Path, ky_config: dict):
    """把 web/{__init__,config,markdown}.py 复制到 tmp_path/board/web，
    在 tmp_path 写入受控 ky_config.json，再以唯一包名导入，返回 (config, markdown)。

    这样 config.py 的 ``_PKG_ROOT``/``_REPO_ROOT`` 会指向 tmp_path，与主仓库完全隔离。
    """
    board = tmp_path / "board"
    web = board / "web"
    web.mkdir(parents=True)
    (web / "__init__.py").write_text("", encoding="utf-8")
    for name in ("config.py", "markdown.py"):
        shutil.copy2(WEB_DIR / name, web / name)
    (tmp_path / "ky_config.json").write_text(
        json.dumps(ky_config, ensure_ascii=False), encoding="utf-8")

    pkg_name = "kyweb_" + uuid.uuid4().hex[:8]
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(web)]
    sys.modules[pkg_name] = pkg
    loaded = {}
    for sub in ("config", "markdown"):
        spec = importlib.util.spec_from_file_location(f"{pkg_name}.{sub}", web / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{sub}"] = mod
        spec.loader.exec_module(mod)
        loaded[sub] = mod
    return loaded["config"], loaded["markdown"]


# ───────────────────────── P5：看板剔除数学卡/板块 ─────────────────────────
def test_p5_math_none_drops_math_subject_and_sections(tmp_path):
    cfg, _ = _load_web(tmp_path, {"study_plan": {"math_key": "none", "math_name": "不考数学"}})
    assert [s["key"] for s in cfg.SUBJECTS] == ["eng", "pol", "pro"]
    assert "math" not in cfg.SECTIONS


def test_p5_math_enabled_keeps_math(tmp_path):
    cfg, _ = _load_web(tmp_path, {"study_plan": {"math_key": "math2"}})
    assert [s["key"] for s in cfg.SUBJECTS] == ["math", "eng", "pol", "pro"]
    assert "math" in cfg.SECTIONS
    assert cfg.SUBJECTS[0]["target"] == 110


def test_p5_mode_b_and_mode_c_not_regressed(tmp_path):
    cfgb, _ = _load_web(tmp_path / "b", {"study_plan": {"pro2_name": "专业课二"}})
    assert [s["key"] for s in cfgb.SUBJECTS] == ["eng", "pol", "pro", "pro2"]
    assert "math" not in cfgb.SECTIONS

    cfgc, _ = _load_web(tmp_path / "c", {"study_plan": {"exam_mode": "mode_c", "pro_name": "199 管理类综合能力"}})
    assert [s["key"] for s in cfgc.SUBJECTS] == ["pro", "eng"]
    assert "math" not in cfgc.SECTIONS


# ───────────────────────── P24：看板重复抽卡 ─────────────────────────
_TEMPLATE_LEARN = (REPO / "04-专业课" / "学情档案.template.md")


def test_p24_pro_weak_sections_have_no_duplicate_kw(tmp_path):
    cfg, _ = _load_web(tmp_path, {"study_plan": {"math_key": "math2"}})
    kws = [kw for rel, kw, tab, _ov in cfg.SECTIONS["pro"]
           if rel == "学情档案.md" and tab == "weak"]
    assert kws == ["章节掌握度", "错题重做队列"]
    assert len(kws) == len(set(kws))


def test_p24_duplicate_resolution_is_gone(tmp_path):
    """阴性对照：旧配置（同时含「章节掌握度」「掌握度」）两条会解析到同一章节，
    新配置只解析出 1 条 —— 即 decks['weak'] 不再生成两份相同卡片组。"""
    _cfg, md = _load_web(tmp_path, {"study_plan": {"math_key": "math2"}})
    text = _TEMPLATE_LEARN.read_text(encoding="utf-8")

    legacy = [("章节掌握度", "weak"), ("掌握度", "weak")]
    resolved_legacy = {md.get_section(text, _kw) for _kw, _t in legacy}
    assert len(resolved_legacy) == 1  # 两个关键词命中同一章节 → 旧行为重复抽卡

    current = [("章节掌握度", "weak")]
    resolved_current = {md.get_section(text, _kw) for _kw, _t in current}
    assert len(resolved_current) == 1
    assert None not in resolved_current


def test_p24_get_section_prefers_exact_heading(tmp_path):
    """精确章节名匹配优先于子串匹配。"""
    _cfg, md = _load_web(tmp_path, {"study_plan": {"math_key": "math2"}})
    text = "## 掌握度\nEXACT_BODY\n\n## 二、章节掌握度雷达\nSUBSTR_BODY\n"
    sec = md.get_section(text, "掌握度")
    assert "EXACT_BODY" in sec and "SUBSTR_BODY" not in sec


# ───────────────────────── P9：total_hours 重算 ─────────────────────────
def _run_wizard(monkeypatch, preset):
    monkeypatch.setattr(sp, "apply_study_plan", lambda *a, **k: None)
    monkeypatch.setattr(sp, "print_study_plan_summary", lambda *a, **k: None)
    return sp.run_study_plan_wizard(interactive=False, preset_data=dict(preset))


def test_p9_total_hours_recomputed_when_math_disabled(monkeypatch):
    plan = _run_wizard(monkeypatch, {
        "math_key": "none", "math_name": "不考数学",
        "total_hours": 8.5, "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5,
    })
    assert plan["math_hours"] == 0.0
    assert plan["total_hours"] == 5.5


def test_p9_explicit_non_preset_total_is_kept(monkeypatch):
    plan = _run_wizard(monkeypatch, {
        "math_key": "none", "math_name": "不考数学",
        "total_hours": 9.0, "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5,
    })
    assert plan["total_hours"] == 9.0


def test_p9_math_present_keeps_total(monkeypatch):
    plan = _run_wizard(monkeypatch, {
        "math_key": "math2", "math_name": "数学二 (302)",
        "total_hours": 8.5, "math_hours": 3.0,
        "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5,
    })
    assert plan["total_hours"] == 8.5


# ───────────────────────── P16：向导编号动态连续 ─────────────────────────
def _drive_interactive_wizard(monkeypatch, capsys):
    def _fake_input(prompt=""):
        sys.stdout.write(str(prompt) + "\n")
        return "none" if "数学科目" in str(prompt) else ""
    monkeypatch.setattr("builtins.input", _fake_input)
    monkeypatch.setattr(sp, "apply_study_plan", lambda *a, **k: None)
    monkeypatch.setattr(sp, "print_study_plan_summary", lambda *a, **k: None)
    sp.run_study_plan_wizard(interactive=True)
    return capsys.readouterr().out


def test_p16_wizard_numbering_is_continuous_without_math(monkeypatch, capsys):
    out = _drive_interactive_wizard(monkeypatch, capsys)
    for expected in ("1. 英语权威资料", "2. 政治权威资料", "3. 专业课权威资料",
                     "2. 英语每日分配小时", "3. 政治每日分配小时", "4. 专业课每日分配小时",
                     "1. 英语目标成绩", "4. 初试总分目标"):
        assert expected in out, expected
    for absent in ("2. 英语权威资料", "4. 专业课权威资料",
                   "3. 英语每日分配小时", "2. 英语目标成绩", "数3.0h"):
        assert absent not in out, absent


# ───────────────────────── P17：无资料不派白名单刷题 ─────────────────────────
_PLACEHOLDER = "暂未放置实体资料（私教严格按【思想政治理论】官方考纲出题，严禁虚构书目）"


def test_p17_material_phrase_falls_back_without_books():
    assert sp._material_phrase(_PLACEHOLDER, "精做") == "按官方考纲精做"
    assert sp._material_phrase("", "选取") == "按官方考纲选取"
    assert sp._material_phrase("不考数学", "精选") == "按官方考纲精选"


def test_p17_material_phrase_quotes_real_whitelist():
    got = sp._material_phrase("[本地资料库已就绪]: 核心考案.pdf, 1000题.pdf", "精做")
    assert got.startswith("精做白名单【") and "核心考案.pdf" in got


def test_p17_today_tasks_do_not_advertise_empty_whitelist(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sp, "_generate_subject_task_content",
        lambda key, name, plan, today, tpl, workspace_root=None: tpl)
    plan = {
        "math_key": "none", "math_name": "不考数学", "pro_name": "专业课",
        "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5,
        "pol_books": _PLACEHOLDER,
        "pro_books": "暂未放置实体资料（私教严格按【专业课】官方考纲出题，严禁虚构书目）",
        "math_books": "不考数学", "eng_books": "暂未放置实体资料（…）",
    }
    sp.generate_plan_and_today_files(plan, ai_strategy="测试策略", workspace_root=tmp_path)

    pol_task = (tmp_path / "03-思想政治理论" / "_状态" / "今日任务.md").read_text(encoding="utf-8")
    pro_task = (tmp_path / "04-专业课" / "_状态" / "今日任务.md").read_text(encoding="utf-8")
    assert "按官方考纲精做 20 道核心选择题自测" in pol_task
    assert "按官方考纲选取经典大题" in pro_task
    assert "暂未放置实体资料" not in pol_task
    assert "暂未放置实体资料" not in pro_task


# ───────────────────── active_subject：不考数学不再默认数学私教 ─────────────────────
def test_active_subject_helper():
    assert sp._default_active_subject_for_plan(
        {"math_key": "none", "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5}) == "eng"
    assert sp._default_active_subject_for_plan(
        {"math_key": "none", "eng_hours": 0, "pol_hours": 1.0}) == "pol"
    assert sp._default_active_subject_for_plan(
        {"math_key": "math2", "eng_hours": 2.0}) is None


def _apply_plan_isolated(monkeypatch, tmp_path, ky_config):
    import tools.syllabus_manager as sm
    import tools.intelligence.watcher as watcher_mod

    (tmp_path / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(json.dumps(ky_config, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(sp, "ROOT", tmp_path)
    monkeypatch.setattr(sp, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(sp, "run_ai_study_plan_generation", lambda *a, **k: "策略")
    monkeypatch.setattr(sp, "update_subject_agents", lambda *a, **k: None)
    monkeypatch.setattr(sp, "generate_plan_and_today_files", lambda *a, **k: None)
    monkeypatch.setattr(sm, "apply_syllabus_selection", lambda **k: None)

    class _FakeWatcher:
        def list_watched(self):
            return []

        def remove_watch(self, *a, **k):
            pass

        def add_watch(self, *a, **k):
            pass

    monkeypatch.setattr(watcher_mod, "AdmissionWatcher", _FakeWatcher)
    return cfg_file


def test_active_subject_written_when_math_disabled(monkeypatch, tmp_path):
    cfg_file = _apply_plan_isolated(monkeypatch, tmp_path, {})
    sp.apply_study_plan({"math_key": "none", "math_name": "不考数学",
                         "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5,
                         "style_name": "严格把关型"}, interactive=False)
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["active_subject"] == "eng"


def test_active_subject_not_overwritten_when_user_chose_one(monkeypatch, tmp_path):
    cfg_file = _apply_plan_isolated(monkeypatch, tmp_path, {"active_subject": "pol"})
    sp.apply_study_plan({"math_key": "none", "math_name": "不考数学",
                         "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5,
                         "style_name": "严格把关型"}, interactive=False)
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["active_subject"] == "pol"


def test_active_subject_untouched_when_math_enabled(monkeypatch, tmp_path):
    cfg_file = _apply_plan_isolated(monkeypatch, tmp_path, {})
    sp.apply_study_plan({"math_key": "math2", "math_name": "数学二 (302)",
                         "math_hours": 3.0, "eng_hours": 2.0, "pol_hours": 1.0,
                         "pro_hours": 2.5, "style_name": "严格把关型"}, interactive=False)
    assert "active_subject" not in json.loads(cfg_file.read_text(encoding="utf-8"))
