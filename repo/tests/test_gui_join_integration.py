"""
Unit Tests for Desktop GUI v2.0 Group Join Integration (Module J4).
Validates Worker Thread, UI Callbacks, Membership Badges, and Event Handling.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

# Ensure repository root is on sys.path
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

try:
    import tkinter as tk
    TKINTER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    tk = None
    TKINTER_AVAILABLE = False

from app_ui_v2 import FacebookBotGUIV2, BotWorkerThreadV2
from config import BotConfig
from helpers.common_helpers import save_group_targets


class TestGUIJoinIntegration(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)
        self.env_file = self.base_path / ".env"
        self.env_file.write_text(
            "EMAIL=user_j4@example.com\n"
            "PASSWORD=password_j4\n"
            "MIN_DELAY_BETWEEN_POSTS=45\n"
            "MAX_DELAY_BETWEEN_POSTS=90\n"
            "MAX_POSTS_PER_RUN=10\n"
            "COOLDOWN_HOURS=24\n"
            "DEBUG_PORT=9224\n"
            "TIMEOUT=15\n",
            encoding="utf-8",
        )
        (self.base_path / "json").mkdir(parents=True, exist_ok=True)
        (self.base_path / "logs").mkdir(parents=True, exist_ok=True)

        self.groups_data = [
            {"url": "https://www.facebook.com/groups/101", "name": "Nhóm 101", "membership": "ALREADY_JOINED"},
            {"url": "https://www.facebook.com/groups/102", "name": "Nhóm 102", "membership": "JOIN_REQUEST_SENT"},
            {"url": "https://www.facebook.com/groups/103", "name": "Nhóm 103", "membership": "NOT_JOINED"},
        ]
        save_group_targets(str(self.base_path / "json" / "links.json"), self.groups_data)
        (self.base_path / "json" / "posts.json").write_text('{"posts": []}', encoding="utf-8")
        (self.base_path / "json" / "history.json").write_text("{}", encoding="utf-8")
        (self.base_path / "json" / "groups_meta.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.test_dir.cleanup()

    @mock.patch("app_ui_v2.FacebookBot")
    def test_worker_thread_v2_executes_join_groups(self, mock_bot_cls):
        """Verifies BotWorkerThreadV2 invokes FacebookBot.join_groups with expected arguments."""
        mock_bot = mock.MagicMock()
        expected_report = [
            {"link": "https://www.facebook.com/groups/101", "status": "ALREADY_JOINED"},
            {"link": "https://www.facebook.com/groups/102", "status": "JOIN_REQUEST_SENT"},
        ]
        mock_bot.join_groups.return_value = expected_report
        mock_bot_cls.return_value = mock_bot

        config = BotConfig(email="test@ex.com", password="pwd", base_dir=self.base_path)
        completed_results = []

        worker = BotWorkerThreadV2(
            config=config,
            join_groups=True,
            target_links=["https://www.facebook.com/groups/101", "https://www.facebook.com/groups/102"],
            join_limit=2,
            default_answer="Đồng ý",
            completion_callback=lambda res: completed_results.extend(res),
        )
        worker.start()
        worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        mock_bot.join_groups.assert_called_once()
        _, kwargs = mock_bot.join_groups.call_args
        self.assertEqual(kwargs["target_links"], ["https://www.facebook.com/groups/101", "https://www.facebook.com/groups/102"])
        self.assertEqual(kwargs["join_limit"], 2)
        self.assertEqual(kwargs["default_answer"], "Đồng ý")
        self.assertEqual(completed_results, expected_report)

    @mock.patch("app_ui_v2.FacebookBot")
    def test_worker_thread_v2_handles_exception_gracefully(self, mock_bot_cls):
        """Worker catches unexpected errors and completes without crashing the host."""
        mock_bot = mock.MagicMock()
        mock_bot.join_groups.side_effect = RuntimeError("Selenium driver crashed")
        mock_bot_cls.return_value = mock_bot

        config = BotConfig(email="test@ex.com", password="pwd", base_dir=self.base_path)
        completed_results = []

        worker = BotWorkerThreadV2(
            config=config,
            join_groups=True,
            target_links=["https://www.facebook.com/groups/101"],
            completion_callback=lambda res: completed_results.extend(res),
        )
        worker.start()
        worker.join(timeout=3.0)

        self.assertEqual(len(completed_results), 1)
        self.assertEqual(completed_results[0]["status"], "CRITICAL_ERROR")
        self.assertIn("Selenium driver crashed", completed_results[0]["details"])

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available")
    def test_gui_v2_refresh_groups_tree_displays_membership_badges(self):
        """Verifies Treeview populates membership status badges correctly while preserving column 4 URL."""
        try:
            app = FacebookBotGUIV2(config_path=str(self.env_file))
            app.withdraw()

            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 3)

            # Check rows: values = (idx, name, members, privacy, url, membership, cooldown)
            vals_0 = app.tree_groups.item(children[0], "values")
            self.assertEqual(vals_0[4], "https://www.facebook.com/groups/101")
            self.assertIn("Đã vào", vals_0[5])

            vals_1 = app.tree_groups.item(children[1], "values")
            self.assertEqual(vals_1[4], "https://www.facebook.com/groups/102")
            self.assertIn("Chờ duyệt", vals_1[5])

            vals_2 = app.tree_groups.item(children[2], "values")
            self.assertEqual(vals_2[4], "https://www.facebook.com/groups/103")
            self.assertIn("Chưa vào", vals_2[5])

            app.destroy()
        except tk.TclError as err:
            self.skipTest(f"Tkinter display error: {err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available")
    @mock.patch("app_ui_v2.messagebox.askyesno", return_value=True)
    def test_gui_v2_start_join_groups_flow_selected_vs_all(self, mock_askyesno):
        """Verifies clicking 'Gia nhập nhóm' targets only selected rows when selected, or all when not."""
        try:
            app = FacebookBotGUIV2(config_path=str(self.env_file))
            app.withdraw()

            # Case A: Selection exists
            children = app.tree_groups.get_children()
            app.tree_groups.selection_set(children[1])  # Select second group (102)

            with mock.patch("app_ui_v2.BotWorkerThreadV2") as mock_worker_cls:
                mock_worker = mock.MagicMock()
                mock_worker_cls.return_value = mock_worker

                app._start_join_groups_flow()

                mock_worker_cls.assert_called_once()
                _, kwargs = mock_worker_cls.call_args
                self.assertEqual(kwargs["target_links"], ["https://www.facebook.com/groups/102"])
                self.assertTrue(kwargs["join_groups"])
                self.assertEqual(app.active_session_type, "join_groups")
                mock_worker.start.assert_called_once()

            # Clean worker state
            app.worker_thread = None

            # Case B: No selection -> targets all groups
            app.tree_groups.selection_remove(*app.tree_groups.selection())
            with mock.patch("app_ui_v2.BotWorkerThreadV2") as mock_worker_cls:
                mock_worker = mock.MagicMock()
                mock_worker_cls.return_value = mock_worker

                app._start_join_groups_flow()

                mock_worker_cls.assert_called_once()
                _, kwargs = mock_worker_cls.call_args
                self.assertEqual(len(kwargs["target_links"]), 3)

            app.destroy()
        except tk.TclError as err:
            self.skipTest(f"Tkinter display error: {err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available")
    @mock.patch("app_ui_v2.messagebox.showwarning")
    def test_gui_v2_start_join_groups_flow_aborts_if_worker_alive(self, mock_showwarning):
        """Cannot launch a new join session if a worker thread is currently running."""
        try:
            app = FacebookBotGUIV2(config_path=str(self.env_file))
            app.withdraw()

            app.worker_thread = mock.MagicMock()
            app.worker_thread.is_alive.return_value = True

            app._start_join_groups_flow()
            mock_showwarning.assert_called_once()

            app.destroy()
        except tk.TclError as err:
            self.skipTest(f"Tkinter display error: {err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available")
    def test_gui_v2_on_bot_event_group_join_result(self):
        """Verifies metrics update properly when group_join_result events are received."""
        try:
            app = FacebookBotGUIV2(config_path=str(self.env_file))
            app.withdraw()

            app.metric_processed = 0
            app.metric_success = 0
            app.metric_pending = 0
            app.metric_failed = 0
            app.metric_total = 3

            # 1. Successful instant join
            app._on_bot_event("group_join_result", {"status": "JOINED_SUCCESS", "index": 1, "total": 3})
            self.assertEqual(app.metric_processed, 1)
            self.assertEqual(app.metric_success, 1)

            # 2. Join request sent (pending)
            app._on_bot_event("group_join_result", {"status": "JOIN_REQUEST_SENT", "index": 2, "total": 3})
            self.assertEqual(app.metric_processed, 2)
            self.assertEqual(app.metric_pending, 1)

            # 3. Action blocked or error
            app._on_bot_event("group_join_result", {"status": "ACTION_BLOCKED", "index": 3, "total": 3})
            self.assertEqual(app.metric_processed, 3)
            self.assertEqual(app.metric_failed, 1)
            self.assertEqual(app.metric_remaining, 0)

            app.destroy()
        except tk.TclError as err:
            self.skipTest(f"Tkinter display error: {err}")


if __name__ == "__main__":
    unittest.main()
