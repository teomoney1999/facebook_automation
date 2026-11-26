@echo off
REM ==============================================================================
REM Facebook Automation Bot - Background Daemon Launcher (Windows)
REM ==============================================================================

cd /d "%~dp0\.."
if not exist "repo\logs" mkdir "repo\logs"

echo [INFO] Dang khoi chay Facebook Bot o che do chay ngam (Headless)...
start "" pythonw repo\main.py --headless %*
echo [OK] Facebook Bot da duoc khoi chay ngam bang pythonw.
echo [NOTE] De kiem tra trang thai, mo tap tin repo\logs\bot.log
