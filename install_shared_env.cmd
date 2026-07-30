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
  echo Install Miniconda or add conda to PATH, then run this file again.
  pause
  exit /b 1
)

echo Installing EEG-EMG recorder into conda environment "%ENV_NAME%"...
"%CONDA_CMD%" run --no-capture-output -n "%ENV_NAME%" python -m pip install -e ".[nidaq,test]"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo ERROR: installation failed with exit code %EXIT_CODE%.
  pause
  exit /b %EXIT_CODE%
)

echo.
echo Installation complete.
"%CONDA_CMD%" run --no-capture-output -n "%ENV_NAME%" python -m eeg_emg_recording --version
pause
exit /b 0
