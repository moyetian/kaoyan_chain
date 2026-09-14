@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- locate python ----
set PY=D:\Python\Python312\python.exe
if not exist "%PY%" set PY=py

echo ============================================
echo   Kaoyan Dashboard - Local Build
echo ============================================
echo.

echo [1/3] Building...
"%PY%" build.py
if errorlevel 1 goto :err

echo [2/2] Local build complete. No Git commit or push was performed.
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
