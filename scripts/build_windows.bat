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
    copy /y "repo\.env.example" "dist\FacebookBot\.env.example"
    echo.
    echo ======================================================================
    echo Ban co the chay truc tiep: dist\FacebookBot\FacebookBot.exe
    echo Hay doi ten .env.example thanh .env va dien thong tin tai khoan.
    echo ======================================================================
) else (
    echo [ERROR] Dong goi that bai! Khong tim thay dist\FacebookBot\FacebookBot.exe
    pause
    exit /b 1
)

pause
