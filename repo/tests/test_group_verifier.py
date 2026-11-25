"""
Unit tests for GroupMembershipVerifier (Module J1).

Tests passive membership inspection using mock WebDriver and DOM elements
without opening a real Chrome browser. Runs in milliseconds.
"""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = REPO_ROOT.parent
for p in (REPO_ROOT, PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from services.group_verifier import GroupMembershipVerifier, MembershipStatus


class TestGroupMembershipVerifier(unittest.TestCase):
    def setUp(self):
        self.mock_selectors = {
            "content_bar": ["//div[@role='button']//span[contains(text(), 'Write something')]"],
            "joined_indicator": ["//div[@aria-label='Joined']"],
            "join_pending_indicator": ["//div[@aria-label='Pending']"],
            "join_group_button": ["//div[@aria-label='Join group']"],
            "action_blocked_dialog": ["//div[@role='dialog'][contains(., 'blocked')]"],
        }
        self.verifier = GroupMembershipVerifier(selectors=self.mock_selectors)

    def test_detect_already_joined_via_content_bar(self):
        mock_driver = MagicMock()
        mock_el = MagicMock()
        mock_el.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if xpath == "//div[@role='button']//span[contains(text(), 'Write something')]":
                return [mock_el]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.ALREADY_JOINED)

    def test_detect_already_joined_via_joined_button(self):
        mock_driver = MagicMock()
        mock_el = MagicMock()
        mock_el.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if xpath == "//div[@aria-label='Joined']":
                return [mock_el]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.ALREADY_JOINED)

    def test_detect_pending_approval(self):
        mock_driver = MagicMock()
        mock_el = MagicMock()
        mock_el.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if xpath == "//div[@aria-label='Pending']":
                return [mock_el]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.PENDING_APPROVAL)

    def test_detect_not_joined(self):
        mock_driver = MagicMock()
        mock_el = MagicMock()
        mock_el.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if xpath == "//div[@aria-label='Join group']":
                return [mock_el]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.NOT_JOINED)

    def test_detect_action_blocked(self):
        mock_driver = MagicMock()
        mock_el = MagicMock()
        mock_el.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if "//div[@role='dialog'][contains(., 'blocked')]" in xpath:
                return [mock_el]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.ACTION_BLOCKED)

    def test_detect_unknown_when_no_elements_found(self):
        mock_driver = MagicMock()
        mock_driver.find_elements.return_value = []

        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.UNKNOWN)

    def test_detect_unknown_when_elements_not_displayed(self):
        mock_driver = MagicMock()
        mock_el = MagicMock()
        mock_el.is_displayed.return_value = False
        mock_driver.find_elements.return_value = [mock_el]

        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.UNKNOWN)

    def test_graceful_handling_on_webdriver_exception(self):
        mock_driver = MagicMock()
        mock_driver.find_elements.side_effect = WebDriverException("Connection lost")

        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.UNKNOWN)

    def test_detect_not_joined_precedence_over_generic_content_bar(self):
        """When Join button is visible, status must be NOT_JOINED even if generic region buttons exist."""
        mock_driver = MagicMock()
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if "//div[@aria-label='Join group']" in xpath:
                return [mock_join_btn]
            # Even if content bar xpath matches something
            if "Write something" in xpath:
                return [mock_join_btn]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.NOT_JOINED)

    def test_detect_already_joined_precedence_over_join_button(self):
        """When Joined indicator is visible, status must be ALREADY_JOINED even if Join button is on page."""
        mock_driver = MagicMock()
        mock_joined_btn = MagicMock()
        mock_joined_btn.is_displayed.return_value = True
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if "//div[@aria-label='Joined']" in xpath:
                return [mock_joined_btn]
            if "//div[@aria-label='Join group']" in xpath:
                return [mock_join_btn]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.ALREADY_JOINED)

    def test_detect_already_joined_defensive_filter_on_join_button(self):
        """If join selector catches an element saying 'Đã tham gia' or 'Joined', classify as ALREADY_JOINED."""
        mock_driver = MagicMock()
        mock_el = MagicMock()
        mock_el.is_displayed.return_value = True
        mock_el.text = "Đã tham gia"
        mock_el.get_attribute.return_value = "Đã tham gia"

        def find_elements_side_effect(by, xpath):
            if "//div[@aria-label='Join group']" in xpath:
                return [mock_el]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.ALREADY_JOINED)

    def test_detect_pending_approval_precedence_over_join_button(self):
        """When Pending indicator is visible, status must be PENDING_APPROVAL even if Join button is on page."""
        mock_driver = MagicMock()
        mock_pending_btn = MagicMock()
        mock_pending_btn.is_displayed.return_value = True
        mock_join_btn = MagicMock()
        mock_join_btn.is_displayed.return_value = True

        def find_elements_side_effect(by, xpath):
            if "//div[@aria-label='Pending']" in xpath:
                return [mock_pending_btn]
            if "//div[@aria-label='Join group']" in xpath:
                return [mock_join_btn]
            return []

        mock_driver.find_elements.side_effect = find_elements_side_effect
        status = self.verifier.check_status(mock_driver)
        self.assertEqual(status, MembershipStatus.PENDING_APPROVAL)


if __name__ == "__main__":
    unittest.main()
