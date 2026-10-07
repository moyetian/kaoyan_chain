@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- locate python (>=3.10; WindowsApps store stub excluded) ----
rem [stub fix] Previous logic hardcoded a developer-machine path and fell
rem back to bare `py` without probing; `where python` alone can also match
rem the WindowsApps store stub, which silently ignores script arguments.
rem Probe for real: `py -3` first, then PATH python with version check
rem (same rule as GUI.bat).
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

echo [X] No usable Python 3.10+ found (WindowsApps store stub excluded).
echo     Install from https://www.python.org/downloads/ and tick "Add to PATH".
pause >nul
exit /b 1

:run
echo ============================================
echo   Kaoyan Dashboard - Local Build
echo ============================================
echo.

echo [1/3] Building...
rem [W13] Local entry must build in full mode (KY_SNAPSHOT_OPT_IN=0)
set KY_SNAPSHOT_OPT_IN=0
rem [R2] Full-mode output goes to the untracked docs/.local
set KY_DASHBOARD_OUTPUT_DIR=docs/.local
%KY_PY% build.py
if errorlevel 1 goto :err

echo [2/2] Local build complete. No Git commit or push was performed.
echo Local full dashboard: docs/.local/index.html
echo Phone: run `python -m http.server 8080 --directory docs` at repo root,
echo        then open http://YOUR-PC-IP:8080/.local/ on your phone.
echo To sync explicitly, run: python tools/update_dashboard.py --push

echo.
echo Done. The local dashboard is ready.
echo Press any key to close.
pause >nul
exit /b 0

:err
echo.
echo [X] Build failed. Check the error above.
pause >nul
exit /b 1
