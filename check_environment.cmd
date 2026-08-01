@echo off
setlocal
cd /d "%~dp0"
call "%~dp0environment_bootstrap.cmd" doctor
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
