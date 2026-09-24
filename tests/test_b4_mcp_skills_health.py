# -*- coding: utf-8 -*-
"""B4 批次回归 · MCP 完形 + Skills 状态真实性。

缺陷现场（2026-09-24 核实，均为实测）
------------------------------------
A. **MCP 声明 ≠ 实现**：``mcp_client`` 在 ``initialize`` 里声明
   tools/resources/prompts 三类 capability，但客户端只实现了 ``tools/*`` ——
   server 按声明暴露资源与 Prompt，客户端却取不到；模块 docstring 同样是空头支票。
B. **MCP 崩溃静默**：``start()`` 失败一律 ``return False``，崩溃/乱输出的
   server 在会话与体检里毫无痕迹（既无 reason 也无 health）。
C. **权限精确匹配**：headless allow_list 与会话信任都是 ``tool_name in set``，
   用户写 ``mcp_*`` 永远不命中，每个外部 MCP 工具都得逐个弹卡/逐个点名。
D. **13 项硬编码"已就绪"**：``SKILLS_REGISTRY`` 的 status 是字面量字符串，
   sympy/pypdf/API Key 缺失时面板照样宣称"已就绪"。

本文件的测试策略
----------------
* **元测试防回退**：每条目必须有可调用的 ``health_check``，且 ``status`` 必须
  等于"该函数此刻的输出"的渲染 —— 硬编码字面量、漏配 health_check 都会变红；
  ``_SKILL_META`` 与 ``_HEALTH_PROVIDERS`` 键集不一致时构建期直接抛错。
* **阴性验证（硬性）**：拔掉 sympy → ``math_verifier`` 必须从 READY 变 DEGRADED
  并带 reason；自检函数抛异常 → 只降级该项技能，注册表构建不得崩溃。
* **MCP 全离线**：只用 ``_mock_mcp_server.py`` 本地子进程与"立刻退出/乱输出"的
  合成坏 server，绝不连接任何真实网络或外部 MCP 服务。
* doctor 的联网探活被显式打桩（``urlopen`` 抛 OSError），MCP 探活被替换为 no-op。
"""

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MOCK_SERVER = REPO_ROOT / "tools" / "agent" / "_mock_mcp_server.py"


# ══════════════════════════════════════════════════════════════════════════
# ① Skills：元测试（status 必须来自运行时自检）
# ══════════════════════════════════════════════════════════════════════════

def test_every_registry_entry_exposes_callable_health_check():
    """元测试·防回退：每个技能条目都必须有可调用的 health_check，且返回合法档位。

    新增技能若忘记登记健康自检，这里与 ``build_skills_registry()`` 的构建期校验
    会同时变红 —— 不允许再出现"字面量已就绪"的条目。
    """
    from tools.skills import SKILLS_REGISTRY

    assert SKILLS_REGISTRY, "SKILLS_REGISTRY 不应为空"
    for skill_id, entry in SKILLS_REGISTRY.items():
        provider = entry.get("health_check")
        assert callable(provider), f"技能 [{skill_id}] 未提供可调用的 health_check（防回退）"
        health = provider()
        assert isinstance(health, dict), f"技能 [{skill_id}] 的 health_check 必须返回 dict"
        assert health.get("status") in ("READY", "DEGRADED", "UNAVAILABLE"), \
            f"技能 [{skill_id}] 返回非法档位: {health!r}"
        assert str(health.get("reason") or "").strip(), \
            f"技能 [{skill_id}] 的健康结果必须带 reason（不允许只有档位没有原因）"


