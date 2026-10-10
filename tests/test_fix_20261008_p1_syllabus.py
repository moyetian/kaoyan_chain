# -*- coding: utf-8 -*-
"""2026-10-08 P1 批次「考纲学情」组（K1-K6）回归测试。

逐条对应（详见桌面分析报告目录《kaoyan_chain_六领域深度审查报告_2026-10-08.md》）：

- K1 知识点切分碎片化：括号内分隔符被切碎（math1 148 点中 24 断片）；
  非登记要求词（应用能力/熟练计算/熟悉）一律记「掌握」。
- K2 FSRS 历史重建失真：全按 good 快进，反复 hard 的弱项间隔高估约 5 倍
  （stage=2+good 快进 46 天 vs hard,hard 真实回放 9 天）。
- K3 完成率双实现：hooks 自带一套表格计数，与 task_parser 口径分歧
  （同一文件 33.3% vs 50%）。
- K4 疲劳检测口径：只开会话不勾选（completed=0）连续 2 天触发假警报。
- K5 学情不进上下文：完成率/到期复测/错因分布均不注入 agent 系统提示。
- K6 今日任务模板僵化：固定三行、零引用 FSRS；到期复测未进清单、无配速。

隔离约定：全部 tmp_path / monkeypatch；不触碰真实 ky_config.json、真实错题库、
真实任务文件；不发起任何网络请求；LLM 一律不调用（仅解析路径）。
"""

import importlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_first(names):
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


def _patch_error_logger(monkeypatch, due=None, records=None):
    """打桩 error_logger 两个导入别名（脚本式/包式）。"""
    stubs = {}
    if due is not None:
        stubs["get_due_reviews"] = lambda subject=None, max_count=5: list(due)[:max_count]
    if records is not None:
        stubs["scan_error_records"] = lambda subject=None: list(records)
    for name in ("skills.error_logger", "tools.skills.error_logger"):
        mod = _import_first((name,))
        if mod is None:
            continue
        for attr, fn in stubs.items():
            monkeypatch.setattr(mod, attr, fn, raising=False)


# ═══════════════════════ K1 考纲切分与要求词 ═══════════════════════

def _parse(text):
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator
    return SyllabusDiffGenerator().parse_syllabus(text)


def test_k1_bracket_protected_split_keeps_atomic_point():
    """括号内顿号不再切碎（修复前：产生「（零点定理」类断片）。"""
    pts = _parse("## 一、章节\n"
                 "- **掌握**：闭区间上连续函数的性质（零点定理、介值定理、最值定理）、"
                 "连续函数的四则运算；\n")
    texts = [p.text for p in pts]
    assert "闭区间上连续函数的性质（零点定理、介值定理、最值定理）" in texts, texts
    assert "连续函数的四则运算" in texts
    assert not any(t.count("（") != t.count("）") for t in texts), f"仍有断片: {texts}"


def test_k1_slash_inside_bracket_stays_intact():
    """括号内含斜杠/逗号的内容整体保留（math1「Taylor公式/Maclaurin展开」形态）。"""
    pts = _parse("## 一、章节\n"
                 "- **熟练应用**：微分中值定理（Rolle、Lagrange、Cauchy中值定理、"
                 "Taylor公式/Maclaurin展开）；\n")
    assert len(pts) == 1, [p.text for p in pts]
    assert "Taylor公式/Maclaurin展开）" in pts[0].text
    assert pts[0].requirement == "熟练应用"


def test_k1_builtin_math1_no_fragments():
    """真实内置 math1 大纲：断片清零（修复前 24 个未配对括号断片）。"""
    from tools.syllabus_manager import MATH_SYLLABI
    pts = _parse(MATH_SYLLABI["math1"]["content"])
    bad = []
    for p in pts:
        for a, b in (("（", "）"), ("(", ")"), ("【", "】")):
            if p.text.count(a) != p.text.count(b):
                bad.append(p.text)
                break
    assert not bad, f"仍有断片: {bad[:5]}"
    assert len(pts) > 100


