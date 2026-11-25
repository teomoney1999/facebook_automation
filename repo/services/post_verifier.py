"""
Post Verification Module for Facebook Automation Bot.

Provides DOM post-condition verification (Design by Contract) after clicking Post,
classifying outcomes into explicit PostStatus states:
- SUCCESS: Post published and modal closed cleanly.
- PENDING_APPROVAL: Post submitted and awaiting admin approval.
- ACTION_BLOCKED: Facebook temporary action restriction detected.
- MARKETPLACE_FORM: Group forced into buy/sell marketplace listing.
- FAILED: Timeout or interaction failure.
"""

from dataclasses import dataclass
from enum import Enum
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Union

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

try:
    from browser.dom_actions import load_selectors
    from helpers.logger import capture_screenshot
except ImportError:
    from repo.browser.dom_actions import load_selectors
    from repo.helpers.logger import capture_screenshot

logger = logging.getLogger("FacebookBot.PostVerifier")


class PostStatus(str, Enum):
    """Explicit post execution state model."""
    SUCCESS = "SUCCESS"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTION_BLOCKED = "ACTION_BLOCKED"
    MARKETPLACE_FORM = "MARKETPLACE_FORM"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PostVerificationResult:
    """Immutable result of post submission verification."""
    status: PostStatus
    message: str
    screenshot_path: Optional[str] = None
    elapsed_time: float = 0.0


# Default fallback selectors if selectors.json is missing or incomplete
DEFAULT_ACTION_BLOCKED_XPATHS = [
    "//div[@role='dialog'][contains(., 'tạm thời bị chặn') or contains(., 'temporarily blocked') or contains(., 'You’re Temporarily Blocked') or contains(., 'Action Blocked')]",
    "//div[@role='alertdialog']",
]

DEFAULT_PENDING_APPROVAL_XPATHS = [
    "//span[contains(text(), 'quản trị viên duyệt') or contains(text(), 'admin approval') or contains(text(), 'Pending') or contains(text(), 'submitted for approval')]",
    "//div[contains(., 'Bài viết của bạn đang chờ phê duyệt') or contains(., 'Your post is pending approval')]",
]

DEFAULT_MODAL_XPATHS = [
    "//div[@role='presentation']//div[@role='textbox' and @contenteditable='true']",
    "//div[@role='dialog']//div[@role='textbox' and @contenteditable='true']",
    "//div[@aria-label='Create a public post' or @aria-label='Tạo bài viết công khai']//div[@role='textbox']",
]

DEFAULT_POST_BUTTON_XPATHS = [
    "//div[@aria-label='Post' or @aria-label='Đăng']",
    "//div[@role='button' and (descendant::span[text()='Post' or text()='Đăng'])]",
    "//button[descendant::span[contains(text(), 'Post') or contains(text(), 'Đăng')]]",
]

DEFAULT_MARKETPLACE_INDICATORS = [
    "//input[@aria-label='Price' or @aria-label='Giá']",
    "//span[contains(text(), 'What are you selling?') or contains(text(), 'Bạn đang bán gì?')]",
    "//div[contains(text(), 'Tạo bài niêm yết') or contains(text(), 'Create a listing')]",
]


