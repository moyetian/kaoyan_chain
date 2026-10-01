# -*- coding: utf-8 -*-
"""[审计 2026-09-30 · 性能篇] PF-2 / PF-3 / PF-5 与三处中影响小项的回归。

* PF-3  ``sanitize_text`` 预编译 + 必备字面量预筛：输出必须与「逐条 re.sub」
  参考实现**逐字节一致**；预筛的健全性（真实匹配必含其规则的一个字面量）
  用真实匹配直接钉住。
* PF-2  ``scan_residual_identity`` 分块解码：含 NUL / 非法字节的二进制快速
  跳过、不抛异常、不整读（峰值内存受控）；文本文件照常命中（阳性对照）；
  含 NUL 但可完整解码的文件仍被扫描（覆盖不降）；token 横跨 64 KiB 块边界
  不漏报。
* PF-5  ``_reclassify_by_sections`` 的索引构造：随机小样本与旧逐字符循环
  逐位一致；``re`` 的 ``\\s`` 与 ``str.isspace()`` 全码点等价；题干前缀
  在去空白文本中的命中必须映射回**原文**偏移（端到端）。
* 中影响：向导走注册表单例、exam_composer 的 splitlines 只求值一次、
  打字机在 quiet（GUI）场景不 sleep。

夹具一律用**中性化**名称（甲大学），PII 样本用字符串拼接构造 —— 否则本文件
会被导出脱敏改写，``test_tests_dir_is_immune_to_py_sanitization`` 变红。
"""
import inspect
import json
import os
import random
import re
import sys
import tracemalloc
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.privacy_policy as pp  # noqa: E402

PLAN = {
    "school": "甲大学",
    "major": "030100 法学",
    "pro_name": "610 法学基础 810 法学综合",
}


def _make_root(tmp_path):
    """造一个只含 ky_config.json 的假仓库根（与 test_privacy_identity_rules 同法）。"""
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": PLAN}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _reference_sanitize(text, patterns, *, line_rules=False):
    """改前语义的参考实现：逐条 ``re.sub``（审计前的 sanitize_text 主体）。"""
    for pat, repl in patterns:
        text = re.sub(pat, repl, text)
    return text


# ══════════════════════════════════════════════════════════════════════════
# PF-3：预编译 + 必备字面量预筛 —— 逐字节一致
# ══════════════════════════════════════════════════════════════════════════

def test_pf3_sanitize_byte_identical_with_reference(tmp_path):
    """脱敏输出与「逐条 re.sub」参考实现逐字节一致（含 PII 反向引用规则）。"""
    from urllib.parse import quote
    root = _make_root(tmp_path)
    rules = pp.build_substitutions(root)
    py_rules = pp.build_py_substitutions(root)
    phone = "138" + "12345678"                     # 拼接构造，避免本文件被改写
    idcard = "110105" + "19900307" + "123X"
    ticket = "2026" + "12345678901"
    samples = [
        "",
        "普通正文，没有任何敏感词。",
        "目标院校：甲大学，专业 030100 法学。",
        "边界样本：0301000 / 1030100 / 030100。",
        "自命题科目 610 法学基础、810 法学综合。",
        "准考证号：" + ticket + "\n联系电话：" + phone + "\n身份证号：" + idcard,
        "链接 https://example.com/q?x=030100",
        quote("甲大学", safe=""),
        "甲大学" * 3,
    ]
    for raw in samples:
        assert pp.sanitize_text(raw, rules) == _reference_sanitize(raw, rules), raw
        assert pp.sanitize_text(raw, py_rules) == _reference_sanitize(raw, py_rules), raw
    # 阴性对照：规则是「活的」——样本里必须真的发生了替换
    assert "目标院校：目标院校" in pp.sanitize_text("目标院校：甲大学", rules)
    # line_rules 分支语义也不变
    sample = "甲大学\n" + "正文行\n" * 3
    assert pp.sanitize_text(sample, rules, line_rules=True) == \
        _reference_sanitize(sample, rules, line_rules=True)


def test_pf3_required_literals_are_sound(tmp_path):
    """预筛健全性：任何真实匹配都必须包含其规则的一个必备字面量。

    这是「跳过整趟扫描」的**唯一**合法性依据 —— 若提取出的字面量不是
    匹配的必要成分，预筛就会漏掉本该替换的文本（比性能问题严重得多）。
    """
    if pp._re_parser_mod is None:
        pytest.skip("当前解释器无法解析正则 AST，预筛整体关闭（安全降级）")
    root = _make_root(tmp_path)
    rules = pp.build_substitutions(root)
    phone = "138" + "12345678"
    ticket = "2026" + "12345678901"
    texts = [
        "甲大学 030100 法学 610 法学基础 810 法学综合",
        "目标院校：甲大学（030100）",
        "准考证号：" + ticket + " 联系电话：" + phone,
        "https://example.com?m=030100&s=610",
        "0301000 1030100 无边界长数字 6100 8100",
        "普通文本 without any token",
    ]
    checked = 0
    for pat, _ in rules:
        lits = pp._required_literals(pat)
        if lits is None:
            continue
        checked += 1
        rx = re.compile(pat)
        for text in texts:
            for m in rx.finditer(text):
                assert any(lit in m.group(0) for lit in lits), \
                    f"预筛不健全：{pat!r} 的匹配 {m.group(0)!r} 不含 {lits}"
    assert checked >= 10, f"启用预筛的规则过少（{checked}），预筛形同虚设"
    # 代表性提取结果（确定性小样本）
    lits = pp._required_literals(r"(?<!\d)030100(?!\d)")
    assert lits and "030100" in lits


