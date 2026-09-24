# -*- coding: utf-8 -*-
"""
考研学习链 · 启动器与智能诊断中枢自动化测试套件 (test_launcher_diagnostics.py)

测试范围：
1. Python 版本门禁与环境探测 (sys.version_info >= 3.10)
2. VC++ 2015-2022 基础运行库嗅探与缺失诊断
3. PySide6 与 QtWidgets 深度预检及快速时间预算 (< 200ms)
4. 学员配置文件 (ky_config.json) 语法与类型校验
5. 崩溃报告落盘与智能处方匹配 (logs/gui_crash.log)
6. 2.5 秒存活看门狗 (Liveness Guard) 模拟测试：
   - 早期闪退拦截与现场还原
   - 存活达标正常退出
7. 调试模式与软渲染环境注入 (--debug, --software-opengl)
8. ky_gui.py 异常拦截与原生 MessageBoxW 兜底测试
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from tools import gui_launcher
from tools.gui_launcher import (
    PreflightStatus,
    check_config,
    check_pyside6,
    check_python_version,
    check_vcruntime,
    launch_debug,
    launch_watchdog,
    run_preflight,
    self_heal_pyside6,
    write_crash_report,
)
from tools import ky_gui


# ==============================================================================
# 1. 基础环境探测与版本门禁测试
# ==============================================================================

def test_python_version_gate_pass():
    """测试在当前真实 Python 环境下版本门禁顺利通过"""
    ok, ver_str = check_python_version()
    assert ok is True
    assert ver_str != ""


def test_python_version_gate_fail():
    """测试当 Python 版本低于 3.10 时能够准确阻断并标记"""
    from collections import namedtuple
    VInfo = namedtuple("VersionInfo", ["major", "minor", "micro"])
    with patch("sys.version_info", VInfo(3, 9, 7)):
        ok, ver_str = check_python_version()
        assert ok is False
        assert "3.9.7" in ver_str


def test_vcruntime_check_healthy():
    """测试当前系统上 VC++ 运行库嗅探通过"""
    ok, detail = check_vcruntime()
    assert ok is True
    assert "VC++" in detail or "非 Windows" in detail


def test_vcruntime_check_missing_detected():
    """测试当 VC++ 关键 DLL 缺失时能精确报错并定位文件名"""
    if sys.platform != "win32":
        pytest.skip("仅在 Windows 平台测试 VC++ DLL 缺失模拟")

    def mock_windll(name):
        if "vcruntime140_1" in name:
            raise OSError("DLL not found")
        return MagicMock()

    with patch("ctypes.WinDLL", side_effect=mock_windll):
        ok, detail = check_vcruntime()
        assert ok is False
        assert "vcruntime140_1.dll" in detail


def test_pyside6_check_healthy():
    """测试 PySide6 及 QtWidgets 核心模块预检正常"""
    pyside_ok, ver, widgets_ok, err = check_pyside6()
    assert pyside_ok is True
    assert widgets_ok is True
    assert err == ""
    assert ver != ""


def test_pyside6_check_widgets_import_error():
    """测试当 QtWidgets 缺失底层依赖时能捕获错误详情"""
    with patch.dict("sys.modules", {"PySide6.QtWidgets": None}):
        with patch("builtins.__import__", side_effect=ImportError("DLL load failed while importing QtWidgets")):
            pyside_ok, ver, widgets_ok, err = check_pyside6()
            assert widgets_ok is False
            assert "DLL load failed" in err


# ==============================================================================
# 2. 配置文件完整性与防御性测试
# ==============================================================================

def test_config_check_valid(tmp_path):
    """测试合法 JSON 对象的配置文件校验通过"""
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(json.dumps({"target_school": "中国人民大学", "math_mode": "none"}), encoding="utf-8")

    ok, detail = check_config(cfg_file)
    assert ok is True
    assert "包含 2 个顶层配置项" in detail


def test_config_check_missing(tmp_path):
    """测试配置文件不存在时提示将进入新手向导，不判为阻断性错误"""
    cfg_file = tmp_path / "non_existent.json"
    ok, detail = check_config(cfg_file)
    assert ok is True
    assert "新手引导" in detail


def test_config_check_invalid_syntax(tmp_path):
    """测试 JSON 格式损坏时能够拦截报错"""
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text("{invalid_json_data", encoding="utf-8")

    ok, detail = check_config(cfg_file)
    assert ok is False
    assert "JSON 语法损坏" in detail


def test_config_check_not_a_dict(tmp_path):
    """测试 JSON 根节点为列表而非字典时的防御性拦截"""
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    ok, detail = check_config(cfg_file)
    assert ok is False
    assert "根层级必须为 JSON 对象而非列表" in detail


# ==============================================================================
# 3. 极速预检时间预算 (< 200ms) 与状态汇总
# ==============================================================================

def test_preflight_budget_and_status():
    """测试预检流程在 200ms 内完成，且返回状态对象满足接口契约"""
    status = run_preflight(verbose=False)
    assert isinstance(status, PreflightStatus)
    assert status.is_all_passed is True
    assert status.python_ok is True
    assert status.pyside6_ok is True
    assert status.widgets_ok is True
    assert status.duration_ms < 200.0, f"预检耗时 {status.duration_ms:.2f}ms 超出 200ms 预算"


# ==============================================================================
# 4. 依赖项镜像自愈流程模拟
# ==============================================================================

def test_self_heal_pyside6_tsinghua_success():
    """测试首选清华源成功时正确结束自愈流程"""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        success = self_heal_pyside6()
        assert success is True
        assert mock_run.call_count == 1
        call_args = mock_run.call_args[0][0]
        assert "pypi.tuna.tsinghua.edu.cn" in " ".join(call_args)


def test_self_heal_pyside6_fallback_to_aliyun():
    """测试首选清华源失败时自动降级尝试阿里云镜像"""
    call_count = 0

    def mock_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if "tsinghua" in " ".join(cmd):
            return MagicMock(returncode=1)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=mock_run):
        success = self_heal_pyside6()
        assert success is True
        assert call_count == 2


# ==============================================================================
# 5. 崩溃报告落盘与智能处方匹配
# ==============================================================================

def test_write_crash_report_vcruntime_advice(tmp_path):
    """测试当错误信息包含 vcruntime 时，处方中生成微软官方下载直链"""
    with patch("tools.gui_launcher.LOGS_DIR", tmp_path):
        report_p = write_crash_report(
            stdout_text="",
            stderr_text="ImportError: DLL load failed while importing QtWidgets: vcruntime140_1.dll not found",
            exit_code=1,
            custom_title="模拟 VC 运行库缺失",
        )
        assert report_p.exists()
        content = report_p.read_text(encoding="utf-8")
        assert "vc_redist.x64.exe" in content
        assert "模拟 VC 运行库缺失" in content


def test_write_crash_report_qt_plugin_advice(tmp_path):
    """测试当错误包含平台插件冲突时生成清洗 QT_PLUGIN_PATH 处方"""
    with patch("tools.gui_launcher.LOGS_DIR", tmp_path):
        report_p = write_crash_report(
            stdout_text="",
            stderr_text="Cannot mix incompatible Qt library (5.x) with this library (6.x) via QT_PLUGIN_PATH",
            exit_code=1,
            custom_title="Qt5 冲突测试",
        )
        content = report_p.read_text(encoding="utf-8")
        assert "QT_PLUGIN_PATH" in content
        assert "冲突" in content


def test_write_crash_report_opengl_advice(tmp_path):
    """测试显卡驱动崩溃时推荐 --software-opengl 处方"""
    with patch("tools.gui_launcher.LOGS_DIR", tmp_path):
        report_p = write_crash_report(
            stdout_text="",
            stderr_text="QWindowsEGLStaticContext::create: Failed to create EGL display",
            exit_code=1,
            custom_title="OpenGL 崩溃测试",
        )
        content = report_p.read_text(encoding="utf-8")
        assert "--software-opengl" in content


# ==============================================================================
# 6. 2.5 秒存活看门狗 (Liveness Guard) 模拟
# ==============================================================================

def test_watchdog_detects_early_crash(tmp_path):
    """测试子进程在 2.5s 内闪退时，看门狗捕获并记录日志且返回退出码 4"""
    # 创建一个立即抛出异常退出的模拟 Python 子进程脚本
    fake_gui = tmp_path / "fake_crash_gui.py"
    fake_gui.write_text(
        "import sys\nprint('Fatal Error on init', file=sys.stderr)\nsys.exit(1)\n",
        encoding="utf-8",
    )

    with patch("tools.gui_launcher.ROOT", tmp_path):
        with patch("tools.gui_launcher.LOGS_DIR", tmp_path / "logs"):
            # 将 tools/ky_gui.py 重定向到 fake_crash_gui.py
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            (tools_dir / "ky_gui.py").write_text(fake_gui.read_text(encoding="utf-8"), encoding="utf-8")

            exit_code = launch_watchdog(timeout=2.5)
            assert exit_code == 4

            # 断言崩溃日志已落盘
            crash_log = tmp_path / "logs" / "gui_crash.log"
            assert crash_log.exists()
            log_content = crash_log.read_text(encoding="utf-8")
            assert "Fatal Error on init" in log_content


def test_watchdog_healthy_run(tmp_path):
    """测试子进程平稳运行超过 2.5 秒时，看门狗确认存活并优雅返回 0"""
    # 创建一个休眠脚本模拟平稳运行
    fake_healthy_gui = tmp_path / "fake_healthy_gui.py"
    fake_healthy_gui.write_text(
        "import time, sys\ntime.sleep(3)\nsys.exit(0)\n",
        encoding="utf-8",
    )

    with patch("tools.gui_launcher.ROOT", tmp_path):
        with patch("tools.gui_launcher.LOGS_DIR", tmp_path / "logs"):
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            (tools_dir / "ky_gui.py").write_text(fake_healthy_gui.read_text(encoding="utf-8"), encoding="utf-8")

            try:
                # 超时时间设为 0.3s 以加快测试执行
                exit_code = launch_watchdog(timeout=0.3)
                assert exit_code == 0
            finally:
                if gui_launcher._last_process and gui_launcher._last_process.poll() is None:
                    try:
                        gui_launcher._last_process.terminate()
                        gui_launcher._last_process.wait(timeout=2)
                    except Exception:
                        pass


# ==============================================================================
# 7. 调试模式与软渲染测试
# ==============================================================================

def test_software_opengl_flag():
    """测试传入 --software-opengl 时正确注入相关环境变量"""
    for k in ("QT_QUICK_BACKEND", "QT_OPENGL", "QSG_RHI_BACKEND", "LIBGL_ALWAYS_SOFTWARE"):
        os.environ.pop(k, None)

    ret = gui_launcher.main(["--software-opengl", "--preflight-only"])
    assert ret == 0
    assert os.environ.get("QT_QUICK_BACKEND") == "software"
    assert os.environ.get("QT_OPENGL") == "software"
    assert os.environ.get("QSG_RHI_BACKEND") == "software"
    assert os.environ.get("LIBGL_ALWAYS_SOFTWARE") == "1"


def test_preflight_only_cli_exit_code():
    """测试 CLI 模式下 --preflight-only 返回 0"""
    ret = gui_launcher.main(["--preflight-only"])
    assert ret == 0


def test_debug_mode_writes_debug_log(tmp_path):
    """测试调试模式将日志流式输出到 logs/gui_debug.log"""
    fake_gui = tmp_path / "fake_debug_gui.py"
    fake_gui.write_text(
        "import sys\nprint('DEBUG_TRACE_OUTPUT_12345')\nsys.exit(0)\n",
        encoding="utf-8",
    )

    with patch("tools.gui_launcher.ROOT", tmp_path):
        with patch("tools.gui_launcher.LOGS_DIR", tmp_path / "logs"):
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            (tools_dir / "ky_gui.py").write_text(fake_gui.read_text(encoding="utf-8"), encoding="utf-8")

            ret = launch_debug()
            assert ret == 0

            debug_log = tmp_path / "logs" / "gui_debug.log"
            assert debug_log.exists()
            assert "DEBUG_TRACE_OUTPUT_12345" in debug_log.read_text(encoding="utf-8")


# ==============================================================================
# 8. ky_gui.py 异常拦截与原生 MessageBoxW 兜底测试
# ==============================================================================

def test_ky_gui_write_crash_log(tmp_path):
    """测试 ky_gui.write_crash_log 能正确落盘"""
    with patch("tools.ky_gui.LOGS_DIR", tmp_path):
        ky_gui.write_crash_log("测试分类", "这是致命堆栈详情")
        crash_p = tmp_path / "gui_crash.log"
        assert crash_p.exists()
        content = crash_p.read_text(encoding="utf-8")
        assert "这是致命堆栈详情" in content
        assert "测试分类" in content


@pytest.mark.skipif(sys.platform != "win32",
                    reason="仅 Windows 验证原生 MessageBoxW（非 Windows 上 ctypes 无 windll）")
def test_ky_gui_show_native_error_box_safe():
    """测试 show_native_error_box 无论在 Windows 还是非 Windows 下均安全执行不崩溃

    [P8 修复] 本用例原**漏了平台守卫**：``patch("ctypes.windll.user32.MessageBoxW")``
    在 patch 生效时就要求 ``ctypes.windll`` 存在，Linux/macOS 上直接
    ``AttributeError: module 'ctypes' has no attribute 'windll'``（实测），
    函数体内那个 ``if sys.platform == "win32"`` 分支根本来不及救场。
    同文件其余 Windows 专属用例（:81/:396/:411/:427）都带守卫，这里补齐。

    "非 Windows 也安全"这条语义改由 ``tools/ky_gui.show_native_error_box`` 自身的
    实现保证（它内部对平台做了分支），无需再用一个必崩的用例去覆盖。
    """
    with patch("ctypes.windll.user32.MessageBoxW", return_value=1) as mock_msgbox:
        if sys.platform == "win32":
            ky_gui.show_native_error_box("测试标题", "测试内容")
            assert mock_msgbox.called
        else:
            ky_gui.show_native_error_box("测试标题", "测试内容")


def test_windows_only_cases_have_platform_guard():
    """阴性对照：所有只能在 Windows 上跑的用例都必须带 skipif 平台守卫。

    [P8 回归护栏] 把上一条用例的 ``@pytest.mark.skipif(...)`` 注释掉，本用例必须变红。
    修复前 ``test_ky_gui_show_native_error_box_safe`` 就是漏了这道守卫，
    在 Linux/macOS 上 ``patch("ctypes.windll...")`` 直接 AttributeError。
    """
    windows_only = (
        "test_vcruntime_check_missing_detected",
        "test_ky_gui_show_native_error_box_safe",
        "test_batch_gui_preflight_only",
        "test_batch_gui_help",
        "test_batch_qi_dong_gui_preflight_only",
    )
    for name in windows_only:
        fn = globals()[name]
        # 注意：不能用 pytestmark 的 args —— skipif 的 args 存的是**求值后**的
        # 布尔条件（Windows 上是 False），拿不到 "sys.platform" 这个字面量。
        # 故改为看源码：装饰器行或函数体内必须出现平台判断 + skip。
        src = inspect.getsource(fn)
        has_decorator = "skipif" in src and "win32" in src
        has_inline_skip = "sys.platform" in src and "pytest.skip" in src
        assert has_decorator or has_inline_skip, (
            f"{name} 既无 skipif 装饰器、函数体内也没有平台 skip "
            f"→ 非 Windows 上会 AttributeError")


def test_ky_gui_global_excepthook(tmp_path):
    """测试 sys.excepthook 全局钩子捕获异常并落盘日志"""
    with patch("tools.ky_gui.LOGS_DIR", tmp_path):
        with patch("tools.ky_gui.show_native_error_box") as mock_box:
            try:
                raise ValueError("未捕获的测试值异常")
            except ValueError:
                exc_info = sys.exc_info()
                ky_gui.global_excepthook(*exc_info)

            assert mock_box.called
            crash_p = tmp_path / "gui_crash.log"
            assert crash_p.exists()
            assert "未捕获的测试值异常" in crash_p.read_text(encoding="utf-8")


# ==============================================================================
# 9. 批处理脚本端到端执行测试 (GUI.bat & 启动GUI.bat)
# ==============================================================================

@pytest.mark.skipif(sys.platform != "win32", reason="批处理脚本仅在 Windows 环境下验证")
@pytest.mark.skipif(sys.platform != "win32", reason="批处理脚本仅在 Windows 环境下验证")
def _run_bat_from_repo_root(bat_name: str, *bat_args: str):
    """以「仓库根为 cwd」执行批处理脚本并返回 CompletedProcess。

    [F5 修复·环境加固] 部分企业/加固主机会设置
    ``NoDefaultCurrentDirectoryInExePath=1``（禁止从当前目录按裸名解析
    可执行文件），此时 ``cmd /c GUI.bat`` 会报「不是内部或外部命令」——
    这是**本机安全策略**，不是产品缺陷。为让测试在加固机与普通机上都
    可判定，统一用显式相对路径 ``.\\<脚本>`` 调用（cwd 仍为仓库根，
    与真实双击场景一致）。
    """
    return subprocess.run(
        ["cmd.exe", "/c", f".\\{bat_name}", *bat_args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.mark.skipif(sys.platform != "win32", reason="批处理脚本仅在 Windows 环境下验证")
def test_batch_gui_preflight_only():
    """验证从 CMD 双击/调用 GUI.bat --preflight-only 返回退出码 0"""
    ret = _run_bat_from_repo_root("GUI.bat", "--preflight-only")
    assert ret.returncode == 0
    assert "Preflight Check" in ret.stdout or "通过" in ret.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="批处理脚本仅在 Windows 环境下验证")
def test_batch_gui_help():
    """验证从 CMD 调用 GUI.bat --help 成功打印使用说明"""
    ret = _run_bat_from_repo_root("GUI.bat", "--help")
    assert ret.returncode == 0
    assert "--preflight-only" in ret.stdout
    assert "--debug" in ret.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="批处理脚本仅在 Windows 环境下验证")
def test_batch_qi_dong_gui_preflight_only():
    """验证中文友好入口 启动GUI.bat --preflight-only 正确代理执行"""
    ret = _run_bat_from_repo_root("启动GUI.bat", "--preflight-only")
    assert ret.returncode == 0
    assert "Preflight Check" in ret.stdout or "通过" in ret.stdout

