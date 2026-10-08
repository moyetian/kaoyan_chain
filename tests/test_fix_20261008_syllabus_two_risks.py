# -*- coding: utf-8 -*-
"""2026-10-08 六领域审查「考纲假数据两雷」回归测试（报告 P0-9 / P0-10）。

**两雷**（详见桌面分析报告目录《kaoyan_chain_六领域深度审查报告_2026-10-08.md》）：

1. **P0-9 agent 工具编造演示考纲**：``tools/agent/tools_impl.py`` 的
   ``diff_syllabus`` 在无 ``old_text/new_text`` 时用 408 大纲常量当基准 +
   伪造变动，且 compare_texts 无 is_demo 通道 → 假研报以正式文件落
   ``04-专业课/考纲变动分析_*.md``（无演示前缀/免责首行），还会被
   ``context_engine`` 挂进后续会话当权威引用。实测危害形态：标着考生本校名、
   以 408 的 25 考点为基数的「0 变动/稳健微调」假研报。
2. **P0-10 表格型大纲解析 0 考点 + 0/0 谎报**：``parse_syllabus`` 只认列表行，
   表格行/编号行全跳过（英语一官方大纲 978 字符 → 0 考点）；0/0 对比 →
   「稳健微调」+「复习范围保持平稳」——把「无法解析」包装成确定性结论。

**修复**：agent 工具无真实文本直接拒绝（演示比对仅在 CLI/REPL 提供）；
解析器补表格行（表头/分隔行跳过、数据行合成考点、等级词单元格提取）与
有序编号行；任一侧 0 考点 → ``stability_grade="无法判定"`` + ``parse_warning``
（CLI/REPL 渲染器、markdown、GUI/TUI 同通道展示）；0 点时跳过 LLM 建议。

注意：真实仓库 ``is_llm_configured`` 为 True（``ky_config.json`` 直读，
conftest 重定向不覆盖），本文件一律打桩 LLM，防止真实计费调用。
"""

import pytest

from tools.intelligence.syllabus_diff import SyllabusDiffGenerator


@pytest.fixture(autouse=True)
def _llm_off(monkeypatch):
    """全文件 LLM 打桩：真实仓库已配 API Key，绝不打真实计费调用。"""
    import tools.llm_client as llm
    monkeypatch.setattr(llm, "is_llm_configured", lambda *a, **k: False)


def _make_registry(ws):
    from tools.agent.permissions import PermissionManager
    from tools.agent.sandbox import Sandbox
    from tools.agent.tools_impl import ToolRegistry

    (ws / "04-专业课").mkdir(parents=True, exist_ok=True)
    return ToolRegistry(
        Sandbox(workspace_root=ws),
        PermissionManager(mode="auto", workspace_root=ws),
    )


# ═══════════════ 第 1 层：P0-9 agent 工具拒绝伪造 ═══════════════

def test_agent_diff_no_text_rejected(tmp_path):
    """无 old_text/new_text → 直接拒绝（修复前：408 常量伪造 + 落正式研报）。"""
    reg = _make_registry(tmp_path)
    out = reg.tools["diff_syllabus"].func(school="甲合成大学", major="618 合成专业")
    assert out.startswith("Error"), f"无参调用未拒绝: {out[:80]}"
    assert "缺少参数" in out and "old_text" in out and "new_text" in out


def test_agent_diff_only_old_rejected(tmp_path):
    """只给 old_text → 仍拒绝（修复前：伪造 new_text 变动）。"""
    reg = _make_registry(tmp_path)
    out = reg.tools["diff_syllabus"].func(
        school="甲合成大学", major="618 合成专业",
        old_text="## 一、章节\n- **掌握**：考点A\n")
    assert out.startswith("Error") and "new_text" in out


def test_agent_diff_bad_path_rejected(tmp_path):
    """形似考纲文件路径但读不到 → 拒绝（修复前：路径字符串被当考纲文本比对）。"""
    reg = _make_registry(tmp_path)
    out = reg.tools["diff_syllabus"].func(
        school="甲合成大学", major="618 合成专业",
        old_text="04-专业课/不存在的大纲.md",
        new_text="04-专业课/也不存在.md")
    assert out.startswith("Error") and "找不到考纲文件" in out


