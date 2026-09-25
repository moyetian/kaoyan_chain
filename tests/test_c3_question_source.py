# -*- coding: utf-8 -*-
"""C3 回归测试：题源溯源 ID + 存量 backfill（可证阶段 · 题源身份）。

背景（升级规划 C3）：题卡此前只有自由文本「题源出处」，无法回答
「这道题是不是之前那道题」（跨文件去重 / 幂等迁移）与「题干有没有被改动过」
（防篡改）。C3 引入结构化身份 ``QuestionSource``
（``tools/skills/question_source.py``）：

  * ``source_id = f"{origin}-{sha256(去空白题干)[:12]}"`` —— 幂等且人眼可读；
  * 渲染侧（``material_ingestion``）对新题卡自动注入「题源ID + 题源校验和」；
  * 解析侧（``exam_composer``）读取并校验；不符 → ``source_tampered`` →
    排除出卷，并计入 ``source_breakdown.tampered``；
  * 存量题卡由 ``tools/backfill_source_ids.py`` 一次性补录（幂等）。

本文件分五层锁定：
  1. 数据模型契约（归一化 / 摘要 / ID 编解码 / verify / 序列化往返）；
  2. **[规划验收] 对组卷入口跑 500 组随机输入的性质测试** —— 不是"抽样
     500 次"，而是现场生成 500 组随机 (题干, 来源) 输入，逐组断言身份不变量
     （幂等 / 单射 / 防篡改 / 格式 / 编解码互逆 / 空白无关 / 渲染↔解析闭环）；
  3. backfill 幂等与"原文保全"（含结构异常卡不盖身份、workspace 双格式扫描）；
  4. 组卷端到端：好卡放行、**篡改卡必被排除并计数**（阴性对照）、存量卡
     惰性认证可用、坏卡不挤占 Top-N 名额、错题卡挂身份；
  5. CLI 契约（--dry-run 不落盘 / 真跑幂等 / --json 报告）。

另含 C3 实现期实测发现的**真缺陷回归**：卡片保留「题源ID」行、仅删掉
「题源校验和」行时，``source_from_card`` 曾退化为"用当前题干现算身份"，
使"删一行 + 改题干"可静默绕过防篡改闸门（第 4 层含正反两向回归）。
"""

import json
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from skills import error_logger  # noqa: E402
from skills.question_source import (  # noqa: E402
    ALL_ORIGINS,
    CHECKSUM_HEX_LEN,
    ORIGIN_MISTAKE,
    ORIGIN_WHITELIST,
    SOURCE_ID_HASH_LEN,
    QuestionSource,
    backfill_file,
    backfill_markdown_text,
    backfill_workspace,
    build_source_id,
    compute_checksum,
    extract_card_stem,
    find_checksum,
    find_source_id,
    has_declared_identity,
    normalize_stem,
    parse_source_id,
    source_from_card,
    split_card_blocks,
)
from tools.skills import exam_composer as exam  # noqa: E402

# ─────────────────── 公共素材 ───────────────────

#: 与渲染侧产物同构的最小合规题卡（题干 ≥8 字符，组卷侧才会收录）
_GOOD_STEM = "示例题干：论述实践与认识的辩证关系及其方法论意义。"
_BAD_STEM = "示例题干：简述矛盾的普遍性与特殊性的辩证统一关系。"
_OTHER_STEM = "示例题干：说明生产力与生产关系的矛盾运动规律。"

_SLICE_TWO_CARDS = (
    "# 题库切片 · 示例\n\n"
    "> 说明：本文件由 ky ingest 生成。\n\n"
    "### 【题号 1】论述题（满分: 15 分）\n"
    "- **【题源出处】**：`示例真题2024`\n"
    "#### 1. 试题原题\n"
    f"{_GOOD_STEM}\n"
    "---\n\n"
    "### 【题号 2】简答题（满分: 10 分）\n"
    "- **【题源出处】**：`示例真题2024`\n"
    "#### 1. 试题原题\n"
    f"{_BAD_STEM}\n"
    "---\n"
)

_MISTAKE_RECORD = (
    "# 04-专业课 · 错题积累集 (2026-09-01)\n\n"
    "## 📌 [2026-09-01] 示例错题 · 实践与认识\n"
    "- **掌握状态**：`[待复测]`\n"
    "- **错因分类**：`概念漏洞`\n"
    "- **题干设问**：\n"
    "```text\n"
    "示例错题题干：简述实践与认识的辩证关系。\n"
    "```\n"
    "- **错题现场与漏洞分析**：示例分析。\n"
    "- **复测节奏**：`stage=0` · 下次到期 `2026-09-01`\n"
    "---\n"
)


def _render_card(stem, *, card_no=1):
    """渲染一张不带题源身份的最小合规卡片（存量卡 / 性质测试用）。"""
    return (
        f"### 【题号 {card_no}】论述题（满分: 15 分）\n"
        "- **【题源出处】**：`示例真题2024`\n"
        "#### 1. 试题原题\n"
        f"{stem}\n"
        "---\n"
    )


def _card_with_id(stem, src, *, card_no=1):
    """渲染一张带正确题源身份的卡片（与渲染侧 backfill 产物同构）。"""
    return (
        f"### 【题号 {card_no}】论述题（满分: 15 分）\n"
        "- **【题源出处】**：`示例真题2024`\n"
        f"- **【题源ID】**：`{src.source_id}`\n"
        f"- **【题源校验和】**：`{src.checksum}`\n"
        "#### 1. 试题原题\n"
        f"{stem}\n"
        "---\n"
    )


# ─────────────────── 第 1 层：数据模型契约 ───────────────────

def test_normalize_stem_strips_all_whitespace():
    """归一化去掉全部空白：半角 / 全角 / 制表 / 换行 / CRLF。"""
    assert normalize_stem("  实 践 \n 检\t验\u3000真理\r\n标准  ") == "实践检验真理标准"
    assert normalize_stem(None) == ""
    assert normalize_stem(12345) == "12345"


def test_checksum_is_layout_insensitive():
    """排版差异（换行 / 缩进 / 全角空格）不改变身份 —— 渲染↔解析口径一致。"""
    a = "试述实践是检验真理的唯一标准。"
    b = "试述实践是检验真理的\n唯一标准。\u3000"
    assert compute_checksum(a) == compute_checksum(b)


def test_checksum_changes_when_content_changes():
    assert compute_checksum("实践检验真理") != compute_checksum("实践检验谬误")


def test_checksum_matches_pinned_sha256():
    """[算法钉死] 硬编码 golden digest —— 防摘要算法漂移。

    若把 sha256 换成 md5 / 换截断长度，**全部存量已 backfill 的卡片会集体
    变成 source_tampered**（需要重跑 backfill），而关系型断言（同输入同值、
    异输入异值）不会红。本用例把算法与长度钉死在真实取值上。
    """
    assert compute_checksum("实践是检验真理的唯一标准") == "f9028289faef6a31"
    assert QuestionSource.build("实践是检验真理的唯一标准", ORIGIN_WHITELIST).source_id == \
        "whitelist-f9028289faef"


