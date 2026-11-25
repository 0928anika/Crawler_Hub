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
rem Python 업데이터 우선(회사 PC처럼 PowerShell 스크립트가 막힌 곳에서도 동작). 없으면 PowerShell.
if not exist "tools\update_from_github.py" goto pszip
set "HUBPY="
call :tryPython "%LOCALAPPDATA%\CrawlerHub\venv\Scripts\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do call :tryPython "%%~D\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do call :tryPython "%%~D\python.exe"
if not defined HUBPY (
  where py >nul 2>nul && py -3 -c "import sys; raise SystemExit(sys.version_info < (3,8))" >nul 2>nul && set "HUBPY=py -3"
)
if not defined HUBPY (
  python -c "import sys; raise SystemExit(sys.version_info < (3,8))" >nul 2>nul && set "HUBPY=python"
)
if not defined HUBPY goto pszip
set "PYTHONIOENCODING=utf-8"
if "%HUBPY:~0,1%"=="p" (
  %HUBPY% -B "tools\update_from_github.py" --target "%~dp0." --branch Oberon
) else (
  "%HUBPY%" -B "tools\update_from_github.py" --target "%~dp0." --branch Oberon
)
if errorlevel 1 goto failed
echo 업데이트 완료
goto run
:pszip
if not exist "tools\update_from_github.ps1" goto noscript
echo 최신 버전 내려받는 중... GitHub Oberon 브랜치
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "tools\update_from_github.ps1" -Target "%~dp0."
if errorlevel 1 goto failed
echo 업데이트 완료
goto run
:noscript
echo [업데이트 건너뜀] tools\update_from_github.py / .ps1 이 없습니다.
goto run
:failed
echo [업데이트 실패] 위 사유를 확인하세요. 기존 버전으로 실행합니다.
pause
:run
echo.
if exist "Crawler_Hub_Update_Windows.bat.new" (
  call "%~dp0Crawler_Hub_Windows.bat" %*
  move /y "Crawler_Hub_Update_Windows.bat.new" "Crawler_Hub_Update_Windows.bat" >nul
  exit /b
)
call "%~dp0Crawler_Hub_Windows.bat" %*
endlocal
exit /b
:tryPython
if defined HUBPY exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys; raise SystemExit(sys.version_info < (3,8))" >nul 2>nul
if not errorlevel 1 set "HUBPY=%~1"
exit /b 0
