@echo off
setlocal
cd /d "%~dp0"

set "ENV_NAME=thermocamera_public"
set "CONDA_CMD="
if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\miniconda3\Scripts\conda.exe"
if not defined CONDA_CMD if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_CMD=%USERPROFILE%\anaconda3\Scripts\conda.exe"
if not defined CONDA_CMD for /f "delims=" %%I in ('where conda.exe 2^>nul') do if not defined CONDA_CMD set "CONDA_CMD=%%I"
if not defined CONDA_CMD (
  echo ERROR: conda was not found.
  pause
  exit /b 1
)

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
"%CONDA_CMD%" run --no-capture-output -n "%ENV_NAME%" python -m eeg_emg_recording list-devices
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
