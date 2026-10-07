# -*- coding: utf-8 -*-
"""UT4 看板与杂项缺陷修复回归（WEB-1 / WEB-2 / WEB-3）。

背景：UT4 三角色沙箱实测发现看板层三条缺陷——

* WEB-1 内嵌今日任务卡照搬磁盘文件「研考倒计时：N 天」静态文本，
  与 hero 按构建当日重算的倒计时同屏矛盾（80/79 并存）。
* WEB-2 目标分区间「120-130 分」在快照层被 _parse_target 截为下限 120，
  且学科小卡只渲染科目名+归档篇数，目标分无 UI 消费点。
* WEB-3 compare 兜底画像「所在城市」粒度参差（市级「河南新乡」vs 省级「河南」
  并排同表，视觉自相矛盾）。

构建类用例直接驱动真实 ``build.build()``（只读仓库数据、不落盘），
与 ``test_dashboard_build_placeholders.py`` 同一套路。
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
for _p in (str(ROOT), str(DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build  # noqa: E402


@pytest.fixture()
def no_today_refresh(monkeypatch):
    """[tripwire 修复 2026-10-07] 打桩 build() 本地完整模式（``KY_SNAPSHOT_OPT_IN=0``）
    的今日任务刷新——否则 build() 会调 ``refresh_stale_today_tasks(
    workspace_root=真实仓库根)`` 改写四科「今日任务.md」（conftest tripwire
    硬失败）。打桩 study_planner 模块属性：build.py 函数内 import 每次重新取。
    """
    try:
        import tools.study_planner as _sp
    except ImportError:  # pragma: no cover
        import study_planner as _sp
    monkeypatch.setattr(_sp, "refresh_stale_today_tasks", lambda **k: None)


# ── WEB-1：内嵌今日任务卡倒计时同口径 ────────────────────────────────

def test_sync_today_countdown_replaces_static_text():
    """「（研考）倒计时：N 天」须按传入天数重算，其余文本原样保留。"""
    text = "> 研考倒计时：80 天 ｜ 当前阶段：强化 ｜ 今日目标用时：120 分钟"
    out = build.sync_today_countdown(text, 79)
    assert "研考倒计时：79 天" in out
    assert "倒计时：80 天" not in out
    # 同一行其余字段不受影响
    assert "当前阶段：强化" in out and "今日目标用时：120 分钟" in out


def test_sync_today_countdown_handles_plain_and_absent():
    """无「研考」前缀的写法同样替换；正文不含倒计时时原样返回。"""
    assert "倒计时：5 天" in build.sync_today_countdown("倒计时：80 天", 5)
    plain = "今日任务：完成英语阅读两篇"
    assert build.sync_today_countdown(plain, 79) == plain


def test_build_today_cards_countdown_matches_hero(no_today_refresh, monkeypatch):
    """端到端：内嵌今日任务卡里的倒计时必须与 hero 同口径（按当日重算）。

    把初试日钉到「今天 + 100 天」，今日任务正文埋「研考倒计时：80 天」静态残留；
    构建产物中卡片应显示 100 天，且不再出现残留的 80 天。
    """
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")  # 完整模式才会内嵌今日任务正文
    exam = datetime.date.today() + datetime.timedelta(days=100)
    monkeypatch.setattr(build, "EXAM_DAY1", exam)
    monkeypatch.setattr(build, "EXAM_DATE", exam + datetime.timedelta(days=1))

    original = build.get_section

    def fake_get_section(md, kw):
        if kw is None:  # today 章节
            return ("> 研考倒计时：80 天 ｜ 当前阶段：回归测试 ｜ 今日目标用时：120 分钟\n\n"
                    "这是一段足够长的模拟今日任务正文，保证不会被过短章节告警拦截。\n")
        return original(md, kw)

    monkeypatch.setattr(build, "get_section", fake_get_section)
    # [2026-10-07 跨环境修复] 同 test_dashboard_build_placeholders：fresh clone /
    # CI / 发布副本里四科「今日任务.md」不存在，build() 在 read 层提前跳过
    # （get_section 桩不被执行）→ 今日任务卡进不了产物 → 断言必红（本机因
    # 工作区恰有文件而假绿）。让「今日任务.md」的读取自供内容，把用例收敛到
    # 「内嵌卡倒计时按构建当日重算」这一被测性质本身。
    original_read = build.read

    def fake_read(p, allow_fallback=True):
        if Path(p).name == "今日任务.md":
            return "# 今日任务 (自供夹具)\n\n占位正文，仅用于让 today 章节进入渲染链。\n"
        return original_read(p, allow_fallback=allow_fallback)

    monkeypatch.setattr(build, "read", fake_read)
    html, _data, _warns, _secs = build.build(offline=True)

    assert "研考倒计时：100 天" in html, "内嵌今日任务卡倒计时未按构建当日重算"
    assert "研考倒计时：80 天" not in html, "建档日静态倒计时残留进了构建产物"


# ── WEB-2：目标分区间原文透传 + 学科小卡消费点 ────────────────────────

def test_config_subjects_carry_target_text():
    """web/config 层：每个科目必须带 target_text 字段，且与 study_plan 原文一致。"""
    from web import config as web_config

    sp = web_config._sp
    for s in web_config.SUBJECTS:
        assert "target_text" in s, f"科目 {s['key']} 缺少 target_text 字段"
        raw = str(sp.get(web_config._TARGET_TEXT_SOURCES.get(s["key"], ""), "") or "").strip()
        assert s["target_text"] == (raw or None), f"科目 {s['key']} 的 target_text 与配置原文不一致"


def test_config_numeric_target_keeps_first_integer():
    """数值 target 维持现状兼容：仍取原文首个整数（区间取下限）。"""
    import re

    from web import config as web_config

    for s in web_config.SUBJECTS:
        if not s["target_text"]:
            continue
        m = re.search(r"\d+", s["target_text"])
        if m:
            assert s["target"] == int(m.group()), (
                f"科目 {s['key']} 数值 target 与原文首整数不一致："
                f"{s['target']} vs {s['target_text']}")


def test_build_subjects_include_target_text_and_ui_consumes(no_today_refresh, monkeypatch):
    """构建层：subj_meta 携带 target_text，页面 JS 存在目标分渲染消费点。"""
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")
    _html, data, _warns, _secs = build.build(offline=True)

    subjects = data.get("subjects", [])
    assert subjects, "构建数据缺少 subjects"
    for s in subjects:
        assert "target_text" in s, f"科目 {s['key']} 快照缺少 target_text"

    html, _d2, _w2, _s2 = build.build(offline=True)
    assert "s.target_text" in html, "学科小卡 JS 未消费 target_text（UI 无目标分渲染点）"
    # 渲染文案前缀（区间原文由 JS 运行时注入，静态产物只含消费代码与前缀）
    assert "stg" in html and "目标 " in html


def test_sanitized_snapshot_still_strips_target_fields():
    """隐私守卫：脱敏快照继续剥离 target/target_text（个人备考目标不随 Pages 发布）。

    若未来有意放开公开快照的目标分字段，须同步修订本用例与快照白名单注释。
    """
    payload = {
        "subjects": [{
            "key": "pro", "name": "专业课", "icon": "", "color": "#059669",
            "dark": "#34d399", "target": 120, "target_text": "120-130 分",
            "notes": 0, "ok": True,
        }],
        "memo": [], "weak": [], "metrics": [], "plan": {}, "maps": {}, "trend": [],
    }
    safe = build.sanitize_public_data(payload)
    public = safe["subjects"][0]
    assert "target" not in public and "target_text" not in public, \
        "脱敏快照不得携带个人目标分（数值或区间原文）"


# ── WEB-3：compare 地区粒度归一 ──────────────────────────────────────

def test_normalize_region_granularity_rules():
    """市级原样、仅省显式标注、直辖市与未知文本透传。"""
    from tools.intelligence.comparator import normalize_region_granularity

    assert normalize_region_granularity("河南新乡") == "河南新乡"   # 市级有则显示市
    assert normalize_region_granularity("河南") == "河南（市级待核验）"  # 仅省 + 标注
    assert normalize_region_granularity("北京") == "北京"           # 直辖市无需标注
    assert normalize_region_granularity("黑龙江齐齐哈尔") == "黑龙江齐齐哈尔"
    assert normalize_region_granularity("待核验（未能从校名推断省份）") == \
        "待核验（未能从校名推断省份）"                              # 未知透传
    assert normalize_region_granularity("") == ""
    assert normalize_region_granularity(None) == ""


def test_compare_normalizes_region_pair(monkeypatch):
    """端到端（quick 离线）：compare 双栏画像的 region 在三处输出中粒度一致。"""
    from tools.intelligence.comparator import SchoolComparator

    profiles = {
        "甲大学": {"name": "甲大学", "region": "河南", "majors": ["专业A"],
                   "catalog_source": "[LOCAL_DB_VERIFIED 测试桩]"},
        "乙大学": {"name": "乙大学", "region": "河南新乡", "majors": ["专业A"],
                   "catalog_source": "[LOCAL_DB_VERIFIED 测试桩]"},
    }

    def fake_fallback(school_name, major_keyword, reason=""):
        return dict(profiles.get(school_name, {"name": school_name, "region": "全国"}))

    monkeypatch.setattr(SchoolComparator, "_fallback_profile",
                        staticmethod(fake_fallback))
    res = SchoolComparator().compare("甲大学", "乙大学", "专业A", quick=True)

    assert res["info1"]["region"] == "河南（市级待核验）"
    assert res["info2"]["region"] == "河南新乡"
    # 终端表与 Markdown 研报同步生效（粒度不再裸并排「河南 vs 河南新乡」）
    assert "河南（市级待核验）" in res["terminal_report"]
    assert "河南（市级待核验）" in res["markdown_report"]