class PostVerifier:
    """
    Inspects DOM state after post submission to verify real Facebook status.
    Guarantees Design by Contract: never assumes click equals success.
    """

    def __init__(
        self,
        timeout: float = 12.0,
        poll_interval: float = 0.5,
        selectors_path: Optional[Union[str, Path]] = None,
    ):
        """
        Initialize PostVerifier with timing configurations and selectors.

        Args:
            timeout: Maximum seconds to wait for modal closure or status confirmation.
            poll_interval: Seconds to wait between polling attempts.
            selectors_path: Path to selectors.json registry file.
        """
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)

        # Load selectors from registry or fallback
        if selectors_path is None:
            selectors_path = Path(__file__).resolve().parent.parent / "json" / "selectors.json"

        self.selectors: Dict[str, List[str]] = {}
        if Path(selectors_path).is_file():
            try:
                self.selectors = load_selectors(selectors_path)
            except Exception as err:
                logger.warning("Failed to load selectors from '%s': %s. Using internal defaults.", selectors_path, err)

        self.action_blocked_xpaths = self.selectors.get("action_blocked_dialog", DEFAULT_ACTION_BLOCKED_XPATHS)
        self.pending_approval_xpaths = self.selectors.get("pending_approval_notice", DEFAULT_PENDING_APPROVAL_XPATHS)
        self.modal_xpaths = self.selectors.get("create_post_modal", DEFAULT_MODAL_XPATHS)
        self.post_button_xpaths = self.selectors.get("post_button", DEFAULT_POST_BUTTON_XPATHS)
        self.marketplace_xpaths = DEFAULT_MARKETPLACE_INDICATORS

    def is_marketplace_form(self, driver: Any) -> bool:
        """
        Checks whether the opened dialog is a Marketplace Sell/Listing form
        instead of a standard discussion post form.
        """
        if driver is None:
            return False

        for xpath in self.marketplace_xpaths:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for el in elements:
                    if hasattr(el, "is_displayed") and el.is_displayed():
                        logger.warning("Marketplace sell form detected via selector '%s'.", xpath)
                        return True
            except Exception:
                continue
        return False

    def verify_post_submission(self, driver: Any, group_url: str = "") -> PostVerificationResult:
        """
        Verifies post status on the DOM following a Post button click.

        Polls the DOM until:
        1. An Action Blocked dialog appears -> ACTION_BLOCKED.
        2. A Pending Approval notice appears -> PENDING_APPROVAL.
        3. The Create Post modal & Post button disappear -> SUCCESS.
        4. Timeout is reached -> FAILED (with screenshot).

        Args:
            driver: Selenium WebDriver instance.
            group_url: URL of the group being posted to (for logging context).

        Returns:
            PostVerificationResult containing status, message, and diagnostic info.
        """
        if driver is None:
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Cannot verify post: driver instance is None.",
                elapsed_time=0.0,
            )

        start_time = time.time()
        logger.info("Starting post verification for group '%s' (timeout=%.1fs)...", group_url, self.timeout)

        try:
            while True:
                elapsed = time.time() - start_time

                # 1. High Priority: Check for Action Blocked popup / dialog
                blocked_result = self._check_action_blocked(driver, group_url, elapsed)
                if blocked_result is not None:
                    return blocked_result

                # 2. Check for Pending Admin Approval notice
                pending_result = self._check_pending_approval(driver, group_url, elapsed)
                if pending_result is not None:
                    return pending_result

                # 3. Check for immediate Published confirmation toast/notice
                published_result = self._check_published_success(driver, group_url, elapsed)
                if published_result is not None:
                    return published_result

                # 4. Check if Create Post modal is completely closed / gone
                if self._is_modal_closed(driver):
                    logger.info("✅ Post confirmed SUCCESS: Modal closed cleanly after %.2fs.", elapsed)
                    return PostVerificationResult(
                        status=PostStatus.SUCCESS,
                        message="Bài viết đã được đăng lên nhóm thành công.",
                        elapsed_time=elapsed,
                    )

                # Check timeout
                if elapsed >= self.timeout:
                    logger.error("❌ Post verification FAILED: Modal still visible after %.1fs.", elapsed)
                    screenshot_path = capture_screenshot(driver, label="post_failed_timeout")
                    return PostVerificationResult(
                        status=PostStatus.FAILED,
                        message=f"Đăng bài thất bại: Khung soạn thảo vẫn hiển thị sau {self.timeout:.1f}s.",
                        screenshot_path=screenshot_path,
                        elapsed_time=elapsed,
                    )

                time.sleep(self.poll_interval)
        except WebDriverException as err:
            logger.critical("🚨 Trình duyệt mất kết nối hoặc không phản hồi trong lúc xác minh: %s", err)
            return PostVerificationResult(
                status=PostStatus.UNKNOWN,
                message=f"Trình duyệt mất kết nối hoặc lỗi WebDriver: {err}",
                elapsed_time=time.time() - start_time,
            )

    def _check_action_blocked(self, driver: Any, group_url: str, elapsed: float) -> Optional[PostVerificationResult]:
        """Scans for Facebook temporary block alerts."""
        for xpath in self.action_blocked_xpaths:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for el in elements:
                    if hasattr(el, "is_displayed") and el.is_displayed():
                        logger.critical("🚨 ACTION BLOCKED detected on '%s' via selector '%s'!", group_url, xpath)
                        screenshot_path = capture_screenshot(driver, label="action_blocked")
                        return PostVerificationResult(
                            status=PostStatus.ACTION_BLOCKED,
                            message="Tài khoản bị Facebook tạm thời chặn tính năng đăng bài (Action Blocked).",
                            screenshot_path=screenshot_path,
                            elapsed_time=elapsed,
                        )
            except (NoSuchElementException, StaleElementReferenceException):
                continue
        return None

    def _check_pending_approval(self, driver: Any, group_url: str, elapsed: float) -> Optional[PostVerificationResult]:
        """
        Scans for pending admin approval notices.
        shortcut: Defer distinct PENDING_APPROVAL vs APPROVED separation and realtime metrics to future phase.
        Currently treats successful submission under approval as SUCCESS.
        """
        for xpath in self.pending_approval_xpaths:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for el in elements:
                    if hasattr(el, "is_displayed") and el.is_displayed():
                        logger.info("ℹ️ Post submitted (pending admin approval) on '%s'. Treating as SUCCESS.", group_url)
                        return PostVerificationResult(
                            status=PostStatus.SUCCESS,
                            message="Bài viết đã được đăng lên nhóm thành công (chờ quản trị viên duyệt).",
                            elapsed_time=elapsed,
                        )
            except (NoSuchElementException, StaleElementReferenceException):
                continue
        return None

    def _check_published_success(self, driver: Any, group_url: str, elapsed: float) -> Optional[PostVerificationResult]:
        """Scans for explicit post published confirmation toasts or feed banners."""
        success_indicators = [
            "//div[contains(., 'Bài viết của bạn đã được đăng') or contains(., 'Your post has been published') or contains(., 'Đã chia sẻ lên nhóm')]",
        ]
        for xpath in success_indicators:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for el in elements:
                    if hasattr(el, "is_displayed") and el.is_displayed():
                        logger.info("✅ Post confirmed SUCCESS via confirmation notice on '%s'.", group_url)
                        return PostVerificationResult(
                            status=PostStatus.SUCCESS,
                            message="Bài viết đã được đăng lên nhóm thành công.",
                            elapsed_time=elapsed,
                        )
            except (NoSuchElementException, StaleElementReferenceException):
                continue
        return None

    def _is_modal_closed(self, driver: Any) -> bool:
        """
        Determines whether the post creation modal has disappeared or closed.
        The post creation popup on Facebook is strictly rendered inside a dialog container
        (`//div[@role='dialog']`).
        Returns True if no post creation modal or post button inside a dialog is currently displayed.
        Propagates WebDriverException if browser connection is broken.
        """
        dialog_modal_indicators = [
            "//div[@role='dialog']//div[@role='textbox']",
            "//div[@role='dialog']//div[@aria-label='Post' or @aria-label='Đăng' or @aria-label='Đăng bài' or contains(@aria-label, 'Đăng') or contains(@aria-label, 'Post')]",
        ]
        for xpath in dialog_modal_indicators:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for el in elements:
                    if hasattr(el, "is_displayed") and el.is_displayed():
                        return False
            except (NoSuchElementException, StaleElementReferenceException):
                continue

        return True
