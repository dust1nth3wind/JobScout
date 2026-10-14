@echo off
setlocal
title JobScout - Desktop-Icon einrichten
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-JobScoutIcons.ps1"
set "JOBSCOUT_SETUP_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %JOBSCOUT_SETUP_EXIT%
