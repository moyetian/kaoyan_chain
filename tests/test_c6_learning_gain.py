# -*- coding: utf-8 -*-
"""C6 元测试：学习增益代理指标（``tools/benchmarks/learning_gain.py``）。

背景（升级规划 C6）：C1–C5 全在测 **AI 正确性**，学习增益此前无任何测试。
被测模块给出三条不联网、不调 LLM 的本地代理指标，本文件按七层锁定契约：

  1. 常量与退出码口径（复用 ``benchmarks.runner`` 的 ``0`` / ``2``）；
  2. 周聚合与通过口径（ISO 跨年周、good/easy 才算通过、pass_rate 精确数值）；
  3. 坏行容错（JSON 损坏 / ts 不可解析 / rating 非法 / 顶层非对象 → 计数跳过，
     其余有效行照常统计，status 仍为 ok）；
  4. 错因分组与跨日复发（弱判据 = 同一错因出现在 ≥2 个**不同日期**；分组顺序
     按 ``open_grader.MISTAKE_TYPES`` 原序，未知错因排后）；
  5. 完成率按周加权（周 rate = ``sum(completed)/sum(total)``，不是逐日 rate 的
     简单平均）；
  6. 空数据 → 三指标全 insufficient 且退出码 2；有数据 → 0；落盘位置、markdown
     三节标题、``to_dict`` 可 JSON 序列化、safe 模式不落盘、闸门竞态兜底；
  7. 隔离元测试（所有用例显式传 ``root=tmp_path``，禁止无参调用与真实路径）。

诚实边界：本文件只锁「口径与机制正确」，不锁任何达标阈值 —— 被测模块本身
刻意不设达标线（退出码只有 0/2，没有"失败"）。测试数据全部中性化（subject 用
math/politics、题名用「测试题」，无任何真实身份信息），且只写 ``tmp_path``。
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmarks import learning_gain as lg  # noqa: E402
from benchmarks.runner import EXIT_INSUFFICIENT, EXIT_OK  # noqa: E402

LOG_REL = (".memory", "review_log.jsonl")
REPORT_REL = (".memory", "learning_gain_report.md")


# ════════════════════════════════════════════════════════════════
# 夹具构造（全部落在 tmp_path 内，绝不触碰真实工作区）
# ════════════════════════════════════════════════════════════════

def _ev(ts, subject, title, rating):
    """构造一行复测日志（中性化数据）。"""
    return json.dumps(
        {"ts": ts, "subject": subject, "title": title, "rating": rating},
        ensure_ascii=False,
    )


def _write_review_log(root, lines):
    p = root.joinpath(*LOG_REL)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _write_mistake_file(root, subject_dir, cards, container="错题本"):
    """写一个错题本文件；cards 为 ``(日期, 标题, 错因或None, 状态或None)``。

    错因/状态传 ``None`` 表示不写该行（覆盖「未标注 / 未知状态」分支）。
    """
    p = root / subject_dir / container / "错题记录_测试.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for d, title, error_type, status in cards:
        blocks.append(f"## 📌 [{d}] {title}")
        if error_type is not None:
            blocks.append(f"- **错因分类**：`{error_type}`")
        if status is not None:
            blocks.append(f"- **掌握状态**：`{status}`")
        blocks.append("")
    p.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    return p


def _write_config(root, history):
    p = root / "ky_config.json"
    p.write_text(
        json.dumps({"api_key": "sk-test-fake", "completion_history": history},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def _seed_full_workspace(root):
    """三类数据源各给一条可用记录（周 2026-W39）。"""
    _write_review_log(root, [
        _ev("2026-09-21 09:00:00", "math", "测试题甲", "good"),
        _ev("2026-09-22 09:30:00", "math", "测试题乙", "again"),
    ])
    _write_mistake_file(root, "01-math", [
        ("2026-09-01", "测试题丙", "概念漏洞", "[待复测]"),
    ])
    _write_config(root, {"2026-09-21": {"rate": 0.5, "total": 10, "completed": 5}})
    return root


# ════════════════════════════════════════════════════════════════
# 第 1 层：常量与数据源口径
# ════════════════════════════════════════════════════════════════

def test_constants_and_exit_codes():
    assert lg.RATINGS == ("again", "hard", "good", "easy")
    assert lg.PASS_RATINGS == ("good", "easy")
    assert lg.GAIN_REPORT_REL == (".memory", "learning_gain_report.md")
    # 退出码复用 runner 常量（全仓口径一致）
    assert lg.EXIT_OK == EXIT_OK == 0
    assert lg.EXIT_INSUFFICIENT == EXIT_INSUFFICIENT == 2


def test_review_log_missing_file_returns_empty(tmp_path):
    events, invalid = lg.collect_review_events(tmp_path / ".memory" / "review_log.jsonl")
    assert events == []
    assert invalid == []


# ════════════════════════════════════════════════════════════════
# 第 2 层：周聚合与通过口径
# ════════════════════════════════════════════════════════════════

def test_week_key_iso_cross_year(tmp_path):
    """2025-12-29（ISO 属 2026-W01）与 2026-01-01 必须聚合到同一周。"""
    _write_review_log(tmp_path, [
        _ev("2025-12-29 10:00:00", "math", "测试题甲", "good"),
        _ev("2026-01-01 10:00:00", "math", "测试题乙", "again"),
        _ev("2026-09-22 10:00:00", "math", "测试题丙", "easy"),
    ])
    events, invalid = lg.collect_review_events(tmp_path.joinpath(*LOG_REL))
    assert invalid == []
    assert [e["date"] for e in events] == [
        date(2025, 12, 29), date(2026, 1, 1), date(2026, 9, 22)
    ]

    m = lg.compute_review_trend(events)
    assert m.status == "ok" and m.ok is True
    assert m.kind == "review_trend"
    assert m.weeks == 2
    rows = m.data["rows"]
    assert [r["week"] for r in rows] == ["2026-W01", "2026-W39"]
    # 跨年两条落在同一周，且该周各评级分布精确
    assert (rows[0]["total"], rows[0]["passed"]) == (2, 1)
    assert (rows[0]["again"], rows[0]["hard"], rows[0]["good"], rows[0]["easy"]) == (1, 0, 1, 0)
    assert rows[1]["week"] == "2026-W39" and rows[1]["total"] == 1 and rows[1]["passed"] == 1


def test_pass_rule_counts_only_good_easy(tmp_path):
    """2 passed / 4 total = 0.5：again/hard 不计入通过。"""
    _write_review_log(tmp_path, [
        _ev("2026-09-21 08:00:00", "math", "测试题甲", "good"),
        _ev("2026-09-21 08:10:00", "math", "测试题乙", "easy"),
        _ev("2026-09-21 08:20:00", "politics", "测试题丙", "again"),
        _ev("2026-09-21 08:30:00", "politics", "测试题丁", "hard"),
    ])
    events, invalid = lg.collect_review_events(tmp_path.joinpath(*LOG_REL))
    m = lg.compute_review_trend(events, invalid)
    assert m.ok and m.weeks == 1
    assert m.data["events"] == 4
    assert m.data["pass_rule"] == "good/easy"
    row = m.data["rows"][0]
    assert row["week"] == "2026-W39"
    assert (row["total"], row["passed"], row["pass_rate"]) == (4, 2, 0.5)
    assert (row["again"], row["hard"], row["good"], row["easy"]) == (1, 1, 1, 1)
    # 字段名与类型契约
    assert isinstance(row["pass_rate"], float)


def test_rating_normalized_case_insensitive(tmp_path):
    _write_review_log(tmp_path, [_ev("2026-09-22 10:00:00", "math", "测试题甲", "GOOD")])
    events, invalid = lg.collect_review_events(tmp_path.joinpath(*LOG_REL))
    assert invalid == []
    assert events[0]["rating"] == "good"


def test_review_trend_single_week_emits_note():
    """仅 1 周数据时给出「趋势判读意义有限」的 note（诚实边界）。"""
    m = lg.compute_review_trend(
        [{"date": date(2026, 9, 22), "subject": "math", "title": "测试题甲", "rating": "good"}]
    )
    assert m.weeks == 1
    assert "仅 1 周数据" in m.data["note"]


# ════════════════════════════════════════════════════════════════
# 第 3 层：坏行容错
# ════════════════════════════════════════════════════════════════

def test_review_log_bad_lines_skipped_and_counted(tmp_path):
    """坏行跳过并计数，有效行仍统计；有有效事件时 status 仍为 ok。"""
    _write_review_log(tmp_path, [
        _ev("2026-09-22 08:00:00", "math", "测试题甲", "good"),            # 1 有效
        "",                                                                # 2 空行（静默跳过）
        '{"ts": "2026-09-22 08:05:00", "subject": "math"',                 # 3 JSON 损坏
        _ev("2026-09-22 08:10:00", "math", "测试题乙", "easy"),            # 4 有效
        '{"ts": "2026/09/22 08:15:00", "rating": "good"}',                 # 5 ts 不可解析
        '{"ts": "2026-09-22 08:20:00", "rating": "unknown"}',              # 6 rating 非法
        "[1, 2]",                                                          # 7 顶层非对象
        _ev("2026-09-22 08:25:00", "math", "测试题丙", "again"),           # 8 有效
    ])
    events, invalid = lg.collect_review_events(tmp_path.joinpath(*LOG_REL))

    assert len(events) == 3
    assert [e["title"] for e in events] == ["测试题甲", "测试题乙", "测试题丙"]
    assert [c for c, _ in invalid] == ["line:3", "line:5", "line:6", "line:7"]
    assert invalid[0][1].startswith("JSON 解析失败")
    assert invalid[1][1] == "ts 不可解析: '2026/09/22 08:15:00'"
    assert invalid[2][1] == "rating 非法: 'unknown'"
    assert invalid[3][1] == "顶层不是对象: list"

    m = lg.compute_review_trend(events, invalid)
    assert m.ok and m.status == "ok"
    assert m.data["events"] == 3
    row = m.data["rows"][0]
    assert (row["total"], row["passed"], row["pass_rate"]) == (3, 2, round(2 / 3, 4))
    # 坏行原样带入 data.invalid（报告层会显示"已跳过"提示）
    assert [x["line"] for x in m.data["invalid"]] == ["line:3", "line:5", "line:6", "line:7"]


def test_review_trend_all_invalid_is_insufficient(tmp_path):
    _write_review_log(tmp_path, ["{坏", '{"ts": "", "rating": "again"}'])
    events, invalid = lg.collect_review_events(tmp_path.joinpath(*LOG_REL))
    assert events == [] and len(invalid) == 2
    m = lg.compute_review_trend(events, invalid)
    assert m.status == "insufficient" and m.ok is False
    assert "尚无复测事件" in m.reason
    assert "另有 2 行不可解析已跳过" in m.reason
    assert m.data["invalid"] == [
        {"line": "line:1", "reason": invalid[0][1]},
        {"line": "line:2", "reason": "ts 不可解析: ''"},
    ]


# ════════════════════════════════════════════════════════════════
# 第 4 层：错因分组与跨日复发
# ════════════════════════════════════════════════════════════════

def test_mistake_records_extract_and_glob_both_dirs(tmp_path):
    """两种目录命名（错题本 / 错题与长难句本）都要被扫到，字段契约精确。"""
    _write_mistake_file(tmp_path, "01-math", [
        ("2026-09-01", "测试题甲", "概念漏洞", "[待复测]"),
    ])
    _write_mistake_file(tmp_path, "02-politics", [
        ("2026-09-02", "测试题乙", "计算失误", "[已掌握]"),
    ], container="错题与长难句本")

    records = lg.collect_mistake_records(tmp_path)
    assert len(records) == 2
    by_subject = {r["subject"]: r for r in records}
    assert set(by_subject) == {"01-math", "02-politics"}
    assert by_subject["01-math"] == {
        "subject": "01-math",
        "date": "2026-09-01",
        "title": "测试题甲",
        "error_type": "概念漏洞",
        "status": "[待复测]",
        "file": "错题记录_测试.md",
    }
    assert by_subject["02-politics"]["error_type"] == "计算失误"
    assert by_subject["02-politics"]["status"] == "[已掌握]"


def test_mistake_recurrence_days_and_flag(tmp_path):
    """两条不同日期的「概念漏洞」→ recurring=True/days=2；单日「计算失误」→ False。"""
    _write_mistake_file(tmp_path, "01-math", [
        ("2026-09-01", "测试题甲", "概念漏洞", "[待复测]"),
        ("2026-09-03", "测试题乙", "概念漏洞", "[待复测]"),
        ("2026-09-02", "测试题丙", "计算失误", "[已掌握]"),
    ])
    records = lg.collect_mistake_records(tmp_path)
    m = lg.compute_mistake_recurrence(records)

    assert m.ok and m.kind == "mistake_recurrence"
    assert m.data["total_records"] == 3
    assert m.data["skipped"] == 0
    rows = {r["error_type"]: r for r in m.data["rows"]}
    assert rows["概念漏洞"]["recurring"] is True
    assert rows["概念漏洞"]["days"] == 2
    assert rows["概念漏洞"]["count"] == 2
    assert rows["概念漏洞"]["first"] == "2026-09-01"
    assert rows["概念漏洞"]["last"] == "2026-09-03"
    assert rows["概念漏洞"]["statuses"] == {"[待复测]": 2}
    assert rows["计算失误"]["recurring"] is False
    assert rows["计算失误"]["days"] == 1
    assert m.data["recurring_types"] == ["概念漏洞"]


def test_mistake_group_order_follows_mistake_types(tmp_path):
    """分组顺序 = MISTAKE_TYPES 原序优先，未知错因（含未标注）按出现序排后。"""
    # 文件内出现序刻意与期望输出序相反
    _write_mistake_file(tmp_path, "01-math", [
        ("2026-09-01", "测试题甲", "自拟错因", "[待复测]"),
        ("2026-09-02", "测试题乙", None, None),          # 未标注错因 / 未知状态
        ("2026-09-03", "测试题丙", "计算失误", "[待复测]"),
        ("2026-09-04", "测试题丁", "概念漏洞", "[待复测]"),
    ])
    m = lg.compute_mistake_recurrence(lg.collect_mistake_records(tmp_path))
    assert [r["error_type"] for r in m.data["rows"]] == [
        "概念漏洞", "计算失误", "自拟错因", "未标注"
    ]
    rows = {r["error_type"]: r for r in m.data["rows"]}
    assert rows["未标注"]["statuses"] == {"未知": 1}
    assert rows["未标注"]["count"] == 1


def test_mistake_recurrence_bad_dates_skipped(tmp_path):
    """日期不可解析的记录计入 skipped，不影响其余记录聚合。"""
    records = [
        {"subject": "01-math", "date": "2026-09-01", "title": "测试题甲",
         "error_type": "概念漏洞", "status": "[待复测]", "file": "x.md"},
        {"subject": "01-math", "date": "坏日期", "title": "测试题乙",
         "error_type": "概念漏洞", "status": "[待复测]", "file": "x.md"},
    ]
    m = lg.compute_mistake_recurrence(records)
    assert m.ok
    assert m.data["skipped"] == 1
    assert m.data["total_records"] == 1
    assert m.data["rows"][0]["count"] == 1
    assert m.data["rows"][0]["recurring"] is False

    # 全部日期不可解析 → insufficient（如实不可聚合）
    m2 = lg.compute_mistake_recurrence([records[1]])
    assert m2.status == "insufficient" and m2.ok is False
    assert "日期均不可解析" in m2.reason
    assert m2.data["skipped"] == 1


# ════════════════════════════════════════════════════════════════
# 第 5 层：完成率按周加权
# ════════════════════════════════════════════════════════════════

def test_completion_week_rate_is_weighted_not_averaged():
    """同周 total=10/completed=5 与 total=10/completed=3 → 8/20 = 0.4。"""
    hist = {
        "2026-09-21": {"rate": 0.9, "total": 10, "completed": 5},
        "2026-09-22": {"rate": 0.1, "total": 10, "completed": 3},
    }
    m = lg.compute_completion_trend(hist)
    assert m.ok and m.kind == "completion_trend"
    assert m.weeks == 1
    assert m.data["days"] == 2
    row = m.data["rows"][0]
    assert row["week"] == "2026-W39"
    assert (row["days"], row["total"], row["completed"]) == (2, 20, 8)
    assert row["rate"] == 0.4
    # 逐日 rate 字段（0.9/0.1，均值 0.5）不得被直接平均
    assert row["rate"] != 0.5

    # 不等权重的对照：加权 8/40=0.2，简单平均 (0.5+0.1)/2=0.3
    hist2 = {
        "2026-09-21": {"rate": 0.5, "total": 10, "completed": 5},
        "2026-09-22": {"rate": 0.1, "total": 30, "completed": 3},
    }
    row2 = lg.compute_completion_trend(hist2).data["rows"][0]
    assert (row2["total"], row2["completed"], row2["rate"]) == (40, 8, 0.2)
    assert row2["rate"] != 0.3


def test_completion_history_collect_filters_and_tolerates(tmp_path):
    """非日期键被过滤；文件缺失 / JSON 损坏 / 结构不符 → 空 dict。"""
    cfg = tmp_path / "ky_config.json"
    cfg.write_text(json.dumps({
        "completion_history": {
            "2026-09-21": {"rate": 0.5, "total": 10, "completed": 5},
            "note": "不是日期键",
            "2026/09/22": {"rate": 0.1},
        }
    }, ensure_ascii=False), encoding="utf-8")
    assert lg.collect_completion_history(cfg) == {
        "2026-09-21": {"rate": 0.5, "total": 10, "completed": 5}
    }

    assert lg.collect_completion_history(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{不是 JSON", encoding="utf-8")
    assert lg.collect_completion_history(broken) == {}
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"completion_history": [1, 2]}), encoding="utf-8")
    assert lg.collect_completion_history(wrong) == {}


def test_completion_rate_fallback_and_skipped():
    """total=0 时回退到逐日 rate 均值；日期键不可解析计入 skipped。"""
    m = lg.compute_completion_trend({
        "2026-09-21": {"rate": 0.25, "total": 0, "completed": 0},
        "坏日期": {"rate": 0.9, "total": 3, "completed": 3},
    })
    assert m.ok
    assert m.data["skipped"] == 1
    assert m.data["days"] == 1
    row = m.data["rows"][0]
    assert (row["week"], row["total"], row["rate"]) == ("2026-W39", 0, 0.25)


# ════════════════════════════════════════════════════════════════
# 第 6 层：空数据 / 退出码 / 落盘 / safe 模式
# ════════════════════════════════════════════════════════════════

def test_empty_workspace_all_insufficient_exit_2(tmp_path):
    report = lg.build_report(tmp_path)
    assert report.saved_to == ""
    assert len(report.metrics) == 3
    assert [m.kind for m in report.metrics] == [
        "review_trend", "mistake_recurrence", "completion_trend"
    ]
    assert all(m.status == "insufficient" for m in report.metrics)
    assert report.any_ok() is False
    reasons = [m.reason for m in report.metrics]
    assert "尚无复测事件" in reasons[0]
    assert "暂无错题记录" in reasons[1]
    assert "无 completion_history" in reasons[2]

    report2, code = lg.run_learning_gain(save=False, root=tmp_path)
    assert code == EXIT_INSUFFICIENT == 2
    assert report2.any_ok() is False
    assert report2.saved_to == ""
    # 不落盘路径下不得产生任何文件
    assert not tmp_path.joinpath(*REPORT_REL).exists()


def test_workspace_with_data_exit_0(tmp_path):
    _seed_full_workspace(tmp_path)
    report, code = lg.run_learning_gain(save=False, root=tmp_path)
    assert code == EXIT_OK == 0
    assert report.any_ok() is True
    assert all(m.ok for m in report.metrics)
    assert report.generated_at  # 非空时间戳
    assert report.saved_to == ""


def test_save_writes_report_under_memory(tmp_path):
    assert not lg.is_read_only_mode(), "前置：本用例需非只读模式（safe 模式不落盘）"
    _seed_full_workspace(tmp_path)
    report, code = lg.run_learning_gain(save=True, root=tmp_path)

    target = tmp_path.joinpath(*REPORT_REL)
    assert code == EXIT_OK == 0
    assert report.saved_to != ""
    assert Path(report.saved_to) == target
    assert Path(report.saved_to).parent.name == ".memory"
    assert target.exists()

    text = target.read_text(encoding="utf-8")
    assert "# 学习增益代理指标报告" in text
    # 三个 ## 节标题（含指标名）
    for idx, name in enumerate(
        ["错题复测通过率（周趋势）", "同类错因复发", "计划完成率（周）"], start=1
    ):
        assert f"## {idx}. {name}" in text
    assert "| 2026-W39 | 2 | 1 | 50% | 1 | 0 | 1 | 0 |" in text
    # [设计] 报告正文不含自我引用页脚：落盘状态由调用方（renderer / saved_to 字段）
    # 展示；若正文写路径会出现"文件里没有、打印时却有"的不一致（曾如此，已删）。
    assert "报告文件" not in report.to_markdown()

    # 序列化契约：整份报告可 JSON 落盘
    dumped = json.dumps(report.to_dict(), ensure_ascii=False)
    assert json.loads(dumped)["metrics"][0]["kind"] == "review_trend"
    assert json.loads(dumped)["saved_to"] == report.saved_to


def test_save_report_propagates_write_gate_denial(tmp_path, monkeypatch):
    """写闸门拒绝时 save_report 必须抛 PermissionDeniedError（不静默写盘）。"""
    report = lg.build_report(tmp_path)

    def _denied(op, target=""):
        raise lg.PermissionDeniedError("模拟：严格只读模式拒绝写入")

    monkeypatch.setattr(lg, "guard_write", _denied)
    target = tmp_path.joinpath(*REPORT_REL)
    with pytest.raises(lg.PermissionDeniedError):
        lg.save_report(report, target)
    assert not target.exists()


def test_save_permission_race_fallback(tmp_path, monkeypatch):
    """闸门竞态兜底：save_report 抛 PermissionDeniedError → saved_to 为空、不算错误。"""
    _seed_full_workspace(tmp_path)

    def _denied(report, path):
        raise lg.PermissionDeniedError("模拟：写入瞬间被闸门拒绝")

    monkeypatch.setattr(lg, "save_report", _denied)
    report, code = lg.run_learning_gain(save=True, root=tmp_path)
    assert report.saved_to == ""
    assert code == EXIT_OK == 0
    assert not tmp_path.joinpath(*REPORT_REL).exists()


def test_safe_mode_does_not_save(tmp_path, monkeypatch):
    """严格只读模式：save=True 也不落盘，saved_to 为空，退出码与不落盘时一致。"""
    _seed_full_workspace(tmp_path)
    monkeypatch.setattr(lg, "is_read_only_mode", lambda: True)

    report, code = lg.run_learning_gain(save=True, root=tmp_path)
    assert report.saved_to == ""
    assert code == EXIT_OK == 0
    assert not tmp_path.joinpath(*REPORT_REL).exists()
    assert not (tmp_path / ".memory" / "learning_gain_report.md").exists()

    _, code_no_save = lg.run_learning_gain(save=False, root=tmp_path)
    assert code == code_no_save == EXIT_OK


# ════════════════════════════════════════════════════════════════
# 第 7 层：隔离元测试
# ════════════════════════════════════════════════════════════════

#: 无参调用检测（模式串经正则转义，源码内不出现裸调用字面量，避免自匹配）
_NOARG_CALL_RE = re.compile(r"\brun_learning_gain\s*\(\s*\)")
#: 带模块前缀的调用标记（拼接构造，避免元测试把自己算成调用点）
_CALL_MARK = "lg." + "run_learning_gain("


def test_meta_no_real_workspace_access():
    """元测试：本文件不得无参调用被测入口，且所有调用必须显式传 root。"""
    src = Path(__file__).read_text(encoding="utf-8")

    assert not _NOARG_CALL_RE.search(src), "禁止无参调用（会读/写真实工作区）"
    for forbidden in ("考研" + "学习chain", "Desk" + "top", "河南" + "农业大学"):
        assert forbidden not in src, f"测试源码不得出现真实路径/身份：{forbidden}"

    calls = [ln.strip() for ln in src.splitlines() if _CALL_MARK in ln]
    assert calls, "元测试失效：未在源码中定位到任何被测入口调用"
    for ln in calls:
        assert "root=" in ln, f"调用未显式传 root=tmp_path：{ln}"

    # 所有落盘断言都锚定 tmp_path（本文件不得出现对真实 .memory 的写入）
    assert "tmp_path" in src


# ════════════════════════════════════════════════════════════════
# 第 8 层：用户可见性守护（ky --help / REPL 指令大盘 / handler 自处理 help）
# ════════════════════════════════════════════════════════════════


def test_ky_help_lists_gain_and_rag(capsys):
    """``ky help`` 的子命令清单必须列出 gain 与 rag —— 功能藏起来 = 用户找不到。"""
    from cli.commands import system as system_cmd

    system_cmd._cmd_help(["help"])
    out = capsys.readouterr().out
    assert "rag <关键词>" in out, "ky help 未列出 rag"
    assert "gain [--no-save]" in out, "ky help 未列出 gain"


def test_command_palette_lists_gain_and_rag():
    """REPL 指令大盘必须含 /rag 与 /gain。"""
    from cli.repl import renderer

    cmds = [c for _title, rows in renderer._palette_sections(False) for c, _d in rows]
    assert any(c.startswith("/rag") for c in cmds), "指令大盘未列 /rag"
    assert any(c.startswith("/gain") for c in cmds), "指令大盘未列 /gain"


def test_handlers_with_own_help_include_rag_gain():
    """rag/gain 的 handler 自处理 --help（详细 _USAGE），不得被 dispatch 拦成简版。"""
    from cli import dispatch

    assert {"rag", "gain"} <= set(dispatch._HANDLERS_WITH_OWN_HELP)


# ════════════════════════════════════════════════════════════════
# 第 9 层：端到端实测缺陷的回归守护（三处，均有实测复现）
# ════════════════════════════════════════════════════════════════


def test_report_path_is_excluded_from_publish():
    """报告落盘路径必须被隐私闸门排除（``.memory`` 任意深度；清扫层曾漏判）。"""
    from privacy_policy import should_publish

    assert should_publish(".memory/learning_gain_report.md") is False
    assert should_publish(".memory/review_log.jsonl") is False
    # 嵌套同判（实测曾对 01-数学/.memory/** 放行不删）
    assert should_publish("01-数学/.memory/learning_gain_report.md") is False


def test_repl_gain_branch_honors_no_save():
    """REPL ``/gain`` 分支必须解析 ``--no-save``（曾硬编码落盘、静默忽略参数）。"""
    src = (ROOT / "tools" / "cli" / "repl" / "loop.py").read_text(encoding="utf-8")
    assert '"--no-save" not in arg.split()' in src
    assert "run_learning_gain(save=_save)" in src
    assert "run_learning_gain(save=True)" not in src


def test_markdown_table_escapes_pipe():
    """错因名含 ``|`` 时表格必须转义（曾把列截断、整行错位）。"""
    recs = [
        {"subject": "01-数学", "date": "2026-09-22", "title": "t",
         "error_type": "审题|偏差", "status": "[待复测]"},
        {"subject": "01-数学", "date": "2026-09-23", "title": "t",
         "error_type": "审题|偏差", "status": "[待复测]"},
    ]
    m = lg.compute_mistake_recurrence(recs)
    md = lg.GainReport(generated_at="t", metrics=[m]).to_markdown()
    rows = [ln for ln in md.splitlines() if ln.startswith("|") and "审题" in ln]
    assert rows, "未渲染出错因行"
    assert "审题\\|偏差" in rows[0]
    assert "审题|偏差" not in rows[0]


def test_internal_review_doc_not_publishable():
    """外部评审/方案类文档必须被隐私闸门排除（C6 推前实测：曾被导出到公开副本）。

    用中性样本名钉住 ``*评审*.md`` 模式（不在公开测试里引用真实内部文档名）。
    """
    from privacy_policy import is_internal_doc, should_publish

    name = "示例端评审与升级改造方案.md"
    assert is_internal_doc(name) is True
    assert should_publish(name) is False
    assert should_publish(f"子目录/{name}") is False
    # 不误伤：正常发布文档仍放行
    assert should_publish("DESIGN.md") is True
