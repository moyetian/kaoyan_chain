# -*- coding: utf-8 -*-
"""P3 回归测试：批改失分却未落库的兜底告警必须真的触发。

背景（多角色端到端测试审查报告 P3）：
    真实 LLM 判分时未调用 log_mistake 归档错题（模型行为），此时系统**唯一**的
    兜底是 tools/cli/agent/engine.py::_warn_if_mistake_not_archived 的告警。
    但该函数的失分 marker 只有连续字符串「扣分」，而真实批改输出是
    「可能被扣 **1分**」这类带空格/加粗的写法，导致告警长期静默失效 ——
    用户既没归档、也没被告知，错题闭环彻底断裂且无感知。

本测试锁定该行为：含「扣 N 分」等写法的批改回复，必须触发告警。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cli.agent import engine  # noqa: E402


def _run_warn(monkeypatch, capsys, user_input, reply, before=3, after=3):
    """以受控的错题条数调用兜底告警，返回 stdout。"""
    monkeypatch.setattr(engine, "_count_error_records", lambda _s: after)
    engine._warn_if_mistake_not_archived(user_input, reply, "pro", before)
    return capsys.readouterr().out


@pytest.mark.parametrize("reply", [
    "你漏掉了「和趋势」，可能被扣 **1分**。",       # 实测 mimo-v2.5 的真实输出
    "本步骤 [-1分]，请规范书写。",
    "扣 2 分：定义不完整。",
    "该答案不够完整，建议订正。",
    "⚠️ 此处遗漏了关键限定词。",
])
def test_warning_triggers_on_various_failure_wordings(monkeypatch, capsys, reply):
    """带数字/加粗/符号的失分写法都必须被识别为「失分」。"""
    out = _run_warn(monkeypatch, capsys, "交作业", reply)
    assert "未能写入错题本" in out, f"失分写法未被识别，兜底告警静默失效: {reply!r}"


def test_warning_triggers_on_literal_koufen(monkeypatch, capsys):
    """原有「扣分」写法不得回归。"""
    out = _run_warn(monkeypatch, capsys, "交作业", "本题扣分，请复盘。")
    assert "未能写入错题本" in out


def test_no_warning_when_mistake_archived(monkeypatch, capsys):
    """已归档（条数增加）时不得告警。"""
    out = _run_warn(monkeypatch, capsys, "交作业", "可能被扣 1分。", before=3, after=4)
    assert "未能写入错题本" not in out


def test_no_warning_on_clean_answer(monkeypatch, capsys):
    """全对且无失分标记时不得告警（避免误报）。"""
    out = _run_warn(
        monkeypatch, capsys, "交作业",
        "✅ 完全正确，本步骤 [+3分]，表述精准，继续保持！",
    )
    assert "未能写入错题本" not in out


def test_no_warning_when_not_grading(monkeypatch, capsys):
    """非批改场景（既无「交作业」也无采分点标记）不得告警。"""
    out = _run_warn(monkeypatch, capsys, "今天天气怎么样", "天气不错，适合背书。")
    assert "未能写入错题本" not in out
