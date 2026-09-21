# -*- coding: utf-8 -*-
"""
看板构建器的占位符与日期边界回归（P2-1 / P2-2）

P2-1：``build.py`` 原先「先注入 today/notes/radar 正文，最后再对整串
``.replace("{{DATA}}", payload)``」——用户笔记里出现字面量 ``{{DATA}}`` 时会被当成
模板占位符，把整份 JSON payload 塞进正文（实测复现）。现改为单遍 ``re.sub``。

P2-2：``build.py`` 的 ``day_no / total_days * 100`` 在起跑日 ≥ 初试日（``total_days<=0``，
配置自相矛盾）时抛 ``ZeroDivisionError``，中断整个看板构建。

两条都直接驱动真实 ``build.build()``（只读仓库数据、不落盘）。
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

import build  # noqa: E402


@pytest.fixture()
def build_mod():
    """导入 build 模块并确保测试不会污染其全局日期。"""
    return build


def test_literal_data_placeholder_in_notes_is_not_replaced(build_mod, monkeypatch):
    """正文里的字面量 {{DATA}} 必须原样保留，不得被整份 payload 替换。"""
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")  # 非脱敏模式才会把正文写进 HTML

    original = build_mod.get_section

    def fake_get_section(md, kw):
        if kw is None:  # today 章节
            return "这是一段模拟今日任务正文，长度足够超过二十个字符。\n\n占位符字面量：{{DATA}}\n"
        return original(md, kw)

    monkeypatch.setattr(build_mod, "get_section", fake_get_section)
    html, data, _warns, _secs = build_mod.build(offline=True)

    assert "{{DATA}}" in html, "字面量 {{DATA}} 被吞掉了"
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    # payload 只应出现在模板自身的注入点（var D = ...）一次，不得被塞进笔记正文
    assert html.count(payload) == 1, "payload 出现了多次，疑似被二次替换进正文"
    idx = html.find("{{DATA}}")
    assert payload not in html[max(0, idx - 500): idx + 500], "payload 被注入到了 {{DATA}} 字面量处"


def test_total_days_zero_does_not_crash(build_mod, monkeypatch):
    """起跑日 = 初试日+1（total_days=0）时不得再抛 ZeroDivisionError。"""
    monkeypatch.setattr(build_mod, "EXAM_DAY1", datetime.date(2026, 12, 19))
    monkeypatch.setattr(build_mod, "EXAM_DATE", datetime.date(2026, 12, 20))
    monkeypatch.setattr(build_mod, "PLAN_START", datetime.date(2026, 12, 20))

    html, _data, _warns, _secs = build_mod.build(offline=True)  # 不应抛异常
    assert "pf.style.width='0.0%'" in html, "total_days<=0 时进度应 clamp 为 0.0"


def test_no_placeholder_leaks_after_build(build_mod):
    """构建产物里不得残留任何 {{...}} 模板占位符（单遍替换的完整性守卫）。"""
    html, _data, _warns, _secs = build_mod.build(offline=True)
    leftovers = set(re.findall(r"\{\{[A-Z0-9_]+\}\}", html))
    assert not leftovers, f"产物残留占位符: {leftovers}"
