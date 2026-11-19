@echo off
setlocal
chcp 65001 >nul
title Crawler Hub - Update develop
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "hubUpdatePython=%LOCALAPPDATA%\CrawlerHub\venv\Scripts\python.exe"
if exist "%hubUpdatePython%" goto run
set "hubUpdatePython="
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do if not defined hubUpdatePython if exist "%%~D\python.exe" set "hubUpdatePython=%%~D\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do if not defined hubUpdatePython if exist "%%~D\python.exe" set "hubUpdatePython=%%~D\python.exe"
if defined hubUpdatePython goto run
where py >nul 2>nul
if not errorlevel 1 goto py
python -B "%~dp0tools\update_develop.py" %*
goto finish
:py
py -3 -B "%~dp0tools\update_develop.py" %*
goto finish
:run
"%hubUpdatePython%" -B "%~dp0tools\update_develop.py" %*
:finish
set "hubUpdateResult=%errorlevel%"
echo.
if not "%hubUpdateResult%"=="0" echo Update failed. Existing jobs and settings were not modified.
pause
endlocal & exit /b %hubUpdateResult%
