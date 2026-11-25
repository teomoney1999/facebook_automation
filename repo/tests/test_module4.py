"""
Unit tests for Module 4: Post Verification & Circuit Breaker.

Verifies:
1. PostStatus enum values and string equality.
2. PostVerificationResult immutability and attributes.
3. PostVerifier:
   - ACTION_BLOCKED detection and screenshot trigger.
   - PENDING_APPROVAL notice detection.
   - SUCCESS confirmation when modal closes cleanly.
   - FAILED timeout when modal remains open.
   - is_marketplace_form detection.
   - Driver exception resilience without crashing.
4. CircuitBreaker:
   - Initial state and properties.
   - Consecutive failures increment and trip at threshold.
   - Reset consecutive failures on SUCCESS or PENDING_APPROVAL.
   - MARKETPLACE_FORM bypass without incrementing failures.
   - Immediate trip on ACTION_BLOCKED.
   - Manual trip and reset operations.
   - raise_on_trip flag raising CircuitBreakerTriggeredError.
"""

import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent
for p in (REPO_ROOT, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    import services.post_verifier as pv_module
    from services.circuit_breaker import (
        CircuitBreaker,
        CircuitBreakerState,
        CircuitBreakerTriggeredError,
    )
    from services.post_verifier import (
        PostStatus,
        PostVerificationResult,
        PostVerifier,
    )
except ImportError:
    import repo.services.post_verifier as pv_module
    from repo.services.circuit_breaker import (
        CircuitBreaker,
        CircuitBreakerState,
        CircuitBreakerTriggeredError,
    )
    from repo.services.post_verifier import (
        PostStatus,
        PostVerificationResult,
        PostVerifier,
    )


class TestPostStatusAndResult(unittest.TestCase):
    """Tests PostStatus Enum and PostVerificationResult dataclass."""

    def test_post_status_enum_values(self):
        """Verifies that all 5 post statuses match expected strings."""
        self.assertEqual(PostStatus.SUCCESS.value, "SUCCESS")
        self.assertEqual(PostStatus.PENDING_APPROVAL.value, "PENDING_APPROVAL")
        self.assertEqual(PostStatus.ACTION_BLOCKED.value, "ACTION_BLOCKED")
        self.assertEqual(PostStatus.MARKETPLACE_FORM.value, "MARKETPLACE_FORM")
        self.assertEqual(PostStatus.FAILED.value, "FAILED")

        # String enum compatibility
        self.assertEqual(PostStatus.SUCCESS, "SUCCESS")
        self.assertEqual(PostStatus.FAILED, "FAILED")

    def test_post_verification_result_immutability(self):
        """Verifies that PostVerificationResult is a frozen dataclass."""
        result = PostVerificationResult(
            status=PostStatus.SUCCESS,
            message="OK",
            screenshot_path="/path/to/shot.png",
            elapsed_time=1.23,
        )
        self.assertEqual(result.status, PostStatus.SUCCESS)
        self.assertEqual(result.message, "OK")
        self.assertEqual(result.screenshot_path, "/path/to/shot.png")
        self.assertEqual(result.elapsed_time, 1.23)

        with self.assertRaises(AttributeError):
            result.status = PostStatus.FAILED  # type: ignore


class TestPostVerifier(unittest.TestCase):
    """Unit tests for PostVerifier using Mock WebDriver."""

    def setUp(self):
        self.verifier = PostVerifier(timeout=0.1, poll_interval=0.01)
        self.mock_driver = MagicMock()

    @patch.object(pv_module, "capture_screenshot")
    def test_verify_action_blocked_dialog(self, mock_capture):
        """Tests that an action blocked popup triggers ACTION_BLOCKED and captures screenshot."""
        mock_capture.return_value = "/logs/screenshots/action_blocked.png"

        mock_blocked_elem = MagicMock()
        mock_blocked_elem.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if any(term in xpath for term in ["tạm thời bị chặn", "alertdialog"]):
                return [mock_blocked_elem]
            return []

        self.mock_driver.find_elements.side_effect = find_elements_side_effect

        result = self.verifier.verify_post_submission(self.mock_driver, "https://facebook.com/groups/test")

        self.assertEqual(result.status, PostStatus.ACTION_BLOCKED)
        self.assertIn("tạm thời chặn", result.message)
        self.assertEqual(result.screenshot_path, "/logs/screenshots/action_blocked.png")
        mock_capture.assert_called_once_with(self.mock_driver, label="action_blocked")

    def test_verify_pending_approval_notice(self):
        """Tests that a pending approval notice returns SUCCESS during current phase."""
        mock_pending_elem = MagicMock()
        mock_pending_elem.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            # No action blocked
            if any(term in xpath for term in ["tạm thời bị chặn", "alertdialog"]):
                return []
            # Pending notice
            if any(term in xpath for term in ["quản trị viên duyệt", "chờ phê duyệt"]):
                return [mock_pending_elem]
            return []

        self.mock_driver.find_elements.side_effect = find_elements_side_effect

        result = self.verifier.verify_post_submission(self.mock_driver, "https://facebook.com/groups/test")

        self.assertEqual(result.status, PostStatus.SUCCESS)
        self.assertIn("thành công", result.message)

    def test_verify_success_when_modal_closed(self):
        """Tests that modal disappearing triggers SUCCESS immediately."""
        # find_elements returns empty list for all elements (modal closed, no error dialogs)
        self.mock_driver.find_elements.return_value = []

        result = self.verifier.verify_post_submission(self.mock_driver, "https://facebook.com/groups/test")

        self.assertEqual(result.status, PostStatus.SUCCESS)
        self.assertIn("thành công", result.message)

    @patch.object(pv_module, "capture_screenshot")
    def test_verify_failed_timeout_when_modal_remains_open(self, mock_capture):
        """Tests that post is marked FAILED with screenshot if modal is still open after timeout."""
        mock_capture.return_value = "/logs/screenshots/post_failed.png"

        mock_modal_elem = MagicMock()
        mock_modal_elem.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            # No action blocked or pending
            if any(term in xpath for term in ["tạm thời bị chặn", "quản trị viên duyệt"]):
                return []
            # Modal textbox still visible
            if "textbox" in xpath:
                return [mock_modal_elem]
            return []

        self.mock_driver.find_elements.side_effect = find_elements_side_effect

        result = self.verifier.verify_post_submission(self.mock_driver, "https://facebook.com/groups/test")

        self.assertEqual(result.status, PostStatus.FAILED)
        self.assertIn("thất bại", result.message)
        self.assertEqual(result.screenshot_path, "/logs/screenshots/post_failed.png")
        mock_capture.assert_called_once_with(self.mock_driver, label="post_failed_timeout")

    def test_verify_none_driver(self):
        """Verifies safe failure return when driver is None."""
        result = self.verifier.verify_post_submission(None)
        self.assertEqual(result.status, PostStatus.FAILED)
        self.assertIn("driver instance is None", result.message)

    def test_is_marketplace_form_detection(self):
        """Tests detection of Facebook Marketplace listing forms."""
        mock_price_elem = MagicMock()
        mock_price_elem.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if "Price" in xpath:
                return [mock_price_elem]
            return []

        self.mock_driver.find_elements.side_effect = find_elements_side_effect
        self.assertTrue(self.verifier.is_marketplace_form(self.mock_driver))

        # None driver check
        self.assertFalse(self.verifier.is_marketplace_form(None))


class TestCircuitBreaker(unittest.TestCase):
    """Unit tests for CircuitBreaker failure tracking and automated kill-switch."""

    def test_initial_state(self):
        """Verifies initial circuit breaker state is CLOSED with 0 failures."""
        cb = CircuitBreaker(max_consecutive_failures=3)
        self.assertEqual(cb.state, CircuitBreakerState.CLOSED)
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertTrue(cb.can_proceed())
        self.assertFalse(cb.is_tripped())

    def test_consecutive_failures_trips_breaker(self):
        """Tests that reaching max_consecutive_failures trips the circuit breaker to OPEN."""
        cb = CircuitBreaker(max_consecutive_failures=3)

        tripped1 = cb.record_result(PostStatus.FAILED, context="group1")
        self.assertFalse(tripped1)
        self.assertEqual(cb.consecutive_failures, 1)
        self.assertTrue(cb.can_proceed())

        tripped2 = cb.record_result(PostStatus.FAILED, context="group2")
        self.assertFalse(tripped2)
        self.assertEqual(cb.consecutive_failures, 2)
        self.assertTrue(cb.can_proceed())

        tripped3 = cb.record_result(PostStatus.FAILED, context="group3")
        self.assertTrue(tripped3)
        self.assertEqual(cb.consecutive_failures, 3)
        self.assertEqual(cb.state, CircuitBreakerState.OPEN)
        self.assertFalse(cb.can_proceed())
        self.assertTrue(cb.is_tripped())
        self.assertIn("Threshold exceeded", cb.trip_reason)

    def test_success_resets_consecutive_failures(self):
        """Tests that SUCCESS resets consecutive failures counter to 0."""
        cb = CircuitBreaker(max_consecutive_failures=3)

        cb.record_result(PostStatus.FAILED)
        cb.record_result(PostStatus.FAILED)
        self.assertEqual(cb.consecutive_failures, 2)

        cb.record_result(PostStatus.SUCCESS)
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertTrue(cb.can_proceed())

        # An extra failure only increases to 1, not tripping breaker
        cb.record_result(PostStatus.FAILED)
        self.assertEqual(cb.consecutive_failures, 1)
        self.assertTrue(cb.can_proceed())

    def test_pending_approval_resets_consecutive_failures(self):
        """Tests that PENDING_APPROVAL also resets consecutive failures counter."""
        cb = CircuitBreaker(max_consecutive_failures=3)

        cb.record_result(PostStatus.FAILED)
        self.assertEqual(cb.consecutive_failures, 1)

        cb.record_result(PostStatus.PENDING_APPROVAL)
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertTrue(cb.can_proceed())

    def test_marketplace_form_does_not_increment_failures(self):
        """Tests that MARKETPLACE_FORM bypasses without increasing failure count."""
        cb = CircuitBreaker(max_consecutive_failures=3)

        cb.record_result(PostStatus.FAILED)
        self.assertEqual(cb.consecutive_failures, 1)

        cb.record_result(PostStatus.MARKETPLACE_FORM)
        self.assertEqual(cb.consecutive_failures, 1)
        self.assertTrue(cb.can_proceed())

    def test_action_blocked_trips_immediately(self):
        """Tests that ACTION_BLOCKED trips the breaker on the 1st occurrence."""
        cb = CircuitBreaker(max_consecutive_failures=3)

        tripped = cb.record_result(PostStatus.ACTION_BLOCKED, context="group_blocked")
        self.assertTrue(tripped)
        self.assertEqual(cb.state, CircuitBreakerState.OPEN)
        self.assertTrue(cb.is_tripped())
        self.assertIn("Account action blocked", cb.trip_reason)

    def test_raise_on_trip_behavior(self):
        """Tests that raise_on_trip=True raises CircuitBreakerTriggeredError."""
        cb = CircuitBreaker(max_consecutive_failures=2)

        cb.record_result(PostStatus.FAILED)
        with self.assertRaises(CircuitBreakerTriggeredError):
            cb.record_result(PostStatus.FAILED, raise_on_trip=True)

        # Immediate raise on ACTION_BLOCKED
        cb_new = CircuitBreaker(max_consecutive_failures=3)
        with self.assertRaises(CircuitBreakerTriggeredError):
            cb_new.record_result(PostStatus.ACTION_BLOCKED, raise_on_trip=True)

    def test_manual_trip_and_reset(self):
        """Tests manual trip (e.g. session lost) and reset."""
        cb = CircuitBreaker(max_consecutive_failures=3)
        cb.trip("Session expired unexpectedly.")

        self.assertTrue(cb.is_tripped())
        self.assertIn("Session expired", cb.trip_reason)

        cb.reset()
        self.assertTrue(cb.can_proceed())
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertIsNone(cb.trip_reason)


if __name__ == "__main__":
    unittest.main()
