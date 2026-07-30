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

set "DURATION_S=%~1"
if not defined DURATION_S set "DURATION_S=20"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

echo Starting a %DURATION_S% second synthetic EEG-EMG recording.
echo Press Q or Esc in the preview window to stop early.
"%CONDA_CMD%" run --no-capture-output -n "%ENV_NAME%" python -m eeg_emg_recording record --config "%~dp0configs\fake_demo.yaml" --duration-s "%DURATION_S%" --fake --preview
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
