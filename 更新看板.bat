@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- locate python ----
set PY=python
where python >nul 2>nul || set PY=py
if exist "D:\Python\Python312\python.exe" set PY=D:\Python\Python312\python.exe

echo ========================================================
echo   考研学习链 (Kaoyan Study Chain) - 本地构建看板
echo ========================================================
echo.

echo [1/3] 正在解析四科状态并生成 Web 看板...
"%PY%" "05-考研看板\build.py"
if errorlevel 1 (
  echo [!] 构建失败，请检查 Python 环境或数据源。
  pause
  exit /b 1
)

echo.
echo [2/2] 本地构建完成，未执行 Git 提交或推送。
echo 如需同步，请在终端运行：python tools/update_dashboard.py --push

timeout /t 5
