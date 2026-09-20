@echo off
setlocal
chcp 936 >nul 2>nul
rem [R2-C3] 本文件以 GBK(CP936) 落盘，请勿另存为 UTF-8（详见 GUI.bat 顶部说明）
title 考研学习链 · 调试模式 (Debug Console)
cd /d "%~dp0"

echo ========================================================
echo   考研学习链 · 桌面客户端调试控制台
echo ========================================================
echo [*] 正在以调试模式启动 GUI (QT_DEBUG_PLUGINS=1)...
echo [*] 调试日志将同步输出到控制台及 logs\gui_debug.log
echo.

call "%~dp0GUI.bat" --debug %*

echo.
echo ========================================================
echo   [i] 调试会话已结束。按任意键关闭此控制台窗口...
echo ========================================================
pause >nul
