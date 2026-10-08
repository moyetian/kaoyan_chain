# -*- coding: utf-8 -*-
"""2026-10-08 P2 批次「考纲学情」组回归测试（六领域审查 §3 考纲 6 条）。

逐条对应（详见桌面分析报告目录《kaoyan_chain_六领域深度审查报告_2026-10-08.md》§3）：

1. 报告指标不守恒：总数（原始列表）与明细（去重后比对基准）口径不一致，
   同一考点重复出现时 added+modified+unchanged ≠ total_new（实测 4 vs 3）。
2. 同级要求变更标「放宽」：掌握→熟练掌握、理解→会 被方向性谎报为降级。
3. format_diff_markdown 内嵌 12s LLM 调用：纯格式化函数带网络副作用、
   失败静默（except: pass）。修复后：可注入（"" 纯离线）/ 缓存去重 / 失败显式降级。
4. /gain 纯数据表无判读建议：修复后按确定性规则补「判读建议」（markdown + 终端）。
5. 系统提示组装无预算/优先级：修复后按优先级执行总长预算（关键规范段不截）。
6. /diff 新增考点「处方」列全量同一句：修复后按考查级别分层。

隔离约定：全部 tmp_path / 合成文本 / monkeypatch；不触碰真实工作区数据文件；
LLM 一律打桩（真实仓库 is_llm_configured=True，绝不打真实接口）。
"""

import importlib
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _gen():
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator
    return SyllabusDiffGenerator()


def _patch_llm(monkeypatch, ok=True, reply="1. **桩建议**" + "长" * 40):
    """打桩 llm_client 两入口；返回调用计数列表。"""
    import tools.llm_client as llm
    calls = []
    monkeypatch.setattr(llm, "is_llm_configured", lambda *a, **k: True)

    if ok:
        def _ok(*a, **k):
            calls.append(1)
            return reply
        monkeypatch.setattr(llm, "chat_completion", _ok)
    else:
        def _boom(*a, **k):
            calls.append(1)
            raise RuntimeError("合成：模拟接口超时")
        monkeypatch.setattr(llm, "chat_completion", _boom)
    return calls