def test_build_parse_roundtrip():
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    assert parse_source_id(src.source_id) == (ORIGIN_WHITELIST,
                                              src.checksum[:SOURCE_ID_HASH_LEN])


def test_build_source_id_is_pure_and_truncating():
    """build_source_id 是纯函数：同参恒同值，且只取摘要前 12 位。"""
    assert build_source_id("whitelist", "abcdef0123456789") == "whitelist-abcdef012345"
    assert build_source_id("whitelist", "abcdef0123456789") == \
        build_source_id("whitelist", "abcdef0123456789")


@pytest.mark.parametrize("bad", [
    "", None, 123, "no-separator", "unknown-abcdef123456", "whitelist-",
    "whitelist-zzzzzzzzzzzz", "whitelist-ABCDEF123456", "-abcdef123456",
])
def test_parse_source_id_rejects_invalid(bad):
    """格式非法一律返回 None（大小写也严格 —— 生成侧恒为小写十六进制）。"""
    assert parse_source_id(bad) is None


def test_verify_requires_checksum():
    """「无源即拒」：没有 checksum 的身份 verify 恒为假，绝不默认放行。"""
    naked = QuestionSource(source_id="whitelist-000000000000", origin=ORIGIN_WHITELIST)
    assert naked.verify("任意题干") is False


def test_verify_detects_any_content_change():
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    assert src.verify(_GOOD_STEM) is True
    assert src.verify(_GOOD_STEM + "。") is False
    assert src.verify(_GOOD_STEM[:-1]) is False
    # 空白改动不算改动（归一化口径）
    assert src.verify("  " + _GOOD_STEM.replace("实践", "实 践") + "\n") is True


def test_to_dict_from_dict_roundtrip():
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_MISTAKE, verified=True,
                               syllabus_ref="认识论")
    assert QuestionSource.from_dict(src.to_dict()) == src


@pytest.mark.parametrize("junk", [None, 42, "not-a-dict", [], {}])
def test_from_dict_tolerates_junk(junk):
    """历史数据缺字段 / 类型不对时不抛异常（解析端要能容错降级）。"""
    out = QuestionSource.from_dict(junk)
    assert isinstance(out, QuestionSource)
    assert out.verify("任意题干") is False


# ─────────────────── 第 2 层：500 组随机输入性质测试（规划验收） ───────────────────

#: 随机题干的内容字符池（中文考点语料 + 拉丁 + 数字 + 数学符号）
_STEM_CHARS = (
    "实践是检验真理的唯一标准矛盾普遍性特殊性认识论辩证法唯物史观"
    "生产力生产关系经济基础上层建筑剩余价值资本循环"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "∑∫√≈≠≤≥αβγθλμ"
)

#: 随机空白变体（含全角空格与 CRLF —— 渲染/解析两侧的空白差异来源）
_WS_VARIANTS = ("", " ", "  ", "\n", "\r\n", "\t", "\u3000", "\n\n", " \t ")

#: 规划验收口径：500 组随机输入（不是"抽样 500 次"）
PROPERTY_CASES = 500

_ID_SUFFIX_RE = re.compile(rf"[0-9a-f]{{{SOURCE_ID_HASH_LEN}}}\Z")
_CHECKSUM_RE = re.compile(rf"[0-9a-f]{{{CHECKSUM_HEX_LEN}}}\Z")


def _random_stem(rng):
    """现场生成随机题干：1~48 个内容字符 + 随机空白布局。"""
    n = rng.randint(1, 48)
    parts = []
    for _ in range(n):
        parts.append(rng.choice(_STEM_CHARS))
        parts.append(rng.choice(_WS_VARIANTS))
    return "".join(parts)


def _mutate_stem(stem, rng):
    """在随机位置插入一个内容字符 —— 归一化后必然不同（模拟题干被改动）。"""
    pos = rng.randint(0, len(stem))
    return stem[:pos] + rng.choice(_STEM_CHARS) + stem[pos:]


def test_property_500_random_inputs_identity_invariants():
    """[规划验收] 500 组随机输入的性质测试：身份不变量逐组断言。

    性质清单（每组都跑，不抽样）：
      P1 幂等      —— 同 (题干, 来源) 两次构建恒得同一身份；
      P2 格式      —— ``origin-[0-9a-f]{12}`` / ``[0-9a-f]{16}``；
      P3 原文通过  —— build 出的身份 verify 原题干为真；
      P4 防篡改    —— 题干插入一个字符后 verify 必为假；
      P5 编解码    —— parse_source_id(build(...)) 与 (origin, 前缀) 互逆；
      P6 空白无关  —— 空白布局重排不改变身份；
      P7 渲染闭环  —— backfill 注入 → 解析侧读回，ID / verify 均一致；
      P8 异源异 ID —— 同题干换来源类别必得不同 ID。
    汇总：全部输入的 (归一化题干, 来源) 与 source_id 必须一一对应（单射）。
    """
    rng = random.Random(42)
    norm_to_id = {}
    id_to_norm = {}
    for i in range(PROPERTY_CASES):
        stem = _random_stem(rng)
        origin = rng.choice(ALL_ORIGINS)
        src = QuestionSource.build(stem, origin)

        # P1 幂等
        assert QuestionSource.build(stem, origin) == src, f"#{i} 幂等被破坏"
        # P2 格式
        assert src.source_id.startswith(origin + "-"), f"#{i} 前缀异常: {src.source_id!r}"
        assert _ID_SUFFIX_RE.fullmatch(src.source_id[len(origin) + 1:]), \
            f"#{i} source_id 格式异常: {src.source_id!r}"
        assert _CHECKSUM_RE.fullmatch(src.checksum), f"#{i} checksum 格式异常"
        # P3 原文通过
        assert src.verify(stem), f"#{i} 原文 verify 失败"
        # P4 防篡改
        assert not src.verify(_mutate_stem(stem, rng)), f"#{i} 篡改未被检出"
        # P5 编解码互逆
        assert parse_source_id(src.source_id) == (origin, src.checksum[:SOURCE_ID_HASH_LEN])
        # P6 空白无关
        reflowed = "".join(rng.choice(_WS_VARIANTS) if ch.isspace() else ch for ch in stem)
        assert compute_checksum(reflowed) == src.checksum, f"#{i} 空白重排改变了身份"
        # P7 渲染↔解析闭环
        card, injected = backfill_markdown_text(_render_card(stem), kind="whitelist")
        assert injected == 1, f"#{i} backfill 未注入"
        read_back = source_from_card(card, origin=ORIGIN_WHITELIST)
        expected = QuestionSource.build(stem, ORIGIN_WHITELIST)
        assert read_back.source_id == expected.source_id, f"#{i} 闭环 ID 不一致"
        assert read_back.verify(stem), f"#{i} 闭环 verify 失败"
        # P8 异源异 ID
        other_origin = rng.choice([o for o in ALL_ORIGINS if o != origin])
        assert QuestionSource.build(stem, other_origin).source_id != src.source_id

        # 单射性收集（不同输入必不同 ID；同一输入必同一 ID）
        key = (normalize_stem(stem), origin)
        assert id_to_norm.get(src.source_id, key) == key, \
            f"#{i} ID 碰撞：不同输入得到同一 ID（{src.source_id}）"
        id_to_norm[src.source_id] = key
        norm_to_id[key] = src.source_id

    assert len(id_to_norm) == len(norm_to_id), "身份与输入不是一一对应"


