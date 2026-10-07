@echo off
rem ============================================
rem  Kaoyan Dashboard - silent local update
rem  For AI/scheduler invocation. No pause, no prompt.
rem  Exit 0 = success, 1 = build failed
rem ============================================
chcp 65001 >nul
cd /d "%~dp0"

rem ---- locate python (>=3.10; WindowsApps store stub excluded) ----
rem [stub fix] `where py || set PY=python` had no stub guard: on machines
rem without the py launcher the WindowsApps store stub would be selected.
rem Probe for real: `py -3` first, then PATH python (WindowsApps excluded).
py -3 -c "import sys; sys.exit(0 if sys.hexversion >= 0x030A0000 else 1)" >nul 2>nul
if not errorlevel 1 set "KY_PY=py -3"
if defined KY_PY goto :run

for /f "delims=" %%I in ('where python 2^>nul') do (
  if not defined KY_PY (
    echo "%%I" | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
      "%%I" -c "import sys; sys.exit(0 if sys.hexversion >= 0x030A0000 else 1)" >nul 2>nul
      if not errorlevel 1 set KY_PY="%%I"
    )
  )
)
if defined KY_PY goto :run

echo [X] no usable Python 3.10+ found (WindowsApps store stub excluded)
exit /b 1

:run
rem [R2] Silent local update: full mode + untracked output dir (docs/.local)
set KY_SNAPSHOT_OPT_IN=0
set KY_DASHBOARD_OUTPUT_DIR=docs/.local

%KY_PY% build.py
if errorlevel 1 (
  echo [X] build failed
  exit /b 1
)

echo [OK] dashboard updated locally; no Git commit or push was performed
echo To sync explicitly, run: python tools/update_dashboard.py --push
exit /b 0
