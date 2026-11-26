"""
Unit tests for Module 3: Core Binary Updater Service.

Verifies SemVer parsing, background update checking, chunked downloading
with SHA-256 verification, and Inno Setup silent launcher execution.
"""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

from repo.services.updater import (
    UpdateInfo,
    check_for_updates,
    download_update,
    is_newer_version,
    launch_silent_installer,
    parse_version,
)


class MockHTTPResponse:
    """Mock urllib.request.urlopen response."""

    def __init__(self, data: bytes, headers: dict = None):
        self.data = data
        self.headers = headers or {"Content-Length": str(len(data))}
        self._io = io.BytesIO(data)

    def read(self, amt: int = -1) -> bytes:
        return self._io.read(amt)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class TestUpdaterService(unittest.TestCase):
    """Test suite for core updater service."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_version(self):
        """Verifies parsing of standard and prefixed version strings."""
        self.assertEqual(parse_version("2.1.0"), (2, 1, 0))
        self.assertEqual(parse_version("v2.1.0"), (2, 1, 0))
        self.assertEqual(parse_version("V3.0.5"), (3, 0, 5))
        self.assertEqual(parse_version("2.0"), (2, 0, 0))
        self.assertEqual(parse_version(""), (0, 0, 0))
        self.assertEqual(parse_version("invalid_text"), (0, 0, 0))

    def test_is_newer_version(self):
        """Verifies strict SemVer comparison logic."""
        self.assertTrue(is_newer_version("2.0.0", "2.1.0"))
        self.assertTrue(is_newer_version("2.0.0", "2.0.1"))
        self.assertTrue(is_newer_version("1.9.9", "2.0.0"))
        self.assertTrue(is_newer_version("v2.0.0", "v2.0.1"))
        self.assertFalse(is_newer_version("2.1.0", "2.1.0"))
        self.assertFalse(is_newer_version("2.1.0", "2.0.9"))
        self.assertFalse(is_newer_version("3.0.0", "2.9.9"))

    def test_check_for_updates_blank_url(self):
        """Blank or None URL returns None immediately without network requests."""
        self.assertIsNone(check_for_updates("2.0.0", ""))
        self.assertIsNone(check_for_updates("2.0.0", "   "))
        self.assertIsNone(check_for_updates("2.0.0", None))

    @mock.patch("urllib.request.urlopen")
    def test_check_for_updates_newer_available(self, mock_urlopen):
        """Detects newer version and parses changelog and platform-specific download url."""
        payload = {
            "version": "2.1.0",
            "release_date": "2026-12-01",
            "changelog": ["✨ New feature", "⚡ Faster speed"],
            "platforms": {
                "windows": {
                    "url": "https://example.com/FacebookBotSetup-v2.1.0.exe",
                    "sha256": "abcdef1234567890",
                }
            },
        }
        mock_urlopen.return_value = MockHTTPResponse(json.dumps(payload).encode("utf-8"))

        info = check_for_updates("2.0.0", "https://example.com/version.json")
        self.assertIsNotNone(info)
        self.assertIsInstance(info, UpdateInfo)
        self.assertEqual(info.version, "2.1.0")
        self.assertEqual(info.release_date, "2026-12-01")
        self.assertEqual(len(info.changelog), 2)
        self.assertEqual(info.download_url, "https://example.com/FacebookBotSetup-v2.1.0.exe")
        self.assertEqual(info.sha256, "abcdef1234567890")

    @mock.patch("urllib.request.urlopen")
    def test_check_for_updates_same_or_older_returns_none(self, mock_urlopen):
        """Returns None when remote version is equal or older than current version."""
        payload = {"version": "2.0.0", "url": "https://example.com/setup.exe"}
        mock_urlopen.return_value = MockHTTPResponse(json.dumps(payload).encode("utf-8"))

        info = check_for_updates("2.0.0", "https://example.com/version.json")
        self.assertIsNone(info)

        older_payload = {"version": "1.9.0", "url": "https://example.com/setup.exe"}
        mock_urlopen.return_value = MockHTTPResponse(json.dumps(older_payload).encode("utf-8"))
        self.assertIsNone(check_for_updates("2.0.0", "https://example.com/version.json"))

    @mock.patch("urllib.request.urlopen")
    def test_check_for_updates_network_error_fails_gracefully(self, mock_urlopen):
        """Network disconnect returns None without crashing."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection reset by peer")
        info = check_for_updates("2.0.0", "https://example.com/version.json")
        self.assertIsNone(info)

    @mock.patch("urllib.request.urlopen")
    def test_download_update_chunked_success(self, mock_urlopen):
        """Downloads data in chunks, reports progress, and verifies SHA-256."""
        dummy_binary = b"MZ\x90\x00InstallerMockBinaryDataContent" * 100
        expected_hash = hashlib.sha256(dummy_binary).hexdigest()
        dest_file = self.tmp_path / "installer.exe"

        mock_urlopen.return_value = MockHTTPResponse(dummy_binary)
        progress_calls = []

        def on_progress(downloaded, total):
            progress_calls.append((downloaded, total))

        success = download_update(
            "https://example.com/setup.exe",
            dest_path=dest_file,
            expected_sha256=expected_hash,
            progress_callback=on_progress,
        )

        self.assertTrue(success)
        self.assertTrue(dest_file.is_file())
        self.assertEqual(dest_file.read_bytes(), dummy_binary)
        self.assertTrue(len(progress_calls) > 0)
        self.assertEqual(progress_calls[-1][0], len(dummy_binary))

    @mock.patch("urllib.request.urlopen")
    def test_download_update_sha256_mismatch_cleans_up(self, mock_urlopen):
        """Corrupted download with hash mismatch returns False and deletes partial file."""
        dummy_binary = b"CorruptedFilePayload"
        wrong_hash = "0000000000000000000000000000000000000000000000000000000000000000"
        dest_file = self.tmp_path / "installer.exe"

        mock_urlopen.return_value = MockHTTPResponse(dummy_binary)

        success = download_update(
            "https://example.com/setup.exe",
            dest_path=dest_file,
            expected_sha256=wrong_hash,
        )

        self.assertFalse(success)
        self.assertFalse(dest_file.exists())
        self.assertFalse(dest_file.with_suffix(".part").exists())

    def test_launch_silent_installer_missing_file(self):
        """Non-existent installer path returns None."""
        res = launch_silent_installer(self.tmp_path / "does_not_exist.exe")
        self.assertIsNone(res)

    @mock.patch("subprocess.Popen")
    def test_launch_silent_installer_invokes_correct_inno_setup_flags(self, mock_popen):
        """Spawns Inno Setup with /VERYSILENT /SUPPRESSMSGBOXES /NORESTART arguments."""
        installer_file = self.tmp_path / "FacebookBotSetup.exe"
        installer_file.write_bytes(b"dummy_exe")

        mock_proc = mock.MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        proc = launch_silent_installer(installer_file, exit_current=False)
        self.assertIsNotNone(proc)
        self.assertEqual(proc.pid, 12345)

        expected_cmd = [str(installer_file), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        mock_popen.assert_called_once_with(expected_cmd, close_fds=True)

    def test_repo_version_json_and_config_default_link(self):
        """Verifies root version.json schema validity and BotConfig default update_url binding."""
        from repo.config import APP_VERSION, DEFAULT_UPDATE_CHECK_URL, BotConfig

        # 1. Verify DEFAULT_UPDATE_CHECK_URL constant
        self.assertIn(
            "raw.githubusercontent.com/teomoney1999/facebook_automation/main/version.json",
            DEFAULT_UPDATE_CHECK_URL,
        )

        # 2. Verify BotConfig default fallback
        cfg_default = BotConfig(email="test@test.com", password="pass")
        self.assertEqual(cfg_default.update_url, DEFAULT_UPDATE_CHECK_URL)

        # 3. Verify root version.json file exists and adheres to updater schema
        root_dir = Path(__file__).resolve().parent.parent.parent
        version_file = root_dir / "version.json"
        self.assertTrue(version_file.is_file(), f"version.json missing at {version_file}")

        data = json.loads(version_file.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertEqual(data.get("version"), APP_VERSION)
        self.assertTrue(bool(data.get("release_date")))
        self.assertIsInstance(data.get("changelog"), list)
        self.assertTrue(len(data["changelog"]) > 0)
        self.assertIn("platforms", data)
        self.assertIn("windows", data["platforms"])
        win_plat = data["platforms"]["windows"]
        self.assertIn("url", win_plat)
        self.assertTrue(
            win_plat["url"].startswith(
                "https://github.com/teomoney1999/facebook_automation/releases/download/v"
            )
        )
        self.assertTrue(win_plat["url"].endswith("FacebookBotSetup.exe"))


if __name__ == "__main__":
    unittest.main()
