# -*- coding: utf-8 -*-
"""K2 版本工程回归测试：三处版本一致性门禁 + 兜底值不再伪装具体版本号。

覆盖：
1. 真实仓库 pyproject.toml / 根 installer.iss / tools.version.get_version() 三处
   一致，且运行时版本不是兜底值 0.0.0+unknown；
2. 漂移注入（伪造 installer.iss / pyproject 读取 / 运行时版本 / 源文件缺失）→
   核心函数判红，差异信息点名具体来源与实际值；
3. tools/build_package.get_app_version() 在全部来源不可用时兜底为
   0.0.0+unknown（而非某个具体版本号），并打印 [warn]。
"""

import sys
from pathlib import Path

from tools import check_version_consistency as k2
from tools.version import UNKNOWN_VERSION, get_version


def test_real_repo_versions_are_consistent():
    """真实仓库三处版本一致；核心函数返回 True 且运行时版本非兜底值。"""
    assert get_version() != UNKNOWN_VERSION

    ok, info = k2.check_version_consistency()

    assert ok, f"真实仓库三处版本应一致，实际：{info}"
    assert info["pyproject"] == info["installer_iss"] == info["runtime"]
    assert info["runtime"] != UNKNOWN_VERSION
    assert info["frozen"] is False


def test_iss_drift_detected_and_named(tmp_path, monkeypatch):
    """伪造 installer.iss 版本 → 判红，差异信息点名 installer.iss 与实际值。"""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "kaoyan-study-chain"\nversion = "3.1.1"\n',
        encoding="utf-8")
    (tmp_path / "installer.iss").write_text(
        '#define MyAppVersion "9.9.9"\n', encoding="utf-8")
    monkeypatch.setattr(k2, "_runtime_version", lambda: "3.1.1")

    ok, info = k2.check_version_consistency(root=tmp_path)

    assert ok is False
    assert info["installer_iss"] == "9.9.9"
    assert "installer.iss" in info["detail"]
    assert "9.9.9" in info["detail"] and "3.1.1" in info["detail"]


def test_pyproject_drift_detected_and_named(monkeypatch):
    """伪造 pyproject 读取结果 → 判红，差异信息点名 pyproject.toml。"""
    monkeypatch.setattr(k2, "_read_pyproject_version", lambda path: "3.0.0")
    monkeypatch.setattr(k2, "_runtime_version", lambda: "3.1.1")

    ok, info = k2.check_version_consistency()

    assert ok is False
    assert info["pyproject"] == "3.0.0"
    assert "pyproject.toml" in info["detail"]
    assert "3.0.0" in info["detail"]


def test_unknown_runtime_rejected(monkeypatch):
    """get_version() 回退到兜底值 → 判红（不把 0.0.0+unknown 当合法版本）。"""
    monkeypatch.setattr(k2, "_runtime_version", lambda: UNKNOWN_VERSION)

    ok, info = k2.check_version_consistency()

    assert ok is False
    assert UNKNOWN_VERSION in info["detail"]


def test_missing_sources_rejected(tmp_path, monkeypatch):
    """pyproject.toml / installer.iss 均缺失（非冻结环境）→ 判红。"""
    monkeypatch.setattr(k2, "_runtime_version", lambda: "3.1.1")

    ok, info = k2.check_version_consistency(root=tmp_path)

    assert ok is False
    assert info["pyproject"] is None and info["installer_iss"] is None


def test_frozen_env_falls_back_to_runtime_only(tmp_path, monkeypatch):
    """冻结（安装版）环境源文件不可得 → 降级为只核对运行时版本，不误报。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "KaoyanStudyChain.exe"))
    monkeypatch.setattr(k2, "_runtime_version", lambda: "3.1.1")

    ok, info = k2.check_version_consistency()

    assert ok is True
    assert info["frozen"] is True
    assert info["pyproject"] is None and info["installer_iss"] is None


def test_cli_exit_codes(capsys):
    """脚本层：一致 → 退出码 0；漂移 → 退出码 1 并打印三处实际值。"""
    assert k2.main() == 0
    assert "[OK]" in capsys.readouterr().out


def test_cli_exit_code_one_on_drift(monkeypatch, capsys):
    """脚本层：运行时版本为兜底值 → 退出码 1，输出含三处实际值与原因。"""
    monkeypatch.setattr(k2, "_runtime_version", lambda: UNKNOWN_VERSION)

    assert k2.main() == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "pyproject.toml" in out and "installer.iss" in out
    assert UNKNOWN_VERSION in out


def test_build_package_fallback_is_unknown(tmp_path, monkeypatch, capsys):
    """build_package 全部来源不可用 → 兜底 0.0.0+unknown 并打印 [warn]。

    直接断言函数行为：屏蔽两种 get_version 导入（sys.modules 置 None 使 import
    失败）并把 ROOT 指向无 pyproject.toml 的空目录 → 必须返回显式未知值，
    不得伪装成任何具体版本号（旧实现硬编码 "3.1.1"）。
    """
    from tools import build_package

    monkeypatch.setitem(sys.modules, "version", None)
    monkeypatch.setitem(sys.modules, "tools.version", None)
    monkeypatch.setattr(build_package, "ROOT", tmp_path)

    assert build_package.get_app_version() == UNKNOWN_VERSION
    assert "[warn]" in capsys.readouterr().out


# ───────────── 冻结版本读取（v3.1.1「pyproject 随包」修复不完整）─────────────

def test_frozen_meipass_pyproject_fallback(tmp_path, monkeypatch):
    """[K2 修复·冻结版本读取不完整] 冻结模式下工作区根（exe 目录）无 pyproject
    时必须回落到 ``sys._MEIPASS/pyproject.toml``（随包分发的那份）。

    现场：v3.1.1 只把 pyproject.toml 打进 ``_internal/``（即 _MEIPASS），而冻结下
    工作区根 = exe 所在目录 → 实测安装版 get_version() 仍为 0.0.0+unknown。
    """
    from tools import version as ver

    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    meipass = exe_dir / "_internal"
    meipass.mkdir()
    (meipass / "pyproject.toml").write_text(
        '[project]\nname = "kaoyan-study-chain"\nversion = "9.9.9"\n',
        encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "KaoyanStudyChain.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    assert ver._version_from_pyproject() == "9.9.9"


def test_frozen_workspace_root_pyproject_wins(tmp_path, monkeypatch):
    """工作区根存在 pyproject.toml 时优先于 _MEIPASS（候选优先级钉住）。"""
    from tools import version as ver

    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    (exe_dir / "pyproject.toml").write_text(
        '[project]\nname = "kaoyan-study-chain"\nversion = "8.8.8"\n',
        encoding="utf-8")
    meipass = exe_dir / "_internal"
    meipass.mkdir()
    (meipass / "pyproject.toml").write_text(
        '[project]\nname = "kaoyan-study-chain"\nversion = "9.9.9"\n',
        encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "KaoyanStudyChain.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    assert ver._version_from_pyproject() == "8.8.8"


def test_source_mode_single_candidate(monkeypatch):
    """源码模式（无 _MEIPASS）：候选只有工作区根，行为与旧实现一致。"""
    from tools import version as ver

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    candidates = ver._pyproject_candidates()
    assert len(candidates) == 1
    assert candidates[0] == ver._project_root() / "pyproject.toml"
