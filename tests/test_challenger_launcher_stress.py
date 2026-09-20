# -*- coding: utf-8 -*-
"""
考研学习链 · Challenger 1 (Gen 3) 启动器与看门狗对战式压力测试套件
(test_challenger_launcher_stress.py)

Empirical stress tests verifying:
1. CLI options (--preflight-only returns 0, --help documents all options)
2. Adversarial crash test (immediate exception / exit 1 -> watchdog catches, logs/gui_crash.log written, prints traceback, exit code 4)
3. Batch launcher pause test (GUI.bat and 启动GUI.bat pause on non-zero exit, hold open without vanishing, exit properly on stdin)
4. Batch script delayed expansion bug audit (Line 114 string truncation reproduction)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
GUI_LAUNCHER_PY = TOOLS_DIR / "gui_launcher.py"
GUI_BAT = ROOT / "GUI.bat"
QI_DONG_GUI_BAT = ROOT / "启动GUI.bat"

PYTHON_EXE = sys.executable


def _decode_console(raw: bytes) -> str:
    """解码 .bat 控制台输出。

    [R2-C3] GUI.bat 等启动脚本已改为 GBK(CP936) 落盘 + ``chcp 936``：输出编码
    由控制台码页决定，不再恒为 UTF-8。硬编码 ``decode("utf-8")`` 会把中文断言
    变成乱码而误判失败 —— 这里按「UTF-8 严格 → GBK」顺序自适应。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


# ==============================================================================
# Task 1: CLI Options Verification
# ==============================================================================

