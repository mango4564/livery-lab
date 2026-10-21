@echo off
setlocal
chcp 65001 >nul
if exist "%~dp0..\.venv\Scripts\python.exe" (
    "%~dp0..\.venv\Scripts\python.exe" -X utf8 "%~dp0switch_ai.py" %*
) else (
    python -X utf8 "%~dp0switch_ai.py" %*
)
set "SWITCH_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %SWITCH_EXIT%
