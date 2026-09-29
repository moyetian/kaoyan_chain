# -*- coding: utf-8 -*-
"""[W11 告警科目指向] 「批改未归档」告警的科目改为「提问推断科目」。

背景（多角色实测）
------------------
工科用户（北邮 408）问专业课时 REPL 默认激活英语 → 告警建议
``ky exam eng --count 3 --save`` 科目错位，误导用户跑错误的补救路径。
推断逻辑本已存在于 ``query_llm_reply``（仅网关路径使用），本批抽为
``infer_subject_from_text`` 公用，loop.py 的计数与告警两处同科目使用。

全程离线：monkeypatch 计数函数，不读真实错题库。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cli.agent import engine  # noqa: E402


# ── 1. 推断函数 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("帮我看看这道英语阅读", "eng"),
    ("政治帽子题怎么背", "pol"),
    ("专业课的参考书目有哪些", "pro"),
    ("这道数学题怎么做", "math"),
    ("/eng 帮我批改", "eng"),
    ("/pol 多选题", "pol"),
    ("/pro 真题", "pro"),
    ("/math 极限", "math"),
])
def test_infer_subject_keywords(text, expected):
    assert engine.infer_subject_from_text(text, fallback="math") == expected


def test_infer_subject_fallback_when_no_keyword():
    assert engine.infer_subject_from_text("今天天气怎么样", fallback="eng") == "eng"
    assert engine.infer_subject_from_text("", fallback="pro") == "pro"


def test_infer_subject_default_fallback_is_math():
    assert engine.infer_subject_from_text("随便聊聊") == "math"


def test_infer_subject_priority_order():
    """多科目关键词同现时按既有优先级（英语→政治→专业课→数学）。"""
    assert engine.infer_subject_from_text("英语和数学都难", fallback="pro") == "eng"


# ── 2. 告警输出使用传入科目（计数同科目契约） ──────────────────────────


def _run_warn(monkeypatch, capsys, subject, user_input="交作业",
              reply="扣 2 分：定义不完整。", before=3, after=3):
    monkeypatch.setattr(engine, "_count_error_records", lambda _s: after)
    engine._warn_if_mistake_not_archived(user_input, reply, subject, before)
    return capsys.readouterr().out


@pytest.mark.parametrize("subject", ["pro", "math", "pol", "eng"])
def test_warn_message_uses_given_subject(monkeypatch, capsys, subject):
    out = _run_warn(monkeypatch, capsys, subject)
    assert "未能写入错题本" in out
    assert f"ky exam {subject} --count 3 --save" in out


def test_warn_subject_from_inference_end_to_end(monkeypatch, capsys):
    """408 提问场景：推断为 pro → 告警建议 ky exam pro（而非激活的 eng）。"""
    subject = engine.infer_subject_from_text("这道 408 专业课真题帮我批改", fallback="eng")
    out = _run_warn(monkeypatch, capsys, subject)
    assert "ky exam pro --count 3 --save" in out
    assert "ky exam eng" not in out


# ── 3. loop.py 接线契约（源码扫描：计数与告警必须同科目） ──────────────


def test_repl_loop_wires_inferred_subject():
    """loop.py 必须：推断科目 → 用它计数 → 用它告警（三处同源）。

    源码扫描型断言（项目既有先例）：REPL 主循环难以轻量集成测试，
    但接线错误（如仍传 curr_subj）会让功能静默失效——用字符串契约钉住。
    """
    src = (ROOT / "tools" / "cli" / "repl" / "loop.py").read_text(encoding="utf-8")
    assert "infer_subject_from_text(user_input, curr_subj)" in src
    assert "_count_error_records(_warn_subject)" in src
    assert ('_warn_if_mistake_not_archived(user_input, reply or "", '
            '_warn_subject, _err_count_before)') in src


def test_query_llm_reply_uses_shared_inference():
    """网关路径（query_llm_reply）复用同一推断函数（单一实现处）。"""
    src = (ROOT / "tools" / "cli" / "agent" / "engine.py").read_text(encoding="utf-8")
    assert 'active_subj = infer_subject_from_text(user_msg, cfg.get("active_subject", "math"))' in src


# ── 4. 面板科目轻提示（B5：仅提示，不自动切换） ────────────────────────


def test_subject_hint_empty_when_same():
    assert engine.format_subject_hint("eng", "eng") == ""


def test_subject_hint_different_subject():
    hint = engine.format_subject_hint("pro", "eng")
    assert "专业课专属私教" in hint
    assert "英语专属私教" in hint
    assert "/pro" in hint


def test_subject_hint_empty_args():
    assert engine.format_subject_hint("", "eng") == ""
    assert engine.format_subject_hint("pro", "") == ""


def test_repl_loop_prints_subject_hint_without_switching():
    """loop.py 打印轻提示但**不得**调用 _switch_subject 自动切换。"""
    src = (ROOT / "tools" / "cli" / "repl" / "loop.py").read_text(encoding="utf-8")
    assert "format_subject_hint(_warn_subject, curr_subj)" in src
    # 提示行附近不得出现自动切换调用（切换会清空 history / active_quiz_item）
    idx = src.find("format_subject_hint(_warn_subject, curr_subj)")
    window = src[idx: idx + 400]
    assert "_switch_subject" not in window, "轻提示不得自动切换科目"
