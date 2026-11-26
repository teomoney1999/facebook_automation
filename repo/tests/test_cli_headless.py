"""
Unit Tests for Module 2: CLI Headless Flags & Background Execution.

Verifies:
1. CLI Argument Parsing: --headless, --no-headless, --headless-mode flags.
2. CLI main() config override: verifies dataclasses.replace updates BotConfig appropriately.
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig
from main import parse_arguments, main


class TestCLIHeadlessArguments(unittest.TestCase):
    """Test suite for CLI argument parser handling headless flags."""

    def test_default_headless_is_false(self):
        with patch.object(sys, "argv", ["main.py"]):
            args = parse_arguments()
            self.assertFalse(args.headless)
            self.assertFalse(args.no_headless)
            self.assertIsNone(args.headless_mode)

    def test_headless_flag_enabled(self):
        with patch.object(sys, "argv", ["main.py", "--headless"]):
            args = parse_arguments()
            self.assertTrue(args.headless)
            self.assertFalse(args.no_headless)

    def test_no_headless_flag_enabled(self):
        with patch.object(sys, "argv", ["main.py", "--no-headless"]):
            args = parse_arguments()
            self.assertFalse(args.headless)
            self.assertTrue(args.no_headless)

    def test_headless_mode_new(self):
        with patch.object(sys, "argv", ["main.py", "--headless", "--headless-mode", "new"]):
            args = parse_arguments()
            self.assertTrue(args.headless)
            self.assertEqual(args.headless_mode, "new")

    def test_headless_mode_offscreen(self):
        with patch.object(sys, "argv", ["main.py", "--headless", "--headless-mode", "offscreen"]):
            args = parse_arguments()
            self.assertTrue(args.headless)
            self.assertEqual(args.headless_mode, "offscreen")

    def test_invalid_headless_mode_rejected(self):
        with patch.object(sys, "argv", ["main.py", "--headless-mode", "invalid_choice"]):
            with self.assertRaises(SystemExit) as ctx:
                parse_arguments()
            self.assertEqual(ctx.exception.code, 2)


class TestCLIMainHeadlessIntegration(unittest.TestCase):
    """Test suite for main() applying CLI headless overrides to BotConfig."""

    def setUp(self):
        self.dummy_config = BotConfig(
            email="cli_test@example.com",
            password="secret_password",
            headless=False,
            headless_mode="new",
            base_dir=REPO_DIR,
        )

    @patch("main.BotConfig.from_env")
    @patch("main.setup_logger")
    @patch("main.FacebookBot")
    @patch("main.ProcessLock")
    def test_main_applies_headless_flag_override(
        self, mock_lock, mock_bot_cls, mock_setup_logger, mock_from_env
    ):
        mock_from_env.return_value = self.dummy_config
        mock_lock_inst = MagicMock()
        mock_lock_inst.acquire.return_value = True
        mock_lock.return_value = mock_lock_inst

        mock_bot_inst = MagicMock()
        mock_bot_inst.circuit_breaker.is_tripped.return_value = False
        mock_bot_inst.run.return_value = []
        mock_bot_cls.return_value = mock_bot_inst

        with patch.object(sys, "argv", ["main.py", "--dry-run", "--headless"]):
            exit_code = main()
            self.assertEqual(exit_code, 0)

        # Inspect the config passed to FacebookBot
        passed_config = mock_bot_cls.call_args[1]["config"]
        self.assertTrue(passed_config.headless)
        self.assertEqual(passed_config.headless_mode, "new")

    @patch("main.BotConfig.from_env")
    @patch("main.setup_logger")
    @patch("main.FacebookBot")
    @patch("main.ProcessLock")
    def test_main_applies_no_headless_override_when_env_was_true(
        self, mock_lock, mock_bot_cls, mock_setup_logger, mock_from_env
    ):
        # Base config has headless=True (e.g. from .env)
        headless_config = BotConfig(
            email="cli_test@example.com",
            password="secret_password",
            headless=True,
            headless_mode="new",
            base_dir=REPO_DIR,
        )
        mock_from_env.return_value = headless_config
        mock_lock_inst = MagicMock()
        mock_lock_inst.acquire.return_value = True
        mock_lock.return_value = mock_lock_inst

        mock_bot_inst = MagicMock()
        mock_bot_inst.circuit_breaker.is_tripped.return_value = False
        mock_bot_inst.run.return_value = []
        mock_bot_cls.return_value = mock_bot_inst

        with patch.object(sys, "argv", ["main.py", "--dry-run", "--no-headless"]):
            exit_code = main()
            self.assertEqual(exit_code, 0)

        # Inspect the config passed to FacebookBot: must be forced to False
        passed_config = mock_bot_cls.call_args[1]["config"]
        self.assertFalse(passed_config.headless)

    @patch("main.BotConfig.from_env")
    @patch("main.setup_logger")
    @patch("main.FacebookBot")
    @patch("main.ProcessLock")
    def test_main_applies_headless_mode_override(
        self, mock_lock, mock_bot_cls, mock_setup_logger, mock_from_env
    ):
        mock_from_env.return_value = self.dummy_config
        mock_lock_inst = MagicMock()
        mock_lock_inst.acquire.return_value = True
        mock_lock.return_value = mock_lock_inst

        mock_bot_inst = MagicMock()
        mock_bot_inst.circuit_breaker.is_tripped.return_value = False
        mock_bot_inst.run.return_value = []
        mock_bot_cls.return_value = mock_bot_inst

        with patch.object(
            sys, "argv", ["main.py", "--dry-run", "--headless", "--headless-mode", "offscreen"]
        ):
            exit_code = main()
            self.assertEqual(exit_code, 0)

        passed_config = mock_bot_cls.call_args[1]["config"]
        self.assertTrue(passed_config.headless)
        self.assertEqual(passed_config.headless_mode, "offscreen")


if __name__ == "__main__":
    unittest.main()
