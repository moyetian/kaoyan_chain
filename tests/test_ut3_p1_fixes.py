# -*- coding: utf-8 -*-
"""UT3 三沙箱测试批次 · 5 条 P1 修复回归（含阴性对照）。

来源：2026-09-30 三角色（材料力学 / 应用数学 / 经济学）真实用户旅程测试，
合并去重后的 P1 清单：
  P1-A 记忆种子硬编码（tools/agent/memory.py）
       —— 不考数学考生被注入「数学二严禁复习三重积分」等反向指令；
          「英语二真题」误导英语一考生；辅导风格写死「严格把关」。
  P1-B safe 只读模式「打卡」裸崩（tools/cli/repl/loop.py）
       —— 未捕获 PermissionDeniedError → 完整 traceback + REPL 退出码 1。
  P1-C 今日任务提示口令不可识别（tools/study_planner.py）
       —— 提示「803 经济学综合报到」不在路由表，考生照做落入 LLM 路径。
  P1-D 政治 AGENTS.md 学员配置区漏更新（tools/study_planner.py）
       —— 模板残留「70+ 分 / 摸底40分」凭空数据。
  P1-E ingest 回忆版解答步骤被切成伪题卡（tools/skills/material_ingestion.py）
       —— 4 题切出 17 张碎片卡，题干为「惯性矩 Iz = …」等解答碎片。

全部用例只读 / 打桩：不落盘真实工作区、不联网、不触碰真实 ky_config.json。
"""
from __future__ import annotations

import builtins
import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.memory import MemoryManager, MemoryScope  # noqa: E402
from tools.cli.repl.router import CHINESE_SUBJECT_MAP  # noqa: E402
from tools.skills.material_ingestion import MaterialIngestionPipeline  # noqa: E402
from tools.study_planner import ensure_subject_today_task, update_subject_agents  # noqa: E402


# ═════════════════════════ 公共工具 ═════════════════════════

def _ky_io_aliases():
    """返回已加载的 ky_io 模块别名（双导入下通常有两个对象）。"""
    mods = []
    for name in ("tools.ky_io", "ky_io"):
        try:
            mods.append(importlib.import_module(name))
        except ImportError:
            continue
    return mods


def _set_read_only(enabled: bool) -> None:
    for mod in _ky_io_aliases():
        mod.set_read_only_mode(enabled)


@pytest.fixture(autouse=True)
def _clean_read_only_flag():
    """全局只读标志逐条复位，避免用例间互相污染。"""
    _set_read_only(False)
    yield
    _set_read_only(False)


# ═════════════════════════ P1-A 记忆种子硬编码 ═════════════════════════

def _isolated_memory(tmp_path) -> MemoryManager:
    """MemoryManager 的 GLOBAL 记忆默认落在 ~/.ky —— 测试必须重定向到 tmp。"""
    mm = MemoryManager(workspace_root=tmp_path)
    gdir = tmp_path / "_fake_home" / ".ky" / "memory"
    gdir.mkdir(parents=True)
    mm.global_memory_dir = gdir
    mm._files[MemoryScope.GLOBAL] = gdir / "user.md"
    return mm


def test_p1a_no_math_plan_gets_no_math_redline(tmp_path):
    """不考数学 + 英语一 + 学霸风格：三类种子都必须按 study_plan 生成。"""
    mm = _isolated_memory(tmp_path)
    mm.init_defaults_from_config({"study_plan": {
        "math_key": "none", "math_name": "不考数学",
        "eng_key": "eng1", "eng_name": "英语一 (201)",
        "style_name": "深度原理·学霸溯源型 (Deep Conceptual Master)",
    }})

    decisions = mm.read_memory(MemoryScope.DECISIONS)
    assert "不考数学" in decisions
    assert "严禁复习" not in decisions, "不得注入任何「数学严禁复习」反向指令"
    assert "三重积分" not in decisions, "不考数学方案不得出现数学考纲红线"
    assert "英语一" in decisions
    assert "英语二真题以 2010" not in decisions, "英语一考生不得被注入英语二策略"

    global_mem = mm.read_memory(MemoryScope.GLOBAL)
    assert "深度原理" in global_mem, "辅导风格必须跟随考生实际选择"
    assert "严格把关" not in global_mem


def test_p1a_math_redline_uses_syllabus_single_source(tmp_path):
    """数学红线文案必须来自 syllabus_manager 的单一真源 description。"""
    from tools import syllabus_manager

    mm = _isolated_memory(tmp_path)
    mm.init_defaults_from_config({"study_plan": {
        "math_key": "math3", "math_name": "数学三 (303)", "eng_key": "eng2",
    }})
    decisions = mm.read_memory(MemoryScope.DECISIONS)

    desc = syllabus_manager.MATH_SYLLABI["math3"]["description"]
    assert desc and desc in decisions
    # 数学二专属红线（不考级数、三重积分与曲线曲面积分）不得出现在数学三方案
    assert "三重积分" not in decisions