def test_status_is_rendered_from_live_health_not_hardcoded():
    """元测试·防硬编码：status 必须等于"实时 health_check 输出"的渲染结果。

    若某条目的 status 被写死成"已就绪 · ..."而其 health_check 此刻返回
    DEGRADED/UNAVAILABLE，本断言立即变红 —— 这正是 D 类缺陷的守门人。
    """
    from tools.skills import build_skills_registry

    prefix = {"READY": "已就绪", "DEGRADED": "降级可用", "UNAVAILABLE": "不可用"}
    reg = build_skills_registry()
    for skill_id, entry in reg.items():
        live = entry["health_check"]()
        assert entry["health"]["status"] == live["status"], \
            f"技能 [{skill_id}] 的 status 快照与实时自检不一致: {entry['health']} vs {live}"
        assert entry["status"].startswith(prefix[live["status"]]), \
            f"技能 [{skill_id}] 的 status 文案未反映真实档位: {entry['status']!r}"
        assert str(live["reason"]).strip() in entry["status"], \
            f"技能 [{skill_id}] 的 status 文案未包含自检原因: {entry['status']!r}"


def test_status_follows_monkeypatched_health_check(monkeypatch):
    """证明 status 来自函数：把 health_check 换成返回 DEGRADED 的桩，注册表随动。"""
    from tools.skills import build_skills_registry
    from tools.skills import math_verifier

    monkeypatch.setattr(
        math_verifier, "health_check",
        lambda: {"status": "DEGRADED", "reason": "样本降级原因（测试桩）"})

    reg = build_skills_registry()
    assert reg["math_verifier"]["health"]["status"] == "DEGRADED"
    assert "样本降级原因（测试桩）" in reg["math_verifier"]["status"]
    assert reg["math_verifier"]["status"].startswith("降级可用")
    # 其他技能不受影响
    assert reg["socratic_tutor"]["health"]["status"] == "READY"


def test_broken_health_check_degrades_only_that_skill(monkeypatch):
    """导入/自检失败不得让 SKILLS_REGISTRY 构建崩溃 —— 只把该技能标为 UNAVAILABLE。"""
    from tools.skills import build_skills_registry
    from tools.skills import socratic_tutor

    def _boom():
        raise RuntimeError("样本自检崩溃")

    monkeypatch.setattr(socratic_tutor, "health_check", _boom)
    reg = build_skills_registry()
    assert reg["socratic_tutor"]["health"]["status"] == "UNAVAILABLE"
    assert "样本自检崩溃" in reg["socratic_tutor"]["health"]["reason"]
    # 其余技能照常
    assert reg["latex_beautifier"]["health"]["status"] == "READY"


def test_registry_build_fails_fast_when_health_provider_missing(monkeypatch):
    """防回退硬校验：技能元信息与健康自检表不一致时，构建期直接抛 RuntimeError。"""
    skills_pkg = importlib.import_module("tools.skills")

    monkeypatch.delitem(skills_pkg._HEALTH_PROVIDERS, "school_scout")
    with pytest.raises(RuntimeError) as ei:
        skills_pkg.build_skills_registry()
    assert "school_scout" in str(ei.value)


# ══════════════════════════════════════════════════════════════════════════
# ② Skills：阴性验证（拔掉 sympy → math_verifier 必须降级）
# ══════════════════════════════════════════════════════════════════════════

def test_math_verifier_negative_control_sympy_removed(monkeypatch):
    """阴性验证（硬性）：拔掉 sympy → 状态必须从 READY 变 DEGRADED 且带 reason。

    两档分别验证：
      * ``HAS_SYMPY=False`` 且 find_spec 命中 → "已安装但导入失败"；
      * ``HAS_SYMPY=False`` 且 find_spec 未命中 → "未安装 sympy"。
    """
    from tools.skills import math_verifier
    from tools.skills import get_skill_health

    before = math_verifier.health_check()
    assert before["status"] == "READY", \
        f"阴性验证前提不成立：本机 sympy 应可用，实际 {before!r}"

    # 场景一：sympy 装了但导入失败（find_spec 仍命中）
    monkeypatch.setattr(math_verifier, "HAS_SYMPY", False)
    after_installed = math_verifier.health_check()
    assert after_installed["status"] == "DEGRADED", after_installed
    assert "sympy" in after_installed["reason"]

    # 场景二：sympy 根本没装（find_spec 也拔掉）
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    after_missing = math_verifier.health_check()
    assert after_missing["status"] == "DEGRADED", after_missing
    assert "未安装 sympy" in after_missing["reason"], after_missing

    # 实时查询接口同步反映（registry 的 provider 与模块是同一份）
    assert get_skill_health("math_verifier")["status"] == "DEGRADED"


