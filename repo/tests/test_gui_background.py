"""
Unit Tests for Module 3: GUI Background & System Tray Operations.

Verifies:
1. BotWorkerThread applies headless configuration override.
2. FacebookBotGUI hide to background (withdraw) and restore from background (deiconify).
3. FacebookBotGUI _on_closing prompt handling (Yes -> minimize, Cancel -> abort, No -> terminate).
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig
from app_ui import BotWorkerThread


class TestBotWorkerThreadHeadless(unittest.TestCase):
    """Test suite for BotWorkerThread respecting headless settings."""

    def setUp(self):
        self.config = BotConfig(
            email="test@example.com",
            password="secret_password",
            headless=False,
            headless_mode="new",
            base_dir=REPO_DIR,
        )

    @patch("app_ui.FacebookBot")
    def test_worker_thread_applies_headless_true(self, mock_bot_cls):
        mock_bot_inst = MagicMock()
        mock_bot_inst.run.return_value = []
        mock_bot_cls.return_value = mock_bot_inst

        worker = BotWorkerThread(
            config=self.config,
            dry_run=True,
            headless=True,
        )
        worker.run()

        # Check config passed to FacebookBot
        passed_config = mock_bot_cls.call_args[1]["config"]
        self.assertTrue(passed_config.headless)

    @patch("app_ui.FacebookBot")
    def test_worker_thread_leaves_headless_when_none(self, mock_bot_cls):
        mock_bot_inst = MagicMock()
        mock_bot_inst.run.return_value = []
        mock_bot_cls.return_value = mock_bot_inst

        worker = BotWorkerThread(
            config=self.config,
            dry_run=True,
            headless=None,
        )
        worker.run()

        passed_config = mock_bot_cls.call_args[1]["config"]
        self.assertFalse(passed_config.headless)


class TestGUIBackgroundLifecycle(unittest.TestCase):
    """Test suite for GUI window minimization and close handling."""

    def test_hide_and_restore_methods(self):
        from app_ui import FacebookBotGUI

        gui = MagicMock(spec=FacebookBotGUI)
        # Mock methods from implementation
        gui.withdraw = MagicMock()
        gui.deiconify = MagicMock()
        gui.lift = MagicMock()
        gui.focus_force = MagicMock()
        gui.logger = MagicMock()
        gui.tray_icon = None

        # Call methods bound from FacebookBotGUI class
        FacebookBotGUI._hide_to_background(gui)
        gui.withdraw.assert_called_once()

        FacebookBotGUI._restore_from_background(gui)
        gui.deiconify.assert_called_once()
        gui.lift.assert_called_once()
        gui.focus_force.assert_called_once()

    def test_hide_buttons_presence_and_binding(self):
        """Verifies that both dashboard and footer hide buttons exist and are mapped in GUI."""
        from app_ui import FacebookBotGUI, TKINTER_AVAILABLE
        if not TKINTER_AVAILABLE:
            self.skipTest("Tkinter is not available in current test environment")

        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env_file = tmp_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")
            (tmp_path / "json").mkdir(parents=True, exist_ok=True)
            (tmp_path / "json" / "links.json").write_text('{"target_links": []}', encoding="utf-8")
            (tmp_path / "json" / "posts.json").write_text('{"content": "test"}', encoding="utf-8")
            (tmp_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            try:
                app.update_idletasks()
                self.assertTrue(hasattr(app, "btn_hide_bg"))
                self.assertTrue(hasattr(app, "btn_hide_bg_footer"))
                self.assertEqual(app.btn_hide_bg.cget("text"), "🕶️ ẨN XUỐNG NỀN")
                self.assertEqual(app.btn_hide_bg_footer.cget("text"), "🕶️ ẨN XUỐNG NỀN")
                self.assertEqual(app.btn_hide_bg.winfo_ismapped(), 1)
                self.assertEqual(app.btn_hide_bg_footer.winfo_ismapped(), 1)
            finally:
                app.destroy()

    @patch("app_ui.messagebox.askyesnocancel")
    def test_on_closing_yes_minimizes_to_background(self, mock_dialog):
        from app_ui import FacebookBotGUI

        mock_dialog.return_value = True  # User chose "Yes" (Hide to background)

        gui = MagicMock(spec=FacebookBotGUI)
        gui.worker_thread = MagicMock()
        gui.worker_thread.is_alive.return_value = True
        gui._hide_to_background = MagicMock()
        gui.destroy = MagicMock()

        FacebookBotGUI._on_closing(gui)

        gui._hide_to_background.assert_called_once()
        gui.destroy.assert_not_called()

    @patch("app_ui.messagebox.askyesnocancel")
    def test_on_closing_cancel_aborts_close(self, mock_dialog):
        from app_ui import FacebookBotGUI

        mock_dialog.return_value = None  # User chose "Cancel"

        gui = MagicMock(spec=FacebookBotGUI)
        gui.worker_thread = MagicMock()
        gui.worker_thread.is_alive.return_value = True
        gui._hide_to_background = MagicMock()
        gui.destroy = MagicMock()

        FacebookBotGUI._on_closing(gui)

        gui._hide_to_background.assert_not_called()
        gui.destroy.assert_not_called()

    @patch("app_ui.messagebox.askyesnocancel")
    def test_on_closing_no_terminates_and_destroys(self, mock_dialog):
        from app_ui import FacebookBotGUI

        mock_dialog.return_value = False  # User chose "No" (Stop & Exit)

        gui = MagicMock(spec=FacebookBotGUI)
        gui.worker_thread = MagicMock()
        gui.worker_thread.is_alive.return_value = True
        gui.stop_event = MagicMock()
        gui._hide_to_background = MagicMock()
        gui.destroy = MagicMock()
        gui.telegram = None

        FacebookBotGUI._on_closing(gui)

        gui.stop_event.set.assert_called_once()
        gui.worker_thread.join.assert_called_once()
        gui.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
