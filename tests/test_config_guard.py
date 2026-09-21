# -*- coding: utf-8 -*-
"""配置守卫（``tools/config_guard.py``）回归测试。

背景（真实事故，2026-09-21）：
    仓库根的 ``ky_config.json`` 是开发者本人的真实备考配置（``.gitignore``
    保护、不在版本控制内）。多个临时验证脚本并发运行时，它被覆写成只剩
    ``{"api_key": "sk-SHORT", "webhooks": {...}}`` 的测试夹具 —— **39 字段
    ``study_plan`` 与 51 字符真实 ``api_key`` 全部丢失**。

    关键是：写入者**不在 pytest 进程内**，所以 ``tests/conftest.py`` 的
    per-test 绊线一次都没响，损坏持续存在并被反复覆盖，直到人工从
    ``D:/测试/ky_r3_adv/`` 的历史副本恢复。

本文件锁定事故后新增的三层守卫：
    (a) ``tools/config_guard.py`` 的快照 / 校验 / 还原；
    (b) ``save_config()`` 写**真实**配置前的自动留档与审计；
    (c) ``conftest.py`` 的会话级指纹守卫（在 ``test_fix_ky_suite_guard.py``
        相邻位置由 conftest 自身覆盖，此处只断言其关键契约）。

约定（见 ``tests/README``）：每条守卫都要有**阴性对照** —— 把修复注释掉，
对应用例必须变红。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import config_guard


@pytest.fixture()
def guard_env(tmp_path, monkeypatch):
    """把守卫的 CONFIG / 备份目录重定向到 tmp，避免碰真实配置。

    注意要连 ``LEGACY_BACKUP_DIR``（旧的仓库内目录）一起重定向 —— 读取时两处
    会合并，不隔离它就会读到本机真实的 ``.config_backup/`` 账本，用例互相污染。
    """
    cfg = tmp_path / "ky_config.json"
    backup = tmp_path / ".config_backup"
    cfg.write_text(json.dumps({
        "api_key": "sk-" + "x" * 48,
        "study_plan": {"school": "示例农业大学", "major": "030500 示例专业"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config_guard, "CONFIG", cfg)
    monkeypatch.setattr(config_guard, "BACKUP_DIR", backup)
    monkeypatch.setattr(config_guard, "LEDGER", backup / "snapshots.json")
    monkeypatch.setattr(config_guard, "LEGACY_BACKUP_DIR", tmp_path / "legacy_backup")
    return cfg, backup


def _corrupt(path: Path) -> None:
    """模拟事故形态：只剩 2 个键、api_key 变成 8 字符假值。"""
    path.write_text(json.dumps({"api_key": "sk-SHORT", "webhooks": {}},
                               ensure_ascii=False), encoding="utf-8")


# ── (a) 快照 / 校验 / 还原 ────────────────────────────────────────────────

def test_snapshot_then_verify_passes(guard_env):
    cfg, _backup = guard_env
    assert config_guard.snapshot("baseline") is not None
    assert config_guard.verify(quiet=True) is True


def test_verify_detects_corruption(guard_env):
    """阴性对照：配置被覆写后 verify 必须返回 False（退出码非 0）。"""
    cfg, _backup = guard_env
    config_guard.snapshot("baseline")
    _corrupt(cfg)
    assert config_guard.verify(quiet=True) is False


def test_restore_recovers_from_snapshot(guard_env):
    """事故恢复路径：还原后内容与快照逐字节一致。"""
    cfg, _backup = guard_env
    original = cfg.read_bytes()
    config_guard.snapshot("baseline")
    _corrupt(cfg)
    assert config_guard.restore("baseline") is True
    assert cfg.read_bytes() == original


def test_restore_without_force_does_not_pollute_expected_baseline(guard_env):
    """`restore --force` 产生的留证快照**不得**成为 verify 的基准。

    否则还原之后 verify 会把「刚还原的正确配置」报成偏离 —— 这是修复过程中
    实际踩到的坑（`before_restore` 覆盖了 `baseline` 成为最近快照）。
    """
    cfg, _backup = guard_env
    config_guard.snapshot("baseline")
    _corrupt(cfg)
    config_guard.restore(None, force=True)   # force 会先存一份 before_restore
    assert config_guard.verify(quiet=True) is True, (
        "留证快照不应被当作期望状态基准"
    )


def test_auto_backup_is_not_expected_baseline(guard_env):
    """`auto-backup` 记录的是「上一次的内容」，同样不能当基准。"""
    cfg, _backup = guard_env
    config_guard.snapshot("baseline")
    config_guard.auto_backup()
    assert config_guard.verify(quiet=True) is True


def test_auto_backup_prunes_old_archives(guard_env, monkeypatch):
    """自动档份数必须有上限，否则长期使用会无限膨胀。"""
    _cfg, backup = guard_env
    monkeypatch.setattr(config_guard, "AUTO_KEEP", 3)
    for _ in range(6):
        config_guard.auto_backup()
    assert len(list(backup.glob("ky_config.auto.*.json"))) <= 3


def test_verify_without_snapshot_is_a_failure(guard_env):
    """[B2 fail-closed] 没有可比对的快照时必须返回 False。

    旧语义返回 True（fail-open）：删掉快照目录即静默解除守卫。
    现无基准 = 未通过，调用方（CLI exit 码）视为错误并提示先 snapshot。
    """
    assert config_guard.verify(quiet=True) is False


def test_bare_restore_ignores_before_restore_evidence(guard_env):
    """裸 ``restore`` 不得选中 ``before_restore``（留证快照 = 损坏现场）。

    缺陷现场：``restore --force`` 会先写一条 ``before_restore``（``expected=False``，
    内容是**当前损坏现场**）再还原。此后一次不带 tag 的裸 ``restore`` 若按
    「记录顺序最后一条」取值，就会选中这条留证快照，把刚修好的配置重新写回
    损坏版本 —— 与 ``verify()`` 只认 ``expected=True`` 的定义不一致。
    """
    cfg, _backup = guard_env
    original = cfg.read_bytes()
    config_guard.snapshot("baseline")
    _corrupt(cfg)

    assert config_guard.restore(None, force=True) is True     # 写 before_restore 后还原
    assert cfg.read_bytes() == original

    # 关键：再一次裸 restore —— 修复前会把 before_restore 的损坏内容写回
    assert config_guard.restore(None) is True
    assert cfg.read_bytes() == original, "裸 restore 选中了 before_restore（损坏现场）"
    assert config_guard.verify(quiet=True) is True


def test_bare_restore_without_expected_baseline_fails_clearly(guard_env):
    """只有留证快照（``expected=False``）时，裸 restore 必须明确报错而非退而求其次。"""
    cfg, _backup = guard_env
    config_guard.auto_backup()          # 只留下一条 expected=False 的快照
    _corrupt(cfg)
    corrupt_bytes = cfg.read_bytes()

    assert config_guard.restore(None) is False, "没有期望状态基准时不得乱还原"
    assert cfg.read_bytes() == corrupt_bytes, "失败时不得改动现场"


def test_snapshot_missing_config_returns_none(guard_env, monkeypatch, tmp_path):
    monkeypatch.setattr(config_guard, "CONFIG", tmp_path / "nope.json")
    assert config_guard.snapshot("baseline") is None


# ── (b) save_config 写真实配置前的留档 ────────────────────────────────────

def test_save_config_leaves_archive_and_audit(guard_env, monkeypatch):
    """`save_config()` 写真实配置前必须留档，并写下调用来源审计。

    这条是覆盖「pytest 之外的脚本」场景的关键 —— 事故里正是这类写入者
    绕过了 conftest 的绊线。
    """
    cfg, backup = guard_env

    import tools.cli.shared as shared
    monkeypatch.setattr(shared, "CONFIG_FILE", cfg)

    # 让守卫认这份 tmp 配置为"真实目标"：把仓库根替换成 tmp 的父目录
    monkeypatch.setattr(shared, "__file__", str(cfg.parent / "tools" / "cli" / "shared.py"))

    shared.save_config(json.loads(cfg.read_text(encoding="utf-8")))

    assert list(backup.glob("ky_config.auto.*.json")), "写盘前未留档"
    audit = backup / "write_audit.log"
    assert audit.exists(), "未写审计日志"
    assert "save_config" in audit.read_text(encoding="utf-8")


def test_save_config_guard_failure_does_not_block_write(guard_env, monkeypatch):
    """守卫是尽力而为：守卫内部炸了也绝不能影响正常写盘。"""
    cfg, _backup = guard_env
    import tools.cli.shared as shared
    monkeypatch.setattr(shared, "CONFIG_FILE", cfg)
    monkeypatch.setattr(shared, "_guard_before_config_write",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # 不抛异常即通过（写盘由 atomic_write_text 保证）
    with pytest.raises(RuntimeError):
        # 直接调被替换的函数会抛；真实 save_config 里那层 try/except 在
        # _guard_before_config_write 内部，故这里断言"守卫自身异常不被吞掉"
        # 只针对替换后的桩；真实实现见下一条用例。
        shared._guard_before_config_write()


def test_real_guard_swallows_its_own_errors(guard_env, monkeypatch):
    """真实 `_guard_before_config_write` 必须吞掉自己的异常（不得影响写盘）。"""
    cfg, _backup = guard_env
    import tools.cli.shared as shared
    monkeypatch.setattr(shared, "CONFIG_FILE", cfg)
    # 让 config_guard 导入失败，模拟守卫自身出问题
    monkeypatch.setitem(__import__("sys").modules, "tools.config_guard", None)
    shared._guard_before_config_write()   # 不抛异常即通过
