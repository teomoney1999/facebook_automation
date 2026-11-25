"""
Facebook Group Auto-Joiner Service (Module J2).

Handles the interaction of joining Facebook groups:
1. Pre-condition membership status inspection.
2. Clicking the 'Join group' action button.
3. Automatically handling questionnaire dialogs (rules agreement checkboxes & answer inputs).
4. Submitting and verifying the resulting post-action status.
Following Pragmatic Programmer & Clean Architecture principles.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
import time
from typing import Dict, List, Optional

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from browser.dom_actions import find_element_with_fallback, inject_text_native_dom, load_selectors, safe_click
from services.group_verifier import GroupMembershipVerifier, MembershipStatus

logger = logging.getLogger("FacebookBot.GroupJoiner")


@dataclass
class JoinResult:
    """Explicit outcome model for group join operations."""
    status: str
    message: str
    group_url: str = ""
    screenshot_path: Optional[str] = None


class GroupJoiner:
    """
    Executes and verifies Facebook group joining actions.
    Decomposed into single-responsibility steps:
    - _click_join_button
    - _handle_questionnaire_modal
    - _verify_join_outcome
    - join_group (Coordinator)
    """

    def __init__(
        self,
        verifier: Optional[GroupMembershipVerifier] = None,
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

        self.verifier = verifier or GroupMembershipVerifier(selectors=self.selectors)

    def _click_join_button(self, driver: WebDriver) -> bool:
        """Locates and safely clicks the 'Join group' button."""
        join_selectors = self.selectors.get("join_group_button", [])
        if not join_selectors:
            logger.warning("Không tìm thấy selector cho join_group_button.")
            return False

        try:
            join_btn = find_element_with_fallback(
                driver,
                join_selectors,
                timeout=3.0,
                fallback_timeout=1.0,
                component_name="join_group_button",
            )
            safe_click(driver, join_btn)
            logger.info("👉 Đã nhấp nút 'Tham gia nhóm'.")
            time.sleep(1.5)
            return True
        except Exception as e:
            logger.warning("Không thể nhấp nút 'Tham gia nhóm': %s", e)
            return False

    def _handle_questionnaire_modal(
        self,
        driver: WebDriver,
        default_answer: str = "Tôi đồng ý tuân thủ nội quy nhóm",
    ) -> bool:
        """
        Xử lý biểu mẫu câu hỏi và quy tắc nhóm Facebook gồm 2 phần:
        - Phần 1: Trả lời câu hỏi (Textbox tự luận, Radio 1 đáp án, Checkbox lựa chọn)
        - Phần 2: Checkbox đồng ý với quy định của nhóm (Rules agreement)
        """
        try:
            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
            visible_dialogs = [d for d in dialogs if d.is_displayed()]
            if not visible_dialogs:
                # Instant join group without questionnaire modal
                return True

            active_dialog = visible_dialogs[0]
            logger.info("📋 Phát hiện Modal câu hỏi / quy tắc thành viên nhóm. Đang tự động xử lý...")

            # 0. Cuộn modal xuống đáy để DOM hiển thị đầy đủ quy tắc và checkbox
            try:
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", active_dialog)
                time.sleep(0.3)
            except Exception:
                pass

            AFFIRMATIVE_KEYWORDS = ["đồng ý", "tuân thủ", "có", "yes", "agree", "chấp nhận", "hiểu rõ", "đúng", "tất cả"]
            NEGATIVE_KEYWORDS = ["không", "từ chối", "disagree", "chưa"]

            # -----------------------------------------------------------------
            # PHẦN 1: TRẢ LỜI CÂU HỎI (TEXTBOX, RADIO BUTTON, CHECKBOX LỰA CHỌN)
            # -----------------------------------------------------------------

            # 1.1. Câu hỏi tự luận: Điền câu trả lời chuẩn vào textarea / input text
            text_selectors = self.selectors.get(
                "question_text_input",
                ["//div[@role='dialog']//textarea", "//div[@role='dialog']//input[@type='text']"],
            )
            for xpath in text_selectors:
                try:
                    inputs = driver.find_elements(By.XPATH, xpath)
                    for inp in inputs:
                        if inp.is_displayed():
                            inject_text_native_dom(driver, inp, default_answer)
                            logger.info("  ✍️ [Phần 1] Đã điền câu trả lời: '%s'", default_answer)
                            time.sleep(0.2)
                except Exception:
                    pass

            # 1.2. Câu hỏi trắc nghiệm 1 đáp án (Radio Button): Ưu tiên khẳng định hoặc chọn Option 1
            radio_selectors = self.selectors.get(
                "question_radio_option",
                [
                    "//div[@role='dialog']//div[@role='radiogroup']//div[@role='radio']",
                    "//div[@role='dialog']//div[@role='radio']",
                    "//div[@role='dialog']//input[@type='radio']",
                ],
            )
            try:
                for xpath in radio_selectors:
                    radios = driver.find_elements(By.XPATH, xpath)
                    visible_radios = [r for r in radios if r.is_displayed()]
                    if visible_radios:
                        chosen_radio = visible_radios[0]
                        for r in visible_radios:
                            raw_label = r.text if hasattr(r, "text") and r.text else ""
                            if not raw_label and hasattr(r, "get_attribute"):
                                attr = r.get_attribute("aria-label")
                                if isinstance(attr, str):
                                    raw_label = attr
                            label = raw_label.lower()
                            is_neg = any(k in label for k in NEGATIVE_KEYWORDS)
                            is_pos = any(k in label for k in AFFIRMATIVE_KEYWORDS)
                            if is_pos and not is_neg:
                                chosen_radio = r
                                break
                        safe_click(driver, chosen_radio)
                        logger.info("  🔘 [Phần 1] Đã chọn phương án trắc nghiệm (Radio).")
                        time.sleep(0.2)
                        break
            except Exception as e:
                logger.debug("Lưu ý xử lý radio: %s", e)

            # -----------------------------------------------------------------
            # PHẦN 2: CHECKBOX ĐỒNG Ý QUY TẮC NHÓM & CHECKBOX CÂU HỎI
            # -----------------------------------------------------------------
            # Cuộn lại xuống đáy đảm bảo checkbox quy tắc ở cuối modal hiển thị
            try:
                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", active_dialog)
            except Exception:
                pass

            cb_selectors = self.selectors.get(
                "rules_agreement_checkbox",
                [
                    "//div[@role='dialog']//div[@role='checkbox' and (contains(., 'đồng ý') or contains(., 'quy tắc') or contains(., 'rules') or contains(., 'agree'))]",
                    "//div[@role='dialog']//label[contains(., 'đồng ý') or contains(., 'quy tắc') or contains(., 'rules') or contains(., 'agree')]//input[@type='checkbox']",
                    "//div[@role='dialog']//input[@type='checkbox']",
                    "//div[@role='dialog']//div[@role='checkbox']",
                ],
            )
            try:
                all_cbs = []
                for xpath in cb_selectors:
                    found = driver.find_elements(By.XPATH, xpath)
                    for c in found:
                        if c.is_displayed() and c not in all_cbs:
                            all_cbs.append(c)

                rules_cbs = []
                question_cbs = []
                for cb in all_cbs:
                    raw_txt = cb.text if hasattr(cb, "text") and cb.text else ""
                    if not raw_txt and hasattr(cb, "get_attribute"):
                        attr = cb.get_attribute("aria-label")
                        if isinstance(attr, str):
                            raw_txt = attr
                    txt = raw_txt.lower()
                    if any(k in txt for k in ["quy tắc", "nội quy", "quy định", "rules", "agree"]):
                        rules_cbs.append(cb)
                    else:
                        question_cbs.append(cb)

                # 2.1. Tích tất cả checkbox quy định nhóm (Bắt buộc để kích hoạt nút Gửi)
                target_rules = rules_cbs if rules_cbs else all_cbs
                for cb in target_rules:
                    is_checked = cb.is_selected() if hasattr(cb, "is_selected") else False
                    aria_checked = cb.get_attribute("aria-checked") == "true"
                    if not is_checked and not aria_checked:
                        safe_click(driver, cb)
                        logger.info("  ☑️ [Phần 2] Đã tích chọn đồng ý quy tắc nhóm.")
                        time.sleep(0.2)

                # 2.2. Xử lý checkbox câu hỏi nếu có (Chọn phương án khẳng định hoặc Option 1)
                if question_cbs and rules_cbs:
                    chosen_q_cb = question_cbs[0]
                    for cb in question_cbs:
                        raw_txt = cb.text if hasattr(cb, "text") and cb.text else ""
                        if not raw_txt and hasattr(cb, "get_attribute"):
                            attr = cb.get_attribute("aria-label")
                            if isinstance(attr, str):
                                raw_txt = attr
                        txt = raw_txt.lower()
                        is_neg = any(k in txt for k in NEGATIVE_KEYWORDS)
                        is_pos = any(k in txt for k in AFFIRMATIVE_KEYWORDS)
                        if is_pos and not is_neg:
                            chosen_q_cb = cb
                            break
                            chosen_q_cb = cb
                            break
                    is_checked = chosen_q_cb.is_selected() if hasattr(chosen_q_cb, "is_selected") else False
                    aria_checked = chosen_q_cb.get_attribute("aria-checked") == "true"
                    if not is_checked and not aria_checked:
                        safe_click(driver, chosen_q_cb)
                        logger.info("  ☑️ [Phần 1] Đã chọn phương án trắc nghiệm (Checkbox).")
                        time.sleep(0.2)
            except Exception as e:
                logger.debug("Lưu ý xử lý checkbox: %s", e)

            # -----------------------------------------------------------------
            # PHẦN 3: GỬI BIỂU MẪU (SUBMIT FORM)
            # -----------------------------------------------------------------
            time.sleep(0.5)
            submit_selectors = self.selectors.get(
                "submit_join_button",
                [
                    "//div[@role='dialog']//div[@aria-label='Submit' or @aria-label='Gửi' or @aria-label='Hoàn tất' or @aria-label='Done']",
                    "//div[@role='dialog']//div[@role='button'][descendant::span[contains(text(), 'Submit') or contains(text(), 'Gửi') or contains(text(), 'Hoàn tất')]]",
                    "//div[@role='dialog']//button[descendant::span[contains(text(), 'Submit') or contains(text(), 'Gửi')]]",
                ],
            )
            submit_btn = find_element_with_fallback(
                driver,
                submit_selectors,
                timeout=3.0,
                fallback_timeout=1.0,
                component_name="submit_join_button",
            )
            safe_click(driver, submit_btn)
            logger.info("🚀 Đã nhấp nút 'Gửi / Hoàn tất' để hoàn thành biểu mẫu câu hỏi.")
            time.sleep(2.0)
            return True
        except Exception as e:
            logger.warning("Lưu ý khi xử lý Modal câu hỏi: %s", e)
            return False

    def _verify_join_outcome(self, driver: WebDriver, group_url: str = "", timeout: float = 5.0) -> JoinResult:
        """
        Hậu kiểm trạng thái thực tế sau khi click và submit biểu mẫu.
        Chờ nút 'Tham gia nhóm' thực sự chuyển đổi sang 'Đã tham gia' (JOINED_SUCCESS)
        hoặc 'Đang chờ' (JOIN_REQUEST_SENT).
        Tuyệt đối không báo cáo thành công giả nếu nút chưa chuyển đổi.
        """
        poll_end = time.time() + timeout
        last_status = MembershipStatus.UNKNOWN

        while time.time() <= poll_end:
            last_status = self.verifier.check_status(driver)

            if last_status == MembershipStatus.ACTION_BLOCKED:
                return JoinResult(
                    status="ACTION_BLOCKED",
                    message="Tài khoản đang bị Facebook tạm khóa tính năng tham gia nhóm.",
                    group_url=group_url,
                )

            if last_status == MembershipStatus.ALREADY_JOINED:
                return JoinResult(
                    status="JOINED_SUCCESS",
                    message="Gia nhập nhóm thành công (Nút đã chuyển sang 'Đã tham gia').",
                    group_url=group_url,
                )

            if last_status == MembershipStatus.PENDING_APPROVAL:
                return JoinResult(
                    status="JOIN_REQUEST_SENT",
                    message="Đã gửi yêu cầu gia nhập nhóm (Nút đã chuyển sang 'Đang chờ phê duyệt').",
                    group_url=group_url,
                )

            time.sleep(0.5)

        # Hết timeout: Nếu nút 'Tham gia nhóm' vẫn hiển thị -> Thất bại
        if last_status == MembershipStatus.NOT_JOINED:
            return JoinResult(
                status="FAILED",
                message="Nút 'Tham gia nhóm' vẫn hiển thị (Yêu cầu chưa được chấp nhận hoặc bị hủy).",
                group_url=group_url,
            )

        # Nếu không còn nút Tham gia nhóm nhưng cũng chưa thấy Đã tham gia/Đang chờ
        return JoinResult(
            status="FAILED",
            message="Không thể xác nhận nút 'Đã tham gia' hoặc 'Đang chờ' sau khi gửi yêu cầu.",
            group_url=group_url,
        )

    def join_group(
        self,
        driver: WebDriver,
        url: str,
        default_answer: str = "Tôi đồng ý tuân thủ nội quy nhóm",
    ) -> JoinResult:
        """
        Main coordinator for single group join operation.
        Guarantees Design by Contract:
        - Pre-check: Bypasses already-joined or pending groups immediately.
        - Execution: Click join button -> handle questionnaire modal if present.
        - Post-check: Re-verifies actual DOM outcome before reporting.
        """
        # Step 0: Pre-condition inspection
        pre_status = self.verifier.check_status(driver)
        if pre_status == MembershipStatus.ALREADY_JOINED:
            return JoinResult(
                status="ALREADY_JOINED",
                message="Tài khoản đã là thành viên của nhóm này từ trước.",
                group_url=url,
            )
        if pre_status == MembershipStatus.PENDING_APPROVAL:
            return JoinResult(
                status="PENDING_APPROVAL",
                message="Yêu cầu tham gia đã được gửi trước đó và đang chờ phê duyệt.",
                group_url=url,
            )
        if pre_status == MembershipStatus.ACTION_BLOCKED:
            return JoinResult(
                status="ACTION_BLOCKED",
                message="Tài khoản đang bị Facebook tạm khóa tính năng.",
                group_url=url,
            )

        # Step 1: Click the 'Join group' button
        clicked = self._click_join_button(driver)
        if not clicked:
            return JoinResult(
                status="FAILED",
                message="Không tìm thấy hoặc không thể nhấp nút 'Tham gia nhóm'.",
                group_url=url,
            )

        # Step 2: Handle questionnaire / group rules modal if present
        self._handle_questionnaire_modal(driver, default_answer=default_answer)

        # Step 3: Verify outcome on DOM
        return self._verify_join_outcome(driver, group_url=url)