def test_agent_diff_real_text_ok(tmp_path):
    """阳性对照：真实文本必须正常比对（防修复过头）。"""
    reg = _make_registry(tmp_path)
    out = reg.tools["diff_syllabus"].func(
        school="甲合成大学", major="618 合成专业",
        old_text="## 一、章节\n- **掌握**：考点A\n",
        new_text="## 一、章节\n- **掌握**：考点A\n- **掌握**：考点B\n",
        save_report=False)
    assert out.startswith("【考纲版本比对完成】"), f"真实文本被误拒: {out[:80]}"


def test_agent_diff_file_and_text_mixed_ok(tmp_path):
    """阳性对照：文件路径 + 文本混合输入正常（文件读为文本）。"""
    reg = _make_registry(tmp_path)
    (tmp_path / "old.md").write_text("## 一、章节\n- **掌握**：考点A\n", encoding="utf-8")
    out = reg.tools["diff_syllabus"].func(
        school="甲合成大学", major="618 合成专业",
        old_text="old.md",
        new_text="## 一、章节\n- **掌握**：考点A\n- **掌握**：考点B\n",
        save_report=False)
    assert out.startswith("【考纲版本比对完成】"), f"文件+文本混合被误拒: {out[:80]}"


def test_agent_diff_no_fake_report_left(tmp_path):
    """关键回归：无参/坏参调用后，04-专业课/ 零研报文件（修复前必产假研报）。"""
    reg = _make_registry(tmp_path)
    for kwargs in (
        {},
        {"old_text": "04-专业课/坏路径.md"},
        {"old_text": "04-专业课/坏路径.md", "new_text": "04-专业课/坏2.md"},
    ):
        reg.tools["diff_syllabus"].func(school="甲合成大学", major="618 合成专业", **kwargs)
    leftovers = list((tmp_path / "04-专业课").glob("考纲变动分析_*.md"))
    assert not leftovers, f"修复后仍落假研报: {leftovers}"


def test_reverse_old_chain_produced_fake_report(tmp_path, monkeypatch):
    """阴性对照（修复前必红）：旧伪造链（408 常量基准 + 无 is_demo）必产假研报。

    复现修复前 ``diff_syllabus`` 无参路径的等价链：CS408 常量当基准 →
    compare_texts → save_diff_report（ROOT 打桩到 tmp 保护真实仓库）。
    实测危害形态：标着考生本校名、以 408 的 25 考点为基数的假研报落正式目录。
    """
    from tools import syllabus_manager as sm
    from tools.intelligence import syllabus_diff as sd

    monkeypatch.setattr(sd, "ROOT", tmp_path)
    gen = sd.SyllabusDiffGenerator()
    ot = sm.CS408_SYLLABUS if isinstance(sm.CS408_SYLLABUS, str) \
        else sm.CS408_SYLLABUS.get("content", "")
    assert ot, "CS408 常量本身为空（对照前提不成立）"
    res = gen.compare_texts(old_text=ot, new_text=ot + "\n", school="甲合成大学",
                            major="618 合成专业")
    sp = gen.save_diff_report(res)
    # 落正式目录（非「演示样例/」）、无演示前缀、无免责首行
    assert sp.parent == tmp_path / "04-专业课", f"未落正式目录: {sp}"
    assert not sp.name.startswith("[演示样例")
    body = sp.read_text(encoding="utf-8")
    assert "本文件为功能演示样例" not in body
    # 张冠李戴特征：标本校名 + 408 的 25 考点基数
    assert "甲合成大学" in body and "| `25` | `25` |" in body
    assert "稳健微调" in body


# ═══════════════ 第 2 层：P0-10 表格/编号行解析 ═══════════════

