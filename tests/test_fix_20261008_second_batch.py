# -*- coding: utf-8 -*-
"""六领域审查第二批 P0-4 / P0-6 修复的行为钉住（2026-10-08）

[这批测试要拦住的回归]

* **P0-4** ``evidence_engine.resolve_conflicts`` 对 list-of-dict 证据崩溃：
  官方简章页挂 PDF 附件是常态（``extractor._extract_pdf_links`` 产出
  ``[{"name": …, "url": …}]``），此前 ``tuple(ev.value)`` 直接入 set →
  ``TypeError: unhashable type: 'dict'`` → 整条 scout 证据链挂
  （``scout_engine.py`` 调用处无 try 保护，考生直接见 traceback）。
  修复：json 序列化后比较（``sort_keys`` 稳定键序防误报、``default=str``
  对非 JSON 原生类型兜底）。
* **P0-6** 冷却「剩余秒数」跨进程冻结：旧 snapshot 只存 ``{key: 剩余秒数}``
  （无写入时间），新进程 restore 只能把它当作「从当前时钟起算」→ 600 秒
  冷却跨天存活（实测 11:11 写盘的 600s 在 15:37 的新进程里原样续满）。
  修复：快照带 ``stored_at`` 墙钟，restore 按真实流逝折算；旧格式整批丢弃；
  时钟回拨按 0 折算（保守：不因回拨而缩短）。

两处修复的语义契约（不可破坏）：
  * ``health.cooldown_state()`` 对外保持平铺 ``{源: 剩余秒数}``；
  * 「刚写盘就重启仍记得冷却」（既有 ``test_cooldown_state_survives_process_restart``）。

全程离线：时钟全部注入，不使用真实 sleep；health 落盘指向 ``tmp_path``。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.http_backoff import CooldownPolicy  # noqa: E402
from tools.intelligence.evidence_engine import (  # noqa: E402
    build_evidence, resolve_conflicts,
)
from tools.search import health  # noqa: E402


class _Clock:
    """可手工推进的时钟（单调 / 墙钟通用，避免测试真等）。"""

    def __init__(self, t: float = 1000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


@pytest.fixture(autouse=True)
def _isolate_cooldown(tmp_path):
    """冷却状态落盘指向 tmp：既验证跨进程语义，又不碰真实工作区 ``.memory/``。"""
    health.set_persistence(True, path=tmp_path / "cooldown.json")
    health.reset()
    yield
    health.reset()
    health.set_persistence(False, path=None)


# ══════════════════════════════════════════════════════════════════════
# P0-4：list-of-dict 证据（PDF 附件）不再让冲突仲裁崩溃
# ══════════════════════════════════════════════════════════════════════

_PDF_FIELD = "官方PDF招生目录附件"
_PDF_LINKS_A = [{"name": "2027 招生目录", "url": "https://yz.example.edu.cn/a.pdf"}]


def _pdf_evidence(links, source_type="college_official",
                  source_name="合成学院", url="https://cs.example.edu.cn"):
    return build_evidence(_PDF_FIELD, links, "个", 2027, source_type,
                          source_name, url, target_year=2027)


def test_p0_4_reverse_control_old_expression_unhashable():
    """反向对照：修复前的表达式（``tuple(list-of-dict)`` 直接入 set）必炸。

    把 bug 形态本身钉进测试 —— 若哪天有人「简化」回 ``tuple(ev.value)``，
    本条仍会绿，但下面 5 条行为用例全红（真源在行为，不在实现）。
    """
    with pytest.raises(TypeError, match="unhashable"):
        set().add(tuple([{"name": "a", "url": "u"}]))


def test_p0_4_list_of_dict_evidence_does_not_crash_and_merges():
    """核心修复：两条同值 PDF 附件证据 → 不再崩溃，且判为一致（多源佐证提权）。"""
    ev1 = _pdf_evidence(_PDF_LINKS_A, source_type="graduate_school",
                        source_name="合成研究生院", url="https://gs.example.edu.cn")
    ev2 = _pdf_evidence([dict(_PDF_LINKS_A[0])])       # 同值不同实例
    out = resolve_conflicts([ev1, ev2])
    assert len(out) == 1, "同值应合并为 1 条"
    assert out[0].confidence > 0.9, "多源佐证应提升置信度"


def test_p0_4_list_of_dict_conflict_still_detected():
    """异值 PDF 附件（**多源**）→ 正确判为 CONFLICT（修复不能把冲突检测一并吞掉）。

    [R11 适配] 冲突仲裁的前提是「多源」（同源 type+url 相同的并列条目不再是
    冲突）；本用例意图「异值仍判冲突」，故第二条改用不同来源构造。
    """
    ev1 = _pdf_evidence(_PDF_LINKS_A)
    ev2 = _pdf_evidence([{"name": "2026 旧目录",
                          "url": "https://yz.example.edu.cn/old.pdf"}],
                        source_type="graduate_school",
                        source_name="合成研究生院", url="https://gs.example.edu.cn")
    out = resolve_conflicts([ev1, ev2])
    assert len(out) == 2 and all(e.status == "CONFLICT" for e in out)


def test_p0_4_dict_key_order_does_not_fake_conflict():
    """键序不同的同内容 dict → ``sort_keys`` 判为一致（不得误报冲突）。"""
    ev1 = _pdf_evidence(_PDF_LINKS_A)
    ev2 = _pdf_evidence([{"url": _PDF_LINKS_A[0]["url"],
                          "name": _PDF_LINKS_A[0]["name"]}])   # 键序颠倒
    out = resolve_conflicts([ev1, ev2])
    assert len(out) == 1, "同内容不同键序不应误报冲突"


def test_p0_4_unserializable_payload_falls_back_to_str():
    """``default=str`` 兜底：含非 JSON 原生类型的 list 不崩溃（同一实例判一致）。"""
    marker = object()
    ev1 = _pdf_evidence([marker])
    ev2 = _pdf_evidence([marker])
    out = resolve_conflicts([ev1, ev2])
    assert len(out) == 1, "str 兜底后同一实例应判一致"


def test_p0_4_scalar_conflict_regression():
    """标量回归：原有 60/58 冲突路径不受本次修复影响。"""
    ev_s = build_evidence("招生人数", 60, "人", 2027, "chsi", "研招网",
                          "https://yz.chsi.com.cn", target_year=2027)
    ev_a = build_evidence("招生人数", 58, "人", 2027, "college_official", "学院",
                          "http://cs.test.edu.cn", target_year=2027)
    out = resolve_conflicts([ev_s, ev_a])
    assert len(out) == 2 and all(e.status == "CONFLICT" for e in out)


# ══════════════════════════════════════════════════════════════════════
# P0-6（策略层）：快照按真实流逝折算，不再给旧冷却续满
# ══════════════════════════════════════════════════════════════════════


def _policy(mono, wall, base=600.0):
    return CooldownPolicy(base_seconds=base, failure_threshold=1,
                          monotonic=mono, time_source=wall)


def test_p0_6_snapshot_carries_wall_clock_timestamp():
    mono, wall = _Clock(1000.0), _Clock(1_700_000_000.0)
    p = _policy(mono, wall)
    p.record_failure("bing")
    snap = p.snapshot()
    assert snap["stored_at"] == 1_700_000_000.0, "快照必须带写入墙钟"
    assert 599.0 <= snap["cooling"]["bing"] <= 600.0


def test_p0_6_restore_discounts_four_hours():
    """核心修复：4 小时流逝后 restore → 冷却已过期（修复前是续满 600s）。"""
    mono, wall = _Clock(1000.0), _Clock(1_700_000_000.0)
    p = _policy(mono, wall)
    p.record_failure("bing")
    snap = p.snapshot()

    mono.advance(14400.0)
    wall.advance(14400.0)
    p2 = _policy(mono, wall)
    p2.restore(snap)
    assert not p2.is_cooling("bing"), "4 小时后不应仍冷却（否则 600s 跨天存活）"


def test_p0_6_restore_discounts_partial_elapsed():
    """部分流逝：10 秒后重启，剩余 ≈ 590（折算正确，不是 600 也不是 0）。"""
    mono, wall = _Clock(1000.0), _Clock(1_700_000_000.0)
    p = _policy(mono, wall)
    p.record_failure("ddg")
    snap = p.snapshot()

    mono.advance(10.0)
    wall.advance(10.0)
    p2 = _policy(mono, wall)
    p2.restore(snap)
    assert 589.0 <= p2.remaining("ddg") <= 591.0


def test_p0_6_restore_keeps_fresh_cooldown():
    """既有契约保持：刚写盘就重启 → 仍记得冷却（量级窗口断言）。"""
    mono, wall = _Clock(1000.0), _Clock(1_700_000_000.0)
    p = _policy(mono, wall)
    p.record_failure("sogou")
    snap = p.snapshot()

    p2 = _policy(mono, wall)
    p2.restore(snap)
    assert p2.is_cooling("sogou")
    assert 599.0 <= p2.remaining("sogou") <= 600.0


def test_p0_6_legacy_snapshot_discarded():
    """旧格式（无写入时间）无法折算 → 整批丢弃，宁可提前半开也不跨天冻结。"""
    p = _policy(_Clock(1000.0), _Clock(1_700_000_000.0))
    p.restore({"sogou": 600.0})                       # 平铺旧格式
    assert not p.is_cooling("sogou")
    p.restore({"cooling": {"sogou": 600.0}})          # 半新（有 cooling 无 stored_at）
    assert not p.is_cooling("sogou")


def test_p0_6_clock_skew_backwards_is_conservative():
    """时钟回拨（stored_at 在未来）→ elapsed 按 0，冷却不缩短也不延长。"""
    p = _policy(_Clock(1000.0), _Clock(1_700_000_000.0))
    p.restore({"stored_at": 1_700_000_000.0 + 3600.0, "cooling": {"tavily": 600.0}})
    assert p.remaining("tavily") == 600.0


def test_p0_6_restore_tolerates_malformed_payloads():
    """损坏载荷不得抛异常（状态损坏不该影响检索）：非 dict / 空 / 坏时间戳 / 坏值。"""
    p = _policy(_Clock(1000.0), _Clock(1_700_000_000.0))
    p.restore(None)                                   # type: ignore[arg-type]
    p.restore({})
    p.restore({"stored_at": 1_700_000_000.0, "cooling": {}})
    p.restore({"stored_at": "not-a-time", "cooling": {"x": 100.0}})
    p.restore({"stored_at": 1_700_000_000.0, "cooling": {"x": "bad"}})
    assert not p.is_cooling("x")


# ══════════════════════════════════════════════════════════════════════
# P0-6（health 层）：落盘 version 2 + 重启折算 + 旧文件丢弃
# ══════════════════════════════════════════════════════════════════════


def test_p0_6_health_persist_format_version_2(tmp_path):
    """落盘结构升级为 version 2：顶层 stored_at + cooling。"""
    health.mark_blocked("bing", "反爬")
    data = json.loads((tmp_path / "cooldown.json").read_text(encoding="utf-8"))
    assert data.get("version") == 2
    assert "stored_at" in data and "cooling" in data


def test_p0_6_health_cooldown_state_flat_contract():
    """诊断 API 契约保持平铺 ``{源: 剩余秒数}``（既有消费方不受结构升级影响）。"""
    health.mark_blocked("sogou-weixin", "反爬")
    st = health.cooldown_state()
    assert 599.0 <= st["sogou-weixin"] <= 600.0


def test_p0_6_health_restart_after_hours_expires(tmp_path):
    """端到端：文件层面 4 小时流逝后重启 → 不再冷却（修复前的跨天冻结形态）。"""
    health.mark_blocked("bing", "反爬")
    state = tmp_path / "cooldown.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data["stored_at"] = float(data["stored_at"]) - 14400.0
    state.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    health._POLICY = None                             # 模拟进程重启
    assert not health.is_cooling("bing")


def test_p0_6_health_restart_immediately_still_cooling():
    """对照：几乎不流逝 → 重启后仍记得冷却（跨进程记忆本身不得被修没）。"""
    health.mark_blocked("ddg", "反爬")
    health._POLICY = None
    assert health.is_cooling("ddg")


def test_p0_6_health_version_1_file_discarded(tmp_path):
    """version 1 旧文件（无 stored_at）→ 丢弃，不续命。"""
    (tmp_path / "cooldown.json").write_text(
        json.dumps({"version": 1, "cooling": {"sogou": 600.0}}),
        encoding="utf-8")
    health._POLICY = None
    assert not health.is_cooling("sogou")
