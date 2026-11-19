@echo off
setlocal
chcp 65001 >nul
title Crawler Hub - Update
cd /d "%~dp0"
rem This whole block is parsed before replacing the running BAT.
if exist "%~f0.new" (
  move /y "%~f0.new" "%~f0" >nul
  if errorlevel 1 (
    echo 업데이트 도구 적용 실패. 다시 실행해주세요.
    pause >nul
    exit /b 1
  )
  call "%~f0" %*
  exit /b
)
set "PYTHONIOENCODING=utf-8"
set "CRAWLER_HUB_UPDATER_DIR=%~dp0"
set "hubInstallRoot=%~dp0"
set "hubLaunch=1"
for %%A in (%*) do if /i "%%~A"=="--check-only" set "hubLaunch=0"
for %%A in (%*) do if /i "%%~A"=="--no-launch" set "hubLaunch=0"
:findroot
if exist "%hubInstallRoot%app\bootstrap.py" if exist "%hubInstallRoot%brands\" goto foundroot
for %%D in ("%hubInstallRoot%..") do set "hubParent=%%~fD\"
if /i "%hubParent%"=="%hubInstallRoot%" goto noroot
set "hubInstallRoot=%hubParent%"
goto findroot
:foundroot
set "hubUpdateScript=%~dp0update_develop.py"
if not exist "%hubUpdateScript%" goto noscript
set "hubUpdatePython="
call :tryPython "%LOCALAPPDATA%\CrawlerHub\venv\Scripts\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do call :tryPython "%%~D\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do call :tryPython "%%~D\python.exe"
if defined hubUpdatePython goto run
where py >nul 2>nul
if errorlevel 1 goto tryPath
py -3 -c "import sys; raise SystemExit(sys.version_info < (3,10))" >nul 2>nul
if not errorlevel 1 goto py
:tryPath
python -c "import sys; raise SystemExit(sys.version_info < (3,10))" >nul 2>nul
if errorlevel 1 goto nopython
python -B "%hubUpdateScript%" --root "%hubInstallRoot%." %*
goto finish
:py
py -3 -B "%hubUpdateScript%" --root "%hubInstallRoot%." %*
goto finish
:run
"%hubUpdatePython%" -B "%hubUpdateScript%" --root "%hubInstallRoot%." %*
goto finish
:tryPython
if defined hubUpdatePython exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys; raise SystemExit(sys.version_info < (3,10))" >nul 2>nul
if not errorlevel 1 set "hubUpdatePython=%~1"
exit /b 0
:noroot
echo 업데이트 실패: 앱 설치 폴더를 찾지 못했습니다.
cmd /c exit 1
goto finish
:noscript
echo 업데이트 실패: update_develop.py를 같은 폴더에 넣어주세요.
cmd /c exit 1
goto finish
:nopython
echo 업데이트 실패: Python 3.10 이상이 필요합니다.
cmd /c exit 1
:finish
set "hubUpdateResult=%errorlevel%"
if not "%hubUpdateResult%"=="0" goto failed
if "%hubLaunch%"=="0" goto done
if not exist "%hubInstallRoot%Crawler_Hub_Windows.bat" goto nolauncher
echo 앱을 실행합니다.
call "%hubInstallRoot%Crawler_Hub_Windows.bat"
set "hubUpdateResult=%errorlevel%"
if not "%hubUpdateResult%"=="0" goto failed
goto done
:nolauncher
echo 업데이트는 완료됐지만 앱 실행 파일이 없습니다.
set "hubUpdateResult=1"
:failed
echo 오류 내용을 확인한 뒤 다시 실행해주세요.
pause >nul
:done
endlocal & exit /b %hubUpdateResult%
