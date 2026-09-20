#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考研学习链 · 桌面客户端智能启动与诊断中枢 (gui_launcher.py)

功能职责：
1. 启动前置诊断 (Preflight Check < 200ms)：
   - Python 版本门禁 (>= 3.10)
   - PySide6 及 QtWidgets / QtCore 核心组件探测
   - 微软 Visual C++ 2015-2022 运行库 (vcruntime140_1.dll 等) 嗅探
   - 核心配置文件 (ky_config.json) 完整性校验
2. 缺失依赖自愈 (Self-Healing)：
   - 当 PySide6 缺失时，自动使用国内清华源 / 阿里源执行快速重试安装
3. 2.5 秒存活看门狗 (Liveness Guard / Watchdog)：
   - 普通双击启动时拉起后台 GUI，阻塞监听 2.5 秒
   - 若 2.5 秒内闪退，捕获 Traceback 并自动记录到 logs/gui_crash.log，控制台保持并返回非零退出码
   - 若存活达标，确认进入 Qt 主事件循环，启动器优雅退出
4. 多模式支持：
   - --debug / -d: 开启 QT_DEBUG_PLUGINS=1、详细 Traceback，实时流式输出到控制台与 logs/gui_debug.log
   - --console / -c: 前台控制台模式
   - --software-opengl: 软解渲染模式（虚拟机、老旧核显防崩溃）
   - --preflight-only: 纯预检模式（供自动化测试与外部脚本检测）
   - --doctor: 调用 ky doctor 全系统健康诊断
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 确保项目根目录与 tools 目录在 sys.path 中
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent

TOOLS = ROOT / "tools"
LOGS_DIR = ROOT / "logs"

