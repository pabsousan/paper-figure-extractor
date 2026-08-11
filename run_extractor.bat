@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: creating a local Python environment...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
".venv\Scripts\python.exe" extract_figures.py
if errorlevel 1 goto :error
pause
exit /b 0
:error
echo.
echo The extractor could not start. Make sure Python 3.10 or newer is installed.
pause
exit /b 1

