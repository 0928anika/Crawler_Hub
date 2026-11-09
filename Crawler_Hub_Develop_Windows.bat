@echo off
setlocal
title Crawler Hub - Develop
cd /d "%~dp0"
set "CRAWLER_HUB_HOME=%LOCALAPPDATA%\CrawlerHub-develop"
set "CRAWLER_HUB_SOURCE_ROOT=%~dp0app"
set "developPython=%LOCALAPPDATA%\CrawlerHub\venv\Scripts\python.exe"
if exist "%developPython%" goto run
call "%~dp0Crawler_Hub_Windows.bat" --port 8766 %*
goto finish
:run
"%developPython%" -B "%~dp0tools\manage_brands.py" check
if errorlevel 1 goto finish
"%developPython%" -B "%~dp0app\server.py" --port 8766 %*
:finish
if errorlevel 1 pause
endlocal
