@echo off
REM ===========================================================================
REM  Data Automation Pro - Windows build script
REM
REM    build.bat            Build the executable AND the installer
REM    build.bat exe        Build the executable only
REM    build.bat installer  Build the installer only (reuses dist\)
REM
REM  Version comes from deployment\version.txt - edit that one file to cut a
REM  new release. Nothing here is tied to a particular developer machine.
REM ===========================================================================

setlocal EnableDelayedExpansion

set "DEPLOY_DIR=%~dp0"
for %%I in ("%DEPLOY_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "DIST_DIR=%PROJECT_ROOT%\dist"
set "APP_DIR=%DIST_DIR%\Data Automation Pro"
set "SPEC_FILE=%DEPLOY_DIR%DataAutomationPro.spec"
set "ISS_FILE=%DEPLOY_DIR%installer\DataAutomationPro.iss"

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=all"

REM --- read version.txt ------------------------------------------------------
set "APP_VERSION="
set "FILE_VERSION="
for /f "usebackq tokens=1,* delims==" %%A in ("%DEPLOY_DIR%version.txt") do (
    set "KEY=%%A"
    if /i "!KEY!"=="APP_VERSION"  set "APP_VERSION=%%B"
    if /i "!KEY!"=="FILE_VERSION" set "FILE_VERSION=%%B"
)
if "%APP_VERSION%"=="" (
    echo [ERROR] APP_VERSION missing from version.txt
    exit /b 1
)
echo === Data Automation Pro build ===
echo     version : %APP_VERSION%  ^(file version %FILE_VERSION%^)
echo     root    : %PROJECT_ROOT%
echo.

REM --- locate the Python interpreter -----------------------------------------
set "PYTHON=%PROJECT_ROOT%\venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

REM --- locate the Inno Setup compiler ----------------------------------------
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
REM winget installs Inno Setup per-user by default:
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC for /f "delims=" %%P in ('where iscc 2^>nul') do set "ISCC=%%P"

REM ===========================================================================
REM  1. Executable
REM ===========================================================================
if /i "%TARGET%"=="installer" goto :installer

echo [1/3] Verifying the application starts from source...
set DAP_SELFTEST=1
"%PYTHON%" "%PROJECT_ROOT%\run_app.py"
set "RC=%ERRORLEVEL%"
set DAP_SELFTEST=
if not "%RC%"=="0" (
    echo [ERROR] Source self-test failed. See logs\selftest.log
    exit /b 1
)
echo       source self-test OK
echo.

echo [2/3] Building the executable with PyInstaller...
if exist "%APP_DIR%" rmdir /s /q "%APP_DIR%"
set "DAP_APP_VERSION=%APP_VERSION%"
set "DAP_FILE_VERSION=%FILE_VERSION%"
"%PYTHON%" -m PyInstaller "%SPEC_FILE%" --noconfirm ^
    --distpath "%DIST_DIR%" --workpath "%DEPLOY_DIR%build" --log-level WARN
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo       verifying the packaged executable...
if exist "%APP_DIR%\logs\selftest.log" del "%APP_DIR%\logs\selftest.log"
set DAP_SELFTEST=1
pushd "%APP_DIR%"
"%APP_DIR%\Data Automation Pro.exe"
set "RC=%ERRORLEVEL%"
popd
set DAP_SELFTEST=
if not "%RC%"=="0" (
    echo [ERROR] Packaged executable self-test failed:
    if exist "%APP_DIR%\var\logs\selftest.log" type "%APP_DIR%\var\logs\selftest.log"
    exit /b 1
)
type "%APP_DIR%\var\logs\selftest.log"
echo.
REM Self-test artefacts must not be shipped inside the installer.
if exist "%APP_DIR%\var"   rmdir /s /q "%APP_DIR%\var"
if exist "%APP_DIR%\config\employees.json" del "%APP_DIR%\config\employees.json"
echo       executable OK: "%APP_DIR%\Data Automation Pro.exe"
echo.

if /i "%TARGET%"=="exe" goto :done

REM ===========================================================================
REM  2. Installer
REM ===========================================================================
:installer
echo [3/3] Building the installer with Inno Setup...
if not defined ISCC (
    echo [ERROR] Inno Setup 6 not found. Install it with:
    echo         winget install --id JRSoftware.InnoSetup
    exit /b 1
)
if not exist "%APP_DIR%\Data Automation Pro.exe" (
    echo [ERROR] No build in dist\. Run "build.bat" without arguments first.
    exit /b 1
)
"%ISCC%" /DAppVersion="%APP_VERSION%" /DFileVersion="%FILE_VERSION%" "%ISS_FILE%"
if not "%ERRORLEVEL%"=="0" (
    echo [ERROR] Inno Setup compilation failed.
    exit /b 1
)

:done
echo.
echo === Build complete ===
if exist "%DIST_DIR%\Data Automation Pro Setup Demo.exe" (
    echo     Installer  : "%DIST_DIR%\Data Automation Pro Setup Demo.exe"
)
echo     Application: "%APP_DIR%\Data Automation Pro.exe"
endlocal
