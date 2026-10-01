# -*- coding: utf-8 -*-
"""K5 批次回归 · 技能桥接工具与工具注册表分级。

本文件锁定（不得回退）：

1. **6 项领域技能入工具表**：``exam_composer`` / ``open_grader`` /
   ``vision_solver`` / ``wechat_searcher`` / ``knowledge_map`` /
   ``exam_diagnoser`` 经 ``skill_bridge`` 注册为 Agent 工具，schema 合法
   （``type=object`` 且 ``required ⊆ properties``）、``tier/level/source``
   元数据齐全、无重名；
2. **分级口径**：essential 18 / extended 12 / all 30；``web_search`` 置顶、
   新工具排尾；``names`` 收尾受限集语义不变；
3. **技能缺失不崩**：技能模块不可用（公开副本 / 裁剪安装）时 handler 返回
   「Error: 未加载 xxx 技能」，而不是崩溃或「未知工具」；注册表构建与技能
   可用性**无关**（计数稳定，公开副本同样绿）；
4. **动态定级**：``grade_exam_paper`` 的 ``auto_advance=False`` → Level 0
   （safe 模式也放行，只读判分）；缺省/true → Level 1（有 FSRS/错题写副作用）；
5. **tier 逃生门**：``ky_config.json`` 的 ``agent.tool_tier``（all 默认 /
   essential），非法值回落 all（绝不静默丢工具）；
6. **ky tools list**：``--json`` 可解析、计数与注册表一致；MCP 工具默认跳过、
   ``--with-mcp`` 才现场加载；``--tier`` 过滤生效。

隔离约定：所有技能函数调用点一律 monkeypatch 打桩 —— 绝不真实联网、绝不调用
真实计费 LLM；工作区文件只写 ``tmp_path``。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent import skill_bridge  # noqa: E402
from tools.agent.permissions import PermissionLevel, PermissionManager  # noqa: E402
from tools.agent.sandbox import Sandbox  # noqa: E402
from tools.agent.tools_impl import (  # noqa: E402
    TIER_ALL,
    TIER_ESSENTIAL,
    TIER_EXTENDED,
    ToolRegistry,
)

#: essential 18：文件/检索/执行/基础考研能力（高频常驻 schema）
ESSENTIAL_TOOLS = {
    "read_file", "write_file", "edit_file", "list_directory", "search_files",
    "grep", "run_command", "git_status", "git_diff", "fetch_url",
    "download_file", "web_search", "read_exam_paper", "verify_math",
    "socratic_hint", "log_mistake", "review_mistakes", "manage_memory",
}

#: extended 6（内置部分）：破坏性/低频能力（delete_file 有意不常驻 schema）
EXTENDED_BUILTIN_TOOLS = {
    "delete_file", "search_variant", "compose_exam", "scout_school",
    "diff_syllabus", "ingest_exam_material",
}

#: extended 6（本批新增）：技能桥接工具 → 技能模块名（声明顺序即注册顺序）
SKILL_TOOL_MODULES = {
    "grade_exam_paper": "exam_composer",
    "grade_open_question": "open_grader",
    "solve_vision": "vision_solver",
    "search_wechat": "wechat_searcher",
    "map_knowledge": "knowledge_map",
    "diagnose_exam": "exam_diagnoser",
}


def _registry(workspace_root=None, mode="safe", config=None) -> ToolRegistry:
    ws = Path(workspace_root or ROOT)
    return ToolRegistry(
        sandbox=Sandbox(workspace_root=ws),
        permissions=PermissionManager(mode=mode, workspace_root=ws),
        config=config,
    )


# ══════════════════════════════════════════════════════════════════════════
# ① 注册表结构与元数据
# ══════════════════════════════════════════════════════════════════════════

def test_registry_has_30_tools_with_valid_metadata():
    """30 个工具（24 内置 + 6 技能桥接）schema 合法、元数据齐全、无重名。"""
    reg = _registry()
    assert len(reg.tools) == 24 + len(SKILL_TOOL_MODULES) == 30
    for name, td in reg.tools.items():
        assert td.name == name, f"ToolDefinition.name 与注册键不一致: {name}"
        assert td.tier in (TIER_ESSENTIAL, TIER_EXTENDED), f"{name}: 非法 tier {td.tier}"
        assert td.source in ("builtin", "skill", "mcp"), f"{name}: 非法 source {td.source}"
        assert callable(td.func), f"{name}: func 不可调用"
        assert isinstance(td.level, int) or callable(td.level), f"{name}: level 类型非法"
        schema = td.params_schema
        assert schema.get("type") == "object", f"{name}: params_schema.type 必须为 object"
        props = schema.get("properties")
        assert isinstance(props, dict), f"{name}: params_schema.properties 必须为 dict"
        required = schema.get("required", [])
        assert isinstance(required, list), f"{name}: required 必须为 list"
        assert set(required) <= set(props), f"{name}: required 不在 properties 内: {required}"
    # 幂等注册：重复调用不新增、不覆盖（无重名/重复登记）
    assert skill_bridge.register_skill_tools(reg) == []
    # 内置与技能工具名不得交叠
    assert not (ESSENTIAL_TOOLS | EXTENDED_BUILTIN_TOOLS) & set(SKILL_TOOL_MODULES)


def test_tier_partition_counts_and_ordering():
    """分级计数（18/12/30）+ web_search 置顶 + essential 段在前、新工具排尾。"""
    reg = _registry()
    essential = {n for n, t in reg.tools.items() if t.tier == TIER_ESSENTIAL}
    extended = {n for n, t in reg.tools.items() if t.tier == TIER_EXTENDED}
    assert essential == ESSENTIAL_TOOLS
    assert extended == EXTENDED_BUILTIN_TOOLS | set(SKILL_TOOL_MODULES)

    assert len(reg.get_openai_tools(tier=TIER_ESSENTIAL)) == 18
    assert len(reg.get_openai_tools(tier=TIER_EXTENDED)) == 12
    assert len(reg.get_openai_tools()) == 30
    assert len(reg.get_openai_tools(tier=TIER_ALL)) == 30

    order = [t["function"]["name"] for t in reg.get_openai_tools()]
    assert len(order) == len(set(order)) == 30, "工具名不得重复"
    assert order[0] == "web_search", "web_search 必须置顶（W10 检索行为引导）"
    tiers = [reg.tools[n].tier for n in order]
    first_ext = tiers.index(TIER_EXTENDED)
    assert all(t == TIER_EXTENDED for t in tiers[first_ext:]), "extended 必须整体排在 essential 之后"
    assert order[-len(SKILL_TOOL_MODULES):] == list(SKILL_TOOL_MODULES), "新技能工具必须排尾"


def test_names_subset_semantics_unchanged():
    """收尾受限集（names）语义不变：只回清单内工具、忽略 tier、未知名静默忽略。"""
    reg = _registry()
    got = [t["function"]["name"] for t in
           reg.get_openai_tools(names=["read_file", "web_search", "no_such_tool"])]
    assert got == ["read_file", "web_search"]
    assert reg.get_openai_tools(names=[]) == []


# ══════════════════════════════════════════════════════════════════════════
# ② tier 逃生门（agent.tool_tier）
# ══════════════════════════════════════════════════════════════════════════

def test_tier_escape_hatch_from_config(tmp_path):
    """ky_config.json 的 agent.tool_tier 可收紧常驻 schema；非法值回落 all。"""
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"agent": {"tool_tier": "essential"}}), encoding="utf-8")
    reg = _registry(workspace_root=tmp_path)
    assert len(reg.get_openai_tools()) == 18, "agent.tool_tier=essential 应只回 essential"

    # 显式传入的 config 优先于文件
    reg2 = _registry(workspace_root=tmp_path, config={"agent": {"tool_tier": "all"}})
    assert len(reg2.get_openai_tools()) == 30

    # 非法值 / 无配置 → all（宁可多给工具，绝不静默丢工具）
    reg3 = _registry(workspace_root=tmp_path, config={"agent": {"tool_tier": "bogus"}})
    assert len(reg3.get_openai_tools()) == 30
    reg4 = _registry(workspace_root=tmp_path, config={})
    assert len(reg4.get_openai_tools()) == 30


# ══════════════════════════════════════════════════════════════════════════
# ③ 技能缺失不崩（公开副本 / 裁剪安装）
# ══════════════════════════════════════════════════════════════════════════

def test_registration_is_independent_of_skill_availability(monkeypatch):
    """注册与技能可用性无关：模块全缺失时仍是 30 个工具（extended/skill 元数据不变）。"""
    monkeypatch.setattr(skill_bridge, "load_skill_module", lambda name: None)
    reg = _registry()
    assert len(reg.tools) == 30
    for name in SKILL_TOOL_MODULES:
        td = reg.tools[name]
        assert td.tier == TIER_EXTENDED and td.source == "skill"


def test_skill_missing_returns_error_and_does_not_crash(monkeypatch):
    """6 个新工具在技能缺失时返回「Error: 未加载 xxx 技能」而非崩溃。"""
    monkeypatch.setattr(skill_bridge, "load_skill_module", lambda name: None)
    reg = _registry()
    cases = {
        "grade_exam_paper": {"paper": "样本卷面", "answers": "1. A"},
        "grade_open_question": {"question": "样本题面", "student_answer": "样本作答"},
        "solve_vision": {"image_path": "样本图片.png"},
        "search_wechat": {"keyword": "样本关键词"},
        "map_knowledge": {"subject": "math"},
        "diagnose_exam": {"exam_input": "第 1 题 计算失误 扣 2 分"},
    }
    assert set(cases) == set(SKILL_TOOL_MODULES)
    for name, kwargs in cases.items():
        out = reg.tools[name].func(**kwargs)
        assert out.startswith("Error: 未加载 "), (name, out)
        assert SKILL_TOOL_MODULES[name] in out, (name, out)


# ══════════════════════════════════════════════════════════════════════════
# ④ grade_exam_paper 动态定级
# ══════════════════════════════════════════════════════════════════════════

def test_grade_exam_paper_dynamic_level():
    """auto_advance=False → READ_ONLY；缺省/true → SAFE_EDIT（有 FSRS 写副作用）。"""
    td = _registry().tools["grade_exam_paper"]
    assert callable(td.level)
    assert td.level({"auto_advance": False}) == PermissionLevel.READ_ONLY
    assert td.level({"auto_advance": True}) == PermissionLevel.SAFE_EDIT
    assert td.level({}) == PermissionLevel.SAFE_EDIT, "缺省 auto_advance 视为有写副作用"


def test_grade_exam_paper_safe_mode_end_to_end(monkeypatch):
    """端到端：safe 模式下 auto_advance=False 放行（Level 0），缺省被拒（Level 1）。"""
    monkeypatch.setattr(skill_bridge, "load_skill_module", lambda name: None)
    reg = _registry(mode="safe")
    out = reg.execute_tool(
        "grade_exam_paper",
        {"paper": "样本卷面", "answers": "1. A", "auto_advance": False},
        interactive=False)
    assert not out.startswith("PermissionDenied"), out
    assert out.startswith("Error: 未加载 exam_composer 技能"), out

    out2 = reg.execute_tool("grade_exam_paper", {"paper": "样本卷面", "answers": "1. A"},
                            interactive=False)
    assert out2.startswith("PermissionDenied"), out2


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 技能可用时 handler 真调模块（打桩，绝不真实联网/计费）
# ══════════════════════════════════════════════════════════════════════════

def _require(skill_id: str):
    """私有技能守卫：公开副本未发布该技能时跳过（不整批红）。"""
    mod = skill_bridge.load_skill_module(skill_id)
    if mod is None:
        pytest.skip(f"技能 {skill_id} 不可用（公开副本/裁剪安装），跳过委托验证")
    return mod


def test_grade_exam_paper_delegates(monkeypatch):
    mod = _require("exam_composer")
    captured = {}

    def _fake(paper, answers, subject="math", auto_advance=True):
        captured.update(paper=paper, answers=answers, subject=subject,
                        auto_advance=auto_advance)
        return {"success": True, "score": 8, "total_score": 10, "accuracy": 80.0,
                "report": "样本判分报告"}

    monkeypatch.setattr(mod, "grade_exam_paper", _fake)
    out = _registry().tools["grade_exam_paper"].func(
        paper="样本卷面", answers="1. A", auto_advance=False)
    assert out == "样本判分报告"
    assert captured == {"paper": "样本卷面", "answers": "1. A", "subject": "math",
                        "auto_advance": False}


def test_grade_open_question_formats_result(monkeypatch):
    mod = _require("open_grader")
    captured = {}

    class _Res:
        match_level = 2
        score = 8.5
        confidence = 0.9
        mistake_type = "无"
        reason = "样本判定依据"
        degraded = False
        error = ""

    def _fake(**kwargs):
        captured.update(kwargs)
        return _Res()

    monkeypatch.setattr(mod, "grade_open_question", _fake)
    out = _registry().tools["grade_open_question"].func(
        question="样本题面", student_answer="样本作答", subject="pro")
    assert "通过" in out and "8.5" in out and "样本判定依据" in out
    assert captured["question"] == "样本题面" and captured["subject"] == "pro"
    assert captured["reference_answer"] == ""


def test_solve_vision_path_gate_and_llm_config(tmp_path, monkeypatch):
    """图片路径过沙箱（越界拒绝）；LLM 配置来自沙箱工作区根（get_llm_config 口径）。"""
    mod = _require("vision_solver")
    img = tmp_path / "样本草稿.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n-sample")
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"api_key": "sk-样本key", "base_url": "https://sample.invalid/v1",
                    "model": "sample-model"}), encoding="utf-8")
    captured = {}

    def _fake(path, prompt, config, stream=True):
        captured.update(path=path, prompt=prompt, config=config, stream=stream)
        return "样本视觉回复"

    monkeypatch.setattr(mod, "solve_image_with_model", _fake)
    reg = _registry(workspace_root=tmp_path, mode="auto")
    reg.permissions.force_allow_all = True   # 只跳过审批通道，不跳过沙箱

    out = reg.execute_tool("solve_vision", {"image_path": "样本草稿.png", "prompt": "看步骤"},
                           interactive=False)
    assert out == "样本视觉回复"
    assert Path(captured["path"]).resolve() == img.resolve()
    assert captured["stream"] is False
    assert captured["config"].get("api_key") == "sk-样本key", "配置必须来自沙箱工作区根"

    # 相对穿越出工作区 → 沙箱拒绝，且视觉引擎不得被调用
    captured.clear()
    out2 = reg.execute_tool("solve_vision", {"image_path": "../../k5_outside_probe.png"},
                            interactive=False)
    assert out2.startswith("SecurityError"), out2
    assert captured == {}, "被沙箱拒绝的路径不得触达视觉引擎"


def test_search_wechat_formats_results(monkeypatch):
    mod = _require("wechat_searcher")
    captured = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return {"keyword": "样本关键词", "total": 1, "fetched": 1,
                "source_status": ["sogou: ok"],
                "results": [{"title": "样本文章", "url": "https://mp.weixin.qq.com/s/sample",
                             "account_display": "样本公众号", "date_display": "2026-09-30",
                             "summary": "样本摘要"}],
                "saved_paths": [], "source_errors": []}

    monkeypatch.setattr(mod, "wechat_search", _fake)
    out = _registry().tools["search_wechat"].func(keyword="样本关键词", max_results=3)
    assert "样本文章" in out and "https://mp.weixin.qq.com/s/sample" in out
    assert captured["max_results"] == 3
    assert captured["fetch_content"] is False and captured["save_to_local"] is False


def test_map_knowledge_and_diagnose_exam_delegate(monkeypatch):
    km = _require("knowledge_map")
    monkeypatch.setattr(km, "format_knowledge_map_table",
                        lambda subject="math": f"【样本图谱 {subject}】")
    assert _registry().tools["map_knowledge"].func(subject="pro") == "【样本图谱 pro】"

    ed = _require("exam_diagnoser")
    monkeypatch.setattr(ed, "diagnose_mock_exam",
                        lambda subject="math", exam_input="": {"report": f"样本诊断[{subject}]:{exam_input}"})
    monkeypatch.setattr(ed, "format_diagnosis_report", lambda res: res["report"])
    out = _registry().tools["diagnose_exam"].func(exam_input="第1题 计算失误", subject="math")
    assert out == "样本诊断[math]:第1题 计算失误"


# ══════════════════════════════════════════════════════════════════════════
# ⑥ ky tools list（CLI 审计入口）
# ══════════════════════════════════════════════════════════════════════════

def test_tools_cli_list_json_counts_and_tier_filter(tmp_path, monkeypatch, capsys):
    from tools.cli.commands import system as system_mod

    # 隔离：把 ROOT 指到空工作区，避免真实 ky_config.json 的 agent.tool_tier 影响计数
    monkeypatch.setattr(system_mod, "ROOT", tmp_path)

    assert system_mod._cmd_tools(["tools", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 30 == len(payload["tools"])
    assert {t["name"] for t in payload["tools"]} == set(_registry().tools)
    assert all({"name", "tier", "level", "source", "desc"} <= set(t) for t in payload["tools"])

    assert system_mod._cmd_tools(["tools", "list", "--json", "--tier=essential"]) == 0
    payload_ess = json.loads(capsys.readouterr().out)
    assert payload_ess["count"] == 18
    assert {t["tier"] for t in payload_ess["tools"]} == {"essential"}

    assert system_mod._cmd_tools(["tools", "list", "--json", "--tier=extended"]) == 0
    payload_ext = json.loads(capsys.readouterr().out)
    assert payload_ext["count"] == 12
    assert {t["name"] for t in payload_ext["tools"]} == EXTENDED_BUILTIN_TOOLS | set(SKILL_TOOL_MODULES)

    # 未知 tier：显式失败（非零退出码），不静默输出
    assert system_mod._cmd_tools(["tools", "list", "--tier=bogus"]) == 1
    # 缺子命令：给用法并失败
    assert system_mod._cmd_tools(["tools"]) == 1


def test_tools_cli_text_smoke(tmp_path, monkeypatch, capsys):
    from tools.cli.commands import system as system_mod

    monkeypatch.setattr(system_mod, "ROOT", tmp_path)
    assert system_mod._cmd_tools(["tools", "list"]) == 0
    out = capsys.readouterr().out
    assert "Agent 工具注册表" in out and "30 项" in out
    assert "grade_exam_paper" in out and "solve_vision" in out


def test_tools_cli_skips_mcp_unless_flagged(tmp_path, monkeypatch, capsys):
    """MCP 工具默认跳过；--with-mcp 才现场加载（打桩，不启动任何子进程）。"""
    from tools.agent import mcp_client as mcp_mod
    from tools.cli.commands import system as system_mod

    monkeypatch.setattr(system_mod, "ROOT", tmp_path)

    fake_tool = {
        "scoped_name": "mcp_sample_srv_probe",
        "mcp_server": "sample_srv",
        "orig_name": "probe",
        "description": "[MCP: sample_srv] 样本工具",
        "inputSchema": {"type": "object", "properties": {}},
    }
    monkeypatch.setattr(mcp_mod.MCPClientManager, "load_from_config",
                        lambda self, *a, **k: None)
    monkeypatch.setattr(mcp_mod.MCPClientManager, "get_all_mcp_tools",
                        lambda self: [fake_tool])
    monkeypatch.setattr(mcp_mod.MCPClientManager, "close_all", lambda self: None)

    assert system_mod._cmd_tools(["tools", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "mcp_sample_srv_probe" not in {t["name"] for t in payload["tools"]}
    assert payload["count"] == 30

    assert system_mod._cmd_tools(["tools", "list", "--json", "--with-mcp"]) == 0
    payload_mcp = json.loads(capsys.readouterr().out)
    names = {t["name"] for t in payload_mcp["tools"]}
    assert "mcp_sample_srv_probe" in names
    assert payload_mcp["count"] == 31
    row = next(t for t in payload_mcp["tools"] if t["name"] == "mcp_sample_srv_probe")
    assert row["tier"] == TIER_EXTENDED and row["source"] == "mcp"
