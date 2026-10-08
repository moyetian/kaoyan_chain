# -*- coding: utf-8 -*-
"""[P1 修复·2026-10-08 K2] mark_error_status 接线层回归测试。

K2 修复的 API 层（fsrs_scheduler.compute_next_interval / load_review_history）
已由 test_fix_20261008_p1_syllabus.py 覆盖；本文件钉住**接线层**行为：
error_logger.mark_error_status 回写链路会读取 .memory/review_log.jsonl 的
真实评级序列做 FSRS 回放，日志缺失/断链/stage 不符时回退 good 快进。

隔离方式：模块属性替换（el.ROOT / el.REVIEW_LOG_FILE / el.load_review_history），
全部经 monkeypatch 自动还原，不触碰真实工作区。
"""

import json
import re
from datetime import date

import pytest

import tools.skills.error_logger as el
from tools.fsrs_scheduler import compute_next_interval

CARD_FILE = "错题本.md"
FAST_FORWARD_INTERVAL = 46  # stage=2 good 快进值（旧行为）


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """隔离工作区：替换 error_logger 的三个模块级引用。"""
    (tmp_path / "01-数学" / "错题本").mkdir(parents=True)
    (tmp_path / ".memory").mkdir()
    monkeypatch.setattr(el, "ROOT", tmp_path)
    monkeypatch.setattr(el, "REVIEW_LOG_FILE", tmp_path / ".memory" / "review_log.jsonl")
    real_load = el.load_review_history
    monkeypatch.setattr(
        el,
        "load_review_history",
        lambda s, t, e: real_load(s, t, e, log_file=el.REVIEW_LOG_FILE),
    )
    return tmp_path


def _write_card(ws, title, stage):
    card = (
        f"## 📌 [2026-10-08] {title}\n"
        f"- **掌握状态**：`[待复测]`\n"
        f"- **错因分类**：`概念漏洞` (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分)\n"
        f"- **题干设问**：\n"
        f"```text\n示例错题题干：{title}。\n```\n"
        f"- **复测节奏**：`stage={stage}` · 下次到期 `2026-10-06`\n"
    )
    (ws / "01-数学" / "错题本" / CARD_FILE).write_text(card, encoding="utf-8")


def _write_log(ws, events):
    with open(ws / ".memory" / "review_log.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _card_text(ws):
    return (ws / "01-数学" / "错题本" / CARD_FILE).read_text(encoding="utf-8")


def _interval(card_text):
    m = re.search(r"距今日\s*(\d+)\s*天", card_text)
    return int(m.group(1)) if m else None


def _mark(ws, title, rating="good"):
    return el.mark_error_status(
        subject="math", file_name=CARD_FILE, title_keyword=title, rating=rating
    )


def test_wiring_replays_real_history(ws):
    """有完整可信日志 → 写回间隔按真实序列回放（hard,hard+good=9），不再快进 46。"""
    _write_card(ws, "接线回放题", stage=2)
    _write_log(ws, [
        {"subject": "math", "title": "接线回放题", "stage_before": 0, "rating": "hard"},
        {"subject": "math", "title": "接线回放题", "stage_before": 1, "rating": "hard"},
    ])
    ok, _ = _mark(ws, "接线回放题")
    assert ok
    expected = compute_next_interval(2, "good", date.today(), history=["hard", "hard"])[2]
    got = _interval(_card_text(ws))
    assert got == expected == 9
    assert got != FAST_FORWARD_INTERVAL


def test_wiring_falls_back_without_log(ws):
    """无 review_log → 回退 good 快进 46 天（接线不改变无日志路径）。"""
    _write_card(ws, "无日志题", stage=2)
    ok, _ = _mark(ws, "无日志题")
    assert ok
    assert _interval(_card_text(ws)) == FAST_FORWARD_INTERVAL


def test_wiring_falls_back_on_broken_chain(ws):
    """日志缺 stage_before=0 事件（断链）→ 回退快进 46 天。"""
    _write_card(ws, "断链题", stage=2)
    _write_log(ws, [
        {"subject": "math", "title": "断链题", "stage_before": 1, "rating": "hard"},
    ])
    ok, _ = _mark(ws, "断链题")
    assert ok
    assert _interval(_card_text(ws)) == FAST_FORWARD_INTERVAL


def test_wiring_falls_back_on_stage_mismatch(ws):
    """日志链终点与卡片 stage 不符 → 回退快进 46 天。"""
    _write_card(ws, "不符题", stage=2)
    _write_log(ws, [
        {"subject": "math", "title": "不符题", "stage_before": 0, "rating": "hard"},
    ])
    ok, _ = _mark(ws, "不符题")
    assert ok
    assert _interval(_card_text(ws)) == FAST_FORWARD_INTERVAL


def test_wiring_appends_review_event(ws):
    """回写后 review_log 追加本次事件（stage_before=2, rating=good）。"""
    _write_card(ws, "写链题", stage=2)
    _write_log(ws, [
        {"subject": "math", "title": "写链题", "stage_before": 0, "rating": "hard"},
        {"subject": "math", "title": "写链题", "stage_before": 1, "rating": "hard"},
    ])
    ok, _ = _mark(ws, "写链题")
    assert ok
    lines = [json.loads(x) for x in
             (ws / ".memory" / "review_log.jsonl").read_text(encoding="utf-8").splitlines()
             if x.strip()]
    assert len(lines) == 3
    assert lines[-1]["stage_before"] == 2
    assert lines[-1]["rating"] == "good"
    assert lines[-1]["interval_after"] == 9


def test_wiring_again_reset_cycle(ws):
    """again 重置后的当前周期只回放重置之后评级（hard+good=5，非快进 11）。"""
    _write_card(ws, "重置题", stage=1)
    _write_log(ws, [
        {"subject": "math", "title": "重置题", "stage_before": 0, "rating": "good"},
        {"subject": "math", "title": "重置题", "stage_before": 1, "rating": "again"},
        {"subject": "math", "title": "重置题", "stage_before": 0, "rating": "hard"},
    ])
    ok, _ = _mark(ws, "重置题")
    assert ok
    got = _interval(_card_text(ws))
    expected = compute_next_interval(1, "good", date.today(), history=["hard"])[2]
    assert got == expected == 5
    assert got != compute_next_interval(1, "good", date.today())[2]
