@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

"%PYTHON%" -m pip check
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" scripts\audit_dependencies.py
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" -m ruff check .
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" -m compileall -q main.py app netops_suite qa scripts tests
if errorlevel 1 exit /b %errorlevel%

"%PYTHON%" -m pytest -q
exit /b %errorlevel%
