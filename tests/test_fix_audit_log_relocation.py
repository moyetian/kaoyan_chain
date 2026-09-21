# -*- coding: utf-8 -*-
"""CRITICAL 加固回归：写前审计日志不得把仓库内备份目录**重新创建出来**。

背景（承接 ``test_fix_config_backup_relocation.py``）：

上一轮把配置快照目录移出仓库根（``config_guard.default_backup_dir()`` →
``%LOCALAPPDATA%/kaoyan-study-chain/config_backup/<工作区>``），目的是让该目录
**永远不在仓库内**、不进任何导出遍历。但 ``shared._guard_before_config_write()``
里的审计日志仍写死 ``root`` 下的旧备份目录 —— 每次真实保存配置都会重建它一次，
等于把刚移走的明文留证目录又搬回仓库。

本文件锁定的契约：
    * 审计日志与快照**同源同落位**（``config_guard.BACKUP_DIR``，即
      ``default_backup_dir()`` 的模块级结果），``shared`` 不另拼路径；
    * 真实仓库根不再出现该目录；
    * 既有语义不变（写前留档 + 记审计 + 异常静默吞掉）。

阴性对照：把 ``backup_dir = Path(BACKUP_DIR)`` 改回仓库内拼接，
``test_audit_log_follows_config_guard_backup_dir`` 与
``test_repo_root_never_regains_config_backup_dir`` 必须变红。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import config_guard as cg          # noqa: E402
from tools.cli import shared as shared_mod    # noqa: E402

#: 旧仓库内备份目录名（只在本文件里出现一次，便于静态断言「shared 已改干净」）
LEGACY_DIRNAME = "." + "config" + "_backup"


def _is_within(child: Path, parent: Path) -> bool:
    """``child`` 是否位于 ``parent`` 目录树内（含相等）。"""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@pytest.fixture()
def fake_repo(tmp_path, monkeypatch):
    """造一个**假仓库根**并让守卫认为它就是真实仓库。

    守卫的判定是 ``Path(__file__).resolve().parent.parent.parent`` 与
    ``CONFIG_FILE`` 相等 —— 两者都指向本 fixture 造出的假根，因此既触发守卫，
    又完全不碰真实仓库。
    """
    repo = tmp_path / "fake_repo"
    (repo / "tools" / "cli").mkdir(parents=True)
    cfg = repo / "ky_config.json"
    cfg.write_text(json.dumps({"api_key": "PLACEHOLDER-KEY", "webhook_token": ""},
                              ensure_ascii=False), encoding="utf-8")

    backup = tmp_path / "outside_backup"
    monkeypatch.setattr(cg, "CONFIG", cfg)
    monkeypatch.setattr(cg, "BACKUP_DIR", backup)
    monkeypatch.setattr(cg, "LEDGER", backup / "snapshots.json")
    monkeypatch.setattr(cg, "LEGACY_BACKUP_DIR", tmp_path / "no_legacy")

    monkeypatch.setattr(shared_mod, "CONFIG_FILE", cfg)
    monkeypatch.setattr(shared_mod, "__file__",
                        str(repo / "tools" / "cli" / "shared.py"))
    return repo, backup


def test_guard_is_actually_triggered(fake_repo):
    """前置断言：假仓库路径确实走进了守卫分支（否则后面两条用例是空转）。"""
    _repo, backup = fake_repo
    shared_mod._guard_before_config_write()
    assert backup.is_dir(), "守卫未触发（用例空转）—— 没有产生任何留档"


def test_audit_log_follows_config_guard_backup_dir(fake_repo):
    """审计日志必须落在 ``config_guard.BACKUP_DIR``（与快照同目录）。"""
    repo, backup = fake_repo
    shared_mod._guard_before_config_write()

    audit = backup / "write_audit.log"
    assert audit.exists(), f"审计日志没有落在与快照同源的目录: {backup}"
    body = audit.read_text(encoding="utf-8")
    assert "save_config ->" in body
    assert str(repo / "ky_config.json") in body
    # 与留档同处一地：auto_backup 的快照也在同一个目录里
    assert list(backup.glob("ky_config.auto.*.json")), "留档与审计日志不同源"


def test_repo_root_never_regains_config_backup_dir(fake_repo):
    """真实仓库根不得因为写审计日志而重新出现旧的备份目录。"""
    repo, backup = fake_repo
    shared_mod._guard_before_config_write()

    stale = repo / LEGACY_DIRNAME
    assert not stale.exists(), \
        f"仓库内备份目录被重新创建（每次保存配置都会重建）: {stale}"
    assert not _is_within(backup / "write_audit.log", repo), \
        "审计日志仍写在仓库内"


def test_shared_has_no_hardcoded_repo_backup_path():
    """``shared.py`` 里不得再有旧的仓库内备份目录字面量（单一事实源）。"""
    src = (ROOT / "tools" / "cli" / "shared.py").read_text(encoding="utf-8")
    assert LEGACY_DIRNAME not in src, \
        "shared.py 仍有仓库内备份目录字面量，绕开了 config_guard 的单一事实源"


def test_guard_swallows_errors(monkeypatch, fake_repo):
    """既有语义不变：留档/审计失败必须静默吞掉，不得阻断正常写盘。"""
    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cg, "snapshot", _boom)
    shared_mod._guard_before_config_write()   # 不抛异常即通过
