"""
Unit tests for Module 5: Bot Orchestrator & CLI Integration.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent
for p in (REPO_ROOT, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from config import BotConfig
    from facebookbot import FacebookBot
    from main import parse_arguments, show_status
    from services.circuit_breaker import CircuitBreaker, CircuitBreakerTriggeredError
    from services.post_verifier import PostStatus, PostVerificationResult, PostVerifier
    from services.state_manager import StateManager
except ImportError:
    from repo.config import BotConfig
    from repo.facebookbot import FacebookBot
    from repo.main import parse_arguments, show_status
    from repo.services.circuit_breaker import CircuitBreaker, CircuitBreakerTriggeredError
    from repo.services.post_verifier import PostStatus, PostVerificationResult, PostVerifier
    from repo.services.state_manager import StateManager


class TestBotOrchestrator(unittest.TestCase):
    """Test suite for FacebookBot orchestrator logic."""

    def setUp(self):
        # Patch sleep to make test executions instantaneous
        self.sleep_patcher = patch("time.sleep", return_value=None)
        self.sleep_patcher.start()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_base = Path(self.temp_dir.name)

        # Setup standard subdirectories
        self.json_dir = self.temp_base / "json"
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir = self.temp_base / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir = self.logs_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

        self.history_file = self.json_dir / "history.json"
        self.groups_file = self.json_dir / "links.json"
        self.posts_file = self.json_dir / "posts.json"
        self.selectors_file = self.json_dir / "selectors.json"
        self.log_file = self.logs_dir / "bot.log"

        with open(self.groups_file, "w", encoding="utf-8") as f:
            json.dump({"target_links": ["https://facebook.com/groups/1", "https://facebook.com/groups/2"]}, f)

        with open(self.posts_file, "w", encoding="utf-8") as f:
            json.dump({"content": "Test automated post content 🚀"}, f)

        with open(self.selectors_file, "w", encoding="utf-8") as f:
            json.dump({
                "content_bar": ["//div[@role='button']"],
                "create_post_modal": ["//div[@role='textbox']"],
                "post_button": ["//div[@aria-label='Post']"],
                "search_bars": ["//input[@placeholder='Search Facebook']"],
                "home_banner": ["//div[@role='banner']"],
                "email_input": ["//input[@name='email']"],
                "login_button": ["//button[@name='login']"],
            }, f)

        self.config = BotConfig(
            email="test@example.com",
            password="secretpassword",
            min_delay_between_posts=0.1,
            max_delay_between_posts=0.2,
            max_posts_per_run=5,
            timeout=1,
            debug_port=9224,
            cooldown_hours=24,
            base_dir=self.temp_base,
        )

        self.mock_logger = MagicMock()

    def tearDown(self):
        self.sleep_patcher.stop()
        self.temp_dir.cleanup()

    def test_bot_initialization(self):
        """Verifies bot initializes dependencies cleanly without side-effects."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        self.assertEqual(bot.config.email, "test@example.com")
        self.assertIsNotNone(bot.state_manager)
        self.assertIsNotNone(bot.circuit_breaker)
        self.assertIn("content_bar", bot.selectors)

    def test_fast_exit_when_no_eligible_groups(self):
        """Verifies fast-exit triggers when all groups are on 24h cooldown."""
        state_mgr = StateManager(str(self.history_file))
        # Mark all groups as posted recently
        state_mgr.record_post("https://facebook.com/groups/1", PostStatus.SUCCESS.value)
        state_mgr.record_post("https://facebook.com/groups/2", PostStatus.SUCCESS.value)

        bot = FacebookBot(config=self.config, state_manager=state_mgr, custom_logger=self.mock_logger)

        with patch("facebookbot.get_driver") as mock_get_driver:
            results = bot.run()
            self.assertEqual(results, [])
            mock_get_driver.assert_not_called()
            self.mock_logger.info.assert_any_call(
                "⚡ [FAST-EXIT] Không có nhóm nào hợp lệ hoặc tất cả đang trong thời gian giãn cách %dh. Kết thúc phiên.",
                24,
            )

    def test_dry_run_mode(self):
        """Verifies dry-run returns planned actions without launching Chrome."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)

        with patch("facebookbot.get_driver") as mock_get_driver:
            results = bot.run(dry_run=True)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["status"], "DRY_RUN")
            self.assertEqual(results[1]["status"], "DRY_RUN")
            mock_get_driver.assert_not_called()

    def test_check_login_status_success(self):
        """Verifies positive detection of active session."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.facebook.com/"
        mock_driver.find_elements.return_value = []

        with patch("facebookbot.WebDriverWait") as mock_wait:
            mock_wait.return_value.until.return_value = MagicMock()
            status = bot.check_login_status(mock_driver)
            self.assertTrue(status)

    def test_check_login_status_redirected_to_login(self):
        """Verifies negative detection when redirected to login/checkpoint."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.facebook.com/login.php"

        status = bot.check_login_status(mock_driver)
        self.assertFalse(status)

    def test_check_login_status_fails_when_login_inputs_present(self):
        """Verifies that presence of email/password input forces login status to False."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.facebook.com/"
        mock_input = MagicMock()
        mock_input.is_displayed.return_value = True
        mock_driver.find_elements.return_value = [mock_input]

        status = bot.check_login_status(mock_driver)
        self.assertFalse(status)

    @patch("facebookbot.get_driver")
    def test_login_interactive_success(self, mock_get_driver):
        """Verifies successful interactive login flow with user confirmation."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_get_driver.return_value.__enter__.return_value = mock_driver

        # First check False (pre-login), second check True (after login)
        with patch.object(bot, "check_login_status", side_effect=[False, True]):
            result = bot.login_interactive(auto_fill=False, on_ready_prompt=lambda: True)
            self.assertTrue(result)

    @patch("facebookbot.get_driver")
    def test_login_interactive_user_cancelled(self, mock_get_driver):
        """Verifies interactive login aborted when user cancels prompt."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_get_driver.return_value.__enter__.return_value = mock_driver

        with patch.object(bot, "check_login_status", return_value=False):
            result = bot.login_interactive(auto_fill=False, on_ready_prompt=lambda: False)
            self.assertFalse(result)

    @patch("facebookbot.safe_click")
    @patch("facebookbot.find_element_with_fallback")
    @patch("facebookbot.inject_text_native_dom")
    def test_post_to_group_success(self, mock_inject, mock_find, mock_click):
        """Verifies standard successful group posting flow."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_driver.current_url = "https://facebook.com/groups/1"
        mock_verifier = MagicMock()
        mock_verifier.is_marketplace_form.return_value = False
        mock_verifier.verify_post_submission.return_value = PostVerificationResult(
            status=PostStatus.SUCCESS, message="Modal closed successfully"
        )

        res = bot.post_to_group(mock_driver, mock_verifier, "https://facebook.com/groups/1", "Test post")
        self.assertEqual(res.status, PostStatus.SUCCESS)
        mock_driver.get.assert_called_with("https://facebook.com/groups/1")
        mock_inject.assert_called_once()
        self.assertEqual(mock_click.call_count, 2)  # content_bar and post_button

    @patch("facebookbot.safe_click")
    @patch("facebookbot.find_element_with_fallback")
    @patch("facebookbot.inject_text_native_dom")
    def test_post_to_group_with_next_button(self, mock_inject, mock_find, mock_click):
        """Verifies posting when group requires clicking Next before Post button."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        bot.selectors["next_button"] = ["//div[@aria-label='Next']"]
        mock_driver = MagicMock()
        mock_driver.current_url = "https://facebook.com/groups/1"
        mock_verifier = MagicMock()
        mock_verifier.is_marketplace_form.return_value = False
        mock_verifier.verify_post_submission.return_value = PostVerificationResult(
            status=PostStatus.SUCCESS, message="Modal closed successfully"
        )

        res = bot.post_to_group(mock_driver, mock_verifier, "https://facebook.com/groups/1", "Test post")
        self.assertEqual(res.status, PostStatus.SUCCESS)
        self.assertEqual(mock_click.call_count, 3)  # content_bar, next_button, post_button

    def test_post_to_group_session_lost(self):
        """Verifies detection of session loss when redirected to checkpoint."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_driver.current_url = "https://www.facebook.com/checkpoint/?next=..."
        mock_verifier = MagicMock()

        with patch("facebookbot.capture_screenshot", return_value="/tmp/session_lost.png"):
            res = bot.post_to_group(mock_driver, mock_verifier, "https://facebook.com/groups/1", "Test post")
            self.assertEqual(res.status, PostStatus.FAILED)
            self.assertIn("checkpoint", res.message)
            self.assertTrue(bot.circuit_breaker.is_tripped())

    def test_post_to_group_network_disconnection(self):
        """Verifies detection of network error when internet drops."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_driver.current_url = "chrome-error://chromewebdata/"
        mock_driver.title = "No internet"
        mock_verifier = MagicMock()

        with patch("facebookbot.capture_screenshot", return_value="/tmp/net_err.png"):
            res = bot.post_to_group(mock_driver, mock_verifier, "https://facebook.com/groups/1", "Test post")
            self.assertEqual(res.status, PostStatus.FAILED)
            self.assertIn("Network disconnection", res.message)
            self.assertTrue(bot.circuit_breaker.is_tripped())

    @patch("facebookbot.get_driver")
    @patch.object(FacebookBot, "check_login_status", return_value=True)
    @patch.object(FacebookBot, "post_to_group")
    def test_run_circuit_breaker_tripped_on_action_blocked(self, mock_post, mock_login, mock_driver_cm):
        """Verifies bot loop terminates immediately when ACTION_BLOCKED is returned."""
        mock_driver = MagicMock()
        mock_driver_cm.return_value.__enter__.return_value = mock_driver

        mock_post.return_value = PostVerificationResult(
            status=PostStatus.ACTION_BLOCKED, message="Blocked dialog detected"
        )

        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        results = bot.run()

        # Should halt on group 1, never attempting group 2
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], PostStatus.ACTION_BLOCKED.value)
        self.assertTrue(bot.circuit_breaker.is_tripped())

    @patch("facebookbot.get_driver")
    @patch.object(FacebookBot, "check_login_status", return_value=True)
    @patch.object(FacebookBot, "post_to_group")
    def test_run_consecutive_failure_resets_on_success(self, mock_post, mock_login, mock_driver_cm):
        """Verifies consecutive failure count resets when a post succeeds."""
        mock_driver = MagicMock()
        mock_driver_cm.return_value.__enter__.return_value = mock_driver

        # Group 1 fails, Group 2 succeeds
        mock_post.side_effect = [
            PostVerificationResult(status=PostStatus.FAILED, message="Temporary error"),
            PostVerificationResult(status=PostStatus.SUCCESS, message="Posted"),
        ]

        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        results = bot.run()

        self.assertEqual(len(results), 2)
        self.assertEqual(bot.circuit_breaker.consecutive_failures, 0)
        self.assertFalse(bot.circuit_breaker.is_tripped())

    @patch("facebookbot.safe_click")
    @patch("facebookbot.find_element_with_fallback")
    def test_post_to_group_marketplace_detection(self, mock_find, mock_click):
        """Verifies Marketplace form detection safely returns MARKETPLACE_FORM without failing."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_driver.current_url = "https://facebook.com/groups/1"
        mock_verifier = MagicMock()
        mock_verifier.is_marketplace_form.return_value = True

        res = bot.post_to_group(mock_driver, mock_verifier, "https://facebook.com/groups/1", "Test post")
        self.assertEqual(res.status, PostStatus.MARKETPLACE_FORM)
        self.assertIn("Marketplace", res.message)

    @patch("facebookbot.get_driver")
    @patch.object(FacebookBot, "check_login_status", return_value=True)
    @patch.object(FacebookBot, "post_to_group")
    def test_run_handles_exception_in_post_to_group(self, mock_post, mock_login, mock_driver_cm):
        """Verifies exceptions during group post are safely caught and recorded without crashing."""
        mock_driver = MagicMock()
        mock_driver_cm.return_value.__enter__.return_value = mock_driver

        from selenium.common.exceptions import WebDriverException
        mock_post.side_effect = WebDriverException("Simulated DOM timeout")

        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        with patch("facebookbot.capture_screenshot", return_value="/tmp/err.png"):
            results = bot.run()

        # Both groups failed via exception, but bot didn't crash
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["status"], PostStatus.FAILED.value)
        self.assertEqual(results[1]["status"], PostStatus.FAILED.value)
        self.assertEqual(bot.circuit_breaker.consecutive_failures, 2)

    @patch("facebookbot.safe_click")
    @patch("facebookbot.find_element_with_fallback")
    @patch("facebookbot.inject_text_native_dom")
    def test_fill_and_submit_login_success(self, mock_inject, mock_find, mock_click):
        """Verifies auto-filling email/password inputs and clicking login button."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()
        mock_email = MagicMock()
        mock_email.get_attribute.return_value = self.config.email
        mock_pass = MagicMock()
        mock_pass.get_attribute.return_value = self.config.password
        mock_btn = MagicMock()

        mock_find.side_effect = [mock_email, mock_pass, mock_btn]

        result = bot.fill_and_submit_login(mock_driver)
        self.assertTrue(result)
        self.assertEqual(mock_find.call_count, 3)
        self.assertEqual(mock_inject.call_count, 2)
        mock_click.assert_called_once_with(mock_driver, mock_btn)

    def test_fill_and_submit_login_missing_credentials(self):
        """Verifies fill_and_submit_login safely returns False when credentials are empty."""
        bot = FacebookBot(
            config=self.config,
            selectors={"email_input": ["//input[@name='email']"]},
            custom_logger=self.mock_logger,
        )
        mock_driver = MagicMock()
        # Simulate missing password via mocked config
        with patch.object(bot, "config", MagicMock(email="test@example.com", password="")):
            self.assertFalse(bot.fill_and_submit_login(mock_driver))

    @patch("facebookbot.find_element_with_fallback", side_effect=Exception("Element not found"))
    def test_fill_and_submit_login_handles_dom_exception(self, mock_find):
        """Verifies fill_and_submit_login catches DOM errors gracefully."""
        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)
        mock_driver = MagicMock()

        self.assertFalse(bot.fill_and_submit_login(mock_driver))
        self.mock_logger.warning.assert_called()

    @patch("facebookbot.get_driver")
    @patch.object(FacebookBot, "post_to_group")
    def test_run_auto_logins_and_proceeds_when_initial_check_fails(self, mock_post, mock_get_driver_cm):
        """Verifies bot auto-fills login when initial check fails and continues posting on success."""
        mock_driver = MagicMock()
        mock_get_driver_cm.return_value.__enter__.return_value = mock_driver

        mock_post.return_value = PostVerificationResult(
            status=PostStatus.SUCCESS, message="Post verified"
        )

        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)

        # Initial check False, check after auto-fill True
        with patch.object(bot, "check_login_status", side_effect=[False, True]):
            with patch.object(bot, "fill_and_submit_login", return_value=True) as mock_fill:
                results = bot.run()
                mock_fill.assert_called_once_with(mock_driver)
                self.assertEqual(len(results), 2)
                self.assertEqual(results[0]["status"], PostStatus.SUCCESS.value)
                self.assertEqual(mock_post.call_count, 2)

    @patch("facebookbot.get_driver")
    @patch.object(FacebookBot, "post_to_group")
    def test_run_handles_2fa_prompt_success(self, mock_post, mock_get_driver_cm):
        """Verifies bot handles 2FA confirmation via on_login_prompt and proceeds to post."""
        mock_driver = MagicMock()
        mock_get_driver_cm.return_value.__enter__.return_value = mock_driver

        mock_post.return_value = PostVerificationResult(
            status=PostStatus.SUCCESS, message="Post verified"
        )

        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)

        # Initial check False, check after auto-fill False (needs 2FA), check after prompt True
        with patch.object(bot, "check_login_status", side_effect=[False, False, True]):
            with patch.object(bot, "fill_and_submit_login", return_value=True):
                mock_prompt = MagicMock(return_value=True)
                results = bot.run(on_login_prompt=mock_prompt)
                mock_prompt.assert_called_once()
                self.assertEqual(len(results), 2)
                self.assertEqual(results[0]["status"], PostStatus.SUCCESS.value)

    @patch("facebookbot.get_driver")
    @patch.object(FacebookBot, "post_to_group")
    def test_run_handles_2fa_prompt_cancelled(self, mock_post, mock_get_driver_cm):
        """Verifies bot returns NEEDS_LOGIN when user cancels 2FA confirmation."""
        mock_driver = MagicMock()
        mock_get_driver_cm.return_value.__enter__.return_value = mock_driver

        bot = FacebookBot(config=self.config, custom_logger=self.mock_logger)

        with patch.object(bot, "check_login_status", side_effect=[False, False]):
            with patch.object(bot, "fill_and_submit_login", return_value=True):
                mock_prompt = MagicMock(return_value=False)
                results = bot.run(on_login_prompt=mock_prompt)
                mock_prompt.assert_called_once()
                mock_post.assert_not_called()
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["status"], "NEEDS_LOGIN")



