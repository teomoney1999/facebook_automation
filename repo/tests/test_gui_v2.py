"""
Unit Tests for Desktop GUI v2.0 (repo/app_ui_v2.py).
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

import sys
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from app_ui_v2 import FacebookBotGUIV2, BotWorkerThreadV2
from config import BotConfig
from services.posting_plan import PostItem, PostingPlan, PostingTask


class TestGUIV2(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)
        self.env_file = self.base_path / ".env"
        self.env_file.write_text(
            "EMAIL=user_v2@example.com\n"
            "PASSWORD=password_v2\n"
            "MIN_DELAY_BETWEEN_POSTS=45\n"
            "MAX_DELAY_BETWEEN_POSTS=90\n"
            "MAX_POSTS_PER_RUN=10\n"
            "COOLDOWN_HOURS=24\n"
            "DEBUG_PORT=9224\n"
            "TIMEOUT=15\n",
            encoding="utf-8",
        )
        (self.base_path / "json").mkdir(parents=True, exist_ok=True)
        (self.base_path / "json" / "links.json").write_text('{"target_links": ["https://facebook.com/groups/g1"]}', encoding="utf-8")
        (self.base_path / "json" / "posts.json").write_text(
            '{"posts": [{"id": "p1", "name": "Bài 1", "content": "Hello v2", "images": [], "is_selected": true, "status": "ready"}]}',
            encoding="utf-8",
        )
        (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

    def tearDown(self):
        self.test_dir.cleanup()

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available")
    def test_gui_v2_instantiation_and_theme_toggle(self):
        """Verify GUI v2 initializes with 3 tabs and theme toggle switches correctly."""
        try:
            app = FacebookBotGUIV2(config_path=str(self.env_file), default_theme="light")
            app.withdraw()

            # Verify 3 tabs
            tab_count = app.notebook.index("end")
            self.assertEqual(tab_count, 3)

            # Verify Tab labels
            self.assertIn("Cấu hình", app.notebook.tab(0, "text"))
            self.assertIn("Nội dung", app.notebook.tab(1, "text"))
            self.assertIn("Vận hành", app.notebook.tab(2, "text"))

            # Verify initial theme is light
            self.assertEqual(app.theme_name, "light")

            # Toggle theme to dark
            app._toggle_theme()
            self.assertEqual(app.theme_name, "dark")

            # Toggle back to light
            app._toggle_theme()
            self.assertEqual(app.theme_name, "light")

            # Teardown
            app.destroy()
        except tk.TclError as err:
            self.skipTest(f"Tkinter display error: {err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available")
    def test_gui_v2_multi_post_creation_and_plan_calculation(self):
        """Verify adding posts dynamically recalculates the round-robin plan."""
        try:
            app = FacebookBotGUIV2(config_path=str(self.env_file))
            app.withdraw()

            # Initially 1 post
            self.assertEqual(len(app.posts), 1)

            # Add post
            app._add_post()
            self.assertEqual(len(app.posts), 2)

            # Plan calculation
            app._refresh_round_robin_plan()
            self.assertIsNotNone(app.current_plan)
            self.assertEqual(app.current_plan.total_runs, 1)

            app.destroy()
        except tk.TclError as err:
            self.skipTest(f"Tkinter display error: {err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available")
    def test_gui_v2_tree_groups_deselect_and_drag(self):
        """Verify deselecting when clicking outside and drag-selecting in GUI v2."""
        try:
            app = FacebookBotGUIV2(config_path=str(self.env_file))
            app.withdraw()

            # Add multiple groups
            app.target_links = ["https://facebook.com/groups/g1", "https://facebook.com/groups/g2", "https://facebook.com/groups/g3"]
            app._refresh_groups_tree()
            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 3)

            # 1. Select group 1, then deselect
            app.tree_groups.selection_set(children[0])
            self.assertEqual(app.tree_groups.selection(), (children[0],))
            app._deselect_groups()
            self.assertEqual(app.tree_groups.selection(), ())

            # 2. Click outside (empty space)
            app.tree_groups.selection_set(children[1])
            empty_event = mock.MagicMock(x=10, y=999, state=0)
            res = app._on_group_tree_press(empty_event)
            self.assertEqual(res, "break")
            self.assertEqual(app.tree_groups.selection(), ())

            # 3. Drag select multiple groups
            press_event = mock.MagicMock(x=10, y=10, state=0)
            with mock.patch.object(app.tree_groups, "identify_row", side_effect=[children[0], children[2]]):
                app._on_group_tree_press(press_event)
                drag_event = mock.MagicMock(x=10, y=60, state=0)
                app._on_group_tree_drag(drag_event)
                self.assertTrue(app._is_dragging_groups)
                self.assertEqual(set(app.tree_groups.selection()), {children[0], children[1], children[2]})

                # 4. Release resets drag state
                app._on_group_tree_release(mock.MagicMock())
                self.assertFalse(app._is_dragging_groups)

            app.destroy()
        except tk.TclError as err:
            self.skipTest(f"Tkinter display error: {err}")

    @mock.patch("app_ui_v2.FacebookBot")
    def test_worker_thread_v2_executes_posting_tasks(self, mock_bot_cls):
        """Verify BotWorkerThreadV2 passes posting_tasks to FacebookBot.run."""
        mock_bot = mock.MagicMock()
        mock_bot.run.return_value = [{"link": "https://facebook.com/groups/g1", "status": "SUCCESS"}]
        mock_bot_cls.return_value = mock_bot

        config = BotConfig(email="test@ex.com", password="pwd", base_dir=self.base_path)
        plan = PostingPlan(
            tasks=[
                PostingTask(index=1, group_url="https://facebook.com/groups/g1", group_name="G1", post_id="p1", post_name="B1", content="C1", images=[])
            ],
            total_runs=1,
            selected_posts_count=1,
            eligible_groups_count=1,
            skipped_cooldown_count=0,
        )

        completed_res = None
        def on_done(res):
            nonlocal completed_res
            completed_res = res

        worker = BotWorkerThreadV2(
            config=config,
            plan=plan,
            dry_run=True,
            completion_callback=on_done,
        )
        worker.start()
        worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        mock_bot.run.assert_called_once()
        self.assertEqual(completed_res[0]["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
