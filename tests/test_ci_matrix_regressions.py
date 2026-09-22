# -*- coding: utf-8 -*-
"""公开副本 CI 矩阵缺陷回归测试。

背景：公开仓库的 GitHub Actions 矩阵（ubuntu / windows / macos × Python
3.10/3.11/3.12）长期全红。逐作业拉取日志后定位到 5 个**互不相同**的根因，
其中 4 个与平台强相关、本机（Windows + 3.12）永不复现，故此前一直无人发现：

  1. ``tools/llm_client.py`` 把自身写进 ``sys.modules["tools.llm_client"]``，
     却从不设置父包属性 —— 先以顶层名 ``llm_client`` 导入时，
     ``mock.patch("tools.llm_client.X")`` 抛 AttributeError（Python 3.10
     实测 16 个用例变红）。
  2. ``test_cli_security_hardening`` 的一个用例把**全局** ``os.name`` 改成
     posix；Windows + Python 3.10/3.11 下 ``pathlib`` 随即在 pytest 渲染失败
     报告时抛 NotImplementedError，升级为 INTERNALERROR ``exit 3``，
     整轮只跑到第 142 个用例就中止。
  3. ``test_challenger2_gen4_stress`` 的剪贴板压力用例缺非 Windows 守卫。
  4. ``test_challenger_launcher_stress`` 的 4 个批量启动用例依赖 runner 的
     解释器探测结果（可能触发联网安装 PySide6），超过 5s 超时。
  5. macOS 崩溃源是 ``pymupdf`` 原生库 import 期段错误（不是 Qt）。

本文件把上述不变量固化成可执行的守门用例 —— 每条都能用「把修复注释掉」
的方式变红（见各用例 docstring 里的阴性对照说明）。
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"


# ══════════════════════════════════════════════════════════════════════════
# 1. llm_client 别名必须挂到父包属性上
# ══════════════════════════════════════════════════════════════════════════

def test_llm_client_alias_binds_parent_package_attribute():
    """以顶层名 ``llm_client`` 先导入时，``tools`` 包上必须有 ``llm_client`` 属性。

    阴性对照：删掉 ``tools/llm_client.py`` 里那段 ``setattr(_parent,
    "llm_client", _MODULE)``，本用例在任何 Python 版本上都会变红
    （子进程里 ``getattr(tools, "llm_client")`` 为 None，随后的 patch 抛
    AttributeError）。

    用子进程固定导入顺序：本进程的 ``sys.modules`` 早已被其它用例污染，
    在进程内复现不了「顶层名先导入」这一前提。
    """
    code = (
        "import sys\n"
        "sys.path.insert(0, 'tools')\n"
        "sys.path.insert(0, '.')\n"
        "import llm_client\n"
        "import tools\n"
        "assert getattr(tools, 'llm_client', None) is llm_client, (\n"
        "    'sys.modules 里有 tools.llm_client，但 tools 包上没有该属性')\n"
        "from unittest.mock import patch\n"
        "with patch('tools.llm_client.safe_urlopen'):\n"
        "    pass\n"
        "print('ALIAS_OK')\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert res.returncode == 0, f"stdout={res.stdout}\nstderr={res.stderr}"
    assert "ALIAS_OK" in res.stdout


# ══════════════════════════════════════════════════════════════════════════
# 2. 测试不得伪造全局 os.name / os.chmod
# ══════════════════════════════════════════════════════════════════════════

def _fakes_global_os(tree: ast.AST) -> bool:
    """AST 层面查找 ``setattr(<os|xxx.os>, "name"|"chmod", ...)``。

    必须走 AST 而非正则：本仓库的**注释与 docstring 里大量引用这个反面写法**
    作为说明（例如 ``_PosixOsShim`` 的文档），纯文本匹配会把说明本身判成违规。
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "setattr"):
            continue
        if len(node.args) < 2:
            continue
        target, attr = node.args[0], node.args[1]
        if not (isinstance(attr, ast.Constant) and attr.value in ("name", "chmod")):
            continue
        if isinstance(target, ast.Name) and target.id == "os":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "os":
            return True
    return False


def test_no_test_fakes_global_os_name_or_chmod():
    """任何测试都不得改写全局 ``os`` 模块的 ``name`` / ``chmod``。

    ``ky_io.os``、``session.os`` 之类**就是**全局 ``os`` 模块，改它等于让整个
    进程伪装成 POSIX。Windows 上 ``pathlib`` 在类创建时就记住了
    ``os.name == 'nt'``，此后任何 ``Path(...)`` 都抛 NotImplementedError ——
    而 pytest 渲染失败报告时也要构造 ``Path``，于是一个用例的失败会被放大成
    整轮 INTERNALERROR（实测公开副本 CI：Python 3.10/3.11 ``exit 3``，
    整轮只跑到第 142 个用例）。需要 POSIX 语义时请在**被测模块内**注入替身
    （见 ``test_cli_security_hardening._PosixOsShim``）。

    阴性对照：把 ``test_align_to_umask_sensitive_is_0600`` 改回
    ``monkeypatch.setattr(ky_io.os, "name", "posix")``，本用例变红。
    """
    offenders = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:                      # 语法错误由 lint_check 负责
            continue
        if _fakes_global_os(tree):
            offenders.append(path.name)
    assert not offenders, (
        "以下测试文件篡改了全局 os 模块（会连带炸掉 pathlib）：" + ", ".join(offenders))


# ══════════════════════════════════════════════════════════════════════════
# 3. 依赖 Win32 剪贴板的用例必须带非 Windows 守卫
# ══════════════════════════════════════════════════════════════════════════

