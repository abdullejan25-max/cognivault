@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title CogniVault Installer
where pwsh.exe >nul 2>&1
if errorlevel 1 goto builtin
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
goto finished

:builtin
rem Compatibility bootstrap for Windows machines without PowerShell 7.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*

:finished
set "install_exit=%errorlevel%"
if /I "%~1"=="-NonInteractive" goto return
echo.
echo Press any key to close this window.
pause >nul
:return
exit /b %install_exit%