def test_property_mutation_never_collides():
    """阴性对照：若把 500 组随机输入**全部**换成变异题干，ID 集合必须整体改变。

    证明 P4 不是空转 —— 变异确实改变了身份空间，而不是恰好都撞回原值。
    """
    rng = random.Random(42)
    base_ids, mutated_ids = set(), set()
    for _ in range(PROPERTY_CASES):
        stem = _random_stem(rng)
        origin = rng.choice(ALL_ORIGINS)
        base_ids.add(QuestionSource.build(stem, origin).source_id)
        mutated_ids.add(QuestionSource.build(_mutate_stem(stem, rng), origin).source_id)
    assert base_ids & mutated_ids == set(), "存在变异后身份不变（防篡改判定会失效）"


# ─────────────────── 第 3 层：backfill 幂等与原文保全 ───────────────────

def test_backfill_is_idempotent_and_counts_cards():
    once, n1 = backfill_markdown_text(_SLICE_TWO_CARDS, kind="whitelist")
    assert n1 == 2
    twice, n2 = backfill_markdown_text(once, kind="whitelist")
    assert n2 == 0 and twice == once


def test_backfill_injects_standalone_lines():
    """注入行必须独立成行（C3 实测缺陷回归：曾因缺换行挤成一行）。"""
    new_text, _ = backfill_markdown_text(_SLICE_TWO_CARDS, kind="whitelist")
    injected_lines = [l for l in new_text.splitlines()
                      if "【题源ID】" in l or "【题源校验和】" in l]
    assert len(injected_lines) == 4, f"注入行数异常: {injected_lines}"
    for line in injected_lines:
        assert line.startswith("- **【"), f"注入行未独立成行: {line!r}"
        assert line.endswith("`"), f"注入行未闭合: {line!r}"
        assert line.count("【题源ID】") + line.count("【题源校验和】") == 1, \
            f"两个字段被挤进同一行: {line!r}"


def test_backfill_preserves_original_lines_in_order():
    """原文每一行都必须按原顺序保留（补录只新增行，不改写既有内容）。"""
    new_text, n = backfill_markdown_text(_SLICE_TWO_CARDS, kind="whitelist")
    assert n == 2
    pos = 0
    for line in [l for l in _SLICE_TWO_CARDS.splitlines() if l.strip()]:
        idx = new_text.find(line, pos)
        assert idx >= 0, f"原行丢失或被改动: {line!r}"
        pos = idx + len(line)


def test_backfill_skips_structurally_broken_card():
    """结构异常（提不出题干）的卡片不盖身份 —— 宁可不补，不可乱补。"""
    broken = (
        "### 【题号 1】论述题（满分: 15 分）\n"
        "- **【题源出处】**：`示例真题`\n"
        "（本卡缺少「试题原题」段）\n"
    )
    out, n = backfill_markdown_text(broken, kind="whitelist")
    assert n == 0 and out == broken


def test_backfill_does_not_overwrite_existing_id():
    """已有 ID 行的卡片原样保留（即使 ID 被人工改过 —— backfill 不覆盖声明）。"""
    once, _ = backfill_markdown_text(_SLICE_TWO_CARDS, kind="whitelist")
    first_id = find_source_id(once)
    doctored = once.replace(first_id, "whitelist-000000000000", 1)
    out, n = backfill_markdown_text(doctored, kind="whitelist")
    assert n == 0 and out == doctored


def test_backfill_workspace_covers_both_card_kinds(tmp_path):
    """workspace 扫描覆盖题库切片（whitelist）与错题记录（mistake），且幂等。"""
    (tmp_path / "04-专业课" / "参考资料").mkdir(parents=True)
    (tmp_path / "04-专业课" / "参考资料" / "题库切片_2024.md").write_text(
        _SLICE_TWO_CARDS, encoding="utf-8")
    (tmp_path / "01-数学" / "错题本").mkdir(parents=True)
    (tmp_path / "01-数学" / "错题本" / "错题记录_2026-09-01.md").write_text(
        _MISTAKE_RECORD, encoding="utf-8")

    rep = backfill_workspace(tmp_path)
    assert {f["kind"] for f in rep["files"]} == {"whitelist", "mistake"}
    assert rep["cards"] == 3
    assert rep["skipped"] == []
    assert backfill_workspace(tmp_path)["cards"] == 0


def test_backfilled_mistake_card_parses_back(tmp_path):
    """错题卡补录后，用错题格式的提取口径能读回同一身份。"""
    (tmp_path / "01-数学" / "错题本").mkdir(parents=True)
    f = tmp_path / "01-数学" / "错题本" / "错题记录_2026-09-01.md"
    f.write_text(_MISTAKE_RECORD, encoding="utf-8")
    assert backfill_workspace(tmp_path)["cards"] == 1

    text = f.read_text(encoding="utf-8")
    src_id, checksum = find_source_id(text), find_checksum(text)
    assert src_id.startswith(ORIGIN_MISTAKE + "-")
    parsed = source_from_card(text, origin=ORIGIN_MISTAKE)
    assert parsed.source_id == src_id and parsed.checksum == checksum
    assert parsed.verify("示例错题题干：简述实践与认识的辩证关系。") is True


def test_backfill_skips_half_declared_card(tmp_path):
    """半声明卡（只有 ID 行、缺校验和行）不重复注入 —— 更不能替它"洗白"。

    修复前：跳过条件要求**两行齐全**，半声明卡会被追加注入 → 文件出现两条
    ID 行；且"改坏 ID 行 + 删校验和行 + 改题干"的卡片会被重新盖章，绕过闸门。
    """
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    half = "\n".join(l for l in _card_with_id(_GOOD_STEM, src).splitlines()
                     if "题源校验和" not in l)
    out, n = backfill_markdown_text(half, kind="whitelist")
    assert n == 0 and out == half
    assert out.count("【题源ID】") == 1


def test_backfill_ignores_anchor_inside_stem():
    """题干内部引用的同名字段行不得作为注入锚点（否则补录把好卡改成坏卡）。"""
    tricky = (
        "### 【题号 1】论述题（满分: 15 分）\n"
        "#### 1. 试题原题\n"
        "阅读以下格式说明：\n"
        "- **【题源出处】**：`示例`\n"
        "请回答本题：说明生产力与生产关系的矛盾运动规律。\n"
        "---\n"
    )
    out, n = backfill_markdown_text(tricky, kind="whitelist")
    assert n == 0 and out == tricky, "锚点在题干内部时不得注入"


