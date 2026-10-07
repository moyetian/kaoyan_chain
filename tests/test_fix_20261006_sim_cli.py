# -*- coding: utf-8 -*-
"""2026-10-06 仿真缺陷修复批次 · W1（CLI/REPL 与 TUI 层）回归测试。

覆盖 F2/F3/F8/F11/F13-显示位：

* F2  考纲 Diff 自动探测不覆盖「408考纲_2027.md」等年份在后的命名
      （tools/cli/shared.py 模式集 + 年份降序；tui_navigator 委托单源）；
* F3  REPL 裸 exit/quit/退出 不可达退出分支 → 坠 LLM 计费对话；
* F8  不考数学默认激活数学（resolve_active_subject 四态 + config 显示位）；
* F11 TUI header 无依据宣称「考纲已核验入库」（placeholder/missing/ready/异常）；
* F13 TUI 考纲 Diff 显示位打印 baseline_warning（契约键由 syllabus_diff 提供，
      缺失/为空不打）。

全部用例隔离在 tmp_path：不联网、不写真实工作区、不触发 LLM。
"""
from __future__ import annotations

import importlib
import inspect
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import tui_navigator as tui  # noqa: E402
from tools.cli import shared as cli_shared  # noqa: E402
from test_fix_20261005_repl import _drive_repl  # noqa: E402


# ════════════════════════════════════════════════════════════════
# F2 · 考纲 Diff 自动探测（模式集 + 年份降序 + TUI 单源委托）
# ════════════════════════════════════════════════════════════════

def _make_ref(tmp_path: Path) -> Path:
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True, exist_ok=True)
    return ref


def test_f2_prefers_latest_year_candidate(monkeypatch, tmp_path):
    """「408考纲_2026.md」+「408考纲_2027.md」→ 必须返回 2027 新版。"""
    ref = _make_ref(tmp_path)
    (ref / "408考纲_2026.md").write_text("old", encoding="utf-8")
    (ref / "408考纲_2027.md").write_text("new", encoding="utf-8")
    monkeypatch.setattr(cli_shared, "ROOT", tmp_path)
    got = cli_shared._discover_new_syllabus()
    assert got is not None and got.name == "408考纲_2027.md", got


def test_f2_dated_beats_yearless(monkeypatch, tmp_path):
    """有四位年份的候选优先于无年份候选（无年份排后）。"""
    ref = _make_ref(tmp_path)
    (ref / "考研考纲.md").write_text("no year", encoding="utf-8")
    (ref / "2027大纲.md").write_text("dated", encoding="utf-8")
    monkeypatch.setattr(cli_shared, "ROOT", tmp_path)
    got = cli_shared._discover_new_syllabus()
    assert got is not None and got.name == "2027大纲.md", got


def test_f2_single_candidate_without_year(monkeypatch, tmp_path):
    """仅「811考纲.md」（无年份）→ 仍能命中（兜底模式）。"""
    ref = _make_ref(tmp_path)
    (ref / "811考纲.md").write_text("only", encoding="utf-8")
    monkeypatch.setattr(cli_shared, "ROOT", tmp_path)
    got = cli_shared._discover_new_syllabus()
    assert got is not None and got.name == "811考纲.md", got


def test_f2_yearless_candidates_sorted_by_name(monkeypatch, tmp_path):
    """同级（均无年份）候选按名称序稳定取首。"""
    ref = _make_ref(tmp_path)
    (ref / "b考纲.md").write_text("b", encoding="utf-8")
    (ref / "a考纲.md").write_text("a", encoding="utf-8")
    monkeypatch.setattr(cli_shared, "ROOT", tmp_path)
    got = cli_shared._discover_new_syllabus()
    assert got is not None and got.name == "a考纲.md", got


def test_f2_demo_and_sample_excluded(monkeypatch, tmp_path):
    """demo/样例 命名的候选仍被排除（仅剩它们时返回 None）。"""
    ref = _make_ref(tmp_path)
    (ref / "demo考纲_2027.md").write_text("d", encoding="utf-8")
    (ref / "样例考纲_2028.md").write_text("s", encoding="utf-8")
    monkeypatch.setattr(cli_shared, "ROOT", tmp_path)
    assert cli_shared._discover_new_syllabus() is None


def test_f2_missing_dir_returns_none(monkeypatch, tmp_path):
    """参考资料目录不存在 → None（阴性）。"""
    monkeypatch.setattr(cli_shared, "ROOT", tmp_path)
    assert cli_shared._discover_new_syllabus() is None


