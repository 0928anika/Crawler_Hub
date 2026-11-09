@echo off
setlocal
title Crawler Hub
cd /d "%~dp0app"
set "hubPython="
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do if not defined hubPython if exist "%%~D\python.exe" set "hubPython=%%~D\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do if not defined hubPython if exist "%%~D\python.exe" set "hubPython=%%~D\python.exe"
if defined hubPython goto run
where py >nul 2>nul
if not errorlevel 1 goto py
python -B bootstrap.py %*
goto finish
:py
py -3 -B bootstrap.py %*
goto finish
:run
"%hubPython%" -B bootstrap.py %*
:finish
if errorlevel 1 pause
endlocal
