@echo off
chcp 65001 >nul
setlocal
title Install Dependencies - Legal Document Platform (ScanReader)
cd /d "%~dp0..\.."

echo ==============================================================================
echo  [УСТАНОВКА] Проверка и установка библиотек Python для AI-системы (ScanReader 0.8.0)
echo ==============================================================================
echo.

set "PY_CMD="
if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PY_CMD=venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PY_CMD=py -3"
    ) else (
        where python >nul 2>nul
        if %ERRORLEVEL% equ 0 (
            set "PY_CMD=python"
        )
    )
)

if "%PY_CMD%"=="" (
    echo [ОШИБКА] Интерпретатор Python не найден в системе!
    echo Установите Python 3.9+ с официального сайта https://www.python.org/
    echo Обязательно отметьте галочку "Add Python to PATH" при установке.
    echo.
    pause
    exit /b 1
)

echo • Используется интерпретатор: %PY_CMD%
echo • Шаг 1/3: Обновление менеджера пакетов pip...
%PY_CMD% -m pip install --upgrade pip

echo.
echo • Шаг 2/3: Установка зависимостей из requirements.txt...
%PY_CMD% -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ОШИБКА] При установке зависимостей возникли ошибки!
    echo Проверьте подключение к интернету или права доступа.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo • Шаг 3/3: Верификация установленных модулей...
%PY_CMD% -c "import pydantic, openai, fitz, PIL, openpyxl, pandas, docx, docx2txt, pypdf, httpx, dotenv; print('   [OK] Все ключевые модули успешно загружены!')"
if %ERRORLEVEL% neq 0 (
    echo [ВНИМАНИЕ] Некоторые модули не прошли тест импорта.
) else (
    echo.
    echo ==============================================================================
    echo  ✅ Все зависимости успешно установлены и готовы к работе!
    echo  Теперь можно запускать 1_ОБРАБОТКА_ДОКУМЕНТОВ.bat
    echo ==============================================================================
)

echo.
pause

