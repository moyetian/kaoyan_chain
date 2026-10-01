# -*- coding: utf-8 -*-
"""W13 验收修复 · 本地入口构建模式（完整 vs 脱敏）回归。

背景（F1）：本地入口（``update_dashboard`` / ``init_workspace`` / 两个
``更新看板.bat``）此前都不显式设置 ``KY_SNAPSHOT_OPT_IN``，构建走
``snapshot_opt_in()`` 的缺省值（=脱敏），考生在本地打开看板看不到今日任务
正文 —— 本地体验被发布策略误伤。

现约定：**本地入口一律完整模式（env=0）**；发布链路负责切到脱敏（env=1）
并事后恢复（``update_dashboard --push`` 先脱敏构建、推送后完整重建；
``sync_publish --force`` 的镜像前重建见 tests/test_fix_publish_privacy.py）。

本文件只钉入口的 env 传递与 bat 静态内容；看板产物层的 ``data-sanitized``
标记见 tests/test_dashboard_build_placeholders.py。

[编码注意] 根 ``更新看板.bat`` 以 GBK 落盘、内层为 UTF-8/ASCII，两者都用
``read_bytes()`` 做字节级断言，避免测试自身引入编码转换。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import update_dashboard as ud  # noqa: E402
from tools import init_workspace as iw  # noqa: E402


class _FakeCompleted:
    """最小可用的 ``subprocess.run`` 返回值替身。"""

    def __init__(self, returncode=0):
        self.returncode = returncode


# ─────────────────── update_dashboard：本地 / --push 两种模式 ───────────────────

def _prepare_repo(tmp_path, monkeypatch, argv):
    """造一个只含看板构建脚本 stub 与白名单产物的假仓库根。"""
    repo = tmp_path / "repo"
    (repo / "05-考研看板").mkdir(parents=True)
    (repo / "05-考研看板" / "build.py").write_text("# stub\n", encoding="utf-8")
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ud, "ROOT", repo)
    monkeypatch.setattr(sys, "argv", ["update_dashboard.py", *argv])
    return repo


def _record_calls(monkeypatch, commit_rc=0):
    """打桩 ``ud.subprocess.run``：记录每次调用的命令与 env（绝不真跑构建/git）。"""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append({"cmd": [str(c) for c in cmd], "env": kwargs.get("env")})
        rc = commit_rc if "commit" in cmd else 0
        return _FakeCompleted(rc)

    monkeypatch.setattr(ud.subprocess, "run", fake_run)
    return calls


def _build_envs(calls):
    """按调用顺序抽取所有 build.py 调用的 KY_SNAPSHOT_OPT_IN 值。"""
    return [(c["env"] or {}).get("KY_SNAPSHOT_OPT_IN")
            for c in calls if any(str(x).endswith("build.py") for x in c["cmd"])]


@pytest.mark.parametrize("argv", [[], ["--local"]], ids=["default", "local"])
def test_local_entry_builds_full_mode(tmp_path, monkeypatch, argv):
    """无 --push（或缺省/--local）→ 完整模式构建，且不触发任何 git 操作。"""
    _prepare_repo(tmp_path, monkeypatch, argv)
    calls = _record_calls(monkeypatch)

    ud.main()

    assert _build_envs(calls) == ["0"], f"本地入口必须以完整模式构建: {calls}"
    assert not any("commit" in c["cmd"] or "push" in c["cmd"] or "add" in c["cmd"]
                   for c in calls), f"本地模式不得触发 git: {calls}"


def test_push_mode_sanitizes_then_restores_full(tmp_path, monkeypatch):
    """--push：先脱敏构建（发布用）→ 提交推送 → 再完整构建恢复本地体验。"""
    _prepare_repo(tmp_path, monkeypatch, ["--push"])
    calls = _record_calls(monkeypatch, commit_rc=0)

    ud.main()

    assert _build_envs(calls) == ["1", "0"], \
        f"推送模式应「先脱敏构建、后完整恢复」: {_build_envs(calls)}"
    build_idx = [i for i, c in enumerate(calls)
                 if any(str(x).endswith("build.py") for x in c["cmd"])]
    i_commit = next(i for i, c in enumerate(calls) if "commit" in c["cmd"])
    assert build_idx[0] < i_commit < build_idx[1], "恢复构建必须在提交之后"
    assert any("push" in c["cmd"] for c in calls), "commit 成功后必须推送"


def test_push_mode_commit_failure_still_restores_full(tmp_path, monkeypatch):
    """--push 且无增量（commit 失败）：不推送，但仍恢复本地完整模式。"""
    _prepare_repo(tmp_path, monkeypatch, ["--push"])
    calls = _record_calls(monkeypatch, commit_rc=1)

    ud.main()

    assert _build_envs(calls) == ["1", "0"], \
        f"commit 失败也必须恢复完整模式: {_build_envs(calls)}"
    assert not any("push" in c["cmd"] for c in calls), \
        f"commit 失败却仍执行了 push: {calls}"


# ─────────────────── init_workspace：引导后的首次构建 ───────────────────

def test_init_workspace_build_dashboard_full_mode(tmp_path, monkeypatch):
    """引导完成后的看板是本地使用的，必须显式完整模式（env=0）。"""
    (tmp_path / "05-考研看板").mkdir(parents=True)
    (tmp_path / "05-考研看板" / "build.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(iw, "ROOT", tmp_path)

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append({"cmd": [str(c) for c in cmd], "env": kwargs.get("env")})
        return _FakeCompleted(0)

    # build_dashboard 内部 `import subprocess`，patch 模块对象即可生效
    monkeypatch.setattr(subprocess, "run", fake_run)

    iw.build_dashboard()

    assert len(calls) == 1, f"build_dashboard 应只构建一次: {calls}"
    assert (calls[0]["env"] or {}).get("KY_SNAPSHOT_OPT_IN") == "0", \
        "init_workspace 引导完成后的看板必须是完整模式"


# ─────────────────── 两个 更新看板.bat：静态字节级断言 ───────────────────

BAT_ROOT = ROOT / "更新看板.bat"
BAT_INNER = ROOT / "05-考研看板" / "更新看板.bat"


@pytest.mark.parametrize("bat_path", [BAT_ROOT, BAT_INNER], ids=["root", "inner"])
def test_bat_sets_full_mode_env_before_build(bat_path):
    """bat 必须在调用 build.py 之前设置 KY_SNAPSHOT_OPT_IN=0。"""
    data = bat_path.read_bytes()
    marker = b"set KY_SNAPSHOT_OPT_IN=0"
    assert marker in data, f"{bat_path.name} 未设置 KY_SNAPSHOT_OPT_IN=0"
    assert b"build.py" in data, f"{bat_path.name} 未调用 build.py（结构变了？）"
    assert data.index(marker) < data.index(b"build.py"), \
        "env 设置必须出现在 build.py 调用之前"


def test_root_bat_stays_gbk_encoded():
    """根 bat 以 GBK(CP936) 落盘；编码被改成 UTF-8 会让中文提示全部乱码。"""
    data = BAT_ROOT.read_bytes()
    assert "考研学习链".encode("gbk") in data, \
        "根 bat 的 GBK 中文字节缺失（文件可能被编辑器另存为 UTF-8）"


# ─────────── 收口补漏：check_dashboard / TUI / study_planner / REPL 重建点 ───────────
# 上述入口之外，收口时全仓扫描又发现 5 处「直调 build.py 未传 env」的本地重建点
# （check_dashboard 守卫、TUI build 动作、study_planner 引导、REPL 两处口令）。
# 它们同属「本地入口」：必须完整模式，否则会把考生的完整看板产物覆盖成脱敏版
# （实测：check_dashboard 一次运行即把完整产物覆盖为 data-sanitized 版）。

def test_check_dashboard_build_forces_full_mode(tmp_path, monkeypatch):
    """看板守卫的默认重建必须显式完整模式（env=0）。"""
    from tools import check_dashboard as cd  # noqa: PLC0415

    stub = tmp_path / "build.py"
    stub.write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(cd, "BUILD_SCRIPT", stub)

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append({"cmd": [str(c) for c in cmd], "env": kwargs.get("env")})
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert cd.build_dashboard() is True

    assert len(calls) == 1, f"build_dashboard 应只构建一次: {calls}"
    assert (calls[0]["env"] or {}).get("KY_SNAPSHOT_OPT_IN") == "0", \
        "check_dashboard 重建必须显式完整模式，否则覆盖本地完整产物"


#: (相对路径, 期望的 subprocess.run 调用数)——这些文件里的 subprocess.run 均为
#: 看板构建，故用「全调用点必须带完整模式 env」作断言；调用数一并钉住，
#: 防止新增调用点后断言静默放过。
#: [问题7 迁移] tui_navigator / study_planner 的重建点已改走
#: ``dashboard_build.run_dashboard_build``（frozen 下进程内执行，不再起子进程），
#: 移入下方 _MIGRATED_REBUILD_SOURCES 单独守护。
_REBUILD_SOURCES = [
    ("tools/cli/repl/loop.py", 2),
]

#: 已迁移到 ``dashboard_build.run_dashboard_build`` 的看板重建点：
#: (相对路径, 期望的调用数)。frozen（exe）下 ``sys.executable`` 指向 GUI
#: 主程序，直调子进程会再弹一个主界面（问题7 根因）；迁移后的调用点必须
#: 走统一执行器且显式完整模式（``snapshot_opt_in=False``）。
_MIGRATED_REBUILD_SOURCES = [
    ("tools/tui_navigator.py", 1),
    ("tools/study_planner.py", 1),
]


def _run_call_env_sources(source: str):
    """返回源码里所有 ``subprocess.run`` 调用的 env 关键字源码段（缺失为 None）。"""
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            continue
        env_src = None
        for kw in node.keywords:
            if kw.arg == "env":
                env_src = ast.get_source_segment(source, kw.value) or ""
        out.append(env_src)
    return out


def _run_dashboard_build_call_sources(source: str):
    """返回源码里所有 ``run_dashboard_build(...)`` 调用的源码段。"""
    tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "run_dashboard_build"):
            out.append(ast.get_source_segment(source, node) or "")
    return out


def test_detector_flags_missing_env_call():
    """阴性对照：检测器必须能识别「subprocess.run 未传 env」。"""
    bad = "import subprocess\nsubprocess.run([sys.executable, 'build.py'])\n"
    assert _run_call_env_sources(bad) == [None]


def test_detector_flags_migrated_call_missing_full_mode():
    """阴性对照：检测器必须能识别「run_dashboard_build 未显式完整模式」。"""
    bad = ("from dashboard_build import run_dashboard_build\n"
           "run_dashboard_build(workspace_root=ROOT)\n")
    calls = _run_dashboard_build_call_sources(bad)
    assert len(calls) == 1
    assert "snapshot_opt_in=False" not in calls[0]


@pytest.mark.parametrize("rel,n_expected", _REBUILD_SOURCES,
                         ids=[p for p, _ in _REBUILD_SOURCES])
def test_all_local_rebuild_points_pass_full_mode_env(rel, n_expected):
    """TUI / study_planner / REPL 的看板重建点必须全部显式完整模式。"""
    source = (ROOT / rel).read_text(encoding="utf-8")
    env_sources = _run_call_env_sources(source)
    assert len(env_sources) == n_expected, \
        f"{rel} 的 subprocess.run 调用数变了（{len(env_sources)} != {n_expected}），请同步维护本测试"
    for env_src in env_sources:
        assert env_src is not None and "KY_SNAPSHOT_OPT_IN" in env_src, \
            f"{rel} 存在未显式完整模式的 build 调用: {env_src!r}"


@pytest.mark.parametrize("rel,n_expected", _MIGRATED_REBUILD_SOURCES,
                         ids=[p for p, _ in _MIGRATED_REBUILD_SOURCES])
def test_migrated_rebuild_points_use_full_mode(rel, n_expected):
    """迁移后的看板重建点必须走统一执行器且显式完整模式（问题7 守护）。"""
    source = (ROOT / rel).read_text(encoding="utf-8")
    calls = _run_dashboard_build_call_sources(source)
    assert len(calls) == n_expected, \
        f"{rel} 的 run_dashboard_build 调用数变了（{len(calls)} != {n_expected}），请同步维护本测试"
    for call in calls:
        assert "snapshot_opt_in=False" in call, \
            f"{rel} 的 run_dashboard_build 调用未显式完整模式: {call!r}"
