"""
Facebook Group Membership Verifier Service.

Provides robust, read-only inspection of Facebook group pages to determine
the user's current membership status without executing state-mutating actions.
Following Pragmatic Programmer & CQS (Command-Query Separation) principles.
"""

from enum import Enum
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from browser.dom_actions import load_selectors

logger = logging.getLogger("FacebookBot.GroupVerifier")


class MembershipStatus(str, Enum):
    """Explicit membership state model."""
    ALREADY_JOINED = "ALREADY_JOINED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    NOT_JOINED = "NOT_JOINED"
    ACTION_BLOCKED = "ACTION_BLOCKED"
    UNKNOWN = "UNKNOWN"


class GroupMembershipVerifier:
    """
    Read-only DOM Inspector for Facebook Group membership status.
    Determines if user is already a member, pending approval, or not yet joined.
    """

    def __init__(
        self,
        selectors: Optional[Dict[str, List[str]]] = None,
        selectors_path: Optional[str] = None,
    ):
        if selectors is not None:
            self.selectors = selectors
        elif selectors_path:
            self.selectors = load_selectors(selectors_path)
        else:
            default_path = Path(__file__).resolve().parent.parent / "json" / "selectors.json"
            self.selectors = load_selectors(str(default_path)) if default_path.exists() else {}

    def check_status(self, driver: WebDriver) -> MembershipStatus:
        """
        Passively inspects the active group DOM to classify membership status.
        Priority:
        1. Action Blocked dialog (Security/Policy cutoff)
        2. Joined indicator ("Đã tham gia" / "Joined" in group header -> ALREADY_JOINED)
        3. Pending indicator ("Đang chờ" / "Pending" / "Hủy yêu cầu" -> PENDING_APPROVAL)
        4. Join group button (If visible and neither joined nor pending -> NOT_JOINED)
        5. Specific content bar fallback ("Bạn viết gì đi..." / "Write something...")
        6. Unknown fallback
        """
        try:
            # 1. Check if Facebook temporarily blocked the account
            blocked_xpaths = self.selectors.get("action_blocked_dialog", [])
            for xpath in blocked_xpaths:
                elems = driver.find_elements(By.XPATH, xpath)
                if any(el.is_displayed() for el in elems):
                    logger.warning("🚨 Phát hiện thông báo ACTION_BLOCKED từ Facebook khi kiểm tra nhóm.")
                    return MembershipStatus.ACTION_BLOCKED

            # 2. Check if dedicated "Joined" / "Đã tham gia" indicator is visible
            joined_xpaths = self.selectors.get("joined_indicator", [])
            for xpath in joined_xpaths:
                elems = driver.find_elements(By.XPATH, xpath)
                if any(el.is_displayed() for el in elems):
                    logger.info("✅ Xác định trạng thái: ĐÃ GIA NHẬP (ALREADY_JOINED - Nút 'Đã tham gia'/'Joined' đang hiển thị).")
                    return MembershipStatus.ALREADY_JOINED

            # 3. Check if membership request is already submitted and pending
            pending_xpaths = self.selectors.get("join_pending_indicator", [])
            for xpath in pending_xpaths:
                elems = driver.find_elements(By.XPATH, xpath)
                if any(el.is_displayed() for el in elems):
                    logger.info("⏳ Xác định trạng thái: ĐANG CHỜ DUYỆT (PENDING_APPROVAL - Nút 'Đang chờ'/'Pending' đang hiển thị).")
                    return MembershipStatus.PENDING_APPROVAL

            # 4. Check if "Join group" button is actively displayed (Guarantees NOT_JOINED only if not joined/pending)
            join_xpaths = self.selectors.get("join_group_button", [])
            for xpath in join_xpaths:
                elems = driver.find_elements(By.XPATH, xpath)
                for el in elems:
                    if el.is_displayed():
                        txt = (el.text or "").strip().lower()
                        aria = (el.get_attribute("aria-label") or "").strip().lower()
                        # Extra defense: if element text/aria contains 'đã tham gia' or 'joined', it is ALREADY_JOINED
                        if any(k in txt or k in aria for k in ["đã tham gia", "joined"]):
                            logger.info("✅ Phần tử chứa chỉ báo thành viên ('Đã tham gia' / 'Joined') -> ALREADY_JOINED")
                            return MembershipStatus.ALREADY_JOINED
                        if any(k in txt or k in aria for k in ["đang chờ", "pending", "hủy yêu cầu", "cancel request"]):
                            logger.info("⏳ Phần tử chứa chỉ báo đang chờ ('Đang chờ' / 'Pending') -> PENDING_APPROVAL")
                            return MembershipStatus.PENDING_APPROVAL
                        logger.info("👉 Xác định trạng thái: CHƯA GIA NHẬP (NOT_JOINED - Nút 'Tham gia nhóm' đang hiển thị).")
                        return MembershipStatus.NOT_JOINED

            # 5. Fallback: Specific post creation bar (Only explicit write prompts, excluding generic regions)
            content_bar_xpaths = [
                xp for xp in self.selectors.get("content_bar", [])
                if "region" not in xp and "main" not in xp
            ]
            for xpath in content_bar_xpaths:
                elems = driver.find_elements(By.XPATH, xpath)
                if any(el.is_displayed() for el in elems):
                    logger.info("✅ Xác định trạng thái: ĐÃ GIA NHẬP (ALREADY_JOINED qua khung tạo bài viết).")
                    return MembershipStatus.ALREADY_JOINED

            logger.warning("❓ Không tìm thấy selector phù hợp, trạng thái không xác định (UNKNOWN).")
            return MembershipStatus.UNKNOWN

        except WebDriverException as e:
            logger.error("Lỗi WebDriver khi kiểm tra trạng thái thành viên: %s", e)
            return MembershipStatus.UNKNOWN
        except Exception as e:
            logger.error("Lỗi ngoại lệ khi kiểm tra trạng thái thành viên: %s", e)
            return MembershipStatus.UNKNOWN
