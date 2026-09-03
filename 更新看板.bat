@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- locate python ----
set PY=D:\Python\Python312\python.exe
if not exist "%PY%" set PY=python
if not exist "%PY%" set PY=py

echo ========================================================
echo   考研学习链 (Kaoyan Study Chain) - 一键构建与推送到 GitHub
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
echo [2/3] 正在提交本地更新...
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set TS=%%i
git add -A
git commit -m "study-chain update %TS%"

echo.
echo [3/3] 正在推送到 GitHub 远程仓库...
git push
if errorlevel 1 (
  echo.
  echo [!] 推送未成功。若首次使用，请先完成远程仓库绑定：
  echo     git remote add origin <你的GitHub仓库地址>
  echo     git branch -M main
  echo     git push -u origin main
) else (
  echo.
  echo [OK] 学习项目已成功同步至 GitHub！
)

timeout /t 5