# -*- coding: utf-8 -*-
"""审批审计（approval_audit）回归 · 4f9bfad 新增功能的门禁补齐。

背景
----
4f9bfad 为 ``PermissionManager.check_permission`` 接入 append-only 审批审计
（``.kaoyan_chain_audit/.memory/approval_audit.jsonl``，落点在 **workspace 之外**），
并新增 ``ky audit approvals`` 只读查询命令。当时未配套任何测试——本文件锁定：

1. ``append_approval_event`` 写入格式（JSONL 单行）与**参数脱敏**：
   只保留 path/file_name/target_file/url 白名单键，凭据与正文绝不入审计；
2. ``read_approval_events`` 读取语义：limit 取最新、损坏行跳过、文件缺失返回空；
3. ``PermissionManager`` 集成：allow / deny 两态均落审计，且审计不写进工作区
   （safe-mode 快照与工作区字节不受影响）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.approval_audit import (  # noqa: E402
    append_approval_event,
    read_approval_events,
    resolve_audit_root,
)
from agent.permissions import PermissionManager  # noqa: E402


def _audit_file(root: Path) -> Path:
    return root / ".memory" / "approval_audit.jsonl"


# ---------- 模块层：写入 / 读取 ----------

def test_append_event_writes_single_jsonl_line(tmp_path):
    root = tmp_path / "audit_root"
    append_approval_event(root, tool_name="write_file", level=1, allowed=False,
                          reason="safe 拒绝", mode="safe", interactive=False,
                          args={"path": "a.md"})
    append_approval_event(root, tool_name="read_file", level=0, allowed=True,
                          reason="只读放行", mode="safe", interactive=False,
                          args={"path": "b.md"})
    lines = _audit_file(root).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool"] == "write_file"
    assert first["level"] == 1
    assert first["allowed"] is False
    assert first["mode"] == "safe"
    assert first["interactive"] is False
    assert isinstance(first["ts"], float)


def test_append_event_redacts_non_whitelist_args(tmp_path):
    root = tmp_path / "audit_root"
    append_approval_event(root, tool_name="write_file", level=1, allowed=False,
                          reason="拒绝", mode="safe", interactive=False,
                          args={"path": "a.md", "content": "绝密正文",
                                "api_key": "sk-secret", "command": "rm -rf /"})
    raw = _audit_file(root).read_text(encoding="utf-8")
    event = json.loads(raw.splitlines()[0])
    assert event["args"] == {"path": "a.md"}
    assert "绝密正文" not in raw
    assert "sk-secret" not in raw
    assert "rm -rf" not in raw


def test_read_missing_file_returns_empty(tmp_path):
    assert read_approval_events(tmp_path / "nonexistent", limit=5) == []


def test_read_skips_corrupted_lines(tmp_path):
    root = tmp_path / "audit_root"
    append_approval_event(root, tool_name="t1", level=0, allowed=True,
                          reason="ok", mode="auto", interactive=True)
    with _audit_file(root).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("这不是 JSON\n")
        handle.write('{"只缺右括号": 1\n')
    append_approval_event(root, tool_name="t2", level=2, allowed=False,
                          reason="no", mode="ask", interactive=False)
    events = read_approval_events(root, limit=10)
    assert [e["tool"] for e in events] == ["t1", "t2"]


def test_read_limit_keeps_latest(tmp_path):
    root = tmp_path / "audit_root"
    for i in range(5):
        append_approval_event(root, tool_name=f"tool{i}", level=0, allowed=True,
                              reason="ok", mode="auto", interactive=True)
    events = read_approval_events(root, limit=2)
    assert [e["tool"] for e in events] == ["tool3", "tool4"]


# ---------- 落点解析（resolve_audit_root：写入与读取的唯一实现处） ----------

def test_resolve_audit_root_env_override_wins(tmp_path, monkeypatch):
    custom = tmp_path / "custom_audit"
    monkeypatch.setenv("KY_APPROVAL_AUDIT_ROOT", str(custom))
    assert resolve_audit_root(tmp_path / "ws") == custom


def test_resolve_audit_root_defaults_to_workspace_parent(tmp_path, monkeypatch):
    monkeypatch.delenv("KY_APPROVAL_AUDIT_ROOT", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    root = resolve_audit_root(ws)
    assert root == tmp_path / ".kaoyan_chain_audit"
    assert root.is_dir()


def test_resolve_audit_root_falls_back_when_parent_unwritable(tmp_path, monkeypatch):
    """打包版安装目录父目录不可写（如 Program Files 下）→ 回退用户级目录。"""
    monkeypatch.delenv("KY_APPROVAL_AUDIT_ROOT", raising=False)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")   # 让 ws.parent 是一个"文件"
    ws = blocker / "ws"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    root = resolve_audit_root(ws)
    assert root == tmp_path / "lad" / "kaoyan-study-chain" / "approval_audit"


def test_append_rotates_when_oversized(tmp_path, monkeypatch):
    """超过大小上限时轮转出 .1 归档，当前文件继续 append。"""
    from agent import approval_audit as audit_mod
    monkeypatch.setattr(audit_mod, "MAX_AUDIT_BYTES", 300)
    root = tmp_path / "r"
    for i in range(6):
        audit_mod.append_approval_event(root, tool_name=f"t{i}", level=0,
                                        allowed=True, reason="ok", mode="auto",
                                        interactive=True)
    path = root / ".memory" / "approval_audit.jsonl"
    rotated = path.with_name(path.name + ".1")
    assert rotated.exists(), "超限应轮转出 .1 归档"
    events = audit_mod.read_approval_events(root, limit=100)
    assert events, "轮转后当前文件应继续写入"
    assert events[-1]["tool"] == "t5"


# ---------- PermissionManager 集成 ----------

def test_deny_decision_is_audited_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("KY_APPROVAL_AUDIT_ROOT", raising=False)
    ws = tmp_path / "workspace"
    ws.mkdir()
    pm = PermissionManager(mode="safe", workspace_root=ws)
    allowed, reason = pm.check_permission("write_file", 1, {"path": "x.md"},
                                          interactive=False)
    assert allowed is False
    assert "safe" in reason or "安全模式" in reason

    audit_root = tmp_path / ".kaoyan_chain_audit"
    events = read_approval_events(audit_root, limit=10)
    assert events, "safe 拒绝后应落一条审计"
    last = events[-1]
    assert last["tool"] == "write_file"
    assert last["allowed"] is False
    assert last["mode"] == "safe"
    # 审计不得落进工作区（不污染 safe-mode 快照/工作区字节）
    assert not (ws / ".kaoyan_chain_audit").exists()


def test_allow_decision_is_audited(tmp_path, monkeypatch):
    monkeypatch.delenv("KY_APPROVAL_AUDIT_ROOT", raising=False)
    ws = tmp_path / "workspace"
    ws.mkdir()
    pm = PermissionManager(mode="auto", workspace_root=ws)
    allowed, _ = pm.check_permission("write_file", 1, {"path": "y.md"},
                                     interactive=False)
    assert allowed is True
    events = read_approval_events(tmp_path / ".kaoyan_chain_audit", limit=10)
    assert events[-1]["tool"] == "write_file"
    assert events[-1]["allowed"] is True


def test_readonly_tool_audited_in_safe_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("KY_APPROVAL_AUDIT_ROOT", raising=False)
    ws = tmp_path / "workspace"
    ws.mkdir()
    pm = PermissionManager(mode="safe", workspace_root=ws)
    allowed, _ = pm.check_permission("read_file", 0, {"path": "z.md"},
                                     interactive=False)
    assert allowed is True
    events = read_approval_events(tmp_path / ".kaoyan_chain_audit", limit=10)
    assert events[-1]["tool"] == "read_file"
    assert events[-1]["allowed"] is True
