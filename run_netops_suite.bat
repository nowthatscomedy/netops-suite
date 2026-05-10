@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Python virtual environment was not found.
    echo Run the setup command first:
    echo   powershell -ExecutionPolicy Bypass -File .\scripts\install_dev.ps1
    set EXIT_CODE=1
    goto :error
)

".venv\Scripts\python.exe" main.py
if errorlevel 1 goto :error

exit /b 0

:error
if "%EXIT_CODE%"=="" set EXIT_CODE=%errorlevel%
echo.
echo NetOps Suite failed to start. Error code: %EXIT_CODE%
pause
exit /b %EXIT_CODE%

