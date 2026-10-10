# -*- coding: utf-8 -*-
"""2026-10-05 数据管线修复批次回归（切片 / 组卷 / 错题管线）。

覆盖 6 项**已实验核实**的缺陷修复，每项附兼容性红线断言：

  1. 切片器跨文档答案串题：``chunk_text`` 入口未重置 ``_last_answer_section``，
     同进程先切 A（Python 路径，置位）再切 B（Rust 路径）时，A 的「## 参考答案」
     被 ``_apply_answer_section`` 按题号回填进 B 的题卡；
  2. 题干被内部 Markdown 水平线 ``---`` 提前截断（``_STEM_RE`` 终止锚），
     普通卡（唯一 ``---`` 在卡尾）提取必须逐字节不变（checksum 兼容红线）；
  3. ``paper_id`` 秒级碰撞：同秒两次组卷生成相同编号，密钥/试卷互相覆盖；
  4. ```text 围栏提前闭合：写侧固定 3 反引号 × 题干含 ``` → 卡片结构损坏、
     指纹失配；围栏长度自适应 + 解析侧反向引用，旧格式解析逐字节不变；
  5. 雷达错因回写错格（不按表头定位「次数」列、多行命中膨胀）与 C/D 行
     读取对 5 列/3 列模板滑窗错位/漏检；
  6. 切片文件名 ``safe_src`` 无长度截断（超长 source_name 直接写失败）。
"""
import datetime as _dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from skills import error_logger  # noqa: E402
from skills import exam_composer as ec  # noqa: E402
from skills import material_ingestion as mi  # noqa: E402
from skills.question_source import (  # noqa: E402
    ORIGIN_MISTAKE,
    backfill_markdown_text,
    compute_checksum,
    extract_card_stem,
    extract_mistake_stem,
    fence_for_text,
    source_from_card,
)

# ════════════════════════════════════════════════════════════════════
# 修复项1：切片器答案串题
# ════════════════════════════════════════════════════════════════════

_DOC_A_WITH_ANSWER = (
    "1. 题目A：以下说法正确的是（ ）\n"
    "A. 甲说法\nB. 乙说法\nC. 丙说法\nD. 丁说法\n"
    "\n## 参考答案\n1. B\n"
)
_DOC_B_NO_ANSWER = (
    "1. 题目B：材料一说明了什么（ ）\n"
    "A. 甲说法\nB. 乙说法\nC. 丙说法\nD. 丁说法\n"
)


class _FakeRust:
    """假 Rust 加速器：固定返回一张 B 文档题卡，确定性触发 Rust 分支。"""

    @staticmethod
    def chunk_text(text, default_source):
        return [{
            "number": 1, "q_type": "choice", "score": 2,
            "stem": "题目B：材料一说明了什么（ ）",
            "options": ["A. 甲说法", "B. 乙说法", "C. 丙说法", "D. 丁说法"],
            "answer": "", "analysis": "", "rubric": [], "points": [],
            "source": default_source,
        }]


def test_cross_document_answer_bleed_is_stopped(tmp_path, monkeypatch):
    """A（Python 路径，带答案区）切完后，B（Rust 路径）不得继承 A 的答案。"""
    monkeypatch.setattr(mi, "_rust", _FakeRust())
    monkeypatch.setattr(mi, "_HAS_RUST_EXT", True)
    pipe = mi.MaterialIngestionPipeline(workspace_root=tmp_path)

    chunks_a = pipe.chunk_text(_DOC_A_WITH_ANSWER)
    assert [c.answer for c in chunks_a] == ["B"], "A 文档自身答案回填不得回归"

    chunks_b = pipe.chunk_text(_DOC_B_NO_ANSWER)
    assert chunks_b, "B 文档应切出题卡"
    assert all((c.answer or "").strip() == "" for c in chunks_b), (
        "B 文档题卡被上一份文档的答案污染（跨文档串题）")