for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Windows 控制台安全编码
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Style:
    """ANSI 颜色样式定义"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"

    @classmethod
    def supports_color(cls) -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if sys.platform == "win32":
            return os.environ.get("TERM") != "" or "WT_SESSION" in os.environ or "ANSICON" in os.environ or True
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def colored(text: str, color_code: str) -> str:
    if Style.supports_color():
        return f"{color_code}{text}{Style.RESET}"
    return text


@dataclass
class PreflightStatus:
    """预检报告结构体"""
    python_ok: bool = True
    python_version: str = ""
    pyside6_ok: bool = False
    pyside6_version: str = ""
    widgets_ok: bool = False
    vcruntime_ok: bool = True
    vcruntime_detail: str = ""
    config_ok: bool = True
    config_detail: str = ""
    duration_ms: float = 0.0
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def is_all_passed(self) -> bool:
        return (
            self.python_ok
            and self.pyside6_ok
            and self.widgets_ok
            and self.vcruntime_ok
            and self.config_ok
        )


def check_python_version() -> tuple[bool, str]:
    """检查 Python 版本是否满足 >= 3.10 门禁"""
    v = sys.version_info
    v_str = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 10):
        return True, v_str
    return False, v_str


def check_vcruntime() -> tuple[bool, str]:
    """在 Windows 上检查 Visual C++ 2015-2022 运行库是否存在"""
    if sys.platform != "win32":
        return True, "非 Windows 系统无需检查 VC++ DLL"

    required_dlls = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"]
    missing = []
    for dll in required_dlls:
        try:
            ctypes.WinDLL(dll)
        except Exception:
            missing.append(dll)

    if missing:
        detail = f"缺失关键运行库 DLL: {', '.join(missing)}"
        return False, detail
    return True, "VC++ 2015-2022 运行库完整"


def check_pyside6() -> tuple[bool, str, bool, str]:
    """检查 PySide6 与 PySide6.QtWidgets"""
    pyside_ver = ""
    pyside_ok = False
    widgets_ok = False
    widgets_err = ""

    try:
        import PySide6
        pyside_ver = getattr(PySide6, "__version__", "已安装")
        pyside_ok = True
    except Exception as e:
        return False, "", False, str(e)

    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401
        from PySide6.QtCore import Qt  # noqa: F401
        widgets_ok = True
    except Exception as e:
        widgets_err = str(e)

    return pyside_ok, pyside_ver, widgets_ok, widgets_err


def check_config(config_path: Path | None = None) -> tuple[bool, str]:
    """检查 ky_config.json 是否存在及格式是否有效"""
    target = config_path or (ROOT / "ky_config.json")
    if not target.exists():
        return True, "配置文件尚未生成（将自动进入新手引导向导）"

    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False, "ky_config.json 格式错误：根层级必须为 JSON 对象而非列表"
        return True, f"配置文件有效 (包含 {len(data)} 个顶层配置项)"
    except json.JSONDecodeError as e:
        return False, f"ky_config.json JSON 语法损坏: {e}"
    except Exception as e:
        return False, f"无法读取配置文件: {e}"


def run_preflight(verbose: bool = True) -> PreflightStatus:
    """
    运行全套前置诊断检查，严格限制在 200ms 以内完成。
    """
    t0 = time.perf_counter()
    status = PreflightStatus()

    # 1. Python 版本
    status.python_ok, status.python_version = check_python_version()
    if not status.python_ok:
        status.issues.append(f"当前 Python 版本为 {status.python_version}，低于最低要求 3.10")
        status.suggestions.append("请升级或安装 Python 3.10+：https://www.python.org/downloads/")

    # 2. VC++ 运行库
    status.vcruntime_ok, status.vcruntime_detail = check_vcruntime()
    if not status.vcruntime_ok:
        status.issues.append(status.vcruntime_detail)
        status.suggestions.append("请下载并安装微软 Visual C++ 2015-2022 运行库: https://aka.ms/vs/17/release/vc_redist.x64.exe")

    # 3. PySide6
    status.pyside6_ok, status.pyside6_version, status.widgets_ok, widgets_err = check_pyside6()
    if not status.pyside6_ok:
        status.issues.append("未安装 PySide6 图形库")
        status.suggestions.append("运行: pip install PySide6 -i https://pypi.tuna.tsinghua.edu.cn/simple")
    elif not status.widgets_ok:
        status.issues.append(f"PySide6.QtWidgets 模块加载失败: {widgets_err}")
        if "DLL load failed" in widgets_err:
            status.suggestions.append("DLL 加载失败，通常因缺少 VC++ 运行库导致，请安装 vc_redist.x64.exe")

    # 4. 配置文件
    status.config_ok, status.config_detail = check_config()
    if not status.config_ok:
        status.issues.append(status.config_detail)
        status.suggestions.append("可重命名或删除损坏的 ky_config.json，软件启动将自动重新生成")

    status.duration_ms = (time.perf_counter() - t0) * 1000.0

    if verbose:
        print(colored("┌──────────────────────────────────────────────────────────┐", Style.CYAN))
        print(colored("│  考研学习链 · 客户端启动前置诊断 (Preflight Check)      │", Style.BOLD + Style.CYAN))
        print(colored("└──────────────────────────────────────────────────────────┘", Style.CYAN))

        py_tag = colored("[√ 通过]", Style.GREEN) if status.python_ok else colored("[× 异常]", Style.RED)
        print(f"  {py_tag} Python 运行时: {status.python_version} ({sys.executable})")

        vc_tag = colored("[√ 通过]", Style.GREEN) if status.vcruntime_ok else colored("[× 缺失]", Style.RED)
        print(f"  {vc_tag} VC++ 基础运行库: {status.vcruntime_detail}")

        qt_tag = colored("[√ 通过]", Style.GREEN) if (status.pyside6_ok and status.widgets_ok) else colored("[× 异常]", Style.RED)
        qt_desc = f"PySide6 {status.pyside6_version}" if status.pyside6_ok else "未安装"
        print(f"  {qt_tag} Qt 渲染环境: {qt_desc}")

        cfg_tag = colored("[√ 通过]", Style.GREEN) if status.config_ok else colored("[! 警告]", Style.YELLOW)
        print(f"  {cfg_tag} 学员档案配置: {status.config_detail}")

        time_tag = colored(f"{status.duration_ms:.1f}ms", Style.GREEN if status.duration_ms < 200 else Style.YELLOW)
        print(f"  [*] 预检耗时: {time_tag} (性能基准: < 200ms)\n")

    return status


def _pyside6_importable() -> bool:
    """重新 import 校验 PySide6 是否**真的**可用（而不只是 pip 返回 0）。

    [P10 修复·自愈假成功] `pip install` 的退出码只代表"命令执行成功"，不代表依赖
    真的可导入：当 PySide6 被屏蔽/装坏时，pip 会因 "Requirement already satisfied"
    直接返回 0。旧代码据此打印"[√] PySide6 安装成功！"，紧接着预检依旧失败并以
    退出码 2 结束 —— 用户被"假成功"误导，且 logs/gui_crash.log 也不生成。

    此处在报"安装成功"之前做一次真实的 import 校验：先清掉可能缓存的失败条目，
    再重新导入 PySide6 / QtWidgets / QtCore（与 check_pyside6 同一套契约）。
    """
    import importlib

    # 只清理"导入失败"留下的 None 哨兵，避免打扰进程内已正常加载的 Qt 状态
    for mod in ("PySide6", "PySide6.QtWidgets", "PySide6.QtCore"):
        if sys.modules.get(mod, True) is None:
            sys.modules.pop(mod, None)
    importlib.invalidate_caches()
    try:
        import PySide6  # noqa: F401
        from PySide6.QtWidgets import QApplication  # noqa: F401
        from PySide6.QtCore import Qt  # noqa: F401
        return True
    except Exception:
        return False


def self_heal_pyside6() -> bool:
    """当检测到 PySide6 缺失时，通过国内镜像源自愈安装"""
    print(colored("[*] 检测到缺失 PySide6 依赖，启动自动修复自愈流程...", Style.YELLOW))

    mirrors = [
        ("清华大学开源软件镜像站", "https://pypi.tuna.tsinghua.edu.cn/simple", "pypi.tuna.tsinghua.edu.cn"),
        ("阿里云开源镜像站", "https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
    ]

    for name, mirror_url, host in mirrors:
        print(colored(f"[*] 正在尝试通过 [{name}] 安装 PySide6...", Style.CYAN))
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "PySide6",
            "-i",
            mirror_url,
            "--trusted-host",
            host,
        ]
        try:
            ret = subprocess.run(cmd, cwd=str(ROOT), text=True)
            if ret.returncode == 0:
                # [P10 修复] 必须以"能否真正 import"为准，而非 pip 的退出码
                if _pyside6_importable():
                    print(colored("[√] PySide6 安装成功！", Style.GREEN))
                    return True
                print(colored(
                    f"[!] {name} 的 pip 命令返回成功，但重新导入 PySide6 仍然失败，"
                    f"判定为未生效，继续尝试下一个候选源...", Style.YELLOW))
        except Exception as e:
            print(colored(f"[!] 镜像源安装失败 ({e})，尝试下一个候选...", Style.YELLOW))

    # 官方 PyPI 兜底
    print(colored("[*] 正在通过官方 PyPI 兜底安装...", Style.CYAN))
    try:
        ret = subprocess.run([sys.executable, "-m", "pip", "install", "PySide6"], cwd=str(ROOT), text=True)
        if ret.returncode == 0:
            if _pyside6_importable():
                print(colored("[√] PySide6 安装成功！", Style.GREEN))
                return True
            print(colored(
                "[×] 官方源 pip 命令返回成功，但重新导入 PySide6 仍然失败："
                "依赖并未真正可用，请手动检查安装环境。", Style.RED))
    except Exception as e:
        print(colored(f"[×] 官方安装失败: {e}", Style.RED))

    return False


def write_crash_report(
    stdout_text: str,
    stderr_text: str,
    exit_code: int,
    custom_title: str = "GUI 客户端启动异常闪退",
) -> Path:
    """格式化并落盘崩溃排查报告至 logs/gui_crash.log"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOGS_DIR / "gui_crash.log"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"

    pyside_ver = "未就绪"
    qt_plugins_dir = "未知"
    try:
        import PySide6
        pyside_ver = getattr(PySide6, "__version__", "已安装")
        qt_plugins_dir = str(Path(PySide6.__file__).parent / "plugins" / "platforms")
    except Exception:
        pass

    clean_qt_plugin = os.environ.get("QT_PLUGIN_PATH", "[已安全清洗/未设置]")

    # 智能诊断建议
    advice_list = []
    combined_err = (stderr_text + "\n" + stdout_text).lower()
    if "vcruntime" in combined_err or "msvcp" in combined_err or "dll load failed" in combined_err:
        advice_list.append("[×] 检测到底层 C++ 运行库缺失。")
        advice_list.append("    请下载并安装微软官方 Visual C++ 2015-2022 运行库:")
        advice_list.append("    https://aka.ms/vs/17/release/vc_redist.x64.exe")
    if "cannot mix incompatible qt library" in combined_err or "qt_plugin_path" in combined_err:
        advice_list.append("[×] 检测到系统中存在旧版 Qt5 或 Anaconda 环境变量冲突。")
        advice_list.append("    启动器已自动尝试清洗 QT_PLUGIN_PATH，请确认未在系统变量强行注入 Qt5 路径。")
    if "could not find the qt platform plugin" in combined_err or "qwindows" in combined_err:
        advice_list.append("[×] 检测到 Qt 平台插件加载异常。")
        advice_list.append("    请尝试在终端执行: pip install --force-reinstall PySide6")
        advice_list.append("    或使用【调试模式启动GUI.bat】查看插件搜寻过程。")
    if "opengl" in combined_err or "qwindowseglstaticcontext" in combined_err:
        advice_list.append("[×] 检测到显卡驱动 OpenGL 上下文创建异常。")
        advice_list.append("    请使用【调试模式启动GUI.bat --software-opengl】以纯软件渲染模式启动。")
    if not advice_list:
        advice_list.append("[*] 请双击运行【调试模式启动GUI.bat】获取完整交互与报错日志。")
        advice_list.append("[*] 可将此文件 logs/gui_crash.log 发送给开发团队协助定位。")

    content = [
        "=" * 80,
        f"考研学习链 (Kaoyan AI Study Chain) · 桌面客户端崩溃诊断报告",
        "=" * 80,
        f"生成时间: {now_str}",
        f"事件概要: {custom_title} (进程退出码: {exit_code})",
        f"操作系统: {os_info}",
        f"Python 解释器: {sys.executable}",
        f"Python 详细版本: {sys.version.replace(chr(10), ' ')}",
        f"PySide6 版本: {pyside_ver}",
        f"Qt 平台目录: {qt_plugins_dir}",
        f"工作区根路径: {ROOT}",
        "",
        "环境变量快照:",
        f"  QT_PLUGIN_PATH = {clean_qt_plugin}",
        f"  QT_QPA_PLATFORM = {os.environ.get('QT_QPA_PLATFORM', 'default')}",
        "",
        "标准错误与异常输出 (stderr):",
        stderr_text.strip() if stderr_text.strip() else "(无 stderr 输出)",
        "",
        "标准输出摘要 (stdout):",
        stdout_text.strip() if stdout_text.strip() else "(无 stdout 输出)",
        "",
        "智能处方与解决指引:",
    ]
    content.extend(f"  {line}" for line in advice_list)
    content.append("=" * 80 + "\n")

    with open(report_path, "a", encoding="utf-8", errors="replace") as f:
        f.write("\n".join(content))

    return report_path


