# -*- coding: utf-8 -*-
"""B2/B3/B4 工具机制工程回归测试（2026-10-08）。

本文件锁定（不得回退）：

1. **[B2] `list_tools` 动态发现**：``ToolRegistry.list_tools/describe_tools``
   活注册表枚举与宽容过滤（tier 非法=不过滤）；``list_tools`` LLM 工具可执行、
   输出清单与 ``describe_tools`` 同源；CLI ``ky tools list`` 行数据与该单一真源
   逐字段一致；
2. **[B3] skill 自描述 TOOL_SPEC 契约**：技能模块经 ``TOOL_SPEC`` + ``execute``
   自描述桥接工具；契约校验（三要素/schema/required 越界/缺 execute 均跳过）；
   level 归一（字符串名/整数/可调用；**缺省或非法 → DANGEROUS fail-closed**）；
3. **[B4] 4 项新桥接工具行为**：``dissect_english_sentence``（本地模板）、
   ``mount_materials``（只读盘点不落盘 + 动态定级）、``search_in_materials``
   （资料库检索 + pdf_path 过沙箱闸门）、``archive_experience``（写 tmp 工作区 +
   院校名防路径穿越）。

隔离约定：所有技能函数调用点一律 monkeypatch 或限定在 ``tmp_path`` —— 绝不
真实联网、绝不调用真实计费 LLM；注册表一律指向 tmp 工作区沙箱。
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
    TIER_ESSENTIAL,
    TIER_EXTENDED,
    ToolRegistry,
)

#: [B3/B4] 自描述工具 → 技能模块名（注册顺序）
SELF_DESCRIBED = {
    "dissect_english_sentence": "english_dissector",
    "mount_materials": "material_scanner",
    "archive_experience": "experience_dossier",
    "search_in_materials": "pdf_extractor",
}


def _registry(workspace_root, mode="safe") -> ToolRegistry:
    ws = Path(workspace_root)
    return ToolRegistry(
        sandbox=Sandbox(workspace_root=ws),
        permissions=PermissionManager(mode=mode, workspace_root=ws),
        config={},
    )


# ══════════════════════════════════════════════════════════════════════════
# ① [B2] list_tools / describe_tools 动态发现
# ══════════════════════════════════════════════════════════════════════════

def test_b2_list_tools_filters_and_lenient_semantics(tmp_path):
    """活注册表枚举：tier/source 精确过滤；all/非法值等价不过滤（宽容）。"""
    reg = _registry(tmp_path)
    total = len(reg.tools)

    assert len(reg.list_tools()) == total
    assert len(reg.list_tools(tier="all")) == total, "all = 不过滤"
    assert len(reg.list_tools(tier="bogus")) == total, "非法 tier 宽容不过滤"

    essential = reg.list_tools(tier=TIER_ESSENTIAL)
    extended = reg.list_tools(tier=TIER_EXTENDED)
    assert {t.tier for t in essential} == {TIER_ESSENTIAL}
    assert {t.tier for t in extended} == {TIER_EXTENDED}
    assert len(essential) + len(extended) == total

    skills = reg.list_tools(source="skill")
    assert len(skills) == 10, "6 集中声明 + 4 自描述"
    assert {t.source for t in skills} == {"skill"}
    assert reg.list_tools(source="mcp") == []
    # 组合过滤
    both = reg.list_tools(tier=TIER_EXTENDED, source="skill")
    assert {t.name for t in both} == {t.name for t in skills}


def test_b2_describe_tools_rows_single_source(tmp_path):
    """describe_tools 行字段口径：动态定级展示为 dynamic，静态级别给 level_name。"""
    reg = _registry(tmp_path)
    rows = {r["name"]: r for r in reg.describe_tools()}
    assert len(rows) == len(reg.tools)

    mount = rows["mount_materials"]
    assert mount["level"] == "dynamic"
    assert mount["level_name"] == "动态（按调用参数定级）"
    assert mount["tier"] == TIER_EXTENDED and mount["source"] == "skill"

    read_file = rows["read_file"]
    assert read_file["level"] == PermissionLevel.READ_ONLY
    assert isinstance(read_file["level_name"], str) and read_file["level_name"]
    for r in rows.values():
        assert {"name", "desc", "level", "level_name", "tier", "source"} <= set(r)


def test_b2_list_tools_llm_tool_executes(tmp_path):
    """list_tools 工具：extended/只读；输出清单与空结果提示可读。"""
    reg = _registry(tmp_path)
    td = reg.tools["list_tools"]
    assert td.tier == TIER_EXTENDED
    assert td.level == PermissionLevel.READ_ONLY

    out = reg.execute_tool("list_tools", {}, interactive=False)
    assert out.startswith(f"当前可用工具 {len(reg.tools)} 个")
    assert "read_file" in out and "grade_exam_paper" in out
    assert "mount_materials" in out

    out_ess = reg.execute_tool("list_tools", {"tier": "essential"}, interactive=False)
    assert out_ess.startswith("当前可用工具 18 个")

    out_empty = reg.execute_tool("list_tools", {"source": "mcp"}, interactive=False)
    assert "没有匹配的工具" in out_empty


def test_b2_cli_rows_match_describe_tools(tmp_path, monkeypatch, capsys):
    """CLI ``ky tools list --json`` 行数据 = describe_tools 单一真源（逐字段）。"""
    from tools.cli.commands import system as system_mod

    monkeypatch.setattr(system_mod, "ROOT", tmp_path)
    assert system_mod._cmd_tools(["tools", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    reg = _registry(tmp_path)
    src = {r["name"]: r for r in reg.describe_tools()}
    assert {t["name"] for t in payload["tools"]} == set(src)
    for row in payload["tools"]:
        s = src[row["name"]]
        assert row["tier"] == s["tier"] and row["source"] == s["source"]
        assert row["level"] == s["level"] and row["level_name"] == s["level_name"]
        assert row["desc"] == s["desc"]


# ══════════════════════════════════════════════════════════════════════════
# ② [B3] 自描述 TOOL_SPEC 契约
# ══════════════════════════════════════════════════════════════════════════

def test_b3_shipped_tool_specs_are_valid():
    """4 个随包自描述技能模块的 TOOL_SPEC 满足契约三要素 + execute 入口。"""
    assert set(skill_bridge.SELF_DESCRIBING_SKILLS) == set(SELF_DESCRIBED.values())
    for module_name in skill_bridge.SELF_DESCRIBING_SKILLS:
        mod = skill_bridge.load_skill_module(module_name)
        assert mod is not None, module_name
        spec = getattr(mod, "TOOL_SPEC", None)
        assert isinstance(spec, dict), module_name
        assert str(spec.get("name") or "").strip(), module_name
        assert str(spec.get("description") or "").strip(), module_name
        params = spec.get("parameters")
        assert params.get("type") == "object", module_name
        assert isinstance(params.get("properties"), dict), module_name
        assert set(params.get("required", [])) <= set(params["properties"]), module_name
        assert callable(getattr(mod, "execute", None)), module_name


def test_b3_registered_self_described_tools_metadata(tmp_path):
    """自描述工具注册进注册表：名字/来源/档位与声明一致。"""
    reg = _registry(tmp_path)
    for tool_name, module_name in SELF_DESCRIBED.items():
        td = reg.tools[tool_name]
        assert td.source == "skill" and td.tier == TIER_EXTENDED, tool_name
        mod = skill_bridge.load_skill_module(module_name)
        assert td.desc == mod.TOOL_SPEC["description"]
        assert td.params_schema == mod.TOOL_SPEC["parameters"]


class _FakeMod:
    """伪技能模块：用于契约校验边界（不触碰任何真实技能）。"""

    def __init__(self, spec, has_exec=True):
        self.__name__ = "skills.fake_probe"
        self.TOOL_SPEC = spec
        if has_exec:
            self.execute = lambda args, ctx=None: "ok"


_GOOD_SPEC = {
    "name": "fake_tool",
    "description": "样本工具",
    "parameters": {"type": "object", "properties": {"x": {"type": "string"}},
                   "required": ["x"]},
    "level": "read_only",
}


def test_b3_malformed_specs_are_skipped(monkeypatch, tmp_path):
    """三要素不全 / schema 非 object / required 越界 / 缺 execute → 跳过不注册。"""
    reg = _registry(tmp_path)
    monkeypatch.setattr(skill_bridge, "SELF_DESCRIBING_SKILLS", ("english_dissector",))

    cases = []
    bad = dict(_GOOD_SPEC, name="")
    cases.append(("缺 name", _FakeMod(bad)))
    bad = dict(_GOOD_SPEC)
    bad.pop("description")
    cases.append(("缺 description", _FakeMod(bad)))
    bad = dict(_GOOD_SPEC, parameters={"type": "array"})
    cases.append(("schema 非 object", _FakeMod(bad)))
    bad = dict(_GOOD_SPEC, parameters={"type": "object", "properties": {"x": {}},
                                       "required": ["y"]})
    cases.append(("required 越界", _FakeMod(bad)))
    cases.append(("缺 execute", _FakeMod(dict(_GOOD_SPEC), has_exec=False)))

    for label, fake in cases:
        monkeypatch.setattr(skill_bridge, "load_skill_module", lambda n, _f=fake: _f)
        assert skill_bridge.build_self_described_specs(reg) == [], label


def test_b3_level_normalization_fail_closed(monkeypatch, tmp_path):
    """level：字符串名/整数/可调用归一；缺省或非法一律 DANGEROUS。"""
    reg = _registry(tmp_path)
    monkeypatch.setattr(skill_bridge, "SELF_DESCRIBING_SKILLS", ("english_dissector",))

    def _level_of(spec):
        monkeypatch.setattr(skill_bridge, "load_skill_module",
                            lambda n, _s=spec: _FakeMod(_s))
        specs = skill_bridge.build_self_described_specs(reg)
        assert len(specs) == 1
        return specs[0].level

    assert _level_of(dict(_GOOD_SPEC, level="safe_edit")) == PermissionLevel.SAFE_EDIT
    assert _level_of(dict(_GOOD_SPEC, level="NETWORK")) == PermissionLevel.NETWORK
    assert _level_of(dict(_GOOD_SPEC, level=2)) == PermissionLevel.LOW_RISK_EXEC
    assert _level_of(dict(_GOOD_SPEC, level="bogus_name")) == PermissionLevel.DANGEROUS
    assert _level_of(dict(_GOOD_SPEC, level=99)) == PermissionLevel.DANGEROUS
    assert _level_of(dict(_GOOD_SPEC, level=True)) == PermissionLevel.DANGEROUS
    missing = dict(_GOOD_SPEC)
    missing.pop("level")
    assert _level_of(missing) == PermissionLevel.DANGEROUS, "缺省必须 fail-closed"

    # 可调用：按参数动态定级；内部异常同样 fail-closed
    dyn = _level_of(dict(_GOOD_SPEC, level=lambda a: "safe_edit" if a.get("x") else "read_only"))
    assert callable(dyn)
    assert dyn({"x": "1"}) == PermissionLevel.SAFE_EDIT
    assert dyn({}) == PermissionLevel.READ_ONLY
    boom = _level_of(dict(_GOOD_SPEC, level=lambda a: 1 / 0))
    assert boom({}) == PermissionLevel.DANGEROUS


def test_b3_handler_ctx_and_error_conventions(monkeypatch, tmp_path):
    """handler：interactive 从模型参数弹出并入 ctx；异常转可读文案。"""
    reg = _registry(tmp_path)
    monkeypatch.setattr(skill_bridge, "SELF_DESCRIBING_SKILLS", ("english_dissector",))
    captured = {}

    def _exec(args, ctx=None):
        captured.update(args=dict(args), ctx=dict(ctx or {}))
        return "样本输出"

    fake = _FakeMod(dict(_GOOD_SPEC))
    fake.execute = _exec
    monkeypatch.setattr(skill_bridge, "load_skill_module", lambda n: fake)

    specs = skill_bridge.build_self_described_specs(reg)
    handler = specs[0].handler
    out = handler(x="1", interactive=False)
    assert out == "样本输出"
    assert captured["args"] == {"x": "1"}, "interactive 不得进入模型参数"
    assert captured["ctx"]["interactive"] is False
    assert captured["ctx"]["workspace_root"] == Path(tmp_path)
    assert captured["ctx"]["registry"] is reg

    # 执行异常 → 可读文案；SecurityException 原样上抛（交 execute_tool 转 SecurityError）
    fake.execute = lambda args, ctx=None: (_ for _ in ()).throw(ValueError("样本崩溃"))
    out2 = specs[0].handler(x="1")
    assert out2.startswith("Error 技能工具执行失败:") and "样本崩溃" in out2

    from tools.agent.sandbox import SecurityException
    fake.execute = lambda args, ctx=None: (_ for _ in ()).throw(SecurityException("样本拦截"))
    with pytest.raises(SecurityException):
        specs[0].handler(x="1")


# ══════════════════════════════════════════════════════════════════════════
# ③ [B4] 四项新桥接工具行为
# ══════════════════════════════════════════════════════════════════════════

def test_b4_dissect_english_sentence(tmp_path):
    """长难句拆解：模板含原句与五步结构；空句报错；非英语给提示。"""
    reg = _registry(tmp_path)
    out = reg.execute_tool(
        "dissect_english_sentence",
        {"sentence": "Although the theory is complex, students can master it."},
        interactive=False)
    assert "Although the theory is complex" in out
    assert "主干骨架" in out and "两步翻译法" in out

    out_err = reg.execute_tool("dissect_english_sentence", {"sentence": "  "},
                               interactive=False)
    assert out_err.startswith("Error")

    out_zh = reg.execute_tool("dissect_english_sentence",
                              {"sentence": "这是一句中文，用来触发非英语提示。"},
                              interactive=False)
    assert "可能不是英语句子" in out_zh


def _workspace_files(root: Path):
    """工作区文件集（排除 .kaoyan_chain_audit：conftest 把审批审计隔离到
    per-test 临时目录，权限层每次 check_permission 的既定副作用，非工具落盘）。"""
    return {p for p in root.rglob("*") if ".kaoyan_chain_audit" not in p.parts}


def test_b4_mount_materials_readonly_and_dynamic_level(tmp_path):
    """资料盘点：只读不落盘；apply=True（NETWORK）在 safe 模式被拒。"""
    (tmp_path / "01-数学" / "参考资料").mkdir(parents=True)
    (tmp_path / "01-数学" / "参考资料" / "样本习题册.md").write_text(
        "# 样本习题册\n" + "第一章 函数极限连续。\n" * 8, encoding="utf-8")

    reg = _registry(tmp_path)
    before = _workspace_files(tmp_path)
    out = reg.execute_tool("mount_materials", {}, interactive=False)
    assert out.startswith("【参考资料盘点 · 只读预览】")
    assert "样本习题册.md" in out
    assert _workspace_files(tmp_path) == before, "只读盘点不得落盘"

    td = reg.tools["mount_materials"]
    # [AGENT-H1·2026-10-09] apply=true 且 auto_scout_school 缺省（true）在目标
    # 档案缺失时会真实联网侦察 → 定级 NETWORK；显式关闭侦察才回落 SAFE_EDIT。
    assert td.level({"apply": True}) == PermissionLevel.NETWORK
    assert td.level({"apply": True, "auto_scout_school": False}) == PermissionLevel.SAFE_EDIT
    assert td.level({"apply": False}) == PermissionLevel.READ_ONLY
    assert td.level({}) == PermissionLevel.READ_ONLY

    out_deny = reg.execute_tool("mount_materials", {"apply": True}, interactive=False)
    assert out_deny.startswith("PermissionDenied"), out_deny


def test_b4_search_in_materials(tmp_path, monkeypatch):
    """资料库检索：命中/行号/未命中说明；pdf_path 过沙箱闸门且委托到 PDF 检索。"""
    eng = tmp_path / "02-英语" / "参考资料"
    eng.mkdir(parents=True)
    (eng / "样本真题.md").write_text("第一行\n边际效用是核心考点\n第三行\n",
                                     encoding="utf-8")
    reg = _registry(tmp_path)

    out = reg.execute_tool("search_in_materials", {"keyword": "边际效用"}, interactive=False)
    assert "样本真题.md" in out and "边际效用" in out and ":L2]" in out

    out_miss = reg.execute_tool("search_in_materials", {"keyword": "绝不存在的词"},
                                interactive=False)
    assert "未命中" in out_miss

    # pdf_path 穿越 → SecurityError，且 PDF 检索函数不得被触达
    pdf_mod = skill_bridge.load_skill_module("pdf_extractor")
    called = {"n": 0}
    monkeypatch.setattr(pdf_mod, "find_questions_by_keyword",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    out_trav = reg.execute_tool("search_in_materials",
                                {"keyword": "x", "pdf_path": "../../outside.pdf"},
                                interactive=False)
    assert out_trav.startswith("SecurityError")
    assert called["n"] == 0, "被沙箱拒绝的路径不得触达 PDF 检索"

    # 工作区内 PDF：过闸门后委托 find_questions_by_keyword（打桩，不解析真实 PDF）
    pdf = tmp_path / "样本真题册.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")
    seen = {}

    def _fake_find(path, keyword, n):
        seen.update(path=Path(path), keyword=keyword, n=n)
        return ["[第 3 页 / 考点相关片段]:\n样本片段"]

    monkeypatch.setattr(pdf_mod, "find_questions_by_keyword", _fake_find)
    out_pdf = reg.execute_tool("search_in_materials",
                               {"keyword": "样本词", "pdf_path": "样本真题册.pdf",
                                "max_results": 5},
                               interactive=False)
    assert seen["path"].resolve() == pdf.resolve()
    assert seen["keyword"] == "样本词" and seen["n"] == 5
    assert "样本片段" in out_pdf


def test_b4_archive_experience(tmp_path):
    """经验归档：safe 模式拒写；auto 模式落盘 tmp；院校名防路径穿越。"""
    reg = _registry(tmp_path)
    out_safe = reg.execute_tool("archive_experience",
                                {"school_name": "样本大学", "content": "样本正文"},
                                interactive=False)
    assert out_safe.startswith("PermissionDenied")
    assert not (tmp_path / ".memory" / "experiences").exists()

    reg_auto = _registry(tmp_path, mode="auto")
    reg_auto.permissions.force_allow_all = True
    out = reg_auto.execute_tool(
        "archive_experience",
        {"school_name": "样本大学", "content": "复试重视基础。", "title": "复试经验",
         "url": "https://sample.invalid/x"},
        interactive=False)
    assert out.startswith("Success:")
    dossier = tmp_path / ".memory" / "experiences" / "样本大学.md"
    assert dossier.exists()
    text = dossier.read_text(encoding="utf-8")
    assert "复试重视基础。" in text and "复试经验" in text

    for bad_school in ("../../evil", "a/b", "a\\b", "a:b"):
        out_bad = reg_auto.execute_tool("archive_experience",
                                        {"school_name": bad_school, "content": "x"},
                                        interactive=False)
        assert out_bad.startswith("Error"), bad_school
    assert not (tmp_path.parent / "evil.md").exists()