def test_clipboard_win32_cases_all_guarded_on_non_windows():
    """``TestREPLClipboardEncoding`` 里 patch ``_read_win32_clipboard_text`` 的
    用例都必须先 ``pytest.skip`` 掉非 Windows 平台。

    非 Windows 上 ``get_clipboard_text`` 走 pbpaste/xclip 分支，根本不会调用被
    patch 的函数，返回值恒为空串 —— 断言必然失败（实测 Linux 三个矩阵各 1 个
    失败：``''.startswith(...)``）。

    阴性对照：删掉 ``test_clipboard_long_multiline_text_handling`` 里的
    ``if sys.platform != "win32": pytest.skip(...)``，本用例变红。
    """
    path = TESTS_DIR / "test_challenger2_gen4_stress.py"
    source = path.read_text(encoding="utf-8")
    # 按「4 空格缩进的 def」切块；切出的第一段是模块头（含 docstring），须剔除，
    # 否则模块 docstring 里提到的 _read_win32_clipboard_text 会被误判成用例。
    blocks = [b for b in re.split(r"\n(?=    def )", source) if b.lstrip().startswith("def ")]
    unguarded = []
    for block in blocks:
        if "_read_win32_clipboard_text" not in block:
            continue
        name = block.lstrip().split("(", 1)[0].strip()
        if "sys.platform" not in block or "skip" not in block:
            unguarded.append(name)
    assert not unguarded, (
        "以下用例 patch 了 Win32 剪贴板却缺少非 Windows 守卫：" + ", ".join(unguarded))


# ══════════════════════════════════════════════════════════════════════════
# 4. GUI.bat 必须命中沙箱 .venv（而不是 runner 的环境解释器）
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch script test requires Windows")
def test_gui_bat_resolves_to_sandbox_venv(tmp_path):
    """沙箱里预置 ``.venv`` 后，GUI.bat 必须用它，且不触发联网装 PySide6。

    阴性对照：删掉 ``_seed_sandbox_venv(tmp_path)`` 这一行，脚本会回落到
    ``py -3`` / ``where python`` 选中的环境解释器，输出的运行时路径不再是
    沙箱内的 ``.venv``，本用例变红。（CI runner 上那条回退路径还可能卡在
    ``pip install -q PySide6``，正是 4 个批量启动用例超时的根因。）

    本用例让 mock 启动器以 0 退出，因此脚本不会走到 ``pause``，
    用 ``capture_output=True`` 也不会挂起。
    """
    from test_challenger_launcher_stress import (
        GUI_BAT,
        _decode_console,
        _seed_sandbox_venv,
    )

    sandbox_tools = tmp_path / "tools"
    sandbox_tools.mkdir(parents=True, exist_ok=True)
    _seed_sandbox_venv(tmp_path)
    (sandbox_tools / "gui_launcher.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

    bat = tmp_path / "GUI.bat"
    bat.write_bytes(GUI_BAT.read_bytes())

    proc = subprocess.run(
        ["cmd.exe", "/c", str(bat)],
        cwd=str(tmp_path),
        capture_output=True,
        timeout=60,
    )
    out = _decode_console(proc.stdout)
    assert str(tmp_path / ".venv") in out, (
        f"GUI.bat 未命中沙箱 .venv，实际输出：\n{out}")


def test_batch_launcher_cases_seed_sandbox_venv():
    """所有 ``cmd.exe`` 调用 ``GUI.bat`` 的用例都必须先 ``_seed_sandbox_venv``。

    没有沙箱 venv 时，脚本会回落到 ``py -3`` / ``where python`` 选中的环境
    解释器；CI runner 上那个解释器可能没装 PySide6，于是脚本转入
    ``pip install -q PySide6``（联网下载上百 MB），远超用例超时 ——
    公开副本 CI 上 4 个批量启动用例全部 ``subprocess.TimeoutExpired``。

    阴性对照：删掉任意一个用例里的 ``_seed_sandbox_venv(tmp_path)``，
    本用例点名该用例并变红。
    """
    path = TESTS_DIR / "test_challenger_launcher_stress.py"
    source = path.read_text(encoding="utf-8")
    blocks = [b for b in re.split(r"\n(?=def )", source) if b.startswith("def ")]
    offenders = []
    for block in blocks:
        if "cmd.exe" not in block:
            continue
        name = block.split("(", 1)[0].replace("def ", "").strip()
        if "_seed_sandbox_venv(" not in block:
            offenders.append(name)
    assert not offenders, (
        "以下用例调用 cmd.exe 跑 GUI.bat 却未预置沙箱 venv：" + ", ".join(offenders))


# ══════════════════════════════════════════════════════════════════════════
# 5. CI 矩阵不得用 --ignore 静默丢弃测试文件
# ══════════════════════════════════════════════════════════════════════════

def test_ci_workflow_does_not_ignore_test_files():
    """pytest 步骤不得出现 ``--ignore``。

    [实测教训] 上一轮把 macOS 的 ``exit 139`` 误判为 Qt 问题，于是给矩阵加了
    12 个 ``--ignore``。faulthandler 事后证明崩溃源是 ``pymupdf`` 原生库，
    与 Qt 无关 —— 那批 ignore 既没解决问题，又**静默丢掉**了 macOS 上 12 个
    文件的覆盖（约 100+ 用例）。平台差异要么修根因，要么显式 skip 并写明理由，
    不允许整文件屏蔽。

    阴性对照：在 workflow 的 pytest 步骤里加回任意一个
    ``--ignore=tests/test_x.py``，本用例变红。
    """
    import yaml

    doc = yaml.safe_load(io.open(WORKFLOW, encoding="utf-8"))
    offenders = []
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            run = step.get("run") or ""
            if "pytest" in run and "--ignore" in run:
                offenders.append(step.get("name", "<unnamed>"))
    assert not offenders, f"CI 步骤用 --ignore 屏蔽了测试文件：{offenders}"
