"""
Unit Tests for Per-Session Summary and Task Completion Time.

Verifies:
1. `format_duration` handles seconds, minutes, hours properly.
2. `StateManager.record_session` persists runs to history.json atomically.
3. `StateManager.get_sessions` returns sessions sorted newest first.
4. `_cluster_legacy_history_into_sessions` groups existing history records by proximity.
5. `FacebookBotGUI` Tab 2:
   - Populates session dropdown with per-run stats and completion time.
   - Selecting a run filters the Treeview to show only groups posted in that run.
   - Summary card displays completion time, time range, and success/pending/failed breakdown.
   - Selecting all sessions displays aggregate summary and all groups.
6. `_reset_ui_after_run` reports completion duration and counts.
"""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.common_helpers import format_duration
from services.state_manager import StateManager
from config import BotConfig

try:
    import tkinter as tk
    from app_ui import FacebookBotGUI, BotWorkerThread
    TKINTER_AVAILABLE = True
except Exception:
    TKINTER_AVAILABLE = False


class TestDurationFormatting(unittest.TestCase):
    """Tests human-readable duration formatting."""

    def test_seconds_only(self):
        self.assertEqual(format_duration(0), "0 giây")
        self.assertEqual(format_duration(15), "15 giây")
        self.assertEqual(format_duration(59), "59 giây")

    def test_minutes_and_seconds(self):
        self.assertEqual(format_duration(60), "1 phút")
        self.assertEqual(format_duration(75), "1 phút 15 giây")
        self.assertEqual(format_duration(135), "2 phút 15 giây")
        self.assertEqual(format_duration(180), "3 phút")

    def test_hours_and_minutes(self):
        self.assertEqual(format_duration(3600), "1 giờ 0 phút")
        self.assertEqual(format_duration(3665), "1 giờ 1 phút 5 giây")
        self.assertEqual(format_duration(7320), "2 giờ 2 phút")


