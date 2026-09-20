@echo off
setlocal
chcp 936 >nul 2>nul
rem [R2-C3] 本文件以 GBK(CP936) 落盘，请勿另存为 UTF-8（详见 GUI.bat 顶部说明）
cd /d "%~dp0"

rem 考研学习链 · 桌面客户端双击启动入口
call "%~dp0GUI.bat" %*