def test_backfill_workspace_scans_english_mistake_dir(tmp_path):
    """英语科目的错题目录叫「错题与长难句本」，也必须被扫描到。"""
    (tmp_path / "02-英语" / "错题与长难句本").mkdir(parents=True)
    (tmp_path / "02-英语" / "错题与长难句本" / "错题记录_2026-09-01.md").write_text(
        _MISTAKE_RECORD, encoding="utf-8")
    rep = backfill_workspace(tmp_path)
    assert rep["cards"] == 1
    assert rep["files"][0]["kind"] == "mistake"


def test_backfill_file_raises_on_unreadable(tmp_path):
    """读取失败必须上抛（不得静默返回 0 —— 那会让 CLI 的退出码契约失效）。"""
    bad = tmp_path / "错题记录_2026-09-01.md"
    bad.write_bytes("示例题干：实践与认识。".encode("gbk"))  # 非 UTF-8
    with pytest.raises(UnicodeDecodeError):
        backfill_file(bad, kind="mistake")
    with pytest.raises(OSError):
        backfill_file(tmp_path / "不存在.md", kind="mistake")


def test_backfill_workspace_records_skipped(tmp_path):
    """不可读文件记入 skipped（不中断整批），CLI 据此以退出码 1 告警。"""
    (tmp_path / "01-数学" / "错题本").mkdir(parents=True)
    (tmp_path / "01-数学" / "错题本" / "错题记录_坏.md").write_bytes(
        "示例题干：实践与认识。".encode("gbk"))
    (tmp_path / "01-数学" / "错题本" / "错题记录_2026-09-01.md").write_text(
        _MISTAKE_RECORD, encoding="utf-8")
    rep = backfill_workspace(tmp_path)
    assert rep["cards"] == 1
    assert len(rep["skipped"]) == 1 and "UnicodeDecodeError" in rep["skipped"][0]["reason"]


def test_split_card_blocks_is_shared_authority(tmp_path, monkeypatch):
    """分块口径必须与解析侧共用：``###  【题号 2】``（多空格）要切成两块。

    修复前解析侧用字面量 ``"### 【题号"`` 切分，多空格标题不被识别 →
    两张卡并成一块、后卡的身份行被算进前卡 → 合法卡被误判 tampered。
    """
    src1 = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    src2 = QuestionSource.build(_BAD_STEM, ORIGIN_WHITELIST)
    text = (_card_with_id(_GOOD_STEM, src1, card_no=1)
            + "\n" + _card_with_id(_BAD_STEM, src2, card_no=2)).replace(
        "### 【题号 2】", "###  【题号 2】")
    assert len(split_card_blocks(text)) == 3  # 头部 + 两张卡

    _setup_ws(tmp_path, monkeypatch, text)
    cards = exam._load_whitelist_cards("pro", need=10)
    assert len(cards) == 2, "解析侧未按共享分块口径切卡"
    assert all(not c["source_tampered"] for c in cards)
    assert {c["source_id"] for c in cards} == {src1.source_id, src2.source_id}


# ─────────────────── 第 4 层：组卷端到端（含阴性对照） ───────────────────

def _patch_roots(tmp_path, monkeypatch):
    """把组卷链路与错题扫描的 ROOT 指向沙箱。

    注：``skills.get_subject_name`` 仍会只读真实 ``ky_config.json`` 取科目名
    （纯读取、不写盘，且本文件断言不依赖其取值）；写入侧全部落在 tmp_path。
    """
    monkeypatch.setattr(exam, "ROOT", tmp_path)
    monkeypatch.setattr(error_logger, "ROOT", tmp_path)


def _setup_ws(tmp_path, monkeypatch, slice_text):
    _patch_roots(tmp_path, monkeypatch)
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "题库切片_2024.md").write_text(slice_text, encoding="utf-8")


def _compose(count=1, **kw):
    return exam.compose_exam_paper(subject="pro", count=count, include_weak=False,
                                   save_file=False, **kw)


def test_trusted_card_with_valid_id_passes(tmp_path, monkeypatch):
    """正向：身份与题干一致的白名单卡正常进卷，零篡改计数。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    _setup_ws(tmp_path, monkeypatch, _card_with_id(_GOOD_STEM, src))
    paper = _compose()
    assert paper["success"] is True
    assert paper["source_breakdown"]["whitelist"] == 1
    assert paper["source_breakdown"]["tampered"] == 0
    assert paper["items"][0]["source_id"] == src.source_id


def test_tampered_card_is_excluded_and_counted(tmp_path, monkeypatch):
    """[阴性对照] 题干被改动（ID / 校验和行保留）→ 必被排除且计数。"""
    good = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    bad = QuestionSource.build(_BAD_STEM, ORIGIN_WHITELIST)
    slice_text = (
        _card_with_id(_GOOD_STEM, good, card_no=1)
        + "\n" + _card_with_id("被改动后的题干内容示例文本。", bad, card_no=2)
    )
    _setup_ws(tmp_path, monkeypatch, slice_text)
    paper = _compose(count=2)
    assert paper["source_breakdown"]["whitelist"] == 1
    assert paper["source_breakdown"]["tampered"] == 1
    assert [it["source_id"] for it in paper["items"]] == [good.source_id]


def test_legacy_card_without_id_is_lazily_authenticated(tmp_path, monkeypatch):
    """存量卡（无 ID 行）不得因缺字段被拒 —— 惰性认证后正常进卷。"""
    _setup_ws(tmp_path, monkeypatch, _render_card(_GOOD_STEM))
    paper = _compose()
    assert paper["source_breakdown"]["whitelist"] == 1
    item = paper["items"][0]
    expected = QuestionSource.build(item["question"], ORIGIN_WHITELIST)
    assert item["source_id"] == expected.source_id
    assert item["source_checksum"] == expected.checksum


def test_tampered_cards_do_not_crowd_out_trusted(tmp_path, monkeypatch):
    """坏卡不挤占 Top-N 名额，且计数不受 need 截断影响（全库统计）。

    构造要点：坏卡题干以「一」「二」开头，标题排序天然排在好卡（「示」）**之前**
    —— 若 `_load_whitelist_cards` 不把坏卡从 trusted 里滤出、不把坏卡全量附在
    返回列表末尾，Top-1 会被坏卡占掉（好卡选不进来）、计数也会被 need 截断。
    """
    good = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    bad1 = QuestionSource.build(_BAD_STEM, ORIGIN_WHITELIST)
    bad2 = QuestionSource.build(_OTHER_STEM, ORIGIN_WHITELIST)
    slice_text = (
        _card_with_id("一、被篡改的题干内容示例文本。", bad1, card_no=1)
        + "\n" + _card_with_id("二、被篡改的题干内容示例文本。", bad2, card_no=2)
        + "\n" + _card_with_id(_GOOD_STEM, good, card_no=3)
    )
    _setup_ws(tmp_path, monkeypatch, slice_text)
    paper = _compose(count=1)
    assert paper["source_breakdown"]["whitelist"] == 1
    assert paper["source_breakdown"]["tampered"] == 2
    assert paper["items"][0]["source_id"] == good.source_id


def test_load_whitelist_returns_trusted_first_and_all_tampered(tmp_path, monkeypatch):
    """加载器的返回契约：可信 Top-N 在前，坏卡全量附后（供计数诊断）。"""
    good = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    bad = QuestionSource.build(_BAD_STEM, ORIGIN_WHITELIST)
    slice_text = (
        _card_with_id("一、被篡改的题干内容示例文本。", bad, card_no=1)
        + "\n" + _card_with_id(_GOOD_STEM, good, card_no=2)
    )
    _setup_ws(tmp_path, monkeypatch, slice_text)
    cards = exam._load_whitelist_cards("pro", need=1)
    assert [c["source_tampered"] for c in cards] == [False, True]
    assert cards[0]["source_id"] == good.source_id


def test_verified_card_reports_source_verified_in_paper(tmp_path, monkeypatch):
    """渲染侧认证戳 → 卡片 dict 的 ``source_verified=True``（认证契约闭环）。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src).replace(
        "`示例真题2024`",
        "`示例真题2024`\n- **【白名单认证】**：📥 `[VERIFIED 官方核验]`")
    _setup_ws(tmp_path, monkeypatch, card)
    paper = _compose()
    assert paper["items"][0]["source_verified"] is True