class TestSessionStatePersistence(unittest.TestCase):
    """Tests StateManager session recording and retrieval."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.temp_dir.name) / "history.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_session_and_get_sessions(self):
        sm = StateManager(self.history_file)
        self.assertEqual(len(sm.get_sessions()), 0)

        results_run1 = [
            {"link": "https://facebook.com/groups/101", "status": "SUCCESS", "details": "Posted ok"},
            {"link": "https://facebook.com/groups/102", "status": "PENDING_APPROVAL", "details": "Pending admin"},
        ]
        s1 = sm.record_session(
            session_type="POST",
            results=results_run1,
            start_time="2026-11-25T10:00:00+00:00",
            end_time="2026-11-25T10:02:15+00:00",
            duration_seconds=135.0,
        )

        self.assertEqual(s1["id"], 1)
        self.assertEqual(s1["duration_formatted"], "2 phút 15 giây")
        self.assertEqual(s1["total_groups"], 2)
        self.assertEqual(s1["success_count"], 1)
        self.assertEqual(s1["pending_count"], 1)
        self.assertEqual(s1["failed_count"], 0)

        # Record second session
        results_run2 = [
            {"link": "https://facebook.com/groups/201", "status": "SUCCESS", "details": "Posted ok"},
            {"link": "https://facebook.com/groups/202", "status": "ACTION_BLOCKED", "details": "Blocked"},
        ]
        s2 = sm.record_session(
            session_type="POST",
            results=results_run2,
            start_time="2026-11-25T11:00:00+00:00",
            end_time="2026-11-25T11:01:00+00:00",
            duration_seconds=60.0,
        )
        self.assertEqual(s2["id"], 2)

        # Reload from disk
        sm_reloaded = StateManager(self.history_file)
        sessions = sm_reloaded.get_sessions()
        self.assertEqual(len(sessions), 2)
        # Newest first
        self.assertEqual(sessions[0]["id"], 2)
        self.assertEqual(sessions[1]["id"], 1)

    def test_cluster_legacy_history_into_sessions(self):
        """Verifies that legacy history without sessions is clustered by time proximity."""
        data = {
            "version": "1.0",
            "updated_at": "2026-11-25T00:00:00+00:00",
            "history": {
                "https://facebook.com/groups/a": {
                    "group_url": "https://facebook.com/groups/a",
                    "last_posted_at": "2026-11-24T10:00:00+00:00",
                    "status": "SUCCESS",
                    "post_count": 1,
                    "error": None,
                },
                "https://facebook.com/groups/b": {
                    "group_url": "https://facebook.com/groups/b",
                    "last_posted_at": "2026-11-24T10:01:30+00:00",
                    "status": "SUCCESS",
                    "post_count": 1,
                    "error": None,
                },
                "https://facebook.com/groups/c": {
                    "group_url": "https://facebook.com/groups/c",
                    "last_posted_at": "2026-11-24T15:00:00+00:00",
                    "status": "PENDING_APPROVAL",
                    "post_count": 1,
                    "error": "Pending admin",
                },
            },
        }
        self.history_file.write_text(json.dumps(data), encoding="utf-8")

        sm = StateManager(self.history_file)
        sessions = sm.get_sessions()
        # Should cluster into 2 sessions (one at 10:00 with 2 groups, one at 15:00 with 1 group)
        self.assertEqual(len(sessions), 2)
        # Newest first
        self.assertEqual(sessions[0]["total_groups"], 1)
        self.assertEqual(sessions[1]["total_groups"], 2)


@unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter is required for GUI tests")
class TestGUIPerSessionSummary(unittest.TestCase):
    """Tests GUI presentation of per-session summaries and completion times."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.d = Path(self.temp_dir.name)
        self.json_dir = self.d / "json"
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.json_dir / "history.json"
        self.env_file = self.d / ".env"

        self.env_file.write_text(
            f"EMAIL=test@domain.com\nPASSWORD=secret\nJSON_DIR={self.json_dir}\n",
            encoding="utf-8",
        )

        # Create two distinct sessions in history.json
        history_data = {
            "version": "1.0",
            "history": {},
            "sessions": [
                {
                    "id": 1,
                    "type": "POST",
                    "start_time": "2026-11-25T08:00:00+00:00",
                    "end_time": "2026-11-25T08:01:45+00:00",
                    "duration_seconds": 105.0,
                    "duration_formatted": "1 phút 45 giây",
                    "total_groups": 2,
                    "success_count": 2,
                    "pending_count": 0,
                    "failed_count": 0,
                    "results": [
                        {"link": "https://facebook.com/groups/run1_g1", "status": "SUCCESS", "details": "Done"},
                        {"link": "https://facebook.com/groups/run1_g2", "status": "SUCCESS", "details": "Done"},
                    ],
                },
                {
                    "id": 2,
                    "type": "POST",
                    "start_time": "2026-11-25T09:00:00+00:00",
                    "end_time": "2026-11-25T09:03:10+00:00",
                    "duration_seconds": 190.0,
                    "duration_formatted": "3 phút 10 giây",
                    "total_groups": 3,
                    "success_count": 1,
                    "pending_count": 1,
                    "failed_count": 1,
                    "results": [
                        {"link": "https://facebook.com/groups/run2_g1", "status": "SUCCESS", "details": "Done"},
                        {"link": "https://facebook.com/groups/run2_g2", "status": "PENDING_APPROVAL", "details": "Pending"},
                        {"link": "https://facebook.com/groups/run2_g3", "status": "FAILED", "details": "Failed error"},
                    ],
                },
            ],
        }
        self.history_file.write_text(json.dumps(history_data), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_gui_session_dropdown_and_tree_filtering(self):
        """Verifies session dropdown options and per-session treeview group filtering."""
        try:
            app = FacebookBotGUI(config_path=str(self.env_file))
            app.withdraw()

            # Check combobox populated
            values = app.cbo_history_sessions["values"]
            self.assertEqual(len(values), 3)  # All, Run 2, Run 1
            self.assertEqual(values[0], "🌐 [Tất cả các lần đăng]")
            self.assertIn("Lần #2", values[1])
            self.assertIn("Lần #1", values[2])

            # Default selected should be all sessions
            self.assertEqual(app.cbo_history_sessions.get(), "🌐 [Tất cả các lần đăng]")

            # Now switch combobox to Run 2
            app.cbo_history_sessions.set(values[1])
            app._on_history_session_changed()

            # Verify Run 2 summary card displays completion time
            self.assertIn("Lần Đăng #2", app.lbl_session_title.cget("text"))
            self.assertIn("3 phút 10 giây", app.lbl_session_duration.cget("text"))
            self.assertIn("✅ Thành công: 1", app.lbl_session_stats.cget("text"))
            self.assertIn("⏳ Chờ duyệt: 1", app.lbl_session_stats.cget("text"))
            self.assertIn("❌ Thất bại: 1", app.lbl_session_stats.cget("text"))

            # Tree should only display the 3 groups from Run 2
            items = app.tree_history.get_children()
            self.assertEqual(len(items), 3)
            vals_first = app.tree_history.item(items[0], "values")
            self.assertEqual(vals_first[2], "https://facebook.com/groups/run2_g1")
            self.assertIn("Thành Công", vals_first[3])

            # Now switch combobox to Run 1
            app.cbo_history_sessions.set(values[2])
            app._on_history_session_changed()

            # Verify Run 1 summary card displays completion time
            self.assertIn("Lần Đăng #1", app.lbl_session_title.cget("text"))
            self.assertIn("1 phút 45 giây", app.lbl_session_duration.cget("text"))
            self.assertIn("Tổng: 2 nhóm", app.lbl_session_stats.cget("text"))

            # Tree should only display the 2 groups from Run 1
            items_run1 = app.tree_history.get_children()
            self.assertEqual(len(items_run1), 2)
            self.assertEqual(app.tree_history.item(items_run1[0], "values")[2], "https://facebook.com/groups/run1_g1")
            self.assertEqual(app.tree_history.item(items_run1[1], "values")[2], "https://facebook.com/groups/run1_g2")

            # Now switch to all sessions
            app.cbo_history_sessions.set("🌐 [Tất cả các lần đăng]")
            app._on_history_session_changed()

            # Should display 5 groups total
            items_all = app.tree_history.get_children()
            self.assertEqual(len(items_all), 5)
            self.assertIn("Tất Cả Các Lần Đăng", app.lbl_session_title.cget("text"))
            self.assertIn("⏱ Tổng thời gian: 4 phút 55 giây", app.lbl_session_duration.cget("text"))

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available: {tcl_err}")

    def test_reset_ui_after_run_shows_completion_dialog(self):
        """Verifies _reset_ui_after_run displays completion duration and statistics."""
        try:
            app = FacebookBotGUI(config_path=str(self.env_file))
            app.withdraw()

            mock_worker = mock.MagicMock()
            mock_worker.duration_str = "2 phút 30 giây"
            app.worker_thread = mock_worker

            results = [
                {"link": "https://facebook.com/groups/test1", "status": "SUCCESS"},
                {"link": "https://facebook.com/groups/test2", "status": "PENDING_APPROVAL"},
            ]

            with mock.patch("tkinter.messagebox.showinfo") as mock_info:
                app._reset_ui_after_run(results)
                mock_info.assert_called_once()
                args, _ = mock_info.call_args
                title, body = args
                self.assertEqual(title, "Hoàn Thành Công Việc")
                self.assertIn("2 phút 30 giây", body)
                self.assertIn("Thành công: 1", body)
                self.assertIn("Chờ duyệt: 1", body)

            # Check status card resets to IDLE
            status_text = app.card_status.val_label.cget("text")
            self.assertEqual(status_text, "🟢 IDLE")

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available: {tcl_err}")


if __name__ == "__main__":
    unittest.main()