def test_p1a_negative_control_old_hardcoded_text_is_discriminated():
    """阴性对照：旧硬编码文案命中正向断言的全部 not-in 关键词 —— 判别力证明。

    正向用例断言 `严禁复习 / 英语二真题以 2010 / 严格把关` 均 not in；
    此处旧文案对这些词全部 in，即正向断言在旧实现上必红。
    """
    old_decisions = (
        "# 关键复习决策与避坑指南 (Decisions Memory)\n"
        "- [考纲红线]: 数学二严禁复习三重积分、曲面积分与无穷级数，严防超纲耗时\n"
        "- [真题范围]: 英语二真题以 2010 年之后的规范真题为主，不盲目刷英语一超纲长难句"
    )
    old_global = ("# 全局学员习惯偏好 (Global Memory)\n"
                  "- 辅导风格偏好: 严格把关·保姆提分型 (Strict & Disciplined)")
    for needle, blob in (("严禁复习", old_decisions),
                         ("英语二真题以 2010", old_decisions),
                         ("严格把关", old_global)):
        assert needle in blob, f"对照前提不成立：旧文案未含「{needle}」"


# ═════════════════════════ P1-B safe 模式打卡/组卷裸崩 ═════════════════════════

_SAFE_CFG = {"onboarding_completed": True, "active_subject": "pro",
             "study_plan": {}, "api_key": ""}


