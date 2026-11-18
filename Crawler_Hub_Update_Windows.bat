@echo off
chcp 65001 >nul
setlocal
title Crawler Hub - 업데이트 후 실행
cd /d "%~dp0"
set "GIT="
where git >nul 2>nul && set "GIT=git"
if not defined GIT for /d %%D in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do if exist "%%~D\resources\app\git\cmd\git.exe" set "GIT=%%~D\resources\app\git\cmd\git.exe"
if not defined GIT goto nogit
if not exist ".git" goto nozip
for /f "usebackq delims=" %%B in (`"%GIT%" rev-parse --abbrev-ref HEAD`) do set "BRANCH=%%B"
echo 최신 버전 확인 중... 브랜치: %BRANCH%
"%GIT%" pull --ff-only
if errorlevel 1 goto failed
echo 업데이트 완료
goto run
:nogit
echo [업데이트 건너뜀] git을 찾지 못했습니다. GitHub Desktop을 설치하고 이 저장소를 Clone 한 폴더에서 실행하세요.
goto run
:nozip
echo [업데이트 건너뜀] ZIP으로 받은 폴더는 자동 업데이트가 안 됩니다. GitHub Desktop으로 Clone 한 폴더에서 실행하세요.
goto run
:failed
echo [업데이트 실패] 이 PC에서 직접 바꾼 파일이 있거나 네트워크 문제입니다. 기존 버전으로 실행합니다.
:run
echo.
call "%~dp0Crawler_Hub_Windows.bat" %*
endlocal
