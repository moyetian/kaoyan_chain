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

# Windows 控制台安全编码
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

    # 全局字体：字号随主题 token（font-scale）走，且字体族不再硬编码
    # "Microsoft YaHei"（Linux / macOS 上该字体并不存在）。
    try:
        from gui import theme_apply
    except ImportError:  # pragma: no cover
        from tools.gui import theme_apply  # type: ignore

    theme = theme_apply.resolve_theme(ROOT)
    font = QFont()
    font.setFamilies([f.strip().strip('"') for f in
                      str(theme.get("font-family", "")).split(",") if f.strip()])
    font.setPointSizeF(round(10 * theme.number("font-scale", 1.0), 1))
    app.setFont(font)

    # 主题 QSS 由 token 现场编译（深/浅/护眼绿/樱粉/高对比 皆可）
    theme_apply.apply_theme(app, theme)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
