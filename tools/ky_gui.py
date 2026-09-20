#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考研学习链 · GUI 可视化操作端启动入口 (ky_gui.py)
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

# 支持打包冻结环境与普通源码环境路径自适应
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


def show_native_error_box(title: str, message: str) -> None:
    """在 Windows 环境下弹出原生 MessageBoxW 弹窗，防止 pythonw 模式下静默丢弃错误。"""
    if sys.platform == "win32":
        try:
            import ctypes
            # MB_ICONERROR (0x10) | MB_OK (0x0) | MB_SETFOREGROUND (0x10000)
            ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10 | 0x0 | 0x10000)
        except Exception:
            pass
    if sys.stderr and not sys.stderr.closed:
        try:
            print(f"[{title}] {message}", file=sys.stderr)
        except Exception:
            pass


def write_crash_log(title: str, details: str) -> None:
    """将致命崩溃信息落盘至 logs/gui_crash.log。"""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        crash_log = LOGS_DIR / "gui_crash.log"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(crash_log, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n{'=' * 75}\n")
            f.write(f"考研学习链 · 客户端运行崩溃报告 ({now_str})\n")
            f.write(f"事件分类: {title}\n")
            f.write(f"Python 路径: {sys.executable} ({sys.version.split()[0]})\n")
            f.write(f"工作区根目录: {ROOT}\n")
            f.write(f"{'-' * 75}\n")
            f.write(f"{details.strip()}\n")
            f.write(f"{'=' * 75}\n")
    except Exception:
        pass


def global_excepthook(exc_type, exc_value, exc_traceback):
    """全局未捕获异常挂钩，保证任何未处理异常都会被记录并弹出原生对话框。"""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    err_summary = f"{exc_type.__name__}: {exc_value}"
    full_info = f"未捕获的运行时异常:\n{err_summary}\n\nTraceback:\n{tb_str}"

    write_crash_log("未捕获全局异常 (sys.excepthook)", full_info)
    show_native_error_box(
        "考研学习链 · 运行异常",
        f"客户端遇到未捕获异常：\n\n{err_summary}\n\n详细崩溃日志已记录至：\n{LOGS_DIR / 'gui_crash.log'}\n\n可双击【调试模式启动GUI.bat】排查详情。"
    )
    if sys.stderr and not sys.stderr.closed:
        try:
            sys.stderr.write(tb_str)
            sys.stderr.flush()
        except Exception:
            pass


def install_qt_message_handler():
    """安装 Qt 底层日志拦截器，捕获 C++ 引擎与插件级警告/致命错误。"""
    try:
        from PySide6.QtCore import qInstallMessageHandler, QtMsgType

        def qt_msg_handler(mode, context, message):
            msg_str = str(message)
            is_fatal = mode == QtMsgType.QtFatalMsg
            is_critical = mode == QtMsgType.QtCriticalMsg
            is_plugin_issue = (
                "Could not find the Qt platform plugin" in msg_str
                or "Cannot mix incompatible Qt library" in msg_str
                or "DLL load failed" in msg_str
            )

            if is_fatal or is_critical or is_plugin_issue:
                loc = f"{getattr(context, 'file', '')}:{getattr(context, 'line', '')}" if context else "unknown"
                details = f"[QtMsgType: {mode}] Location: {loc}\nMessage: {msg_str}"
                write_crash_log("Qt 底层严重错误 / 插件异常", details)
                if is_fatal or is_plugin_issue:
                    show_native_error_box(
                        "考研学习链 · Qt 平台错误",
                        f"Qt 底层运行时发生严重异常：\n\n{msg_str}\n\n详细排查日志已保存至：\n{LOGS_DIR / 'gui_crash.log'}"
                    )
            elif sys.stderr and not sys.stderr.closed:
                try:
                    sys.stderr.write(f"[Qt] {msg_str}\n")
                except Exception:
                    pass

        qInstallMessageHandler(qt_msg_handler)
    except Exception:
        pass


def main():
    # 安装顶层未捕获异常钩子
    sys.excepthook = global_excepthook

    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont, QIcon
    except ImportError as e:
        err_msg = (
            f"未检测到 PySide6 依赖组件或底层 DLL 载入失败：\n{e}\n\n"
            "请在终端运行：pip install PySide6\n"
            "或运行【调试模式启动GUI.bat】排查详情。"
        )
        write_crash_log("PySide6 依赖载入失败", str(e))
        show_native_error_box("考研学习链 · 依赖缺失", err_msg)
        if sys.stderr and not sys.stderr.closed:
            print(f"[!] {err_msg}", file=sys.stderr)
        sys.exit(1)

    # 挂载 Qt 平台级底层消息拦截器
    install_qt_message_handler()

    try:
        from gui.main_window import MainWindow
    except Exception as e:
        tb = traceback.format_exc()
        write_crash_log("GUI 核心组件载入失败", f"{e}\n\n{tb}")
        show_native_error_box(
            "考研学习链 · 核心组件载入失败",
            f"GUI 核心组件载入异常：\n{e}\n\n详细错误已保存至 logs/gui_crash.log"
        )
        if sys.stderr and not sys.stderr.closed:
            print(f"[!] GUI 组件载入异常: {e}\n{tb}", file=sys.stderr)
        sys.exit(1)

    # 高 DPI 支持
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    # Windows 任务栏应用分组与官方 Logo 贴图绑定
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kaoyan.study.chain.gui.v2")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("考研学习链 GUI")
    app.setOrganizationName("KaoyanStudyChain")

    # 全局挂载官方 Logo
    icon_p = ROOT / "docs" / "assets" / "logo" / "logo.png"
    if not icon_p.exists():
        icon_p = ROOT / "docs" / "assets" / "logo.png"
    if icon_p.exists():
        app.setWindowIcon(QIcon(str(icon_p)))

    # 全局字体与主题应用
    try:
        try:
            from gui import theme_apply
        except ImportError:
            from tools.gui import theme_apply

        theme = theme_apply.resolve_theme(ROOT)
        font = QFont()
        font.setFamilies([f.strip().strip('"') for f in
                          str(theme.get("font-family", "")).split(",") if f.strip()])
        font.setPointSizeF(round(10 * theme.number("font-scale", 1.0), 1))
        app.setFont(font)

        theme_apply.apply_theme(app, theme)
    except Exception as e:
        write_crash_log("主题渲染异常 (已降级默认)", traceback.format_exc())

    try:
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        global_excepthook(*sys.exc_info())
        sys.exit(1)


if __name__ == "__main__":
    main()
