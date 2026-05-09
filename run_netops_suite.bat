@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

python main.py
if errorlevel 1 goto :error

exit /b 0

:error
echo.
echo NetOps Suite failed to start. Error code: %errorlevel%
pause
exit /b %errorlevel%

