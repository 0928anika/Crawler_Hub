@echo off
chcp 65001 >nul
setlocal
title Crawler Hub - 업데이트 후 실행
cd /d "%~dp0"
if exist "Crawler_Hub_Update_Windows.bat.new" (
  echo 업데이트 실행 파일 새 버전이 있습니다. 다음 실행부터 적용됩니다.
)
set "GIT="
where git >nul 2>nul && set "GIT=git"
if not defined GIT for /d %%D in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do if exist "%%~D\resources\app\git\cmd\git.exe" set "GIT=%%~D\resources\app\git\cmd\git.exe"
if exist ".git" if defined GIT goto gitpull
goto zip
:gitpull
for /f "usebackq delims=" %%B in (`"%GIT%" rev-parse --abbrev-ref HEAD`) do set "BRANCH=%%B"
echo 최신 버전 확인 중... git 브랜치: %BRANCH%
"%GIT%" pull --ff-only
if errorlevel 1 goto failed
echo 업데이트 완료
goto run
:zip
if not exist "tools\update_from_github.ps1" goto noscript
echo 최신 버전 내려받는 중... GitHub Oberon 브랜치
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "tools\update_from_github.ps1" -Target "%~dp0."
if errorlevel 1 goto failed
echo 업데이트 완료
goto run
:noscript
echo [업데이트 건너뜀] tools\update_from_github.ps1 이 없습니다.
goto run
:failed
echo [업데이트 실패] 인터넷 연결 또는 git 상태 문제입니다. 기존 버전으로 실행합니다.
:run
echo.
if exist "Crawler_Hub_Update_Windows.bat.new" (
  call "%~dp0Crawler_Hub_Windows.bat" %*
  move /y "Crawler_Hub_Update_Windows.bat.new" "Crawler_Hub_Update_Windows.bat" >nul
  exit /b
)
call "%~dp0Crawler_Hub_Windows.bat" %*
endlocal