# ══════════════════════════════════════════════════════════════════════════
# PF-2：分块解码扫描 —— 二进制快跳 + 覆盖不降
# ══════════════════════════════════════════════════════════════════════════

def test_pf2_binary_with_token_is_skipped(tmp_path):
    """含 NUL + 非法 UTF-8 的二进制（哪怕内容带身份 token）不报残留、不抛异常。"""
    root = _make_root(tmp_path)
    dst = tmp_path / "product"
    dst.mkdir()
    blob = b"\x00" + "甲大学".encode("utf-8") + b"\xff\xfe\x80" + b"\x00" * (4 << 20)
    (dst / "big.bin").write_bytes(blob)
    (dst / "clean.md").write_text("普通内容", encoding="utf-8")
    assert pp.scan_residual_identity(dst, root) == []
    assert pp.scan_residual_identity(dst, root, include_pii=True) == []


def test_pf2_binary_skip_does_not_read_whole_file(tmp_path):
    """快速跳过 = 不整读：16 MiB 二进制的扫描峰值分配必须远小于文件体积。

    旧实现 ``read_text`` 会先把 16 MiB 全部读进内存；新实现读到第一个非法
    字节（本样本在文件头）即停。用 tracemalloc 峰值分配做确定性断言。
    """
    root = _make_root(tmp_path)
    dst = tmp_path / "product"
    dst.mkdir()
    blob = b"\xff" + b"\x00" * (16 << 20)
    (dst / "huge.bin").write_bytes(blob)
    tracemalloc.start()
    try:
        hits = pp.scan_residual_identity(dst, root)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert hits == []
    assert peak < 4 << 20, f"疑似整读了二进制：峰值分配 {peak / 1e6:.1f} MB"


def test_pf2_text_still_flagged_and_pii_switch_intact(tmp_path):
    """阳性对照：文本文件照常命中；include_pii 开关语义不变。"""
    root = _make_root(tmp_path)
    dst = tmp_path / "product"
    dst.mkdir()
    (dst / "leak.md").write_text("目标院校：甲大学", encoding="utf-8")
    (dst / "phone.md").write_text("联系电话：" + "138" + "12345678", encoding="utf-8")
    assert pp.scan_residual_identity(dst, root) == ["leak.md"]
    assert pp.scan_residual_identity(dst, root, include_pii=True) == \
        ["leak.md", "phone.md"]


def test_pf2_nul_but_decodable_file_still_scanned(tmp_path):
    """含 NUL 但可完整 UTF-8 解码的文件仍被扫描 —— 覆盖不得下降。

    旧实现能完整解码就扫描；新实现逐块解码，同样能完整解码 → 同样扫描。
    （因此不能用「头部含 NUL 即判二进制」的简化方案：那会把这类文件漏掉。）
    """
    root = _make_root(tmp_path)
    dst = tmp_path / "product"
    dst.mkdir()
    (dst / "nul.dat").write_bytes(("\x00" + "甲大学" + "\x00").encode("utf-8"))
    assert pp.scan_residual_identity(dst, root) == ["nul.dat"]


def test_pf2_token_across_chunk_boundary(tmp_path):
    """token 横跨 64 KiB 扫描块边界（含恰好压线/贴尾）不得漏报。"""
    root = _make_root(tmp_path)
    dst = tmp_path / "product"
    dst.mkdir()
    chunk = pp._SCAN_CHUNK_BYTES
    expected = []
    for pad in (chunk - 6, chunk - 3, chunk, chunk + 1, chunk + 4):
        name = f"edge_{pad}.md"
        (dst / name).write_text("a" * pad + "甲大学" + "b" * 8, encoding="utf-8")
        expected.append(name)
    (dst / "edge_eof.md").write_text("x" * 100 + "甲大学", encoding="utf-8")
    expected.append("edge_eof.md")
    assert pp.scan_residual_identity(dst, root) == sorted(expected)


# ══════════════════════════════════════════════════════════════════════════
# PF-5：去空白索引构造
# ══════════════════════════════════════════════════════════════════════════