def test_cli_preflight_only_returncode_zero():
    """
    Test 1.1: Run `python tools/gui_launcher.py --preflight-only`
    and assert returncode == 0 and contains preflight report.
    """
    proc = subprocess.run(
        [PYTHON_EXE, str(GUI_LAUNCHER_PY), "--preflight-only"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert proc.returncode == 0, f"Expected returncode 0, got {proc.returncode}. Stderr: {proc.stderr}"
    assert "Preflight Check" in proc.stdout or "启动前置诊断" in proc.stdout
    assert "Python 运行时" in proc.stdout
    assert "VC++" in proc.stdout
    assert "Qt 渲染环境" in proc.stdout
    assert "预检耗时" in proc.stdout


def test_cli_help_documents_all_options():
    """
    Test 1.2: Run `python tools/gui_launcher.py --help`
    and verify all options are documented.
    """
    proc = subprocess.run(
        [PYTHON_EXE, str(GUI_LAUNCHER_PY), "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert proc.returncode == 0
    help_text = proc.stdout
    expected_options = [
        "-h, --help",
        "--debug",
        "-d",
        "--console",
        "-c",
        "--software-opengl",
        "--preflight-only",
        "--doctor",
    ]
    for opt in expected_options:
        assert opt in help_text, f"Expected option '{opt}' to be documented in --help output"


def test_cli_software_opengl_flag_acceptance():
    """
    Test 1.3: Run `python tools/gui_launcher.py --software-opengl --preflight-only`
    and verify acceptance and successful exit.
    """
    proc = subprocess.run(
        [PYTHON_EXE, str(GUI_LAUNCHER_PY), "--software-opengl", "--preflight-only"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert proc.returncode == 0
    assert "QT_OPENGL=software" in proc.stdout or "软件 OpenGL" in proc.stdout


# ==============================================================================
# Task 2: Adversarial Crash Test (Watchdog Early Exit Catching)
# ==============================================================================

def test_adversarial_crash_immediate_exception(tmp_path):
    """
    Test 2.1: Execute `python tools/gui_launcher.py` with a mock target that raises
    an immediate exception. Verify that the 2.5s watchdog catches it,
    writes logs/gui_crash.log, prints the traceback, and exits with code 4.
    """
    sandbox_tools = tmp_path / "tools"
    sandbox_tools.mkdir(parents=True, exist_ok=True)
    sandbox_logs = tmp_path / "logs"

    shutil.copy2(GUI_LAUNCHER_PY, sandbox_tools / "gui_launcher.py")

    mock_gui = sandbox_tools / "ky_gui.py"
    mock_gui_content = (
        "import sys\n"
        "import traceback\n"
        "def blow_up():\n"
        "    raise RuntimeError('ADVERSARIAL_CRASH_SIMULATION: Fatal GPU Context Error on line 42')\n"
        "if __name__ == '__main__':\n"
        "    blow_up()\n"
    )
    mock_gui.write_text(mock_gui_content, encoding="utf-8")

    t0 = time.time()
    proc = subprocess.run(
        [PYTHON_EXE, str(sandbox_tools / "gui_launcher.py")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    elapsed = time.time() - t0

    # 1. Verify exit code == 4
    assert proc.returncode == 4, f"Expected exit code 4, got {proc.returncode}. Output: {proc.stdout}\n{proc.stderr}"

    # 2. Verify watchdog caught it promptly (within reasonable time)
    assert elapsed < 5.0, f"Execution took too long: {elapsed:.2f}s"

    # 3. Verify logs/gui_crash.log was written
    crash_log = sandbox_logs / "gui_crash.log"
    assert crash_log.exists(), f"Crash log not found at {crash_log}"
    log_content = crash_log.read_text(encoding="utf-8", errors="replace")

    # 4. Verify crash log contains traceback and error details
    assert "ADVERSARIAL_CRASH_SIMULATION" in log_content
    assert "Fatal GPU Context Error" in log_content

    # 5. Verify stdout/stderr contains crash notice and traceback
    combined_output = proc.stdout + "\n" + proc.stderr
    assert "异常闪退" in combined_output or "闪退" in combined_output
    assert "ADVERSARIAL_CRASH_SIMULATION" in combined_output
    assert "RuntimeError" in combined_output


def test_adversarial_crash_immediate_exit_code_1(tmp_path):
    """
    Test 2.2: Execute `python tools/gui_launcher.py` with a mock target that
    prints an error message to stderr and exits with code 1.
    Verify watchdog catches it, writes crash log, and exits with code 4.
    """
    sandbox_tools = tmp_path / "tools"
    sandbox_tools.mkdir(parents=True, exist_ok=True)
    sandbox_logs = tmp_path / "logs"

    shutil.copy2(GUI_LAUNCHER_PY, sandbox_tools / "gui_launcher.py")

    mock_gui = sandbox_tools / "ky_gui.py"
    mock_gui.write_text(
        "import sys\n"
        "sys.stderr.write('CRITICAL: PySide6 platform plugin qwindows failed to load\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [PYTHON_EXE, str(sandbox_tools / "gui_launcher.py")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )

    assert proc.returncode == 4
    crash_log = sandbox_logs / "gui_crash.log"
    assert crash_log.exists()
    log_content = crash_log.read_text(encoding="utf-8", errors="replace")
    assert "qwindows failed to load" in log_content

    combined_output = proc.stdout + "\n" + proc.stderr
    assert "qwindows failed to load" in combined_output


def test_adversarial_crash_hard_process_exit(tmp_path):
    """
    Test 2.3: Execute with a mock target that terminates via os._exit(134) (e.g. SIGABRT simulation).
    Verify watchdog catches it and exits with code 4.
    """
    sandbox_tools = tmp_path / "tools"
    sandbox_tools.mkdir(parents=True, exist_ok=True)
    sandbox_logs = tmp_path / "logs"

    shutil.copy2(GUI_LAUNCHER_PY, sandbox_tools / "gui_launcher.py")

    mock_gui = sandbox_tools / "ky_gui.py"
    mock_gui.write_text(
        "import os, sys\n"
        "sys.stderr.write('Fatal C++ Abort: Incompatible Qt DLLs detected\\n')\n"
        "os._exit(134)\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [PYTHON_EXE, str(sandbox_tools / "gui_launcher.py")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )

    assert proc.returncode == 4
    crash_log = sandbox_logs / "gui_crash.log"
    assert crash_log.exists()
    log_content = crash_log.read_text(encoding="utf-8", errors="replace")
    assert "Fatal C++ Abort" in log_content


# ==============================================================================
# Task 3: Batch Launcher Pause Test (GUI.bat & 启动GUI.bat)
# ==============================================================================

@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script test requires Windows")
def test_batch_gui_pauses_on_launcher_crash(tmp_path):
    """
    Test 3.1: Test invoking GUI.bat with a simulated error condition.
    Verify via automated script that when the launcher exits with non-zero,
    the batch script PAUSES/HOLDS rather than vanishing silently, and exits
    only after user input is received.
    """
    sandbox_tools = tmp_path / "tools"
    sandbox_tools.mkdir(parents=True, exist_ok=True)

    shutil.copy2(GUI_BAT, tmp_path / "GUI.bat")

    mock_launcher = sandbox_tools / "gui_launcher.py"
    mock_launcher.write_text(
        "import sys\n"
        "print('[MOCK_LAUNCHER] Simulated watchdog failure: GUI crash detected')\n"
        "sys.exit(4)\n",
        encoding="utf-8",
    )

    cmd = ["cmd.exe", "/c", str(tmp_path / "GUI.bat")]
    proc = subprocess.Popen(
        cmd,
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # 1. Wait 1.0 second. Since the batch script hit 'pause', the process MUST still be alive!
        time.sleep(1.0)
        assert proc.poll() is None, "Batch script terminated prematurely! It did NOT pause on non-zero exit!"

        # 2. Now send a newline into stdin to satisfy the pause prompt
        raw_out, raw_err = proc.communicate(input=b"\r\n", timeout=5)
        stdout_data = _decode_console(raw_out)

        # 3. Now verify the process has terminated with returncode 4
        assert proc.returncode == 4, f"Expected returncode 4 from batch script, got {proc.returncode}"

        # 4. Verify diagnostic guidance is printed
        assert "logs\\gui_crash.log" in stdout_data
        assert "调试模式启动GUI.bat" in stdout_data or "gui_crash.log" in stdout_data

    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script test requires Windows")
def test_batch_qi_dong_gui_pauses_on_launcher_crash(tmp_path):
    """
    Test 3.2: Verify that 启动GUI.bat also pauses when GUI.bat hits an error.
    """
    sandbox_tools = tmp_path / "tools"
    sandbox_tools.mkdir(parents=True, exist_ok=True)

    shutil.copy2(GUI_BAT, tmp_path / "GUI.bat")
    shutil.copy2(QI_DONG_GUI_BAT, tmp_path / "启动GUI.bat")

    mock_launcher = sandbox_tools / "gui_launcher.py"
    mock_launcher.write_text(
        "import sys\n"
        "print('[MOCK_LAUNCHER] Critical dependency failure')\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )

    cmd = ["cmd.exe", "/c", str(tmp_path / "启动GUI.bat")]
    proc = subprocess.Popen(
        cmd,
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        time.sleep(1.0)
        assert proc.poll() is None, "启动GUI.bat exited prematurely without pausing!"

        raw_out, _ = proc.communicate(input=b"\r\n", timeout=5)
        assert proc.returncode == 2

    finally:
        if proc.poll() is None:
            proc.kill()


# ==============================================================================
# Full End-to-End Chain: Real Watchdog Crash Caught by Real GUI.bat
# ==============================================================================

@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script test requires Windows")
def test_full_chain_batch_invokes_launcher_catches_real_watchdog_crash(tmp_path):
    """
    Test 3.3: Complete integration stress test:
    - Real GUI.bat
    - Real gui_launcher.py (with 2.5s watchdog)
    - Adversarial ky_gui.py crashing on startup
    Verify full chain:
    mock ky_gui crashes -> watchdog catches it -> logs/gui_crash.log created ->
    gui_launcher exits with 4 -> GUI.bat detects exit 4 -> prints error banner ->
    PAUSES until user presses enter -> exits with 4.
    """
    sandbox_tools = tmp_path / "tools"
    sandbox_tools.mkdir(parents=True, exist_ok=True)
    sandbox_logs = tmp_path / "logs"

    shutil.copy2(GUI_BAT, tmp_path / "GUI.bat")
    shutil.copy2(GUI_LAUNCHER_PY, sandbox_tools / "gui_launcher.py")

    # Mock crashing GUI
    mock_gui = sandbox_tools / "ky_gui.py"
    mock_gui.write_text(
        "import sys\n"
        "sys.stderr.write('CRITICAL EXCEPTION: Cannot load Vulkan/DirectX backend\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    cmd = ["cmd.exe", "/c", str(tmp_path / "GUI.bat")]
    proc = subprocess.Popen(
        cmd,
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait 3.5 seconds (watchdog is 2.5s)
        time.sleep(3.5)
        # Should now be paused in GUI.bat
        assert proc.poll() is None, "Batch script did not hold on pause after launcher watchdog exit!"

        raw_out, _ = proc.communicate(input=b"\r\n", timeout=5)
        stdout_data = _decode_console(raw_out)

        # Batch script should exit with code 4
        assert proc.returncode == 4, f"Expected return code 4, got {proc.returncode}"

        # Check that crash log exists
        crash_log = sandbox_logs / "gui_crash.log"
        assert crash_log.exists(), "Crash log was not written to logs/gui_crash.log"
        log_txt = crash_log.read_text(encoding="utf-8", errors="replace")
        assert "Cannot load Vulkan/DirectX backend" in log_txt

        # Verify console output contains the log reference and pause
        assert "logs\\gui_crash.log" in stdout_data or "gui_crash.log" in stdout_data

    finally:
        if proc.poll() is None:
            proc.kill()


# ==============================================================================
# Task 4: Empirical Audit for Delayed Expansion Bug in GUI.bat
# ==============================================================================

@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script test requires Windows")
def test_audit_gui_bat_line_114_delayed_expansion_truncation(tmp_path):
    """
    Empirical Bug Audit:
    In GUI.bat, line 114:
        echo   [!] GUI 客户端异常退出 [退出码: !LAUNCHER_EXIT!]
    Because `setlocal enabledelayedexpansion` is active, cmd.exe treats
    everything between the first `!` (in `[!]`) and second `!` (before `LAUNCHER_EXIT`)
    as a variable named `] GUI 客户端异常退出 [退出码: `.
    Because that variable is unset, cmd.exe substitutes it with empty string,
    resulting in output `  [ 4]` instead of `  [!] GUI 客户端异常退出 [退出码: 4]`.

    This test empirically documents this behavior.
    """
    sandbox_tools = tmp_path / "tools"
    sandbox_tools.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GUI_BAT, tmp_path / "GUI.bat")

    mock_launcher = sandbox_tools / "gui_launcher.py"
    mock_launcher.write_text("import sys\nsys.exit(4)\n", encoding="utf-8")

    cmd = ["cmd.exe", "/c", str(tmp_path / "GUI.bat")]
    proc = subprocess.Popen(
        cmd,
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    raw_out, _ = proc.communicate(input=b"\r\n", timeout=5)
    stdout_text = _decode_console(raw_out)

    # Document empirical observation: line 114 outputs `  [ 4]` instead of full headline
    has_full_headline = "GUI 客户端异常退出" in stdout_text
    has_truncated_line = "  [ 4]" in stdout_text

    # As a challenger test, we record this observation:
    # If unpatched, has_truncated_line is True and has_full_headline is False.
    print(f"\n[Audit] GUI.bat Line 114: Full headline present = {has_full_headline}, Truncated '  [ 4]' = {has_truncated_line}")
    assert has_truncated_line or has_full_headline, "Unexpected output from GUI.bat error banner"
