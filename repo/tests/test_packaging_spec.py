"""
Unit tests for Module 5: Packaging & Inno Setup Specification.

Verifies PyInstaller spec integrity, hidden imports coverage,
Inno Setup Non-Admin configurations, and build automation scripts.
"""

import ast
from pathlib import Path
import unittest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class TestPackagingSpec(unittest.TestCase):
    """Test suite validating packaging spec and installer scripts."""

    def test_spec_file_exists_and_parses(self):
        """facebook_bot.spec exists and is valid Python code."""
        spec_path = ROOT_DIR / "facebook_bot.spec"
        self.assertTrue(spec_path.is_file(), "facebook_bot.spec must exist at repository root")

        content = spec_path.read_text(encoding="utf-8")
        parsed = ast.parse(content)
        self.assertIsNotNone(parsed)

    def test_spec_hidden_imports_coverage(self):
        """All critical automation and update services are explicitly listed in hidden_imports."""
        spec_path = ROOT_DIR / "facebook_bot.spec"
        content = spec_path.read_text(encoding="utf-8")

        required_hidden_imports = [
            "services.selector_sync",
            "services.updater",
            "services.group_verifier",
            "services.group_joiner",
            "services.posting_plan",
            "services.state_manager",
            "services.circuit_breaker",
            "services.post_verifier",
            "config",
            "facebookbot",
        ]

        for mod in required_hidden_imports:
            self.assertIn(f'"{mod}"', content, f"Module '{mod}' must be declared in hidden_imports")

    def test_spec_windowed_mode(self):
        """Executable is configured in windowed mode (console=False)."""
        spec_path = ROOT_DIR / "facebook_bot.spec"
        content = spec_path.read_text(encoding="utf-8")
        self.assertIn("console=False", content)

    def test_inno_setup_script_conformance(self):
        """scripts/installer_windows.iss satisfies Non-Admin and silent update requirements."""
        iss_path = ROOT_DIR / "scripts" / "installer_windows.iss"
        self.assertTrue(iss_path.is_file(), "scripts/installer_windows.iss must exist")

        iss_content = iss_path.read_text(encoding="utf-8")

        # 1. Non-admin user space installation
        self.assertIn("PrivilegesRequired=lowest", iss_content)
        self.assertIn(r"{localappdata}\Programs\FacebookBot", iss_content)

        # 2. Silent update support
        self.assertIn("CloseApplications=force", iss_content)
        self.assertIn("OutputBaseFilename=FacebookBotSetup", iss_content)

    def test_build_windows_script_references(self):
        """scripts/build_windows.bat links PyInstaller and Inno Setup together."""
        bat_path = ROOT_DIR / "scripts" / "build_windows.bat"
        self.assertTrue(bat_path.is_file(), "scripts/build_windows.bat must exist")

        bat_content = bat_path.read_text(encoding="utf-8")
        self.assertIn("facebook_bot.spec", bat_content)
        self.assertIn("installer_windows.iss", bat_content)


if __name__ == "__main__":
    unittest.main()
