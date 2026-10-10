# -*- coding: utf-8 -*-
"""[审查 2026-10-09 批次二·D 域] CLI/配置域三条缺陷回归测试。

* REPL-M1  多行草稿中 Ctrl-C 退出整个 REPL（草稿丢失）；
* HIST-M1  REPL 历史文件无 0600、无 sk- 凭证过滤（粘贴的 API key 明文留档）；
* CONF-L1  config_guard.restore() 不校验快照完整性（损坏快照覆盖有效配置）。

全部用例只读 / 打桩：不联网、不写工作区；历史文件重定向到 ``tmp_path``
（``tests/conftest.py`` 对真实 ``ky_history.json`` 有重定向 + tripwire 双防线）。
[夹具中性化] 本文件不出现任何真实院校 / 专业 / 考生信息。
"""
from __future__ import annotations

import builtins
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import config_guard  # noqa: E402
from tools.cli.repl import loop as repl_loop  # noqa: E402


# ════════════════════════════════════════════════════════════════
# REPL-M1 · 多行草稿中 Ctrl-C：只丢草稿，不退出整个 REPL
# ════════════════════════════════════════════════════════════════

def _input_queue(monkeypatch, items):
    """按序返回输入；元素为异常实例时抛出（模拟 Ctrl-C / EOF）。"""
    queue = list(items)

    def _fake_input(prompt=""):
        if not queue:
            raise EOFError
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(builtins, "input", _fake_input)


def test_repl_m1_ctrl_c_with_draft_discards_and_rereads(monkeypatch, capsys):
    """①草稿中 Ctrl-C：丢弃草稿、不抛异常，重新以主提示符读下一条。"""
    _input_queue(monkeypatch, ["第一行\\", KeyboardInterrupt(), "你好"])
    assert repl_loop._read_user_input("> ") == "你好"
    out = capsys.readouterr().out
    assert "已取消本次多行输入" in out, "缺少草稿取消提示"


