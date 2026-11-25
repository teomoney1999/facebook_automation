"""
Unit Tests for Image Posting Feature (CLI & GUI).
Validates posts.json schema, auto-discovery of repo/images, fail-fast validations,
DOM file attachment mechanisms, CLI arguments, and GUI image management.
"""

import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

# Ensure repo directory is in sys.path
REPO_DIR = Path(__file__).resolve().parent.parent
import sys
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig
from browser.dom_actions import DOMActionError, attach_media_files
from facebookbot import FacebookBot
from main import parse_arguments
from services.post_verifier import PostStatus, PostVerificationResult


class TestImagePosting(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.test_dir.name)
        self.json_dir = self.base_path / "json"
        self.images_dir = self.base_path / "images"
        self.logs_dir = self.base_path / "logs"

        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Create dummy group links file
        self.links_file = self.json_dir / "links.json"
        self.links_file.write_text(
            json.dumps({"target_links": ["https://facebook.com/groups/test_image_group"]}),
            encoding="utf-8",
        )

        # Create dummy posts file
        self.posts_file = self.json_dir / "posts.json"
        self.posts_file.write_text(
            json.dumps({"content": "Nội dung bài viết mẫu"}),
            encoding="utf-8",
        )

        # Create dummy selectors file
        self.selectors_file = self.json_dir / "selectors.json"
        self.selectors_data = {
            "search_bars": ["//input[@placeholder='Search Facebook']"],
            "content_bar": ["//div[contains(text(), 'bạn đang nghĩ gì')]"],
            "create_post_modal": ["//div[@role='textbox']"],
            "photo_video_button": ["//div[@aria-label='Ảnh/video']"],
            "file_input": ["//input[@type='file']"],
            "post_button": ["//div[@aria-label='Đăng']"],
        }
        self.selectors_file.write_text(json.dumps(self.selectors_data), encoding="utf-8")

        # Create real sample image files in temp images_dir
        self.sample_img1 = self.images_dir / "photo1.jpg"
        self.sample_img1.write_bytes(b"\xff\xd8\xff\xe0testimage1")

        self.sample_img2 = self.images_dir / "photo2.png"
        self.sample_img2.write_bytes(b"\x89PNG\r\ntestimage2")

        # Non-image files that should be ignored
        (self.images_dir / ".gitkeep").write_text("", encoding="utf-8")
        (self.images_dir / "notes.txt").write_text("not an image", encoding="utf-8")

        self.config = BotConfig(
            email="test@example.com",
            password="password123",
            base_dir=self.base_path,
            timeout=1,
            max_posts_per_run=5,
            cooldown_hours=24,
        )

    def tearDown(self):
        self.test_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Schema & Persistence
    # -------------------------------------------------------------------------
    def test_posts_json_schema_with_images(self):
        """Verify posts.json correctly stores both text content and images list."""
        posts_file = self.json_dir / "posts.json"
        payload = {
            "content": "Bài viết thử nghiệm có ảnh",
            "images": [str(self.sample_img1), str(self.sample_img2)],
        }
        posts_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        loaded = json.loads(posts_file.read_text(encoding="utf-8"))
        self.assertEqual(loaded["content"], "Bài viết thử nghiệm có ảnh")
        self.assertIsInstance(loaded["images"], list)
        self.assertEqual(len(loaded["images"]), 2)
        self.assertEqual(loaded["images"][0], str(self.sample_img1))

    def test_backward_compatibility_text_only_posts_json(self):
        """Verify posts.json without 'images' key is fully supported without crash."""
        posts_file = self.json_dir / "posts.json"
        posts_file.write_text(json.dumps({"content": "Bài viết chỉ có chữ"}), encoding="utf-8")

        # Empty images_dir so auto-discovery finds nothing
        os.remove(self.sample_img1)
        os.remove(self.sample_img2)

        bot = FacebookBot(config=self.config)
        results = bot.run(dry_run=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "DRY_RUN")
        self.assertEqual(results[0]["images"], [])

    # -------------------------------------------------------------------------
    # 2. Image Discovery & Prioritization
    # -------------------------------------------------------------------------
    def test_auto_discovery_repo_images_on_cli(self):
        """Verify CLI default automatically picks up images from images/ folder."""
        posts_file = self.json_dir / "posts.json"
        posts_file.write_text(json.dumps({"content": "Bài viết test auto discovery"}), encoding="utf-8")

        bot = FacebookBot(config=self.config)
        results = bot.run(dry_run=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "DRY_RUN")
        # Should discover photo1.jpg and photo2.png, but ignore .gitkeep and notes.txt
        discovered_names = results[0]["images"]
        self.assertIn("photo1.jpg", discovered_names)
        self.assertIn("photo2.png", discovered_names)
        self.assertNotIn(".gitkeep", discovered_names)
        self.assertNotIn("notes.txt", discovered_names)
        self.assertEqual(len(discovered_names), 2)

    def test_explicit_images_override_discovery(self):
        """Verify passing explicit images list in constructor overrides images directory."""
        custom_img = self.base_path / "custom.webp"
        custom_img.write_bytes(b"webpdata")

        bot = FacebookBot(config=self.config, images=[str(custom_img)])
        results = bot.run(dry_run=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["images"], ["custom.webp"])

    def test_posts_json_images_override_directory(self):
        """Verify images in posts.json takes precedence over repo/images directory."""
        custom_img = self.base_path / "json_image.png"
        custom_img.write_bytes(b"pngdata")

        posts_file = self.json_dir / "posts.json"
        posts_file.write_text(
            json.dumps({"content": "Hello", "images": [str(custom_img)]}),
            encoding="utf-8",
        )

        bot = FacebookBot(config=self.config)
        results = bot.run(dry_run=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["images"], ["json_image.png"])

    # -------------------------------------------------------------------------
    # 3. Fail-Fast Validation
    # -------------------------------------------------------------------------
    def test_fail_fast_when_image_missing(self):
        """Verify bot immediately stops before browser launch if an image file does not exist."""
        bot = FacebookBot(
            config=self.config,
            images=["/non/existent/path/fake_image.jpg"],
            post_content="Valid text",
        )
        results = bot.run(dry_run=False)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAILED")
        self.assertIn("Tệp ảnh không tồn tại", results[0]["details"])

    def test_fail_fast_when_both_content_and_images_empty(self):
        """Verify bot stops if both text content and image list are completely empty."""
        os.remove(self.sample_img1)
        os.remove(self.sample_img2)

        bot = FacebookBot(config=self.config, post_content="", images=[])
        results = bot.run(dry_run=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAILED")
        self.assertIn("Nội dung bài đăng và ảnh đều rỗng", results[0]["details"])

    # -------------------------------------------------------------------------
    # 4. DOM attach_media_files Tests
    # -------------------------------------------------------------------------
    def test_attach_media_files_empty_list(self):
        """Passing empty list returns True without touching driver."""
        mock_driver = mock.MagicMock()
        self.assertTrue(attach_media_files(mock_driver, "//input", None, []))
        mock_driver.find_elements.assert_not_called()

    def test_attach_media_files_missing_file_raises_error(self):
        """Passing non-existent path raises DOMActionError immediately."""
        mock_driver = mock.MagicMock()
        with self.assertRaises(DOMActionError) as ctx:
            attach_media_files(mock_driver, "//input", None, ["/does_not_exist.jpg"])
        self.assertIn("Tệp hình ảnh không tồn tại", str(ctx.exception))

    def test_attach_media_files_direct_input(self):
        """When <input type='file'> is found directly, send_keys is called with absolute paths."""
        mock_driver = mock.MagicMock()
        mock_input = mock.MagicMock()
        mock_driver.find_elements.return_value = [mock_input]

        with mock.patch("time.sleep"):
            success = attach_media_files(
                mock_driver,
                "//input[@type='file']",
                None,
                [str(self.sample_img1), str(self.sample_img2)],
            )

        self.assertTrue(success)
        mock_input.send_keys.assert_called_once()
        sent_arg = mock_input.send_keys.call_args[0][0]
        self.assertIn(os.path.abspath(self.sample_img1), sent_arg)
        self.assertIn(os.path.abspath(self.sample_img2), sent_arg)

    def test_attach_media_files_click_photo_button_first(self):
        """When file_input is not found initially, photo/video button is clicked first."""
        mock_driver = mock.MagicMock()
        mock_photo_btn = mock.MagicMock()
        mock_photo_btn.is_displayed.return_value = True
        mock_input = mock.MagicMock()

        # Call 1: check file_input -> empty []
        # Call 2: check photo_btn -> [mock_photo_btn]
        # Call 3: check file_input again -> [mock_input]
        mock_driver.find_elements.side_effect = [
            [],
            [mock_photo_btn],
            [mock_input],
        ]

        with mock.patch("browser.dom_actions.safe_click") as mock_click, mock.patch("time.sleep"):
            success = attach_media_files(
                mock_driver,
                "//input[@type='file']",
                "//div[@aria-label='Ảnh/video']",
                [str(self.sample_img1)],
            )

        self.assertTrue(success)
        mock_click.assert_called_once_with(mock_driver, mock_photo_btn)
        mock_input.send_keys.assert_called_once()

    def test_attach_media_files_input_not_found_raises_dom_error(self):
        """When input is nowhere to be found, DOMActionError is raised."""
        mock_driver = mock.MagicMock()
        mock_driver.find_elements.return_value = []

        with self.assertRaises(DOMActionError) as ctx, mock.patch("time.sleep"):
            attach_media_files(mock_driver, "//input[@type='file']", None, [str(self.sample_img1)])
        self.assertIn("Không tìm thấy phần tử <input type='file'>", str(ctx.exception))

    def test_attach_media_files_recovers_from_stale_element(self):
        """When the first input becomes stale, it refreshes and attaches successfully."""
        from selenium.common.exceptions import StaleElementReferenceException

        mock_driver = mock.MagicMock()
        stale_input = mock.MagicMock()
        stale_input.send_keys.side_effect = StaleElementReferenceException("stale element")
        fresh_input = mock.MagicMock()

        # Call 1: initial locate -> stale_input
        # Call 2: sequential locate fallback -> fresh_input
        mock_driver.find_elements.side_effect = [
            [stale_input],
            [fresh_input],
        ]

        with mock.patch("time.sleep"):
            success = attach_media_files(
                mock_driver,
                "//input[@type='file']",
                None,
                [str(self.sample_img1)],
            )

        self.assertTrue(success)
        fresh_input.send_keys.assert_called_once()

    # -------------------------------------------------------------------------
    # 5. CLI Argument Parsing
    # -------------------------------------------------------------------------
    def test_cli_argument_parsing(self):
        """Verify --image and --images flags in main.py."""
        with mock.patch("sys.argv", ["main.py", "--images", "img1.png", "img2.jpg"]):
            args = parse_arguments()
            self.assertEqual(args.images, ["img1.png", "img2.jpg"])

        with mock.patch("sys.argv", ["main.py", "--image", "single.png"]):
            args = parse_arguments()
            self.assertEqual(args.images, ["single.png"])

    # -------------------------------------------------------------------------
    # 6. Post To Group Integration Mock
    # -------------------------------------------------------------------------
    @mock.patch("facebookbot.find_element_with_fallback")
    @mock.patch("facebookbot.safe_click")
    @mock.patch("facebookbot.inject_text_native_dom")
    @mock.patch("facebookbot.attach_media_files")
    def test_post_to_group_with_images_mock(
        self,
        mock_attach,
        mock_inject,
        mock_click,
        mock_find,
    ):
        """Verify post_to_group coordinates text injection and media attachment."""
        mock_driver = mock.MagicMock()
        mock_driver.current_url = "https://facebook.com/groups/test"
        mock_driver.title = "Group Test"

        mock_verifier = mock.MagicMock()
        mock_verifier.is_marketplace_form.return_value = False
        mock_verifier.verify_post_submission.return_value = PostVerificationResult(
            status=PostStatus.SUCCESS,
            message="Post successfully published",
        )

        bot = FacebookBot(config=self.config)

        with mock.patch("time.sleep"):
            res = bot.post_to_group(
                driver=mock_driver,
                verifier=mock_verifier,
                group_url="https://facebook.com/groups/test",
                content="Nội dung bài viết",
                images=[str(self.sample_img1), str(self.sample_img2)],
            )

        self.assertEqual(res.status, PostStatus.SUCCESS)
        mock_inject.assert_called_once()
        mock_attach.assert_called_once()
        attach_call_args = mock_attach.call_args[0]
        # Check images argument passed to attach_media_files
        self.assertEqual(attach_call_args[3], [str(self.sample_img1), str(self.sample_img2)])

    # -------------------------------------------------------------------------
    # 7. GUI Image Management
    # -------------------------------------------------------------------------
    def test_gui_image_loading_and_saving(self):
        """Test GUI posts.json loading and saving images without opening window."""
        try:
            import tkinter as tk
            from app_ui import FacebookBotGUI
        except (ImportError, RuntimeError):
            self.skipTest("Tkinter not available or display not present")

        # Create temporary posts.json
        posts_file = self.json_dir / "posts.json"
        posts_file.write_text(
            json.dumps({"content": "GUI Content", "images": [str(self.sample_img1)]}, ensure_ascii=False),
            encoding="utf-8",
        )

        try:
            gui = FacebookBotGUI()
            gui.withdraw()  # Hide UI window during test
            gui.posts_file = posts_file
            gui.links_file = self.links_file

            # Load posts
            gui._load_groups_and_posts()
            loaded_imgs = list(gui.lst_images.get(0, tk.END))
            self.assertEqual(loaded_imgs, [str(self.sample_img1)])

            # Add an image programmatically
            gui.lst_images.insert(tk.END, str(self.sample_img2))
            with mock.patch("tkinter.messagebox.showinfo"):
                gui._save_posts_file()

            # Verify saved JSON
            saved_data = json.loads(posts_file.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_data["images"]), 2)
            self.assertIn(str(self.sample_img2), saved_data["images"])

            # Test clear all images
            gui._clear_all_images()
            self.assertEqual(gui.lst_images.size(), 0)

            gui.destroy()
        except tk.TclError:
            self.skipTest("No X11/macOS display connection available for Tkinter GUI test")


if __name__ == "__main__":
    unittest.main()