def get_gui_executable() -> str:
    """获取最适合拉起 GUI 的 Python 解释器路径"""
    py_dir = Path(sys.executable).parent
    pythonw = py_dir / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


_last_process: subprocess.Popen | None = None


def launch_watchdog(forwarded_args: list[str] | None = None, timeout: float = 2.5) -> int:
    """
    2.5 秒存活看门狗 (Liveness Guard) 核心机制。
    拉起 GUI 子进程并阻塞监听 2.5 秒：
    - 若 2.5s 内非零退出，捕获全部 stdout/stderr，记录 logs/gui_crash.log，并返回 4 (触发批处理 pause)
    - 若 2.5s 后仍在稳定运行，认定进入 Qt 主循环，打印成功信息并优雅退出
    """
    global _last_process
    gui_script = ROOT / "tools" / "ky_gui.py"
    if not gui_script.exists():
        print(colored(f"[×] 致命错误: 未找到 GUI 入口文件 {gui_script}", Style.RED))
        return 1

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    startup_log_path = LOGS_DIR / "gui_startup.log"

    gui_python = get_gui_executable()
    cmd = [gui_python, str(gui_script)]
    if forwarded_args:
        cmd.extend(forwarded_args)

    print(colored("[*] 正在拉起考研学习链桌面图形界面...", Style.CYAN))
    print(colored(f"[*] 解释器: {gui_python}", Style.GRAY))
    print(colored("[*] 启动守护看门狗激活中 (2.5 秒存活检测)...", Style.GRAY))

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

    # 将子进程 stdout/stderr 导入 gui_startup.log
    with open(startup_log_path, "w", encoding="utf-8", errors="replace") as log_f:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT),
                creationflags=creation_flags,
            )
            _last_process = proc
        except Exception as e:
            tb = traceback.format_exc()
            write_crash_report("", f"无法拉起 GUI 进程: {e}\n{tb}", 1)
            print(colored(f"\n[×] 拉起 GUI 进程失败: {e}", Style.RED))
            return 1

    # 监听 2.5 秒存活情况
    start_time = time.time()
    while time.time() - start_time < timeout:
        ret = proc.poll()
        if ret is not None:
            # 进程在 2.5 秒内提前退出
            output_content = ""
            try:
                with open(startup_log_path, "r", encoding="utf-8", errors="replace") as rf:
                    output_content = rf.read()
            except Exception:
                pass

            if ret == 0:
                print(colored("  [√] GUI 客户端已正常退出 (退出码: 0)", Style.GREEN))
                return 0

            # 闪退异常！
            print(colored(f"\n[×] 检测到考研学习链 GUI 启动异常闪退 (退出码: {ret})", Style.BOLD + Style.RED))
            print(colored("=" * 65, Style.RED))
            if output_content.strip():
                print(output_content.strip())
            else:
                print("(子进程无标准输出/错误输出，请检查 VC++ 运行库或底层 DLL)")
            print(colored("=" * 65, Style.RED))

            report_file = write_crash_report("", output_content, ret, "GUI 启动期 2.5 秒内异常闪退")
            print(colored(f"\n[*] 详细诊断报告已保存至: {report_file}", Style.YELLOW))
            print(colored("[*] 您也可以双击【调试模式启动GUI.bat】以交互调试模式启动以观察详细日志。\n", Style.CYAN))
            return 4

        time.sleep(0.05)

    # 存活超 2.5 秒，确认 GUI 窗口已稳定加载
    print(colored("  [√] GUI 窗口已成功加载呈现 (存活已达 2.5 秒)，启动守护正常退出。", Style.GREEN))
    return 0


