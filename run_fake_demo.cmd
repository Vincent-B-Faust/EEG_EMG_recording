@echo off
setlocal
cd /d "%~dp0"

set "DURATION_S=%~1"
if not defined DURATION_S set "DURATION_S=20"

echo Starting a %DURATION_S% second synthetic EEG-EMG recording.
echo Press Q or Esc in the preview window to stop early.
call "%~dp0environment_bootstrap.cmd" run -- -m eeg_emg_recording record --config "%~dp0configs\fake_demo.yaml" --duration-s "%DURATION_S%" --fake --preview
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%
