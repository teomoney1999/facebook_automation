"""
Structured Logging and Observability Helper for Facebook Automation Bot.

Provides dual-handler logging (Console + RotatingFileHandler) and automated
screenshot capturing for visual post-mortem debugging.
"""

import logging
from logging.handlers import RotatingFileHandler
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DEFAULT_LOG_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(
    name: str = "FacebookBot",
    log_file: Optional[Path] = None,
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,  # 5MB
    backup_count: int = 3
) -> logging.Logger:
    """Configure and return an idempotent logger with Console and RotatingFile handlers."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent duplicate handlers on repeated setup
    if logger.handlers:
        return logger

    formatter = logging.Formatter(fmt=DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)

    # 1. Console Handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. Rotating File Handler
    if log_file is None:
        base_dir = Path(__file__).resolve().parent.parent
        log_file = base_dir / "logs" / "bot.log"
    else:
        log_file = Path(log_file)

    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def capture_screenshot(
    driver: Any,
    label: str = "error",
    target_dir: Optional[Path] = None
) -> Optional[str]:
    """
    Capture and persist a browser screenshot with timestamped filename.
    
    Returns the absolute path to saved screenshot, or None if failed.
    Guarantees zero unhandled crash if browser is closed or driver is None.
    """
    logger = logging.getLogger("FacebookBot.Screenshot")

    if driver is None:
        logger.warning("Cannot capture screenshot: driver instance is None.")
        return None

    if target_dir is None:
        base_dir = Path(__file__).resolve().parent.parent
        target_dir = base_dir / "logs" / "screenshots"
    else:
        target_dir = Path(target_dir).resolve()

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as dir_err:
        logger.error(f"Failed to create screenshots directory '{target_dir}': {dir_err}")
        return None

    # Sanitize label for filesystem safety
    safe_label = re.sub(r"[^\w\-_]", "_", label.strip())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    screenshot_path = target_dir / f"{safe_label}_{timestamp}.png"

    try:
        if hasattr(driver, "save_screenshot"):
            success = driver.save_screenshot(str(screenshot_path))
            if success:
                logger.info(f"📸 Screenshot captured: {screenshot_path}")
                return str(screenshot_path)
            logger.warning(f"save_screenshot returned False for path: {screenshot_path}")
            return None
        elif hasattr(driver, "get_screenshot_as_file"):
            driver.get_screenshot_as_file(str(screenshot_path))
            logger.info(f"📸 Screenshot captured: {screenshot_path}")
            return str(screenshot_path)
        else:
            logger.warning("Driver does not support screenshot methods.")
            return None
    except Exception as exc:
        # Zero silent failure: log warning without crashing caller
        logger.warning(f"Failed to capture screenshot '{label}': {exc}")
        return None
