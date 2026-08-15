@echo off
setlocal
cd /d "%~dp0"
for %%I in ("%~dp0.") do set "PROJECT_ROOT=%%~fI"

echo Starting Reaktoro Scientific Workbench...
echo The GUI opens in a separate window; the first launch may take a few seconds.
echo Keep this console open while the workbench is running. Startup failures remain visible here.
echo.

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

set "WORKBENCH_PREFIX=%REAKTORO_WORKBENCH_PREFIX%"
if not defined WORKBENCH_PREFIX set "WORKBENCH_PREFIX=%USERPROFILE%\.conda\envs\reaktoro-workbench"
set "SOLVER_PREFIX=%REAKTORO_SOLVER_PREFIX%"
if not defined SOLVER_PREFIX set "SOLVER_PREFIX=%USERPROFILE%\.conda\envs\fypr-reaktoro"
if not exist "%WORKBENCH_PREFIX%\python.exe" (
    echo Workbench environment not found: %WORKBENCH_PREFIX%
    echo Create it explicitly from environment-workbench.yml before launching.
    pause
    exit /b 1
)
if not exist "%SOLVER_PREFIX%\python.exe" (
    echo Solver environment not found: %SOLVER_PREFIX%
    pause
    exit /b 1
)

"%CONDA_EXE_PATH%" run --no-capture-output -p "%WORKBENCH_PREFIX%" python -m workbench --project-root "%PROJECT_ROOT%" --solver-prefix "%SOLVER_PREFIX%"
if errorlevel 1 (
    echo.
    echo The workbench could not start. The error is shown above.
    pause
)
endlocal
