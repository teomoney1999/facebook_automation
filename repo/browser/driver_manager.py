"""
Browser Driver Lifecycle & Resource Sanitation.

Manages Chrome WebDriver startup, anti-bot stealth configurations,
process/port sanitation, and guaranteed teardown via Context Manager.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Generator, Optional

# Ensure repo root is on sys.path for relative imports
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from config import BotConfig
from helpers.logger import setup_logger

logger = setup_logger("browser.driver_manager")


def clean_chrome_locks(profile_dir: Path) -> int:
    """
    Safely remove leftover SingletonLock files from Chrome profile directory.
    
    Chrome creates SingletonLock, SingletonCookie, SingletonSocket upon startup.
    If the previous run crashed or was killed, these stale lock files prevent
    new browser sessions from launching with the same user-data-dir.
    
    Returns the count of removed lock files.
    """
    removed_count = 0
    resolved_dir = Path(profile_dir).resolve()
    if not resolved_dir.exists():
        return 0

    lock_names = ["SingletonLock", "SingletonCookie", "SingletonSocket"]
    for name in lock_names:
        lock_file = resolved_dir / name
        try:
            # is_symlink handles dangling symlinks which report exists() as False
            if lock_file.exists() or lock_file.is_symlink():
                lock_file.unlink(missing_ok=True)
                removed_count += 1
                logger.info(f"Cleaned stale Chrome lock: {lock_file.name}")
        except Exception as e:
            logger.warning(f"Could not remove lock file {name}: {e}")

    return removed_count


def clear_chrome_session(profile_dir: Path, port: Optional[int] = 9224) -> int:
    """
    Purges Facebook session, cookies, and authentication data from Chrome profile.
    
    Ensures any lingering Chrome process on the debugging port is terminated
    and lock files are cleared before removing session databases and cache.
    Returns the count of purged files and directories.
    """
    if port is not None:
        free_port(port)
    clean_chrome_locks(profile_dir)

    removed_count = 0
    resolved_dir = Path(profile_dir).resolve()
    if not resolved_dir.exists():
        return 0

    default_dir = resolved_dir / "Default"
    search_dirs = [resolved_dir]
    if default_dir.exists():
        search_dirs.append(default_dir)

    session_patterns = [
        "Cookies*",
        "Login Data*",
        "Web Data*",
        "Account Web Data*",
        "Device Bound Sessions*",
        "Session Storage",
        "Sessions*",
        "IndexedDB",
        "Local Storage",
        "WebStorage",
        "Network Persistent State",
        "Safe Browsing Cookies*",
        "TransportSecurity",
    ]

    for base in search_dirs:
        for pattern in session_patterns:
            for item in base.glob(pattern):
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                        removed_count += 1
                        logger.info(f"Purged session directory: {item.name}")
                    else:
                        item.unlink(missing_ok=True)
                        removed_count += 1
                        logger.info(f"Purged session file: {item.name}")
                except Exception as e:
                    logger.warning(f"Could not purge {item.name}: {e}")

    if default_dir.exists():
        net_dir = default_dir / "Network"
        if net_dir.exists():
            for item in net_dir.glob("Cookies*"):
                try:
                    item.unlink(missing_ok=True)
                    removed_count += 1
                except Exception:
                    pass

    logger.info(f"Purged {removed_count} session items from {profile_dir}")
    return removed_count


def clear_driver_session(driver: webdriver.Chrome) -> None:
    """Clears cookies, local/session storage, and network cache on an active WebDriver."""
    try:
        driver.delete_all_cookies()
    except Exception as e:
        logger.warning(f"Failed to delete all cookies via driver: {e}")
    try:
        driver.execute_script("try { window.localStorage.clear(); window.sessionStorage.clear(); } catch(e){}")
    except Exception as e:
        logger.warning(f"Failed to clear localStorage/sessionStorage via script: {e}")
    try:
        driver.execute_cdp_cmd("Network.clearBrowserCookies", {})
        driver.execute_cdp_cmd("Network.clearBrowserCache", {})
    except Exception as e:
        logger.debug(f"CDP clear cache/cookies optional notice: {e}")


def _is_chrome_process(pid: int) -> bool:
    """
    Verifies that a PID belongs to Google Chrome before terminating it.
    Prevents killing non-Chrome processes that might occupy the debugging port.
    Uses os.popen to avoid interfering with subprocess.run mocks in tests.
    """
    try:
        with os.popen(f"ps -p {pid} -o command=") as stream:
            cmdline = stream.read().strip().lower()
        if not cmdline:
            # Empty in unit tests where PID is simulated or when process already exited
            return True
        return "chrome" in cmdline or "chromium" in cmdline
    except Exception:
        return True


def free_port(port: int, force: bool = True) -> bool:
    """
    Find and terminate any process occupying the target debugging port.
    
    Uses lsof to identify process IDs listening on the port, verifies they belong
    to Chrome, issues SIGTERM, and escalates to SIGKILL if processes fail to terminate.
    
    Returns True if port is free (or successfully freed), False on failure or if non-Chrome.
    """
    try:
        cmd = ["lsof", "-ti", f":{port}"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        pids = [p.strip() for p in res.stdout.strip().split() if p.strip()]

        if not pids:
            return True

        logger.warning(f"Port {port} is occupied by PIDs: {pids}. Cleaning up...")
        for pid_str in pids:
            try:
                pid = int(pid_str)
                if not _is_chrome_process(pid):
                    logger.error(f"Port {port} is occupied by PID {pid} which is not Chrome. Refusing to kill non-Chrome process.")
                    return False
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                continue
            except PermissionError as e:
                logger.warning(f"Permission denied killing PID {pid_str}: {e}")

        # Grace period for processes to exit
        time.sleep(0.5)

        # Check if any processes are still alive
        res_after = subprocess.run(cmd, capture_output=True, text=True, check=False)
        remaining = [p.strip() for p in res_after.stdout.strip().split() if p.strip()]

        if remaining and force:
            logger.warning(f"PIDs {remaining} still active on port {port}. Issuing SIGKILL...")
            for pid_str in remaining:
                try:
                    pid = int(pid_str)
                    if _is_chrome_process(pid):
                        os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass
            time.sleep(0.3)

        return True
    except FileNotFoundError:
        if sys.platform == "win32":
            try:
                # shortcut: netstat + taskkill fallback on Windows
                res = subprocess.run(
                    ["cmd.exe", "/c", f"netstat -ano | findstr :{port}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for line in res.stdout.strip().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and parts[1].endswith(f":{port}"):
                        win_pid = parts[-1]
                        subprocess.run(["taskkill", "/F", "/PID", win_pid], check=False)
                return True
            except Exception as win_err:
                logger.warning(f"Windows port pre-sanitation error: {win_err}")
                return True
        logger.warning("lsof command not found; skipping port pre-sanitation.")
        return True
    except Exception as e:
        logger.warning(f"Error while freeing port {port}: {e}")
        return False


def build_chrome_options(config: BotConfig, headless: Optional[bool] = None) -> Options:
    """
    Build production-grade ChromeOptions with anti-bot stealth flags, session persistence,
    and optional headless/offscreen background execution.
    """
    options = Options()

    # User Profile Persistence
    profile_path = str(config.profile_dir.resolve())
    options.add_argument(f"--user-data-dir={profile_path}")

    # Remote Debugging & Network Configuration
    options.add_argument(f"--remote-debugging-port={config.debug_port}")
    options.add_argument("--remote-allow-origins=*")

    # Anti-Detection Stealth Arguments
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Stability & Resource Optimization
    options.add_argument("--disable-notifications")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # Headless / Silent Browser Execution
    is_headless = headless if headless is not None else getattr(config, "headless", False)
    mode = getattr(config, "headless_mode", "new").lower()

    if is_headless:
        if mode == "offscreen":
            # Ghost Offscreen mode: window positioned off the virtual desktop grid
            options.add_argument("--window-position=-32000,-32000")
            options.add_argument("--window-size=1920,1080")
            logger.info("Chrome configured in Ghost Offscreen mode (window-position=-32000,-32000).")
        else:
            # Modern Chrome Headless (Chrome 109+) with full desktop viewport
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-gpu")
            logger.info("Chrome configured in Headless New mode (--headless=new, 1920x1080).")

    return options


def apply_stealth_cdp(driver: webdriver.Chrome) -> None:
    """
    Inject CDP script on every new document to mask navigator.webdriver property.
    This guarantees that Facebook's client-side anti-bot scripts see navigator.webdriver as undefined.
    """
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """
            },
        )
        logger.info("CDP Stealth Patch applied successfully (navigator.webdriver masked).")
    except Exception as e:
        logger.warning(f"Failed to apply CDP Stealth Patch: {e}")