#: 中性合成样本（结构对齐英语一官方大纲：表格题型分值 + 编号技能要求）
_TABLE_SAMPLE = """# 合成科目 · 官方核心考试大纲

## 一、试卷题型与分值结构

| 题型模块 | 考查形式 | 题量与分值 | 核心考查目标 |
|---|---|---|---|
| **第一部分：知识运用** | 完形填空 | 20 题，共 **10 分** | 语篇逻辑、固定搭配 |
| **第二部分：阅读理解** | 传统阅读 | 20 题，共 **40 分** | 主旨题、细节题 |

## 二、语言技能要求
1. **词汇基准**：掌握 5500 左右核心词汇及常见派生词；
2. **语法骨架**：熟练解构非谓语动词结构与三大从句。
"""


def test_table_and_numbered_rows_parsed():
    """表格型 + 编号型大纲不再解析 0 考点（修复前：978 字符官方大纲 → 0 点）。"""
    gen = SyllabusDiffGenerator()
    pts = gen.parse_syllabus(_TABLE_SAMPLE)
    texts = [p.text for p in pts]
    assert len(pts) >= 4, f"表格/编号行解析不足: {texts}"
    assert any("第一部分：知识运用" in t for t in texts), "表格数据行未解析"
    assert any("第二部分：阅读理解" in t for t in texts)
    assert any("词汇基准" in t for t in texts), "编号行未解析"
    assert any("语法骨架" in t for t in texts)


def test_table_header_and_separator_skipped():
    """表头行与分隔行不得成为考点（防噪声混入）。"""
    gen = SyllabusDiffGenerator()
    pts = gen.parse_syllabus(_TABLE_SAMPLE)
    texts = [p.text for p in pts]
    assert not any("题型模块" in t for t in texts), "表头被当考点"
    assert not any("考查形式" in t for t in texts), "表头被当考点"
    assert not any(set(t) <= set("-: |") for t in texts), "分隔行被当考点"


def test_table_requirement_cell_extracted():
    """等级词单元格（独立成格 / 「**掌握**：…」）提取为考查要求，不入正文。"""
    gen = SyllabusDiffGenerator()
    sample = (
        "## 一、章节\n"
        "| 考点 | 要求 |\n"
        "|---|---|\n"
        "| 合成考点甲 | 掌握 |\n"
        "| 合成考点乙 | 理解 |\n"
        "| **掌握**：合成考点丙 | 补充说明 |\n"
    )
    pts = {p.text: p for p in gen.parse_syllabus(sample)}
    assert any("合成考点甲" in t for t in pts), f"未解析: {list(pts)}"
    p_jia = next(p for t, p in pts.items() if "合成考点甲" in t)
    p_yi = next(p for t, p in pts.items() if "合成考点乙" in t)
    assert p_jia.requirement == "掌握" and "掌握" not in p_jia.text
    assert p_yi.requirement == "理解" and "理解" not in p_yi.text
    assert any("合成考点丙" in t for t in pts)


def test_revision_notes_still_filtered():
    """阴性对照：修订说明（「新增：…」「剔除：无。」）仍被过滤，不因新分支回归。"""
    gen = SyllabusDiffGenerator()
    sample = ("# 合成大纲\n## 一、存在论\n- **掌握**：存在与本质\n"
              "## 三、2027届调整\n- 新增：合成新考点\n- 剔除：无。\n")
    pts = gen.parse_syllabus(sample)
    texts = [p.text for p in pts]
    assert any("存在与本质" in t for t in texts)
    assert not any("合成新考点" in t for t in texts)
    assert not any(t.strip() in ("无", "无。") for t in texts)


# ═══════════════ 第 3 层：P0-10 0 点谎报 ═══════════════

def test_zero_points_grade_unjudgeable():
    """0/0 与「0 点旧 vs 有内容新」都不得给确定性等级（修复前：稳健微调/重大重构）。"""
    gen = SyllabusDiffGenerator()
    for old, new in (
        ("", ""),
        ("", "## 一、章节\n- **掌握**：考点A\n"),
        ("## 一、章节\n- **掌握**：考点A\n", ""),
    ):
        rep = gen.compare_texts(old_text=old, new_text=new, school="甲合成大学", major="合成专业")
        assert rep["metrics"]["stability_grade"] == "无法判定", \
            f"0 点输入仍给确定性等级: {old!r} vs {new!r}"


