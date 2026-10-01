# -*- coding: utf-8 -*-
"""K6 批次回归 · KaoyanContext 统一上下文 + school_scope_guard 院校范围守卫。

背景（agent 内核修复方案 K6）：科目/数学编码/目标院校/初试日期此前散落
在各处各自读盘（loop 内联 ctx、context_engine 读 ky_config.json、hooks 从
context 取键），存在「hook 按目标校 A 拦截、系统提示却挂载目标校 B 考情」
的漂移风险。本批收敛为一份 frozen 的 KaoyanContext，并新增院校范围守卫
（防止模型脱离学员目标院校自行侦察无关院校）。

本文件锁定（不得回退）：
1. ``from_config`` 字段解析与 ``active_subject`` / ``math_key`` 的
   **旧 ctx 逐字同语义**（含配置缺键时 math_key 为 None 的历史行为）；
2. ``hook_ctx`` 键与 hooks.py 现有读取键兼容；frozen 不可变 +
   ``with_subject`` 的 math_key 归一；
3. ``school_scope_guard`` 四条判据：目标校一致放行 / 学员提及放行（含
   别名归一）/ 模型自创校名阻断 / 空目标完全 no-op；
4. 放行非目标校时 PostToolUse 追加对照澄清；跨校对比不误伤；
5. ``build_system_prompt(kaoyan_ctx=...)`` 优先取 ctx 不读盘，None 保持
   旧行为（读 ky_config.json）；
6. ``AgentRunner`` 接线：构造一次 + 每次 run 按最新配置重建（热切换）。

全程离线：AgentRunner 路径 monkeypatch ``_call_llm``；夹具全部使用
合成校名（合成农业大学 / 虚构理工学院），不含任何真实身份信息。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.hooks import (  # noqa: E402
    HookManager,
    _school_aliases,
    _school_mentioned,
    _same_school,
)
from tools.agent.kaoyan_context import KaoyanContext  # noqa: E402

TARGET = "合成农业大学"        # 目标校（合成）
ALIAS = "合成农大"            # 目标校常见简称
OTHER = "虚构理工学院"         # 非目标校（合成）


def _ctx(target=TARGET, user_input=""):
    """构造 school_scope_guard 消费的最小 ctx（键与 hook_ctx 一致）。"""
    return {
        "active_subject": "pro",
        "math_key": None,
        "user_input": user_input,
        "target_school": target,
        "target_major": "合成专业",
    }


# ── 1. KaoyanContext.from_config 字段解析 ──────────────────────────────


def test_from_config_parses_all_fields():
    """完整配置 → 各字段正确解析（exam_date 走 exam_calendar 单一真源）。"""
    cfg = {
        "active_subject": "pro",
        "coaching_style": "深度原理·学霸溯源型",
        "study_plan": {
            "school": " 合成农业大学 ",
            "major": "030500 合成专业",
            "math_key": "math2",
            "style_name": "严格把关·保姆提分型",
            "exam_date": "2026-12-19",
        },
    }
    k = KaoyanContext.from_config(cfg, workspace_root=ROOT, session_id="s-1")
    assert k.active_subject == "pro"
    assert k.math_key is None                    # 非数学科目一律 None
    assert k.target_school == TARGET             # 去空白
    assert k.target_major == "030500 合成专业"
    assert k.exam_date is not None
    assert k.exam_date.isoformat() == "2026-12-19"
    assert k.style_name == "严格把关·保姆提分型"   # plan 优先于顶层
    assert k.workspace_root == ROOT.resolve()
    assert k.session_id == "s-1"
    assert k.source == "config"


def test_from_config_math_key_semantics():
    """math_key 与旧内联 ctx 逐字同语义：仅原始 active_subject == "math" 时解析。"""
    # 数学科目：取 plan.math_key
    k = KaoyanContext.from_config({"active_subject": "math",
                                   "study_plan": {"math_key": "math3"}})
    assert k.math_key == "math3"
    # 数学科目 + plan 缺 math_key：回落 "math2"
    k = KaoyanContext.from_config({"active_subject": "math", "study_plan": {}})
    assert k.math_key == "math2"
    # 非数学科目：None
    k = KaoyanContext.from_config({"active_subject": "eng",
                                   "study_plan": {"math_key": "math2"}})
    assert k.math_key is None
    # 配置缺 active_subject：旧逻辑 math_key 判定用不带默认的 get → None
    k = KaoyanContext.from_config({"study_plan": {"math_key": "math2"}})
    assert k.active_subject == "math"    # ctx 展示值回落默认
    assert k.math_key is None            # 判定值不回落（逐字复刻旧行为）


def test_from_config_empty_and_invalid_config_safe():
    """空 / None / 非法 study_plan 一律安全默认，绝不抛异常。"""
    for cfg in (None, {}, {"study_plan": "不是字典"}, {"study_plan": None}):
        k = KaoyanContext.from_config(cfg)
        assert k.target_school == ""
        assert k.target_major == ""
        assert k.style_name == ""
        assert k.workspace_root is None
        assert k.session_id is None
        assert k.exam_date is not None    # 无配置也按日历推算（resolve_exam_date 兜底）


def test_hook_ctx_keys_compatible():
    """hook_ctx 键与 hooks.py 读取键（active_subject/math_key/user_input）兼容。"""
    k = KaoyanContext.from_config({
        "active_subject": "math",
        "study_plan": {"math_key": "math1", "school": TARGET, "major": "合成专业"},
    })
    ctx = k.hook_ctx("样本学员输入")
    assert ctx["active_subject"] == "math"
    assert ctx["math_key"] == "math1"
    assert ctx["user_input"] == "样本学员输入"
    assert ctx["target_school"] == TARGET
    assert ctx["target_major"] == "合成专业"


def test_frozen_and_with_subject():
    """frozen 不可变；with_subject 返回替换副本且 math_key 归一正确。"""
    k = KaoyanContext.from_config({"active_subject": "eng"})
    with pytest.raises(Exception):
        k.active_subject = "math"    # frozen dataclass 禁止赋值
    k2 = k.with_subject("math")
    assert k2.active_subject == "math"
    assert k2.math_key == "math2"    # 切到数学：缺失回落 math2
    assert k.active_subject == "eng"  # 原实例不受影响
    k3 = k2.with_subject("pol")
    assert k3.math_key is None       # 切离数学：置 None


# ── 2. 校名归一辅助 ────────────────────────────────────────────────────


def test_school_aliases_normalization():
    """别名集合：全名 / 去后缀主干 / 常见简称；2 字主干不收（防误匹配）。"""
    alts = _school_aliases(TARGET)
    assert TARGET in alts
    assert "合成农业" in alts      # 去「大学」主干
    assert ALIAS in alts           # 常见简称
    assert _same_school(TARGET, ALIAS) is True
    assert _same_school(TARGET, OTHER) is False
    assert _school_mentioned(TARGET, "帮我看看合成农大的情况") is True
    assert _school_mentioned(TARGET, "帮我看看虚构理工学院") is False
    # 2 字主干不收：「北京大学」不得因「北京」二字匹配到「北京师范大学」
    bj = _school_aliases("北京大学")
    assert "北京" not in bj
    assert _school_mentioned("北京大学", "北京师范大学怎么样") is False


# ── 3. school_scope_guard 四条判据 ─────────────────────────────────────


def test_guard_blocks_model_invented_school(tmp_path):
    """模型自创校名（学员未提及且非目标校）→ 硬阻断。"""
    hm = HookManager(workspace_root=tmp_path)
    allow, reason, _ = hm.trigger_pre_tool_use(
        "scout_school", {"school": OTHER}, _ctx(user_input="帮我制定今天的复习计划"))
    assert allow is False
    assert "院校范围守卫" in reason
    assert TARGET in reason and OTHER in reason


def test_guard_allows_user_mentioned_school(tmp_path):
    """学员本轮显式提及该校 → 放行（侦察非目标校是学员主动要求的）。"""
    hm = HookManager(workspace_root=tmp_path)
    allow, _, _ = hm.trigger_pre_tool_use(
        "scout_school", {"school": OTHER}, _ctx(user_input=f"顺便看看{OTHER}的情况"))
    assert allow is True


def test_guard_allows_target_school_and_alias(tmp_path):
    """目标校放行三态：工具用全名 / 工具用简称（别名归一）/ 学员用简称提及。"""
    hm = HookManager(workspace_root=tmp_path)
    # 工具参数 = 目标校全名
    allow, _, _ = hm.trigger_pre_tool_use(
        "scout_school", {"school": TARGET}, _ctx(user_input="继续"))
    assert allow is True
    # 工具参数 = 目标校简称（模型可能用简称调工具）：别名归一放行
    allow, _, _ = hm.trigger_pre_tool_use(
        "scout_school", {"school": ALIAS}, _ctx(user_input="继续"))
    assert allow is True
    # 学员用简称提及非目标校：提及判定同样吃别名归一
    allow, _, _ = hm.trigger_pre_tool_use(
        "diff_syllabus", {"school": OTHER, "major": "合成专业"},
        _ctx(user_input="看看虚构理工的考纲变动"))
    assert allow is True


def test_guard_noop_when_target_empty(tmp_path):
    """目标校未配置（空 / 未指定）→ 完全 no-op，任意校名放行。"""
    hm = HookManager(workspace_root=tmp_path)
    for target in ("", "未指定", "  "):
        allow, _, _ = hm.trigger_pre_tool_use(
            "scout_school", {"school": OTHER}, _ctx(target=target, user_input="随便看看"))
        assert allow is True, f"target={target!r} 时守卫必须 no-op"


def test_guard_noop_for_other_tools(tmp_path):
    """非院校级工具（web_search 等）完全不受影响。"""
    hm = HookManager(workspace_root=tmp_path)
    allow, _, _ = hm.trigger_pre_tool_use(
        "web_search", {"query": OTHER}, _ctx(user_input="查点资料"))
    assert allow is True


def test_guard_cross_school_compare_not_harmed(tmp_path):
    """跨校对比不误伤：学员同时提及两校 → 两次调用均放行。"""
    hm = HookManager(workspace_root=tmp_path)
    user = f"帮我对比一下{TARGET}和{OTHER}的考纲差异"
    for school in (TARGET, OTHER):
        allow, _, _ = hm.trigger_pre_tool_use(
            "diff_syllabus", {"school": school, "major": "合成专业"},
            _ctx(user_input=user))
        assert allow is True, f"跨校对比误伤: {school}"


# ── 4. PostToolUse 澄清 ────────────────────────────────────────────────


def test_clarify_appended_for_override(tmp_path):
    """放行非目标校（学员提及）→ 结果末尾追加对照澄清。"""
    hm = HookManager(workspace_root=tmp_path)
    out = hm.trigger_post_tool_use(
        "scout_school", {"school": OTHER}, "侦察结果样本",
        _ctx(user_input=f"顺便看看{OTHER}"))
    assert out.startswith("侦察结果样本")
    assert "[System Hook Feedback]" in out
    assert "对照参考" in out
    assert TARGET in out and OTHER in out


def test_clarify_not_for_target_school(tmp_path):
    """目标校本校 → 无澄清，结果原样返回。"""
    hm = HookManager(workspace_root=tmp_path)
    out = hm.trigger_post_tool_use(
        "scout_school", {"school": TARGET}, "侦察结果样本", _ctx(user_input="继续"))
    assert out == "侦察结果样本"


# ── 5. build_system_prompt 的 ctx 优先与不读盘 ─────────────────────────


def _make_workspace_with_config(tmp_path, school="甲合成大学", exp_text="甲合成样本经验内容"):
    """构造带 ky_config.json 与院校经验档案的最小工作区。"""
    import json
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": {"school": school}}, ensure_ascii=False), encoding="utf-8")
    exp_dir = tmp_path / ".memory" / "experiences"
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / f"{school}_就读经验.md").write_text(exp_text, encoding="utf-8")


def test_build_system_prompt_prefers_ctx_no_disk(tmp_path):
    """ctx 非 None：target_school 取 ctx（乙），不读盘上配置（甲）。"""
    from tools.agent.context_engine import ContextEngine
    _make_workspace_with_config(tmp_path, school="甲合成大学", exp_text="甲合成样本经验内容")
    exp_dir = tmp_path / ".memory" / "experiences"
    (exp_dir / "乙合成大学_就读经验.md").write_text("乙合成样本经验内容", encoding="utf-8")

    engine = ContextEngine(workspace_root=tmp_path, active_subject="pro", config={})
    k = KaoyanContext.from_config({"study_plan": {"school": "乙合成大学"}})
    prompt = engine.build_system_prompt(kaoyan_ctx=k)
    assert "乙合成样本经验内容" in prompt
    assert "乙合成大学" in prompt
    assert "甲合成大学" not in prompt
    assert "甲合成样本经验内容" not in prompt


def test_build_system_prompt_ctx_empty_school_no_fallback(tmp_path):
    """ctx 非 None 但 target_school 为空：不回落读盘（严格「优先取 ctx」）。"""
    from tools.agent.context_engine import ContextEngine
    _make_workspace_with_config(tmp_path, school="甲合成大学")
    engine = ContextEngine(workspace_root=tmp_path, active_subject="pro", config={})
    k = KaoyanContext.from_config({})
    prompt = engine.build_system_prompt(kaoyan_ctx=k)
    assert "甲合成大学" not in prompt


def test_build_system_prompt_none_ctx_reads_disk(tmp_path):
    """kaoyan_ctx=None：保持旧行为，从工作区 ky_config.json 读取目标校。"""
    from tools.agent.context_engine import ContextEngine
    _make_workspace_with_config(tmp_path, school="甲合成大学")
    engine = ContextEngine(workspace_root=tmp_path, active_subject="pro", config={})
    prompt = engine.build_system_prompt()
    assert "甲合成大学" in prompt
    assert "甲合成样本经验内容" in prompt


# ── 6. AgentRunner 接线（构造一次 + run 重建） ─────────────────────────


def test_agent_runner_rebuilds_ctx_each_run(tmp_path, monkeypatch):
    """AgentRunner：__init__ 构建一次；run 开头按最新配置重建（热切换生效）。"""
    from tools.agent.loop import AgentRunner

    def fake_call_llm(self, messages):
        return {"choices": [{"message": {"content": "样本回复", "tool_calls": []}}]}

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    cfg = {"api_key": "sk-test-fake", "active_subject": "pro",
           "study_plan": {"school": "甲合成大学", "major": "合成专业"}}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    assert runner.kaoyan_ctx.target_school == "甲合成大学"

    # 热切换：改配置 → 下一轮 run 重建 ctx
    cfg["study_plan"]["school"] = "乙合成大学"
    runner.run("样本问题")
    assert runner.kaoyan_ctx.target_school == "乙合成大学"

    # set_subject 同步：math_key 归一
    runner.set_subject("math")
    assert runner.kaoyan_ctx.active_subject == "math"
    assert runner.kaoyan_ctx.math_key == "math2"
    runner.close()


def test_agent_runner_guard_end_to_end(tmp_path, monkeypatch):
    """端到端：runner 的 hook 链上，模型自创校名被阻断、目标校放行。"""
    from tools.agent.loop import AgentRunner

    monkeypatch.setattr(AgentRunner, "_call_llm",
                        lambda self, messages: {"choices": [{"message": {
                            "content": "样本回复", "tool_calls": []}}]})
    cfg = {"api_key": "sk-test-fake", "active_subject": "pro",
           "study_plan": {"school": TARGET, "major": "合成专业"}}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    ctx = runner.kaoyan_ctx.hook_ctx("帮我制定复习计划")
    allow, reason, _ = runner.hooks.trigger_pre_tool_use(
        "scout_school", {"school": OTHER}, ctx)
    assert allow is False and "院校范围守卫" in reason
    allow, _, _ = runner.hooks.trigger_pre_tool_use(
        "scout_school", {"school": TARGET}, ctx)
    assert allow is True
    runner.close()
