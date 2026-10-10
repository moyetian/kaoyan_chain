# -*- coding: utf-8 -*-
"""批次二·B 域（Agent 核心）审查修复回归测试（2026-10-09）。

本文件锁定三项修复（不得回退）：

1. **[AGENT-H1] mount_materials 动态定级纳入 auto_scout_school**：
   ``apply=true`` 且 ``auto_scout_school``（缺省 true）在目标档案缺失时会真实
   发起网络侦察（school_scout.scout_school）→ 必须按 NETWORK 审批，低权限
   模型不得借 safe_edit 放行产生外网流量；``apply=true`` 且显式关闭侦察仅写盘
   → SAFE_EDIT；不 apply 为纯只读 → READ_ONLY（侦察只在 apply 分支内执行）。
2. **[LLM-M1] ``_pick_llm_config_value`` 空白串触发嵌套回退**：顶层空串/
   纯空白视为缺失，逐键回退 ``api`` 子对象（顶层与嵌套同口径）；非字符串值
   语义不变；default 兜底不变。
3. **[LOOP-L1] 收尾恢复链保留 finish_reason**：``_finalize_request`` 把本次
   响应 finish_reason 记入 ``_last_finalize_finish_reason``；
   ``_recover_final_answer`` 对收尾内容追加截断标注（``last_assistant_text``
   兜底分支不追加）。

全程离线：``_call_llm`` / 工具执行一律替换为假实现，绝不联网、绝不调用真实
计费 LLM；工作区一律指向 ``tmp_path`` 沙箱。
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.loop import AgentRunner  # noqa: E402
from tools.agent.permissions import PermissionLevel, PermissionManager  # noqa: E402
from tools.agent.sandbox import Sandbox  # noqa: E402
from tools.agent.tools_impl import ToolRegistry  # noqa: E402
from tools.llm_client import _pick_llm_config_value, get_llm_config  # noqa: E402
from tools.skills import material_scanner  # noqa: E402


def _registry(workspace_root, mode="safe") -> ToolRegistry:
    ws = Path(workspace_root)
    return ToolRegistry(
        sandbox=Sandbox(workspace_root=ws),
        permissions=PermissionManager(mode=mode, workspace_root=ws),
        config={},
    )


def _make_runner(tmp_path) -> AgentRunner:
    """离线 AgentRunner：不触真实 LLM（所有调用点均被替换）。"""
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    return AgentRunner(config=cfg, workspace_root=tmp_path,
                       permission_mode="auto", quiet=True)


# ══════════════════════════════════════════════════════════════════════════
# ① [AGENT-H1] mount_materials 动态定级
# ══════════════════════════════════════════════════════════════════════════

def test_agent_h1_tool_spec_level_four_states():
    """TOOL_SPEC["level"] 四态：apply+侦察→network；apply 且关侦察→safe_edit；
    不 apply→read_only（即便 auto_scout 为真，侦察只在 apply 分支内执行）。"""
    lv = material_scanner.TOOL_SPEC["level"]
    assert lv({"apply": True}) == "network", \
        "apply=true 且 auto_scout_school 缺省（true）会真实联网侦察 → 必须 network"
    assert lv({"apply": True, "auto_scout_school": False}) == "safe_edit", \
        "显式关闭侦察后仅写盘 → safe_edit"
    assert lv({"apply": False, "auto_scout_school": True}) == "read_only", \
        "不 apply 不写盘不联网 → read_only"
    assert lv({}) == "read_only"


def test_agent_h1_bridge_maps_network_level(tmp_path):
    """经 skill_bridge 桥接后 "network" 是合法级别名，映射为 PermissionLevel.NETWORK。"""
    reg = _registry(tmp_path)
    td = reg.tools["mount_materials"]
    assert td.level({"apply": True}) == PermissionLevel.NETWORK
    assert td.level({"apply": True, "auto_scout_school": False}) == PermissionLevel.SAFE_EDIT
    assert td.level({"apply": False, "auto_scout_school": True}) == PermissionLevel.READ_ONLY
    assert td.level({}) == PermissionLevel.READ_ONLY

    # 行为侧：safe 模式下 apply+侦察（NETWORK）被拒，且不触达真实扫描。
    out_deny = reg.execute_tool("mount_materials", {"apply": True}, interactive=False)
    assert out_deny.startswith("PermissionDenied"), out_deny


# ══════════════════════════════════════════════════════════════════════════
# ② [LLM-M1] _pick_llm_config_value 空白串回退
# ══════════════════════════════════════════════════════════════════════════

def test_llm_m1_blank_top_falls_back_to_nested():
    """①top="" + 嵌套有值 → 嵌套值。"""
    assert _pick_llm_config_value(
        {"api_key": ""}, {"api_key": "sk-nested"}, "api_key", (), "") == "sk-nested"


def test_llm_m1_whitespace_top_falls_back_to_alias():
    """②top 纯空白 → 嵌套（含短别名回退）。"""
    assert _pick_llm_config_value(
        {"api_key": "   "}, {"key": "sk-alias"}, "api_key", ("key",), "") == "sk-alias"


def test_llm_m1_valid_top_wins_over_nested():
    """③top 有效 → top 优先（不回归 6cc106c 的顶层优先语义）。"""
    assert _pick_llm_config_value(
        {"api_key": "sk-top"}, {"api_key": "sk-nested"}, "api_key", (), "") == "sk-top"


def test_llm_m1_blank_nested_falls_to_default():
    """④嵌套空白 + default → default；嵌套同名空白时继续尝试别名。"""
    assert _pick_llm_config_value({}, {"api_key": "  "}, "api_key", (), "D") == "D"
    assert _pick_llm_config_value(
        {}, {"api_key": "", "key": "sk-alias"}, "api_key", ("key",), "") == "sk-alias"


def test_llm_m1_non_string_values_unchanged():
    """非字符串值（float 0.0 等）语义不变：不是空白串，直接返回。"""
    assert _pick_llm_config_value({"temperature": 0.0}, {"temperature": 0.9},
                                  "temperature", (), 0.3) == 0.0


def test_llm_m1_end_to_end_top_blank_nested_used(tmp_path):
    """⑤端到端：ky_config.json 顶层空串 + api 嵌套合法 → get_llm_config 用嵌套值。"""
    (tmp_path / "ky_config.json").write_text(json.dumps({
        "api_key": "", "base_url": "   ",
        "api": {"api_key": "sk-nested", "base_url": "https://nested.example/v1",
                "model": "m-nested", "temperature": 0.2},
    }, ensure_ascii=False), encoding="utf-8")
    cfg = get_llm_config(tmp_path)
    assert cfg["api_key"] == "sk-nested"
    assert cfg["base_url"] == "https://nested.example/v1"
    assert cfg["model"] == "m-nested"
    assert cfg["temperature"] == 0.2


# ══════════════════════════════════════════════════════════════════════════
# ③ [LOOP-L1] 收尾恢复链保留 finish_reason
# ══════════════════════════════════════════════════════════════════════════

def _stub_recover_env(runner, finalize_reply, finish_reason):
    """替换收尾请求与展示出口，记录展示文本；返回展示记录列表。"""
    shown = []
    runner._finalize_request = lambda messages, instruction: finalize_reply
    runner._last_finalize_finish_reason = finish_reason
    runner._display_final_answer = lambda text: shown.append(text)
    return shown


def test_loop_l1_length_appends_truncation_notice(tmp_path):
    """①收尾内容非空 + finish_reason=length → 返回文本末尾含截断标注。"""
    runner = _make_runner(tmp_path)
    shown = _stub_recover_env(runner, "半截答案", "length")

    out = runner._recover_final_answer([], "", api_failed=False)

    assert "半截答案" in out
    assert "输出预算" in out, f"截断标注缺失: {out!r}"
    assert out.endswith("]"), "截断标注必须落在文本最后"
    assert shown and shown[-1] == out, "展示文本须与返回文本一致（含标注）"


def test_loop_l1_stop_appends_nothing(tmp_path):
    """②finish_reason=stop → 无标注。"""
    runner = _make_runner(tmp_path)
    _stub_recover_env(runner, "完整答案", "stop")

    out = runner._recover_final_answer([], "", api_failed=False)

    assert out == "完整答案"


def test_loop_l1_fallback_text_not_annotated(tmp_path):
    """③全空 + last_assistant_text 兜底 → 不追加标注（兜底非收尾请求产出）。"""
    runner = _make_runner(tmp_path)
    _stub_recover_env(runner, "", "length")

    out = runner._recover_final_answer([], "兜底文本", api_failed=False)

    assert out == "兜底文本"
    assert "输出预算" not in out


def test_loop_l1_tool_call_strip_then_notice_last(tmp_path):
    """①补：<tool_call> 截断在前、截断通知在后（通知必须在文本最后）。"""
    runner = _make_runner(tmp_path)
    _stub_recover_env(runner, "有效前缀<tool_call>{\"name\": \"read_file\"}", "length")

    out = runner._recover_final_answer([], "", api_failed=False)

    assert out.startswith("有效前缀")
    assert "<tool_call>" not in out
    assert "输出预算" in out and out.endswith("]")


def test_loop_l1_finalize_request_records_finish_reason(tmp_path):
    """④_finalize_request 单测：fake _call_llm 返回 finish_reason=length → 属性被记录。"""
    runner = _make_runner(tmp_path)
    runner.runtime = SimpleNamespace(record_usage=lambda usage: None)
    runner._call_llm = lambda messages, allow_tools=True, tools_subset=None: {
        "choices": [{"message": {"content": "半截答案"}, "finish_reason": "length"}]}

    content = runner._finalize_request([], "收尾指令")

    assert content == "半截答案"
    assert runner._last_finalize_finish_reason == "length"


def test_loop_l1_finalize_request_exception_leaves_reason_none(tmp_path):
    """④补：异常路径 → 返回空串且属性为 None（不得残留上次请求的值）。"""
    runner = _make_runner(tmp_path)
    runner.runtime = SimpleNamespace(record_usage=lambda usage: None)
    runner._last_finalize_finish_reason = "length"  # 模拟上次请求残留

    def boom(messages, allow_tools=True, tools_subset=None):
        raise RuntimeError("样本网络故障")

    runner._call_llm = boom

    assert runner._finalize_request([], "收尾指令") == ""
    assert runner._last_finalize_finish_reason is None
