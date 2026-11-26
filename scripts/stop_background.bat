@echo off
REM ==============================================================================
REM Facebook Automation Bot - Background Daemon Stopper (Windows)
REM ==============================================================================

echo [INFO] Dang dung tien trinh Chrome va Facebook Bot...
taskkill /F /IM chrome.exe /FI "WINDOWTITLE eq *FacebookBot*" >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
echo [OK] Da dung tien trinh Facebook Bot chay ngam.
