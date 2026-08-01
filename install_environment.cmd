@echo off
setlocal
cd /d "%~dp0"
call "%~dp0install_shared_env.cmd"
exit /b %ERRORLEVEL%