def test_repl_m1_idle_ctrl_c_still_propagates(monkeypatch):
    """②空闲态 Ctrl-C：仍抛 KeyboardInterrupt（主循环优雅退出，语义不变）。"""
    _input_queue(monkeypatch, [KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        repl_loop._read_user_input("> ")


def test_repl_m1_normal_continuation_unaffected(monkeypatch):
    """③正常续行不受影响：多行拼接为一条指令。"""
    _input_queue(monkeypatch, ["第一行\\", "第二行"])
    assert repl_loop._read_user_input("> ") == "第一行\n第二行"


def test_repl_m1_cancel_then_fresh_multiline_draft(monkeypatch):
    """①补充：取消后新输入的多行草稿完整保留（证明只丢弃旧草稿）。"""
    _input_queue(monkeypatch, ["旧草稿一\\", KeyboardInterrupt(), "新一\\", "新二"])
    assert repl_loop._read_user_input("> ") == "新一\n新二"


def test_repl_m1_midway_eof_returns_partial(monkeypatch):
    """④续行中 EOF（有草稿）：返回已读内容，不回归。"""
    _input_queue(monkeypatch, ["只有一段\\"])
    assert repl_loop._read_user_input("> ") == "只有一段"


# ════════════════════════════════════════════════════════════════
# HIST-M1 · 历史文件 0600 + sk- 凭证脱敏
# ════════════════════════════════════════════════════════════════

class _FakeReadlineWriter:
    """最小 readline 桩：write_history_file 把给定文本原样落盘。"""

    def __init__(self, text):
        self.text = text
        self.write_calls = []

    def write_history_file(self, p):
        self.write_calls.append(str(p))
        Path(p).write_text(self.text, encoding="utf-8")


class _FakeKyIo:
    def __init__(self, read_only):
        self._read_only = read_only

    def is_read_only_mode(self):
        return self._read_only


def _persist_env(monkeypatch, tmp_path, text, *, tty=True, read_only=False):
    fake = _FakeReadlineWriter(text)
    hist = tmp_path / "ky_history.json"
    monkeypatch.setattr(repl_loop, "_readline", fake)
    monkeypatch.setattr(repl_loop, "_stdin_is_tty", lambda: tty)
    monkeypatch.setattr(repl_loop, "HISTORY_FILE", hist)
    monkeypatch.setattr(repl_loop, "ky_io", _FakeKyIo(read_only))
    return fake, hist


def test_hist_redact_helper_removes_secret():
    """①helper：sk- 凭证替换为 sk-***；短片段与普通文本不动（阴性对照）。"""
    out = repl_loop._redact_history_secrets("key=sk-abcdef123456 tail")
    assert "sk-abcdef123456" not in out
    assert out == "key=sk-*** tail"
    assert repl_loop._redact_history_secrets("sk-短") == "sk-短"
    assert repl_loop._redact_history_secrets("普通历史条目") == "普通历史条目"


def test_hist_persist_end_to_end_redacts_and_keeps_other_entries(monkeypatch, tmp_path):
    """②端到端：TTY + 非只读，写盘后凭证已打码、其余条目保留。"""
    _fake, hist = _persist_env(
        monkeypatch, tmp_path,
        "/today\nkey=sk-abcdef123456\n数学报到\n", tty=True, read_only=False)
    repl_loop._persist_repl_history()
    content = hist.read_text(encoding="utf-8")
    assert "sk-abcdef123456" not in content, "凭证未脱敏"
    assert "sk-***" in content
    assert "/today" in content and "数学报到" in content, "误伤普通历史条目"


@pytest.mark.skipif(os.name != "posix",
                    reason="POSIX 权限位断言；Windows 跳过（CI 为 Linux）")
def test_hist_persist_chmod_0600(monkeypatch, tmp_path):
    """③POSIX：写盘后历史文件权限为 0600。"""
    _fake, hist = _persist_env(monkeypatch, tmp_path, "/today\n")
    repl_loop._persist_repl_history()
    assert stat.S_IMODE(hist.stat().st_mode) == 0o600


def test_hist_persist_non_tty_not_written(monkeypatch, tmp_path):
    """④阴性对照：非 TTY 不写（既有语义不回归）。"""
    fake, hist = _persist_env(monkeypatch, tmp_path, "/today\n", tty=False)
    repl_loop._persist_repl_history()
    assert fake.write_calls == []
    assert not hist.exists()


def test_hist_persist_read_only_not_written(monkeypatch, tmp_path):
    """④阴性对照：只读模式不写（既有语义不回归）。"""
    fake, hist = _persist_env(monkeypatch, tmp_path, "/today\n", read_only=True)
    repl_loop._persist_repl_history()
    assert fake.write_calls == []
    assert not hist.exists()


# ════════════════════════════════════════════════════════════════
# CONF-L1 · restore() 快照完整性校验
# ════════════════════════════════════════════════════════════════

@pytest.fixture()
def guard_env(tmp_path, monkeypatch):
    """与 tests/test_config_guard.py 同姿势：CONFIG / 备份目录全部重定向到 tmp。"""
    monkeypatch.delenv(config_guard.BACKUP_DIR_ENV, raising=False)
    cfg = tmp_path / "ky_config.json"
    backup = tmp_path / ".config_backup"
    cfg.write_text(json.dumps({
        "api_key": "sk-" + "x" * 48,
        "study_plan": {"school": "示例院校", "major": "示例专业"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config_guard, "CONFIG", cfg)
    monkeypatch.setattr(config_guard, "BACKUP_DIR", backup)
    monkeypatch.setattr(config_guard, "LEDGER", backup / "snapshots.json")
    monkeypatch.setattr(config_guard, "LEGACY_BACKUP_DIR", tmp_path / "legacy_backup")
    return cfg, backup


def _corrupt(path: Path) -> None:
    path.write_text(json.dumps({"api_key": "sk-SHORT", "webhooks": {}},
                               ensure_ascii=False), encoding="utf-8")


def test_conf_l1_restore_intact_snapshot_succeeds(guard_env):
    """①正常快照：restore 成功且内容逐字节还原。"""
    cfg, _backup = guard_env
    original = cfg.read_bytes()
    config_guard.snapshot("baseline")
    _corrupt(cfg)
    assert config_guard.restore("baseline") is True
    assert cfg.read_bytes() == original


def test_conf_l1_restore_rejects_tampered_snapshot(guard_env, capsys):
    """②快照被追加损坏字节：restore 返回 False，配置文件不得被覆盖。"""
    cfg, backup = guard_env
    config_guard.snapshot("baseline")
    snap = next(backup.glob("ky_config.baseline.*.json"))
    snap.write_bytes(snap.read_bytes() + b"\ncorrupted")
    _corrupt(cfg)
    corrupt_bytes = cfg.read_bytes()

    assert config_guard.restore("baseline") is False
    out = capsys.readouterr().out
    assert "拒绝还原" in out, "缺少明确拒绝提示"
    assert cfg.read_bytes() == corrupt_bytes, "拒绝时不得改动现场"


def test_conf_l1_legacy_ledger_without_sha256_still_restores(guard_env):
    """③老账本（无 sha256 字段）：行为与现状一致（成功还原，最小惊讶）。"""
    cfg, backup = guard_env
    original = cfg.read_bytes()
    config_guard.snapshot("baseline")
    ledger = backup / "snapshots.json"
    entries = json.loads(ledger.read_text(encoding="utf-8"))
    for e in entries:
        e.pop("sha256", None)
    ledger.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    _corrupt(cfg)

    assert config_guard.restore("baseline") is True
    assert cfg.read_bytes() == original
