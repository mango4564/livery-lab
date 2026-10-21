@echo off
setlocal
chcp 65001 >nul
if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" -X utf8 "%~dp0compare_ai.py" %*
) else (
    python -X utf8 "%~dp0compare_ai.py" %*
)
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)
exit /b 0
