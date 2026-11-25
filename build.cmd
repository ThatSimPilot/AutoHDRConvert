@echo off
rem Runs build.ps1 (builds dist\AutoHDRConvert.exe) from cmd or a double-click.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
exit /b %errorlevel%
