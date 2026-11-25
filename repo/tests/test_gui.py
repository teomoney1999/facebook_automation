"""
Unit Tests for Desktop GUI (repo/app_ui.py).
Tests QueueLogHandler, BotWorkerThread, and FacebookBotGUI components.
"""

import json
import logging
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
try:
    import tkinter as tk
    TKINTER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    tk = None
    TKINTER_AVAILABLE = False
import unittest
from unittest import mock

# Ensure repo directory is in sys.path
REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig
from app_ui import BotWorkerThread, FacebookBotGUI, QueueLogHandler
from facebookbot import FacebookBot
from helpers.common_helpers import (
    extract_facebook_group_urls,
    load_group_targets,
    parse_group_header_info,
    save_group_targets,
)
from services.post_verifier import PostStatus, PostVerifier, PostVerificationResult


class TestGUIComponents(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)
        self.config = BotConfig(
            email="test_ui@example.com",
            password="test_ui_password",
            base_dir=self.base_path,
        )

    def tearDown(self):
        self.test_dir.cleanup()

    def test_queue_log_handler(self):
        """Verify QueueLogHandler catches log records and enqueues them."""
        log_q = queue.Queue()
        handler = QueueLogHandler(log_q)
        formatter = logging.Formatter("[%(levelname)s] %(message)s")
        handler.setFormatter(formatter)

        test_logger = logging.getLogger("TestQueueLogger")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.INFO)

        test_logger.info("Thử nghiệm hàng đợi GUI")
        self.assertFalse(log_q.empty())

        lvl, formatted = log_q.get_nowait()
        self.assertEqual(lvl, "INFO")
        self.assertIn("Thử nghiệm hàng đợi GUI", formatted)

        test_logger.removeHandler(handler)

    def test_no_duplicate_log_in_queue_from_propagated_logger(self):
        """Verify that when queue handler is attached to root, child loggers do not duplicate messages."""
        log_q = queue.Queue()
        handler = QueueLogHandler(log_q)
        root = logging.getLogger()
        root.addHandler(handler)

        gui_logger = logging.getLogger("FacebookBotGUI")
        gui_logger.setLevel(logging.INFO)
        gui_logger.info("Unique single log line")

        root.removeHandler(handler)
        self.assertEqual(log_q.qsize(), 1)

    @mock.patch("app_ui.FacebookBot")
    def test_worker_thread_execution(self, mock_bot_cls):
        """Verify BotWorkerThread instantiates bot, runs it, and triggers completion callback."""
        mock_bot = mock.MagicMock()
        mock_bot.run.return_value = [{"link": "https://facebook.com/1", "status": "SUCCESS"}]
        mock_bot_cls.return_value = mock_bot

        completed_results = None

        def on_complete(res):
            nonlocal completed_results
            completed_results = res

        stop_evt = threading.Event()
        worker = BotWorkerThread(
            config=self.config,
            dry_run=True,
            custom_batch_size=3,
            stop_event=stop_evt,
            completion_callback=on_complete,
        )

        worker.start()
        worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        mock_bot.run.assert_called_once_with(
            dry_run=True,
            custom_batch_size=3,
            stop_event=stop_evt,
            event_callback=None,
        )
        self.assertIsNotNone(completed_results)
        self.assertEqual(completed_results[0]["status"], "SUCCESS")

    @mock.patch("app_ui.FacebookBot")
    def test_worker_thread_exception_handling(self, mock_bot_cls):
        """Verify BotWorkerThread catches exceptions safely and reports critical error."""
        mock_bot = mock.MagicMock()
        mock_bot.run.side_effect = RuntimeError("Simulated crash in bot")
        mock_bot_cls.return_value = mock_bot

        completed_results = None

        def on_complete(res):
            nonlocal completed_results
            completed_results = res

        worker = BotWorkerThread(
            config=self.config,
            dry_run=False,
            completion_callback=on_complete,
        )

        worker.start()
        worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        self.assertIsNotNone(completed_results)
        self.assertEqual(completed_results[0]["status"], "CRITICAL_ERROR")

    @mock.patch("app_ui.FacebookBot")
    def test_worker_thread_join_mode(self, mock_bot_cls):
        """Verify BotWorkerThread calls join_groups when join_mode is True."""
        mock_bot = mock.MagicMock()
        mock_bot.join_groups.return_value = [{"link": "https://facebook.com/1", "status": "ALREADY_JOINED"}]
        mock_bot_cls.return_value = mock_bot

        completed_results = None

        def on_complete(res):
            nonlocal completed_results
            completed_results = res

        stop_evt = threading.Event()
        worker = BotWorkerThread(
            config=self.config,
            join_mode=True,
            custom_batch_size=5,
            stop_event=stop_evt,
            completion_callback=on_complete,
            target_links=["https://facebook.com/1"],
        )

        worker.start()
        worker.join(timeout=3.0)

        self.assertFalse(worker.is_alive())
        mock_bot.join_groups.assert_called_once_with(
            target_links=["https://facebook.com/1"],
            dry_run=False,
            join_limit=5,
            delay_min=15.0,
            delay_max=35.0,
            stop_event=stop_evt,
            event_callback=None,
        )
        self.assertIsNotNone(completed_results)
        self.assertEqual(completed_results[0]["status"], "ALREADY_JOINED")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_gui_instantiation_and_tabs(self):
        """Verify FacebookBotGUI creates 3 tabs, inputs, and widgets without crashing."""
        try:
            # Create a mock .env in temp directory
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")

            # Create json dir
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            (self.base_path / "json" / "links.json").write_text('{"target_links": ["https://facebook.com/groups/1"]}', encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Bài viết test"}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()  # Hide window during test

            # 1. Verify 4 tabs exist (Dashboard, History, Content, Config)
            tab_count = app.notebook.index("end")
            self.assertEqual(tab_count, 4)

            # 2. Verify config entries
            self.assertIn("EMAIL", app.cfg_entries)
            self.assertEqual(app.cfg_entries["EMAIL"].get(), "test@example.com")

            # 3. Verify Log Queue consumption
            app.log_queue.put(("INFO", "[INFO] Test direct queue log"))
            app._process_log_queue()
            console_content = app.console_text.get("1.0", tk.END)
            self.assertIn("Test direct queue log", console_content)

            # 4. Verify Progress UI update
            app._update_progress_ui(1, 5, 20)
            self.assertEqual(app.progress_bar["value"], 20)

            # 5. Verify Logout button exists
            self.assertTrue(hasattr(app, "btn_logout"))

            # 6. Clean teardown

            app.destroy()

        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_emergency_stop_signals_event_and_updates_ui(self):
        """Verify _stop_bot_flow sets stop_event, updates status card, and resets safely."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            (self.base_path / "json" / "links.json").write_text('{"target_links": []}', encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Test"}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            # Trigger emergency stop
            app._stop_bot_flow()
            self.assertTrue(app.stop_event.is_set())
            self.assertEqual(app.card_status.val_label.cget("text"), "🛑 ĐANG DỪNG KHẨN CẤP...")

            # Reset UI after stop
            app._reset_ui_after_run([])
            self.assertEqual(app.card_status.val_label.cget("text"), "🛑 ĐÃ DỪNG")
            self.assertEqual(str(app.btn_start.cget("state")), "normal")

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    def test_post_to_group_aborts_immediately_on_stop_event_before_post(self):
        """Verify post_to_group checks stop_event and aborts before clicking post button."""
        (self.base_path / "json").mkdir(parents=True, exist_ok=True)
        (self.base_path / "json" / "selectors.json").write_text(
            '{"content_bar": ["//div"], "create_post_modal": ["//div"], "post_button": ["//button"]}',
            encoding="utf-8",
        )
        (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

        bot = FacebookBot(config=self.config)
        stop_evt = threading.Event()
        stop_evt.set()  # Signal stop before calling

        mock_driver = mock.MagicMock()
        mock_verifier = mock.MagicMock()

        res = bot.post_to_group(
            driver=mock_driver,
            verifier=mock_verifier,
            group_url="https://facebook.com/groups/test_stop",
            content="Nội dung kiểm tra dừng khẩn cấp",
            stop_event=stop_evt,
        )

        self.assertEqual(res.status, PostStatus.FAILED)
        self.assertIn("Dừng khẩn cấp", res.message)
        # Verify post submission was never attempted
        mock_verifier.verify_post_submission.assert_not_called()

    @mock.patch("facebookbot.get_driver")
    def test_login_interactive_aborts_on_stop_event(self, mock_get_driver):
        """Verify login_interactive aborts immediately when stop_event is pre-set."""
        bot = FacebookBot(config=self.config, selectors={"content_bar": ["//div"]})
        stop_evt = threading.Event()
        stop_evt.set()

        mock_driver = mock.MagicMock()
        mock_get_driver.return_value.__enter__.return_value = mock_driver

        success = bot.login_interactive(stop_event=stop_evt)
        self.assertFalse(success)

    def test_extract_facebook_group_urls(self):
        """Verify extract_facebook_group_urls strips junk and extracts unique valid URLs."""
        raw_text = """
        1. https://www.facebook.com/groups/123456?ref=share, 
        [Nhóm ABC](https://fb.com/groups/my_group.1/)
        Rác: http://google.com, https://facebook.com/user/profile
        https://facebook.com/groups/123456/ (trùng lặp)
        * https://www.facebook.com/groups/vietnam.devs/
        """
        urls = extract_facebook_group_urls(raw_text)
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls[0], "https://www.facebook.com/groups/123456")
        self.assertEqual(urls[1], "https://www.facebook.com/groups/my_group.1")
        self.assertEqual(urls[2], "https://www.facebook.com/groups/vietnam.devs")

    def test_parse_group_header_info(self):
        """Verify parse_group_header_info extracts name, members, and privacy properly."""
        info_vn = parse_group_header_info("Cộng Đồng Python Việt Nam | Facebook", "Nhóm công khai · 45,2K thành viên")
        self.assertEqual(info_vn["name"], "Cộng Đồng Python Việt Nam")
        self.assertEqual(info_vn["privacy"], "Công khai")
        self.assertEqual(info_vn["members"], "45,2K")

        info_en = parse_group_header_info("Global Developers", "Private group · 1.5M members")
        self.assertEqual(info_en["name"], "Global Developers")
        self.assertEqual(info_en["privacy"], "Riêng tư")
        self.assertEqual(info_en["members"], "1.5M")

        info_mem = parse_group_header_info("React Vietnam", "Nhóm công khai · 10K thành viên", membership="ALREADY_JOINED")
        self.assertEqual(info_mem["name"], "React Vietnam")
        self.assertEqual(info_mem["membership"], "ALREADY_JOINED")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_treeview_multi_remove_and_auto_sync(self):
        """Verify multiple row selection removal and immediate auto-sync to links.json."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            links_file = self.base_path / "json" / "links.json"
            links_file.write_text('{"target_links": ["https://facebook.com/groups/1", "https://facebook.com/groups/2", "https://facebook.com/groups/3"]}', encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Test"}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            # Verify initial load: 3 items
            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 3)

            # Select first 2 items and remove
            app.tree_groups.selection_set(children[0], children[1])
            app._remove_selected_group()

            # Verify tree has 1 item left
            remaining = app.tree_groups.get_children()
            self.assertEqual(len(remaining), 1)
            self.assertEqual(app._get_group_url_from_item(remaining[0]), "https://facebook.com/groups/3")

            # Verify links.json was automatically synced without manual save
            import json
            saved_data = json.loads(links_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_data["target_links"], ["https://facebook.com/groups/3"])


            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 3)

            # 1. Select group 1
            app.tree_groups.selection_set(children[0])
            self.assertEqual(app.tree_groups.selection(), (children[0],))

            # 2. Deselect via _deselect_groups
            app._deselect_groups()
            self.assertEqual(app.tree_groups.selection(), ())

            # 3. Deselect when clicking empty space (identify_row returns "")
            app.tree_groups.selection_set(children[1])
            self.assertEqual(app.tree_groups.selection(), (children[1],))
            empty_event = mock.MagicMock(x=10, y=999, state=0)
            res = app._on_group_tree_press(empty_event)
            self.assertEqual(res, "break")
            self.assertEqual(app.tree_groups.selection(), ())

            # 4. Drag select multiple groups from grp1 to grp3
            press_event = mock.MagicMock(x=10, y=10, state=0)
            with mock.patch.object(app.tree_groups, "identify_row", side_effect=[children[0], children[2]]):
                app._on_group_tree_press(press_event)
                drag_event = mock.MagicMock(x=10, y=60, state=0)
                app._on_group_tree_drag(drag_event)
                self.assertTrue(app._is_dragging_groups)
                self.assertEqual(set(app.tree_groups.selection()), {children[0], children[1], children[2]})

                # 5. Release after drag does not open browser URL
                with mock.patch.object(app, "_open_url_in_browser") as mock_open:
                    rel_event = mock.MagicMock(x=10, y=60)
                    with mock.patch.object(app.tree_groups, "identify_region", return_value="cell"), \
                         mock.patch.object(app.tree_groups, "identify_column", return_value="#5"):
                        app._on_group_tree_click(rel_event)
                    mock_open.assert_not_called()
                self.assertFalse(app._is_dragging_groups)

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    def test_load_and_save_group_targets_structure(self):
        """Verify load_group_targets and save_group_targets handle new schema and legacy fallback."""
        test_file = self.base_path / "custom_links.json"

        # 1. Legacy format
        test_file.write_text('{"target_links": ["https://facebook.com/groups/10"]}', encoding="utf-8")
        loaded = load_group_targets(str(test_file))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["url"], "https://facebook.com/groups/10")
        self.assertEqual(loaded[0]["name"], "—")

        # 2. Save structured format
        groups_to_save = [
            {"url": "https://facebook.com/groups/10", "name": "Nhóm 10", "members": "5K", "privacy": "Công khai"},
            {"url": "https://facebook.com/groups/20", "name": "Nhóm 20", "members": "100K", "privacy": "Riêng tư"},
        ]
        ok = save_group_targets(str(test_file), groups_to_save)
        self.assertTrue(ok)

        # 3. Reload structured format
        reloaded = load_group_targets(str(test_file))
        self.assertEqual(len(reloaded), 2)
        self.assertEqual(reloaded[0]["name"], "Nhóm 10")
        self.assertEqual(reloaded[0]["members"], "5K")
        self.assertEqual(reloaded[0]["privacy"], "Công khai")
        self.assertEqual(reloaded[1]["name"], "Nhóm 20")

        # 4. Verify target_links array is also present for backward compatibility
        import json
        raw = json.loads(test_file.read_text(encoding="utf-8"))
        self.assertIn("groups", raw)
        self.assertIn("target_links", raw)
        self.assertEqual(raw["target_links"], ["https://facebook.com/groups/10", "https://facebook.com/groups/20"])

    def test_parse_group_header_info_notification_badges(self):
        """Verify notification badges like (3) are stripped and member strings parsed."""
        info = parse_group_header_info("(5) Chợ Cư Dân Quận 1 | Facebook", "Nhóm công khai · 24,5K thành viên")
        self.assertEqual(info["name"], "Chợ Cư Dân Quận 1")
        self.assertEqual(info["members"], "24,5K")
        self.assertEqual(info["privacy"], "Công khai")

        info_en = parse_group_header_info("(99+) Tech Jobs Overseas | Facebook", "Public group · 880 members")
        self.assertEqual(info_en["name"], "Tech Jobs Overseas")
        self.assertEqual(info_en["members"], "880")
        self.assertEqual(info_en["privacy"], "Công khai")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_browser_open_url_on_click(self):
        """Verify _open_url_in_browser launches system default browser."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            (self.base_path / "json" / "links.json").write_text('{"groups": [{"url": "https://facebook.com/groups/test_url"}]}', encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Test"}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            with mock.patch("webbrowser.open") as mock_open:
                app._open_url_in_browser("https://facebook.com/groups/test_url")
                mock_open.assert_called_once_with("https://facebook.com/groups/test_url")


            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 3)

            # 1. Select group 1
            app.tree_groups.selection_set(children[0])
            self.assertEqual(app.tree_groups.selection(), (children[0],))

            # 2. Deselect via _deselect_groups
            app._deselect_groups()
            self.assertEqual(app.tree_groups.selection(), ())

            # 3. Deselect when clicking empty space (identify_row returns "")
            app.tree_groups.selection_set(children[1])
            self.assertEqual(app.tree_groups.selection(), (children[1],))
            empty_event = mock.MagicMock(x=10, y=999, state=0)
            res = app._on_group_tree_press(empty_event)
            self.assertEqual(res, "break")
            self.assertEqual(app.tree_groups.selection(), ())

            # 4. Drag select multiple groups from grp1 to grp3
            press_event = mock.MagicMock(x=10, y=10, state=0)
            with mock.patch.object(app.tree_groups, "identify_row", side_effect=[children[0], children[2]]):
                app._on_group_tree_press(press_event)
                drag_event = mock.MagicMock(x=10, y=60, state=0)
                app._on_group_tree_drag(drag_event)
                self.assertTrue(app._is_dragging_groups)
                self.assertEqual(set(app.tree_groups.selection()), {children[0], children[1], children[2]})

                # 5. Release after drag does not open browser URL
                with mock.patch.object(app, "_open_url_in_browser") as mock_open:
                    rel_event = mock.MagicMock(x=10, y=60)
                    with mock.patch.object(app.tree_groups, "identify_region", return_value="cell"), \
                         mock.patch.object(app.tree_groups, "identify_column", return_value="#5"):
                        app._on_group_tree_click(rel_event)
                    mock_open.assert_not_called()
                self.assertFalse(app._is_dragging_groups)

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_history_tab_display_and_clear(self):
        """Verify History tab loads, formats dates, sorts by latest, and supports clearing."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            (self.base_path / "json" / "links.json").write_text('{"groups": []}', encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Test"}', encoding="utf-8")
            
            # Populate sample history with 2 entries
            hist_file = self.base_path / "json" / "history.json"
            hist_file.write_text("""{
                "version": "1.0",
                "history": {
                    "https://facebook.com/groups/old": {
                        "group_url": "https://facebook.com/groups/old",
                        "last_posted_at": "2026-11-20T10:00:00+00:00",
                        "status": "FAILED",
                        "post_count": 1,
                        "error": "Timeout"
                    },
                    "https://facebook.com/groups/recent": {
                        "group_url": "https://facebook.com/groups/recent",
                        "last_posted_at": "2026-11-22T15:30:00+00:00",
                        "status": "SUCCESS",
                        "post_count": 2,
                        "error": null
                    }
                }
            }""", encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            # Verify history items loaded in order (recent first)
            children = app.tree_history.get_children()
            self.assertEqual(len(children), 2)
            first_vals = app.tree_history.item(children[0], "values")
            self.assertEqual(first_vals[2], "https://facebook.com/groups/recent")
            self.assertIn("Thành Công", first_vals[3])

            second_vals = app.tree_history.item(children[1], "values")
            self.assertEqual(second_vals[2], "https://facebook.com/groups/old")
            self.assertIn("Thất Bại", second_vals[3])

            # Verify clear history
            with mock.patch("tkinter.messagebox.askyesno", return_value=True):
                with mock.patch("tkinter.messagebox.showinfo"):
                    app._clear_history_data()

            self.assertEqual(len(app.tree_history.get_children()), 0)


            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 3)

            # 1. Select group 1
            app.tree_groups.selection_set(children[0])
            self.assertEqual(app.tree_groups.selection(), (children[0],))

            # 2. Deselect via _deselect_groups
            app._deselect_groups()
            self.assertEqual(app.tree_groups.selection(), ())

            # 3. Deselect when clicking empty space (identify_row returns "")
            app.tree_groups.selection_set(children[1])
            self.assertEqual(app.tree_groups.selection(), (children[1],))
            empty_event = mock.MagicMock(x=10, y=999, state=0)
            res = app._on_group_tree_press(empty_event)
            self.assertEqual(res, "break")
            self.assertEqual(app.tree_groups.selection(), ())

            # 4. Drag select multiple groups from grp1 to grp3
            press_event = mock.MagicMock(x=10, y=10, state=0)
            with mock.patch.object(app.tree_groups, "identify_row", side_effect=[children[0], children[2]]):
                app._on_group_tree_press(press_event)
                drag_event = mock.MagicMock(x=10, y=60, state=0)
                app._on_group_tree_drag(drag_event)
                self.assertTrue(app._is_dragging_groups)
                self.assertEqual(set(app.tree_groups.selection()), {children[0], children[1], children[2]})

                # 5. Release after drag does not open browser URL
                with mock.patch.object(app, "_open_url_in_browser") as mock_open:
                    rel_event = mock.MagicMock(x=10, y=60)
                    with mock.patch.object(app.tree_groups, "identify_region", return_value="cell"), \
                         mock.patch.object(app.tree_groups, "identify_column", return_value="#5"):
                        app._on_group_tree_click(rel_event)
                    mock_open.assert_not_called()
                self.assertFalse(app._is_dragging_groups)

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_multi_drafts_gui_workflow(self):
        """Verify GUI supports creating, editing, duplicating, deleting, and saving multiple post drafts."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            (self.base_path / "json" / "links.json").write_text('{"groups": []}', encoding="utf-8")
            posts_file = self.base_path / "json" / "posts.json"
            posts_file.write_text('{"content": "Bài số 1", "images": []}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            # 1. Initial draft loaded
            self.assertEqual(len(app.posts), 1)
            self.assertEqual(app.posts[0].content, "Bài số 1")

            # 2. Add new draft
            app._add_draft()
            self.assertEqual(len(app.posts), 2)
            self.assertEqual(app.active_draft_idx, 1)

            # 3. Edit draft 2
            app.entry_draft_name.delete(0, tk.END)
            app.entry_draft_name.insert(0, "Bài Viết Khuyến Mãi")
            app.txt_content.delete("1.0", tk.END)
            app.txt_content.insert("1.0", "Nội dung bài viết khuyến mãi")
            app._sync_active_draft_from_controls()

            # 4. Duplicate draft 2
            app._duplicate_draft()
            self.assertEqual(len(app.posts), 3)
            self.assertIn("Bản sao", app.posts[2].name)

            # 5. Delete draft (with confirm mock)
            with mock.patch("tkinter.messagebox.askyesno", return_value=True):
                app._delete_draft()
            self.assertEqual(len(app.posts), 2)

            # 6. Save drafts to posts.json
            with mock.patch("tkinter.messagebox.showinfo"):
                app._save_posts_file()

            saved_data = json.loads(posts_file.read_text(encoding="utf-8"))
            self.assertIn("posts", saved_data)
            self.assertEqual(len(saved_data["posts"]), 2)
            self.assertIn("content", saved_data)

            # 7. Switch back to draft 0 via combobox event simulation
            app.cbo_drafts.current(0)
            app._on_draft_selected_from_combobox()
            self.assertEqual(app.active_draft_idx, 0)
            self.assertIn("Bài số 1", app.txt_content.get("1.0", "end-1c"))


            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 3)

            # 1. Select group 1
            app.tree_groups.selection_set(children[0])
            self.assertEqual(app.tree_groups.selection(), (children[0],))

            # 2. Deselect via _deselect_groups
            app._deselect_groups()
            self.assertEqual(app.tree_groups.selection(), ())

            # 3. Deselect when clicking empty space (identify_row returns "")
            app.tree_groups.selection_set(children[1])
            self.assertEqual(app.tree_groups.selection(), (children[1],))
            empty_event = mock.MagicMock(x=10, y=999, state=0)
            res = app._on_group_tree_press(empty_event)
            self.assertEqual(res, "break")
            self.assertEqual(app.tree_groups.selection(), ())

            # 4. Drag select multiple groups from grp1 to grp3
            press_event = mock.MagicMock(x=10, y=10, state=0)
            with mock.patch.object(app.tree_groups, "identify_row", side_effect=[children[0], children[2]]):
                app._on_group_tree_press(press_event)
                drag_event = mock.MagicMock(x=10, y=60, state=0)
                app._on_group_tree_drag(drag_event)
                self.assertTrue(app._is_dragging_groups)
                self.assertEqual(set(app.tree_groups.selection()), {children[0], children[1], children[2]})

                # 5. Release after drag does not open browser URL
                with mock.patch.object(app, "_open_url_in_browser") as mock_open:
                    rel_event = mock.MagicMock(x=10, y=60)
                    with mock.patch.object(app.tree_groups, "identify_region", return_value="cell"), \
                         mock.patch.object(app.tree_groups, "identify_column", return_value="#5"):
                        app._on_group_tree_click(rel_event)
                    mock_open.assert_not_called()
                self.assertFalse(app._is_dragging_groups)

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_group_membership_gui_and_join_flow(self):
        """Verify membership badges in group list and execution of join groups workflow."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            links_file = self.base_path / "json" / "links.json"
            links_file.write_text(json.dumps({
                "groups": [
                    {
                        "url": "https://facebook.com/groups/joined_group",
                        "name": "Nhóm Đã Vào",
                        "members": "10k",
                        "privacy": "Công khai",
                        "membership": "ALREADY_JOINED",
                    },
                    {
                        "url": "https://facebook.com/groups/unjoined_group",
                        "name": "Nhóm Chưa Vào",
                        "members": "5k",
                        "privacy": "Riêng tư",
                        "membership": "NOT_JOINED",
                    },
                ]
            }), encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Test"}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            # 1. Verify membership badge rendering
            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 2)

            vals_joined = app.tree_groups.item(children[0], "values")
            self.assertEqual(vals_joined[4], "✅ Đã vào")
            self.assertEqual(vals_joined[5], "https://facebook.com/groups/joined_group")

            vals_unjoined = app.tree_groups.item(children[1], "values")
            self.assertEqual(vals_unjoined[4], "❌ Chưa vào")
            self.assertEqual(vals_unjoined[5], "https://facebook.com/groups/unjoined_group")

            # 2. Test URL extractor helper
            self.assertEqual(app._get_group_url_from_item(children[0]), "https://facebook.com/groups/joined_group")
            self.assertEqual(app._get_group_url_from_item(children[1]), "https://facebook.com/groups/unjoined_group")

            # 3. Test starting join groups flow
            with mock.patch.object(app, "_launch_worker") as mock_launch:
                # Select only the second group (unjoined)
                app.tree_groups.selection_set(children[1])
                app._start_join_groups_flow()
                mock_launch.assert_called_once_with(
                    dry_run=False,
                    check_dom=False,
                    join_mode=True,
                    custom_targets=["https://facebook.com/groups/unjoined_group"],
                    batch_size=10,
                    join_delay_min=15.0,
                    join_delay_max=35.0,
                )


            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_groups_tree_selection_deselect_and_drag(self):
        """Verify selecting group, deselecting when clicking outside, and drag-selecting multiple groups."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\n", encoding="utf-8")
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            links_file = self.base_path / "json" / "links.json"
            links_file.write_text(json.dumps({
                "target_links": [
                    "https://facebook.com/groups/grp1",
                    "https://facebook.com/groups/grp2",
                    "https://facebook.com/groups/grp3",
                ]
            }), encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Test"}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 3)

            # 1. Select group 1
            app.tree_groups.selection_set(children[0])
            self.assertEqual(app.tree_groups.selection(), (children[0],))

            # 2. Deselect via _deselect_groups
            app._deselect_groups()
            self.assertEqual(app.tree_groups.selection(), ())

            # 3. Deselect when clicking empty space (identify_row returns "")
            app.tree_groups.selection_set(children[1])
            self.assertEqual(app.tree_groups.selection(), (children[1],))
            empty_event = mock.MagicMock(x=10, y=999, state=0)
            res = app._on_group_tree_press(empty_event)
            self.assertEqual(res, "break")
            self.assertEqual(app.tree_groups.selection(), ())

            # 4. Drag select multiple groups from grp1 to grp3
            press_event = mock.MagicMock(x=10, y=10, state=0)
            with mock.patch.object(app.tree_groups, "identify_row", side_effect=[children[0], children[2]]):
                app._on_group_tree_press(press_event)
                drag_event = mock.MagicMock(x=10, y=60, state=0)
                app._on_group_tree_drag(drag_event)
                self.assertTrue(app._is_dragging_groups)
                self.assertEqual(set(app.tree_groups.selection()), {children[0], children[1], children[2]})

                # 5. Release after drag does not open browser URL
                with mock.patch.object(app, "_open_url_in_browser") as mock_open:
                    rel_event = mock.MagicMock(x=10, y=60)
                    with mock.patch.object(app.tree_groups, "identify_region", return_value="cell"), \
                         mock.patch.object(app.tree_groups, "identify_column", return_value="#5"):
                        app._on_group_tree_click(rel_event)
                    mock_open.assert_not_called()
                self.assertFalse(app._is_dragging_groups)

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_gui_join_cooldown_and_delay_configuration(self):
        """Verify GUI configuration of JOIN_DELAY_MIN, JOIN_DELAY_MAX, JOIN_LIMIT, and Tab 2 quick inputs."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text(
                "EMAIL=test@example.com\nPASSWORD=secret\nJOIN_DELAY_MIN=10.0\nJOIN_DELAY_MAX=25.0\nJOIN_LIMIT=7\nCOOLDOWN_HOURS=24\n",
                encoding="utf-8",
            )
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            links_file = self.base_path / "json" / "links.json"
            links_file.write_text('{"target_links": ["https://facebook.com/groups/1"]}', encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Test"}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            # 1. Verify Tab 1 entries populated
            self.assertEqual(app.cfg_entries["JOIN_DELAY_MIN"].get(), "10.0")
            self.assertEqual(app.cfg_entries["JOIN_DELAY_MAX"].get(), "25.0")
            self.assertEqual(app.cfg_entries["JOIN_LIMIT"].get(), "7")

            # 2. Modify Tab 1 and save
            app.cfg_entries["JOIN_DELAY_MIN"].delete(0, tk.END)
            app.cfg_entries["JOIN_DELAY_MIN"].insert(0, "12.0")
            app.cfg_entries["JOIN_DELAY_MAX"].delete(0, tk.END)
            app.cfg_entries["JOIN_DELAY_MAX"].insert(0, "30.0")
            app.cfg_entries["JOIN_LIMIT"].delete(0, tk.END)
            app.cfg_entries["JOIN_LIMIT"].insert(0, "8")
            app._save_env_config()

            saved_env = env_file.read_text(encoding="utf-8")
            self.assertIn("JOIN_DELAY_MIN=12.0", saved_env)
            self.assertIn("JOIN_DELAY_MAX=30.0", saved_env)
            self.assertIn("JOIN_LIMIT=8", saved_env)

            # 3. Test Tab 2 quick spinboxes update and launch with custom values
            self.assertTrue(hasattr(app, "entry_join_min"))
            self.assertTrue(hasattr(app, "entry_join_max"))
            self.assertTrue(hasattr(app, "entry_join_limit"))

            app.entry_join_min.delete(0, tk.END)
            app.entry_join_min.insert(0, "20.0")
            app.entry_join_max.delete(0, tk.END)
            app.entry_join_max.insert(0, "40.0")
            app.entry_join_limit.delete(0, tk.END)
            app.entry_join_limit.insert(0, "5")

            with mock.patch.object(app, "_launch_worker") as mock_launch:
                app._start_join_groups_flow()
                mock_launch.assert_called_once_with(
                    dry_run=False,
                    check_dom=False,
                    join_mode=True,
                    custom_targets=["https://facebook.com/groups/1"],
                    batch_size=5,
                    join_delay_min=20.0,
                    join_delay_max=40.0,
                )

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")

    @unittest.skipUnless(TKINTER_AVAILABLE, "Tkinter not available in this Python environment")
    def test_post_to_selected_group_workflow(self):
        """Verify posting workflow specifically for a selected group in treeview."""
        try:
            env_file = self.base_path / ".env"
            env_file.write_text("EMAIL=test@example.com\nPASSWORD=secret\nCOOLDOWN_HOURS=24\n", encoding="utf-8")
            (self.base_path / "json").mkdir(parents=True, exist_ok=True)
            links_file = self.base_path / "json" / "links.json"
            links_file.write_text(json.dumps({
                "groups": [
                    {"url": "https://facebook.com/groups/alpha", "name": "Nhóm Alpha"},
                    {"url": "https://facebook.com/groups/beta", "name": "Nhóm Beta"},
                ]
            }), encoding="utf-8")
            (self.base_path / "json" / "posts.json").write_text('{"content": "Hello Post"}', encoding="utf-8")
            (self.base_path / "json" / "history.json").write_text('{}', encoding="utf-8")

            app = FacebookBotGUI(config_path=str(env_file))
            app.withdraw()

            children = app.tree_groups.get_children()
            self.assertEqual(len(children), 2)

            # 1. No selection -> prompt info, no worker launched
            app.tree_groups.selection_set([])
            with mock.patch("tkinter.messagebox.showinfo") as mock_info, mock.patch.object(app, "_launch_worker") as mock_launch:
                app._start_post_to_selected_groups()
                mock_info.assert_called_once()
                mock_launch.assert_not_called()

            # 2. Select group Alpha -> launches worker with Alpha
            app.tree_groups.selection_set([children[0]])
            with mock.patch.object(app, "_launch_worker") as mock_launch:
                app._start_post_to_selected_groups()
                mock_launch.assert_called_once_with(
                    dry_run=False,
                    check_dom=False,
                    batch_size=1,
                    custom_targets=["https://facebook.com/groups/alpha"],
                    override_cooldown=False,
                )

            # 3. _start_bot_flow with selection routes to _start_post_to_selected_groups
            app.tree_groups.selection_set([children[1]])
            with mock.patch.object(app, "_start_post_to_selected_groups") as mock_post_sel:
                app._start_bot_flow()
                mock_post_sel.assert_called_once()

            # 4. In cooldown: prompt user; if accepted, override_cooldown=True
            from datetime import datetime, timezone
            state_mgr = app.state_manager if hasattr(app, "state_manager") else None
            from services.state_manager import StateManager
            sm = StateManager(str(app.history_file))
            sm.record_post("https://facebook.com/groups/alpha", "SUCCESS", post_time=datetime.now(timezone.utc).isoformat())

            app.tree_groups.selection_set([children[0]])
            with mock.patch("tkinter.messagebox.askyesno", return_value=True) as mock_ask, mock.patch.object(app, "_launch_worker") as mock_launch:
                app._start_post_to_selected_groups()
                mock_ask.assert_called_once()
                mock_launch.assert_called_once_with(
                    dry_run=False,
                    check_dom=False,
                    batch_size=1,
                    custom_targets=["https://facebook.com/groups/alpha"],
                    override_cooldown=True,
                )

            # 5. In cooldown: prompt user; if declined, do not launch
            with mock.patch("tkinter.messagebox.askyesno", return_value=False) as mock_ask, mock.patch.object(app, "_launch_worker") as mock_launch:
                app._start_post_to_selected_groups()
                mock_ask.assert_called_once()
                mock_launch.assert_not_called()

            app.destroy()
        except tk.TclError as tcl_err:
            self.skipTest(f"Tkinter display not available in this environment: {tcl_err}")


if __name__ == "__main__":
    unittest.main()
