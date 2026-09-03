@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py
if exist "D:\Python\Python312\python.exe" set PY=D:\Python\Python312\python.exe

"%PY%" "%~dp0tools\ky_cli.py" %*
