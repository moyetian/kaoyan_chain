# -*- coding: utf-8 -*-
"""[D0] 最小事务化：写前快照 + 按文件 / 按批回滚 —— 回归测试。

背景（升级规划 §D0）
--------------------
D1 要在真实仓库动核心文件，而本仓库**禁用 git 状态切换命令**
（checkout/restore/stash/reset）。旧实现只有两个口子：

  * ``create_checkpoint`` 仅在 plan 模式经审批卡片触发（``_checkpoint_for``）；
  * ``restore_last_checkpoint`` 只能回滚「最近一次目录里的那一个文件」——
    同秒第二次写入会覆盖 ``_meta.json``，前一个文件的元数据直接丢失。

D0 把「写前每文件快照 + 按文件 / 按批回滚」提升为通用能力：
  * 任何模式下，``write_file`` / ``edit_file`` / ``delete_file`` 批准后、
    执行前一律建快照（批次 = 一次 AgentRunner turn / 一次用户动作）；
  * 批次内同一文件只记录**首次触碰前**的状态；整批回滚即回到批次开始前；
  * ``ky rollback`` 支持 ``--list`` / ``--checkpoint`` / ``--file`` / ``--dry-run``；
  * 快照时不存在的新建文件，回滚 = 删除该文件（限工作区内）。

全程离线：不联网、不调用 LLM。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.agent.permissions import PermissionManager  # noqa: E402


def _pm(tmp_path: Path, mode: str = "auto") -> PermissionManager:
    return PermissionManager(mode=mode, workspace_root=tmp_path)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ckpt_dirs(tmp_path: Path) -> list:
    base = tmp_path / ".checkpoint"
    if not base.is_dir():
        return []
    return sorted([d for d in base.iterdir() if d.is_dir() and d.name.startswith("ckpt_")])


# ── 1. 快照完整性：同秒多文件不丢元数据 ──────────────────────────────────
def test_batch_same_second_multi_file_keeps_all_entries(tmp_path):
    """同一批次写两个文件：两条条目都要在（旧实现同秒第二次会覆盖 _meta.json）。"""
    a, b = tmp_path / "a.py", tmp_path / "sub" / "b.py"
    _write(a, "A0")
    _write(b, "B0")
    pm = _pm(tmp_path)
    pm.begin_write_batch(label="t")
    pm.create_checkpoint(a)
    pm.create_checkpoint(b)

    dirs = _ckpt_dirs(tmp_path)
    assert len(dirs) == 1, f"同一批次应共享一个目录，实际 {dirs}"
    manifest = json.loads((dirs[0] / "manifest.json").read_text(encoding="utf-8"))
    rels = {e["relative_file"] for e in manifest["entries"]}
    assert rels == {"a.py", "sub/b.py"}, f"条目缺失：{rels}"
    # 备份文件按相对路径存放（与旧版布局一致，Plan 卡片 glob 依赖）
    assert (dirs[0] / "a.py").is_file() and (dirs[0] / "sub" / "b.py").is_file()


def test_create_checkpoint_idempotent_within_batch(tmp_path):
    """同一批次内同一文件重复调用只记录一次，且保留首次触碰前的状态。"""
    f = tmp_path / "x.py"
    _write(f, "V1")
    pm = _pm(tmp_path)
    pm.begin_write_batch()
    pm.create_checkpoint(f)
    _write(f, "V2")                      # 批内第一次写入
    pm.create_checkpoint(f)              # 再次快照：应复用首条，不记录 V2

    dirs = _ckpt_dirs(tmp_path)
    manifest = json.loads((dirs[0] / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 1
    assert (dirs[0] / "x.py").read_text(encoding="utf-8") == "V1", "批内应保留首次触碰前状态"


# ── 2. 按文件 / 按批回滚 ────────────────────────────────────────────────
def test_restore_single_file_only(tmp_path):
    """--file 语义：只回滚指定文件，同批其他文件不受影响。"""
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    _write(a, "A0")
    _write(b, "B0")
    pm = _pm(tmp_path)
    pm.begin_write_batch()
    pm.create_checkpoint(a)
    pm.create_checkpoint(b)
    _write(a, "A1")
    _write(b, "B1")

    res = pm.restore_checkpoint(files=["a.py"])
    assert res["success"] is True, res
    assert a.read_text(encoding="utf-8") == "A0"
    assert b.read_text(encoding="utf-8") == "B1", "未指定的文件不应被回滚"


def test_restore_file_by_suffix_match(tmp_path):
    """--file loop.py 这类后缀匹配：命中唯一条目即可回滚。"""
    f = tmp_path / "tools" / "agent" / "loop.py"
    _write(f, "OLD")
    pm = _pm(tmp_path)
    pm.begin_write_batch()
    pm.create_checkpoint(f)
    _write(f, "NEW")

    res = pm.restore_checkpoint(files=["loop.py"])
    assert res["success"] is True, res
    assert f.read_text(encoding="utf-8") == "OLD"


def test_restore_checkpoint_restores_whole_batch(tmp_path):
    """按检查点回滚 = 整批回到批次开始前。"""
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    _write(a, "A0")
    _write(b, "B0")
    pm = _pm(tmp_path)
    pm.begin_write_batch()
    pm.create_checkpoint(a)
    pm.create_checkpoint(b)
    _write(a, "A1")
    _write(b, "B1")

    name = _ckpt_dirs(tmp_path)[0].name
    res = pm.restore_checkpoint(checkpoint=name)
    assert res["success"] is True, res
    assert a.read_text(encoding="utf-8") == "A0" and b.read_text(encoding="utf-8") == "B0"


def test_new_file_rollback_deletes_it(tmp_path):
    """快照时不存在的新建文件：回滚 = 删除。"""
    newf = tmp_path / "created.py"
    pm = _pm(tmp_path)
    pm.begin_write_batch()
    pm.create_checkpoint(newf)          # 此时还不存在
    _write(newf, "fresh")

    res = pm.restore_checkpoint(files=["created.py"])
    assert res["success"] is True, res
    assert not newf.exists(), "新建文件回滚应删除"
    assert "created.py" in res["deleted"]


def test_restore_skips_target_outside_workspace(tmp_path):
    """清单被手工篡改（../ 越界）时不得写出工作区外。"""
    outside = tmp_path.parent / "outside_d0.txt"
    _write(outside, "SAFE")
    pm = _pm(tmp_path)
    pm.begin_write_batch()
    pm.create_checkpoint(tmp_path / "seed.py")   # 先建一个合法批次目录
    ck = _ckpt_dirs(tmp_path)[0]
    manifest = json.loads((ck / "manifest.json").read_text(encoding="utf-8"))
    manifest["entries"].append({
        "relative_file": "../outside_d0.txt",
        "original_file": str(outside),
        "existed": True,
        "backup_file": str(tmp_path / "seed.py"),   # 指向任意可读文件
    })
    (ck / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                      encoding="utf-8")

    res = pm.restore_checkpoint(checkpoint=ck.name)
    assert outside.read_text(encoding="utf-8") == "SAFE", "越界目标被改写"
    assert any("工作区外" in s.get("reason", "") for s in res["skipped"]), res


def test_dry_run_does_not_touch_disk(tmp_path):
    f = tmp_path / "x.py"
    _write(f, "V0")
    pm = _pm(tmp_path)
    pm.begin_write_batch()
    pm.create_checkpoint(f)
    _write(f, "V1")

    res = pm.restore_checkpoint(files=["x.py"], dry_run=True)
    assert res["dry_run"] is True and res["success"] is True
    assert f.read_text(encoding="utf-8") == "V1", "dry-run 不得动磁盘"


# ── 3. 通用能力：非 plan 模式的写操作也有写前快照 ───────────────────────
def test_auto_mode_write_creates_snapshot(tmp_path):
    """D0 头条：auto 模式的 write_file 批准后即建写前快照（旧实现仅 plan 模式）。"""
    f = tmp_path / "notes.md"
    _write(f, "原文")
    pm = _pm(tmp_path, mode="auto")
    allowed, _ = pm.check_permission("write_file", 1, {"path": "notes.md", "content": "新"})
    assert allowed is True
    dirs = _ckpt_dirs(tmp_path)
    assert dirs, "auto 模式写操作未建快照"
    assert (dirs[0] / "notes.md").read_text(encoding="utf-8") == "原文"


def test_delete_file_is_snapshotted(tmp_path):
    """delete_file（Level 5）也要写前快照 —— 删除后仍可回滚。"""
    class _ApproveAll:
        """最小审批通道替身：Level 4-5 一律批准（等价于用户在卡片上点 [y]）。"""

        def __init__(self):
            self.session_allowed_tools = set()

        def request(self, tool_name, level, tool_args):
            return True, "测试通道批准"

        def request_plan(self, tool_name, level, tool_args):
            return True, "测试通道批准计划"

    f = tmp_path / "gone.md"
    _write(f, "要保留的内容")
    pm = PermissionManager(mode="auto", workspace_root=tmp_path,
                           approval_channel=_ApproveAll())
    allowed, _ = pm.check_permission("delete_file", 5, {"path": "gone.md"})
    assert allowed is True
    f.unlink()                            # 模拟工具真正执行删除
    res = pm.restore_checkpoint(files=["gone.md"])
    assert res["success"] is True, res
    assert f.read_text(encoding="utf-8") == "要保留的内容"


def test_non_write_tool_creates_no_snapshot(tmp_path):
    """只读工具不建快照（避免噪声）。"""
    f = tmp_path / "r.md"
    _write(f, "x")
    pm = _pm(tmp_path, mode="auto")
    pm.check_permission("read_file", 0, {"path": "r.md"})
    assert not _ckpt_dirs(tmp_path)


# ── 4. 向后兼容 ─────────────────────────────────────────────────────────
def test_legacy_meta_only_checkpoint_is_readable(tmp_path):
    """旧格式（只有 _meta.json、无 manifest.json）仍可列出并回滚。"""
    f = tmp_path / "legacy.md"
    _write(f, "OLD")
    ck = tmp_path / ".checkpoint" / "ckpt_20200101_000000"
    ck.mkdir(parents=True)
    (ck / "legacy.md").write_text("OLD", encoding="utf-8")
    (ck / "_meta.json").write_text(json.dumps({
        "timestamp": "20200101_000000",
        "original_file": str(f),
        "relative_file": "legacy.md",
        "backup_file": str(ck / "legacy.md"),
    }, ensure_ascii=False), encoding="utf-8")
    _write(f, "NEW")

    pm = _pm(tmp_path)
    listed = pm.list_checkpoints()
    assert listed and listed[0]["checkpoint"] == "ckpt_20200101_000000"
    res = pm.restore_last_checkpoint()
    assert res["success"] is True, res
    assert f.read_text(encoding="utf-8") == "OLD"


def test_restore_last_checkpoint_returns_success_and_message(tmp_path):
    """旧入口兼容：ky_suite 断言 success=True，REPL 打印 message。"""
    f = tmp_path / "s.md"
    _write(f, "S0")
    pm = _pm(tmp_path)
    pm.create_checkpoint(f)
    _write(f, "S1")
    res = pm.restore_last_checkpoint()
    assert res["success"] is True
    assert "回滚至快照状态" in res["message"]
    assert f.read_text(encoding="utf-8") == "S0"


# ── 5. CLI / AgentRunner 集成 ───────────────────────────────────────────
def test_cli_rollback_list_and_file(tmp_path, monkeypatch, capsys):
    """`ky rollback --list` / `--file` 走通（handler 与 REPL 共用同一实现）。"""
    from tools.cli.commands import misc
    monkeypatch.setattr(misc, "ROOT", tmp_path)

    f = tmp_path / "cli.md"
    _write(f, "C0")
    pm = _pm(tmp_path)
    pm.begin_write_batch(label="cli")
    pm.create_checkpoint(f)
    _write(f, "C1")

    misc._cmd_rollback(["rollback", "--list"])
    out = capsys.readouterr().out
    assert "快照检查点" in out and "cli.md" in out

    misc._cmd_rollback(["rollback", "--file", "cli.md"])
    out = capsys.readouterr().out
    assert "快照回滚" in out
    assert f.read_text(encoding="utf-8") == "C0"


def test_agent_runner_batches_writes_per_turn(tmp_path, monkeypatch):
    """一次 run 内的多次写入共享同一批次目录；批次随 run 结束关闭。"""
    import json as _json

    from tools.agent.loop import AgentRunner

    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True, max_steps=3)

    calls = []

    def _tool_reply(path, content, call_id):
        return {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": "write_file",
                         "arguments": _json.dumps({"path": path, "content": content,
                                                   "overwrite": True},
                                                  ensure_ascii=False)},
        }]}}]}

    def fake_call_llm(self, messages, allow_tools=True):
        calls.append(1)
        if len(calls) == 1:
            return _tool_reply("f1.md", "one", "c1")
        if len(calls) == 2:
            return _tool_reply("f2.md", "two", "c2")
        return {"choices": [{"message": {"content": "完成", "tool_calls": []}}]}

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    answer = runner.run("写两个文件", interactive=False)
    assert answer == "完成"

    dirs = _ckpt_dirs(tmp_path)
    assert len(dirs) == 1, f"一次 run 应共享一个批次目录，实际 {dirs}"
    manifest = json.loads((dirs[0] / "manifest.json").read_text(encoding="utf-8"))
    rels = {e["relative_file"] for e in manifest["entries"]}
    assert rels == {"f1.md", "f2.md"}, rels
    assert (tmp_path / "f1.md").read_text(encoding="utf-8") == "one"
    # 回滚整批：两个新建文件都应被删除
    res = runner.permissions.restore_checkpoint(checkpoint=dirs[0].name)
    assert res["success"] is True, res
    assert not (tmp_path / "f1.md").exists() and not (tmp_path / "f2.md").exists()