def test_dispatch_guard_blocks_only_unavailable(monkeypatch):
    """UNAVAILABLE 不调度：守卫返回可辨识提示；READY/DEGRADED 放行（返回 None）。"""
    from tools.skills import dispatch_guard
    from tools.skills import vision_solver

    monkeypatch.setattr(
        vision_solver, "health_check",
        lambda: {"status": "UNAVAILABLE", "reason": "样本原因：未配置 API Key"})
    hint = dispatch_guard("vision_solver")
    assert hint and "技能不可用" in hint and "样本原因：未配置 API Key" in hint

    monkeypatch.setattr(
        vision_solver, "health_check",
        lambda: {"status": "DEGRADED", "reason": "样本原因：本地 OCR 缺失"})
    assert dispatch_guard("vision_solver") is None, "DEGRADED 不应被守卫拦截"

    # 未注册的技能 → 明确提示而不是 KeyError
    unknown = dispatch_guard("sample_unknown_skill")
    assert unknown and "技能不可用" in unknown


def test_import_skills_keeps_heavy_deps_lazy():
    """防回退：health_check 不得把 pypdf/cryptography 等重依赖拉回导入期。

    在干净子进程里 ``import tools.skills``，断言 pypdf / cryptography 不在
    ``sys.modules`` —— 这是 C2 惰性化设计的一部分，健康自检不能破坏它。
    """
    code = (
        "import sys; sys.path.insert(0, r'%s'); "
        "import tools.skills as s; "
        "assert 'pypdf' not in sys.modules, 'pypdf 被导入期拉起'; "
        "assert 'cryptography' not in sys.modules, 'cryptography 被导入期拉起'; "
        "assert len(s.SKILLS_REGISTRY) >= 13; "
        "print('OK')" % str(REPO_ROOT)
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, f"子进程失败: {proc.stdout}\n{proc.stderr}"
    assert "OK" in proc.stdout


# ══════════════════════════════════════════════════════════════════════════
# ③ MCP：双 server 全通（list_tools + call_tool + 命名 + resources/prompts）
# ══════════════════════════════════════════════════════════════════════════

def _load_manager(servers: dict, start_timeout: int = 10):
    from tools.agent.mcp_client import MCPClientManager

    mgr = MCPClientManager(workspace_root=REPO_ROOT)
    mgr.load_from_config(servers, start_timeout=start_timeout)
    return mgr


def _two_mock_servers() -> dict:
    return {
        "sample_srv_a": {"command": sys.executable, "args": [str(MOCK_SERVER)]},
        "sample_srv_b": {"command": sys.executable, "args": [str(MOCK_SERVER)]},
    }


def test_mcp_two_servers_full_roundtrip():
    """验收：两个真实 mock server 全通 —— 工具发现/调用/命名 + 资源 + Prompt。"""
    mgr = _load_manager(_two_mock_servers())
    try:
        health = mgr.mcp_health()
        assert health["total"] == 2, health
        assert health["healthy"] == 2 and health["dead"] == 0 and health["degraded"] == 0, health
        for s in health["servers"]:
            assert {"name", "status", "reason", "tool_count"} <= set(s), s
            assert s["status"] == "healthy"
            assert s["tool_count"] == 1, f"tools/list 计数应为 1: {s}"

        tools = mgr.get_all_mcp_tools()
        names = {t["scoped_name"] for t in tools}
        assert "mcp_sample_srv_a_study_calc" in names, names
        assert "mcp_sample_srv_b_study_calc" in names, names
        assert all(t["description"].startswith("[MCP: ") for t in tools)

        out = mgr.execute_mcp_tool("sample_srv_a", "study_calc", {"score": 100})
        assert "120" in out, out

        # resources/*：正常路径 + 错误路径（未知 uri 不得崩溃）
        client = mgr.clients["sample_srv_a"]
        resources = client.list_resources()
        assert resources and resources[0]["uri"] == "ky://syllabus/sample", resources
        content = client.read_resource("ky://syllabus/sample")
        assert content and "样本考纲" in content["contents"][0]["text"], content
        assert client.read_resource("ky://missing") is None

        # prompts/*：正常路径 + 错误路径
        prompts = client.list_prompts()
        assert prompts and prompts[0]["name"] == "sample_coach_prompt", prompts
        got = client.get_prompt("sample_coach_prompt", {"topic": "样本考点"})
        assert got and "样本考点" in got["messages"][0]["content"]["text"], got
        assert client.get_prompt("sample_missing_prompt") is None
    finally:
        mgr.close_all()


def test_mcp_crashed_and_garbage_servers_report_dead_with_reason():
    """验收：崩溃/乱输出的 server 必须报 dead 且有可辨识 reason，不抛裸栈。"""
    from tools.agent.mcp_client import MCPClientManager

    mgr = MCPClientManager(workspace_root=REPO_ROOT)
    mgr.load_from_config({
        # 起来就立刻退出
        "sample_crash": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(3)"]},
        # 输出不是 JSON-RPC，然后一直挂着
        "sample_garbage": {"command": sys.executable,
                           "args": ["-c", "import time; print('not-json-line'); time.sleep(30)"]},
    }, start_timeout=1)
    try:
        assert mgr.clients == {}, "启动失败的 server 不得进入可用集合"
        health = mgr.mcp_health()
        assert health["total"] == 2 and health["dead"] == 2, health
        assert health["healthy"] == 0
        reasons = {s["name"]: s["reason"] for s in health["servers"]}
        assert set(reasons) == {"sample_crash", "sample_garbage"}
        assert all(str(r).strip() for r in reasons.values()), reasons
        assert "无响应" in reasons["sample_garbage"] or "超时" in reasons["sample_garbage"], reasons
        # 崩溃项的 reason 必须可辨识（进程退出 / 管道失败 / 握手无响应之一）
        crash_reason = reasons["sample_crash"]
        assert any(k in crash_reason for k in ("退出", "失败", "无响应", "超时")), crash_reason
    finally:
        mgr.close_all()


def test_mcp_missing_command_and_bad_config_are_reported():
    """配置层错误同样必须可辨识：命令不存在 / 缺 command 字段。"""
    from tools.agent.mcp_client import MCPClientManager

    mgr = MCPClientManager(workspace_root=REPO_ROOT)
    mgr.load_from_config({
        "sample_missing_cmd": {"command": "ky-sample-definitely-missing-cmd", "args": []},
        "sample_no_cmd": {"args": ["x"]},
    }, start_timeout=1)
    try:
        health = mgr.mcp_health()
        assert health["dead"] == 2, health
        reasons = {s["name"]: s["reason"] for s in health["servers"]}
        assert "无法启动命令" in reasons["sample_missing_cmd"], reasons
        assert "command" in reasons["sample_no_cmd"], reasons
    finally:
        mgr.close_all()


# ══════════════════════════════════════════════════════════════════════════
# ④ MCP：mcp_* 权限通配
# ══════════════════════════════════════════════════════════════════════════

def test_mcp_wildcard_in_headless_allow_list():
    """headless 白名单支持 ``mcp_*`` 一次放行全部 MCP 工具；普通名字仍精确匹配。"""
    from tools.agent.approval import HeadlessApproval

    ch = HeadlessApproval(mode="ask", policy="allow_list", allow_tools=("mcp_*",))
    ok, reason = ch.request("mcp_sample_srv_a_study_calc", 3, {})
    assert ok is True and "白名单放行" in reason
    assert ch.request("write_file", 1, {})[0] is False, "未点名的普通工具仍应拒绝"

    ch2 = HeadlessApproval(mode="ask", policy="allow_list", allow_tools=("write_file",))
    assert ch2.request("write_file", 1, {})[0] is True
    assert ch2.request("write_file_extra", 1, {})[0] is False, "精确匹配语义不得放宽"


def test_mcp_session_trust_collapses_to_wildcard():
    """会话信任收口：批准一个 MCP 工具 = 信任 ``mcp_*``，不再逐个弹卡。"""
    from tools.agent.approval import session_remember_key
    from tools.agent.permissions import PermissionManager

    assert session_remember_key("mcp_sample_srv_a_study_calc") == "mcp_*"
    assert session_remember_key("write_file") == "write_file"

    pm = PermissionManager(mode="ask", workspace_root=REPO_ROOT)
    pm.session_allowed_tools.add("mcp_*")
    allowed, reason = pm.check_permission(
        "mcp_sample_srv_b_other_tool", 3, {}, interactive=False)
    assert allowed is True and "永久信任" in reason, (allowed, reason)
    # 普通工具不受 MCP 通配影响
    assert pm.check_permission("write_file", 1, {}, interactive=False)[0] is False


def test_tty_and_gateway_approvals_remember_mcp_as_wildcard(monkeypatch):
    """TTY 与网关通道的"本会话记住"对 MCP 工具写入 ``mcp_*`` 信任键。"""
    from tools.agent.approval import GatewayApproval, TtyApproval

    shared_tty = set()
    tty = TtyApproval(session_allowed_tools=shared_tty)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "a")
    ok, _ = tty.request("mcp_sample_srv_a_study_calc", 3, {})
    assert ok is True
    assert shared_tty == {"mcp_*"}, shared_tty

    shared_gw = set()
    gw_holder = {}

    def _deliver(card):
        gw_holder["ch"].submit_reply(card["request_id"], True, True)

    gw = GatewayApproval(deliver_card=_deliver, timeout=2.0, session_allowed_tools=shared_gw)
    gw_holder["ch"] = gw
    ok, _ = gw.request("mcp_sample_srv_b_other_tool", 3, {})
    assert ok is True
    assert shared_gw == {"mcp_*"}, shared_gw


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 展示位：doctor 技能汇总 + 启动横幅真实计数
# ══════════════════════════════════════════════════════════════════════════