def launch_debug(forwarded_args: list[str] | None = None) -> int:
    """
    调试模式 (Debug Mode)：
    - 强制前台控制台执行
    - 设置 QT_DEBUG_PLUGINS=1，打印所有插件加载追踪
    - 设置 PYTHONFAULTHANDLER=1
    - 实时流式输出到控制台，同时镜像写入 logs/gui_debug.log
    """
    gui_script = ROOT / "tools" / "ky_gui.py"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    debug_log_path = LOGS_DIR / "gui_debug.log"

    os.environ["QT_DEBUG_PLUGINS"] = "1"
    os.environ["PYTHONFAULTHANDLER"] = "1"

    cmd = [sys.executable, str(gui_script)]
    if forwarded_args:
        cmd.extend(forwarded_args)

    print(colored("============================================================", Style.CYAN))
    print(colored("  🔍 考研学习链 · 调试模式 (Debug Console Active)", Style.BOLD + Style.CYAN))
    print(colored("  [*] QT_DEBUG_PLUGINS=1 已注入", Style.GRAY))
    print(colored("  [*] PYTHONFAULTHANDLER=1 已激活", Style.GRAY))
    print(colored(f"  [*] 实时日志镜像同步至: {debug_log_path}", Style.GRAY))
    print(colored("============================================================\n", Style.CYAN))

    with open(debug_log_path, "w", encoding="utf-8", errors="replace") as log_f:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_f.write(f"--- Kaoyan Study Chain GUI Debug Session Started ({now_str}) ---\n")
        log_f.flush()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        try:
            if proc.stdout:
                for line in proc.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    log_f.write(line)
                    log_f.flush()
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            print(colored("\n[!] 收到中断信号，正在退出调试会话...", Style.YELLOW))
        finally:
            if proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

        exit_code = proc.returncode or 0
        log_f.write(f"\n--- Process Exited with code {exit_code} ---\n")

    print(colored(f"\n[i] 客户端进程已退出 (退出码: {exit_code})", Style.GREEN if exit_code == 0 else Style.YELLOW))
    print(colored(f"[*] 调试日志已保存在: {debug_log_path}", Style.GRAY))
    return exit_code


