@echo off
chcp 65001 >nul
setlocal EnableExtensions
title ai-shortform-video

rem ===== 설정 (필요하면 바꾸세요) =====
set "REPO_URL=https://github.com/jeongguskbag-prog/ai-shortform-video.git"
if not defined AI_SHORTS_BRANCH set "AI_SHORTS_BRANCH=main"
if defined AI_SHORTS_DIR goto :dir_chosen
set "AI_SHORTS_DIR=%USERPROFILE%\Documents\ai-shortform-video"
if exist D:\ set "AI_SHORTS_DIR=D:\ai-shortform-video"
:dir_chosen

rem winget으로 방금 설치한 프로그램도 바로 찾도록 PATH 보강
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links;%ProgramFiles%\Git\cmd"

echo.
echo ============================================================
echo   ai-shortform-video 설치 및 실행
echo   설치 위치: %AI_SHORTS_DIR%
echo ============================================================
echo.

echo [1/5] 필요한 프로그램 확인
call :find_python
if not defined PYEXE call :winget_install Python.Python.3.12 "Python"
if not defined PYEXE call :find_python
if not defined PYEXE goto :need_restart

where git >nul 2>nul
if errorlevel 1 call :winget_install Git.Git "Git"
where git >nul 2>nul
if errorlevel 1 goto :need_restart

where ffmpeg >nul 2>nul
if errorlevel 1 call :winget_install Gyan.FFmpeg "FFmpeg"
where ffmpeg >nul 2>nul
if errorlevel 1 goto :need_restart
echo       완료

echo [2/5] 프로그램 받기 / 최신 버전으로 업데이트
if exist "%AI_SHORTS_DIR%\.git" goto :update
git clone --branch "%AI_SHORTS_BRANCH%" "%REPO_URL%" "%AI_SHORTS_DIR%"
if errorlevel 1 goto :fail
goto :fetched
:update
git -C "%AI_SHORTS_DIR%" pull --ff-only
if errorlevel 1 echo       업데이트하지 못했어요. 지금 있는 버전으로 계속합니다.
:fetched
cd /d "%AI_SHORTS_DIR%"
if errorlevel 1 goto :fail

echo [3/5] 파이썬 환경 준비 (처음에는 몇 분 걸려요)
if not exist ".venv\Scripts\python.exe" %PYEXE% -m venv .venv
if not exist ".venv\Scripts\python.exe" goto :fail
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

echo [4/5] 설정 파일 확인
if not exist ".env" copy /y ".env.example" ".env" >nul

echo [5/5] 실행합니다. 이 창을 닫으면 프로그램이 종료돼요.
echo.
".venv\Scripts\python.exe" desktop.py
exit /b %errorlevel%


rem ===== 도우미 =====
:find_python
set "PYEXE="
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (set "PYEXE=py -3.12" & exit /b 0)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (set "PYEXE=py -3" & exit /b 0)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (set "PYEXE=python" & exit /b 0)
exit /b 1

:winget_install
where winget >nul 2>nul
if errorlevel 1 goto :no_winget
echo       %~2 설치 중... (설치 창이 뜨면 [예]를 눌러 주세요)
winget install -e --id %1 --accept-package-agreements --accept-source-agreements
exit /b 0

:no_winget
echo.
echo winget(앱 설치 관리자)을 찾을 수 없어요.
echo Microsoft Store에서 "앱 설치 관리자"를 설치하거나 업데이트한 뒤 다시 실행해 주세요.
goto :end_pause

:need_restart
echo.
echo 프로그램 설치를 마쳤어요. 이 창을 닫고 이 파일을 한 번 더 실행해 주세요.
echo (그래도 같은 화면이 나오면 PC를 다시 시작한 뒤 실행해 주세요.)
goto :end_pause

:fail
echo.
echo 문제가 생겼어요. 위에 나온 메시지를 캡처해서 알려 주세요.

:end_pause
echo.
pause
exit /b 1