def test_python_path_answer_backfill_unaffected(tmp_path, monkeypatch):
    """正常路径（文档自带答案区，路由 Python）仍按题号回填答案。"""
    pipe = mi.MaterialIngestionPipeline(workspace_root=tmp_path)
    monkeypatch.setattr(pipe, "_force_python", True, raising=False)
    chunks = pipe.chunk_text(_DOC_A_WITH_ANSWER)
    assert [c.answer for c in chunks] == ["B"]


# ════════════════════════════════════════════════════════════════════
# 修复项2：题干 --- 截断
# ════════════════════════════════════════════════════════════════════

_PLAIN_CARD = (
    "### 【题号 1】单项选择题（满分: 2 分）\n"
    "- **【题源出处】**：`示例真题2024`\n"
    "#### 1. 试题原题\n"
    "以下关于实践的说法正确的是（ ）\n"
    "- **A. 说法一**\n"
    "- **B. 说法二**\n"
    "#### 2. 标准答案\n"
    "> B\n"
    "---\n"
)

#: 冻结的普通卡提取结果（修复前实测值；checksum 兼容红线：逐字节不变）
_PLAIN_CARD_EXTRACT_FROZEN = (
    "以下关于实践的说法正确的是（ ）\n"
    "- **A. 说法一**\n"
    "- **B. 说法二**\n"
    "#### 2. 标准答案\n"
    "> B"
)

#: 冻结指纹（sha256(去空白题干)[:16]）。
#: [STEM-M1 口径变更 2026-10-09] 本卡含「#### 2. 标准答案」小节：checksum 取数
#: 现为**截断到第一个答案类小节之前**（身份只锚定题干本体，微调答案/解析不再
#: 触发 source_tampered 误报）。旧口径值 ``f511ccedf02fec5b``（全文 hash）仍被
#: ``checksum_matches`` 双口径接受 —— 存量旧章卡的兼容由该 helper 保障。
#: 提取口径（``extract_card_stem``）未变，上方 ``_PLAIN_CARD_EXTRACT_FROZEN`` 仍逐字节冻结。
_PLAIN_CARD_CHECKSUM_FROZEN = "dbdb1238e07d0e1c"


def test_plain_card_extraction_byte_identical():
    """普通卡（唯一 --- 在卡尾）提取结果与修复前逐字节一致。"""
    assert extract_card_stem(_PLAIN_CARD) == _PLAIN_CARD_EXTRACT_FROZEN


def test_plain_card_checksum_frozen():
    """普通卡指纹不漂移（否则存量已盖章卡会集体被判 source_tampered）。"""
    assert compute_checksum(extract_card_stem(_PLAIN_CARD)) == _PLAIN_CARD_CHECKSUM_FROZEN


def test_stem_with_internal_hr_not_truncated():
    """题干含内部 Markdown 水平线时，提取吃到卡尾 --- 而不是提前截断。"""
    card = (
        "### 【题号 2】材料分析题（满分: 10 分）\n"
        "- **【题源出处】**：`示例真题2024`\n"
        "#### 1. 试题原题\n"
        "阅读以下材料：\n"
        "---\n"
        "材料正文：实践是检验真理的唯一标准。\n"
        "---\n"
    )
    got = extract_card_stem(card)
    assert "阅读以下材料：" in got
    assert "---" in got, "内部水平线必须保留在提取结果中"
    assert "材料正文：实践是检验真理的唯一标准。" in got, "水平线之后的内容不得丢失"


# ════════════════════════════════════════════════════════════════════
# 修复项3：paper_id 秒级碰撞
# ════════════════════════════════════════════════════════════════════

_SLICE_FOR_PAPER = (
    "### 【题号 1】论述题（满分: 15 分）\n"
    "- **【题源出处】**：`示例真题2024`\n"
    "#### 1. 试题原题\n"
    "示例题干：论述实践与认识的辩证关系及其方法论意义。\n"
    "---\n"
)


class _FixedDatetime(_dt.datetime):
    """固定到同一秒的 datetime：暴露旧实现的秒级碰撞。"""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 5, 12, 0, 0)


