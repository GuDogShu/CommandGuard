@echo off
cd /d %~dp0
set PYRUN=py -3
where py >nul 2>nul || set PYRUN=python
%PYRUN% -m PyInstaller --noconfirm --clean --onefile --windowed --name CommandGuard --icon icon.ico --distpath . --workpath build --specpath build command_guard.py
if errorlevel 1 (echo Build failed & pause & exit /b 1)
echo Build OK: CommandGuard.exe
pause
