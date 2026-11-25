"""
Unit tests for GroupJoiner (Module J2).

Tests:
1. Pre-check bypasses (ALREADY_JOINED, PENDING_APPROVAL, ACTION_BLOCKED).
2. Failure to locate join button.
3. Instant join without questionnaire modal (JOINED_SUCCESS).
4. Instant join requiring admin approval (JOIN_REQUEST_SENT).
5. Questionnaire modal handling: rules checkbox, text answer input, and submit (JOIN_REQUEST_SENT).
6. Action Blocked detection after clicking join button.
7. Verification fallback heuristics.
100% Mock WebDriver and elements, runs in milliseconds.
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent
for p in (REPO_ROOT, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from services.group_verifier import GroupMembershipVerifier, MembershipStatus
from services.group_joiner import GroupJoiner, JoinResult


class TestGroupJoiner(unittest.TestCase):
    def setUp(self):
        # Patch sleep to keep unit tests blazing fast
        self.sleep_patcher = patch("time.sleep", return_value=None)
        self.sleep_patcher.start()

        self.mock_selectors = {
            "join_group_button": ["//div[@aria-label='Join group']"],
            "joined_indicator": ["//div[@aria-label='Joined']"],
            "join_pending_indicator": ["//div[@aria-label='Pending']"],
            "membership_questionnaire_dialog": ["//div[@role='dialog']"],
            "rules_agreement_checkbox": ["//div[@role='dialog']//input[@type='checkbox']"],
            "question_text_input": ["//div[@role='dialog']//textarea"],
            "submit_join_button": ["//div[@role='dialog']//div[@aria-label='Submit']"],
            "action_blocked_dialog": ["//div[@role='dialog'][contains(., 'blocked')]"],
        }
        self.mock_verifier = MagicMock(spec=GroupMembershipVerifier)
        self.joiner = GroupJoiner(
            verifier=self.mock_verifier,
            selectors=self.mock_selectors,
        )

    def tearDown(self):
        self.sleep_patcher.stop()

    def test_precheck_already_joined(self):
        """Pre-condition: If user is already a member, return ALREADY_JOINED without clicking."""
        self.mock_verifier.check_status.return_value = MembershipStatus.ALREADY_JOINED
        mock_driver = MagicMock()

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/101")
        self.assertEqual(result.status, "ALREADY_JOINED")
        mock_driver.find_element.assert_not_called()

    def test_precheck_pending_approval(self):
        """Pre-condition: If request is pending, return PENDING_APPROVAL without clicking."""
        self.mock_verifier.check_status.return_value = MembershipStatus.PENDING_APPROVAL
        mock_driver = MagicMock()

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/102")
        self.assertEqual(result.status, "PENDING_APPROVAL")
        mock_driver.find_element.assert_not_called()

    def test_precheck_action_blocked(self):
        """Pre-condition: If account is action-blocked, return ACTION_BLOCKED without clicking."""
        self.mock_verifier.check_status.return_value = MembershipStatus.ACTION_BLOCKED
        mock_driver = MagicMock()

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/103")
        self.assertEqual(result.status, "ACTION_BLOCKED")
        mock_driver.find_element.assert_not_called()

    def test_click_join_button_not_found(self):
        """Returns FAILED when join group button cannot be located."""
        self.mock_verifier.check_status.return_value = MembershipStatus.NOT_JOINED
        mock_driver = MagicMock()
        from selenium.common.exceptions import NoSuchElementException
        mock_driver.find_element.side_effect = NoSuchElementException("Button not found")
        mock_driver.find_elements.return_value = []

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/104")
        self.assertEqual(result.status, "FAILED")
        self.assertIn("Không tìm thấy", result.message)

    @patch("services.group_joiner.safe_click")
    def test_instant_join_success(self, mock_safe_click):
        """Public group instant join: Button clicked, no questionnaire modal, outcome ALREADY_JOINED."""
        # Pre-status is NOT_JOINED, post-status is ALREADY_JOINED
        self.mock_verifier.check_status.side_effect = [
            MembershipStatus.NOT_JOINED,
            MembershipStatus.ALREADY_JOINED,
        ]
        mock_driver = MagicMock()
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True
        mock_driver.find_element.return_value = mock_join_btn

        def find_elements_side_effect(by, xpath):
            if xpath == "//div[@aria-label='Join group']":
                return [mock_join_btn]
            if xpath == "//div[@role='dialog']":
                return []  # No modal dialog
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/105")
        self.assertEqual(result.status, "JOINED_SUCCESS")
        mock_safe_click.assert_called_with(mock_driver, mock_join_btn)

    @patch("services.group_joiner.safe_click")
    def test_instant_join_pending_approval(self, mock_safe_click):
        """Private group instant join: Button clicked, outcome PENDING_APPROVAL."""
        self.mock_verifier.check_status.side_effect = [
            MembershipStatus.NOT_JOINED,
            MembershipStatus.PENDING_APPROVAL,
        ]
        mock_driver = MagicMock()
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True
        mock_driver.find_element.return_value = mock_join_btn

        def find_elements_side_effect(by, xpath):
            if xpath == "//div[@aria-label='Join group']":
                return [mock_join_btn]
            if xpath == "//div[@role='dialog']":
                return []
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/106")
        self.assertEqual(result.status, "JOIN_REQUEST_SENT")

    @patch("services.group_joiner.inject_text_native_dom")
    @patch("services.group_joiner.safe_click")
    def test_join_with_questionnaire_modal(self, mock_safe_click, mock_inject_text):
        """Group with questions & rules: Checks checkboxes, fills answer, clicks submit."""
        self.mock_verifier.check_status.side_effect = [
            MembershipStatus.NOT_JOINED,
            MembershipStatus.PENDING_APPROVAL,
        ]
        mock_driver = MagicMock()

        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True

        mock_dialog = MagicMock()
        mock_dialog.is_displayed.return_value = True

        mock_checkbox = MagicMock()
        mock_checkbox.is_displayed.return_value = True
        mock_checkbox.is_selected.return_value = False
        mock_checkbox.get_attribute.return_value = "false"

        mock_textarea = MagicMock()
        mock_textarea.is_displayed.return_value = True

        mock_submit_btn = MagicMock()
        mock_submit_btn.is_displayed.return_value = True

        def find_element_side_effect(by, xpath):
            if "//div[@aria-label='Submit']" in xpath:
                return mock_submit_btn
            return mock_join_btn

        mock_driver.find_element.side_effect = find_element_side_effect

        def find_elements_side_effect(by, xpath):
            if xpath == "//div[@aria-label='Join group']":
                return [mock_join_btn]
            if xpath == "//div[@role='dialog']":
                return [mock_dialog]
            if "//input[@type='checkbox']" in xpath:
                return [mock_checkbox]
            if "//textarea" in xpath:
                return [mock_textarea]
            if "//div[@aria-label='Submit']" in xpath:
                return [mock_submit_btn]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect

        result = self.joiner.join_group(
            mock_driver,
            "https://facebook.com/groups/107",
            default_answer="Đồng ý quy định",
        )
        self.assertEqual(result.status, "JOIN_REQUEST_SENT")
        mock_inject_text.assert_called_with(mock_driver, mock_textarea, "Đồng ý quy định")

    @patch("services.group_joiner.safe_click")
    def test_join_action_blocked_detected(self, mock_safe_click):
        """Action blocked detected after attempting join: Reports ACTION_BLOCKED."""
        self.mock_verifier.check_status.side_effect = [
            MembershipStatus.NOT_JOINED,
            MembershipStatus.ACTION_BLOCKED,
        ]
        mock_driver = MagicMock()
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True
        mock_driver.find_elements.side_effect = lambda by, xpath: [mock_join_btn] if xpath == "//div[@aria-label='Join group']" else []

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/108")
        self.assertEqual(result.status, "ACTION_BLOCKED")

    @patch("services.group_joiner.safe_click")
    def test_verify_join_fails_when_join_button_persists(self, mock_safe_click):
        """When Join button remains visible after click/modal, result must be FAILED, never success."""
        # Pre-status is NOT_JOINED, and stays NOT_JOINED during verification
        self.mock_verifier.check_status.return_value = MembershipStatus.NOT_JOINED
        mock_driver = MagicMock()
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True
        mock_driver.find_elements.side_effect = lambda by, xpath: [mock_join_btn] if xpath == "//div[@aria-label='Join group']" else []

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/109")
        self.assertEqual(result.status, "FAILED")
        self.assertIn("vẫn hiển thị", result.message)

    @patch("services.group_joiner.safe_click")
    def test_verify_join_waits_for_transition_to_joined(self, mock_safe_click):
        """Verifies polling waits until status flips from NOT_JOINED to ALREADY_JOINED."""
        # Call 1: Pre-check -> NOT_JOINED
        # Call 2: Poll 1 -> NOT_JOINED
        # Call 3: Poll 2 -> ALREADY_JOINED
        self.mock_verifier.check_status.side_effect = [
            MembershipStatus.NOT_JOINED,
            MembershipStatus.NOT_JOINED,
            MembershipStatus.ALREADY_JOINED,
        ]
        mock_driver = MagicMock()
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True
        mock_driver.find_elements.side_effect = lambda by, xpath: [mock_join_btn] if xpath == "//div[@aria-label='Join group']" else []

        result = self.joiner.join_group(mock_driver, "https://facebook.com/groups/110")
        self.assertEqual(result.status, "JOINED_SUCCESS")
        self.assertIn("Đã tham gia", result.message)

    @patch("services.group_joiner.inject_text_native_dom")
    @patch("services.group_joiner.safe_click")
    def test_questionnaire_modal_two_parts_radio_and_rules(self, mock_safe_click, mock_inject_text):
        """Tests modal handling: Part 1 (radio affirmative + text) and Part 2 (rules checkbox)."""
        self.mock_verifier.check_status.side_effect = [
            MembershipStatus.NOT_JOINED,
            MembershipStatus.ALREADY_JOINED,
        ]
        mock_driver = MagicMock()
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True

        mock_dialog = MagicMock()
        mock_dialog.is_displayed.return_value = True

        # Part 1: Radio buttons (Negative option vs Affirmative option)
        mock_radio_no = MagicMock()
        mock_radio_no.is_displayed.return_value = True
        mock_radio_no.text = "Không đồng ý"

        mock_radio_yes = MagicMock()
        mock_radio_yes.is_displayed.return_value = True
        mock_radio_yes.text = "Tôi đồng ý tuân thủ"

        # Part 1: Text input
        mock_textarea = MagicMock()
        mock_textarea.is_displayed.return_value = True

        # Part 2: Rules agreement checkbox
        mock_rules_cb = MagicMock()
        mock_rules_cb.is_displayed.return_value = True
        mock_rules_cb.text = "Tôi đồng ý với các quy tắc của nhóm"
        mock_rules_cb.is_selected.return_value = False
        mock_rules_cb.get_attribute.return_value = "false"

        # Submit button
        mock_submit_btn = MagicMock()
        mock_submit_btn.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if xpath == "//div[@aria-label='Join group']":
                return [mock_join_btn]
            if xpath == "//div[@role='dialog']":
                return [mock_dialog]
            if "radio" in xpath:
                return [mock_radio_no, mock_radio_yes]
            if "textarea" in xpath:
                return [mock_textarea]
            if "checkbox" in xpath:
                return [mock_rules_cb]
            if "Submit" in xpath or "submit" in xpath.lower() or "Gửi" in xpath:
                return [mock_submit_btn]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect

        def find_element_side_effect(by, xpath):
            if "Submit" in xpath or "submit" in xpath.lower() or "Gửi" in xpath:
                return mock_submit_btn
            return mock_join_btn

        mock_driver.find_element.side_effect = find_element_side_effect

        result = self.joiner.join_group(
            mock_driver,
            "https://facebook.com/groups/111",
            default_answer="Tôi đồng ý",
        )
        self.assertEqual(result.status, "JOINED_SUCCESS")

        # Verify radio affirmative was clicked
        mock_safe_click.assert_any_call(mock_driver, mock_radio_yes)
        # Verify rules agreement checkbox was clicked
        mock_safe_click.assert_any_call(mock_driver, mock_rules_cb)
        # Verify text was injected
        mock_inject_text.assert_called_with(mock_driver, mock_textarea, "Tôi đồng ý")
        # Verify submit button was clicked
        mock_safe_click.assert_any_call(mock_driver, mock_submit_btn)


if __name__ == "__main__":
    unittest.main()