def test_k1_non_registered_requirement_word_not_stamped_mastery():
    """非登记要求词不记「掌握」：如实保留词本身并原子切分（修复前记「掌握」）。"""
    pts = _parse("## 一、章节\n- **熟悉**：合成考点甲、合成考点乙\n")
    assert len(pts) == 2
    assert all(p.requirement == "熟悉" for p in pts), [p.requirement for p in pts]
    assert {p.text for p in pts} == {"合成考点甲", "合成考点乙"}


def test_k1_registered_application_words_are_parsed():
    """「应用能力/熟练计算」登记为要求词：不再被记「掌握」且整行原子切分。"""
    pts = _parse("## 一、章节\n"
                 "- **应用能力**：合成要点甲、合成要点乙、合成要点丙；\n"
                 "- **熟练计算**：合成运算甲、合成运算乙。\n")
    assert len(pts) == 5, [p.text for p in pts]
    assert sum(1 for p in pts if p.requirement == "应用能力") == 3
    assert sum(1 for p in pts if p.requirement == "熟练计算") == 2
    assert all(p.requirement != "掌握" for p in pts)


def test_k1_topic_label_head_unchanged():
    """阴性对照：考点名冒号头（词汇基准）行为不变，不因要求词改动回归。"""
    pts = _parse("## 一、章节\n1. **词汇基准**：掌握 5500 左右核心词汇及常见派生词；\n")
    assert len(pts) == 1
    assert "词汇基准" in pts[0].text
    assert pts[0].requirement == "掌握"


# ═══════════════════════ K2 FSRS 历史回放 ═══════════════════════

def test_k2_replay_fixes_hard_history_overestimate():
    """核心回归：hard,hard 历史回放 9 天；无历史仍为 good 快进 46 天（兼容）。"""
    from tools.fsrs_scheduler import compute_next_interval
    today = date(2026, 1, 1)
    assert compute_next_interval(2, "good", today)[2] == 46, "无历史行为被改变"
    assert compute_next_interval(2, "good", today, history=["hard", "hard"])[2] == 9
    assert compute_next_interval(2, "good", today, history=["good", "good"])[2] == 46


def test_k2_untrusted_history_falls_back():
    """不可信历史（长度不符/含 again/未知评级/非序列）保守回退快进路径。"""
    from tools.fsrs_scheduler import compute_next_interval
    today = date(2026, 1, 1)
    for bad in (["hard"], ["hard", "again"], ["hard", "乱"], "not-a-list"):
        assert compute_next_interval(2, "good", today, history=bad)[2] == 46, bad