def test_pf5_regex_ws_equals_str_isspace():
    """``re`` 的 ``\\s`` 与 ``str.isspace()`` 在全部 Unicode 码点上等价。

    这是 PF-5 用 ``re.sub(r"\\s+", ...)`` 生成去空白原文的合法性依据 ——
    两者一旦分叉，去空白文本会与索引映射错位。
    """
    chars = "".join(map(chr, range(0x110000)))
    ws_isspace = {ord(c) for c in chars if c.isspace()}
    ws_regex = {ord(c) for c in re.findall(r"\s", chars)}
    assert ws_isspace == ws_regex


def test_pf5_index_construction_matches_old_loop():
    """随机小样本：新构造（列表推导 + 正则去空白）与旧逐字符循环逐位一致。"""
    rng = random.Random(20260930)
    pool = "ab c\n\t\u3000\u00a0中文\x1c"
    for _ in range(300):
        raw = "".join(rng.choice(pool) for _ in range(rng.randint(0, 120)))
        old_chars, old_idx = [], []
        for i, ch in enumerate(raw):
            if not ch.isspace():
                old_chars.append(ch)
                old_idx.append(i)
        new_idx = [i for i, ch in enumerate(raw) if not ch.isspace()]
        new_text = re.sub(r"\s+", "", raw)
        assert new_idx == old_idx
        assert new_text == "".join(old_chars)


def test_pf5_reclassify_maps_probe_back_to_raw_offsets():
    """端到端：题干前缀在去空白文本中的命中必须映射回**原文**偏移。

    构造：两个分段（名词解释 / 论述题）之间塞入大量空行。若索引映射算错
    （例如把去空白位置当原文位置），第二节题干的归属会算到第一节上。
    """
    from tools.skills.material_ingestion import MaterialIngestionPipeline, QuestionChunk
    raw = ("一、名词解释\n" + "\n" * 200 + "1. 齐物\n【答案】略\n"
           + "三、论述题\n" + "\n" * 5 + "1. 白马非马\n【答案】略\n")
    chunks = [
        QuestionChunk(number=1, q_type="essay", score=0, stem="1. 齐物"),
        QuestionChunk(number=2, q_type="essay", score=0, stem="1. 白马非马"),
    ]
    out = MaterialIngestionPipeline()._reclassify_by_sections(chunks, raw)
    assert out[0].q_type == "term", out[0].q_type
    assert out[1].q_type == "discuss", out[1].q_type


# ══════════════════════════════════════════════════════════════════════════
# 中影响三项
# ══════════════════════════════════════════════════════════════════════════

def test_onboarding_wizard_uses_registry_singleton(monkeypatch, tmp_path):
    """向导必须走 get_registry() 单例，而不是每次重载 1834 实体的新实例。"""
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 测试")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import tools.gui.widgets.onboarding_wizard as ow

    QApplication.instance() or QApplication([])

    class _FakeRegistry:
        _entities: dict = {}

        def resolve(self, query):
            return None

    sentinel = _FakeRegistry()
    calls = []

    def _fake_get_registry():
        calls.append(1)
        return sentinel

    monkeypatch.setattr(ow, "get_registry", _fake_get_registry)
    wizard = ow.OnboardingWizard(workspace_root=tmp_path)
    assert wizard._registry is sentinel
    assert len(calls) == 1, "向导未调用注册表单例入口"
    wizard.deleteLater()


def test_exam_composer_splitlines_evaluated_once():
    """``_load_whitelist_cards`` 里同一 splitlines() 不得求值两次。"""
    import tools.skills.exam_composer as ec
    code_lines = [l for l in inspect.getsource(ec._load_whitelist_cards).splitlines()
                  if not l.strip().startswith("#")]
    idx = next(i for i, line in enumerate(code_lines) if "m_type = re.search" in line)
    window = "\n".join(code_lines[max(0, idx - 2):idx + 1])
    assert window.count("splitlines()") == 1, \
        f"splitlines() 求值次数不是 1：\n{window}"


def test_typewriter_skips_sleep_in_quiet_mode(monkeypatch):
    """quiet（GUI）场景打字机不得人为 sleep；终端场景保留节奏。"""
    from tools.agent import loop as loop_mod
    sleeps = []
    monkeypatch.setattr(loop_mod.time, "sleep", lambda s: sleeps.append(s))
    runner = loop_mod.AgentRunner.__new__(loop_mod.AgentRunner)
    chunks = []
    runner.stream_callback = chunks.append
    text = "答" * 240
    runner.quiet = True
    runner._display_final_answer(text)
    assert "".join(chunks) == text
    assert sleeps == [], f"quiet 场景仍 sleep 了 {len(sleeps)} 次"
    chunks.clear()
    runner.quiet = False
    runner._display_final_answer(text)
    assert "".join(chunks) == text
    assert len(sleeps) == 20, "终端场景必须保留打字机节奏（240 字 / 每 12 字一次）"
