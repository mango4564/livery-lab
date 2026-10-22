@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -X utf8 "%~dp0package_source.py" %*
) else (
    python -X utf8 "%~dp0package_source.py" %*
)
set "BUILD_EXIT=%ERRORLEVEL%"
echo.
if not "%BUILD_EXIT%"=="0" echo Source packaging failed. See the error above.
pause
exit /b %BUILD_EXIT%
