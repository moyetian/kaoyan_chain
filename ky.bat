@echo off
setlocal
chcp 936 >nul 2>nul
rem [R2-C3] 本文件以 GBK(CP936) 落盘，请勿另存为 UTF-8（详见 GUI.bat 顶部说明）
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem ---- locate python (>=3.10；排除 WindowsApps 商店替身) ----
rem [stub 修复] 旧写法用 `python -c "import sys"` 探测兜底解释器，无法识别
rem WindowsApps 下的 python.exe 商店替身（它对任何参数都静默返回 0）：
rem 一旦本机没有 py 启动器，就会把替身当成可用解释器、ky_cli 静默失败。
rem 现改为与 GUI.bat 同款真探测：py -3 优先；PATH 中的 python 须排除
rem WindowsApps 且通过版本门禁。
py -3 -c "import sys; sys.exit(0 if sys.hexversion >= 0x030A0000 else 1)" >nul 2>nul
if not errorlevel 1 set "KY_PY=py -3"
if defined KY_PY goto :launch

for /f "delims=" %%I in ('where python 2^>nul') do (
    if not defined KY_PY (
        echo "%%I" | findstr /i "WindowsApps" >nul
        if errorlevel 1 (
            "%%I" -c "import sys; sys.exit(0 if sys.hexversion >= 0x030A0000 else 1)" >nul 2>nul
            if not errorlevel 1 set KY_PY="%%I"
        )
    )
)
if defined KY_PY goto :launch

echo [!] 未检测到可用的 Python 3.10+ 环境（WindowsApps 商店替身已排除）。
echo 请访问 https://www.python.org/downloads/ 安装 Python 3.10 或更高版本。
pause
exit /b 1

:launch
%KY_PY% "%~dp0tools\ky_cli.py" %*
exit /b %errorlevel%
