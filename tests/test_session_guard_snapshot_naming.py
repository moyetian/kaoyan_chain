# -*- coding: utf-8 -*-
"""会话级守卫的留证快照：**名字必须与内容相符**。

缺陷现场（``tests/conftest.py`` 的 ``_session_config_guard``）：
    唯一的拷贝发生在**会话结束时**，拷的却是那时（可能已被污染）的内容，
    文件却命名为 ``<name>.session_before`` —— 语义与名字相反。事后运维
    据名"恢复"会把污染内容写回，造成二次污染。

修复后的契约：
    * 会话**开始**：原始字节 → ``<name>.session_before``（真·原始）；
    * 会话**结束**且发现偏离：污染现场 → ``<name>.session_after``（真·污染）。

本文件直接驱动守卫的生命周期生成器（不跑真实会话），并把 ``ROOT`` 重定向到
``tmp_path``、把留证目录覆盖到 ``tmp_path``，确保**一个字节都不碰真实配置**。

[2026-09-21 复现的缺陷] 早前这里用 ``sys.modules["tools.config_guard"] = None``
「让 config_guard 导入失败」，但 ``from tools import config_guard`` 会命中
**已加载的包属性**从而绕过该拦截 —— 于是 ``_session_guard_finalize`` 里的
``config_guard.restore(None)`` 真的跑了，把**仓库根的真实 ``ky_config.json``**
覆盖成了历史快照（跑一次全量测试后 mtime 被刷新，该次内容恰好相同所以 md5 未变；
若真实配置期间被合法修改过就会被静默回滚）。修法有两层：本文件改为覆盖留证目录，
``conftest._restore_from_guard`` 另加「目标文件归属」安全闸（见其 docstring）。

阴性对照：把 ``_session_guard_run`` 里的 ``_session_guard_prepare(before)`` 移到
``yield`` 之后（即回到旧行为），
``test_session_guard_lifecycle_snapshots_before_pristine_after_dirty`` 必须变红。
"""
from __future__ import annotations

from pathlib import Path

import conftest as conftest_mod

SENTINEL_PRISTINE = '{"study_plan": "示例原始配置"}'
SENTINEL_DIRTY = '{"api_key": "sk-SHORT"}'


def _backup(tmp_path: Path) -> Path:
    return tmp_path / ".config_backup"


def _isolate(tmp_path, monkeypatch) -> Path:
    """把守卫的工作区与留证目录都重定向到 tmp，不碰真实配置。"""
    monkeypatch.setattr(conftest_mod, "ROOT", tmp_path)
    monkeypatch.setattr(conftest_mod, "REAL_CONFIG", tmp_path / "ky_config.json")
    monkeypatch.setattr(conftest_mod, "REAL_HISTORY", tmp_path / "ky_history.json")
    monkeypatch.setattr(conftest_mod, "EVIDENCE_DIR_OVERRIDE", _backup(tmp_path))
    return tmp_path / "ky_config.json"


def test_snapshot_file_name_matches_content(tmp_path, monkeypatch):
    """``_snapshot_file`` 的落盘内容就是调用那一刻的原始字节。"""
    monkeypatch.setattr(conftest_mod, "ROOT", tmp_path)
    monkeypatch.setattr(conftest_mod, "EVIDENCE_DIR_OVERRIDE", _backup(tmp_path))
    path = tmp_path / "ky_config.json"
    path.write_text(SENTINEL_PRISTINE, encoding="utf-8")

    conftest_mod._snapshot_file(path, "session_before")
    dest = _backup(tmp_path) / "ky_config.json.session_before"
    assert dest.read_text(encoding="utf-8") == SENTINEL_PRISTINE


def test_session_guard_lifecycle_snapshots_before_pristine_after_dirty(
        tmp_path, monkeypatch):
    """生命周期：开始时留 ``session_before``（原始），结束时留 ``session_after``。"""
    real_cfg = _isolate(tmp_path, monkeypatch)
    real_cfg.write_text(SENTINEL_PRISTINE, encoding="utf-8")

    lifecycle = conftest_mod._session_guard_run()
    next(lifecycle)                       # 会话开始

    snap_before = _backup(tmp_path) / "ky_config.json.session_before"
    assert snap_before.read_text(encoding="utf-8") == SENTINEL_PRISTINE, (
        "会话开始时未留下原始留证 —— 名字与内容不符（旧缺陷）")

    # 会话中发生污染
    real_cfg.write_text(SENTINEL_DIRTY, encoding="utf-8")
    try:
        next(lifecycle)                   # 会话结束
        polluted = []
    except StopIteration as stop:
        polluted = stop.value or []

    assert polluted and polluted[0][0] == "ky_config.json", polluted
    snap_after = _backup(tmp_path) / "ky_config.json.session_after"
    assert snap_after.read_text(encoding="utf-8") == SENTINEL_DIRTY
    # 关键：session_before 仍是原始内容，未被污染内容覆盖
    assert snap_before.read_text(encoding="utf-8") == SENTINEL_PRISTINE, (
        "session_before 被污染内容覆盖 —— 名字与内容再次不符")


def test_session_guard_no_snapshot_when_unchanged(tmp_path, monkeypatch):
    """未偏离时不得留下 ``session_after``（避免误导性留证）。"""
    real_cfg = _isolate(tmp_path, monkeypatch)
    real_cfg.write_text(SENTINEL_PRISTINE, encoding="utf-8")

    lifecycle = conftest_mod._session_guard_run()
    next(lifecycle)
    assert (_backup(tmp_path) / "ky_config.json.session_before").exists()
    try:
        next(lifecycle)
        polluted = []
    except StopIteration as stop:
        polluted = stop.value or []

    assert polluted == []
    assert not (_backup(tmp_path) / "ky_config.json.session_after").exists()
