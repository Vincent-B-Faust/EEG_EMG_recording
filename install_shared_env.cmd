@echo off
setlocal
cd /d "%~dp0"

echo Locating a shared environment or creating an independent EEG-EMG environment...
call "%~dp0environment_bootstrap.cmd" install
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo ERROR: installation failed with exit code %EXIT_CODE%.
  pause
  exit /b %EXIT_CODE%
)

echo.
echo Installation complete.
pause
exit /b 0
