"""
Unit Tests for Module 1: Foundation & Observability.
Runs 100% independently without requiring a real Chrome browser.
"""

import logging
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

# Ensure repo directory is in sys.path
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig, ConfigurationError
from helpers.logger import setup_logger, capture_screenshot


class MockDriver:
    """Mock WebDriver to test screenshot capture without Selenium."""
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.saved_path = None

    def save_screenshot(self, filepath: str) -> bool:
        if self.should_fail:
            raise RuntimeError("Browser process terminated unexpectedly")
        self.saved_path = filepath
        Path(filepath).touch()
        return True


class TestModule1(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)

    def tearDown(self):
        # Close all handlers on test loggers to prevent file lock issues on Windows
        for logger_name in ("TestBotLogger", "FacebookBot.Screenshot"):
            log = logging.getLogger(logger_name)
            for handler in list(log.handlers):
                handler.close()
                log.removeHandler(handler)
        self.test_dir.cleanup()

    def test_config_immutability(self):
        """Verify that BotConfig is immutable (@dataclass(frozen=True))."""
        cfg = BotConfig(
            email="user@test.com",
            password="secret_password",
            base_dir=self.base_path
        )
        with self.assertRaises(Exception):
            cfg.email = "new_user@test.com"

    def test_config_fail_fast_on_missing_credentials(self):
        """Verify fail-fast behavior when EMAIL or PASSWORD is missing."""
        with self.assertRaises(ConfigurationError):
            BotConfig(email="", password="secret", base_dir=self.base_path)

        with self.assertRaises(ConfigurationError):
            BotConfig(email="user@test.com", password="  ", base_dir=self.base_path)

    def test_config_timing_validation(self):
        """Verify validation of timing and anti-ban parameters."""
        with self.assertRaises(ConfigurationError):
            BotConfig(
                email="a@b.com", password="pass",
                min_delay_between_posts=100.0, max_delay_between_posts=50.0,
                base_dir=self.base_path
            )

    def test_config_derived_paths_and_dirs_creation(self):
        """Verify absolute paths calculation and automatic directory creation."""
        cfg = BotConfig(
            email="user@test.com",
            password="secret_password",
            base_dir=self.base_path
        )
        self.assertTrue(cfg.base_dir.is_absolute())
        self.assertTrue(cfg.logs_dir.is_absolute())
        self.assertTrue(cfg.screenshots_dir.is_absolute())
        self.assertTrue(cfg.json_dir.is_absolute())
        self.assertTrue(cfg.profile_dir.is_absolute())
        self.assertTrue(cfg.logs_dir.exists())
        self.assertTrue(cfg.screenshots_dir.exists())

    def test_config_password_masking(self):
        """Verify that password is not exposed in string representation."""
        cfg = BotConfig(
            email="user@test.com",
            password="super_sensitive_password",
            base_dir=self.base_path
        )
        rep = repr(cfg)
        self.assertNotIn("super_sensitive_password", rep)
        self.assertIn("password='***'", rep)

    def test_logger_dual_handlers_and_utf8(self):
        """Verify logger outputs to rotating file and properly encodes UTF-8."""
        log_file = self.base_path / "logs" / "test_bot.log"
        logger = setup_logger(
            name="TestBotLogger",
            log_file=log_file
        )

        test_msg = "Kiểm tra log Tiếng Việt có dấu và icons: 🚀 ✅ ❌"
        logger.info(test_msg)

        for handler in logger.handlers:
            handler.flush()

        self.assertTrue(log_file.exists())
        content = log_file.read_text(encoding="utf-8")
        self.assertIn(test_msg, content)

    def test_capture_screenshot_success_and_resilience(self):
        """Verify screenshot capturing with mock driver and graceful failure handling."""
        mock_driver = MockDriver(should_fail=False)
        shot_path = capture_screenshot(
            driver=mock_driver,
            label="test_action_blocked",
            target_dir=self.base_path / "screenshots"
        )
        self.assertIsNotNone(shot_path)
        self.assertTrue(Path(shot_path).exists())

        # Test resilience when driver raises error (zero crash guarantee)
        failing_driver = MockDriver(should_fail=True)
        res = capture_screenshot(
            driver=failing_driver,
            label="fatal_error",
            target_dir=self.base_path / "screenshots"
        )
        self.assertIsNone(res)

        # Test when driver is None
        res_none = capture_screenshot(
            driver=None,
            label="null_driver",
            target_dir=self.base_path / "screenshots"
        )
        self.assertIsNone(res_none)

    def test_config_debug_port_and_from_env_validation(self):
        """Verify debug port range checking and fail-fast on malformed env values."""
        with self.assertRaises(ConfigurationError):
            BotConfig(
                email="a@b.com",
                password="pass",
                debug_port=80,  # Below 1024
                base_dir=self.base_path
            )

        # Test malformed environment variable float/int raises ConfigurationError
        with mock.patch.dict(os.environ, {"MIN_DELAY_BETWEEN_POSTS": "invalid_delay", "EMAIL": "a@b.com", "PASSWORD": "p"}):
            with self.assertRaises(ConfigurationError):
                BotConfig.from_env()


if __name__ == "__main__":
    unittest.main()
