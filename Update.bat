@echo off
setlocal
chcp 65001 >nul
title Crawler Hub - Update
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "CRAWLER_HUB_UPDATER_DIR=%~dp0"
set "hubInstallRoot=%~dp0"
:findroot
if exist "%hubInstallRoot%app\bootstrap.py" if exist "%hubInstallRoot%brands\" goto foundroot
for %%D in ("%hubInstallRoot%..") do set "hubParent=%%~fD\"
if /i "%hubParent%"=="%hubInstallRoot%" goto noroot
set "hubInstallRoot=%hubParent%"
goto findroot
:foundroot
set "hubUpdateScript=%~dp0update_develop.py"
if not exist "%hubUpdateScript%" set "hubUpdateScript=%hubInstallRoot%tools\update_develop.py"
if not exist "%hubUpdateScript%" goto noscript
set "hubUpdatePython=%LOCALAPPDATA%\CrawlerHub\venv\Scripts\python.exe"
if exist "%hubUpdatePython%" goto run
set "hubUpdatePython="
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do if not defined hubUpdatePython if exist "%%~D\python.exe" set "hubUpdatePython=%%~D\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do if not defined hubUpdatePython if exist "%%~D\python.exe" set "hubUpdatePython=%%~D\python.exe"
if defined hubUpdatePython goto run
where py >nul 2>nul
if not errorlevel 1 goto py
python -B "%hubUpdateScript%" --root "%hubInstallRoot%." %*
goto finish
:py
py -3 -B "%hubUpdateScript%" --root "%hubInstallRoot%." %*
goto finish
:run
"%hubUpdatePython%" -B "%hubUpdateScript%" --root "%hubInstallRoot%." %*
goto finish
:noroot
echo 업데이트 실패: 앱 설치 폴더를 찾지 못했습니다.
cmd /c exit 1
goto finish
:noscript
echo 업데이트 실패: update_develop.py를 같은 폴더에 넣어주세요.
cmd /c exit 1
:finish
set "hubUpdateResult=%errorlevel%"
echo.
pause >nul
endlocal & exit /b %hubUpdateResult%
