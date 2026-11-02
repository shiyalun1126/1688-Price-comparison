@echo off
rem Springhead one-click launcher (content is ASCII-only on purpose;
rem the project path contains Chinese characters, so %~dp0 is expanded by cmd at runtime).
cd /d "%~dp0"

rem Pick a Python interpreter: prefer the dev machine's miniconda, fall back to PATH python.
rem start_all.py spawns uvicorn via sys.executable, so whichever is picked here is used throughout.
if exist "D:\miniconda3\python.exe" (
    set "PYEXE=D:\miniconda3\python.exe"
) else (
    set "PYEXE=python"
)

"%PYEXE%" "%~dp0scripts\start_all.py" %*
echo.
pause
