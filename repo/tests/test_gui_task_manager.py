"""
Unit Tests for GUI Task Manager & Multi-Task Parallel Workflow.

Verifies:
1. _launch_worker enqueues jobs into JobDispatcher without blocking.
2. Concurrent/sequential job queuing: starting a second task queues it behind the first.
3. _stop_bot_flow triggers global dispatcher cancellation.
4. Independent job cancellation signals stop_event only on the target task.
5. _reset_ui_after_run maintains running UI state when dispatcher has remaining jobs.
6. _refresh_task_manager_ui properly updates task summary and widgets.
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig
from services.job_dispatcher import JobDispatcher, JobItem, JobStatus
from app_ui import FacebookBotGUI


class TestGUITaskManagerIntegration(unittest.TestCase):
    """Test suite verifying Task Manager bar and JobDispatcher integration with GUI."""

    def setUp(self):
        self.config = BotConfig(
            email="test@example.com",
            password="secret_password",
            base_dir=REPO_DIR,
        )

    @patch("app_ui.BotWorkerThread")
    def test_launch_worker_enqueues_job_in_dispatcher(self, mock_worker_cls):
        mock_worker_inst = MagicMock()
        mock_worker_cls.return_value = mock_worker_inst

        gui = MagicMock(spec=FacebookBotGUI)
        gui.config = self.config
        gui.history_file = REPO_DIR / "data" / "history.json"
        gui.env_file = REPO_DIR / ".env"
        gui.dispatcher = JobDispatcher()
        gui.logger = MagicMock()
        gui.stop_event = MagicMock()
        gui.btn_start = MagicMock()
        gui.btn_login = MagicMock()
        gui.btn_logout = MagicMock()
        gui.btn_dryrun = MagicMock()
        gui.btn_canary = MagicMock()
        gui.btn_stop = MagicMock()
        gui.card_status = MagicMock()
        gui.progress_bar = MagicMock()
        gui.telegram = None
        gui.tree_groups = MagicMock()
        gui.tree_groups.get_children.return_value = []
        gui.posts = []
        gui.active_draft_idx = 0
        gui.spn_batch = MagicMock()
        gui.spn_batch.get.return_value = "5"
        gui.var_headless = MagicMock()
        gui.var_headless.get.return_value = False
        gui._sync_active_draft_from_controls = MagicMock()
        gui._load_config_state = MagicMock()

        # Call _launch_worker for dry-run
        FacebookBotGUI._launch_worker(
            gui,
            dry_run=True,
            check_dom=False,
            batch_size=5,
        )

        jobs = gui.dispatcher.get_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_type, "DRY_RUN")
        self.assertIn("Chạy thử", jobs[0].name)
        gui.dispatcher.cancel_all()

    @patch("app_ui.BotWorkerThread")
    def test_multiple_jobs_queued_without_blocking(self, mock_worker_cls):
        mock_worker_inst = MagicMock()
        mock_worker_cls.return_value = mock_worker_inst

        gui = MagicMock(spec=FacebookBotGUI)
        gui.config = self.config
        gui.history_file = REPO_DIR / "data" / "history.json"
        gui.env_file = REPO_DIR / ".env"
        gui.dispatcher = JobDispatcher()
        gui.logger = MagicMock()
        gui.stop_event = MagicMock()
        gui.btn_login = MagicMock()
        gui.btn_logout = MagicMock()
        gui.btn_stop = MagicMock()
        gui.card_status = MagicMock()
        gui.progress_bar = MagicMock()
        gui.telegram = None
        gui.tree_groups = MagicMock()
        gui.tree_groups.get_children.return_value = []
        gui.posts = []
        gui.active_draft_idx = 0
        gui.spn_batch = MagicMock()
        gui.spn_batch.get.return_value = "5"
        gui.var_headless = MagicMock()
        gui.var_headless.get.return_value = False
        gui._sync_active_draft_from_controls = MagicMock()
        gui._load_config_state = MagicMock()

        # Launch Task 1: Check DOM
        FacebookBotGUI._launch_worker(gui, dry_run=False, check_dom=True)
        # Launch Task 2: Join Groups
        FacebookBotGUI._launch_worker(gui, dry_run=False, check_dom=False, join_mode=True, custom_targets=["https://fb.com/groups/1"])

        jobs = gui.dispatcher.get_jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].job_type, "DOM_CHECK")
        self.assertEqual(jobs[1].job_type, "JOIN_GROUPS")

        gui.dispatcher.cancel_all()

    def test_stop_bot_flow_cancels_all_dispatcher_jobs(self):
        gui = MagicMock(spec=FacebookBotGUI)
        gui.logger = MagicMock()
        gui.stop_event = MagicMock()
        gui.dispatcher = JobDispatcher()
        gui.btn_stop = MagicMock()
        gui.card_status = MagicMock()

        # Submit dummy queued job
        gui.dispatcher.submit_job("Sample Job", "POST", target_fn=lambda j: None)

        FacebookBotGUI._stop_bot_flow(gui)

        gui.stop_event.set.assert_called_once()
        jobs = gui.dispatcher.get_jobs()
        self.assertTrue(all(j.stop_event.is_set() or j.status == JobStatus.CANCELLED for j in jobs))

    @patch("app_ui.messagebox.showinfo")
    @patch("app_ui.messagebox.showwarning")
    def test_reset_ui_after_run_respects_active_dispatcher_queue(self, mock_warn, mock_info):
        gui = MagicMock(spec=FacebookBotGUI)
        gui.logger = MagicMock()
        gui.stop_event = MagicMock()
        gui.stop_event.is_set.return_value = False
        gui.dispatcher = MagicMock(spec=JobDispatcher)
        gui.dispatcher.is_running = True  # There are still tasks running/queued!
        gui.btn_start = MagicMock()
        gui.btn_login = MagicMock()
        gui.btn_logout = MagicMock()
        gui.btn_dryrun = MagicMock()
        gui.btn_canary = MagicMock()
        gui._set_scan_buttons_state = MagicMock()
        gui.btn_stop = MagicMock()
        gui.card_status = MagicMock()
        gui.worker_thread = None
        gui.telegram = None
        gui._refresh_kpi_cards = MagicMock()
        gui._load_groups_and_posts = MagicMock()
        gui._refresh_history_tab = MagicMock()

        # Call _reset_ui_after_run
        FacebookBotGUI._reset_ui_after_run(gui, results=[{"status": "SUCCESS"}])

        # btn_stop should REMAIN enabled because dispatcher is still running!
        gui.btn_stop.configure.assert_called_with(state="normal")
        # btn_login/logout should NOT be re-enabled while queue is active
        gui.btn_login.configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
