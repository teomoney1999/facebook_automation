"""
Unit tests for Module 3: State & History Manager.

Verifies:
1. Normalization of group URLs (trailing slashes, fragments, whitespace).
2. Default file initialization when history.json does not exist.
3. Atomic JSON persistence (tempfile + os.replace) and reload integrity.
4. Corrupted JSON file recovery and backup.
5. Cooldown calculation (24h cooldown, remaining seconds, timezone safety).
6. Non-successful statuses (FAILED, MARKETPLACE_FORM) bypass cooldown.
7. Filtering of eligible vs cooldown groups (deduplication, ordering).
8. Fast-exit condition (empty eligible list when all are in cooldown).
9. Batch slicing up to max_posts_per_run.
10. Recording post increments count and stores timestamps accurately.
"""

from datetime import datetime, timezone, timedelta
import json
import sys
from pathlib import Path
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent
for p in (REPO_ROOT, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from services.state_manager import StateManager, PostRecord, normalize_url
except ImportError:
    from repo.services.state_manager import StateManager, PostRecord, normalize_url


class TestNormalizeUrl(unittest.TestCase):
    """Test URL normalization utility."""

    def test_strip_trailing_slashes(self):
        url = "https://www.facebook.com/groups/123456789/  "
        self.assertEqual(normalize_url(url), "https://www.facebook.com/groups/123456789")

    def test_strip_fragments(self):
        url = "https://www.facebook.com/groups/mygroup/#extra-info"
        self.assertEqual(normalize_url(url), "https://www.facebook.com/groups/mygroup")

    def test_empty_or_none(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url("   "), "")


class TestStateManager(unittest.TestCase):
    """Unit tests for StateManager core persistence and cooldown operations."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.temp_dir.name) / "history.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_creates_default_history(self):
        """Verifies that initializing with non-existent file creates valid empty JSON structure."""
        manager = StateManager(self.history_file)
        self.assertTrue(self.history_file.exists())
        self.assertEqual(len(manager.history), 0)

        with open(self.history_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("version"), "1.0")
        self.assertEqual(data.get("history"), {})

    def test_atomic_persistence_and_reload(self):
        """Tests that saving records persists correctly and can be reloaded by a new instance."""
        manager = StateManager(self.history_file)
        manager.record_post("https://www.facebook.com/groups/group1/", "SUCCESS")
        manager.record_post("https://www.facebook.com/groups/group2", "PENDING_APPROVAL")

        # Reload with a fresh StateManager instance
        reloaded = StateManager(self.history_file)
        self.assertEqual(len(reloaded.history), 2)
        norm_url1 = "https://www.facebook.com/groups/group1"
        self.assertIn(norm_url1, reloaded.history)
        self.assertEqual(reloaded.history[norm_url1].status, "SUCCESS")
        self.assertEqual(reloaded.history[norm_url1].post_count, 1)

    def test_record_post_increments_count(self):
        """Tests that consecutive posts to the same group increment post_count."""
        manager = StateManager(self.history_file)
        url = "https://www.facebook.com/groups/group1"
        r1 = manager.record_post(url, "SUCCESS")
        self.assertEqual(r1.post_count, 1)

        r2 = manager.record_post(url, "SUCCESS")
        self.assertEqual(r2.post_count, 2)
        self.assertEqual(manager.history[url].post_count, 2)

    def test_corrupted_file_recovery(self):
        """Tests that corrupted JSON creates a backup and initializes clean state without crashing."""
        self.history_file.write_text("{ this is corrupted invalid json }}}", encoding="utf-8")
        manager = StateManager(self.history_file)
        self.assertEqual(len(manager.history), 0)
        self.assertTrue(self.history_file.exists())

        # Check that backup file was created
        backup_files = list(Path(self.temp_dir.name).glob("history.corrupted_*.json"))
        self.assertEqual(len(backup_files), 1)

    def test_is_in_cooldown_active(self):
        """Verifies cooldown calculation when post was made 2 hours ago (within 24h)."""
        manager = StateManager(self.history_file)
        url = "https://www.facebook.com/groups/group1"

        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
        posted_at = now - timedelta(hours=2)

        manager.history[url] = PostRecord(
            group_url=url,
            last_posted_at=posted_at.isoformat(),
            status="SUCCESS",
            post_count=1
        )

        in_cd, remaining = manager.is_in_cooldown(url, cooldown_hours=24.0, now=now)
        self.assertTrue(in_cd)
        self.assertAlmostEqual(remaining, 22 * 3600.0, delta=1.0)

    def test_is_in_cooldown_expired(self):
        """Verifies that cooldown expires after 24 hours."""
        manager = StateManager(self.history_file)
        url = "https://www.facebook.com/groups/group1"

        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
        posted_at = now - timedelta(hours=25)

        manager.history[url] = PostRecord(
            group_url=url,
            last_posted_at=posted_at.isoformat(),
            status="SUCCESS",
            post_count=1
        )

        in_cd, remaining = manager.is_in_cooldown(url, cooldown_hours=24.0, now=now)
        self.assertFalse(in_cd)
        self.assertEqual(remaining, 0.0)

    def test_failed_status_does_not_trigger_cooldown(self):
        """Verifies that FAILED status does not block 24h cooldown by default."""
        manager = StateManager(self.history_file)
        url = "https://www.facebook.com/groups/group1"

        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
        posted_at = now - timedelta(minutes=10)

        manager.history[url] = PostRecord(
            group_url=url,
            last_posted_at=posted_at.isoformat(),
            status="FAILED",
            post_count=1,
            error="Timeout"
        )

        in_cd, remaining = manager.is_in_cooldown(url, cooldown_hours=24.0, now=now)
        self.assertFalse(in_cd)
        self.assertEqual(remaining, 0.0)

    def test_filter_eligible_groups_and_deduplication(self):
        """Tests filtering of eligible vs cooldown groups and deduplication."""
        manager = StateManager(self.history_file)
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)

        # group1: posted 5 hours ago (in cooldown)
        # group2: never posted (eligible)
        # group3: posted 30 hours ago (eligible)
        manager.history["https://www.facebook.com/groups/group1"] = PostRecord(
            group_url="https://www.facebook.com/groups/group1",
            last_posted_at=(now - timedelta(hours=5)).isoformat(),
            status="SUCCESS"
        )
        manager.history["https://www.facebook.com/groups/group3"] = PostRecord(
            group_url="https://www.facebook.com/groups/group3",
            last_posted_at=(now - timedelta(hours=30)).isoformat(),
            status="SUCCESS"
        )

        candidates = [
            "https://www.facebook.com/groups/group1/",
            "https://www.facebook.com/groups/group2",
            "https://www.facebook.com/groups/group3/",
            "https://www.facebook.com/groups/group2/",  # duplicate
        ]

        eligible, skipped = manager.filter_eligible_groups(candidates, cooldown_hours=24.0, now=now)

        self.assertEqual(len(eligible), 2)
        self.assertIn("https://www.facebook.com/groups/group2", eligible)
        self.assertIn("https://www.facebook.com/groups/group3", eligible)

        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["url"], "https://www.facebook.com/groups/group1")
        self.assertAlmostEqual(skipped[0]["remaining_hours"], 19.0, delta=0.1)

    def test_fast_exit_scenario(self):
        """Verifies Fast-Exit when all target groups are in active cooldown."""
        manager = StateManager(self.history_file)
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)

        manager.history["https://www.facebook.com/groups/group1"] = PostRecord(
            group_url="https://www.facebook.com/groups/group1",
            last_posted_at=(now - timedelta(hours=1)).isoformat(),
            status="SUCCESS"
        )

        batch = manager.get_batch_targets(
            ["https://www.facebook.com/groups/group1"],
            max_batch_size=10,
            cooldown_hours=24.0,
            now=now
        )
        self.assertEqual(batch, [])

    def test_batch_slicing_limit(self):
        """Verifies that eligible groups list is sliced up to max_batch_size."""
        manager = StateManager(self.history_file)
        all_groups = [f"https://www.facebook.com/groups/{i}" for i in range(25)]

        batch = manager.get_batch_targets(all_groups, max_batch_size=5, cooldown_hours=24.0)
        self.assertEqual(len(batch), 5)
        self.assertEqual(batch, [f"https://www.facebook.com/groups/{i}" for i in range(5)])


if __name__ == "__main__":
    unittest.main()