def test_refusal_guidance_surfaces_tampered_cards(tmp_path, monkeypatch):
    """诊断口径：坏卡不算"可用题源"，但必须在拒绝引导里可见（可指导修复）。"""
    bad = QuestionSource.build(_BAD_STEM, ORIGIN_WHITELIST)
    _setup_ws(tmp_path, monkeypatch,
              _card_with_id("被改动后的题干内容示例文本。", bad))
    paper = _compose()
    assert paper["success"] is False and paper["refused"] is True
    assert paper["diagnostics"]["whitelist"] == 0   # 坏卡不计入可用题源
    assert paper["diagnostics"]["tampered"] == 1
    assert "因题干与题源ID 校验和不符被排除" in paper["content"]


def test_compose_after_backfill_is_green(tmp_path, monkeypatch):
    """[规划验收] 存量题卡 backfill 后组卷回归：身份齐备、零篡改、正常出卷。"""
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True)
    (ref / "题库切片_2024.md").write_text(
        _render_card(_GOOD_STEM, card_no=1) + "\n" + _render_card(_OTHER_STEM, card_no=2),
        encoding="utf-8")
    rep = backfill_workspace(tmp_path)
    assert rep["cards"] == 2

    _patch_roots(tmp_path, monkeypatch)
    paper = _compose(count=2)
    assert paper["success"] is True
    assert paper["source_breakdown"]["whitelist"] == 2
    assert paper["source_breakdown"]["tampered"] == 0
    for it in paper["items"]:
        assert it["source_id"] and it["source_checksum"]
        assert it["source_verified"] is False  # 切片无 [VERIFIED] 认证戳


#: [规划验收] 组卷入口的性质测试组数（与身份层的 500 组互补：这里真的过组卷）
PROPERTY_PAPER_CASES = 500


def test_property_500_random_mixes_through_compose_entry(tmp_path, monkeypatch):
    """[规划验收·组卷入口] 500 组随机卡片组合 → ``compose_exam_paper`` → 精确断言。

    第 2 层锁的是身份模型自身的性质；这里把随机组合喂进**真正的组卷入口**
    （读切片文件 → 解析 → 防篡改闸门 → 选题），逐组断言：

      * 被排除的坏卡数 == ``source_breakdown.tampered``（不多不少，且不受
        ``need`` 截断影响）；
      * 好卡全部入选、数量与请求一致、入选身份与题干重算值一致。

    组内随机维度：好卡数 1~3、坏卡数 0~3、卡片顺序洗牌、题号连续分配。
    """
    rng = random.Random(2026)
    for i in range(PROPERTY_PAPER_CASES):
        n_good = rng.randint(1, 3)
        n_bad = rng.randint(0, 3)
        cards, good_ids = [], []
        for k in range(n_good):
            stem = f"好卡{i}-{k}：论述实践与认识的辩证关系及其方法论意义。"
            src = QuestionSource.build(stem, ORIGIN_WHITELIST)
            good_ids.append(src.source_id)
            cards.append(_card_with_id(stem, src, card_no=len(cards) + 1))
        for k in range(n_bad):
            stem = f"坏卡{i}-{k}：简述矛盾的普遍性与特殊性的辩证统一关系。"
            src = QuestionSource.build(stem, ORIGIN_WHITELIST)
            cards.append(_card_with_id(f"第 {i}-{k} 组被改动后的题干内容示例。", src,
                                       card_no=len(cards) + 1))
        rng.shuffle(cards)

        _setup_ws(tmp_path, monkeypatch, "\n".join(cards))
        paper = _compose(count=n_good)
        assert paper["success"] is True, f"#{i} 组卷失败"
        assert paper["source_breakdown"]["tampered"] == n_bad, (
            f"#{i} tampered 计数不符：期望 {n_bad}，"
            f"实际 {paper['source_breakdown']['tampered']}")
        assert paper["source_breakdown"]["whitelist"] == n_good, f"#{i} 好卡未全入选"
        assert {it["source_id"] for it in paper["items"]} == set(good_ids), \
            f"#{i} 入选身份与期望不符"


# ─── C3 实测缺陷回归：校验和行被删后的弱校验（fail-closed） ───

def test_checksum_line_removed_with_intact_stem_still_verifies():
    """宽容分支：仅校验和行被删、题干未动 → 用 ID 内嵌前缀重新认证通过。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src)
    without_checksum = "\n".join(l for l in card.splitlines() if "题源校验和" not in l)
    read_back = source_from_card(without_checksum, origin=ORIGIN_WHITELIST)
    assert read_back.verify(_GOOD_STEM) is True
    assert read_back.source_id == src.source_id


def test_checksum_line_removed_with_changed_stem_is_rejected():
    """[C3 实测缺陷回归] 删校验和行 + 改题干 → 必须判为不可信。

    修复前：checksum 缺失即走「惰性构建」，用**当前题干**现算身份 ——
    「删一行 + 改题干」可静默绕过防篡改闸门。修复后：只要声明过 ID，
    就用其内嵌摘要前缀做弱校验，不符即拒。
    """
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src)
    without_checksum = "\n".join(l for l in card.splitlines() if "题源校验和" not in l)
    tampered = without_checksum.replace(_GOOD_STEM, "被改动后的题干内容示例文本。")
    read_back = source_from_card(tampered, origin=ORIGIN_WHITELIST)
    assert read_back.verify("被改动后的题干内容示例文本。") is False


def test_checksum_removed_and_stem_changed_is_excluded_from_paper(tmp_path, monkeypatch):
    """端到端：上述绕过手法在组卷侧同样拦得住（坏卡导致拒绝出卷而非放行假题）。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src)
    without_checksum = "\n".join(l for l in card.splitlines() if "题源校验和" not in l)
    tampered = without_checksum.replace(_GOOD_STEM, "被改动后的题干内容示例文本。")
    _setup_ws(tmp_path, monkeypatch, tampered)
    paper = _compose()
    assert paper["success"] is False and paper["refused"] is True
    assert paper["source_breakdown"]["tampered"] == 1


