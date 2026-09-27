@echo off
setlocal
title Export Results to Excel - ScanReader 0.8.0
cd /d "%~dp0..\.."

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" launcher.py --excel
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" launcher.py --excel
) else (
    py -3 launcher.py --excel
)
pause
