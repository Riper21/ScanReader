@echo off
setlocal
title Legal Document Processing - ScanReader 0.8.0
cd /d "%~dp0..\.."

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" launcher.py %*
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" launcher.py %*
) else (
    py -3 launcher.py %*
)
pause
