@echo off
setlocal
cd /d "%~dp0"

call "%~dp0environment_bootstrap.cmd" run -- -m eeg_emg_recording list-devices
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
