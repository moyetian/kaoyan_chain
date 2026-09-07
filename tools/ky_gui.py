#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考研学习链 · GUI 可视化操作端启动入口 (ky_gui.py)
"""

import sys
from pathlib import Path

# 确保项目根目录与 tools 目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont
    except ImportError:
        print("[!] 未检测到 PySide6 依赖。请运行以下命令进行安装：")
        print("    pip install PySide6")
        sys.exit(1)

    try:
        from gui.main_window import MainWindow
    except ImportError as e:
        print(f"[!] GUI 组件载入异常: {e}")
        sys.exit(1)

    # 高 DPI 支持
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    app = QApplication(sys.argv)
    app.setApplicationName("考研学习链 GUI")
    app.setOrganizationName("KaoyanStudyChain")

    # 全局字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    # 加载深色主题
    theme_path = TOOLS / "gui" / "theme" / "dark.qss"
    if theme_path.exists():
        try:
            app.setStyleSheet(theme_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