def test_f2_tui_delegates_to_shared_single_source(monkeypatch, tmp_path):
    """TUI 探测必须委托 cli.shared 单源：行为一致 + 源码不再带第二份模式集。"""
    ref = _make_ref(tmp_path)
    (ref / "408考纲_2026.md").write_text("old", encoding="utf-8")
    (ref / "408考纲_2027.md").write_text("new", encoding="utf-8")
    patched = 0
    for name in ("tools.cli.shared", "cli.shared"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        monkeypatch.setattr(mod, "ROOT", tmp_path)
        patched += 1
    assert patched >= 1, "cli.shared 未能定位，测试前置条件不成立"

    got = tui._discover_new_syllabus_file()
    assert got is not None and got.name == "408考纲_2027.md", got

    src = inspect.getsource(tui._discover_new_syllabus_file)
    assert "*2027*大纲*" not in src, "TUI 仍自带第二份 glob 模式集（未委托单源）"


# ════════════════════════════════════════════════════════════════
# F3 · REPL 裸 exit/quit/退出
# ════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("phrase", ["exit", "quit", "退出"])
def test_f3_bare_exit_phrases_quit_without_llm(monkeypatch, capsys, phrase):
    """裸退出词必须走本地退出分支：正常退出且不触发 LLM 调用。"""
    state = _drive_repl(monkeypatch, [phrase])
    out = capsys.readouterr().out
    assert state["llm_calls"] == [], \
        f"裸 {phrase!r} 坠入了 LLM 计费对话（应走本地退出分支）"
    assert "再见" in out, f"裸 {phrase!r} 未走退出分支：{out[-300:]}"


def test_f3_slash_exit_still_works(monkeypatch, capsys):
    """既有 /exit 形态回归不受影响。"""
    state = _drive_repl(monkeypatch, ["/exit"])
    out = capsys.readouterr().out
    assert state["llm_calls"] == []
    assert "再见" in out


# ════════════════════════════════════════════════════════════════
# F8 · 不考数学默认激活数学
# ════════════════════════════════════════════════════════════════

_F8_CASES = [
    # (cfg, expected)
    ({"active_subject": "math", "study_plan": {"math_key": "none"}}, "eng"),
    ({"active_subject": "math", "study_plan": {"math_key": "math1"}}, "math"),
    ({"active_subject": "pro", "study_plan": {"math_key": "none"}}, "pro"),
    ({"study_plan": {"math_key": "none"}}, "eng"),
    ({}, "math"),
    ({"active_subject": "bogus", "study_plan": {"math_key": "none"}}, "eng"),
]


@pytest.mark.parametrize("cfg,expected", _F8_CASES)
def test_f8_resolve_active_subject_states(cfg, expected):
    assert cli_shared.resolve_active_subject(cfg) == expected


def test_f8_show_config_displays_english_when_math_disabled(capsys):
    """config 显示位（曾直读 active_subject）不得再显示数学私教。"""
    from tools.cli import config as cli_config

    cli_config.show_config({"active_subject": "math",
                            "study_plan": {"math_key": "none"}})
    out = capsys.readouterr().out
    assert "英语专属私教" in out, f"config 显示位未按不考数学解析：{out[-400:]}"
    assert "数学专属私教" not in out, "不考数学仍显示数学专属私教"


# ════════════════════════════════════════════════════════════════
# F11 · TUI header 考纲状态文案分档
# ════════════════════════════════════════════════════════════════

def _setup_ribbon_env(monkeypatch, tmp_path: Path, syllabus_text):
    pro_dir = tmp_path / "04-专业课"
    pro_dir.mkdir(parents=True, exist_ok=True)
    if syllabus_text is not None:
        (pro_dir / "考试大纲.md").write_text(syllabus_text, encoding="utf-8")
    monkeypatch.setattr(tui, "ROOT", tmp_path)
    monkeypatch.setattr(tui, "get_config_summary",
                        lambda: {"school": "测试大学"})


@pytest.mark.parametrize("state,text,expect", [
    ("ready", "第一章 马克思主义基本原理（真实考纲正文）", "考纲已核验入库"),
    ("placeholder", "【待自填】请根据报考院校官网大纲填入", "待自填占位（未核验）"),
    ("missing", None, "尚未导入考纲"),
])
def test_f11_ribbon_by_syllabus_state(monkeypatch, tmp_path, state, text, expect):
    _setup_ribbon_env(monkeypatch, tmp_path, text)
    ribbon = tui.get_intel_ribbon()
    lines = [r for r in ribbon if "考纲变动" in r]
    assert lines, f"{state}: 缺少考纲变动 ribbon：{ribbon}"
    assert expect in lines[0], f"{state}: 文案不符：{lines[0]}"
    if state != "ready":
        assert "考纲已核验入库" not in lines[0], \
            f"{state}: 仍无依据宣称「已核验入库」：{lines[0]}"


def test_f11_probe_exception_keeps_legacy_wording(monkeypatch, tmp_path):
    """探测异常时保守沿用原文案（不新增错误断言，也不中断 ribbon）。"""
    _setup_ribbon_env(monkeypatch, tmp_path, "真实正文")

    def _boom(*a, **k):
        raise RuntimeError("probe fail")

    patched = 0
    for name in ("syllabus_manager", "tools.syllabus_manager"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        monkeypatch.setattr(mod, "pro_syllabus_state", _boom)
        patched += 1
    assert patched >= 1, "syllabus_manager 未能定位，测试前置条件不成立"

    ribbon = tui.get_intel_ribbon()
    lines = [r for r in ribbon if "考纲变动" in r]
    assert lines and "考纲已核验入库" in lines[0], f"异常态未沿用原文案：{lines}"


# ════════════════════════════════════════════════════════════════
# F13 显示位 · TUI 考纲 Diff 打印 baseline_warning
# ════════════════════════════════════════════════════════════════

_METRICS = {"volatility_percentage": 0.0, "stability_grade": "稳定",
            "added_count": 0, "removed_count": 0,
            "modified_count": 0, "unchanged_count": 3}


class _FakeDiffGen:
    def __init__(self, rep):
        self.rep = rep

    def compare_files(self, **kw):
        return self.rep

    def save_diff_report(self, rep):
        return "fake/考纲变动分析.md"


def _drive_diff_action(monkeypatch, tmp_path: Path, rep):
    old = tmp_path / "旧考纲.md"
    old.write_text("old body", encoding="utf-8")
    new = tmp_path / "新考纲.md"
    new.write_text("new body", encoding="utf-8")

    class _SD:
        @staticmethod
        def infer_diff_naming(a, b):
            return ("测试大学", "测试专业")

    mod = types.SimpleNamespace(
        current_exam_year=lambda: 2027,
        get_syllabus_diff_generator=lambda: _FakeDiffGen(rep),
        syllabus_diff=_SD(),
    )
    monkeypatch.setattr(tui, "resolve_intel_import", lambda: mod)
    monkeypatch.setattr(tui, "get_config_summary",
                        lambda: {"school": "测试大学", "major": "测试专业"})
    return tui.execute_action("4", interactive=False,
                              extra={"old": str(old), "new": str(new)},
                              batch=True)


def test_f13_diff_action_prints_baseline_warning(monkeypatch, tmp_path, capsys):
    """rep 含 baseline_warning → 打印黄色警示行。"""
    rep = {"metrics": dict(_METRICS),
           "baseline_warning": "新旧大纲为同一文件（旧考纲.md），自我对照无考纲变动意义"}
    _drive_diff_action(monkeypatch, tmp_path, rep)
    out = capsys.readouterr().out
    assert "自我对照无考纲变动意义" in out, f"未打印基准警示：{out[-400:]}"
    assert "[!]" in out, "警示行缺少 [!] 前缀"


def test_f13_diff_action_silent_without_warning(monkeypatch, tmp_path, capsys):
    """rep 无 baseline_warning（旧版返回）→ 不打警示（阴性）。"""
    _drive_diff_action(monkeypatch, tmp_path, {"metrics": dict(_METRICS)})
    out = capsys.readouterr().out
    assert "考纲 Diff 报告生成完毕" in out, "正常打印路径未走到，前置条件不成立"
    assert "[!]" not in out, f"无警示却打印了警示行：{out[-400:]}"


def test_f13_diff_action_silent_on_empty_warning(monkeypatch, tmp_path, capsys):
    """baseline_warning 为空串 → 同样不打（契约：缺失/为空不打）。"""
    _drive_diff_action(monkeypatch, tmp_path,
                       {"metrics": dict(_METRICS), "baseline_warning": ""})
    out = capsys.readouterr().out
    assert "[!]" not in out, f"空警示却打印了警示行：{out[-400:]}"
