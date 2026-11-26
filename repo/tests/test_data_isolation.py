"""
Unit tests for Module 1: Data Directory Isolation & Persistent Storage.

Verifies that user data (.env, links.json, posts.json, history.json, FacebookBotProfile)
is safely decoupled from application bundle installation directory (Zero Data Loss on update).
"""

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from repo.config import (
    BotConfig,
    get_default_env_path,
    get_user_data_dir,
    _seed_template_files,
)


class TestDataIsolation(unittest.TestCase):
    """Verifies data directory resolution and template seeding in dev & frozen modes."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name).resolve()
        self.base_dir = self.tmp_path / "app_bundle"
        self.data_dir = self.tmp_path / "user_data"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dev_mode_defaults_to_base_dir(self):
        """In developer mode (sys.frozen not set), data_dir equals base_dir for 100% backward compatibility."""
        resolved = get_user_data_dir(base_dir=self.base_dir)
        self.assertEqual(resolved, self.base_dir)

        cfg = BotConfig(
            email="dev@example.com",
            password="secret_password",
            base_dir=self.base_dir,
        )
        self.assertEqual(cfg.data_dir, self.base_dir)
        self.assertEqual(cfg.logs_dir, self.base_dir / "logs")
        self.assertEqual(cfg.screenshots_dir, self.base_dir / "logs" / "screenshots")
        self.assertEqual(cfg.json_dir, self.base_dir / "json")
        self.assertEqual(cfg.profile_dir, self.base_dir / "FacebookBotProfile")
        self.assertTrue(cfg.data_dir.exists())
        self.assertTrue(cfg.json_dir.exists())
        self.assertTrue(cfg.logs_dir.exists())

    def test_environment_variable_override_takes_precedence(self):
        """FACEBOOK_BOT_DATA_DIR overrides any default path across all platforms."""
        custom_target = self.tmp_path / "custom_override_data"
        with mock.patch.dict(os.environ, {"FACEBOOK_BOT_DATA_DIR": str(custom_target)}):
            resolved = get_user_data_dir(base_dir=self.base_dir)
            self.assertEqual(resolved, custom_target)
            self.assertTrue(custom_target.exists())

            cfg = BotConfig(
                email="dev@example.com",
                password="secret_password",
                base_dir=self.base_dir,
            )
            self.assertEqual(cfg.data_dir, custom_target)
            self.assertEqual(cfg.json_dir, custom_target / "json")

    def test_frozen_mode_windows_localappdata(self):
        """When packaged as frozen exe on Windows, data_dir resolves to %LOCALAPPDATA%\\FacebookAutomationBot."""
        fake_appdata = self.tmp_path / "AppData" / "Local"
        fake_appdata.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": str(fake_appdata)}, clear=False):
            # Ensure env override is empty
            if "FACEBOOK_BOT_DATA_DIR" in os.environ:
                del os.environ["FACEBOOK_BOT_DATA_DIR"]

            resolved = get_user_data_dir(app_name="FacebookAutomationBot", base_dir=self.base_dir)
            expected = fake_appdata / "FacebookAutomationBot"
            self.assertEqual(resolved, expected)
            self.assertTrue(expected.exists())

    def test_frozen_mode_macos_app_support(self):
        """When packaged as frozen app on macOS, data_dir resolves to ~/Library/Application Support/FacebookAutomationBot."""
        fake_home = self.tmp_path / "home" / "user"
        fake_home.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.object(sys, "platform", "darwin"), \
             mock.patch.object(Path, "home", return_value=fake_home):
            if "FACEBOOK_BOT_DATA_DIR" in os.environ:
                del os.environ["FACEBOOK_BOT_DATA_DIR"]

            resolved = get_user_data_dir(app_name="FacebookAutomationBot", base_dir=self.base_dir)
            expected = fake_home / "Library" / "Application Support" / "FacebookAutomationBot"
            self.assertEqual(resolved, expected)
            self.assertTrue(expected.exists())

    def test_template_seeding_when_data_dir_differs(self):
        """Default JSON templates and starter .env are copied to data_dir if missing, without overwriting existing data."""
        # Setup source template files in app bundle
        src_json = self.base_dir / "json"
        src_json.mkdir(parents=True, exist_ok=True)
        (src_json / "selectors.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
        (src_json / "links.json").write_text('{"groups": []}', encoding="utf-8")
        (src_json / "posts.json").write_text('{"posts": []}', encoding="utf-8")
        (self.base_dir / ".env").write_text("EMAIL=bundle@test.com\nPASSWORD=bundle_pass\n", encoding="utf-8")

        # In user data directory, pre-exist a customized links.json
        dst_json = self.data_dir / "json"
        dst_json.mkdir(parents=True, exist_ok=True)
        (dst_json / "links.json").write_text('{"groups": [{"url": "https://custom.com"}]}', encoding="utf-8")

        # Initialize BotConfig pointing to separated data_dir
        cfg = BotConfig(
            email="user@test.com",
            password="secret_password",
            base_dir=self.base_dir,
            data_dir=self.data_dir,
        )

        # 1. Missing files were seeded
        self.assertTrue((self.data_dir / "json" / "selectors.json").exists())
        self.assertEqual(json.loads((self.data_dir / "json" / "selectors.json").read_text(encoding="utf-8")), {"version": "1.0.0"})
        self.assertTrue((self.data_dir / "json" / "posts.json").exists())
        self.assertTrue((self.data_dir / ".env").exists())

        # 2. Existing customized file was NOT overwritten (Zero Data Loss guarantee)
        user_links = json.loads((self.data_dir / "json" / "links.json").read_text(encoding="utf-8"))
        self.assertEqual(user_links["groups"][0]["url"], "https://custom.com")

    def test_get_default_env_path(self):
        """get_default_env_path prioritizes data_dir/.env over base_dir/.env when it exists."""
        (self.base_dir / ".env").write_text("BASE=1", encoding="utf-8")

        # Before data_dir has .env -> returns base_dir/.env
        p1 = get_default_env_path(base_dir=self.base_dir)
        self.assertEqual(p1, self.base_dir / ".env")

        # When data_dir has .env with override
        custom_data = self.tmp_path / "custom_data"
        custom_data.mkdir(parents=True, exist_ok=True)
        (custom_data / ".env").write_text("DATA=1", encoding="utf-8")

        with mock.patch.dict(os.environ, {"FACEBOOK_BOT_DATA_DIR": str(custom_data)}):
            p2 = get_default_env_path(base_dir=self.base_dir)
            self.assertEqual(p2, custom_data / ".env")


if __name__ == "__main__":
    unittest.main()
