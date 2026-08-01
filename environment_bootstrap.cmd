@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

set "EEG_EMG_PROJECT_ROOT=%~dp0"
if exist "%~dp0environment.local.cmd" call "%~dp0environment.local.cmd"

if defined EEG_EMG_BOOTSTRAP_PYTHON if exist "%EEG_EMG_BOOTSTRAP_PYTHON%" goto run_configured_python

if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" goto run_active_python

if defined EEG_EMG_CONDA_EXE call :python_from_conda "%EEG_EMG_CONDA_EXE%"
if defined BOOTSTRAP_PYTHON goto run_python
if defined CONDA_EXE call :python_from_conda "%CONDA_EXE%"
if defined BOOTSTRAP_PYTHON goto run_python

for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined BOOTSTRAP_PYTHON call :usable_python "%%I"
if defined BOOTSTRAP_PYTHON goto run_python

for %%R in ("%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%PROGRAMDATA%\miniconda3" "%PROGRAMDATA%\anaconda3") do if not defined BOOTSTRAP_PYTHON call :usable_python "%%~R\python.exe"
if defined BOOTSTRAP_PYTHON goto run_python

for %%D in (A B C D E F G H I J K L M N O P Q R S T U V W X Y Z) do if exist "%%D:\" if not defined BOOTSTRAP_PYTHON call :scan_drive "%%D:\"
if defined BOOTSTRAP_PYTHON goto run_python

where py.exe >nul 2>nul
if not errorlevel 1 goto run_py_launcher

echo ERROR: No bootstrap Python interpreter was found.
echo Configure EEG_EMG_BOOTSTRAP_PYTHON or EEG_EMG_CONDA_EXE in environment.local.cmd.
exit /b 1

:run_configured_python
"%EEG_EMG_BOOTSTRAP_PYTHON%" "%~dp0tools\environment_bootstrap.py" %*
exit /b %ERRORLEVEL%

:run_active_python
"%CONDA_PREFIX%\python.exe" "%~dp0tools\environment_bootstrap.py" %*
exit /b %ERRORLEVEL%

:run_py_launcher
py -3 "%~dp0tools\environment_bootstrap.py" %*
exit /b %ERRORLEVEL%

:python_from_conda
set "CONDA_CANDIDATE=%~1"
for %%I in ("%~dp1..\python.exe") do call :usable_python "%%~fI"
exit /b 0

:scan_drive
for %%R in ("%~1Miniconda3" "%~1Anaconda3" "%~1miniconda3" "%~1anaconda3" "%~1ProgramData\Miniconda3" "%~1ProgramData\Anaconda3") do if not defined BOOTSTRAP_PYTHON call :usable_python "%%~R\python.exe"
if exist "%~1Users\" for /d %%U in ("%~1Users\*") do for %%R in ("%%~U\miniconda3" "%%~U\anaconda3") do if not defined BOOTSTRAP_PYTHON call :usable_python "%%~R\python.exe"
exit /b 0

:usable_python
if not exist "%~1" exit /b 0
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)" >nul 2>nul
if "%ERRORLEVEL%"=="0" set "BOOTSTRAP_PYTHON=%~1"
exit /b 0

:run_python
"%BOOTSTRAP_PYTHON%" "%~dp0tools\environment_bootstrap.py" %*
exit /b %ERRORLEVEL%
