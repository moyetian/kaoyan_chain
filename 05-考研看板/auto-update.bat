@echo off
rem ============================================
rem  Kaoyan Dashboard - silent auto update
rem  For AI/scheduler invocation. No pause, no prompt.
rem  Exit 0 = success or nothing-to-do, 1 = build failed
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

git add -A
git diff --cached --quiet
if not errorlevel 1 (
  echo [=] no changes, nothing to push
  exit /b 0
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set TS=%%i
git commit -m "auto %TS%"
git push
if errorlevel 1 (
  echo [!] push failed - remote not configured or offline
  exit /b 0
)

echo [OK] dashboard updated and pushed
exit /b 0
