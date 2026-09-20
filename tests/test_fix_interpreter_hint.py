# -*- coding: utf-8 -*-
"""P15 回归：用户可见文案里的 Python 解释器名必须随平台自适应。

背景：本机（Windows）``python`` 是微软商店 stub，直接执行会 exit 49、零输出，
帮助文案若让考生敲 ``python tools/ky_cli.py`` 必然失败。正确做法是按平台给出
``py`` / ``python3`` / ``python``，且判定只在一处（``tools.cli.shared``）。
"""

import subprocess
import sys

import pytest

import tools.cli.shared as shared

_ALLOWED = {"py", "python", "python3"}


@pytest.fixture(autouse=True)
def _reset_hint_cache(monkeypatch):
    """每个用例前清空模块级缓存，保证探测逻辑真的被执行。"""
    monkeypatch.setattr(shared, "_INTERPRETER_HINT", None, raising=False)


def test_interpreter_hint_returns_supported_name():
    assert shared.interpreter_hint() in _ALLOWED


def test_interpreter_hint_windows_prefers_py(monkeypatch):
    """Windows 分支：py 可用时返回 py。"""
    monkeypatch.setattr(shared, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(
        shared.shutil, "which",
        lambda name: r"C:\Windows\py.EXE" if name == "py" else None,
    )
    assert shared.interpreter_hint() == "py"


def test_interpreter_hint_windows_falls_back_to_python(monkeypatch):
    """Windows 分支：py 缺失时退回 python。"""
    monkeypatch.setattr(shared, "_IS_WINDOWS", True, raising=False)
    monkeypatch.setattr(shared.shutil, "which", lambda name: None)
    assert shared.interpreter_hint() == "python"


def test_interpreter_hint_posix_prefers_python3(monkeypatch):
    """非 Windows 分支：python3 可用时返回 python3。"""
    monkeypatch.setattr(shared, "_IS_WINDOWS", False, raising=False)
    monkeypatch.setattr(
        shared.shutil, "which",
        lambda name: "/usr/bin/python3" if name == "python3" else None,
    )
    assert shared.interpreter_hint() == "python3"


def test_interpreter_hint_is_cached(monkeypatch):
    """结果做模块级缓存：探测函数只应被调用一次。"""
    calls = []

    def _which(name):
        calls.append(name)
        return "/usr/bin/python3"

    monkeypatch.setattr(shared, "_IS_WINDOWS", False, raising=False)
    monkeypatch.setattr(shared.shutil, "which", _which)
    shared.interpreter_hint()
    shared.interpreter_hint()
    assert len(calls) == 1


def test_help_and_unknown_cmd_use_platform_interpreter():
    """进程级验收：--help 与未知命令提示都用当前平台解释器名。"""
    hint = shared.interpreter_hint()
    assert hint in _ALLOWED

    r = subprocess.run(
        [sys.executable, "-m", "tools.ky_cli", "--help"],
        capture_output=True, encoding="utf-8", timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert f"{hint} tools/ky_cli.py" in r.stdout

    r2 = subprocess.run(
        [sys.executable, "-m", "tools.ky_cli", "definitely-not-a-command"],
        capture_output=True, encoding="utf-8", timeout=60,
    )
    assert r2.returncode == 1
    assert f"{hint} tools/ky_cli.py --help" in r2.stdout

    if hint != "python":
        # 不允许再残留写死的 python（python3 是另一个合法 token，不受影响）
        assert "python tools/ky_cli.py" not in r.stdout