def _setup_paper_ws(tmp_path, monkeypatch, slice_text=_SLICE_FOR_PAPER):
    monkeypatch.setattr(ec, "ROOT", tmp_path)
    monkeypatch.setattr(error_logger, "ROOT", tmp_path)
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True)
    (ref / "题库切片_test.md").write_text(slice_text, encoding="utf-8")


def test_same_second_composes_do_not_collide(tmp_path, monkeypatch):
    """同一秒两次组卷必须得到不同 paper_id，密钥/试卷互不覆盖。"""
    _setup_paper_ws(tmp_path, monkeypatch)
    monkeypatch.setattr(ec, "datetime", _FixedDatetime)
    # 随机后缀确定化：测试不依赖 1/65536 的碰撞概率（确定性红/绿）
    _seq = iter(["aa11", "bb22"])
    monkeypatch.setattr(ec.secrets, "token_hex", lambda n: next(_seq))

    first = ec.compose_exam_paper(subject="pro", count=1, include_weak=False, save_file=True)
    second = ec.compose_exam_paper(subject="pro", count=1, include_weak=False, save_file=True)
    assert first["success"] and second["success"]
    assert first["paper_id"] != second["paper_id"], "同秒组卷 paper_id 碰撞"

    key_dir = tmp_path / ".memory" / "exam_keys"
    assert (key_dir / f"{first['paper_id']}.json").exists()
    assert (key_dir / f"{second['paper_id']}.json").exists()
    assert Path(first["saved_path"]).exists()
    assert Path(second["saved_path"]).exists()
    assert Path(first["saved_path"]).name != Path(second["saved_path"]).name, (
        "两份试卷写到了同一文件名（后卷覆盖前卷）")


def test_paper_id_format_has_random_suffix(tmp_path, monkeypatch):
    """paper_id 契约：EXAM-<科目>-<日期>-<时刻>-<随机后缀>。"""
    _setup_paper_ws(tmp_path, monkeypatch)
    paper = ec.compose_exam_paper(subject="pro", count=1, include_weak=False, save_file=False)
    assert paper["success"]
    assert re.fullmatch(r"EXAM-PRO-\d{8}-\d{6}-[0-9a-f]{2,}", paper["paper_id"]), (
        f"paper_id 格式不含随机后缀: {paper['paper_id']}")


# ════════════════════════════════════════════════════════════════════
# 修复项4：```text 围栏自适应
# ════════════════════════════════════════════════════════════════════

_Q_WITH_FENCE = "题干含代码：\n```python\nprint('hi')\n```\n结束"

_LEGACY_MISTAKE_CARD = (
    "## 📌 [2026-10-05] 旧格式卡\n"
    "- **掌握状态**：`[待复测]`\n"
    "- **错因分类**：`概念漏洞` (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分)\n"
    "- **题干设问**：\n"
    "```text\n"
    "示例错题题干：简述实践与认识的辩证关系。\n"
    "```\n"
    "- **复测节奏**：`stage=0` · 下次到期 `2026-10-06`\n"
)

_LEGACY_MISTAKE_STEM = "示例错题题干：简述实践与认识的辩证关系。"
_LEGACY_MISTAKE_CHECKSUM_FROZEN = "b9159b550b539cff"


def _mistake_card(question: str) -> str:
    """按修复后的写侧口径手工构造错题卡（等价 log_error_record 的产物）。"""
    fence = fence_for_text(question)
    return (
        "## 📌 [2026-10-05] 卡\n"
        "- **掌握状态**：`[待复测]`\n"
        "- **错因分类**：`概念漏洞` (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分)\n"
        "- **题干设问**：\n"
        f"{fence}text\n{question.strip()}\n{fence}\n"
        "- **复测节奏**：`stage=0` · 下次到期 `2026-10-06`\n"
    )


def test_fence_length_adapts_to_content():
    assert fence_for_text("普通文本") == "```"
    assert fence_for_text("含 ``` 的文本") == "````"
    assert fence_for_text("含 ````` 的文本") == "``````"


