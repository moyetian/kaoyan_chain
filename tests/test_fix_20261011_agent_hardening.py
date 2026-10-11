# -*- coding: utf-8 -*-
"""R12 修复回归：Agent 核心安全加固（F1-F5）与 ingest 链路（G1b/G2）。

来源：第四轮三方审查报告（2026-10-11）第二节对抗式审查缺口 + 第三节功能性问题：
  * F1 审计可静默失效（fail-open）：permissions 审计写入异常被 `except: pass`
    吞掉 → 工具照常执行、审计零记录零告警；
  * F2 headless 白名单无权限级别闸门：`headless_allow_tools: ["*"]` 一行配置
    即让 L5 delete_file 在无人环境直接执行；
  * F3 两个高危闸门不写审计（check_session_script_exec / check_external_read）；
  * F4 会话日志脱敏漏 7 类（裸 sk- / 裸 AKIA / aws_*_access_key= / 含 @ 的
    password 值 / 带空格手机号 / 中文「密钥：」上下文）；
  * F5 runtime 时钟回退时总时长熔断 fail-open（elapsed 被钳为 0）；
  * G1b 缺 rich 等第三方依赖时首跑抛双栈 ImportError，无安装指引；
  * G2 主观题参考答案（编号列表式文本答案）未回填题卡。

全部用例隔离：tmp_path 工作区 / 打桩时钟 / 打桩导入钩子；不联网、不落盘
真实工作区、不触碰真实 ky_config.json。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.approval_audit import read_approval_events  # noqa: E402
from tools.agent.permissions import (  # noqa: E402
    EXTERNAL_READ_TOOL_NAME, PermissionLevel, PermissionManager,
)


def _pm(tmp_path, mode="auto") -> PermissionManager:
    return PermissionManager(workspace_root=tmp_path, mode=mode)


# ═════════════════ F1 审计静默失效 ═════════════════

def test_audit_write_failure_is_surfaced(tmp_path, capsys):
    """[红线] 审计根不可写：告警必须可见 + audit_degraded 置位 + 决策不受影响。"""
    blocker = tmp_path / "audit_root_is_a_file"
    blocker.write_text("x", encoding="utf-8")
    pm = _pm(tmp_path)
    pm.audit_root = blocker  # 文件冒充目录 → 写入必失败
    allowed, _ = pm.check_permission(
        "read_file", PermissionLevel.READ_ONLY, {"path": "a.txt"})
    assert allowed is True, "审计失败不得阻塞工具执行（审计是事后可追溯层）"
    assert pm.audit_degraded is True
    err = capsys.readouterr().err
    assert "审批审计写入失败" in err
    assert str(blocker) in err, "告警须含审计根路径，便于定位"


def test_audit_write_success_records_event(tmp_path):
    """对照：审计根正常 → 事件落盘、audit_degraded 保持 False。"""
    pm = _pm(tmp_path)
    pm.audit_root = tmp_path / "audit_ok"
    allowed, _ = pm.check_permission(
        "read_file", PermissionLevel.READ_ONLY, {"path": "a.txt"})
    assert allowed is True
    assert pm.audit_degraded is False
    events = read_approval_events(tmp_path / "audit_ok")
    assert events and events[-1]["tool"] == "read_file"
    assert events[-1]["allowed"] is True


def test_audit_failure_warns_only_once(tmp_path, capsys):
    """告警只打一次（后续失败不刷屏），但每次调用仍尝试写入。"""
    blocker = tmp_path / "audit_root_is_a_file"
    blocker.write_text("x", encoding="utf-8")
    pm = _pm(tmp_path)
    pm.audit_root = blocker
    for _ in range(3):
        pm.check_permission("read_file", PermissionLevel.READ_ONLY, {"path": "a.txt"})
    err = capsys.readouterr().err
    assert err.count("审批审计写入失败") == 1


# ═════════════════ F2 headless 白名单级别闸门 ═════════════════

def test_headless_wildcard_blocks_dangerous_level():
    """[红线] `["*"]` 通配 + L5 工具 → 拒绝，且理由是**高危闸门**文案。

    断言必须钉住"高危/破坏性"语义而非笼统的 "Level 5"：后者会让网络闸门
    （L5 数值上也 ≥ NETWORK）的误文案冒充高危闸门通过测试。
    """
    from tools.agent.approval import HeadlessApproval
    ch = HeadlessApproval(policy="allow_list", allow_tools=("*",))
    ok, reason = ch.request("delete_file", PermissionLevel.DANGEROUS, {"path": "a.txt"})
    assert ok is False
    assert "高危" in reason or "任何通道都不自动放行" in reason, reason


def test_headless_wildcard_blocks_network_level():
    """通配条目只覆盖 L0-L3：L4 网络工具需精确点名。"""
    from tools.agent.approval import HeadlessApproval
    ch = HeadlessApproval(policy="allow_list", allow_tools=("*",))
    ok, reason = ch.request("fetch_url", PermissionLevel.NETWORK, {"url": "https://x"})
    assert ok is False
    assert "网络" in reason or "精确点名" in reason


def test_headless_exact_network_still_allowed():
    """[回归保护] 精确点名 L4 工具仍放行（W3 评测受控配置依赖此语义）。"""
    from tools.agent.approval import HeadlessApproval
    ch = HeadlessApproval(policy="allow_list", allow_tools=("fetch_url",))
    ok, reason = ch.request("fetch_url", PermissionLevel.NETWORK, {"url": "https://x"})
    assert ok is True
    assert "放行" in reason


def test_headless_exact_dangerous_still_blocked():
    """精确点名 L5 也不放行（系统级不变量：高危任何通道都不自动放行）。"""
    from tools.agent.approval import HeadlessApproval
    ch = HeadlessApproval(policy="allow_list", allow_tools=("delete_file",))
    ok, _ = ch.request("delete_file", PermissionLevel.DANGEROUS, {"path": "a.txt"})
    assert ok is False


def test_headless_exact_low_level_allowed():
    """精确点名 L0 工具照常放行（常规白名单语义不变）。"""
    from tools.agent.approval import HeadlessApproval
    ch = HeadlessApproval(policy="allow_list", allow_tools=("read_file",))
    ok, _ = ch.request("read_file", PermissionLevel.READ_ONLY, {"path": "a.txt"})
    assert ok is True


def test_headless_glob_low_level_allowed():
    """glob 通配命中 L0-L3 照常放行（`mcp_*` 类用法不回归）。"""
    from tools.agent.approval import HeadlessApproval
    ch = HeadlessApproval(policy="allow_list", allow_tools=("mcp_*",))
    ok, _ = ch.request("mcp_search", PermissionLevel.LOW_RISK_EXEC, {})
    assert ok is True


# ═════════════════ F3 两高危闸门落审计 ═════════════════

def test_session_script_gate_writes_audit(tmp_path):
    """[红线] 会话脚本执行闸门（headless 拒绝路径）必须落审计。"""
    pm = _pm(tmp_path)
    pm.audit_root = tmp_path / "audit_ok"
    ok, _ = pm.check_session_script_exec(
        "tmp_script.py", {"command": "python tmp_script.py"}, interactive=False)
    assert ok is False  # headless 一律拒绝
    events = read_approval_events(tmp_path / "audit_ok")
    hits = [e for e in events if e["tool"] == "run_command@session_script"]
    assert hits, events
    assert hits[-1]["allowed"] is False
    assert hits[-1]["level"] == PermissionLevel.DANGEROUS


def test_external_read_gate_writes_audit(tmp_path):
    """[红线] 工作区外读取闸门（headless 拒绝路径）必须落审计。"""
    pm = _pm(tmp_path)
    pm.audit_root = tmp_path / "audit_ok"
    ok, _ = pm.check_external_read(
        "/tmp/outside.txt", {"path": "/tmp/outside.txt"}, interactive=False)
    assert ok is False
    events = read_approval_events(tmp_path / "audit_ok")
    hits = [e for e in events if e["tool"] == EXTERNAL_READ_TOOL_NAME]
    assert hits, events
    assert hits[-1]["allowed"] is False
    assert hits[-1]["level"] == PermissionLevel.NETWORK


def test_gates_audit_failure_also_surfaced(tmp_path, capsys):
    """两闸门同样享受 F1 的降级告警（共用 _audit_decision 出口）。"""
    blocker = tmp_path / "audit_root_is_a_file"
    blocker.write_text("x", encoding="utf-8")
    pm = _pm(tmp_path)
    pm.audit_root = blocker
    pm.check_session_script_exec("t.py", {"command": "python t.py"}, interactive=False)
    assert pm.audit_degraded is True
    assert "审批审计写入失败" in capsys.readouterr().err


# ═════════════════ G4-6 ky audit 覆盖范围口径澄清 ═════════════════

def test_audit_command_declares_scope(tmp_path, monkeypatch, capsys):
    """[G4-6] ky audit 文本输出必须声明覆盖范围（审批闸门决策；一次性 CLI
    写命令不在内）—— 防止用户误读「审计为空 = 无写操作发生过」。"""
    from tools.cli.commands import system as sys_cmd
    import tools.agent.approval_audit as audit_mod
    monkeypatch.setattr(audit_mod, "resolve_audit_root",
                        lambda _root: tmp_path / "audit_root")
    rc = sys_cmd._cmd_audit(["audit", "approvals"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "覆盖范围" in out, out
    assert "一次性 CLI 写命令" in out, out


def test_audit_command_json_mode_has_no_scope_banner(tmp_path, monkeypatch, capsys):
    """阴性：--json 模式保持纯机器可读（不掺入说明行）。"""
    from tools.cli.commands import system as sys_cmd
    import tools.agent.approval_audit as audit_mod
    monkeypatch.setattr(audit_mod, "resolve_audit_root",
                        lambda _root: tmp_path / "audit_root")
    rc = sys_cmd._cmd_audit(["audit", "approvals", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "覆盖范围" not in out, out
    assert json.loads(out) == []


# ═════════════════ F4 会话日志脱敏补 7 类 ═════════════════

@pytest.mark.parametrize("raw,needle", [
    ("密钥 sk-abc12345def", "sk-abc12345def"),
    ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
     "wJalrXUtnFEMI"),
    ("aws_access_key_id=AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("password=hunter2@2024", "hunter2@2024"),
    ("密码：hunter2@2024", "hunter2@2024"),
    ("138 1234 5678", "138 1234 5678"),
    ("138-1234-5678", "138-1234-5678"),
    # [代码审查补漏] 双空格分隔（从 PDF/文档复制常见）：单分隔符 `[\s\-]?`
    # 只认一个分隔字符，双空格整体漏网（审查实测）
    ("138  1234  5678", "138  1234  5678"),
])
def test_session_redaction_covers_new_classes(raw, needle):
    from tools.agent.session_log import _redact_session_text
    out = _redact_session_text(raw)
    assert needle not in out, f"未脱敏: {raw!r} -> {out!r}"


def test_session_redaction_keeps_normal_text():
    """阴性对照：正常复习文本与分数不得被误脱敏。"""
    from tools.agent.session_log import _redact_session_text
    for s in ("今天复习了马原第一章，重点是实践与认识的辩证关系。",
              "第 1 题 选 A，得分 2 分。",
              "共 138 页，分 12 章。"):
        assert _redact_session_text(s) == s, s


# ═════════════════ F5 runtime 时钟回退高水位 ═════════════════

def test_elapsed_high_water_survives_clock_rollback(monkeypatch):
    """[红线] 单调钟回退 5s 后 elapsed 不缩水；预算按已观察最大值触发。"""
    from tools.agent import runtime as rt_mod
    from tools.agent.runtime import RunBudgetExceeded, RunLimits, RunRuntime

    clock = {"t": 1000.0}
    monkeypatch.setattr(rt_mod, "time", SimpleNamespace(
        monotonic=lambda: clock["t"], time=lambda: clock["t"]))

    r = RunRuntime(limits=RunLimits(max_seconds=10))
    r.start()
    clock["t"] = 1005.0
    assert r._elapsed_seconds() == pytest.approx(5.0)
    # 时钟回退 5s：高水位保证 elapsed 不缩水（旧实现回退为 0 → 熔断被绕过）
    clock["t"] = 1000.0
    assert r._elapsed_seconds() == pytest.approx(5.0)
    clock["t"] = 1003.0
    assert r._elapsed_seconds() == pytest.approx(5.0)
    # 恢复推进后继续累积
    clock["t"] = 1011.0
    assert r._elapsed_seconds() == pytest.approx(11.0)
    with pytest.raises(RunBudgetExceeded):
        r._check_time_budget()


def test_elapsed_high_water_resets_per_run(monkeypatch):
    """对照：新一次 start() 重置高水位（不跨 run 泄漏）。"""
    from tools.agent import runtime as rt_mod
    from tools.agent.runtime import RunLimits, RunRuntime

    clock = {"t": 500.0}
    monkeypatch.setattr(rt_mod, "time", SimpleNamespace(
        monotonic=lambda: clock["t"], time=lambda: clock["t"]))
    r = RunRuntime(limits=RunLimits(max_seconds=0))
    r.start()
    clock["t"] = 530.0
    assert r._elapsed_seconds() == pytest.approx(30.0)
    r.start()  # 重置
    assert r._elapsed_seconds() == pytest.approx(0.0)


# ═════════════════ G1b 缺依赖友好提示 ═════════════════

def test_ky_cli_missing_third_party_dependency_hint():
    """[红线] rich 缺失：友好提示 + pip 指引 + rc=1（不再双栈回溯）。

    忠实模拟 `py tools/ky_cli.py` 的真实环境：脚本目录（tools/）入 sys.path[0]，
    与直接 subprocess 跑脚本一致 —— 两条导入路径（tools.* / cli.*）都会先链到
    renderer 的 `from rich import box`，最终缺失模块名识别为 rich。
    """
    wrapper = (
        "import builtins, sys\n"
        "sys.path.insert(0, sys.argv[2])  # 模拟脚本目录入 path[0]\n"
        "_orig = builtins.__import__\n"
        "def _fake(name, *a, **k):\n"
        "    if name == 'rich' or name.startswith('rich.'):\n"
        "        raise ImportError(\"No module named 'rich'\", name='rich')\n"
        "    return _orig(name, *a, **k)\n"
        "builtins.__import__ = _fake\n"
        "src = open(sys.argv[1], encoding='utf-8').read()\n"
        "g = {'__name__': '__main__', '__file__': sys.argv[1]}\n"
        "exec(compile(src, sys.argv[1], 'exec'), g)\n"
    )
    cp = subprocess.run(
        [sys.executable, "-c", wrapper,
         str(ROOT / "tools" / "ky_cli.py"), str(ROOT / "tools")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120)
    assert cp.returncode == 1, f"rc={cp.returncode}\n{cp.stdout}\n{cp.stderr}"
    out = cp.stdout + cp.stderr
    assert "缺少运行依赖" in out and "rich" in out, out
    assert "pip install" in out, out
    # 不得再出现「During handling of the above exception」双栈回溯
    assert "During handling of the above exception" not in out, out


def test_ky_cli_missing_dependency_hint_module_mode():
    """[红线·代码审查补漏] 模块式调用（`python -m tools.ky_cli`，仓库自测在用）
    下缺依赖同样必须给出提示，而不是被「No module named 'cli'」遮蔽。

    背景：模块模式下 tools/ 不在 sys.path，第二条导入路径（cli.*）必然失败且
    缺失名恰为 "cli"（布局标记）；旧实现 `_second_exc.name or _first_exc.name`
    优先取到 "cli" → 被判为「本项目包缺失，保持原样抛出」→ 提示被跳过、
    用户看到误导性的 "No module named 'cli'"（审查实测复现）。
    """
    wrapper = (
        "import builtins, runpy\n"
        "_orig = builtins.__import__\n"
        "def _fake(name, *a, **k):\n"
        "    if name == 'rich' or name.startswith('rich.'):\n"
        "        raise ImportError(\"No module named 'rich'\", name='rich')\n"
        "    return _orig(name, *a, **k)\n"
        "builtins.__import__ = _fake\n"
        "runpy.run_module('tools.ky_cli', run_name='__main__')\n"
    )
    cp = subprocess.run(
        [sys.executable, "-c", wrapper, "--version"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120)
    assert cp.returncode == 1, f"rc={cp.returncode}\n{cp.stdout}\n{cp.stderr}"
    out = cp.stdout + cp.stderr
    assert "缺少运行依赖" in out and "rich" in out, out
    assert "pip install" in out, out


# ═════════════════ G2 主观题文本答案回填 ═════════════════

_G2_DOC = """# 2024 年示例学科真题（回忆版）

