@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- locate python ----
set PY=D:\Python\Python312\python.exe
if not exist "%PY%" set PY=py

echo ============================================
echo   Kaoyan Dashboard - Build ^& Push
echo ============================================
echo.

echo [1/3] Building...
"%PY%" build.py
if errorlevel 1 goto :err

echo.
echo [2/3] Committing...
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set TS=%%i
git add -A
git commit -m "update %TS%"

echo.
echo [3/3] Pushing...
git push
if errorlevel 1 (
  echo.
  echo [!] Push failed. Remote may not be configured yet.
  echo     Run: git remote add origin ^<your-repo-url^>
)

echo.
echo Done. Dashboard will be live in ~1 min.
echo Press any key to close.
pause >nul
exit /b 0

:err
echo.
echo [X] Build failed. Check the error above.
pause >nul
exit /b 1
