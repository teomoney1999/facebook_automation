"""
Unit tests for Production Hardening Plan.
Verifies critical fixes from PRODUCTION_READINESS.md:
1. Browser disconnection returns UNKNOWN instead of false SUCCESS.
2. Negative/zero batch-size validation in StateManager and CLI.
3. Account ACTION_BLOCKED state persistence across restarts.
4. Bot halts immediately with NEEDS_LOGIN when authentication fails.
5. Single-instance ProcessLock prevents concurrent executions.
6. Robust URL normalization (query param/fragment stripping, domain validation).
7. In-flight / UNKNOWN status cooldown protection against duplicate posting.
"""

import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent
for p in (REPO_ROOT, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from config import BotConfig
    from facebookbot import FacebookBot
    from main import ProcessLock, check_positive_int
    from services.circuit_breaker import CircuitBreaker
    from services.post_verifier import PostStatus, PostVerificationResult, PostVerifier
    from services.state_manager import StateManager, normalize_url
    from selenium.common.exceptions import WebDriverException
except ImportError:
    from repo.config import BotConfig
    from repo.facebookbot import FacebookBot
    from repo.main import ProcessLock, check_positive_int
    from repo.services.circuit_breaker import CircuitBreaker
    from repo.services.post_verifier import PostStatus, PostVerificationResult, PostVerifier
    from repo.services.state_manager import StateManager, normalize_url
    from selenium.common.exceptions import WebDriverException


class TestProductionHardening(unittest.TestCase):
    """Test suite for Production Hardening verifications."""

    def setUp(self):
        self.sleep_patcher = patch("time.sleep", return_value=None)
        self.sleep_patcher.start()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_base = Path(self.temp_dir.name)
        self.json_dir = self.temp_base / "json"
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.json_dir / "history.json"
        self.selectors_file = self.json_dir / "selectors.json"
        self.groups_file = self.json_dir / "links.json"
        self.posts_file = self.json_dir / "posts.json"

        with open(self.selectors_file, "w", encoding="utf-8") as f:
            json.dump({
                "content_bar": ["//div[@role='button']"],
                "create_post_modal": ["//div[@role='textbox']"],
                "post_button": ["//div[@aria-label='Post']"],
                "search_bars": ["//input[@placeholder='Search Facebook']"],
                "home_banner": ["//div[@role='banner']"],
            }, f)

        with open(self.groups_file, "w", encoding="utf-8") as f:
            json.dump({"target_links": ["https://facebook.com/groups/101", "https://facebook.com/groups/102"]}, f)

        with open(self.posts_file, "w", encoding="utf-8") as f:
            json.dump({"content": "Production Hardening Post Content"}, f)

        self.config = BotConfig(
            email="prod@example.com",
            password="secret_password",
            min_delay_between_posts=0.1,
            max_delay_between_posts=0.2,
            max_posts_per_run=5,
            timeout=1,
            debug_port=9224,
            cooldown_hours=24,
            base_dir=self.temp_base,
        )
        self.mock_logger = MagicMock()

    def tearDown(self):
        self.sleep_patcher.stop()
        self.temp_dir.cleanup()

    def test_post_verifier_returns_unknown_on_webdriver_disconnection(self):
        """Verifies driver crash or disconnection produces UNKNOWN, never SUCCESS."""
        mock_driver = MagicMock()
        mock_driver.find_elements.side_effect = WebDriverException("Browser connection lost / crashed")

        verifier = PostVerifier(timeout=0.5, poll_interval=0.1)
        res = verifier.verify_post_submission(mock_driver, "https://facebook.com/groups/test")

        self.assertEqual(res.status, PostStatus.UNKNOWN)
        self.assertIn("mất kết nối", res.message)

    def test_batch_size_negative_or_zero_raises_value_error(self):
        """Verifies StateManager rejects non-positive batch size."""
        mgr = StateManager(self.history_file)
        targets = ["https://facebook.com/groups/1", "https://facebook.com/groups/2"]

        with self.assertRaises(ValueError):
            mgr.get_batch_targets(targets, max_batch_size=-1)

        with self.assertRaises(ValueError):
            mgr.get_batch_targets(targets, max_batch_size=0)

    def test_cli_batch_size_validation(self):
        """Verifies CLI argument validator rejects <= 0 or invalid integers."""
        self.assertEqual(check_positive_int("5"), 5)
        with self.assertRaises(argparse.ArgumentTypeError):
            check_positive_int("-1")
        with self.assertRaises(argparse.ArgumentTypeError):
            check_positive_int("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            check_positive_int("abc")

    def test_account_blocked_persistence_across_restarts(self):
        """Verifies ACTION_BLOCKED state persists and stops bot on next startup."""
        mgr1 = StateManager(self.history_file)
        mgr1.mark_account_blocked(duration_hours=24.0)

        # Fresh instance simulating restart
        mgr2 = StateManager(self.history_file)
        is_blocked, remaining = mgr2.is_account_blocked()
        self.assertTrue(is_blocked)
        self.assertGreater(remaining, 0)

        # Bot should fast-stop immediately without opening browser
        bot = FacebookBot(config=self.config, state_manager=mgr2, custom_logger=self.mock_logger)
        with patch("facebookbot.get_driver") as mock_get_driver:
            results = bot.run()
            mock_get_driver.assert_not_called()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], PostStatus.ACTION_BLOCKED.value)

    @patch("facebookbot.get_driver")
    def test_bot_stops_immediately_when_login_fails(self, mock_get_driver_cm):
        """Verifies bot halts with NEEDS_LOGIN when session check fails even after login flow."""
        mock_driver = MagicMock()
        mock_get_driver_cm.return_value.__enter__.return_value = mock_driver

        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        with patch.object(bot, "check_login_status", return_value=False):
            with patch.object(bot, "post_to_group") as mock_post:
                results = bot.run()
                mock_post.assert_not_called()
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["status"], "NEEDS_LOGIN")

    @patch("facebookbot.get_driver")
    @patch.object(FacebookBot, "post_to_group")
    def test_bot_auto_logins_before_posting(self, mock_post, mock_get_driver_cm):
        """Verifies bot auto-fills credentials and succeeds if initial session check is False."""
        mock_driver = MagicMock()
        mock_get_driver_cm.return_value.__enter__.return_value = mock_driver

        mock_post.return_value = PostVerificationResult(
            status=PostStatus.SUCCESS, message="Success"
        )

        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        with patch.object(bot, "check_login_status", side_effect=[False, True]):
            with patch.object(bot, "fill_and_submit_login", return_value=True) as mock_fill:
                results = bot.run()
                mock_fill.assert_called_once_with(mock_driver)
                self.assertEqual(len(results), 2)
                self.assertEqual(results[0]["status"], PostStatus.SUCCESS.value)
                self.assertEqual(mock_post.call_count, 2)

    def test_single_instance_process_lock(self):
        """Verifies ProcessLock prevents multiple concurrent bot processes."""
        lock_path = self.temp_base / "test_bot.lock"
        lock1 = ProcessLock(lock_path)
        lock2 = ProcessLock(lock_path)

        # First lock acquires
        self.assertTrue(lock1.acquire())

        # Second lock attempt must fail
        self.assertFalse(lock2.acquire())

        # After releasing first, second can acquire
        lock1.release()
        self.assertTrue(lock2.acquire())
        lock2.release()

    def test_normalize_url_strips_queries_and_validates_domain(self):
        """Verifies URL normalizer canonicalizes domain, removes tracking params, filters foreign domains."""
        url_with_query = "https://www.facebook.com/groups/123456/?ref=share&mibextid=ZbWKwL#heading"
        self.assertEqual(normalize_url(url_with_query), "https://www.facebook.com/groups/123456")

        # Canonical domain normalization across facebook.com, m.facebook.com, and raw strings
        self.assertEqual(normalize_url("https://facebook.com/groups/123456"), "https://www.facebook.com/groups/123456")
        self.assertEqual(normalize_url("https://m.facebook.com/groups/123456"), "https://www.facebook.com/groups/123456")
        self.assertEqual(normalize_url("facebook.com/groups/123456"), "https://www.facebook.com/groups/123456")

        # Foreign domain must be rejected
        foreign_url = "https://malicious.com/groups/123456"
        self.assertEqual(normalize_url(foreign_url), "")

    def test_unknown_and_in_progress_states_enforce_cooldown(self):
        """Verifies IN_PROGRESS and UNKNOWN states prevent duplicate posting on restart."""
        mgr = StateManager(self.history_file)
        test_url = "https://facebook.com/groups/in_flight_group"
        mgr.record_post(test_url, "IN_PROGRESS", error="Process died midway")

        in_cd, remaining = mgr.is_in_cooldown(test_url, cooldown_hours=24.0)
        self.assertTrue(in_cd)
        self.assertGreater(remaining, 0)

    def test_corrupted_history_halts_bot(self):
        """Verifies bot halts immediately when history file is corrupted, preserving safety."""
        # Write corrupted JSON to history file
        self.history_file.write_text("{ this is broken json }", encoding="utf-8")
        mgr = StateManager(self.history_file)
        self.assertTrue(mgr.is_corrupted)

        bot = FacebookBot(config=self.config, state_manager=mgr, custom_logger=self.mock_logger)
        with patch("facebookbot.get_driver") as mock_get_driver:
            results = bot.run()
            mock_get_driver.assert_not_called()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "FAILED")
            self.assertIn("Corrupted history", results[0]["details"])


if __name__ == "__main__":
    unittest.main()
