# -*- coding: utf-8 -*-
"""UT3 三沙箱测试批次 · P2 修复回归（批 1–4 精选，含阴性对照）。

来源：2026-09-30 三角色（材料力学 / 应用数学 / 经济学）真实用户旅程测试，
P2 清单合并去重后的关键项：

  批 1  P2-1  查漏报告 nomath 残留数学行 + 专业课雷达载体缺位（cli/repl/router.py）
        打卡完成率滞后（cli/shared.py mark_today_task_done 后不重算）
  批 2  P2-2  看板科目目标分硬编码（05-考研看板/web/config.py）
        P2-3  雷达基线未建立显示绿色「指纹正常」假信号（web/radar.py 三态）
  批 3  P2-4  ingest LLM 补全无上限（skills/material_ingestion.py 预算控制）
        P2-5  满分字段双路径漂移（「每小题 N 分」声明解析）
  批 4  P2-6  思考标签泄漏到回复（agent/loop.py _strip_think_tags）
        P2-7  safe 宣称「禁止文件写入」与审计快照写入的张力（文案精确化）
        P2-8  watcher 标题提取大小写漏滤（「ENGLISH」当通知标题）
        P2-9  「四科」文案在 2/3 科方案下残留（动态化 + 摸底合计占位符）

全部用例只读 / 打桩：不落盘真实工作区、不联网、不触碰真实 ky_config.json。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
for _p in (str(ROOT), str(ROOT / "tools"), str(DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.skills.material_ingestion import (  # noqa: E402
    MaterialIngestionPipeline, _per_question_score_from_section,
)
from tools.study_planner import (  # noqa: E402
    baseline_total_label, print_study_plan_summary, subject_count_label,
)
from tools.intelligence import watcher as watcher_mod  # noqa: E402
from tools.agent.loop import _strip_think_tags  # noqa: E402
from web.radar import _school_watch_state, build_radar_html  # noqa: E402


# ═════════════════════════ 批 1：查漏报告与打卡 ═════════════════════════

def test_p2_weakness_scan_nomath_hides_math_row(tmp_path, monkeypatch):
    """不考数学方案下查漏报告不得出现数学行；专业课无雷达时明确提示而非静默跳过。"""
    from tools.cli.repl import router

    monkeypatch.setattr(router, "ROOT", tmp_path)
    monkeypatch.setattr(router, "load_config",
                        lambda: {"study_plan": {"math_key": "none", "math_name": "不考数学"}})
    monkeypatch.setattr(router, "error_logger", None)  # 不触碰真实错题本

    report = router.build_weakness_scan_report()
    assert "数学" not in report, f"nomath 下仍列出数学行:\n{report}"
    assert "专业课" in report
    # 04 科雷达载体全缺时应显式提示（此前整块静默跳过）
    assert "尚未建立薄弱点雷达" in report


def test_p2_weakness_scan_pro_reads_study_archive(tmp_path, monkeypatch):
    """专业课薄弱点载体与 error_logger 同源：学情档案.md 里的卡点要进查漏。"""
    from tools.cli.repl import router

    monkeypatch.setattr(router, "ROOT", tmp_path)
    monkeypatch.setattr(router, "load_config", lambda: {})
    monkeypatch.setattr(router, "error_logger", None)
    pro_dir = tmp_path / "04-专业课" / "_状态"
    pro_dir.mkdir(parents=True)
    (pro_dir / "学情档案.md").write_text(
        "## 薄弱点\n"
        "| 模块名称 | 评级 | 卡点描述 |\n"
        "| --- | --- | --- |\n"
        "| 材料力学 | C | 弯曲正应力公式适用条件记不牢 |\n", encoding="utf-8")

    report = router.build_weakness_scan_report()
    assert "弯曲正应力" in report, f"学情档案卡点未进查漏:\n{report}"


def test_p2_mark_done_recomputes_completion_rate(tmp_path, monkeypatch):
    """打卡后立即重算今日完成率（此前要等下次 SessionEnd 钩子才更新）。"""
    from tools.cli import shared
    # [双导入] shared 函数内 `import study_planner` 拿的是顶层名，打桩必须打同一对象
    import study_planner as sp_top

    monkeypatch.setattr(shared, "ROOT", tmp_path)
    task_dir = tmp_path / "02-英语" / "_状态"
    task_dir.mkdir(parents=True)
    (task_dir / "今日任务.md").write_text(
        "| 模块 | 内容 | 时长 | 完成状态 |\n| --- | --- | --- | --- |\n"
        "| 阅读 | 2018 Text 2 精读 | 40分钟 | [ ] |\n"
        "| 单词 | 核心词 Unit 3 | 20分钟 | [x] |\n"
        "| 写作 | 小作文功能句模板 | 30分钟 | [ ] |\n", encoding="utf-8")

    calls = []
    monkeypatch.setattr(sp_top, "record_daily_completion",
                        lambda rate, total, completed: calls.append((rate, total, completed)))

    ok, info = shared.mark_today_task_done("2018 Text 2")
    assert ok and "已完成打卡" in info
    # 打卡后 2/3 完成（若仍走「等 SessionEnd 兜底」的旧路径，此调用为空）
    assert calls == [(66.7, 3, 2)], f"完成率未即时重算: {calls}"


# ═════════════════════════ 批 2：看板目标分与雷达三态 ═════════════════════════

def test_p2_parse_target_from_plan_text():
    """看板科目目标分必须来自考生方案文本，而不是固定常量。"""
    from web.config import _parse_target

    assert _parse_target("95+ 分", 110) == 95
    assert _parse_target("120-130 分", 120) == 120
    assert _parse_target("", 70) == 70
    assert _parse_target(None, 70) == 70


def test_p2_watch_state_pending_baseline_is_not_green():
    """雷达三态：基线未建立（抓取失败）不得判成 UNCHANGED 绿色正常。"""
    assert _school_watch_state({"updates": [{"title": "x"}]}) == "UPDATED"
    assert _school_watch_state({"baseline_complete": True, "last_hash": "abc"}) == "UNCHANGED"
    assert _school_watch_state({"baseline_complete": False, "last_hash": ""}) == "PENDING_BASELINE"
    assert _school_watch_state({}) == "PENDING_BASELINE"
    # 外部巡检 JSON 自带 status 优先（兼容旧形状）
    assert _school_watch_state({"status": "WATCHING"}) == "WATCHING"


def test_p2_radar_sanitized_aggregate_pending_baseline(tmp_path, monkeypatch):
    """脱敏聚合徽章（手机端发布形态）同样按三态聚合，不再无条件绿色「正常」。"""
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "1")
    mem = tmp_path / ".memory"
    mem.mkdir()
    (mem / "admission_watch.json").write_text(json.dumps({
        "10001": {"name": "某校", "baseline_complete": False, "last_hash": ""},
    }, ensure_ascii=False), encoding="utf-8")

    html = build_radar_html(tmp_path)
    assert "基线未建立" in html, "脱敏聚合徽章仍是假绿色正常"
    assert "某校" not in html, "脱敏模式下院校名泄漏"


# ═════════════════════════ 批 3：ingest 预算与满分解析 ═════════════════════════

def test_p2_ingest_llm_budget_cap(tmp_path, capsys):
    """ingest 的 LLM 补全按预算封顶，调用前提示候选题数与上限。"""
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    p._llm_configured = lambda: True
    calls = []
    p.enrich_rubric_with_llm = lambda chunk, subject="pro": calls.append(chunk.number)

    doc = "\n".join(f"{i}. 简述第 {i} 个核心考点及其现实意义。" for i in range(1, 13))
    p.ingest_text(doc, subject="pro", source_name="回归预算",
                  target_path=tmp_path / "out.md", llm_budget=3)
    out = capsys.readouterr().out
    assert len(calls) == 3, f"预算=3 实际补全 {len(calls)} 次"
    assert "12 道题缺少步骤采分点" in out and "本次补全前 3 道" in out


def test_p2_ingest_llm_off_switch(tmp_path, capsys):
    """llm_enrich=False（--no-llm）时绝不触发任何 LLM 调用。"""
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    p._llm_configured = lambda: True
    calls = []
    p.enrich_rubric_with_llm = lambda chunk, subject="pro": calls.append(chunk.number)

    doc = "\n".join(f"{i}. 简述第 {i} 个核心考点。" for i in range(1, 6))
    p.ingest_text(doc, subject="pro", source_name="回归关闸",
                  target_path=tmp_path / "out2.md", llm_enrich=False)
    assert calls == []


def test_p2_per_question_score_declaration():
    """试卷分段标题的「每题分值」解析：声明优先，A×B=C 形式带乘积校验。"""
    assert _per_question_score_from_section("三、简答题（每小题 5 分，共 20 分）") == 5
    assert _per_question_score_from_section("一、选择题（每题2分，共20分）") == 2
    assert _per_question_score_from_section("二、简答题（5 × 8 = 40 分）") == 8
    # 乘积不符（5×8≠45）时不采信该声明，回退 0 由调用方按题型默认
    assert _per_question_score_from_section("二、简答题（5 × 8 = 45 分）") == 0
    # 仅有总分声明无法推算单题分，返回 0（不得把总分当末题满分）
    assert _per_question_score_from_section("四、论述题（共 30 分）") == 0


# ═════════════════════════ 批 4：思考标签 / safe 文案 / watcher / 四科 ═════════════════════════

def test_p2_strip_think_tags():
    """模型思考标签必须从用户可见回复中清除（完整块与孤立残标）。"""
    assert _strip_think_tags("答案是 42") == "答案是 42"
    assert _strip_think_tags("<think>推理过程</think>答案是 42") == "答案是 42"
    assert _strip_think_tags("<thinking>a\nb</thinking>\n\n最终回复") == "\n\n最终回复"
    assert _strip_think_tags("前半</think>后半") == "前半后半"
    assert _strip_think_tags("<THINK>X</THINK>ok") == "ok"


def test_p2_watcher_noise_filter_case_insensitive():
    """页面英文导航词任意大小写都不得当通知标题（「ENGLISH」漏滤修复）。"""
    html = ("<a href='/en'>ENGLISH</a><a href='/e2'>English</a>"
            "<a href='/n'>关于 2026 年硕士研究生招生考试网上报名的通知</a>")
    w = watcher_mod.AdmissionWatcher.__new__(watcher_mod.AdmissionWatcher)
    titles = w._extract_recent_titles(html)
    assert not any(t.lower() == "english" for t in titles), titles
    assert any("网上报名" in t for t in titles), titles


def test_p2_safe_help_discloses_audit_exception(capsys):
    """safe 模式帮助文案须如实说明审计快照/日志例外，不再宣称绝对禁止写入。"""
    from tools.cli.commands import system as syscmd

    with contextlib.redirect_stdout(io.StringIO()) as buf:
        syscmd._cmd_help([])
    out = buf.getvalue()
    assert "配置留证快照/审计日志除外" in out
    assert "禁止文件写入与执行" not in out


def test_p2_subject_count_label_four_modes():
    """「N科」标签按方案实际科目数（常规4 / nomath3 / mode_b 4 / mode_c 2）。"""
    assert subject_count_label({"math_name": "数学一 (301)"}) == "四科"
    assert subject_count_label({"math_key": "none", "math_name": "不考数学"}) == "三科"
    assert subject_count_label({"exam_mode": "mode_b", "pro2_name": "801 高等代数"}) == "四科"
    assert subject_count_label({"exam_mode": "mode_c", "pro_name": "199管理类综合能力"}) == "两科"


def test_p2_baseline_total_label_boundaries():
    """摸底合计：>300 的值剔除、取句末摸底值、定性描述回退「见各科摸底」。"""
    assert baseline_total_label(
        {"eng_baseline": "六级 430，摸底 60 分", "pol_baseline": "已听网课，摸底 58 分",
         "pro_baseline": "数学系本科，摸底 75 分"},
        ["eng_baseline", "pol_baseline", "pro_baseline"]) == "约 193 分"
    assert baseline_total_label({"eng_baseline": "四级 480 分，摸底 58 分"},
                                ["eng_baseline"]) == "约 58 分"
    assert baseline_total_label({"pol_baseline": "未启动"},
                                ["pol_baseline"]) == "见各科摸底"


def test_p2_summary_nomath_renders_three_subjects():
    """nomath 建档总结页全程「三科」口径，无「四科」残留。"""
    plan = {
        "math_key": "none", "math_name": "不考数学", "math_hours": 0.0,
        "eng_name": "英语一 (201)", "eng_hours": 2.0, "eng_baseline": "摸底 60 分",
        "eng_weakness": "长难句", "eng_books": "真题", "pol_hours": 1.0,
        "pol_baseline": "摸底 58 分", "pol_weakness": "帽子题", "pol_books": "精讲精练",
        "pro_name": "601 数学分析", "pro_hours": 2.0, "pro_baseline": "摸底 75 分",
        "pro_weakness": "级数", "pro_books": "教材", "school": "曲阜师范大学",
        "major": "070100 数学", "exam_date": "2026-12-19", "days_left": 80,
        "total_hours": 5.0,
    }
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        print_study_plan_summary(plan)
    out = buf.getvalue()
    assert "三科提分矩阵与薄弱项雷达" in out
    assert "今日首日三科任务清单" in out
    assert "随时查看今日三科任务与完成状态" in out
    assert "四科" not in out


# ═════════════════════════ 批 5：建档缺字段（appmath P2-6） ═════════════════════════

def test_p2_wizard_seeds_pol_name_at_build(monkeypatch):
    """P2-6：建档向导采集阶段即写入 study_plan.pol_name（规范名「思想政治理论」），
    不再依赖 ky mount 的 material_scanner 兜底补齐（三沙箱实测：建档后未 mount 前
    ky_config 缺该字段，mount 输出「pol_name 旧:（无）」）。既有值不覆盖。"""
    import tools.study_planner as sp_mod

    monkeypatch.setattr(sp_mod, "apply_study_plan", lambda *a, **k: None)
    monkeypatch.setattr(sp_mod, "print_study_plan_summary", lambda *a, **k: None)
    _base = {"math_key": "none", "math_name": "不考数学",
             "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.0}

    plan = sp_mod.run_study_plan_wizard(interactive=False, preset_data=dict(_base))
    assert plan["pol_name"] == "思想政治理论"

    # 既有值（非空）不被覆盖
    plan2 = sp_mod.run_study_plan_wizard(
        interactive=False, preset_data={**_base, "pol_name": "思想政治理论 (已定制)"})
    assert plan2["pol_name"] == "思想政治理论 (已定制)"


def test_p2_knowledge_map_skips_md_separator_lines(tmp_path):
    """D-12 兜底修复的回归：markdown 分隔线（---/***）不得被收成「未标注」考点。

    首版兜底把以 -/* 开头的分隔线也计入考点（真实快照实测 eng/pol 各混入
    2 条 name="---"）；普通 bullet 的兜底收集必须保留。"""
    from tools.skills.knowledge_map import build_knowledge_map

    d = tmp_path / "01-数学"
    d.mkdir(parents=True)
    # 注：正文行需 ≥5 条且每条 ≥8 字符，否则会被 _is_placeholder_syllabus 判为占位模板
    (d / "考试大纲.md").write_text(
        "# 大纲\n\n## 一、极限\n\n- 数列极限的严格定义\n- 函数极限的性质与运算\n- 无穷小量与无穷大量阶\n\n---\n\n"
        "## 二、导数\n\n- 导数定义的几何意义\n***\n- 基本初等函数求导法则\n- 复合函数链式求导法则\n",
        encoding="utf-8")
    m = build_knowledge_map("math", root=tmp_path)
    names = [p["name"] for ch in m["chapters"] for p in ch["points"]]
    assert "---" not in names and "***" not in names, f"分隔线被误收: {names}"
    assert "数列极限的严格定义" in names, f"普通 bullet 兜底失效: {names}"
    assert "复合函数链式求导法则" in names