class TestMainCLI(unittest.TestCase):
    """Test suite for main.py command-line argument parsing and execution."""

    def test_cli_argument_parsing(self):
        with patch("sys.argv", ["main.py", "--dry-run", "--batch-size", "7"]):
            args = parse_arguments()
            self.assertTrue(args.dry_run)
            self.assertEqual(args.batch_size, 7)
            self.assertFalse(args.check_dom)
            self.assertFalse(args.status)

    def test_cli_check_dom_flag(self):
        with patch("sys.argv", ["main.py", "--check-dom"]):
            args = parse_arguments()
            self.assertTrue(args.check_dom)
            self.assertFalse(args.dry_run)

    def test_cli_status_flag(self):
        with patch("sys.argv", ["main.py", "--status"]):
            args = parse_arguments()
            self.assertTrue(args.status)

    def test_cli_url_flag(self):
        with patch("sys.argv", ["main.py", "--url", "https://facebook.com/groups/tracer_test"]):
            args = parse_arguments()
            self.assertEqual(args.url, "https://facebook.com/groups/tracer_test")

    def test_show_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            json_dir = tmp_path / "json"
            json_dir.mkdir(parents=True)
            logs_dir = tmp_path / "logs"
            logs_dir.mkdir(parents=True)
            hist_file = json_dir / "history.json"
            with open(hist_file, "w") as f:
                json.dump({"groups": {"https://facebook.com/groups/1": {"last_posted_at": "2026-11-21T10:00:00", "post_count": 1, "last_status": "SUCCESS"}}}, f)

            cfg = BotConfig(
                email="test@example.com",
                password="secretpassword",
                base_dir=tmp_path,
            )
            code = show_status(cfg, MagicMock())
            self.assertEqual(code, 0)

    @patch("main.get_driver")
    def test_run_dom_check_execution(self, mock_driver_cm):
        """Verifies run_dom_check executes DOMInspector and prints health table."""
        from main import run_dom_check
        mock_driver = MagicMock()
        mock_driver_cm.return_value.__enter__.return_value = mock_driver

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            json_dir = tmp_path / "json"
            json_dir.mkdir(parents=True)
            logs_dir = tmp_path / "logs"
            logs_dir.mkdir(parents=True)
            sel_file = json_dir / "selectors.json"
            with open(sel_file, "w") as f:
                json.dump({"content_bar": ["//div"]}, f)

            cfg = BotConfig(
                email="test@example.com",
                password="secretpassword",
                base_dir=tmp_path,
            )
            mock_logger = MagicMock()
            code = run_dom_check(cfg, mock_logger)
            self.assertEqual(code, 0)

    @patch("main.setup_logger")
    @patch("main.FacebookBot")
    @patch("main.BotConfig.from_env")
    @patch("main.parse_arguments")
    def test_main_login_execution(self, mock_parse_args, mock_from_env, mock_bot_cls, mock_setup_logger):
        """Verifies main() branches properly on --login flag."""
        from main import main
        mock_args = MagicMock()
        mock_args.login = True
        mock_args.status = False
        mock_args.check_dom = False
        mock_args.check_membership = False
        mock_args.join_groups = False
        mock_parse_args.return_value = mock_args

        mock_bot_instance = MagicMock()
        mock_bot_instance.login_interactive.return_value = True
        mock_bot_cls.return_value = mock_bot_instance

        exit_code = main()
        self.assertEqual(exit_code, 0)
        mock_bot_instance.login_interactive.assert_called_once()

    @patch("main.setup_logger")
    @patch("main.FacebookBot")
    @patch("main.BotConfig.from_env")
    @patch("main.parse_arguments")
    def test_main_logout_execution(self, mock_parse_args, mock_from_env, mock_bot_cls, mock_setup_logger):
        """Verifies main() branches properly on --logout flag."""
        from main import main
        mock_args = MagicMock()
        mock_args.logout = True
        mock_args.login = False
        mock_args.status = False
        mock_args.check_dom = False
        mock_args.check_membership = False
        mock_args.join_groups = False
        mock_parse_args.return_value = mock_args

        mock_bot_instance = MagicMock()
        mock_bot_instance.logout.return_value = True
        mock_bot_cls.return_value = mock_bot_instance

        exit_code = main()
        self.assertEqual(exit_code, 0)
        mock_bot_instance.logout.assert_called_once()

    @patch("facebookbot.clear_chrome_session")
    def test_facebookbot_logout_calls_clear_chrome_session(self, mock_clear_session):
        """Verifies FacebookBot.logout invokes clear_chrome_session with profile_dir."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = BotConfig(
                email="test@example.com",
                password="secretpassword",
                base_dir=Path(tmp),
                debug_port=9224,
            )
            mock_clear_session.return_value = 5
            bot = FacebookBot(config=cfg, selectors={"dummy": []})
            res = bot.logout()
            self.assertTrue(res)
            mock_clear_session.assert_called_once_with(cfg.profile_dir, port=cfg.debug_port)



if __name__ == "__main__":
    unittest.main()
