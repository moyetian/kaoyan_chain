@echo off
rem ============================================
rem  Kaoyan Dashboard - silent local update
rem  For AI/scheduler invocation. No pause, no prompt.
rem  Exit 0 = success, 1 = build failed
rem ============================================
chcp 65001 >nul
cd /d "%~dp0"

set PY=D:\Python\Python312\python.exe
if not exist "%PY%" set PY=py

"%PY%" build.py
if errorlevel 1 (
  echo [X] build failed
  exit /b 1
)

echo [OK] dashboard updated locally; no Git commit or push was performed
echo To sync explicitly, run: python tools/update_dashboard.py --push
exit /b 0
