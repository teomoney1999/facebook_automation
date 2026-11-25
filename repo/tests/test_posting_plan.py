"""
Unit Tests for Posting Plan & Round-Robin Allocation (repo/services/posting_plan.py).
"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import sys
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from services.posting_plan import (
    PostItem,
    PostingPlan,
    build_round_robin_plan,
    load_posts_collection,
    save_posts_collection,
)
from services.state_manager import StateManager


class TestPostingPlan(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_backward_compatibility_legacy_posts_json(self):
        """Verify legacy format {"content": "...", "images": [...]} converts automatically."""
        legacy_file = self.base_path / "posts.json"
        legacy_file.write_text('{"content": "Nội dung cũ", "images": ["/img1.jpg"]}', encoding="utf-8")

        posts = load_posts_collection(legacy_file)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].content, "Nội dung cũ")
        self.assertEqual(posts[0].images, ["/img1.jpg"])
        self.assertTrue(posts[0].is_selected)
        self.assertEqual(posts[0].status, "ready")

    def test_save_and_load_multi_posts_collection(self):
        """Verify saving and re-loading multiple posts maintains fields and legacy top-level keys."""
        posts_file = self.base_path / "posts.json"
        items = [
            PostItem(id="p1", name="Bài 1", content="Nội dung 1", images=["/a.jpg"], is_selected=True, status="ready"),
            PostItem(id="p2", name="Bài 2", content="Nội dung 2", images=[], is_selected=False, status="draft"),
        ]
        save_posts_collection(posts_file, items)

        reloaded = load_posts_collection(posts_file)
        self.assertEqual(len(reloaded), 2)
        self.assertEqual(reloaded[0].name, "Bài 1")
        self.assertEqual(reloaded[1].name, "Bài 2")
        self.assertFalse(reloaded[1].is_selected)

    def test_round_robin_allocation_three_posts_nine_groups(self):
        """Verify 3 posts mapped to 9 groups creates 9 tasks cycling 1, 2, 3 (UX04)."""
        posts = [
            PostItem(id="p1", name="Bài 01", content="Text 1", images=["/img1.jpg"], is_selected=True, status="ready"),
            PostItem(id="p2", name="Bài 02", content="Text 2", images=["/img2.jpg"], is_selected=True, status="ready"),
            PostItem(id="p3", name="Bài 03", content="Text 3", images=["/img3.jpg"], is_selected=True, status="ready"),
        ]
        groups = [f"https://facebook.com/groups/{i}" for i in range(1, 10)]

        plan = build_round_robin_plan(
            posts=posts,
            target_groups=groups,
            state_manager=None,
            max_posts_per_run=10,
            cooldown_hours=24,
        )

        self.assertEqual(plan.total_runs, 9)
        self.assertEqual(plan.selected_posts_count, 3)
        self.assertEqual(plan.eligible_groups_count, 9)
        self.assertEqual(plan.skipped_cooldown_count, 0)

        # Check cyclic assignment: p1, p2, p3, p1, p2, p3, p1, p2, p3
        expected_ids = ["p1", "p2", "p3", "p1", "p2", "p3", "p1", "p2", "p3"]
        actual_ids = [t.post_id for t in plan.tasks]
        self.assertEqual(actual_ids, expected_ids)

    def test_cooldown_filtering_skips_ineligible_groups(self):
        """Verify groups in 24h cooldown are skipped prior to allocation."""
        posts = [
            PostItem(id="p1", name="Bài 1", content="Text", images=[], is_selected=True, status="ready"),
        ]
        groups = [
            "https://facebook.com/groups/ready_1",
            "https://facebook.com/groups/cooldown_1",
            "https://facebook.com/groups/ready_2",
        ]

        mock_state = mock.MagicMock(spec=StateManager)
        def mock_is_in_cd(url, cooldown_hours):
            return (True, 3600.0) if "cooldown" in url else (False, 0.0)
        mock_state.is_in_cooldown.side_effect = mock_is_in_cd

        plan = build_round_robin_plan(
            posts=posts,
            target_groups=groups,
            state_manager=mock_state,
            max_posts_per_run=10,
            cooldown_hours=24,
        )

        self.assertEqual(plan.total_runs, 2)
        self.assertEqual(plan.skipped_cooldown_count, 1)
        self.assertEqual(plan.tasks[0].group_url, "https://facebook.com/groups/ready_1")
        self.assertEqual(plan.tasks[1].group_url, "https://facebook.com/groups/ready_2")

    def test_batch_limit_truncates_plan(self):
        """Verify max_posts_per_run limits total tasks."""
        posts = [PostItem(id="p1", name="B1", content="A", is_selected=True, status="ready")]
        groups = [f"https://facebook.com/groups/{i}" for i in range(10)]

        plan = build_round_robin_plan(posts, groups, max_posts_per_run=4)
        self.assertEqual(plan.total_runs, 4)
        self.assertEqual(len(plan.tasks), 4)

    def test_empty_posts_or_groups_yields_zero_tasks(self):
        """Verify safe exit without creating empty tasks if no post is selected."""
        posts = [PostItem(id="p1", name="B1", content="", is_selected=False, status="draft")]
        groups = ["https://facebook.com/groups/1"]

        plan = build_round_robin_plan(posts, groups)
        self.assertEqual(plan.total_runs, 0)
        self.assertEqual(len(plan.tasks), 0)


if __name__ == "__main__":
    unittest.main()