def _drive_repl(monkeypatch, answers):
    """以打桩输入驱动 run_repl：逐条喂入 answers，耗尽后抛 EOFError 退出。"""
    from tools.cli.repl import loop as loop_mod

    monkeypatch.setattr(loop_mod, "load_config", lambda: dict(_SAFE_CFG))
    monkeypatch.setattr(loop_mod, "start_background_live_server", lambda *a, **k: 0)
    monkeypatch.setattr(loop_mod, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(loop_mod, "AgentRunner", None)

    queue = list(answers)

    def _fake_input(prompt=""):
        if queue:
            return queue.pop(0)
        raise EOFError

    monkeypatch.setattr(builtins, "input", _fake_input)
    return loop_mod


def test_p1b_safe_mode_checkin_graceful_refusal(tmp_path, monkeypatch, capsys):
    """真实链路：safe 只读 + 「打卡」→ 友好拒绝、REPL 继续、零落盘、无堆栈。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    task_dir = ws / "04-专业课" / "_状态"
    task_dir.mkdir(parents=True)
    task_file = task_dir / "今日任务.md"
    task_file.write_text(
        "# 今日专业课任务 (2026-09-30)\n\n"
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
        "|---|---|---|---|\n"
        "| 核心精讲 | 弯曲变形核心知识点梳理 | 30 分钟 | [ ] |\n",
        encoding="utf-8")
    before = task_file.read_bytes()

    monkeypatch.setattr(cli_shared, "ROOT", ws)
    loop_mod = _drive_repl(monkeypatch, ["打卡 核心知识点"])

    _set_read_only(True)
    try:
        # 旧实现：mark_today_task_done 抛 PermissionDeniedError 一路裸崩
        loop_mod.run_repl(permission_mode="safe")
    finally:
        _set_read_only(False)

    out = capsys.readouterr().out
    assert "[✘ 已拒绝]" in out, f"缺少优雅拒绝提示；实际输出尾部: {out[-400:]!r}"
    assert "Traceback" not in out
    assert task_file.read_bytes() == before, "严格只读模式下必须零落盘"


def test_p1b_negative_control_guard_actually_raises(tmp_path, monkeypatch):
    """阴性对照：底层调用在 safe 模式确实抛 PermissionDeniedError ——
    REPL 侧不捕获就是裸崩，证明修复（try/except）是承重的。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    task_dir = ws / "04-专业课" / "_状态"
    task_dir.mkdir(parents=True)
    (task_dir / "今日任务.md").write_text(
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
        "|---|---|---|---|\n"
        "| 核心精讲 | 弯曲变形核心知识点梳理 | 30 分钟 | [ ] |\n",
        encoding="utf-8")
    monkeypatch.setattr(cli_shared, "ROOT", ws)

    _set_read_only(True)
    try:
        with pytest.raises(Exception) as ei:
            cli_shared.mark_today_task_done("核心知识点", "pro")
        assert ei.type.__name__ == "PermissionDeniedError"
    finally:
        _set_read_only(False)


def test_p1b_safe_mode_compose_graceful_refusal(tmp_path, monkeypatch, capsys):
    """组卷（save_file=True）在 safe 模式被拒时同样优雅 —— 旧实现裸崩。"""
    import tools.ky_io as ky_io

    class _Composer:
        @staticmethod
        def compose_exam_paper(subject, count=3, save_file=False):
            ky_io.guard_write("组卷落盘", "试卷.md")  # 真实写盘闸门
            return {"formatted_paper": "不应到达"}    # pragma: no cover

    loop_mod = _drive_repl(monkeypatch, ["组卷"])
    monkeypatch.setattr(loop_mod, "exam_composer", _Composer)

    _set_read_only(True)
    try:
        loop_mod.run_repl(permission_mode="safe")
    finally:
        _set_read_only(False)

    out = capsys.readouterr().out
    assert "[✘ 已拒绝]" in out
    assert "不应到达" not in out


# ═════════════════════════ P1-C 今日任务提示口令 ═════════════════════════

def test_p1c_custom_subject_name_hint_is_router_recognized(tmp_path):
    """自定义科目显示名（803 经济学综合）时，提示口令必须是路由表可识别口令。"""
    plan = {"pro_name": "803 经济学综合", "pro_hours": 2.0, "pro_books": "暂未放置",
            "pro_weakness": "无", "days_left": 80}
    res = ensure_subject_today_task(plan, "pro", workspace_root=tmp_path)
    content = Path(res["path"]).read_text(encoding="utf-8")
    m = re.search(r"发送「([^」]+)」", content)
    assert m, "今日任务缺少私教提示行"
    cmd = m.group(1)
    assert cmd in CHINESE_SUBJECT_MAP, (
        f"提示口令「{cmd}」不在路由表 —— 考生照做会落入 LLM 自由问答路径（计费+慢）")
    assert cmd == "专业课报到"


def test_p1c_all_four_subjects_hint_recognized(tmp_path):
    plan = {"math_name": "数学三 (303)", "math_books": "暂未放置",
            "eng_name": "英语一 (201)", "eng_books": "暂未放置",
            "pol_name": "思想政治理论", "pol_books": "暂未放置",
            "pro_name": "803 经济学综合", "pro_books": "暂未放置",
            "days_left": 80}
    expects = {"math": "数学报到", "eng": "英语报到", "pol": "政治报到", "pro": "专业课报到"}
    for sk, expect in expects.items():
        res = ensure_subject_today_task(plan, sk, workspace_root=tmp_path)
        content = Path(res["path"]).read_text(encoding="utf-8")
        m = re.search(r"发送「([^」]+)」", content)
        assert m and m.group(1) == expect, f"[{sk}] 提示口令异常: {m.group(1) if m else None}"
        assert m.group(1) in CHINESE_SUBJECT_MAP


def test_p1c_negative_control_old_hint_would_fall_through():
    """阴性对照：旧提示「{显示名}报到」不在路由表 —— 判别力证明。"""
    for old_hint in ("803 经济学综合报到", "308 护理综合报到", "数学三 (303)报到"):
        assert old_hint not in CHINESE_SUBJECT_MAP
    for new_cmd in ("数学报到", "英语报到", "政治报到", "专业课报到"):
        assert new_cmd in CHINESE_SUBJECT_MAP


# ═════════════════════════ P1-D 政治学员配置区漏更新 ═════════════════════════

_POL_AGENTS_TEMPLATE = """# 思想政治理论 AGENTS

### 学员配置区
- **目标分数**：`70+ 分` (摸底: 基础刚起步 / 摸底40分)
- **每日投入**：`1.0 小时`
- **核心书目**：`暂未放置实体资料（私教严格按【政治】官方考纲出题，严禁虚构书目）`
- **核心薄弱点**：`马原多选题漏选`
"""


def test_p1d_political_section_fully_updated(tmp_path):
    """模板自带「学员配置区」（常态路径）时，四行都必须按考生数据更新。"""
    pol_dir = tmp_path / "03-思想政治理论"
    pol_dir.mkdir(parents=True)
    (pol_dir / "AGENTS.md").write_text(_POL_AGENTS_TEMPLATE, encoding="utf-8")

    plan = {"pol_target": "68+ 分", "pol_baseline": "未启动", "pol_hours": 0.8,
            "pol_books": "肖秀荣精讲精练", "pol_weakness": "马原哲学原理"}
    update_subject_agents(plan, workspace_root=tmp_path)

    out = (pol_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert "- **目标分数**：`68+ 分` (摸底: 未启动)" in out
    assert "- **每日投入**：`0.8 小时`" in out
    assert "肖秀荣精讲精练" in out
    assert "马原哲学原理" in out
    assert "摸底40分" not in out, "模板残留凭空数据（考生从未提供）"
    assert "70+ 分" not in out


def test_p1d_negative_control_old_branch_leaves_template_residue():
    """阴性对照：旧分支只更新书目/薄弱点 —— 同模板上「摸底40分」残留、
    考生目标分从未写入，正向断言必红。"""
    t = _POL_AGENTS_TEMPLATE
    t = re.sub(r"- \*\*核心书目\*\*：.*", "- **核心书目**：`肖秀荣精讲精练`", t)
    t = re.sub(r"- \*\*核心薄弱点\*\*：.*", "- **核心薄弱点**：`马原哲学原理`", t)
    assert "摸底40分" in t, "对照前提不成立：旧逻辑竟未残留模板数据"
    assert "68+ 分" not in t
    assert "0.8 小时" not in t


# ═════════════════════════ P1-E ingest 回忆版碎片 ═════════════════════════

_RECALL_DOC = """# 801 材料力学 · 弯曲变形真题回忆（自拟·模拟测试）

## 真题回忆 1：叠加法求挠度

**题目（回忆版）**：矩形截面简支梁 AB，跨度 l = 4 m。
求跨中截面 C 的挠度 wC。

**回忆答案要点**：
1. 惯性矩 Iz = bh³/12。
2. 叠加：wC = wC1 + wC2 = 4.5 mm。

**易错点**：单位统一。

## 真题回忆 2：积分法确定转角

**题目（回忆版）**：悬臂梁长 l，自由端受集中力 F。
用积分法求自由端转角 θB。

**回忆答案要点**：
1. 弯矩方程 M(x) = -F·x。
2. θB = Fl²/(2EI)。
"""


def test_p1e_recall_doc_slices_by_question_markers():
    """回忆版：题目标记即题目边界，解答步骤不得成为伪题卡。"""
    chunks = MaterialIngestionPipeline().chunk_text(_RECALL_DOC, "801弯曲变形真题回忆")
    assert len(chunks) == 2, [c.stem[:24] for c in chunks]
    assert chunks[0].stem.startswith("矩形截面简支梁")
    assert chunks[1].stem.startswith("悬臂梁长")
    # 答案要点完整保留在对应题的答案正文里
    assert "惯性矩" in chunks[0].answer
    assert "弯矩方程" in chunks[1].answer
    # 旧缺陷形态：题干为「惯性矩 Iz = …」等解答碎片 / 粘连下一节标题
    for c in chunks:
        assert not c.stem.startswith("1. ")
        assert "惯性矩 Iz" not in c.stem
        assert "真题回忆" not in c.stem and "##" not in c.stem
        assert "真题回忆" not in c.answer


def test_p1e_dispatcher_routing_matches_python_engine():
    """命中题目标记/答案区结构的文档必须路由到 Python 参考实现（ext 环境产物一致）。"""
    default = MaterialIngestionPipeline().chunk_text(_RECALL_DOC, "src")
    pipe_py = MaterialIngestionPipeline()
    pipe_py._force_python = True
    py = pipe_py.chunk_text(_RECALL_DOC, "src")
    assert [(c.stem, c.answer) for c in default] == [(c.stem, c.answer) for c in py]


def test_p1e_inline_answer_point_does_not_swallow_next_question():
    """内联「**答案要点**：内容」不是答案区小节 —— 不得吞掉下一道编号题。"""
    doc = ("一、名词解释（每题5分）\n"
           "1. 齐物\n"
           "**回忆答案要点**：\n"
           "1. 我的理解一：庄子核心概念。\n"
           "2. 我的理解二：齐物论。\n\n"
           "2. 白马非马\n"
           "**答案要点**：公孙龙名实之辩。\n")
    chunks = MaterialIngestionPipeline().chunk_text(doc, "测试")
    stems = [c.stem for c in chunks]
    assert len(chunks) == 2, stems
    assert "齐物" in stems[0] and "白马非马" in stems[1]
    assert "我的理解" not in stems[0] and "我的理解" not in stems[1]
    # 解答步骤保留在上一题的答案正文中（此前被切断为空）
    assert "我的理解一" in chunks[0].answer


def test_p1e_negative_control_without_marker_support_fragments_recur(monkeypatch):
    """阴性对照：关闭题目标记与答案区识别（等价旧实现）后，同一文档重新切出碎片。"""
    pipe = MaterialIngestionPipeline()
    pipe._force_python = True
    _never = re.compile(r"(?!x)x")
    monkeypatch.setattr(MaterialIngestionPipeline, "_QUESTION_MARKER_PATTERN", _never)
    monkeypatch.setattr(MaterialIngestionPipeline, "_ANSWER_SEC_PATTERN", _never)

    chunks = pipe.chunk_text(_RECALL_DOC, "src")
    assert len(chunks) > 2, "旧缺陷未复现，对照前提不成立"
    assert any("惯性矩" in c.stem for c in chunks), "解答步骤未变成伪题卡题干"
