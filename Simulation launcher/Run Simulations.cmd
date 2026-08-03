@echo off
setlocal
cd /d "%~dp0.."

set "CONDA_EXE_PATH="
if defined CONDA_EXE if exist "%CONDA_EXE%" set "CONDA_EXE_PATH=%CONDA_EXE%"
for /f "delims=" %%I in ('where conda.exe 2^>nul') do if not defined CONDA_EXE_PATH set "CONDA_EXE_PATH=%%I"
if not defined CONDA_EXE_PATH if exist "%ProgramData%\miniconda3\Scripts\conda.exe" set "CONDA_EXE_PATH=%ProgramData%\miniconda3\Scripts\conda.exe"
if not defined CONDA_EXE_PATH if exist "%ProgramData%\anaconda3\Scripts\conda.exe" set "CONDA_EXE_PATH=%ProgramData%\anaconda3\Scripts\conda.exe"
if not defined CONDA_EXE_PATH if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_EXE_PATH=%USERPROFILE%\miniconda3\Scripts\conda.exe"
if not defined CONDA_EXE_PATH if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_EXE_PATH=%USERPROFILE%\anaconda3\Scripts\conda.exe"

if not defined CONDA_EXE_PATH (
    echo Conda was not found in PATH or a standard installation location.
    echo Install Miniconda/Anaconda or update CONDA_EXE in your Windows environment.
    pause
    exit /b 1
)
"%CONDA_EXE_PATH%" run --no-capture-output -n fypr-reaktoro python "%~dp0simulation_launcher.py"

if errorlevel 1 (
    echo.
    echo The launcher could not start. The error is shown above.
    pause
)

endlocal
