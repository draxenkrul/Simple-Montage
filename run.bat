@echo off
rem ===================================================================
rem  Simple Montage launcher - just double-click this file.
rem  First run: creates .venv, installs packages, downloads FFmpeg and
rem  libmpv into bin\ (a few minutes). Later runs start the app directly.
rem  Optional: drag a video onto this file to open it.
rem ===================================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto venv_ready

echo [1/3] Looking for Python...
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=python"
if not defined PY goto no_python
echo       Creating the Python environment in .venv ...
%PY% -m venv .venv
if errorlevel 1 goto failed

:venv_ready
.venv\Scripts\python.exe -c "import PySide6, simple_montage" >nul 2>nul
if not errorlevel 1 goto packages_ready
echo [2/3] Installing Python packages (PySide6, python-mpv)...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e .
if errorlevel 1 goto failed

:packages_ready
if not exist "bin\ffmpeg.exe" goto fetch
if not exist "bin\ffprobe.exe" goto fetch
if not exist "bin\libmpv-2.dll" goto fetch
goto start

:fetch
echo [3/3] Downloading FFmpeg and libmpv into bin\ (about 300 MB)...
powershell -NoProfile -ExecutionPolicy Bypass -File "packaging\fetch_binaries.ps1"
if errorlevel 1 goto failed

:start
echo Starting Simple Montage... (this window shows errors if something goes wrong)
.venv\Scripts\python.exe -m simple_montage %*
if errorlevel 1 (
    echo.
    echo The app stopped with an error. Checking the installation:
    .venv\Scripts\python.exe -m simple_montage --check
    pause
)
exit /b 0

:no_python
echo.
echo  Python 3.11 or newer was not found.
echo  1. Download Python from https://www.python.org/downloads/windows/
echo     (the "Windows installer (64-bit)").
echo  2. In the installer, tick "Add python.exe to PATH".
echo  3. Double-click run.bat again.
echo.
echo  Note: if typing "python" opens the Microsoft Store, Python is NOT installed;
echo  that is only a shortcut to the Store.
pause
exit /b 1

:failed
echo.
echo  Setup failed - see the messages above.
echo  Delete the .venv folder and try again, or send the error text.
pause
exit /b 1
