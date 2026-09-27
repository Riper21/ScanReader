@echo off
setlocal
title Ground Truth Benchmark - ScanReader 0.8.0
cd /d "%~dp0..\.."

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" launcher.py --benchmark
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" launcher.py --benchmark
) else (
    py -3 launcher.py --benchmark
)
pause
