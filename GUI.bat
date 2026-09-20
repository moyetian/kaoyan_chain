@chcp 936 >nul 2>nul
@echo off
rem ========================================================
rem   [R2-C3] 本文件以 GBK(CP936) 落盘，请勿另存为 UTF-8。
rem   cmd.exe 按「控制台当前代码页」逐行读取 .bat。默认 zh-CN 控制台是 CP936，
rem   而原文件是 UTF-8：中文被误解码后整行当命令执行（报「不是内部或外部命令」），
rem   并连带吞掉后续行，导致关键提示全不打印（全新控制台实测 10/10 轮必现）。
rem   改为 GBK 落盘 + 首行 chcp 936：读文件与打印输出同码页，彻底消除失步。
rem   （UTF-8 带 BOM 实测更差：cmd 把 BOM 当命令字符，连 @echo off 都失效；
rem     仅靠 chcp 65001 也无法修复，实测 6/6 轮仍报错。）
rem ========================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem ========================================================
rem   考研学习链 (Kaoyan Study Chain) · 智能桌面客户端启动器
rem ========================================================

rem 环境净化：清除外部可能注入的 Qt5 / Anaconda 插件路径变量，防止 C++ abort 崩溃
set "QT_PLUGIN_PATH="

set "PY="

rem 1. 本地虚拟环境 (.venv)
set "CANDIDATE=%~dp0.venv\Scripts\python.exe"
if exist "!CANDIDATE!" call :check_candidate "!CANDIDATE!"
if defined PY goto :launch

rem 2. 本地虚拟环境 (venv)
set "CANDIDATE=%~dp0venv\Scripts\python.exe"
if exist "!CANDIDATE!" call :check_candidate "!CANDIDATE!"
if defined PY goto :launch

rem 3. 环境变量已激活的虚拟环境 (%VIRTUAL_ENV%)
if defined VIRTUAL_ENV (
    set "CANDIDATE=%VIRTUAL_ENV%\Scripts\python.exe"
    if exist "!CANDIDATE!" call :check_candidate "!CANDIDATE!"
    if defined PY goto :launch
)

rem 4. Conda 环境 (%CONDA_PREFIX%)
if defined CONDA_PREFIX (
    set "CANDIDATE=%CONDA_PREFIX%\python.exe"
    if exist "!CANDIDATE!" call :check_candidate "!CANDIDATE!"
    if defined PY goto :launch
)

rem 5. py -3 启动器
py -3 -c "import sys; sys.exit(0 if sys.hexversion >= 0x030A0000 else 1)" >nul 2>nul
if !errorlevel! equ 0 (
    set "PY=py -3"
    goto :launch
)

rem 6. 系统 PATH 中的 python (严格排除 WindowsApps 0 字节假替身)
for /f "delims=" %%I in ('where python 2^>nul') do (
    set "CUR_P=%%I"
    echo "!CUR_P!" | findstr /i "WindowsApps" >nul
    if !errorlevel! neq 0 (
        call :check_candidate "!CUR_P!"
        if defined PY goto :launch
    )
)

rem 7. 常见独立安装路径 (支持 Python 3.10 至 3.14)
for %%D in (
    "D:\Python\Python314\python.exe"
    "D:\Python\Python313\python.exe"
    "D:\Python\Python312\python.exe"
    "D:\Python\Python311\python.exe"
    "D:\Python\Python310\python.exe"
    "%LocalAppData%\Programs\Python\Python314\python.exe"
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Program Files\Python314\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Program Files\Python310\python.exe"
    "C:\Python314\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist "%%~D" (
        call :check_candidate "%%~D"
        if defined PY goto :launch
    )
)

rem 注意：本脚本启用了 enabledelayedexpansion，`!` 会被解释器吞掉，
rem 因此字面量感叹号必须写成 `^^!`（经 cmd 实测，顶层与括号块内均有效）。
echo.
echo ========================================================
echo   [^^!] 未检测到可用的 Python 3.10+ 运行环境
echo ========================================================
echo 请确认已安装 Python 并在安装时勾选 Add Python to PATH。
echo 官方下载地址: https://www.python.org/downloads/
echo.
pause
exit /b 1

:check_candidate
set "PROBE=%~1"
"%PROBE%" -c "import sys; sys.exit(0 if sys.hexversion >= 0x030A0000 else 1)" >nul 2>nul
if !errorlevel! equ 0 (
    set "PY=%PROBE%"
)
exit /b 0

:launch
echo [*] 已就绪 Python 运行时: %PY%

rem 检查 PySide6 环境自检与自愈 (核心图形中枢: tools\ky_gui.py)
%PY% -c "import PySide6" >nul 2>nul
if !errorlevel! neq 0 (
    echo [*] 正在为考研学习链安装图形界面依赖 PySide6...
    %PY% -m pip install -q PySide6
    if !errorlevel! neq 0 (
        echo [^^!] 自动安装 PySide6 失败，请在终端手动运行: pip install PySide6
    )
)

echo [*] 启动图形界面中枢 [tools\gui_launcher.py -^> tools\ky_gui.py]...

%PY% "%~dp0tools\gui_launcher.py" %*
set "LAUNCHER_EXIT=!errorlevel!"

if !LAUNCHER_EXIT! neq 0 (
    echo.
    echo ========================================================
    echo   [*] GUI 客户端异常退出 [退出码: !LAUNCHER_EXIT!]
    rem [P11 修复] 此前无论日志是否真的生成都固定宣称"已保存至 logs\gui_crash.log"，
    rem 而启动器只会在部分异常分支写该文件，用户按提示找不到文件。
    rem 现仅当文件真实存在时才报路径，否则给出可行动的替代指引。
    if exist "%~dp0logs\gui_crash.log" (
        echo   [*] 详细诊断日志已保存至: logs\gui_crash.log
    ) else (
        echo   [^^!] 未生成 logs\gui_crash.log，可双击运行【调试模式启动GUI.bat】查看实时报错
    )
    echo   [*] 若需排查底层故障，可双击运行: 调试模式启动GUI.bat
    echo ========================================================
    echo.
    pause
    exit /b !LAUNCHER_EXIT!
)

exit /b 0
