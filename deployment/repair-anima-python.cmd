@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-anima-controls.ps1" -DependenciesOnly %*
set "installer_exit=%errorlevel%"
echo.
echo Exit code: %installer_exit%. The log is next to this file.
pause
exit /b %installer_exit%