def test_k2_review_log_fixture_roundtrip(tmp_path):
    """review_log 夹具：合法链提取真实序列；断链/终点不符/无文件 → None。"""
    from tools.fsrs_scheduler import compute_next_interval, load_review_history
    log = tmp_path / "review_log.jsonl"
    log.write_text(
        json.dumps({"subject": "math", "title": "合成错题", "stage_before": 0,
                    "rating": "hard"}, ensure_ascii=False) + "\n"
        + json.dumps({"subject": "eng", "title": "无关题", "stage_before": 0,
                      "rating": "good"}, ensure_ascii=False) + "\n"
        + "坏行{{{\n"
        + json.dumps({"subject": "math", "title": "合成错题", "stage_before": 1,
                      "rating": "hard"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    hist = load_review_history("math", "合成错题", 2, log_file=log)
    assert hist == ["hard", "hard"]
    assert compute_next_interval(2, "good", date(2026, 1, 1), history=hist)[2] == 9
    assert load_review_history("math", "合成错题", 3, log_file=log) is None
    assert load_review_history("math", "合成错题", 2, log_file=tmp_path / "无.md") is None


def test_k2_review_log_chain_guards_interleave(tmp_path):
    """同标题多卡交错事件被 stage 链校验拦截（防张冠李戴）。"""
    from tools.fsrs_scheduler import load_review_history
    log = tmp_path / "review_log.jsonl"
    events = [{"stage_before": 0, "rating": "good"}, {"stage_before": 0, "rating": "hard"},
              {"stage_before": 1, "rating": "good"}]
    log.write_text("".join(
        json.dumps({"subject": "math", "title": "同标题", **e}, ensure_ascii=False) + "\n"
        for e in events), encoding="utf-8")
    assert load_review_history("math", "同标题", 2, log_file=log) is None


def test_k2_again_resets_cycle(tmp_path):
    """again 重置后的当前周期：只回放重置之后的评级。"""
    from tools.fsrs_scheduler import load_review_history
    log = tmp_path / "review_log.jsonl"
    events = [{"stage_before": 0, "rating": "good"},
              {"stage_before": 1, "rating": "again"},
              {"stage_before": 0, "rating": "hard"}]
    log.write_text("".join(
        json.dumps({"subject": "math", "title": "重置题", **e}, ensure_ascii=False) + "\n"
        for e in events), encoding="utf-8")
    assert load_review_history("math", "重置题", 1, log_file=log) == ["hard"]


def test_k2_pinned_backward_compat():
    """既有钉住断言：0 档 good/again 与 stage 语义不变。"""
    from tools.fsrs_scheduler import compute_next_interval
    today = date(2026, 1, 1)
    assert compute_next_interval(0, "good", today) == (1, date(2026, 1, 3), 2)
    assert compute_next_interval(0, "again", today) == (0, date(2026, 1, 2), 1)


# ═══════════════════════ K3 完成率单一真源 ═══════════════════════

#: 含「幽灵行」（单格备注行）的任务文件：旧 hooks 口径 33.3%，task_parser 50%
_K3_TASK_TEXT = ("| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
                 "|---|---|---|---|\n"
                 "| 阅读 | 合成真题精读 | 30 分钟 | [x] |\n"
                 "| 单词 | 合成核心词复习 | 20 分钟 | [ ] |\n"
                 "| 备注：今日加油 |\n")


def test_k3_hooks_and_parser_same_numbers(tmp_path, monkeypatch):
    """hooks 完成率与 task_parser 数值一致（修复前 33.3% vs 50%）。"""
    from tools.state.task_parser import parse_task_lines

    d = tmp_path / "02-英语" / "_状态"
    d.mkdir(parents=True)
    (d / "今日任务.md").write_text(_K3_TASK_TEXT, encoding="utf-8")

    captured = []
    for name in ("study_planner", "tools.study_planner"):
        mod = _import_first((name,))
        if mod is not None:
            monkeypatch.setattr(mod, "record_daily_completion",
                                lambda **kw: captured.append(kw), raising=False)
    _patch_error_logger(monkeypatch, due=[])

    from tools.agent.hooks import HookManager
    HookManager(workspace_root=tmp_path).trigger_session_end({})

    items = parse_task_lines(_K3_TASK_TEXT)
    parser_rate = round(sum(1 for it in items if it.done) / len(items) * 100, 1)
    assert parser_rate == 50.0
    assert len(captured) == 1, "有任务时必须照常记录"
    assert captured[0]["total"] == 2, "幽灵行（单格备注）不得计为任务"
    assert captured[0]["rate"] == parser_rate == 50.0


def test_k3_negative_control_old_logic_would_differ():
    """阴性对照：旧「含竖线即计数」口径对同一文件给出 33.3%（判别力证明）。"""
    total = done = 0
    for l in _K3_TASK_TEXT.splitlines():
        if ("|" in l and not l.replace(" ", "").startswith("|---|")
                and "完成状态" not in l and "模块" not in l):
            total += 1
            if "[x]" in l.lower():
                done += 1
    assert total == 3 and round(done / total * 100, 1) == 33.3


def test_k3_list_style_rows_counted(tmp_path, monkeypatch):
    """列表式任务（- [x] …）不再被 hooks 漏计（task_parser 契约）。"""
    d = tmp_path / "04-专业课" / "_状态"
    d.mkdir(parents=True)
    (d / "今日任务.md").write_text("- [x] 合成任务甲\n- [ ] 合成任务乙\n",
                                   encoding="utf-8")
    captured = []
    for name in ("study_planner", "tools.study_planner"):
        mod = _import_first((name,))
        if mod is not None:
            monkeypatch.setattr(mod, "record_daily_completion",
                                lambda **kw: captured.append(kw), raising=False)
    _patch_error_logger(monkeypatch, due=[])
    from tools.agent.hooks import HookManager
    HookManager(workspace_root=tmp_path).trigger_session_end({})
    assert captured and captured[0]["total"] == 2 and captured[0]["completed"] == 1


# ═══════════════════════ K4 疲劳检测口径 ═══════════════════════

def test_k4_no_action_sessions_do_not_alert():
    """只开会话不勾选（completed=0）连续 2 天不得触发假警报。"""
    from tools.study_planner import check_fatigue_alert
    res = check_fatigue_alert({"completion_history": {
        "2026-09-01": {"rate": 0.0, "total": 3, "completed": 0},
        "2026-09-02": {"rate": 0.0, "total": 3, "completed": 0}}})
    assert res["alert"] is False, res


def test_k4_real_low_completion_still_alerts():
    """有真实勾选的低完成率连续 2 天仍触发（功能不被削弱）。"""
    from tools.study_planner import check_fatigue_alert
    res = check_fatigue_alert({"completion_history": {
        "2026-09-01": {"rate": 50.0, "total": 4, "completed": 2},
        "2026-09-02": {"rate": 40.0, "total": 5, "completed": 2}}})
    assert res["alert"] is True and res["consecutive_low_days"] == 2
    assert "防疲劳保障提醒" in res["message"]


def test_k4_legacy_records_without_completed_keep_behavior():
    """旧数据（缺 completed 字段）无法判定时保守视为有动作，行为不变。"""
    from tools.study_planner import check_fatigue_alert
    res = check_fatigue_alert({"completion_history": {
        "2026-09-09": {"rate": 10.0}, "2026-09-10": {"rate": 20.0}}})
    assert res["alert"] is True
    gap = check_fatigue_alert({"completion_history": {
        "2026-09-01": {"rate": 10.0}, "2026-09-10": {"rate": 20.0}}})
    assert gap["alert"] is False


# ═══════════════════════ K5 学情速览注入 ═══════════════════════

def _seed_overview_workspace(tmp_path):
    d = tmp_path / "02-英语" / "_状态"
    d.mkdir(parents=True)
    (d / "今日任务.md").write_text(_K3_TASK_TEXT, encoding="utf-8")


def test_k5_study_overview_injected(tmp_path, monkeypatch):
    """系统提示词含「学情速览」：完成率/到期复测/错因分布三行齐备。"""
    _seed_overview_workspace(tmp_path)
    _patch_error_logger(
        monkeypatch,
        due=[{"subject": "eng", "title": "合成题A"}, {"subject": "pro", "title": "合成题B"}],
        records=[{"error_type": "概念漏洞"}, {"error_type": "概念漏洞"},
                 {"error_type": "计算失误"}])
    from tools.agent.context_engine import ContextEngine
    prompt = ContextEngine(workspace_root=tmp_path, active_subject="eng").build_system_prompt()
    assert "学情速览" in prompt
    assert "今日任务完成率" in prompt and "英语 1/2（50%）" in prompt
    assert "到期复测错题：共 2 道" in prompt and "专业课 1" in prompt
    assert "概念漏洞 2" in prompt


def test_k5_no_data_no_section(tmp_path, monkeypatch):
    """阴性对照：无任何学情数据时整段不输出（不注入空壳段）。"""
    _patch_error_logger(monkeypatch, due=[], records=[])
    from tools.agent.context_engine import ContextEngine
    prompt = ContextEngine(workspace_root=tmp_path, active_subject="eng").build_system_prompt()
    assert "学情速览" not in prompt


# ═══════════════════════ K6 今日任务模板 ═══════════════════════

# [既有缺陷修复·2026-10-09] 天数原写死 ``days_left: 72`` 与断言 ``还剩 72 天``，
# 而实现是按 ``exam_date - 今天`` 现算的 —— 两者随日期漂移不一致（跑批当天算出
# 71 天，断言却写 72），使本用例成为与被测行为无关的日期地雷。
# 改为**从 exam_date 动态推导**期望值：既与实现同源，又永不再漂移。
_K6_EXAM_DATE = "2026-12-19"
_K6_PLAN = {"eng_name": "英语一 (201)", "eng_hours": 2.0, "eng_books": "暂未放置",
            "eng_weakness": "合成薄弱点",
            "days_left": (date.fromisoformat(_K6_EXAM_DATE) - date.today()).days,
            "exam_date": _K6_EXAM_DATE}


def test_k6_due_review_first_row_and_pacing(tmp_path, monkeypatch):
    """有到期错题：首行为「到期复测」+ 配速提示行；task_parser 契约兼容。"""
    _patch_error_logger(monkeypatch, due=[{"subject": "eng"}, {"subject": "eng"}])
    from tools.state.task_parser import parse_task_lines
    from tools.study_planner import ensure_subject_today_task
    res = ensure_subject_today_task(dict(_K6_PLAN), "eng", workspace_root=tmp_path)
    text = Path(res["path"]).read_text(encoding="utf-8")
    items = parse_task_lines(text)
    assert items and items[0].module == "到期复测", [it.module for it in items]
    assert len(items) == 4 and res["task_count"] == 4
    expected = (date.fromisoformat(_K6_EXAM_DATE) - date.today()).days
    assert "配速提示" in text and f"还剩 {expected} 天" in text and "日均量" in text


def test_k6_no_due_no_row_pacing_kept(tmp_path, monkeypatch):
    """无到期错题：不生成到期行（不虚增任务），配速提示仍在。"""
    _patch_error_logger(monkeypatch, due=[])
    from tools.state.task_parser import parse_task_lines
    from tools.study_planner import ensure_subject_today_task
    res = ensure_subject_today_task(dict(_K6_PLAN), "eng", workspace_root=tmp_path)
    text = Path(res["path"]).read_text(encoding="utf-8")
    items = parse_task_lines(text)
    assert len(items) == 3 and "到期复测" not in text
    assert "配速提示" in text and res["task_count"] == 3


def test_k6_same_day_file_preserved(tmp_path, monkeypatch):
    """阴性对照：当日文件已存在时原样保留（勾选/编辑不被覆盖）。"""
    _patch_error_logger(monkeypatch, due=[{"subject": "eng"}])
    from tools.study_planner import ensure_subject_today_task
    today_str = date.today().strftime("%Y-%m-%d")
    d = tmp_path / "02-英语" / "_状态"
    d.mkdir(parents=True)
    original = (f"# 今日英语任务 ({today_str})\n\n"
                f"| 模块 | 任务内容 | 预计用时 | 完成状态 |\n|---|---|---|---|\n"
                f"| 词汇破冰 | 考生自改任务 | 1 分钟 | [x] |\n")
    (d / "今日任务.md").write_text(original, encoding="utf-8")
    res = ensure_subject_today_task(dict(_K6_PLAN), "eng", workspace_root=tmp_path)
    assert res["status"] == "exists"
    assert (d / "今日任务.md").read_text(encoding="utf-8") == original
    assert res["task_count"] == 1