def test_id_and_checksum_must_be_mutually_consistent():
    """身份行内部不自洽（ID 前缀 ≠ 校验和前 12 位）→ 视为不可信。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src)
    doctored = card.replace(src.source_id, "whitelist-000000000000")
    read_back = source_from_card(doctored, origin=ORIGIN_WHITELIST)
    assert read_back.verify(_GOOD_STEM) is False


def test_id_line_removed_with_intact_stem_still_verifies():
    """宽容分支：仅 ID 行被删、校验和行与题干一致 → 按当前题干重建身份放行。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src)
    without_id = "\n".join(l for l in card.splitlines() if "【题源ID】" not in l)
    read_back = source_from_card(without_id, origin=ORIGIN_WHITELIST)
    assert read_back.verify(_GOOD_STEM) is True
    assert read_back.source_id == src.source_id


def test_id_line_removed_with_changed_stem_is_rejected():
    """[红队回归] 删 ID 行（保留校验和行）+ 改题干 → 必须判为不可信。

    修复前：``declared_id`` 取不到就当成"存量卡"走惰性构建，用**当前题干**
    现算身份 —— 「删掉 ID 行」即可整体绕过防篡改闸门。修复后：只要声明过
    **任一**身份行（含半声明），就必须通过校验。
    """
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src)
    without_id = "\n".join(l for l in card.splitlines() if "【题源ID】" not in l)
    tampered = without_id.replace(_GOOD_STEM, "被改动后的题干内容示例文本。")
    read_back = source_from_card(tampered, origin=ORIGIN_WHITELIST)
    assert read_back.verify("被改动后的题干内容示例文本。") is False


def test_id_line_removed_with_changed_stem_excluded_from_paper(tmp_path, monkeypatch):
    """端到端：删 ID 行的绕过手法在组卷侧同样拦得住（拒绝出卷而非放行假题）。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src)
    without_id = "\n".join(l for l in card.splitlines() if "【题源ID】" not in l)
    tampered = without_id.replace(_GOOD_STEM, "被改动后的题干内容示例文本。")
    _setup_ws(tmp_path, monkeypatch, tampered)
    paper = _compose()
    assert paper["success"] is False and paper["refused"] is True
    assert paper["source_breakdown"]["tampered"] == 1


def test_invalid_id_line_is_fail_closed():
    """ID 行格式非法（解析不出摘要前缀）→ 不可信，不得退化为惰性构建。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src)
    broken = card.replace(src.source_id, "whitelist-")
    read_back = source_from_card(broken, origin=ORIGIN_WHITELIST)
    assert read_back.checksum == "" and read_back.verify(_GOOD_STEM) is False


def test_verified_marker_propagates_through_parse():
    """渲染侧的 ``[VERIFIED]`` 认证戳必须被解析侧读回（source_verified 契约）。"""
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    card = _card_with_id(_GOOD_STEM, src).replace(
        "`示例真题2024`",
        "`示例真题2024`\n- **【白名单认证】**：📥 `[VERIFIED 官方核验]`")
    read_back = source_from_card(card, origin=ORIGIN_WHITELIST)
    assert read_back.verified is True
    assert read_back.verify(_GOOD_STEM) is True


def test_backfill_does_not_fabricate_verified_marker():
    """backfill 只落盘身份两行，不伪造 ``[VERIFIED]`` 认证戳（认证归渲染侧）。"""
    out, n = backfill_markdown_text(_SLICE_TWO_CARDS, kind="whitelist")
    assert n == 2
    assert "[VERIFIED" not in out
    assert "【白名单认证】" not in out


def test_mistake_card_gets_source_id_in_paper(tmp_path, monkeypatch):
    """错题卡（无 ID）进卷时也挂题源身份（origin=mistake）。"""
    _patch_roots(tmp_path, monkeypatch)
    mist = tmp_path / "04-专业课" / "错题本"
    mist.mkdir(parents=True)
    (mist / "错题记录_2026-09-01.md").write_text(_MISTAKE_RECORD, encoding="utf-8")
    paper = _compose()
    assert paper["source_breakdown"]["mistake"] == 1
    item = paper["items"][0]
    assert item["origin"] == ORIGIN_MISTAKE
    assert item["source_id"].startswith(ORIGIN_MISTAKE + "-")
    assert item["source_checksum"]


# ─────────────────── 第 5 层：CLI 契约 ───────────────────

def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "backfill_source_ids.py"), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )


def _make_ws_with_slice(tmp_path):
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True, exist_ok=True)
    f = ref / "题库切片_2024.md"
    f.write_text(_SLICE_TWO_CARDS, encoding="utf-8")
    return f


