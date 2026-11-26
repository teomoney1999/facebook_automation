import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from helpers.common_helpers import (
    ensure_group_targets_file,
    extract_facebook_group_urls,
    load_group_targets,
    save_group_targets,
)


class TestLinksAutoInit(unittest.TestCase):
    """Test suite for auto-creation of links.json and robust group extraction."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.links_file = os.path.join(self.test_dir, "json", "links.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_ensure_group_targets_file_creates_empty_schema(self):
        """Verify that ensure_group_targets_file automatically creates links.json with contract schema if missing."""
        self.assertFalse(os.path.exists(self.links_file))
        res = ensure_group_targets_file(self.links_file)
        self.assertTrue(res)
        self.assertTrue(os.path.exists(self.links_file))

        with open(self.links_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("groups", data)
        self.assertIn("target_links", data)
        self.assertEqual(data["groups"], [])
        self.assertEqual(data["target_links"], [])

        # load_group_targets should safely return empty list
        targets = load_group_targets(self.links_file)
        self.assertEqual(targets, [])

    def test_ensure_group_targets_file_preserves_existing_data(self):
        """Verify that ensure_group_targets_file does not overwrite pre-existing links."""
        initial_groups = [
            {"url": "https://www.facebook.com/groups/existing_dev", "name": "Existing Dev"}
        ]
        save_group_targets(self.links_file, initial_groups)

        # Call ensure again
        res = ensure_group_targets_file(self.links_file)
        self.assertTrue(res)

        targets = load_group_targets(self.links_file)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["url"], "https://www.facebook.com/groups/existing_dev")
        self.assertEqual(targets[0]["name"], "Existing Dev")

    def test_extract_facebook_group_urls_diverse_formats(self):
        """Verify extraction handles desktop, mobile, missing https, standalone IDs, and filters system routes."""
        raw_input = """
        1. https://www.facebook.com/groups/dev_full/
        2. https://facebook.com/groups/dev_no_www
        3. facebook.com/groups/dev_no_scheme
        4. https://m.facebook.com/groups/mobile_group/
        5. https://web.facebook.com/groups/web_group?ref=share&mibextid=NSMWBT
        6. https://facebook.com/groups/joins/ (system route - should be filtered)
        7. https://facebook.com/groups/feed (system route - should be filtered)
        8. 1523459548029885
        9. Rác: https://google.com, https://facebook.com/username
        """
        urls = extract_facebook_group_urls(raw_input)

        expected = [
            "https://www.facebook.com/groups/dev_full",
            "https://www.facebook.com/groups/dev_no_www",
            "https://www.facebook.com/groups/dev_no_scheme",
            "https://www.facebook.com/groups/mobile_group",
            "https://www.facebook.com/groups/web_group",
            "https://www.facebook.com/groups/1523459548029885",
        ]
        self.assertEqual(urls, expected)

    def test_save_group_targets_contract_schema(self):
        """Verify save_group_targets strictly adheres to the contract schema."""
        groups = [
            {
                "url": "https://www.facebook.com/groups/test_group",
                "name": "Test Group",
                "members": "10K",
                "privacy": "Công khai",
                "membership": "ALREADY_JOINED",
            }
        ]
        save_group_targets(self.links_file, groups)

        with open(self.links_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("groups", data)
        self.assertIn("target_links", data)
        self.assertEqual(len(data["groups"]), 1)
        self.assertEqual(data["target_links"], ["https://www.facebook.com/groups/test_group"])
        g = data["groups"][0]
        self.assertEqual(g["url"], "https://www.facebook.com/groups/test_group")
        self.assertEqual(g["name"], "Test Group")
        self.assertEqual(g["members"], "10K")
        self.assertEqual(g["privacy"], "Công khai")
        self.assertEqual(g["membership"], "ALREADY_JOINED")


if __name__ == "__main__":
    unittest.main()
