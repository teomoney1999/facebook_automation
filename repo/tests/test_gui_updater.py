"""
Unit tests for Module 4: UI Integration for Auto-Updater.

Verifies footer bar versioning, update check button interactions,
modal UpdateDialog rendering, and download progress reporting.
"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

try:
    import tkinter as tk
    TKINTER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    tk = None
    TKINTER_AVAILABLE = False

from repo.config import BotConfig
from repo.app_ui import FacebookBotGUI, UpdateDialog
from repo.services.updater import UpdateInfo


class TestGUIUpdaterLogic(unittest.TestCase):
    """Test suite for GUI Updater logic with mocked UI containers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_handle_update_result_logic_with_update(self):
        """Verifies _handle_update_result instantiates UpdateDialog when update is found."""
        mock_gui = mock.MagicMock()
        mock_gui.config = BotConfig(email="test@test.com", password="pass", app_version="2.0.0")
        mock_gui.btn_check_update = mock.MagicMock()
        mock_gui.update_status_lbl = mock.MagicMock()
        mock_gui.logger = mock.MagicMock()

        info = UpdateInfo(
            version="2.1.0",
            release_date="2026-12-01",
            changelog=["✨ New features"],
            download_url="https://example.com/setup.exe",
        )

        with mock.patch("repo.app_ui.UpdateDialog") as mock_dialog:
            FacebookBotGUI._handle_update_result(mock_gui, info, silent=False)
            mock_dialog.assert_called_once_with(mock_gui, info, logger=mock_gui.logger)
            mock_gui.btn_check_update.config.assert_called_with(state="normal")
            mock_gui.update_status_lbl.config.assert_called_with(text="")

    def test_handle_update_result_logic_no_update_silent(self):
        """Verifies silent mode does not pop up dialog when already at latest version."""
        mock_gui = mock.MagicMock()
        mock_gui.config = BotConfig(email="test@test.com", password="pass", app_version="2.0.0")
        mock_gui.btn_check_update = mock.MagicMock()
        mock_gui.update_status_lbl = mock.MagicMock()

        with mock.patch("repo.app_ui.messagebox") as mock_msg:
            FacebookBotGUI._handle_update_result(mock_gui, None, silent=True)
            if mock_msg:
                mock_msg.showinfo.assert_not_called()
            mock_gui.btn_check_update.config.assert_called_with(state="normal")


@unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
class TestGUIUpdaterWidgets(unittest.TestCase):
    """Test suite for actual Tkinter widget mounting when Tkinter is present."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name).resolve()
        self.env_file = self.tmp_path / ".env"
        self.env_file.write_text("EMAIL=test@test.com\nPASSWORD=secret\n", encoding="utf-8")
        (self.tmp_path / "json").mkdir(parents=True, exist_ok=True)
        (self.tmp_path / "json" / "links.json").write_text('{"groups": []}', encoding="utf-8")
        (self.tmp_path / "json" / "posts.json").write_text('{"posts": []}', encoding="utf-8")
        (self.tmp_path / "json" / "history.json").write_text('{}', encoding="utf-8")
        (self.tmp_path / "json" / "selectors.json").write_text('{"btn": ["//div"]}', encoding="utf-8")

        self.app = None

    def tearDown(self):
        if self.app:
            try:
                self.app.destroy()
            except Exception:
                pass
        self.temp_dir.cleanup()

    def test_footer_version_and_button_exist(self):
        """Footer contains the application version label and update trigger button."""
        self.app = FacebookBotGUI(config_path=str(self.env_file))
        self.assertTrue(hasattr(self.app, "footer_frame"))
        self.assertTrue(hasattr(self.app, "version_lbl"))
        self.assertTrue(hasattr(self.app, "btn_check_update"))
        self.assertIn("v2.0.0", self.app.version_lbl.cget("text"))
        self.assertEqual(self.app.btn_check_update.cget("text"), "🔄 Kiểm tra cập nhật")

    def test_update_dialog_rendering(self):
        """UpdateDialog mounts widgets cleanly."""
        self.app = FacebookBotGUI(config_path=str(self.env_file))
        info = UpdateInfo(
            version="2.1.0",
            release_date="2026-12-01",
            changelog=["🚀 Tính năng AI mới"],
            download_url="https://example.com/setup.exe",
            sha256="12345678",
        )
        dialog = UpdateDialog(self.app, info, logger=self.app.logger)
        try:
            self.assertEqual(dialog.title(), "🚀 Cập Nhật Ứng Dụng Mới")
            self.assertTrue(hasattr(dialog, "btn_update"))
        finally:
            dialog.destroy()


if __name__ == "__main__":
    unittest.main()
