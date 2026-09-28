@echo off
setlocal
title Open Results Folder - ScanReader 0.8.0
cd /d "%~dp0..\.."
if exist "output" (
    start "" "output"
) else if exist "Результаты" (
    start "" "Результаты"
) else (
    echo Папка "output" еще не создана.
    pause
)