def test_cli_dry_run_reports_without_writing(tmp_path):
    f = _make_ws_with_slice(tmp_path)
    before = f.read_text(encoding="utf-8")
    r = _run_cli("--dry-run", "--root", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "预演" in r.stdout and "2 张" in r.stdout
    assert f.read_text(encoding="utf-8") == before, "--dry-run 不得写盘"


def test_cli_backfills_and_second_run_is_noop(tmp_path):
    f = _make_ws_with_slice(tmp_path)
    r1 = _run_cli("--root", str(tmp_path))
    assert r1.returncode == 0, r1.stderr
    text1 = f.read_text(encoding="utf-8")
    assert "题源ID" in text1 and "题源校验和" in text1

    r2 = _run_cli("--root", str(tmp_path))
    assert r2.returncode == 0, r2.stderr
    assert "无需补录" in r2.stdout
    assert f.read_text(encoding="utf-8") == text1, "第二次执行不得产生差异"


def test_cli_json_report(tmp_path):
    _make_ws_with_slice(tmp_path)
    r = _run_cli("--dry-run", "--json", "--root", str(tmp_path))
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["dry_run"] is True
    assert data["cards"] == 2
    assert len(data["files"]) == 1
    assert data["files"][0]["kind"] == "whitelist"


# ═══════════ 第 6 层：第三轮复查（P1–P5 / P9）修复回归 ═══════════
# 复查在 C3 交付（1876c17）后发现 10 项缺陷，此处锁定已修复项：
#   P1 成功出卷路径 tampered 不可见 / shortfall 归因错误；
#   P2 错题卡不走防篡改闸门（落盘身份与进卷身份分裂）；
#   P3 题干标记缩进 → 注入落进题干内部（越补越坏）；
#   P4 题干内引用身份行 → 误判"已声明"（拒修 + 误伤好卡）；
#   P5 校验和值被改坏 → 退化为"存量卡"放行；
#   P9 分块正则 `\s*` 跨行 → `###\n【题号` 误切。

# ─── P9：分块锚点不跨行 ───

def test_split_card_blocks_does_not_split_across_newline():
    """[P9] `###\\n【题号`（跨行）不得被当成新卡；同行多空格变体仍须切分。"""
    text = ("### 【题号 1】论述题\n#### 1. 试题原题\n示例题干内容。\n\n"
            "###\n【题号 不是卡片】引用文本\n")
    assert len(split_card_blocks(text)) == 2  # 头部 + 1 张卡
    multi_space = "###  【题号 2】论述题\n#### 1. 试题原题\n示例题干内容。\n"
    assert len(split_card_blocks(multi_space)) == 2  # 修复目标：多空格仍切


# ─── P3：题干标记缩进 → 注入仍在元数据区 ───

def test_backfill_injects_into_metadata_zone_with_indented_stem_mark():
    """[P3] 题干标记被缩进时，注入仍须落在元数据区（不得落进题干内部）。"""
    card = (
        "### 【题号 1】论述题（满分: 15 分）\n"
        "- **【题源出处】**：`示例真题2024`\n"
        "  #### 1. 试题原题\n"
        f"{_GOOD_STEM}\n"
        "- **【题源出处】**：`这是题干内部的引用行`\n"
        "---\n"
    )
    out, n = backfill_markdown_text(card, kind="whitelist")
    assert n == 1
    stem = extract_card_stem(out)
    assert "【题源ID】" not in stem and "【题源校验和】" not in stem, \
        "身份行被注入进了题干内部（越补越坏）"
    read_back = source_from_card(out, origin=ORIGIN_WHITELIST)
    assert read_back.verify(stem) is True


# ─── P4：题干内引用身份行不算声明 ───

#: 题干内引用「题源ID / 题源校验和」格式文本的卡片（引用在题干段之内）
_QUOTING_CARD = (
    "### 【题号 1】论述题（满分: 15 分）\n"
    "- **【题源出处】**：`示例真题2024`\n"
    "#### 1. 试题原题\n"
    f"{_GOOD_STEM}\n"
    "请核对题卡格式：\n"
    "- **【题源ID】**：`whitelist-abcdef123456`\n"
    "- **【题源校验和】**：`abcdef1234567890`\n"
    "---\n"
)


def test_has_declared_identity_ignores_stem_quotes():
    """[P4 单元] 声明检测只看元数据区：题干内引用不算声明，元数据区身份行才算。"""
    assert has_declared_identity(_QUOTING_CARD) is False
    src = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    assert has_declared_identity(_card_with_id(_GOOD_STEM, src)) is True


def test_backfill_repairs_card_quoting_identity_lines_in_stem(tmp_path, monkeypatch):
    """[P4] 引用卡可被 backfill 修复（注入真身份行），引用文本不被破坏。"""
    out, n = backfill_markdown_text(_QUOTING_CARD, kind="whitelist")
    assert n == 1, "引用被误判为已声明 → 拒绝修复"
    stem = extract_card_stem(out)
    assert "whitelist-abcdef123456" in stem, "题干内的引用文本被破坏"
    assert has_declared_identity(out) is True

    _setup_ws(tmp_path, monkeypatch, out)
    paper = _compose()
    assert paper["success"] is True
    assert paper["source_breakdown"]["tampered"] == 0
    item = paper["items"][0]
    assert item["source_id"].startswith(ORIGIN_WHITELIST + "-")
    assert item["source_id"] != "whitelist-abcdef123456", "读到的仍是题干内的引用"


def test_quoted_identity_lines_do_not_tamper_unbackfilled_card(tmp_path, monkeypatch):
    """[P4 阴性对照] 未补录的引用卡：declared=False → 惰性认证放行，不得误伤。"""
    _setup_ws(tmp_path, monkeypatch, _QUOTING_CARD)
    paper = _compose()
    assert paper["success"] is True
    assert paper["source_breakdown"]["tampered"] == 0
    assert paper["source_breakdown"]["whitelist"] == 1


# ─── P5：值被改坏也算"声明过身份" ───

#: 无 ID 行 + 校验和值不可解析（zzz）的卡片（修复前 declared=False 放行）
_CORRUPTED_CHECKSUM_CARD = (
    "### 【题号 1】论述题（满分: 15 分）\n"
    "- **【题源出处】**：`示例真题2024`\n"
    "- **【题源校验和】**：`zzz`\n"
    "#### 1. 试题原题\n"
    "被改动后的题干内容示例文本。\n"
    "---\n"
)


def test_corrupted_checksum_value_is_fail_closed():
    """[P5] 校验和值不可解析同样是"声明过" → 题干不符必须判不可信。"""
    assert has_declared_identity(_CORRUPTED_CHECKSUM_CARD) is True
    read_back = source_from_card(_CORRUPTED_CHECKSUM_CARD, origin=ORIGIN_WHITELIST)
    assert read_back.verify("被改动后的题干内容示例文本。") is False


def test_corrupted_checksum_value_excluded_from_paper(tmp_path, monkeypatch):
    """[P5 端到端] 改坏值 + 改题干 → 拒绝出卷且计数可见。"""
    _setup_ws(tmp_path, monkeypatch, _CORRUPTED_CHECKSUM_CARD)
    paper = _compose()
    assert paper["success"] is False and paper["refused"] is True
    assert paper["source_breakdown"]["tampered"] == 1


def test_backfill_skips_card_with_corrupted_identity_value():
    """[P5] 值被改坏的卡片 backfill 不得"洗白"（与半声明同口径）。"""
    out, n = backfill_markdown_text(_CORRUPTED_CHECKSUM_CARD, kind="whitelist")
    assert n == 0 and out == _CORRUPTED_CHECKSUM_CARD


# ─── P1：成功出卷路径呈现 tampered + shortfall 归因 ───

def test_success_path_surfaces_tampered_and_attributes_shortfall(tmp_path, monkeypatch):
    """[P1] 好卡 1 + 坏卡 2、请求 3 题：坏卡信号必须在卷面可见，且归因不得
    再指向"本地未命中更多真题"（真实原因是被防篡改闸门排除）。"""
    good = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    bad1 = QuestionSource.build(_BAD_STEM, ORIGIN_WHITELIST)
    bad2 = QuestionSource.build(_OTHER_STEM, ORIGIN_WHITELIST)
    slice_text = (
        _card_with_id(_GOOD_STEM, good, card_no=1)
        + "\n" + _card_with_id("一、被改动后的题干内容示例。", bad1, card_no=2)
        + "\n" + _card_with_id("二、被改动后的题干内容示例。", bad2, card_no=3)
    )
    _setup_ws(tmp_path, monkeypatch, slice_text)
    paper = _compose(count=3)
    assert paper["success"] is True
    assert paper["shortfall"] == 2
    assert paper["source_breakdown"]["tampered"] == 2
    content = paper["content"]
    assert "题源完整性声明" in content
    assert "另有 2 张题源卡" in content
    assert "未命中更多真题" not in content, "归因错误：真因是被排除，不是本地没题"
    assert "修复上述卡片后重新组卷即可恢复题量" in content


def test_success_path_without_tampered_keeps_original_wording(tmp_path, monkeypatch):
    """[P1 阴性对照] 无坏卡时文案不得出现"完整性声明 / 被排除"字样。"""
    good = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    _setup_ws(tmp_path, monkeypatch, _card_with_id(_GOOD_STEM, good))
    paper = _compose(count=3)
    assert paper["shortfall"] == 2
    content = paper["content"]
    assert "题源完整性声明" not in content
    assert "被防篡改闸门排除" not in content
    assert "本地「参考资料/」未命中更多真题" in content


def test_success_path_tampered_visible_even_without_shortfall(tmp_path, monkeypatch):
    """[P1] 好卡够用时（shortfall=0），坏卡信号仍须在卷面可见。"""
    good = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    bad = QuestionSource.build(_BAD_STEM, ORIGIN_WHITELIST)
    slice_text = (
        _card_with_id(_GOOD_STEM, good, card_no=1)
        + "\n" + _card_with_id("一、被改动后的题干内容示例。", bad, card_no=2)
    )
    _setup_ws(tmp_path, monkeypatch, slice_text)
    paper = _compose(count=1)
    assert paper["shortfall"] == 0
    assert paper["source_breakdown"]["tampered"] == 1
    assert "题源完整性声明" in paper["content"]
    assert "另有 1 张题源卡" in paper["content"]


# ─── P2：错题卡身份闭环 ───

_MISTAKE_STEM = "示例错题题干：简述实践与认识的辩证关系。"


def _mistake_card(*, stem=_MISTAKE_STEM, src=None):
    """构造一张错题记录卡（与 error_logger 模板同构；src 非空时带身份两行）。"""
    lines = [
        "## 📌 [2026-09-01] 示例错题 · 实践与认识",
        "- **掌握状态**：`[待复测]`",
        "- **错因分类**：`概念漏洞`",
    ]
    if src is not None:
        lines.append(f"- **【题源ID】**：`{src.source_id}`")
        lines.append(f"- **【题源校验和】**：`{src.checksum}`")
    lines += [
        "- **题干设问**：",
        "```text",
        stem,
        "```",
        "- **错题现场与漏洞分析**：示例分析。",
        "- **复测节奏**：`stage=0` · 下次到期 `2026-09-01`",
        "---",
    ]
    return "\n".join(lines) + "\n"


def _write_mistake_card(tmp_path, text):
    mist = tmp_path / "04-专业课" / "错题本"
    mist.mkdir(parents=True, exist_ok=True)
    (mist / "错题记录_2026-09-01.md").write_text(text, encoding="utf-8")


def test_scan_error_records_reads_back_persisted_identity(tmp_path, monkeypatch):
    """[P2] scan 层读回落盘身份：自洽卡标记可信、身份来自落盘（不重建）。"""
    _patch_roots(tmp_path, monkeypatch)
    src = QuestionSource.build(_MISTAKE_STEM, ORIGIN_MISTAKE)
    _write_mistake_card(tmp_path, _mistake_card(src=src))
    recs = error_logger.scan_error_records("pro")
    assert len(recs) == 1
    assert recs[0]["source_tampered"] is False
    assert recs[0]["source_id"] == src.source_id
    assert recs[0]["source_checksum"] == src.checksum


def test_scan_error_records_marks_tampered(tmp_path, monkeypatch):
    """[P2] scan 层：盖章后被改题干的错题卡被标记 source_tampered。"""
    _patch_roots(tmp_path, monkeypatch)
    src = QuestionSource.build(_MISTAKE_STEM, ORIGIN_MISTAKE)
    _write_mistake_card(tmp_path, _mistake_card(stem="被改动后的错题题干示例文本。", src=src))
    recs = error_logger.scan_error_records("pro")
    assert len(recs) == 1
    assert recs[0]["source_tampered"] is True
    assert recs[0]["source_id"] == src.source_id  # 落盘身份仍在（供修复对照）


def test_mistake_card_with_valid_identity_passes(tmp_path, monkeypatch):
    """[P2] 自洽错题卡正常进卷，进卷身份 == 落盘身份（不现场重建）。"""
    _patch_roots(tmp_path, monkeypatch)
    src = QuestionSource.build(_MISTAKE_STEM, ORIGIN_MISTAKE)
    _write_mistake_card(tmp_path, _mistake_card(src=src))
    paper = _compose()
    assert paper["source_breakdown"]["mistake"] == 1
    assert paper["source_breakdown"]["tampered"] == 0
    item = paper["items"][0]
    assert item["source_id"] == src.source_id
    assert item["source_checksum"] == src.checksum


def test_tampered_mistake_card_is_excluded_and_counted(tmp_path, monkeypatch):
    """[P2 阴性对照] 盖章后改题干的错题卡不进卷，计数与修复引导可见。"""
    _patch_roots(tmp_path, monkeypatch)
    src = QuestionSource.build(_MISTAKE_STEM, ORIGIN_MISTAKE)
    _write_mistake_card(tmp_path, _mistake_card(stem="被改动后的错题题干示例文本。", src=src))
    paper = _compose()
    # 唯一题源是坏错题卡 → 拒绝出卷，但坏卡必须在盘点里可见
    assert paper["success"] is False and paper["refused"] is True
    assert paper["source_breakdown"]["tampered"] == 1
    assert paper["diagnostics"]["tampered"] == 1
    assert "因题干与题源ID 校验和不符被排除" in paper["content"]


def test_tampered_mistake_card_does_not_crowd_out_whitelist(tmp_path, monkeypatch):
    """[P2] 坏错题卡不挤占名额：好白名单卡仍正常出卷 + 坏卡信号可见。"""
    _patch_roots(tmp_path, monkeypatch)
    src = QuestionSource.build(_MISTAKE_STEM, ORIGIN_MISTAKE)
    _write_mistake_card(tmp_path, _mistake_card(stem="被改动后的错题题干示例文本。", src=src))
    good = QuestionSource.build(_GOOD_STEM, ORIGIN_WHITELIST)
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "题库切片_2024.md").write_text(_card_with_id(_GOOD_STEM, good), encoding="utf-8")
    paper = _compose()
    assert paper["success"] is True
    assert paper["source_breakdown"]["tampered"] == 1
    assert paper["source_breakdown"]["whitelist"] == 1
    assert "题源完整性声明" in paper["content"]


# ─── P6：题干提取单一事实源（防漂移） ───

def test_stem_extraction_is_shared_authority(tmp_path, monkeypatch):
    """[P6] 组卷侧题干提取必须走 question_source.extract_card_stem ——
    若有人改回内联复制正则，本测试即红。"""
    calls = []
    real = exam.extract_card_stem

    def spy(blk):
        calls.append(blk)
        return real(blk)

    monkeypatch.setattr(exam, "extract_card_stem", spy)
    _setup_ws(tmp_path, monkeypatch, _render_card(_GOOD_STEM))
    paper = _compose()
    assert paper["source_breakdown"]["whitelist"] == 1
    assert calls, "组卷链路未经过共享的题干提取函数"