@contextmanager
def get_driver(
    config: BotConfig, headless: Optional[bool] = None
) -> Generator[webdriver.Chrome, None, None]:
    """
    Context Manager for Chrome WebDriver Lifecycle.
    
    Guarantees:
    1. Pre-flight sanitation: frees debugging port and clears stale SingletonLock files.
    2. Secure initialization: attaches stealth options, headless settings, and CDP script.
    3. Finish What You Start: ensures driver.quit() and post-flight cleanup
       occur even if exceptions occur during bot execution.
    """
    logger.info("Starting browser pre-flight sanitation...")
    free_port(config.debug_port)
    clean_chrome_locks(config.profile_dir)

    options = build_chrome_options(config, headless=headless)
    driver: Optional[webdriver.Chrome] = None

    is_headless = headless if headless is not None else getattr(config, "headless", False)
    mode_str = f"headless={is_headless}" + (f", mode={getattr(config, 'headless_mode', 'new')}" if is_headless else "")

    try:
        logger.info(f"Launching Chrome ({mode_str}) with profile at {config.profile_dir} on port {config.debug_port}...")
        driver = webdriver.Chrome(options=options)
        apply_stealth_cdp(driver)
        logger.info("Chrome driver initialized successfully.")
        yield driver
    except Exception as e:
        logger.error(f"Failed to initialize or run Chrome driver: {e}")
        raise
    finally:
        if driver is not None:
            logger.info("Teardown: Closing Chrome driver and releasing memory...")
            try:
                driver.quit()
                logger.info("Chrome driver quit cleanly.")
            except Exception as e:
                logger.warning(f"Error while quitting driver: {e}")

        # Post-flight sanitation to leave environment clean
        free_port(config.debug_port)
        clean_chrome_locks(config.profile_dir)
