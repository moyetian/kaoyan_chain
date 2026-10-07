@echo off
chcp 936 >nul 2>nul
rem [R2-C3] 本文件以 GBK(CP936) 落盘，请勿另存为 UTF-8（详见 GUI.bat 顶部说明）
cd /d "%~dp0"

rem ---- locate python (>=3.10；排除 WindowsApps 商店替身) ----
rem [stub 修复] 旧写法 `set PY=python & where python || set PY=py` 会把
rem WindowsApps 下的 python.exe 商店替身当成可用解释器（where 命中即算
rem 找到，而替身对脚本参数静默返回、构建必然失败）。改为与 GUI.bat 同款
rem 真探测：py -3 优先；PATH 中的 python 须排除 WindowsApps 且通过版本门禁。
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

echo [!] 未检测到可用的 Python 3.10+ 环境（WindowsApps 商店替身已排除）。
echo     请安装 Python 3.10 或更高版本（勾选 Add Python to PATH）：
echo     https://www.python.org/downloads/
pause
exit /b 1

:run
echo ========================================================
echo   考研学习链 (Kaoyan Study Chain) - 本地构建看板
echo ========================================================
echo.

rem [W13] Local entry must build in full mode (KY_SNAPSHOT_OPT_IN=0)
set KY_SNAPSHOT_OPT_IN=0
rem [R2] Full-mode output goes to the untracked docs/.local
set KY_DASHBOARD_OUTPUT_DIR=docs/.local
echo [1/3] 正在解析四科状态并生成 Web 看板...
%KY_PY% "05-考研看板\build.py"
if errorlevel 1 (
  echo [!] 构建失败，请检查 Python 环境或数据源。
  pause
  exit /b 1
)

echo.
echo [2/2] 本地构建完成，未执行 Git 提交或推送。
echo 本地完整版看板：docs\.local\index.html
echo 手机端浏览：python -m http.server 8080 --directory docs 后，用手机打开 http://电脑IP:8080/.local/
echo 如需同步，请在终端运行：python tools/update_dashboard.py --push

timeout /t 5