def launch_console(forwarded_args: list[str] | None = None) -> int:
    """前台控制台模式，直接运行 ky_gui.py 并等待其结束"""
    gui_script = ROOT / "tools" / "ky_gui.py"
    cmd = [sys.executable, str(gui_script)]
    if forwarded_args:
        cmd.extend(forwarded_args)

    ret = subprocess.run(cmd, cwd=str(ROOT))
    return ret.returncode


def run_doctor() -> int:
    """运行全系统健康诊断 ky doctor"""
    doctor_path = ROOT / "tools" / "doctor.py"
    if not doctor_path.exists():
        print(colored(f"[×] 未找到体检工具: {doctor_path}", Style.RED))
        return 1

    print(colored("[*] 正在执行全系统健康体检...", Style.CYAN))
    ret = subprocess.run([sys.executable, str(doctor_path)], cwd=str(ROOT))
    return ret.returncode


def main(argv: list[str] | None = None) -> int:
    """启动器主调度中枢"""
    # 彻底清洗外部可能污染的 QT_PLUGIN_PATH
    if "QT_PLUGIN_PATH" in os.environ:
        os.environ.pop("QT_PLUGIN_PATH")

    parser = argparse.ArgumentParser(
        description="考研学习链 · 桌面客户端智能启动与诊断中枢",
        add_help=True,
    )
    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="开启调试控制台模式：QT_DEBUG_PLUGINS=1、详细 Traceback 并流式输出至 logs/gui_debug.log",
    )
    parser.add_argument(
        "--console", "-c",
        action="store_true",
        help="使用前台控制台启动并保留交互流",
    )
    parser.add_argument(
        "--software-opengl",
        action="store_true",
        help="强制启用纯软件渲染模式 (解决特定显卡驱动崩溃、虚拟机黑屏)",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="仅执行启动前置诊断检查并返回退出码 (0:正常, 1:Python环境异常, 2:PySide6缺失, 3:VC++缺失, 5:配置损坏)",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="调用 tools/doctor.py 执行全链路系统体检",
    )

    args, unknown_args = parser.parse_known_args(argv)

    # 1. 软件 OpenGL 渲染开关
    if args.software_opengl:
        print(colored("[*] 已强制注入软件 OpenGL 渲染环境变量 (QT_OPENGL=software)", Style.YELLOW))
        os.environ["QT_QUICK_BACKEND"] = "software"
        os.environ["QT_OPENGL"] = "software"
        os.environ["QSG_RHI_BACKEND"] = "software"
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"

    # 2. 全系统体检模式
    if args.doctor:
        return run_doctor()

    # 3. 前置诊断检查
    status = run_preflight(verbose=True)

    if args.preflight_only:
        if not status.python_ok:
            return 1
        if not status.vcruntime_ok:
            return 3
        if not (status.pyside6_ok and status.widgets_ok):
            return 2
        if not status.config_ok:
            return 5
        return 0

    # 4. 依赖项自愈
    if not (status.pyside6_ok and status.widgets_ok):
        healed = self_heal_pyside6()
        if not healed:
            write_crash_report("", "PySide6 依赖库缺失且自动安装失败", 2)
            print(colored("\n[×] 自动修复失败，请按上述指引手动安装 PySide6。", Style.RED))
            return 2
        # 重新预检
        status = run_preflight(verbose=False)
        if not (status.pyside6_ok and status.widgets_ok):
            return 2

    if not status.python_ok:
        write_crash_report("", f"Python 版本不满足要求: {status.python_version}", 1)
        return 1

    if not status.vcruntime_ok:
        write_crash_report("", f"VC++ 运行库缺失: {status.vcruntime_detail}", 3)
        print(colored("\n[×] 缺少 Visual C++ 运行库，请根据上述提示下载安装后再启动。", Style.RED))
        return 3

    # 5. 分支启动调度
    if args.debug:
        return launch_debug(unknown_args)
    if args.console:
        return launch_console(unknown_args)

    return launch_watchdog(unknown_args, timeout=2.5)


if __name__ == "__main__":
    sys.exit(main())
