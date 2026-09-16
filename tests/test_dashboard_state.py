# -*- coding: utf-8 -*-
"""
tools/state 共享状态层回归测试

覆盖目标：三端（CLI / TUI / GUI）此前各写一份「今日任务解析」所导致的真实差异。
其中 GUI 那份的表格分隔行判定不认 `| --- |`（带空格）写法，会把分隔行
当成一条任务计入总数 —— 本文件用夹具把该缺陷钉住。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.state import (  # noqa: E402
    SUBJECT_ORDER,
    is_separator_row,
    load_dashboard_state,
    parse_task_lines,
    resolve_daily_hours,
    resolve_style,
    state_to_cli_dict,
)


# ── 夹具工作区构造 ──────────────────────────────────────────────

def _make_workspace(tmp_path: Path, math_text: str, encoding: str = "utf-8") -> Path:
    """搭一个最小工作区：四科 _状态/今日任务.md + ky_config.json。"""
    for key, folder in SUBJECT_ORDER:
        d = tmp_path / folder / "_状态"
        d.mkdir(parents=True, exist_ok=True)
        (d / "今日任务.md").write_text(
            math_text if key == "math" else "", encoding=encoding)
    return tmp_path


# ── 解析器单元测试 ──────────────────────────────────────────────

def test_separator_row_with_spaces_is_not_a_task():
    """[缺陷修复] `| --- |` 带空格的分隔行必须被识别为分隔行。

    修复前 GUI 用 `startswith("|---|")` 判定，带空格写法漏判 → 任务总数虚高。
    """
    text = (
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
        "| --- | --- | --- | --- |\n"
        "| 概念精讲 | 梳理高频考点 | 36 分钟 | [ ] |\n"
    )
    items = parse_task_lines(text)
    assert len(items) == 1, f"分隔行被当成了任务：{items}"
    assert items[0].module == "概念精讲"
    assert items[0].done is False


@pytest.mark.parametrize("line", [
    "|---|---|---|",
    "| --- | --- | --- |",
    "| :--- | :---: | ---: |",
    "|  ---  |  ---  |",
])
def test_separator_row_variants(line):
    cells = [c.strip() for c in line.split("|") if c.strip()]
    assert is_separator_row(cells), f"未识别为分隔行: {line!r}"


@pytest.mark.parametrize("line", [
    "| 概念精讲 | 梳理高频考点 | 36 分钟 | [ ] |",
    "| 模块 | 任务内容 | 预计用时 | 完成状态 |",
])
def test_data_row_is_not_separator(line):
    cells = [c.strip() for c in line.split("|") if c.strip()]
    assert not is_separator_row(cells), f"被误判为分隔行: {line!r}"


def test_list_style_tasks():
    text = "- [ ] 精读真题阅读\n- [x] 复盘昨日错题\n- [X] 大写字面量也应识别\n"
    items = parse_task_lines(text)
    assert [i.done for i in items] == [False, True, True]
    assert items[0].content == "精读真题阅读"
    assert items[0].module == "任务"
    assert items[0].duration == ""


def test_header_row_excluded():
    text = "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n|---|---|---|---|\n"
    assert parse_task_lines(text) == (), "表头/分隔行不应产生任务"


def test_empty_and_none_text():
    assert parse_task_lines("") == ()
    assert parse_task_lines("\n\n  \n") == ()


# ── 共享状态层集成测试 ──────────────────────────────────────────

TABLE_TASK = (
    "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
    "| --- | --- | --- | --- |\n"
    "| 概念精讲 | 梳理高频考点 | 36 分钟 | [x] |\n"
    "| 习题精练 | 精选真题演练 | 99 分钟 | [ ] |\n"
)


def test_state_counts_are_correct_with_spaced_separator(tmp_path):
    ws = _make_workspace(tmp_path, TABLE_TASK)
    state = load_dashboard_state(ws, cfg={})

    math = state.by_key("math")
    assert math is not None
    assert math.total == 2, f"总数应为 2（分隔行不得计入），实际 {math.total}"
    assert math.completed == 1
    assert math.pct == 50
    assert math.summary_text == "1/2 (50%)"


def test_state_root_parameter_isolates_workspace(tmp_path):
    """[缺陷修复] GUI 旧实现支持自定义工作区，CLI/TUI 硬编码 ROOT。

    现在四端统一走 workspace_root 参数，这里验证「换根目录就换数据」。
    """
    ws = _make_workspace(tmp_path, TABLE_TASK)
    state = load_dashboard_state(ws, cfg={})
    assert state.by_key("math").total == 2
    # 其它三科是空文件 → 0 任务，但不报错
    for key in ("eng", "pol", "pro"):
        assert state.by_key(key).total == 0
        assert state.by_key(key).summary_text == "0/0 (0%)"


def test_gbk_task_file_is_readable(tmp_path):
    """[缺陷修复] GUI/TUI 旧实现只试 utf-8 且静默吞异常 → GBK 手写笔记变「没有任务」。

    CLI 那份支持 gbk 回退。统一后三端都应能读到 GBK 文件。
    """
    gbk_text = "- [ ] 背诵政治帽子词\n- [x] 复盘英语长难句\n"
    ws = _make_workspace(tmp_path, gbk_text, encoding="gbk")
    state = load_dashboard_state(ws, cfg={})
    math = state.by_key("math")
    assert math.total == 2, "GBK 编码的任务文件应能被读取"
    assert math.completed == 1


def test_missing_task_file_yields_zero(tmp_path):
    """文件不存在时不报错、按 0 任务处理（与三端旧行为一致）。"""
    state = load_dashboard_state(tmp_path, cfg={})
    assert state.total == 0
    assert state.rate == 0.0


# ── 配置解析的真源规则 ──────────────────────────────────────────

def test_daily_hours_reads_real_config_key():
    """[缺陷修复] TUI 旧实现读 `daily_budget_hours`（全项目无人写入）

    → 永远显示兜底 8.5 小时。真实来源是向导写入的 `study_plan.total_hours`。
    """
    cfg = {"study_plan": {"total_hours": 6.8}, "daily_budget_hours": 9.9}
    assert resolve_daily_hours(cfg) == 6.8, "应优先采信 study_plan.total_hours"


def test_daily_hours_fallback():
    assert resolve_daily_hours({}) == 8.5
    assert resolve_daily_hours({"study_plan": {"total_hours": "bad"}}) == 8.5
    assert resolve_daily_hours({"study_plan": {"total_hours": 0}}) == 8.5


def test_style_prefers_wizard_written_key():
    """[缺陷修复] 风格单一真源：study_plan.style_name → 顶层 coaching_style。"""
    assert resolve_style({"study_plan": {"style_name": "A"}, "coaching_style": "B"}) == "A"
    assert resolve_style({"coaching_style": "B"}) == "B"
    assert resolve_style({}) == "严格把关·保姆提分型"


def test_cli_dict_shape_is_unchanged():
    """[零破坏] `ky today --json` 的字典结构必须与旧实现逐键一致。"""
    state = load_dashboard_state(ROOT, cfg={})
    data = state_to_cli_dict(state)
    assert set(data) == {"date", "subjects", "summary"}
    assert set(data["summary"]) == {"total", "completed", "rate"}
    for key, _folder in SUBJECT_ORDER:
        assert key in data["subjects"]
        entry = data["subjects"][key]
        assert set(entry) == {"label", "tasks", "total", "completed"}
        assert entry["total"] == len(entry["tasks"])
        for task in entry["tasks"]:
            assert set(task) == {"module", "content", "duration", "done"}


def test_countdown_comes_from_exam_calendar():
    """倒计时不在本层重复实现，委托 exam_calendar（数值应为非负整数）。"""
    state = load_dashboard_state(cfg={"study_plan": {"exam_date": "2026-12-19"}})
    assert isinstance(state.days_left, int)
    assert state.days_left >= 0
    assert state.exam_date == "2026-12-19"