def _patch_error_logger(monkeypatch, due=None, records=None):
    """打桩 error_logger 两个导入别名（脚本式/包式），隔离真实错题库。"""
    stubs = {}
    if due is not None:
        stubs["get_due_reviews"] = lambda subject=None, max_count=5: list(due)[:max_count]
    if records is not None:
        stubs["scan_error_records"] = lambda subject=None: list(records)
    for name in ("skills.error_logger", "tools.skills.error_logger"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        for attr, fn in stubs.items():
            monkeypatch.setattr(mod, attr, fn, raising=False)


# ═══════════════════════ 条目 1：报告指标守恒 ═══════════════════════

def test_p2_1_metrics_conserved_with_duplicates():
    """同考点重复出现时守恒恒成立（修复前实测 4 vs 3、3 vs 2）。"""
    gen = _gen()
    rep = gen.compare_texts(
        old_text=("## 一、章节\n"
                  "- **掌握**：考点A、考点B\n"
                  "- **理解**：考点A\n"),          # 原始 3 点，去重后 2 点
        new_text=("## 一、章节\n"
                  "- **掌握**：考点A、考点B、考点C\n"
                  "- **了解**：考点C\n"))          # 原始 4 点，去重后 3 点
    m = rep["metrics"]
    assert m["total_new"] == m["added_count"] + m["modified_count"] + m["unchanged_count"]
    assert m["total_old"] == m["removed_count"] + m["modified_count"] + m["unchanged_count"]
    assert (m["total_old"], m["total_new"]) == (2, 3)
    # 明细列表与四个分类计数一致
    counts = {"ADDED": 0, "REMOVED": 0, "MODIFIED": 0, "UNCHANGED": 0}
    for d in rep["diff_items"]:
        counts[d.change_type] += 1
    assert counts["ADDED"] == m["added_count"]
    assert counts["REMOVED"] == m["removed_count"]
    assert counts["MODIFIED"] == m["modified_count"]
    assert counts["UNCHANGED"] == m["unchanged_count"]


def test_p2_1_gate_sample_counts_unchanged():
    """阴性对照：无重复考点的常规样本计数不变（对齐门禁 ky_suite 20-2/20-3 口径）。"""
    gen = _gen()
    rep = gen.compare_texts(
        old_text=("## 一、高等数学\n### 1. 函数、极限、连续\n"
                  "- **掌握**：极限四则运算法则、等价无穷小代换\n"
                  "- **理解**：闭区间连续函数零点定理\n"
                  "- **了解**：函数奇偶性与周期性\n"
                  "### 2. 一元函数微分学\n"
                  "- **掌握**：洛必达法则求极限、导数物理意义\n"
                  "- **理解**：拉格朗日中值定理\n"),
        new_text=("## 一、高等数学\n### 1. 函数、极限、连续\n"
                  "- **掌握**：极限四则运算法则、等价无穷小代换\n"
                  "- **掌握**：闭区间连续函数零点定理\n"
                  "### 2. 一元函数微分学\n"
                  "- **掌握**：洛必达法则求极限\n"
                  "- **掌握**：泰勒公式与麦克劳林展开\n"
                  "- **理解**：拉格朗日中值定理\n"))
    m = rep["metrics"]
    assert m["total_old"] == 7 and m["total_new"] == 6
    assert m["added_count"] >= 1 and m["removed_count"] >= 1 and m["modified_count"] >= 1


# ═══════════════════════ 条目 2：同级要求变更方向 ═══════════════════════

def test_p2_2_same_level_change_not_relaxed():
    """同级要求词变更如实标「同级调整」，不得标「放宽」（修复前实测 2 处误标）。"""
    gen = _gen()
    rep = gen.compare_texts(
        old_text="## 一、章节\n- **掌握**：考点甲\n- **理解**：考点乙\n",
        new_text="## 一、章节\n- **熟练掌握**：考点甲\n- **会**：考点乙\n")
    details = [d.detail for d in rep["diff_items"] if d.change_type == "MODIFIED"]
    assert len(details) == 2, details
    assert all("放宽" not in t for t in details), details
    assert any("同级调整" in t and "熟练掌握" in t for t in details), details
    assert any("同级调整" in t and "【会】" in t for t in details), details


def test_p2_2_up_and_down_directions_preserved():
    """阴性对照：真实升降级方向判定不被削弱（提升/放宽各归其位）。"""
    gen = _gen()
    rep = gen.compare_texts(
        old_text="## 一、章节\n- **了解**：考点甲\n- **掌握**：考点乙\n",
        new_text="## 一、章节\n- **掌握**：考点甲\n- **了解**：考点乙\n")
    details = [d.detail for d in rep["diff_items"] if d.change_type == "MODIFIED"]
    assert any("提升" in t and "考点甲" not in t for t in details), details
    assert any("放宽" in t for t in details), details


# ═══════════════════════ 条目 3：LLM 副作用 / 失败静默 ═══════════════════════

def _rep_with_added(gen):
    return gen.compare_texts(
        old_text="## 一、章节\n- **掌握**：考点A\n",
        new_text="## 一、章节\n- **掌握**：考点A、考点B\n")


def test_p2_3_offline_injection_skips_llm(monkeypatch):
    """注入 ""（纯离线）→ 零 LLM 调用，回落通用模板。"""
    calls = _patch_llm(monkeypatch)
    gen = _gen()
    md = gen.format_diff_markdown(_rep_with_added(gen), strategic_advice="")
    assert calls == [], "注入离线模式仍调用了 LLM"
    assert "新增考点零遗漏" in md


def test_p2_3_injected_advice_used(monkeypatch):
    """调用方注入的建议优先使用，且不触发 LLM。"""
    calls = _patch_llm(monkeypatch)
    gen = _gen()
    md = gen.format_diff_markdown(_rep_with_added(gen),
                                  strategic_advice="1. **合成注入建议**：按计划执行。")
    assert calls == []
    assert "合成注入建议" in md


def test_p2_3_default_backcompat_and_cache(monkeypatch):
    """默认路径兼容（生成建议）；同一报告二次格式化复用缓存、不重复调 LLM。"""
    calls = _patch_llm(monkeypatch)
    gen = _gen()
    rep = _rep_with_added(gen)
    md1 = gen.format_diff_markdown(rep)
    assert len(calls) == 1 and "桩建议" in md1
    md2 = gen.format_diff_markdown(rep)
    assert len(calls) == 1, "同报告二次格式化重复调用了 LLM"
    assert "桩建议" in md2


def test_p2_3_save_after_format_no_second_call(monkeypatch, tmp_path):
    """format 后紧跟 save（agent 工具链路形态）：落盘不产生第二次 LLM 调用。"""
    calls = _patch_llm(monkeypatch)
    gen = _gen()
    rep = _rep_with_added(gen)
    gen.format_diff_markdown(rep)
    out = gen.save_diff_report(rep, output_path=tmp_path / "合成研报.md")
    assert len(calls) == 1, f"落盘又调了一次 LLM（共 {len(calls)} 次）"
    text = Path(out).read_text(encoding="utf-8")
    assert "桩建议" in text


def test_p2_3_failure_explicit_degradation(monkeypatch, capsys):
    """LLM 失败不再静默：打印降级告警，且研报照常回落通用模板。"""
    _patch_llm(monkeypatch, ok=False)
    gen = _gen()
    md = gen.format_diff_markdown(_rep_with_added(gen))
    out = capsys.readouterr().out
    assert "失败" in out and "降级" in out, f"失败路径无显式告警: {out!r}"
    assert "新增考点零遗漏" in md


def test_p2_3_zero_points_still_skip_llm(monkeypatch):
    """P0-10 口径回归：0 考点（parse_warning）不得调用 LLM。"""
    calls = _patch_llm(monkeypatch)
    gen = _gen()
    rep = gen.compare_texts(old_text="", new_text="")
    md = gen.format_diff_markdown(rep)
    assert calls == [], "0 点数据仍调用了 LLM"
    assert "比对结果不可信" in md


# ═══════════════════════ 条目 6：新增考点处方分层 ═══════════════════════

def test_p2_6_prescription_levels_differ():
    """处方按考查级别分层：掌握类高危 / 理解类再认 / 了解类速览。"""
    gen = _gen()
    assert "高危" in gen.added_prescription("掌握")
    assert "高危" in gen.added_prescription("熟练应用")
    assert gen.added_prescription("掌握") != gen.added_prescription("了解")
    assert "速览" in gen.added_prescription("了解")
    # 未登记要求词按「理解」档（与 compare_points 缺省级别一致）
    assert gen.added_prescription("合成要求词") == gen.added_prescription("理解")


def test_p2_6_added_table_rows_differ():
    """Markdown 新增考点表：不同级别的新增行处方不同（修复前全量同一句）。"""
    gen = _gen()
    rep = gen.compare_texts(
        old_text="## 一、章节\n- **掌握**：旧考点\n",
        new_text=("## 一、章节\n- **掌握**：旧考点、新增考点甲\n"
                  "- **了解**：新增考点乙\n"))
    md = gen.format_diff_markdown(rep, strategic_advice="")
    row_a = [ln for ln in md.splitlines() if ln.startswith("|") and "新增考点甲" in ln]
    row_b = [ln for ln in md.splitlines() if ln.startswith("|") and "新增考点乙" in ln]
    assert row_a and row_b, "新增考点行未渲染"
    assert "高危" in row_a[0] and "速览" in row_b[0], (row_a[0], row_b[0])


# ═══════════════════════ 条目 4：/gain 判读建议 ═══════════════════════

def _ok_review_metric(events):
    from benchmarks import learning_gain as lg
    return lg.compute_review_trend(events)


def test_p2_4_gain_markdown_contains_advice():
    """报告 Markdown 含「判读建议」（修复前只有数据表）。"""
    from benchmarks import learning_gain as lg

    m = lg.compute_review_trend([
        {"date": date(2026, 9, 21), "subject": "math", "title": "测试题甲", "rating": "again"},
        {"date": date(2026, 9, 22), "subject": "math", "title": "测试题乙", "rating": "hard"},
    ])
    assert m.ok and m.data.get("advice"), "复测指标未附判读建议"
    assert "偏低" in m.data["advice"]
    md = lg.GainReport(generated_at="t", metrics=[m]).to_markdown()
    assert "**判读建议**" in md


def test_p2_4_gain_terminal_contains_advice(capsys):
    """终端渲染（ky gain / /gain 共用）含「判读建议」行。"""
    from benchmarks import learning_gain as lg
    from cli.repl.renderer import print_learning_gain

    m = lg.compute_completion_trend(
        {"2026-09-21": {"rate": 0.9, "total": 10, "completed": 9}})
    print_learning_gain(lg.GainReport(generated_at="t", metrics=[m]))
    out = capsys.readouterr().out
    assert "判读建议" in out
    assert "执行稳定" in out


def test_p2_4_gain_advice_rules_boundaries():
    """三条指标的判读规则边界（低/高、复发/无复发）逐一钉住。"""
    from benchmarks import learning_gain as lg

    # 复测：两周数据 → 环比提升 + 高位保持良好
    m = lg.compute_review_trend([
        {"date": date(2026, 9, 14), "subject": "math", "title": "t1", "rating": "again"},
        {"date": date(2026, 9, 15), "subject": "math", "title": "t2", "rating": "again"},
        {"date": date(2026, 9, 21), "subject": "math", "title": "t3", "rating": "good"},
        {"date": date(2026, 9, 22), "subject": "math", "title": "t4", "rating": "easy"},
    ])
    assert "提升" in m.data["advice"] and "保持良好" in m.data["advice"]

    # 完成率：低档提示下调任务量
    m_low = lg.compute_completion_trend(
        {"2026-09-21": {"rate": 0.4, "total": 10, "completed": 4}})
    assert "下调" in m_low.data["advice"]

    # 错因复发：跨日复发 → 专项变式；单日 → 无复发
    recs = [
        {"subject": "01-math", "date": "2026-09-01", "title": "t1",
         "error_type": "概念漏洞", "status": "[待复测]"},
        {"subject": "01-math", "date": "2026-09-03", "title": "t2",
         "error_type": "概念漏洞", "status": "[待复测]"},
    ]
    m_rec = lg.compute_mistake_recurrence(recs)
    assert "复发错因" in m_rec.data["advice"]
    m_rec1 = lg.compute_mistake_recurrence(recs[:1])
    assert "暂无跨日复发" in m_rec1.data["advice"]


def test_p2_4_gain_insufficient_no_advice(tmp_path, capsys):
    """阴性对照：数据不足的指标不编建议（仍如实显示原因）。"""
    from benchmarks import learning_gain as lg
    from cli.repl.renderer import print_learning_gain

    report = lg.build_report(tmp_path)
    assert all(m.status == "insufficient" for m in report.metrics)
    md = report.to_markdown()
    assert "判读建议" not in md
    assert "数据不足" in md
    print_learning_gain(report)
    assert "判读建议" not in capsys.readouterr().out


# ═══════════════════════ 条目 5：系统提示总长预算 ═══════════════════════

def _engine(tmp_path):
    from tools.agent.context_engine import ContextEngine
    return ContextEngine(workspace_root=tmp_path, active_subject="eng")


def test_p2_5_over_budget_truncates_data_keeps_contract(tmp_path, monkeypatch):
    """巨型大纲超预算：数据段截断并插显式标记；规范段完整保留。"""
    from tools.agent.context_engine import SYSTEM_PROMPT_CHAR_BUDGET

    _patch_error_logger(monkeypatch, due=[], records=[])
    (tmp_path / "AGENTS.md").write_text(
        "# 合成总控协议\n合成总控条款甲\n", encoding="utf-8")
    s = tmp_path / "02-英语"
    s.mkdir(parents=True)
    (s / "考试大纲.md").write_text(
        "# 合成大纲\n" + "- **掌握**：合成考点X\n" * 8000, encoding="utf-8")

    prompt = _engine(tmp_path).build_system_prompt()
    assert len(prompt) <= SYSTEM_PROMPT_CHAR_BUDGET + 500, \
        f"超预算未截断（{len(prompt)} 字符）"
    assert "因系统提示总长预算超限已截断" in prompt
    # 规范类关键段完整保留（不因截断丢行为契约）
    assert "合成总控条款甲" in prompt
    assert "Agent 智能体工具调用行为规范" in prompt
    assert "作答契约与引用规范" in prompt
    # 巨型大纲被截到头部（不再全量注入：原始 8000 行，截后显著减少但仍保留开头）
    _kept = prompt.count("合成考点X")
    assert 100 < _kept < 3000, f"截断比例异常（保留 {_kept} 行 / 原 8000 行）"


def test_p2_5_under_budget_unchanged(tmp_path, monkeypatch):
    """阴性对照：常规小工作区不触发截断（预算内逐字节与旧实现同构）。"""
    _patch_error_logger(monkeypatch, due=[], records=[])
    (tmp_path / "AGENTS.md").write_text(
        "# 合成总控协议\n合成总控条款甲\n", encoding="utf-8")
    prompt = _engine(tmp_path).build_system_prompt()
    assert "预算超限" not in prompt
    assert "合成总控条款甲" in prompt


def test_p2_5_critical_never_cut(tmp_path, monkeypatch):
    """关键段本身超预算时允许整体超出（软上限），但绝不被截断。"""
    from tools.agent.context_engine import SYSTEM_PROMPT_CHAR_BUDGET

    _patch_error_logger(monkeypatch, due=[], records=[])
    body = "# 合成总控协议\n" + "合成条款行内容\n" * 9000 + "合成末行标记\n"
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")

    prompt = _engine(tmp_path).build_system_prompt()
    assert len(body) > SYSTEM_PROMPT_CHAR_BUDGET
    assert "合成末行标记" in prompt, "关键段被截断（不得发生）"
    # 无可截的数据段 → 不产生截断标记（软上限允许整体超出）
    assert "预算超限" not in prompt
