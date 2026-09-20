@echo off
setlocal
chcp 936 >nul 2>nul
rem [R2-C3] 本文件以 GBK(CP936) 落盘，请勿另存为 UTF-8（详见 GUI.bat 顶部说明）
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

py -3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    endlocal
    py -3 "%~dp0tools\ky_cli.py" %*
    exit /b %errorlevel%
)

python -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    endlocal
    python "%~dp0tools\ky_cli.py" %*
    exit /b %errorlevel%
)



echo [!] 未检测到可用的 Python 3.10+ 环境。
echo 请访问 https://www.python.org/downloads/ 安装 Python 3.10 或更高版本。
pause
exit /b 1
