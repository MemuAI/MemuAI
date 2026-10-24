@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "NO_PAUSE=0"
set "ARGS="
:parse
if "%~1"=="" goto :run
if /I "%~1"=="--no-pause" (set "NO_PAUSE=1") else (set "ARGS=%ARGS% %~1")
shift
goto :parse
:run
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :fail
)
rem Builds installer\MemuAIProxy-Setup-<version>.exe (standalone MemuAI Proxy) with the custom
rem installer in installer\ (no Inno Setup needed; see installer\README.md).
rem Options: --skip-exe, --fast (quick compression for test builds), --clean, --force
".venv\Scripts\python.exe" build.py proxy-installer %ARGS%
if errorlevel 1 goto :fail
if "%NO_PAUSE%"=="0" pause
exit /b 0
:fail
echo.
echo Proxy installer build failed.
if "%NO_PAUSE%"=="0" pause
exit /b 1
