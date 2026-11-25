@echo off
rem Starts ChurchLight Presenter and opens the operator window.
rem Keep this file in the same folder as "churchlight_presenter". Double-click it to start.
rem The black window runs the program; it starts minimized on the taskbar. Closing it (or the Shut down
rem button on the Settings tab) stops ChurchLight, and the window closes by itself.
if /i not "%~1"=="min" (
  start "ChurchLight Presenter" /min "%~f0" min
  exit /b
)
title ChurchLight Presenter
cd /d "%~dp0"
rem Which Python: one already installed on this PC, version 3.10 or newer, is used first. The copy inside the
rem "python" folder next to this file is used only when none is found. Otherwise a plain explanation is shown.
set "PY="
set "OKPY=import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
python -c "%OKPY%" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run
py -c "%OKPY%" >nul 2>nul
if not errorlevel 1 set "PY=py"
if defined PY goto run
if not exist "python\python.exe" goto nopython
"python\python.exe" -c "%OKPY%" >nul 2>nul
if errorlevel 1 goto nopython
set "PY=python\python.exe"
:run
%PY% -m churchlight_presenter --db churchlight.db --media media --open --no-console
set "CODE=%errorlevel%"
if "%CODE%"=="0" exit
echo.
echo ChurchLight stopped because of a problem, code %CODE%. Details are in churchlight.log in this folder.
pause
exit

:nopython
echo.
echo ChurchLight needs Python, and this computer does not have it.
echo   1. A download page is opening. Choose the newest Windows installer, 64-bit.
echo   2. In the installer, TICK "Add python.exe to PATH" at the bottom of the first screen, then Install.
echo   3. Double-click start_churchlight.bat again.
echo.
echo A download of ChurchLight that has a folder named "python" next to this file needs no install.
echo If that folder was deleted, get the full download again. An installed Python older than 3.10 is not enough.
start "" "https://www.python.org/downloads/windows/"
pause
exit
