"""
Unit Tests for Module 2: Browser Automation Engine.

Tests Driver Lifecycle, Resource Sanitation, ChromeOptions building,
and Safe DOM Actions without launching a real Chrome browser instance.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    WebDriverException,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import BotConfig
from browser.driver_manager import (
    clean_chrome_locks,
    clear_chrome_session,
    clear_driver_session,
    free_port,
    build_chrome_options,
    apply_stealth_cdp,
    get_driver,
)
from browser.dom_actions import (
    DOMActionError,
    DOMSelectorError,
    INJECT_TEXT_SCRIPT,
    load_selectors,
    find_element_with_fallback,
    safe_click,
    inject_text_native_dom,
)
from browser.dom_inspector import (
    DOMStatus,
    ElementHealth,
    ComponentHealth,
    DOMHealthReport,
    DOMInspector,
)


class TestDriverManager(unittest.TestCase):
    """Test suite for Chrome driver management and sanitation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_dir = Path(self.temp_dir.name)

        self.config = BotConfig(
            email="test@example.com",
            password="test_password",
            base_dir=self.profile_dir,
            debug_port=9224,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_clean_chrome_locks_removes_existing_locks(self):
        """Verify that stale SingletonLock, SingletonCookie, SingletonSocket are deleted."""
        lock1 = self.profile_dir / "SingletonLock"
        lock2 = self.profile_dir / "SingletonCookie"
        lock3 = self.profile_dir / "SingletonSocket"
        other_file = self.profile_dir / "Preferences"

        lock1.touch()
        lock2.touch()
        lock3.touch()
        other_file.touch()

        removed = clean_chrome_locks(self.profile_dir)
        self.assertEqual(removed, 3)
        self.assertFalse(lock1.exists())
        self.assertFalse(lock2.exists())
        self.assertFalse(lock3.exists())
        self.assertTrue(other_file.exists(), "Non-lock files must NOT be deleted.")

    def test_clean_chrome_locks_nonexistent_directory(self):
        """Verify that nonexistent profile directory returns 0 and does not crash."""
        nonexistent = self.profile_dir / "does_not_exist"
        removed = clean_chrome_locks(nonexistent)
        self.assertEqual(removed, 0)

    @patch("subprocess.run")
    def test_free_port_when_port_is_empty(self, mock_run):
        """Verify free_port returns True immediately when no process is listening."""
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = free_port(9224)
        self.assertTrue(result)
        mock_run.assert_called_once()

    @patch("browser.driver_manager.time.sleep")
    @patch("os.kill")
    @patch("subprocess.run")
    def test_free_port_terminates_occupying_processes(self, mock_run, mock_kill, mock_sleep):
        """Verify free_port issues SIGTERM to processes occupying the port."""
        mock_run.side_effect = [
            MagicMock(stdout="4567\n", returncode=0),
            MagicMock(stdout="", returncode=0),
        ]
        result = free_port(9224)
        self.assertTrue(result)
        mock_kill.assert_called_with(4567, signal.SIGTERM)

    @patch("browser.driver_manager.time.sleep")
    @patch("os.kill")
    @patch("subprocess.run")
    def test_free_port_escalates_to_sigkill(self, mock_run, mock_kill, mock_sleep):
        """Verify free_port escalates to SIGKILL if processes remain alive after SIGTERM."""
        mock_run.side_effect = [
            MagicMock(stdout="9999\n", returncode=0),
            MagicMock(stdout="9999\n", returncode=0),
        ]
        result = free_port(9224, force=True)
        self.assertTrue(result)
        self.assertEqual(mock_kill.call_count, 2)
        mock_kill.assert_any_call(9999, signal.SIGTERM)
        mock_kill.assert_any_call(9999, signal.SIGKILL)

    def test_build_chrome_options_contains_required_flags(self):
        """Verify that ChromeOptions includes stealth, persistence, and stability flags."""
        options = build_chrome_options(self.config)

        args = options.arguments
        user_data_arg = f"--user-data-dir={self.config.profile_dir.resolve()}"
        debug_port_arg = f"--remote-debugging-port={self.config.debug_port}"

        self.assertIn(user_data_arg, args)
        self.assertIn(debug_port_arg, args)
        self.assertIn("--remote-allow-origins=*", args)
        self.assertIn("--disable-blink-features=AutomationControlled", args)
        self.assertIn("--disable-notifications", args)
        self.assertIn("--no-sandbox", args)

        exp_options = options.experimental_options
        self.assertIn("enable-automation", exp_options.get("excludeSwitches", []))
        self.assertFalse(exp_options.get("useAutomationExtension", True))

    def test_apply_stealth_cdp(self):
        """Verify CDP script injection for navigator.webdriver masking."""
        mock_driver = MagicMock()
        apply_stealth_cdp(mock_driver)
        mock_driver.execute_cdp_cmd.assert_called_once()
        args, kwargs = mock_driver.execute_cdp_cmd.call_args
        self.assertEqual(args[0], "Page.addScriptToEvaluateOnNewDocument")
        cdp_params = args[1] if len(args) > 1 else kwargs.get("cmd_args", {})
        self.assertIn("Object.defineProperty(navigator, 'webdriver'", cdp_params["source"])

    @patch("browser.driver_manager.webdriver.Chrome")
    @patch("browser.driver_manager.free_port")
    @patch("browser.driver_manager.clean_chrome_locks")
    def test_get_driver_lifecycle_guarantees_cleanup(
        self, mock_clean_locks, mock_free_port, mock_chrome
    ):
        """Verify Context Manager guarantees sanitation before launch and upon teardown."""
        mock_instance = MagicMock()
        mock_chrome.return_value = mock_instance

        with get_driver(self.config) as driver:
            self.assertEqual(driver, mock_instance)
            self.assertTrue(mock_free_port.called)
            self.assertTrue(mock_clean_locks.called)

        mock_instance.quit.assert_called_once()
        self.assertEqual(mock_free_port.call_count, 2)
        self.assertEqual(mock_clean_locks.call_count, 2)

    @patch("browser.driver_manager.free_port")
    @patch("browser.driver_manager.clean_chrome_locks")
    def test_clear_chrome_session_purges_target_files_and_directories(
        self, mock_clean_locks, mock_free_port
    ):
        """Verify clear_chrome_session removes cookies, session storage, and login data."""
        default_dir = self.profile_dir / "Default"
        default_dir.mkdir(parents=True, exist_ok=True)
        (default_dir / "Cookies").write_text("dummy_cookie")
        (default_dir / "Login Data").write_text("dummy_login")
        (default_dir / "Network Persistent State").write_text("dummy_net")
        session_store = default_dir / "Session Storage"
        session_store.mkdir()
        (session_store / "000001.log").write_text("log")
        pref_file = default_dir / "Preferences"
        pref_file.write_text("prefs")

        purged_count = clear_chrome_session(self.profile_dir, port=9224)
        mock_free_port.assert_called_once_with(9224)
        mock_clean_locks.assert_called_once_with(self.profile_dir)
        self.assertGreaterEqual(purged_count, 4)
        self.assertFalse((default_dir / "Cookies").exists())
        self.assertFalse((default_dir / "Login Data").exists())
        self.assertFalse(session_store.exists())
        self.assertTrue(pref_file.exists())  # Preferences should not be destroyed

    def test_clear_driver_session_invokes_driver_cleanup(self):
        """Verify clear_driver_session calls delete_all_cookies, scripts, and CDP commands."""
        mock_driver = MagicMock()
        clear_driver_session(mock_driver)
        mock_driver.delete_all_cookies.assert_called_once()
        mock_driver.execute_script.assert_called_once()
        self.assertEqual(mock_driver.execute_cdp_cmd.call_count, 2)

    @patch("browser.driver_manager.webdriver.Chrome")
    @patch("browser.driver_manager.free_port")
    @patch("browser.driver_manager.clean_chrome_locks")
    def test_get_driver_cleans_up_even_on_exception(
        self, mock_clean_locks, mock_free_port, mock_chrome
    ):
        """Verify Context Manager finishes what it starts even when an unhandled exception occurs."""
        mock_instance = MagicMock()
        mock_chrome.return_value = mock_instance

        with self.assertRaises(RuntimeError):
            with get_driver(self.config) as driver:
                raise RuntimeError("Simulated crash inside bot execution loop")

        mock_instance.quit.assert_called_once()
        self.assertEqual(mock_free_port.call_count, 2)
        self.assertEqual(mock_clean_locks.call_count, 2)


class TestDOMActions(unittest.TestCase):
    """Test suite for DOM actions, fallback resolution, and JS text injection."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.selectors_path = REPO_ROOT / "json" / "selectors.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_load_selectors_from_existing_registry(self):
        """Verify load_selectors correctly loads real selectors.json with valid structure."""
        selectors = load_selectors(self.selectors_path)
        self.assertIsInstance(selectors, dict)
        self.assertGreaterEqual(len(selectors), 11)
        for comp, sel_list in selectors.items():
            self.assertIsInstance(sel_list, list)
            self.assertGreater(len(sel_list), 0)

    def test_load_selectors_file_not_found(self):
        """Verify load_selectors raises FileNotFoundError for missing path."""
        missing = Path(self.temp_dir.name) / "nonexistent.json"
        with self.assertRaises(FileNotFoundError):
            load_selectors(missing)

    def test_load_selectors_invalid_content(self):
        """Verify load_selectors raises ValueError for non-dict or empty selector arrays."""
        bad_json = Path(self.temp_dir.name) / "bad.json"
        bad_json.write_text(json.dumps(["not", "a", "dict"]))
        with self.assertRaises(ValueError):
            load_selectors(bad_json)

        empty_array_json = Path(self.temp_dir.name) / "empty_array.json"
        empty_array_json.write_text(json.dumps({"btn": []}))
        with self.assertRaises(ValueError):
            load_selectors(empty_array_json)

    @patch("browser.dom_actions.WebDriverWait")
    def test_find_element_with_fallback_primary_success(self, mock_wait_cls):
        """Verify primary selector match returns element without trying fallbacks."""
        mock_driver = MagicMock()
        mock_element = MagicMock()
        mock_wait_instance = MagicMock()
        mock_wait_instance.until.return_value = mock_element
        mock_wait_cls.return_value = mock_wait_instance

        selectors = ["//xpath/primary", "//xpath/fallback"]
        result = find_element_with_fallback(
            mock_driver, selectors, timeout=5.0, fallback_timeout=1.0, component_name="test_comp"
        )

        self.assertEqual(result, mock_element)
        mock_wait_cls.assert_called_once_with(mock_driver, 5.0)

    @patch("browser.dom_actions.WebDriverWait")
    def test_find_element_with_fallback_triggers_fallback_and_warning(self, mock_wait_cls):
        """Verify fallback selector is tried and matched when primary selector times out."""
        mock_driver = MagicMock()
        mock_element = MagicMock()

        # Primary wait fails (TimeoutException), Fallback wait succeeds
        mock_wait_primary = MagicMock()
        mock_wait_primary.until.side_effect = TimeoutException("Primary timed out")

        mock_wait_fallback = MagicMock()
        mock_wait_fallback.until.return_value = mock_element

        mock_wait_cls.side_effect = [mock_wait_primary, mock_wait_fallback]

        selectors = ["//xpath/primary", "//xpath/fallback"]
        with self.assertLogs("FacebookBot.DOMActions", level="WARNING") as log_ctx:
            result = find_element_with_fallback(
                mock_driver, selectors, timeout=5.0, fallback_timeout=2.0, component_name="post_button"
            )

        self.assertEqual(result, mock_element)
        self.assertEqual(mock_wait_cls.call_count, 2)
        # Check that DOM DRIFT warning was logged
        warning_logged = any("DOM DRIFT WARNING" in msg for msg in log_ctx.output)
        self.assertTrue(warning_logged, "Should have logged DOM DRIFT WARNING")

    @patch("browser.dom_actions.WebDriverWait")
    def test_find_element_with_fallback_all_fail_raises_selector_error(self, mock_wait_cls):
        """Verify DOMSelectorError is raised when all selectors fail."""
        mock_driver = MagicMock()
        mock_wait_instance = MagicMock()
        mock_wait_instance.until.side_effect = TimeoutException("Timed out")
        mock_wait_cls.return_value = mock_wait_instance

        selectors = ["//xpath/1", "//xpath/2"]
        with self.assertRaises(DOMSelectorError):
            find_element_with_fallback(mock_driver, selectors, timeout=1.0, fallback_timeout=1.0)

    def test_find_element_with_fallback_precondition_checks(self):
        """Verify DOMSelectorError is raised for invalid inputs (None driver or empty list)."""
        mock_driver = MagicMock()
        with self.assertRaises(DOMSelectorError):
            find_element_with_fallback(None, ["//xpath"])

        with self.assertRaises(DOMSelectorError):
            find_element_with_fallback(mock_driver, [])

    @patch("browser.dom_actions.ActionChains")
    def test_safe_click_native_success(self, mock_action_chains):
        """Verify safe_click centers view and executes native click when unblocked."""
        mock_driver = MagicMock()
        mock_element = MagicMock()
        mock_chain = MagicMock()
        mock_action_chains.return_value = mock_chain
        mock_chain.move_to_element.return_value = mock_chain
        mock_chain.click.return_value = mock_chain

        result = safe_click(mock_driver, mock_element)
        self.assertTrue(result)

        # Scrolled into view
        mock_driver.execute_script.assert_called_once_with(
            "arguments[0].scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});",
            mock_element,
        )
        mock_chain.perform.assert_called_once()

    @patch("browser.dom_actions.ActionChains")
    def test_safe_click_intercepted_falls_back_to_js_click(self, mock_action_chains):
        """Verify safe_click falls back to JavaScript click if native click is intercepted."""
        mock_driver = MagicMock()
        mock_element = MagicMock()

        mock_chain = MagicMock()
        mock_action_chains.return_value = mock_chain
        mock_chain.move_to_element.return_value = mock_chain
        mock_chain.click.return_value = mock_chain
        mock_chain.perform.side_effect = ElementClickInterceptedException("Element intercepted by modal overlay")

        result = safe_click(mock_driver, mock_element)
        self.assertTrue(result)

        # Verify JavaScript click fallback was called
        calls = mock_driver.execute_script.call_args_list
        self.assertEqual(len(calls), 2)  # 1. scrollIntoView, 2. arguments[0].click()
        self.assertEqual(calls[1], call("arguments[0].click();", mock_element))

    @patch("browser.dom_actions.ActionChains")
    def test_safe_click_both_fail_raises_dom_action_error(self, mock_action_chains):
        """Verify safe_click raises DOMActionError if both native and JS click fail."""
        mock_driver = MagicMock()
        mock_element = MagicMock()

        mock_chain = MagicMock()
        mock_action_chains.return_value = mock_chain
        mock_chain.move_to_element.return_value = mock_chain
        mock_chain.click.return_value = mock_chain
        mock_chain.perform.side_effect = ElementClickInterceptedException("Intercepted")

        # scroll succeeds (first call), but JS click fails (second call)
        mock_driver.execute_script.side_effect = [None, WebDriverException("JS execution failed")]

        with self.assertRaises(DOMActionError):
            safe_click(mock_driver, mock_element)

    def test_safe_click_preconditions(self):
        """Verify safe_click raises DOMActionError on invalid driver or element."""
        mock_driver = MagicMock()
        mock_element = MagicMock()

        with self.assertRaises(DOMActionError):
            safe_click(None, mock_element)
        with self.assertRaises(DOMActionError):
            safe_click(mock_driver, None)

    def test_inject_text_native_dom_executes_script(self):
        """Verify inject_text_native_dom calls execute_script with target element and text."""
        mock_driver = MagicMock()
        mock_element = MagicMock()
        test_content = "Bài viết thử nghiệm tiếng Việt 🚀"

        result = inject_text_native_dom(mock_driver, mock_element, test_content)
        self.assertTrue(result)

        calls = mock_driver.execute_script.call_args_list
        self.assertEqual(len(calls), 2)
        # Call 1: scrollIntoView
        self.assertIn("scrollIntoView", calls[0][0][0])
        # Call 2: INJECT_TEXT_SCRIPT
        self.assertEqual(calls[1][0][0], INJECT_TEXT_SCRIPT)
        self.assertEqual(calls[1][0][1], mock_element)
        self.assertEqual(calls[1][0][2], test_content)

    def test_inject_text_native_dom_failure_raises_dom_action_error(self):
        """Verify inject_text_native_dom wraps execution failures in DOMActionError."""
        mock_driver = MagicMock()
        mock_element = MagicMock()
        mock_driver.execute_script.side_effect = WebDriverException("Cannot focus element")

        with self.assertRaises(DOMActionError):
            inject_text_native_dom(mock_driver, mock_element, "Text")

    def test_inject_text_native_dom_preconditions(self):
        """Verify inject_text_native_dom raises DOMActionError on None driver or element."""
        mock_driver = MagicMock()
        mock_element = MagicMock()

        with self.assertRaises(DOMActionError):
            inject_text_native_dom(None, mock_element, "Text")
        with self.assertRaises(DOMActionError):
            inject_text_native_dom(mock_driver, None, "Text")

    def test_inject_text_native_dom_preserves_newlines_and_special_chars(self):
        """Verify inject_text_native_dom preserves newlines and special characters by default."""
        mock_driver = MagicMock()
        mock_element = MagicMock()
        mock_element.text = "Line 1\nLine 2"
        multiline_content = "📢 Thông báo tuyển dụng!\n🕒 Giờ làm việc: 8h-17h\n💰 Mức lương: 30.000đ/h\n📍 Địa chỉ: Cầu Giấy"

        result = inject_text_native_dom(mock_driver, mock_element, multiline_content)
        self.assertTrue(result)

        calls = mock_driver.execute_script.call_args_list
        self.assertEqual(calls[1][0][2], multiline_content)
        self.assertIn("\n", calls[1][0][2])
        self.assertEqual(calls[1][0][2].count("\n"), 3)

    def test_inject_text_native_dom_strips_newlines_only_when_requested(self):
        """Verify inject_text_native_dom strips newlines only when strip_newlines=True."""
        mock_driver = MagicMock()
        mock_element = MagicMock()
        multiline_content = "Line 1\nLine 2\nLine 3"

        result = inject_text_native_dom(mock_driver, mock_element, multiline_content, strip_newlines=True)
        self.assertTrue(result)

        calls = mock_driver.execute_script.call_args_list
        self.assertEqual(calls[1][0][2], "Line 1 Line 2 Line 3")
        self.assertNotIn("\n", calls[1][0][2])

    def test_inject_text_script_guarantees_single_paste(self):
        """Verify INJECT_TEXT_SCRIPT contains single-paste exit condition when synthetic paste is handled."""
        from browser.dom_actions import INJECT_TEXT_SCRIPT
        # Ensure pasteSuccess early exit exists to prevent double paste
        self.assertIn("pasteSuccess", INJECT_TEXT_SCRIPT)
        self.assertIn("if (pasteSuccess)", INJECT_TEXT_SCRIPT)
        self.assertIn("pasteEvt.defaultPrevented", INJECT_TEXT_SCRIPT)


class TestDOMInspector(unittest.TestCase):
    """Test suite for DOM health inspection, drift detection, and reporting."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.artifacts_dir = Path(self.temp_dir.name) / "dom_drift"

        self.sample_selectors = {
            "content_bar": ["//xpath/primary", "//xpath/fallback"],
            "post_button": ["//xpath/btn_primary", "//xpath/btn_fallback"],
        }
        self.inspector = DOMInspector(
            selectors=self.sample_selectors,
            artifacts_dir=self.artifacts_dir,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dom_status_enum_values(self):
        """Verify DOMStatus enum states and ElementHealth alias."""
        self.assertEqual(DOMStatus.PASS.value, "PASS")
        self.assertEqual(DOMStatus.WARN.value, "WARN")
        self.assertEqual(DOMStatus.BROKEN.value, "BROKEN")
        self.assertIs(ElementHealth, DOMStatus)

    def test_dom_health_report_properties_and_table(self):
        """Verify DOMHealthReport aggregation calculations and table formatting."""
        report = DOMHealthReport()
        report.components["c1"] = ComponentHealth("c1", DOMStatus.PASS, matched_index=0, matched_selector="//c1")
        report.components["c2"] = ComponentHealth("c2", DOMStatus.WARN, matched_index=1, matched_selector="//c2_fb")

        self.assertEqual(len(report.passed), 1)
        self.assertEqual(len(report.warned), 1)
        self.assertEqual(len(report.broken), 0)
        self.assertTrue(report.is_healthy)
        self.assertFalse(report.all_passed)

        table = report.format_table()
        self.assertIn("c1", table)
        self.assertIn("PASS", table)
        self.assertIn("c2", table)
        self.assertIn("WARN", table)
        self.assertIn("HEALTHY", table)

        # Add a broken component
        report.components["c3"] = ComponentHealth("c3", DOMStatus.BROKEN, error_message="Failed")
        self.assertFalse(report.is_healthy)
        table_critical = report.format_table()
        self.assertIn("CRITICAL", table_critical)

    @patch("browser.dom_inspector.WebDriverWait")
    def test_inspect_component_primary_pass(self, mock_wait_cls):
        """Verify component health is PASS when primary selector matches."""
        mock_driver = MagicMock()
        mock_wait_instance = MagicMock()
        mock_wait_instance.until.return_value = MagicMock()
        mock_wait_cls.return_value = mock_wait_instance

        health = self.inspector.inspect_component(
            mock_driver, "content_bar", ["//primary", "//fallback"], timeout=1.0
        )
        self.assertEqual(health.status, DOMStatus.PASS)
        self.assertEqual(health.matched_index, 0)
        self.assertEqual(health.matched_selector, "//primary")
        self.assertTrue(health.is_healthy)

    @patch("browser.dom_inspector.WebDriverWait")
    def test_inspect_component_fallback_warn(self, mock_wait_cls):
        """Verify component health is WARN when primary fails but fallback matches."""
        mock_driver = MagicMock()
        mock_wait_primary = MagicMock()
        mock_wait_primary.until.side_effect = TimeoutException("Primary failed")
        mock_wait_fallback = MagicMock()
        mock_wait_fallback.until.return_value = MagicMock()

        mock_wait_cls.side_effect = [mock_wait_primary, mock_wait_fallback]

        health = self.inspector.inspect_component(
            mock_driver, "content_bar", ["//primary", "//fallback"], timeout=1.0
        )
        self.assertEqual(health.status, DOMStatus.WARN)
        self.assertEqual(health.matched_index, 1)
        self.assertEqual(health.matched_selector, "//fallback")
        self.assertTrue(health.is_healthy)

    @patch("browser.dom_inspector.WebDriverWait")
    def test_inspect_component_all_fail_broken(self, mock_wait_cls):
        """Verify component health is BROKEN when all selectors fail."""
        mock_driver = MagicMock()
        mock_wait_instance = MagicMock()
        mock_wait_instance.until.side_effect = TimeoutException("Failed")
        mock_wait_cls.return_value = mock_wait_instance

        health = self.inspector.inspect_component(
            mock_driver, "content_bar", ["//primary", "//fallback"], timeout=1.0
        )
        self.assertEqual(health.status, DOMStatus.BROKEN)
        self.assertIsNone(health.matched_index)
        self.assertFalse(health.is_healthy)

    def test_inspect_component_empty_selectors(self):
        """Verify empty selector list immediately returns BROKEN."""
        mock_driver = MagicMock()
        health = self.inspector.inspect_component(mock_driver, "empty_comp", [])
        self.assertEqual(health.status, DOMStatus.BROKEN)

    @patch.object(DOMInspector, "inspect_component")
    def test_inspect_page_aggregates_health(self, mock_inspect_component):
        """Verify inspect_page checks all target components and compiles a DOMHealthReport."""
        mock_driver = MagicMock()
        mock_inspect_component.side_effect = [
            ComponentHealth("content_bar", DOMStatus.PASS, matched_index=0, matched_selector="//primary"),
            ComponentHealth("post_button", DOMStatus.WARN, matched_index=1, matched_selector="//fb"),
        ]

        report = self.inspector.inspect_page(mock_driver, timeout=1.0, capture_artifacts_on_broken=False)
        self.assertEqual(len(report.components), 2)
        self.assertEqual(len(report.passed), 1)
        self.assertEqual(len(report.warned), 1)
        self.assertEqual(len(report.broken), 0)
        self.assertTrue(report.is_healthy)

    @patch.object(DOMInspector, "save_drift_artifact")
    @patch.object(DOMInspector, "inspect_component")
    def test_inspect_page_triggers_artifact_capture_on_broken(
        self, mock_inspect_component, mock_save_artifact
    ):
        """Verify inspect_page automatically invokes save_drift_artifact if any component is BROKEN."""
        mock_driver = MagicMock()
        mock_inspect_component.side_effect = [
            ComponentHealth("content_bar", DOMStatus.PASS, matched_index=0),
            ComponentHealth("post_button", DOMStatus.BROKEN, error_message="All failed"),
        ]

        report = self.inspector.inspect_page(mock_driver, timeout=1.0, capture_artifacts_on_broken=True)
        self.assertFalse(report.is_healthy)
        mock_save_artifact.assert_called_once()

    def test_save_drift_artifact_saves_files(self):
        """Verify save_drift_artifact writes screenshot and html dump to disk."""
        mock_driver = MagicMock()
        mock_driver.page_source = "<html><body>Facebook Mock DOM</body></html>"

        broken = [ComponentHealth("test_comp", DOMStatus.BROKEN)]
        artifact_path = self.inspector.save_drift_artifact(mock_driver, broken)

        self.assertIsNotNone(artifact_path)
        self.assertTrue(artifact_path.exists())
        mock_driver.save_screenshot.assert_called_once()

        html_files = list(self.artifacts_dir.glob("drift_*.html"))
        self.assertEqual(len(html_files), 1)
        self.assertIn("Facebook Mock DOM", html_files[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