def test_legacy_mistake_card_parsing_unchanged():
    """旧格式（3 反引号、内容不含 ```）解析结果逐字节不变 + 指纹冻结。"""
    assert extract_mistake_stem(_LEGACY_MISTAKE_CARD) == _LEGACY_MISTAKE_STEM
    assert compute_checksum(extract_mistake_stem(_LEGACY_MISTAKE_CARD)) == (
        _LEGACY_MISTAKE_CHECKSUM_FROZEN)


def test_mistake_roundtrip_with_fence_in_question(tmp_path, monkeypatch):
    """含 ``` 题干：写→读→提取一致（不再提前闭合）。"""
    monkeypatch.setattr(error_logger, "ROOT", tmp_path)
    error_logger.log_error_record(
        subject="pro", title="代码题", error_type="概念漏洞",
        detail="示例分析", prescription="示例处方", question=_Q_WITH_FENCE)

    files = list((tmp_path / "04-专业课" / "错题本").glob("错题记录_*.md"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert "````text" in raw, "含 ``` 的题干必须用加长围栏写入"

    records = error_logger.scan_error_records("pro")
    assert [r["question"] for r in records] == [_Q_WITH_FENCE], (
        "scan 提取的题干被围栏提前闭合截断")


def test_identity_roundtrip_for_both_fence_formats():
    """指纹闭环：新旧两种围栏格式盖章后，解析侧 verify 均通过。"""
    cases = (
        (_LEGACY_MISTAKE_CARD, _LEGACY_MISTAKE_STEM),
        (_mistake_card(_Q_WITH_FENCE), _Q_WITH_FENCE),
    )
    for card, stem in cases:
        stamped, changed = backfill_markdown_text(card, kind="mistake")
        assert changed == 1
        assert extract_mistake_stem(stamped) == stem, "盖章后题干提取必须不变"
        src = source_from_card(stamped, origin=ORIGIN_MISTAKE, kind="mistake")
        assert src.verify(stem), "新/旧格式卡的题源身份必须自洽"


_SLICE_WITH_FENCE_STEM = (
    "### 【题号 1】综合题（满分: 10 分）\n"
    "- **【题源出处】**：`示例真题2024`\n"
    "#### 1. 试题原题\n"
    "阅读以下代码并说明输出：\n```python\nprint('hi')\n```\n请写出执行结果。\n"
    "---\n"
)


def test_paper_fence_adapts_for_code_stem(tmp_path, monkeypatch):
    """组卷卷面：题面含 ``` 时围栏加长、题面完整（不提前闭合）。"""
    _setup_paper_ws(tmp_path, monkeypatch, slice_text=_SLICE_WITH_FENCE_STEM)
    paper = ec.compose_exam_paper(subject="pro", count=1, include_weak=False, save_file=False)
    assert paper["success"]
    content = paper["content"]
    assert "````text" in content, "试卷题面围栏未随内容自适应加长"
    assert "print('hi')" in content
    assert "请写出执行结果。" in content, "围栏后的题面内容不得丢失"


# ════════════════════════════════════════════════════════════════════
# 修复项5：雷达错因回写与 C/D 行读取
# ════════════════════════════════════════════════════════════════════

_MATH_RADAR = (
    "# 数学模块掌握度雷达\n\n"
    "| 模块名称 | 预估分值 | 当前评级 | 核心卡点与错因 |\n"
    "|---|---|---|---|\n"
    "| 常微分方程 | 10 | C | 概念漏洞：通解结构记不清 |\n"
    "| 线性代数基础 | 30-35 | 未测 | 待首次自测评估 |\n\n"
    "## 错因五分类\n\n"
    "| # | 错因 | 典型表现 | 改进动作 | 次数 |\n"
    "|---|---|---|---|---|\n"
    "| 1 | 计算失误 | 符号/代数/化简失误 | 每步只做一件事并回代验算 | 0 |\n"
    "| 2 | 概念漏洞 | 定义不准/条件漏用 | 追溯课本定义与反例 | 0 |\n"
)


def test_radar_sync_updates_only_count_column(tmp_path, monkeypatch):
    """只更新「次数」列：模块行预估分值不得被动（旧实现 10 → 11 错格）。"""
    monkeypatch.setattr(error_logger, "ROOT", tmp_path)
    f = tmp_path / "01-数学" / "_状态" / "薄弱点雷达.md"
    f.parent.mkdir(parents=True)
    f.write_text(_MATH_RADAR, encoding="utf-8")

    error_logger._sync_radar_error_count("math", "概念漏洞", "示例错题")
    after = f.read_text(encoding="utf-8")
    assert "| 常微分方程 | 10 | C | 概念漏洞：通解结构记不清 |" in after, (
        "模块行预估分值被误 +1（错格）")
    assert "| 2 | 概念漏洞 | 定义不准/条件漏用 | 追溯课本定义与反例 | 1 |" in after, (
        "错因五分类「次数」列未 +1")

    error_logger._sync_radar_error_count("math", "概念漏洞", "示例错题")
    after2 = f.read_text(encoding="utf-8")
    assert "| 2 | 概念漏洞 | 定义不准/条件漏用 | 追溯课本定义与反例 | 2 |" in after2
    assert "| 常微分方程 | 10 | C | 概念漏洞：通解结构记不清 |" in after2


def test_radar_sync_ignores_tables_without_count_column(tmp_path, monkeypatch):
    """无「次数」列的表格（模块表）含错因词也不得被写。"""
    monkeypatch.setattr(error_logger, "ROOT", tmp_path)
    f = tmp_path / "01-数学" / "_状态" / "薄弱点雷达.md"
    f.parent.mkdir(parents=True)
    f.write_text(
        "# 雷达\n\n| 模块名称 | 预估分值 | 当前评级 | 核心卡点与错因 |\n"
        "|---|---|---|---|\n| 常微分方程 | 10 | C | 概念漏洞 |\n",
        encoding="utf-8")
    before = f.read_text(encoding="utf-8")
    error_logger._sync_radar_error_count("math", "概念漏洞", "t")
    assert f.read_text(encoding="utf-8") == before, "无次数列的表格被误写"


def test_radar_sync_updates_first_match_only(tmp_path, monkeypatch):
    """同一错因命中两张含次数列表时，只更新首个命中行（防膨胀）。"""
    monkeypatch.setattr(error_logger, "ROOT", tmp_path)
    f = tmp_path / "01-数学" / "_状态" / "薄弱点雷达.md"
    f.parent.mkdir(parents=True)
    f.write_text(
        "# 雷达\n\n| # | 错因 | 次数 |\n|---|---|---|\n| 1 | 概念漏洞 | 0 |\n\n"
        "| 项目 | 说明 | 次数 |\n|---|---|---|\n| 复盘 | 概念漏洞专项 | 0 |\n",
        encoding="utf-8")
    error_logger._sync_radar_error_count("math", "概念漏洞", "t")
    after = f.read_text(encoding="utf-8")
    assert "| 1 | 概念漏洞 | 1 |" in after
    assert "| 复盘 | 概念漏洞专项 | 0 |" in after, "同一次归档把统计加了两遍（膨胀）"


_POL_RADAR = (
    "# 政治模块掌握度雷达\n\n"
    "| 模块 | 考题形式 | 目标分 | 当前评级 | 易混卡点 |\n"
    "|---|---|---|---|---|\n"
    "| 哲学原理模块 | 单选+多选+分析(第34题) | 24 | C | 易混知识点待巩固 |\n"
    "| 毛中特与新思想 | 单选+多选+分析(第35题) | 30 | 未测 | 待首次自测评估 |\n"
)
_ENG_RADAR = (
    "# 英语能力雷达\n\n"
    "| 题型模块 | 熟练评级 | 主要失分原因 |\n"
    "|---|---|---|\n"
    "| 推理判断题 | C | 定位偏差 |\n"
    "| 主旨大意题 | B | 已掌握 |\n"
)
_MATH_RADAR_ROWS = (
    "# 数学模块掌握度雷达\n\n"
    "| 模块名称 | 预估分值 | 当前评级 | 核心卡点与错因 |\n"
    "|---|---|---|---|\n"
    "| 常微分方程 | 10 | C | 通解结构记不清 |\n"
    "| 线性代数基础 | 30 | D | 矩阵秩概念模糊 |\n"
    "| 概率统计 | 20 | A | 已掌握 |\n"
)


def test_read_radar_rows_pol_5col_aligned(tmp_path, monkeypatch):
    """政治 5 列模板：模块名取第一列（旧实现滑窗错位到「考题形式」列）。"""
    monkeypatch.setattr(ec, "ROOT", tmp_path)
    d = tmp_path / "03-思想政治理论" / "_状态"
    d.mkdir(parents=True)
    (d / "薄弱点雷达.md").write_text(_POL_RADAR, encoding="utf-8")
    assert ec._read_radar_rows("pol") == [("哲学原理模块", "易混知识点待巩固")]


def test_read_radar_rows_eng_3col_detected(tmp_path, monkeypatch):
    """英语 3 列模板：C/D 行不再整体漏检。"""
    monkeypatch.setattr(ec, "ROOT", tmp_path)
    d = tmp_path / "02-英语" / "_状态"
    d.mkdir(parents=True)
    (d / "薄弱点雷达.md").write_text(_ENG_RADAR, encoding="utf-8")
    assert ec._read_radar_rows("eng") == [("推理判断题", "定位偏差")]


def test_read_radar_rows_math_4col_unchanged(tmp_path, monkeypatch):
    """数学 4 列模板：输出与旧实现逐项一致（C/D 两行、模块名与卡点列）。"""
    monkeypatch.setattr(ec, "ROOT", tmp_path)
    d = tmp_path / "01-数学" / "_状态"
    d.mkdir(parents=True)
    (d / "薄弱点雷达.md").write_text(_MATH_RADAR_ROWS, encoding="utf-8")
    assert ec._read_radar_rows("math") == [
        ("常微分方程", "通解结构记不清"),
        ("线性代数基础", "矩阵秩概念模糊"),
    ]


# ════════════════════════════════════════════════════════════════════
# 修复项6：切片文件名长度截断
# ════════════════════════════════════════════════════════════════════


def test_safe_filename_helper_caps_length():
    assert len(mi.safe_filename("真题汇编" * 100)) <= 120
    # [POSIX 字节上限] CJK 每字 3 字节：仅字符数达标不够 —— 120 字 = 360 字节
    # 在 Linux NAME_MAX(255 字节) 下写不出（ubuntu CI 实测 Errno 36）。
    assert len(mi.safe_filename("真题汇编" * 100).encode("utf-8")) <= 180


def test_ingest_long_source_name_truncated(tmp_path, monkeypatch):
    """超长 source_name 必须被截断（旧实现生成 400 字符文件名直接写失败）。"""
    pipe = mi.MaterialIngestionPipeline(workspace_root=tmp_path)
    long_name = "真题汇编" * 100  # 400 字符，远超文件名上限
    result = pipe.ingest_text(
        "1. 题目：以下说法正确的是（ ）\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n",
        subject="pro", source_name=long_name, llm_enrich=False)
    assert result["success"], result

    files = list((tmp_path / "04-专业课" / "参考资料").glob("题库切片_*.md"))
    assert len(files) == 1
    m = re.match(r"题库切片_(.+)_\d{8}_\d{6}\.md$", files[0].name)
    assert m, f"文件名结构不符: {files[0].name}"
    assert len(m.group(1)) <= 120, f"safe_src 未截断: {len(m.group(1))} 字符"
    # [POSIX 字节上限] 文件名上限在 POSIX 是 **字节制**（NAME_MAX=255 字节）：
    # 120 个汉字 = 360 字节在 Linux 上直接 ENAMETOOLONG（ubuntu CI 实测红）。
    assert len(m.group(1).encode("utf-8")) <= 180, \
        f"safe_src 字节超限: {len(m.group(1).encode('utf-8'))} 字节"