def test_zero_points_parse_warning_sides():
    """parse_warning 须如实指出哪一侧解析到 0 考点。"""
    gen = SyllabusDiffGenerator()
    rep_both = gen.compare_texts(old_text="", new_text="")
    w = rep_both.get("parse_warning") or ""
    assert "基准（旧）大纲" in w and "最新（新）大纲" in w

    rep_new0 = gen.compare_texts(old_text="## 一、章节\n- **掌握**：考点A\n", new_text="")
    w2 = rep_new0.get("parse_warning") or ""
    assert "最新（新）大纲" in w2 and "基准（旧）大纲" not in w2


def test_zero_markdown_no_steady_claim():
    """0 点报告不得出现「保持平稳」确定性文案，须有警示 blockquote。"""
    gen = SyllabusDiffGenerator()
    rep = gen.compare_texts(old_text="", new_text="")
    md = gen.format_diff_markdown(rep)
    assert "保持平稳" not in md, "0 点仍谎报「复习范围保持平稳」"
    assert "> ⚠️" in md and "比对结果不可信" in md


def test_zero_points_skips_llm(monkeypatch):
    """0 点时不得调 LLM 生成战术建议（防空数据编建议 + 防真实计费）。"""
    import tools.llm_client as llm
    calls = []
    monkeypatch.setattr(llm, "is_llm_configured", lambda *a, **k: True)
    monkeypatch.setattr(llm, "chat_completion",
                        lambda *a, **k: calls.append(1) or "1. **桩建议**" + "长" * 40)

    gen = SyllabusDiffGenerator()
    rep = gen.compare_texts(old_text="", new_text="")
    gen.format_diff_markdown(rep)
    assert calls == [], "0 点时仍调用了 LLM"

    # 阳性对照：真实内容时 LLM 建议链路不被破坏
    rep2 = gen.compare_texts(
        old_text="## 一、章节\n- **掌握**：考点A\n",
        new_text="## 一、章节\n- **掌握**：考点A\n- **掌握**：考点B\n")
    md2 = gen.format_diff_markdown(rep2)
    assert len(calls) == 1, "真实内容未调 LLM（功能回归）"
    assert "桩建议" in md2


def test_normal_content_no_parse_warning():
    """阴性对照：真实内容（有考点）不产生 parse_warning、不误降级。"""
    gen = SyllabusDiffGenerator()
    rep = gen.compare_texts(
        old_text="## 一、章节\n- **掌握**：考点A\n",
        new_text="## 一、章节\n- **掌握**：考点A\n- **掌握**：考点B\n")
    assert not rep.get("parse_warning")
    assert rep["metrics"]["stability_grade"] != "无法判定"


def test_render_syllabus_diff_prints_parse_warning(capsys):
    """CLI/REPL 共用渲染器须打印解析警示（0 点时）。"""
    from tools.cli.commands.intel import render_syllabus_diff

    gen = SyllabusDiffGenerator()
    res = gen.compare_texts(old_text="", new_text="", school="甲合成大学", major="合成专业")
    render_syllabus_diff(res, "甲合成大学", "合成专业", 2026, 2027)
    out = capsys.readouterr().out
    assert "解析警示" in out and "无法判定" in out


def test_render_syllabus_diff_quiet_for_normal(capsys):
    """阴性对照：真实内容渲染不打印解析警示。"""
    from tools.cli.commands.intel import render_syllabus_diff

    gen = SyllabusDiffGenerator()
    res = gen.compare_texts(
        old_text="## 一、章节\n- **掌握**：考点A\n",
        new_text="## 一、章节\n- **掌握**：考点A\n- **掌握**：考点B\n",
        school="甲合成大学", major="合成专业")
    render_syllabus_diff(res, "甲合成大学", "合成专业", 2026, 2027)
    out = capsys.readouterr().out
    assert "解析警示" not in out
