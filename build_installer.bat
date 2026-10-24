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
rem Builds installer\MemuAI-Setup-<version>.exe with the custom installer in installer\
rem (Python + WebView2 UI, no Inno Setup needed; see installer\README.md).
rem Options: --skip-exe, --fast (quick compression for test builds), --clean, --force
".venv\Scripts\python.exe" build.py installer %ARGS%
if errorlevel 1 goto :fail
if "%NO_PAUSE%"=="0" pause
exit /b 0
:fail
echo.
echo Installer build failed.
if "%NO_PAUSE%"=="0" pause
exit /b 1
