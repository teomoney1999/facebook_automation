"""
Unit Tests for Module 1: Browser Headless & Stealth Core.

Verifies:
1. BotConfig validation and environment parsing for HEADLESS and HEADLESS_MODE.
2. build_chrome_options flags for default (headed), modern headless (--headless=new), and ghost offscreen.
3. Explicit headless parameter override in build_chrome_options and get_driver.
4. Smart Headless Fallback in login_interactive ensuring headless=False during 2FA/login.
"""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure repo directory is in sys.path
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig, ConfigurationError
from browser.driver_manager import build_chrome_options, get_driver
from facebookbot import FacebookBot


class TestHeadlessDriverConfig(unittest.TestCase):
    """Test suite for headless configuration schema and parsing."""

    def setUp(self):
        self.tmp_dir = Path("/tmp/fb_headless_test")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def test_default_config_has_headless_disabled(self):
        config = BotConfig(
            email="test@example.com",
            password="secret_password",
            base_dir=self.tmp_dir,
        )
        self.assertFalse(config.headless)
        self.assertEqual(config.headless_mode, "new")

    def test_custom_headless_config(self):
        config = BotConfig(
            email="test@example.com",
            password="secret_password",
            headless=True,
            headless_mode="offscreen",
            base_dir=self.tmp_dir,
        )
        self.assertTrue(config.headless)
        self.assertEqual(config.headless_mode, "offscreen")

    def test_invalid_headless_mode_raises_error(self):
        with self.assertRaises(ConfigurationError) as ctx:
            BotConfig(
                email="test@example.com",
                password="secret_password",
                headless=True,
                headless_mode="invalid_mode",
                base_dir=self.tmp_dir,
            )
        self.assertIn("HEADLESS_MODE must be either 'new' or 'offscreen'", str(ctx.exception))

    def test_from_env_parses_headless_flags(self):
        env_vars = {
            "EMAIL": "env_user@example.com",
            "PASSWORD": "env_password",
            "HEADLESS": "true",
            "HEADLESS_MODE": "offscreen",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = BotConfig.from_env()
            self.assertTrue(config.headless)
            self.assertEqual(config.headless_mode, "offscreen")

    def test_from_env_defaults_to_false_and_new(self):
        env_vars = {
            "EMAIL": "env_user@example.com",
            "PASSWORD": "env_password",
            "HEADLESS": "false",
            "HEADLESS_MODE": "",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            config = BotConfig.from_env()
            self.assertFalse(config.headless)
            self.assertEqual(config.headless_mode, "new")


class TestChromeOptionsHeadless(unittest.TestCase):
    """Test suite for ChromeOptions generation under different headless modes."""

    def setUp(self):
        self.base_dir = Path("/tmp/fb_headless_options_test")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.headed_config = BotConfig(
            email="user@example.com",
            password="secret_password",
            headless=False,
            headless_mode="new",
            base_dir=self.base_dir,
        )
        self.headless_new_config = BotConfig(
            email="user@example.com",
            password="secret_password",
            headless=True,
            headless_mode="new",
            base_dir=self.base_dir,
        )
        self.headless_offscreen_config = BotConfig(
            email="user@example.com",
            password="secret_password",
            headless=True,
            headless_mode="offscreen",
            base_dir=self.base_dir,
        )

    def test_headed_options_do_not_contain_headless_flags(self):
        options = build_chrome_options(self.headed_config)
        args = options.arguments
        self.assertNotIn("--headless=new", args)
        self.assertNotIn("--window-position=-32000,-32000", args)
        self.assertNotIn("--disable-gpu", args)

    def test_headless_new_options_contain_proper_flags(self):
        options = build_chrome_options(self.headless_new_config)
        args = options.arguments
        self.assertIn("--headless=new", args)
        self.assertIn("--window-size=1920,1080", args)
        self.assertIn("--disable-gpu", args)
        self.assertNotIn("--window-position=-32000,-32000", args)

    def test_headless_offscreen_options_contain_proper_flags(self):
        options = build_chrome_options(self.headless_offscreen_config)
        args = options.arguments
        self.assertIn("--window-position=-32000,-32000", args)
        self.assertIn("--window-size=1920,1080", args)
        self.assertNotIn("--headless=new", args)

    def test_explicit_headless_parameter_overrides_config(self):
        # Config has headless=True, but explicit call passes headless=False
        options = build_chrome_options(self.headless_new_config, headless=False)
        args = options.arguments
        self.assertNotIn("--headless=new", args)
        self.assertNotIn("--window-position=-32000,-32000", args)

        # Config has headless=False, but explicit call passes headless=True
        options_forced = build_chrome_options(self.headed_config, headless=True)
        self.assertIn("--headless=new", options_forced.arguments)


class TestSmartHeadlessFallback(unittest.TestCase):
    """Test suite for Smart Headless Fallback in FacebookBot."""

    def setUp(self):
        self.config = BotConfig(
            email="user@example.com",
            password="secret_password",
            headless=True,  # Global config is headless
            headless_mode="new",
            base_dir=REPO_DIR,
        )

    @patch("facebookbot.get_driver")
    def test_login_interactive_forces_headless_false(self, mock_get_driver):
        """Interactive login MUST force headless=False so user can see and interact with Chrome."""
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.facebook.com"
        mock_get_driver.return_value.__enter__.return_value = mock_driver

        bot = FacebookBot(config=self.config)
        with patch.object(bot, "check_login_status", return_value=True):
            result = bot.login_interactive(auto_fill=False)
            self.assertTrue(result)

        # Verify get_driver was called with headless=False
        mock_get_driver.assert_called_once_with(self.config, headless=False)


if __name__ == "__main__":
    unittest.main()