def _offline_urlopen(*_a, **_k):
    """doctor 的联网探活打桩：测试环境禁止任何真实网络请求。"""
    raise OSError("测试环境禁止联网（urlopen 已打桩）")


def _silence_mcp_probe(monkeypatch):
    """把 doctor 的 MCP 探活替换为 no-op（不启动任何外部进程），双导入路径都覆盖。"""
    for name in ("tools.agent.mcp_client", "agent.mcp_client"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(mod.MCPClientManager, "load_from_config",
                            lambda self, *a, **k: None)


def test_doctor_reports_real_skills_health(monkeypatch, capsys):
    """doctor 必须输出真实的技能健康汇总与 MCP 挂载检查项（不再只有"全就绪"）。"""
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _offline_urlopen)
    _silence_mcp_probe(monkeypatch)

    from tools import doctor as doctor_mod
    summary = doctor_mod.run_doctor(return_summary=True)
    out = capsys.readouterr().out

    assert "技能中枢真实状态" in out, out[-2000:]
    assert "技能就绪度" in out
    assert "MCP 外部工具挂载" in out
    assert isinstance(summary, dict) and "warnings" in summary


def test_welcome_banner_counts_real_status(monkeypatch, capsys):
    """启动横幅不得再写死"N项全就绪"：有降级/不可用时按真实档位统计。"""
    from tools.cli.repl import renderer as renderer_mod

    fake_registry = {
        "s1": {"name": "样本技能一", "desc": "样本", "command": "/s1",
               "status": "已就绪 · 样本", "health": {"status": "READY", "reason": "样本"}},
        "s2": {"name": "样本技能二", "desc": "样本", "command": "/s2",
               "status": "降级可用 · 样本", "health": {"status": "DEGRADED", "reason": "样本"}},
        "s3": {"name": "样本技能三", "desc": "样本", "command": "/s3",
               "status": "不可用 · 样本", "health": {"status": "UNAVAILABLE", "reason": "样本"}},
    }
    monkeypatch.setattr(renderer_mod, "list_skills", lambda: fake_registry)
    renderer_mod.print_welcome(animate=False)
    out = capsys.readouterr().out
    assert "1/3 项就绪" in out, out[-1500:]
    assert "3项全就绪" not in out
