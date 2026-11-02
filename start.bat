@echo off
rem Springhead one-click launcher (content is ASCII-only on purpose;
rem the project path contains Chinese characters, so %~dp0 is expanded by cmd at runtime).
cd /d "%~dp0"
"D:\miniconda3\python.exe" "%~dp0scripts\start_all.py" %*
echo.
pause
