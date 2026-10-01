# -*- coding: utf-8 -*-
"""K8 批次回归 · 工具输出预算（单一真源 + execute_tool 后置兜底）。

背景（agent 内核修复方案 K8）：工具结果体积治理的两层 ——
  L1 各工具自带的截断上限收敛为 ``TOOL_OUTPUT_LIMITS`` 单一真源
     （数值与历史字面量逐字一致，本批只搬家、不改值）；
  L2 ``execute_tool`` 出口的统一兜底 ``apply_output_budget``：结果超过
     ``ToolDefinition.budget``（默认 400k）→ 截断返回值 + 完整原文落盘
     ``.memory/tool_outputs/<日期>/``（原子写；失败静默降级「未落盘」）。

本文件锁定（不得回退）：
1. TOOL_OUTPUT_LIMITS 六个数值逐字锁定（防搬家时改值），且 tools_impl
   引用的是**同一对象**（单一真源，非复制）；
2. 直通语义：未超预算 / budget<=0 / 非法 budget → 原样返回（零行为变化）；
3. 超预算：前 budget 字符 + 截断说明；完整原文落盘逐字节（含被截断部分）；
4. 落盘失败（IO 异常 / 无 workspace_root）→ 静默降级「未落盘」，
   截断照常返回（预算兜底绝不变成新的故障点）；
5. 接线：ToolDefinition.budget 默认 400k；execute_tool 出口超预算被
   截断 + 落盘（budget 改小端到端验证）。

全程离线、tmp_path 隔离，零网络。
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.output_budget import (  # noqa: E402
    DEFAULT_TOOL_OUTPUT_BUDGET,
    TOOL_OUTPUT_LIMITS,
    TOOL_OUTPUTS_DIR,
    apply_output_budget,
)


# ── 1. 单一真源与数值锁定 ──────────────────────────────────────────────


def test_limits_values_locked():
    """六个截断上限与历史字面量逐字一致（K8 只搬家、不改值）。"""
    assert TOOL_OUTPUT_LIMITS["fetch_url_chars"] == 3000
    assert TOOL_OUTPUT_LIMITS["git_diff_chars"] == 2000
    assert TOOL_OUTPUT_LIMITS["run_command_stdout_chars"] == 1500
    assert TOOL_OUTPUT_LIMITS["run_command_stderr_chars"] == 800
    assert TOOL_OUTPUT_LIMITS["read_file_pdf_chars"] == 12000
    assert TOOL_OUTPUT_LIMITS["read_file_text_lines"] == 2000
    assert len(TOOL_OUTPUT_LIMITS) == 6


def test_single_source_of_truth():
    """tools_impl 引用同一 dict 对象（单一真源；改一处全体生效）。"""
    from tools.agent import tools_impl
    assert tools_impl.TOOL_OUTPUT_LIMITS is TOOL_OUTPUT_LIMITS
    assert tools_impl.DEFAULT_TOOL_OUTPUT_BUDGET == DEFAULT_TOOL_OUTPUT_BUDGET
    assert DEFAULT_TOOL_OUTPUT_BUDGET == 50 * 1024


# ── 2. 直通语义（零行为变化） ──────────────────────────────────────────


def test_under_limit_passthrough(tmp_path):
    """未超预算（含恰好等于）→ 原样返回，不产生任何落盘。"""
    text = "短文本"
    assert apply_output_budget(text, "t", workspace_root=tmp_path) == text
    exact = "x" * 100
    assert apply_output_budget(exact, "t", budget=100, workspace_root=tmp_path) == exact
    assert not (tmp_path / ".memory").exists(), "未超预算不得产生任何落盘"


def test_zero_or_invalid_budget_passthrough(tmp_path):
    """budget<=0 表示「不限」；非法值回落默认 → 均原样返回。"""
    text = "x" * 1000
    assert apply_output_budget(text, "t", budget=0, workspace_root=tmp_path) == text
    assert apply_output_budget(text, "t", budget=-5, workspace_root=tmp_path) == text
    assert apply_output_budget(text, "t", budget="bad", workspace_root=tmp_path) == text


# ── 3. 超预算：截断 + 完整原文落盘 ─────────────────────────────────────


def test_truncates_and_saves_verbatim(tmp_path):
    """超预算 → 前 budget 字符 + 说明；完整原文落盘逐字节。"""
    full = "样" * 60 + "本" * 40          # 100 字符
    out = apply_output_budget(full, "read_file", call_id="call_abc",
                              budget=30, workspace_root=tmp_path)
    assert out.startswith(full[:30])
    assert "超预算" in out and "已截断" in out and "完整输出已落盘" in out
    day = date.today().strftime("%Y-%m-%d")
    dest = tmp_path.joinpath(*TOOL_OUTPUTS_DIR) / day / "read_file_call_abc.txt"
    assert dest.exists(), "完整原文必须落盘"
    assert dest.read_text(encoding="utf-8") == full, \
        "落盘内容必须逐字节等于完整原文（含被截断部分）"
    assert ".memory/tool_outputs/" in out, "说明中给出可续读的相对路径"


# ── 4. 落盘失败：静默降级 ──────────────────────────────────────────────


def test_save_failure_degrades_silently(tmp_path, monkeypatch):
    """IO 异常（如严格只读模式）→「未落盘」降级；截断照常、不抛异常。"""
    from tools.agent import output_budget as ob

    def boom(*a, **k):
        raise PermissionError("严格只读模式")

    monkeypatch.setattr(ob, "atomic_write_text", boom)
    full = "x" * 100
    out = ob.apply_output_budget(full, "t", budget=10, workspace_root=tmp_path)
    assert out.startswith("x" * 10)
    assert "落盘失败" in out
    assert not (tmp_path / ".memory").exists()


def test_no_workspace_root_degrades():
    """无 workspace_root → 同样降级「未落盘」（不抛）。"""
    out = apply_output_budget("x" * 100, "t", budget=10, workspace_root=None)
    assert "落盘失败" in out


# ── 5. 接线：ToolDefinition.budget 与 execute_tool 出口 ────────────────


def test_tool_definition_default_budget():
    from tools.agent.tools_impl import ToolDefinition
    td = ToolDefinition("t", "d", {}, lambda: "", 0)
    assert td.budget == 50 * 1024
    td2 = ToolDefinition("t", "d", {}, lambda: "", 0, budget=123)
    assert td2.budget == 123


def test_execute_tool_budget_wiring(tmp_path):
    """execute_tool 出口接线（端到端）：budget 改小 → 截断 + 落盘。"""
    from tools.agent.loop import AgentRunner

    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    try:
        sample = tmp_path / "样本.md"
        sample.write_text("样本行内容\n" * 100, encoding="utf-8")
        runner.tool_registry.tools["read_file"].budget = 50

        out = runner.tool_registry.execute_tool(
            "read_file", {"path": "样本.md"}, interactive=False, call_id="c9")
        assert "超预算" in out, "超预算结果必须被出口兜底截断"
        day = date.today().strftime("%Y-%m-%d")
        dest = tmp_path.joinpath(*TOOL_OUTPUTS_DIR) / day / "read_file_c9.txt"
        assert dest.exists(), "完整原文必须落盘（文件名含 call_id）"
        saved = dest.read_text(encoding="utf-8")
        assert saved.startswith(out[:50]), "落盘的是完整原文（前缀与截断结果一致）"
        assert len(saved) > 50
    finally:
        runner.close()
