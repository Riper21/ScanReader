@echo off
setlocal
title VLM Diagnostics and Token Tracker - ScanReader 0.8.0
cd /d "%~dp0..\.."

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" launcher.py --diagnostics
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" launcher.py --diagnostics
) else (
    py -3 launcher.py --diagnostics
)
pause
