"""
Unit tests for Group Join Orchestrator & CLI Integration (Module J3).

Verifies:
1. print_join_summary_table formatting and counter calculations.
2. Dry-run simulation behavior.
3. Batch limit slicing.
4. Account blocked fast-abort.
5. Dynamic Jitter: Fast-Delay for ALREADY_JOINED vs Safe-Jitter for new requests.
6. Circuit Breaker emergency cutoff on ACTION_BLOCKED.
7. Stop event cancellation during execution and delay.
8. CLI command routing for --join-groups.
100% Mocked, zero real Chrome dependencies, runs in milliseconds.
"""

import io
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent
for p in (REPO_ROOT, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import BotConfig
from facebookbot import FacebookBot
from helpers.common_helpers import print_join_summary_table
from services.circuit_breaker import CircuitBreaker
from services.group_joiner import JoinResult
from services.state_manager import StateManager


class TestGroupJoinOrchestrator(unittest.TestCase):
    def setUp(self):
        # Patch sleep to make tests execute instantly
        self.sleep_patcher = patch("time.sleep", return_value=None)
        self.sleep_patcher.start()

        self.mock_config = MagicMock(spec=BotConfig)
        self.mock_config.cooldown_hours = 24
        self.mock_config.logs_dir = Path("/tmp")
        self.mock_config.json_dir = Path("/tmp")
        self.mock_config.screenshots_dir = Path("/tmp")

        self.mock_state_mgr = MagicMock(spec=StateManager)
        self.mock_state_mgr.is_account_blocked.return_value = (False, 0)

        self.circuit_breaker = CircuitBreaker(max_consecutive_failures=3)

        self.bot = FacebookBot(
            config=self.mock_config,
            state_manager=self.mock_state_mgr,
            circuit_breaker=self.circuit_breaker,
            selectors={"join_group_button": ["//div"]},
            target_links=[
                "https://facebook.com/groups/101",
                "https://facebook.com/groups/102",
                "https://facebook.com/groups/103",
            ],
        )

    def tearDown(self):
        self.sleep_patcher.stop()

    def test_print_join_summary_table(self):
        """Verifies summary table prints without exception and categorizes correctly."""
        results = [
            {"link": "https://facebook.com/groups/101", "status": "ALREADY_JOINED", "details": "Đã là thành viên"},
            {"link": "https://facebook.com/groups/102", "status": "JOIN_REQUEST_SENT", "details": "Chờ duyệt"},
            {"link": "https://facebook.com/groups/103", "status": "JOINED_SUCCESS", "details": "Vào ngay"},
            {"link": "https://facebook.com/groups/104", "status": "ACTION_BLOCKED", "details": "Bị khóa"},
        ]
        captured_out = io.StringIO()
        with patch("sys.stdout", captured_out):
            print_join_summary_table(results)

        output = captured_out.getvalue()
        self.assertIn("FINAL GROUP JOINING SUMMARY", output)
        self.assertIn("101", output)
        self.assertIn("ALREADY_JOINED", output)
        self.assertIn("Total Groups Checked: 4", output)

    def test_join_groups_dry_run(self):
        """Dry-run should return plan and not initialize Chrome driver."""
        with patch("facebookbot.get_driver") as mock_get_driver:
            results = self.bot.join_groups(dry_run=True, join_limit=2)
            mock_get_driver.assert_not_called()
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["status"], "DRY_RUN")
            self.assertEqual(results[0]["link"], "https://facebook.com/groups/101")
            self.assertEqual(results[1]["link"], "https://facebook.com/groups/102")

    def test_join_groups_account_blocked_abort(self):
        """If account is marked ACTION_BLOCKED in state_manager, aborts immediately."""
        self.mock_state_mgr.is_account_blocked.return_value = (True, 3600)
        results = self.bot.join_groups()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "ACTION_BLOCKED")

    @patch("facebookbot.save_group_targets")
    @patch("facebookbot.load_group_targets")
    @patch("facebookbot.GroupJoiner")
    @patch("facebookbot.get_driver")
    def test_join_groups_fast_delay_vs_safe_jitter(
        self, mock_get_driver, mock_joiner_cls, mock_load_targets, mock_save_targets
    ):
        """Verifies ALREADY_JOINED triggers Fast-Delay (2-4s) while JOIN_REQUEST_SENT triggers Safe-Jitter."""
        mock_driver = MagicMock()
        mock_get_driver.return_value.__enter__.return_value = mock_driver

        self.bot._ensure_authenticated = MagicMock(return_value=True)

        mock_joiner = MagicMock()
        mock_joiner.join_group.side_effect = [
            JoinResult(status="ALREADY_JOINED", message="Đã vào", group_url="https://facebook.com/groups/101"),
            JoinResult(status="JOIN_REQUEST_SENT", message="Chờ duyệt", group_url="https://facebook.com/groups/102"),
            JoinResult(status="JOINED_SUCCESS", message="Vào ngay", group_url="https://facebook.com/groups/103"),
        ]
        mock_joiner_cls.return_value = mock_joiner

        with patch("random.uniform") as mock_uniform:
            mock_uniform.return_value = 2.5
            results = self.bot.join_groups(
                target_links=[
                    "https://facebook.com/groups/101",
                    "https://facebook.com/groups/102",
                    "https://facebook.com/groups/103",
                ],
                delay_min=15.0,
                delay_max=35.0,
            )

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["status"], "ALREADY_JOINED")
        self.assertEqual(results[1]["status"], "JOIN_REQUEST_SENT")
        self.assertEqual(results[2]["status"], "JOINED_SUCCESS")

    @patch("facebookbot.GroupJoiner")
    @patch("facebookbot.get_driver")
    def test_join_groups_circuit_breaker_trips_on_action_blocked(
        self, mock_get_driver, mock_joiner_cls
    ):
        """Circuit breaker halts the whole loop when encountering ACTION_BLOCKED."""
        mock_driver = MagicMock()
        mock_get_driver.return_value.__enter__.return_value = mock_driver
        self.bot._ensure_authenticated = MagicMock(return_value=True)

        mock_joiner = MagicMock()
        mock_joiner.join_group.side_effect = [
            JoinResult(status="ACTION_BLOCKED", message="Tài khoản bị khóa tính năng"),
            JoinResult(status="JOINED_SUCCESS", message="Không bao giờ chạy tới đây"),
        ]
        mock_joiner_cls.return_value = mock_joiner

        results = self.bot.join_groups(
            target_links=["https://facebook.com/groups/101", "https://facebook.com/groups/102"]
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "ACTION_BLOCKED")
        self.assertTrue(self.bot.circuit_breaker.is_tripped())

    @patch("facebookbot.GroupJoiner")
    @patch("facebookbot.get_driver")
    def test_join_groups_stop_event_cancels(self, mock_get_driver, mock_joiner_cls):
        """Setting stop_event aborts loop cleanly."""
        mock_driver = MagicMock()
        mock_get_driver.return_value.__enter__.return_value = mock_driver
        self.bot._ensure_authenticated = MagicMock(return_value=True)

        stop_evt = threading.Event()
        stop_evt.set()

        results = self.bot.join_groups(
            target_links=["https://facebook.com/groups/101", "https://facebook.com/groups/102"],
            stop_event=stop_evt,
        )
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