一、单项选择题（每题 2 分，共 2 分）

1. 示例选择题题干（  ）
A. 选项甲
B. 选项乙
C. 选项丙
D. 选项丁

二、简答题（每题 10 分，共 30 分）

2. 简述实践在认识中的决定作用。

3. 简述矛盾的普遍性与特殊性的辩证关系。

4. 论述生产力与生产关系的矛盾运动规律。

## 参考答案

1. A

2. 实践是认识的基础，决定认识的产生和发展：实践是认识的来源，是认识发展的动力，是检验认识真理性的唯一标准，是认识的目的。

3. 矛盾的普遍性与特殊性是共性与个性、一般与个别的关系。第一，二者相互区别；第二，二者相互联结，普遍性寓于特殊性之中；第三，二者在一定条件下相互转化。

4. 生产力决定生产关系，生产关系反作用于生产力。生产关系一定要适合生产力状况的规律是社会发展的基本规律。
"""


def test_text_answers_backfilled_to_subjective_questions(tmp_path):
    """[红线] 编号列表式文本答案必须回填到各主观题（此前 3 道简答答案全丢）。"""
    from tools.skills.material_ingestion import MaterialIngestionPipeline
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    p._force_python = True
    res = p.ingest_text(_G2_DOC, subject="pro", source_name="G2文本答案",
                        llm_enrich=False)
    assert res["success"] is True
    body = Path(res["target_path"]).read_text(encoding="utf-8")
    # 三道主观题的答案正文都已进卡片
    assert "实践是认识的来源" in body
    assert "共性与个性" in body
    assert "生产关系一定要适合生产力状况" in body
    # 选择题答案不受影响（既有字母回填路径）
    assert "标准答案" in body


def test_text_answers_do_not_overwrite_letter_answers(tmp_path):
    """阴性：已由字母答案命中的题号不被文本条目覆盖（第 1 题答案仍为 A）。"""
    from tools.skills.material_ingestion import MaterialIngestionPipeline
    from tools.skills import question_source as qs
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    p._force_python = True
    res = p.ingest_text(_G2_DOC, subject="pro", source_name="G2覆盖保护",
                        llm_enrich=False)
    body = Path(res["target_path"]).read_text(encoding="utf-8")
    blocks = qs.split_card_blocks(body)[1:]
    assert blocks
    first = next(b for b in blocks if "示例选择题题干" in b)
    assert "> A" in first, first
    # 文本答案（第 2 题的正文）不得串进选择题卡
    assert "实践是认识的来源" not in first
