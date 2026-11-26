"""
Unit tests for Module 2: Hot-Patch Selectors Synchronization Service.

Verifies zero-downtime, fail-safe background updates of selectors.json.
"""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

from repo.services.selector_sync import (
    MAX_SELECTORS_PAYLOAD_BYTES,
    sync_selectors,
    validate_selectors_data,
)


class MockHTTPResponse:
    """Mock urllib.request.urlopen response context manager."""

    def __init__(self, data: bytes, status: int = 200):
        self.data = data
        self.status = status
        self._io = io.BytesIO(data)

    def read(self, amt: int = -1) -> bytes:
        return self._io.read(amt)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class TestSelectorSync(unittest.TestCase):
    """Test suite for hot-patch selector synchronization."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name).resolve()
        self.local_selectors = self.tmp_path / "selectors.json"
        self.dummy_valid_selectors = {
            "post_button": ["//div[@aria-label='Post']", "//button[contains(., 'Post')]"],
            "create_post": ["//div[@role='textbox']"],
        }
        self.local_selectors.write_text(json.dumps(self.dummy_valid_selectors), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_or_blank_url(self):
        """Blank or None URL returns False immediately with no network call."""
        self.assertFalse(sync_selectors("", self.local_selectors))
        self.assertFalse(sync_selectors("   ", self.local_selectors))
        self.assertFalse(sync_selectors(None, self.local_selectors))

    @mock.patch("urllib.request.urlopen")
    def test_successful_update_when_content_changes(self, mock_urlopen):
        """New valid selectors from remote URL atomically replace the local file."""
        new_selectors = {
            "_version": "2.1.0",
            "post_button": ["//div[@aria-label='Đăng bài mới']"],
            "create_post": ["//div[@role='textbox' and @contenteditable='true']"],
        }
        payload = json.dumps(new_selectors).encode("utf-8")
        mock_urlopen.return_value = MockHTTPResponse(payload)

        updated = sync_selectors("https://example.com/selectors.json", self.local_selectors)
        self.assertTrue(updated)

        # Verify disk content is updated
        saved_data = json.loads(self.local_selectors.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["_version"], "2.1.0")
        self.assertEqual(saved_data["post_button"], ["//div[@aria-label='Đăng bài mới']"])

    @mock.patch("urllib.request.urlopen")
    def test_no_update_when_content_hash_matches(self, mock_urlopen):
        """If remote content is identical to local file, return False (0 redundant disk writes)."""
        current_bytes = self.local_selectors.read_bytes()
        mock_urlopen.return_value = MockHTTPResponse(current_bytes)

        updated = sync_selectors("https://example.com/selectors.json", self.local_selectors)
        self.assertFalse(updated)

    @mock.patch("urllib.request.urlopen")
    def test_corrupted_json_rejected_safely(self, mock_urlopen):
        """Corrupted JSON payload is rejected and existing local file is untouched."""
        original_content = self.local_selectors.read_text(encoding="utf-8")
        mock_urlopen.return_value = MockHTTPResponse(b"{ invalid json string ...")

        updated = sync_selectors("https://example.com/selectors.json", self.local_selectors)
        self.assertFalse(updated)
        # Original file is preserved 100%
        self.assertEqual(self.local_selectors.read_text(encoding="utf-8"), original_content)

    @mock.patch("urllib.request.urlopen")
    def test_invalid_schema_rejected_safely(self, mock_urlopen):
        """Payload not matching dict of non-empty lists is rejected."""
        original_content = self.local_selectors.read_text(encoding="utf-8")
        # Empty list for component
        mock_urlopen.return_value = MockHTTPResponse(json.dumps({"btn": []}).encode("utf-8"))
        self.assertFalse(sync_selectors("https://example.com/selectors.json", self.local_selectors))

        # String instead of list
        mock_urlopen.return_value = MockHTTPResponse(json.dumps({"btn": "//xpath"}).encode("utf-8"))
        self.assertFalse(sync_selectors("https://example.com/selectors.json", self.local_selectors))

        # Array instead of dict
        mock_urlopen.return_value = MockHTTPResponse(json.dumps(["not", "a", "dict"]).encode("utf-8"))
        self.assertFalse(sync_selectors("https://example.com/selectors.json", self.local_selectors))

        self.assertEqual(self.local_selectors.read_text(encoding="utf-8"), original_content)

    @mock.patch("urllib.request.urlopen")
    def test_oversized_payload_rejected(self, mock_urlopen):
        """Payload exceeding 512KB is rejected to prevent memory exhaustion."""
        large_payload = b"x" * (MAX_SELECTORS_PAYLOAD_BYTES + 100)
        mock_urlopen.return_value = MockHTTPResponse(large_payload)

        updated = sync_selectors("https://example.com/selectors.json", self.local_selectors)
        self.assertFalse(updated)

    @mock.patch("urllib.request.urlopen")
    def test_network_error_handled_gracefully(self, mock_urlopen):
        """Network disconnect or 404 returns False without raising exception."""
        original_content = self.local_selectors.read_text(encoding="utf-8")
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        updated = sync_selectors("https://example.com/selectors.json", self.local_selectors)
        self.assertFalse(updated)
        self.assertEqual(self.local_selectors.read_text(encoding="utf-8"), original_content)

    def test_validate_selectors_data(self):
        """Verifies schema validation helper directly."""
        self.assertTrue(validate_selectors_data({"btn": ["//div"]}))
        self.assertTrue(validate_selectors_data({"_version": "1.0", "btn": ["//div"]}))
        self.assertFalse(validate_selectors_data({}))
        self.assertFalse(validate_selectors_data({"btn": []}))
        self.assertFalse(validate_selectors_data({"btn": [""]}))
        self.assertFalse(validate_selectors_data("not a dict"))


if __name__ == "__main__":
    unittest.main()
