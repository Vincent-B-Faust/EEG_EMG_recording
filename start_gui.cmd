@echo off
setlocal
cd /d "%~dp0"

set "CONFIG_PATH=%~1"
if not defined CONFIG_PATH set "CONFIG_PATH=%~dp0configs\ni_usb6001_dam50.yaml"

call "%~dp0environment_bootstrap.cmd" run -- -m eeg_emg_recording gui --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
