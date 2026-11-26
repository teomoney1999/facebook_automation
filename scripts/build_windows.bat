@echo off
REM ==============================================================================
REM Facebook Automation Bot - Windows Build & Packaging Script (.exe)
REM ==============================================================================
echo ======================================================================
echo 🪟 Dong goi ung dung Facebook Automation Bot cho Windows (.exe)
echo ======================================================================

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%.."

REM 1. Kiem tra Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python chua duoc cai dat hoac chua duoc them vao PATH!
    pause
    exit /b 1
)

REM 2. Kiem tra PyInstaller
python -m pyinstaller --version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] PyInstaller chua duoc cai dat. Dang tien hanh cai dat...
    python -m pip install pyinstaller
)

REM 3. Don dep thu muc build cu
echo [INFO] Don dep thu muc cu (build, dist)...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM 4. Tien hanh dong goi voi facebook_bot.spec
echo [INFO] Dang dong goi bang PyInstaller voi facebook_bot.spec...
python -m pyinstaller --clean facebook_bot.spec

REM 5. Kiem tra ket qua
if exist "dist\FacebookBot\FacebookBot.exe" (
    echo [SUCCESS] Tao thanh cong thu muc ung dung doc lap tai dist\FacebookBot!
    copy /y "repo\.env.example" "dist\FacebookBot\.env.example" >nul 2>&1
    echo.
    echo ======================================================================
    echo Ban co the chay truc tiep: dist\FacebookBot\FacebookBot.exe
    echo ======================================================================
) else (
    echo [ERROR] Dong goi that bai! Khong tim thay dist\FacebookBot\FacebookBot.exe
    pause
    exit /b 1
)

REM 6. Bien dich bo cai dat Inno Setup (neu co ISCC.exe)
echo.
echo [INFO] Kiem tra trinh bien dich Inno Setup (ISCC.exe)...
set ISCC_PATH=""
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC_PATH="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC_PATH="%ProgramFiles%\Inno Setup 6\ISCC.exe"

if not %ISCC_PATH%=="" (
    echo [INFO] Tim thay Inno Setup: %ISCC_PATH%
    echo [INFO] Dang tao bo cai dat FacebookBotSetup.exe...
    if exist dist_setup rmdir /s /q dist_setup
    %ISCC_PATH% scripts\installer_windows.iss
    if exist "dist_setup\FacebookBotSetup.exe" (
        echo [SUCCESS] Tao thanh cong bo cai dat duy nhat tai: dist_setup\FacebookBotSetup.exe!
    )
) else (
    echo [NOTE] Chua phat hien Inno Setup 6 tren may.
    echo Ban co the su dung truc tiep thu muc dist\FacebookBot hoac cai dat Inno Setup 6
    echo de bien dich thanh file cai dat duy nhat FacebookBotSetup.exe.
)

pause
