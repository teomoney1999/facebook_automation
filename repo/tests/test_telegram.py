"""
Unit Tests for Telegram Remote Control Service (repo/services/telegram_controller.py).
Tests 100% of controller logic, admin authorization, command parsing, and alert dispatching
using mock HTTP requests (zero network calls, zero external library requirements).
"""

import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

import sys
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from services.telegram_controller import TelegramController


class MockHTTPResponse:
    """Mock for urllib.request.urlopen response context manager."""

    def __init__(self, data: dict, status: int = 200):
        self.data = data
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def read(self) -> bytes:
        return json.dumps(self.data).encode("utf-8")


class TestTelegramController(unittest.TestCase):

    def setUp(self):
        self.logger = logging.getLogger("TestTelegramController")
        self.logger.setLevel(logging.DEBUG)
        self.token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
        self.admin_id = "987654321"

    def test_configuration_properties(self):
        """Verify is_configured and busy state flags."""
        unconfigured = TelegramController(token="", admin_id="")
        self.assertFalse(unconfigured.is_configured)
        self.assertFalse(unconfigured.start_polling())

        half_configured = TelegramController(token=self.token, admin_id="")
        self.assertFalse(half_configured.is_configured)

        configured = TelegramController(token=self.token, admin_id=self.admin_id)
        self.assertTrue(configured.is_configured)
        self.assertFalse(configured.is_running)

        configured.set_busy(True)
        self.assertTrue(configured._is_busy)
        configured.set_busy(False)
        self.assertFalse(configured._is_busy)

    @mock.patch("urllib.request.urlopen")
    def test_send_message_success_and_failure(self, mock_urlopen):
        """Verify send_message successfully posts payload and handles errors gracefully."""
        controller = TelegramController(token=self.token, admin_id=self.admin_id, logger=self.logger)

        # 1. Success case
        mock_urlopen.return_value = MockHTTPResponse({"ok": True, "result": {"message_id": 1}})
        success = controller.send_message("Xin chào Admin!")
        self.assertTrue(success)

        # 2. Telegram API returns error
        mock_urlopen.return_value = MockHTTPResponse({"ok": False, "description": "Bad Request: chat not found"})
        failed = controller.send_message("Lỗi chat!")
        self.assertFalse(failed)

        # 3. HTTP Network exception
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        err_handled = controller.send_message("Test network down")
        self.assertFalse(err_handled)

    @mock.patch("urllib.request.urlopen")
    def test_send_photo_with_file_and_fallback(self, mock_urlopen):
        """Verify send_photo uploads binary multipart data, or falls back when file missing."""
        controller = TelegramController(token=self.token, admin_id=self.admin_id, logger=self.logger)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"\x89PNG\r\n\x1a\nFakeImageData")
            tmp_path = tmp.name

        try:
            # Case 1: Valid photo file upload
            mock_urlopen.return_value = MockHTTPResponse({"ok": True, "result": {"photo": []}})
            res = controller.send_photo(tmp_path, caption="Ảnh chụp màn hình lỗi")
            self.assertTrue(res)

            # Case 2: Missing file fallback to message
            mock_urlopen.reset_mock()
            mock_urlopen.return_value = MockHTTPResponse({"ok": True})
            res_missing = controller.send_photo("/path/to/non_existent.png", caption="Ảnh không tồn tại")
            self.assertTrue(res_missing)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @mock.patch.object(TelegramController, "send_message")
    def test_admin_whitelist_authorization(self, mock_send_msg):
        """Verify that requests from non-admin IDs are rejected with access denied."""
        on_start_called = False

        def mock_start(dry_run, batch_size):
            nonlocal on_start_called
            on_start_called = True

        controller = TelegramController(
            token=self.token,
            admin_id=self.admin_id,
            on_start=mock_start,
            logger=self.logger,
        )

        # Unauthorized update (attacker ID: 111222333)
        unauthorized_update = {
            "update_id": 100,
            "message": {
                "message_id": 1,
                "from": {"id": 111222333, "username": "intruder"},
                "chat": {"id": 111222333},
                "text": "/start",
            },
        }

        controller._handle_update(unauthorized_update)
        self.assertFalse(on_start_called)
        mock_send_msg.assert_called_once()
        self.assertIn("Truy cập bị từ chối", mock_send_msg.call_args[0][0])

    @mock.patch.object(TelegramController, "send_message")
    def test_command_handling_start_dryrun_stop_status_help(self, mock_send_msg):
        """Verify command parsing for /start, /dryrun, /status, /stop, and /help."""
        start_calls = []
        stop_called = False

        def mock_start(dry_run, batch):
            start_calls.append((dry_run, batch))

        def mock_stop():
            nonlocal stop_called
            stop_called = True

        def mock_status():
            return {
                "total_groups_tracked": 15,
                "total_successful_posts": 8,
                "active_cooldown_count": 3,
                "last_posted_group": "https://facebook.com/groups/test",
                "last_posted_time": "2026-11-21 16:00:00",
            }

        controller = TelegramController(
            token=self.token,
            admin_id=self.admin_id,
            on_start=mock_start,
            on_stop=mock_stop,
            get_status=mock_status,
            logger=self.logger,
        )

        def make_update(text):
            return {
                "update_id": 200,
                "message": {
                    "message_id": 2,
                    "from": {"id": int(self.admin_id)},
                    "chat": {"id": int(self.admin_id)},
                    "text": text,
                },
            }

        # 1. /start 7
        controller._handle_update(make_update("/start 7"))
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(start_calls[0], (False, 7))

        # 2. /start when busy
        controller.set_busy(True)
        controller._handle_update(make_update("/start 10"))
        self.assertEqual(len(start_calls), 1)  # Did not invoke again
        self.assertIn("đang thực thi", mock_send_msg.call_args[0][0])
        controller.set_busy(False)

        # 3. /dryrun
        controller._handle_update(make_update("/dryrun"))
        self.assertEqual(len(start_calls), 2)
        self.assertEqual(start_calls[1], (True, None))

        # 4. /status
        controller._handle_update(make_update("/status"))
        self.assertIn("BÁO CÁO TRẠNG THÁI BOT", mock_send_msg.call_args[0][0])
        self.assertIn("Tổng nhóm theo dõi: 15", mock_send_msg.call_args[0][0])

        # 5. /stop
        controller._handle_update(make_update("/stop"))
        self.assertTrue(stop_called)
        self.assertIn("DỪNG KHẨN CẤP", mock_send_msg.call_args[0][0])

        # 6. /help
        controller._handle_update(make_update("/help"))
        self.assertIn("HƯỚNG DẪN ĐIỀU KHIỂN TỪ XA", mock_send_msg.call_args[0][0])

    @mock.patch.object(TelegramController, "send_message")
    @mock.patch.object(TelegramController, "send_photo")
    def test_notification_dispatchers(self, mock_send_photo, mock_send_msg):
        """Verify notification helper functions for post outcome, circuit breaker, summary."""
        controller = TelegramController(token=self.token, admin_id=self.admin_id, logger=self.logger)

        # 1. Post result without screenshot
        controller.notify_post_result("https://facebook.com/groups/1", "SUCCESS", "Đăng thành công")
        mock_send_msg.assert_called()
        self.assertIn("SUCCESS", mock_send_msg.call_args[0][0])

        # 2. Circuit breaker alert
        controller.notify_circuit_breaker("Too many failures")
        self.assertIn("CIRCUIT BREAKER", mock_send_msg.call_args[0][0])

        # 3. Session summary alert
        controller.notify_session_summary([
            {"status": "SUCCESS"},
            {"status": "ACTION_BLOCKED"},
            {"status": "FAILED"},
        ])
        self.assertIn("BÁO CÁO KẾT THÚC PHIÊN", mock_send_msg.call_args[0][0])
        self.assertIn("Tổng số bài xử lý : <b>3</b>", mock_send_msg.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
