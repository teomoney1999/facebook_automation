"""
Facebook Automation Bot - Orchestrator Module.

Coordinates configuration, browser lifecycle, DOM actions, state persistence,
post verification, and circuit breaker protection into a cohesive, production-grade
automation workflow following Pragmatic Programmer principles.
"""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import random
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import BotConfig
from helpers.common_helpers import (
    format_duration,
    load_from_file,
    load_group_targets,
    print_join_summary_table,
    print_summary_table,
    save_group_targets,
)
from helpers.logger import capture_screenshot, setup_logger
from browser.dom_actions import (
    DOMActionError,
    attach_media_files,
    find_element_with_fallback,
    inject_text_native_dom,
    load_selectors,
    safe_click,
)
from browser.driver_manager import clear_chrome_session, get_driver
from services.circuit_breaker import CircuitBreaker, CircuitBreakerTriggeredError
from services.group_joiner import GroupJoiner, JoinResult
from services.post_verifier import PostStatus, PostVerificationResult, PostVerifier
from services.state_manager import StateManager, normalize_url


class FacebookBot:
    """
    Lean Bot Orchestrator.

    Adheres to the Single Responsibility Principle: coordinates high-level business logic
    and delegates specialized operations (browser control, DOM manipulation, persistence,
    verification, and safety cutoff) to dedicated modules.
    """

    def __init__(
        self,
        config: Optional[BotConfig] = None,
        state_manager: Optional[StateManager] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        selectors: Optional[Dict[str, List[str]]] = None,
        custom_logger: Optional[logging.Logger] = None,
        target_links: Optional[List[str]] = None,
        post_content: Optional[str] = None,
        images: Optional[List[str]] = None,
    ):
        self.config = config or BotConfig.from_env()
        self.logger = custom_logger or setup_logger("FacebookBot", str(self.config.logs_dir / "bot.log"))

        self.history_file = str(self.config.json_dir / "history.json")
        self.selectors_file = str(self.config.json_dir / "selectors.json")
        self.group_links_file = str(self.config.json_dir / "links.json")
        self.post_content_file = str(self.config.json_dir / "posts.json")

        self.target_links = target_links
        self.post_content = post_content
        self.images = images

        self.state_manager = state_manager or StateManager(self.history_file)
        self.circuit_breaker = circuit_breaker or CircuitBreaker(max_consecutive_failures=3)
        self.selectors = selectors or load_selectors(self.selectors_file)

    def check_login_status(self, driver: WebDriver, timeout: float = 6.0) -> bool:
        """
        Verifies if an authenticated Facebook session is active.

        Checks for key presence indicators (search bar, navigation banner) and confirms
        absence of standard login redirection paths and login form inputs.
        """
        current_url = driver.current_url.lower()
        if any(kw in current_url for kw in ["/login", "/checkpoint", "login.php", "/recover"]):
            self.logger.warning("Trình duyệt đang ở trang đăng nhập hoặc checkpoint: %s", current_url)
            return False

        # Strict Precondition: If login form elements are present and displayed, NOT logged in!
        login_form_selectors = self.selectors.get("email_input", []) + self.selectors.get("login_button", [])
        for xpath in login_form_selectors:
            try:
                elems = driver.find_elements(By.XPATH, xpath)
                if any(el.is_displayed() for el in elems):
                    self.logger.warning("Phát hiện form đăng nhập trên màn hình (email/login button). Trình duyệt chưa đăng nhập.")
                    return False
            except Exception:
                pass

        search_selectors = self.selectors.get("search_bars", [])
        for xpath in search_selectors:
            try:
                WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.XPATH, xpath)))
                self.logger.info("Đã phát hiện phiên đăng nhập hợp lệ (Search bar xác nhận).")
                return True
            except TimeoutException:
                continue

        # Check authenticated account controls or user profile navigation
        auth_selectors = self.selectors.get("authenticated_account", []) + self.selectors.get("home_banner", [])
        for xpath in auth_selectors:
            try:
                WebDriverWait(driver, 2.0).until(EC.presence_of_element_located((By.XPATH, xpath)))
                self.logger.info("Đã phát hiện phiên đăng nhập hợp lệ (Account controls xác nhận).")
                return True
            except TimeoutException:
                continue

        self.logger.warning("Không tìm thấy dấu hiệu phiên đăng nhập hợp lệ.")
        return False

    def fill_and_submit_login(self, driver: WebDriver) -> bool:
        """
        Locates email and password inputs, injects credentials from config,
        and clicks the submit/login button.
        """
        if not self.config.email or not self.config.password:
            self.logger.warning("Chưa cấu hình EMAIL hoặc PASSWORD trong .env. Bỏ qua tự động điền form.")
            return False

        try:
            self.logger.info("🔑 Phát hiện form đăng nhập Facebook. Tự động điền email và mật khẩu...")
            email_el = find_element_with_fallback(
                driver,
                self.selectors.get("email_input", ["//input[@name='email']"]),
                timeout=5.0,
                fallback_timeout=1.5,
                component_name="email_input",
            )
            try:
                email_el.click()
            except Exception:
                pass
            inject_text_native_dom(driver, email_el, self.config.email)
            try:
                if email_el.get_attribute("value") != self.config.email:
                    email_el.send_keys(Keys.COMMAND, "a")
                    email_el.send_keys(Keys.CONTROL, "a")
                    email_el.send_keys(Keys.BACKSPACE)
                    email_el.send_keys(self.config.email)
            except Exception:
                pass
            time.sleep(0.3)

            pass_el = find_element_with_fallback(
                driver,
                self.selectors.get("password_input", ["//input[@name='pass']"]),
                timeout=5.0,
                fallback_timeout=1.5,
                component_name="password_input",
            )
            try:
                pass_el.click()
            except Exception:
                pass
            inject_text_native_dom(driver, pass_el, self.config.password)
            try:
                if pass_el.get_attribute("value") != self.config.password:
                    pass_el.send_keys(Keys.COMMAND, "a")
                    pass_el.send_keys(Keys.CONTROL, "a")
                    pass_el.send_keys(Keys.BACKSPACE)
                    pass_el.send_keys(self.config.password)
            except Exception:
                pass
            time.sleep(0.3)

            # Try to locate login button
            self.logger.info("Tìm và nhấp nút Đăng nhập...")
            login_selectors = self.selectors.get("login_button", [
                "//div[@role='button' and (contains(@aria-label, 'Log in') or contains(@aria-label, 'Log In') or contains(., 'Log in') or contains(., 'Log In') or contains(., 'Đăng nhập') or contains(., 'Đăng Nhập'))]",
                "//*[@role='button' and (contains(@aria-label, 'Log in') or contains(@aria-label, 'Log In') or contains(@aria-label, 'Đăng nhập') or contains(@aria-label, 'Đăng Nhập'))]",
                "//button[@name='login']",
                "//button[@type='submit']",
                "//button[@id='loginbutton']",
                "//button[contains(., 'Log In') or contains(., 'Log in') or contains(., 'Đăng nhập') or contains(., 'Đăng Nhập')]",
                "//input[@type='submit' or @name='login']",
            ])
            try:
                login_btn = find_element_with_fallback(
                    driver,
                    login_selectors,
                    timeout=4.0,
                    fallback_timeout=1.0,
                    component_name="login_button",
                )
                safe_click(driver, login_btn)
            except Exception as btn_err:
                self.logger.warning(
                    "Không tìm thấy nút login (%s). Sử dụng phím ENTER tại ô mật khẩu để submit form...",
                    btn_err,
                )
                pass_el.send_keys(Keys.ENTER)

            time.sleep(3.0)
            return True
        except Exception as e:
            self.logger.warning("Không thể tự động điền và submit form đăng nhập: %s", e)
            return False

    def perform_login_flow(
        self,
        driver: WebDriver,
        auto_fill: bool = True,
        on_login_prompt: Optional[Callable[[], bool]] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        stop_event: Optional[Any] = None,
    ) -> bool:
        """
        Coordinates full login flow:
        1. Auto-fills credentials and submits login if enabled.
        2. Fast-checks if authenticated immediately (no 2FA).
        3. If 2FA/CAPTCHA or confirmation needed, pauses and prompts via
           custom callback, interactive CLI prompt, or non-blocking polling.
        4. Re-verifies authenticated session status.
        """
        if auto_fill and self.config.email and self.config.password:
            submitted = self.fill_and_submit_login(driver)
            if submitted:
                # Fast check if login was immediately successful without 2FA
                if self.check_login_status(driver, timeout=4.0):
                    self.logger.info("🎉 Đăng nhập tự động thành công vào Bảng tin Facebook!")
                    return True

        # Handle 2FA / CAPTCHA / User confirmation
        if on_login_prompt is not None:
            self.logger.info("👉 Vui lòng chủ động giải CAPTCHA hoặc nhập mã 2FA trên cửa sổ Chrome nếu có...")
            confirmed = on_login_prompt()
            if not confirmed:
                self.logger.warning("Quá trình xác nhận đăng nhập bị hủy bởi người dùng.")
                return False
            return self.check_login_status(driver, timeout=6.0)

        if event_callback:
            event_callback("login_action_required", {
                "message": "Vui lòng chủ động giải CAPTCHA hoặc nhập mã 2FA trên trình duyệt Chrome. Session sẽ được lưu cho các ca chạy tiếp theo."
            })

        # CLI prompt: Only when running on main thread directly from interactive terminal without GUI/callback
        is_interactive_cli = (
            threading.current_thread() is threading.main_thread()
            and event_callback is None
            and sys.stdin is not None
            and hasattr(sys.stdin, "isatty")
            and sys.stdin.isatty()
            and self.config.timeout > 2
            and not os.getenv("BOT_TESTING")
        )

        if is_interactive_cli:
            self.logger.info("👉 [CHỦ ĐỘNG THỰC HIỆN]: Nếu Facebook yêu cầu mã 2FA hoặc giải CAPTCHA, vui lòng thao tác trực tiếp trên cửa sổ Chrome.")
            self.logger.info("👉 Sau khi đã vào được Bảng tin Facebook, hãy nhấn [ENTER] tại đây để lưu session cho các ca chạy tiếp theo...")
            try:
                input("Nhấn [ENTER] sau khi đã vào được Bảng tin Facebook: ")
                return self.check_login_status(driver, timeout=6.0)
            except (EOFError, KeyboardInterrupt):
                self.logger.warning("Quá trình xác nhận đăng nhập bị hủy từ terminal.")
                return False

        # Non-interactive / GUI / background polling fallback
        poll_timeout = 180.0 if self.config.timeout > 2 else 0.1
        max_attempts = max(1, int(poll_timeout / 2.0))
        self.logger.info(
            "⏳ Đang chờ người dùng chủ động hoàn tất CAPTCHA / 2FA trên Chrome (tối đa %0.0fs)...",
            poll_timeout,
        )

        for _ in range(max_attempts):
            if stop_event and stop_event.is_set():
                self.logger.warning("🛑 Dừng chờ xác thực đăng nhập theo yêu cầu (Stop Requested).")
                return False
            if self.check_login_status(driver, timeout=1.0):
                self.logger.info("🎉 Đã nhận diện Bảng tin Facebook! Session đã được lưu thành công vào profile.")
                return True
            time.sleep(2.0)

        return self.check_login_status(driver, timeout=2.0)

    def login_interactive(
        self,
        auto_fill: bool = True,
        on_ready_prompt: Optional[Callable[[], bool]] = None,
        stop_event: Optional[Any] = None,
    ) -> bool:
        """
        Interactive login session.
        Launches Chrome with user-data-dir=FacebookBotProfile, navigates to facebook.com,
        pre-fills EMAIL/PASS from config, and pauses until user finishes login & 2FA.
        
        Persists authenticated session permanently into FacebookBotProfile for subsequent runs.
        """
        self.logger.info("🔑 Khởi động phiên tương tác đăng nhập Facebook...")
        with get_driver(self.config, headless=False) as driver:
            if stop_event and stop_event.is_set():
                self.logger.warning("🛑 Dừng phiên tương tác đăng nhập theo yêu cầu (Stop Requested).")
                return False

            driver.get("https://www.facebook.com")
            time.sleep(2.0)

            if stop_event and stop_event.is_set():
                self.logger.warning("🛑 Dừng phiên tương tác đăng nhập theo yêu cầu (Stop Requested).")
                return False

            # Check if already authenticated
            if self.check_login_status(driver, timeout=3.0):
                self.logger.info("✅ Tài khoản đã có session lưu từ trước trong profile (không cần đăng nhập lại)!")
                return True

            success = self.perform_login_flow(
                driver,
                auto_fill=auto_fill,
                on_login_prompt=on_ready_prompt,
                stop_event=stop_event,
            )
            if success:
                self.logger.info("🎉 Đăng nhập thành công! Session đã được lưu vĩnh viễn vào profile cho các ca chạy tiếp theo.")
                return True
            else:
                self.logger.error("❌ Vẫn chưa phát hiện phiên đăng nhập. Vui lòng thử lại.")
                return False

    def logout(self) -> bool:
        """
        Logs out of Facebook by purging session data from Chrome.
        
        Instead of brittle DOM interaction on Facebook web UI,
        this frees the debugging port, terminates lingering Chrome processes,
        cleans locks, and wipes all session cookies, local storage, and login cache
        from the Chrome user data directory.
        """
        self.logger.info("🚪 Đang đăng xuất Facebook bằng cách xóa session khỏi Chrome...")
        count = clear_chrome_session(self.config.profile_dir, port=self.config.debug_port)
        self.logger.info("✅ Đã xóa %d tệp/thư mục session Facebook khỏi Chrome profile.", count)
        return True

    # ---------------- Group Posting Helpers ----------------

    def _check_navigation_health(self, driver: WebDriver) -> Optional[PostVerificationResult]:
        """Verifies driver is on a valid page; trips circuit breaker on session loss or network drop."""
        current_url = driver.current_url.lower()
        if any(x in current_url for x in ["/login", "/checkpoint", "/recover", "login.php"]):
            self.logger.critical("🚨 Session Facebook bị mất hoặc dính Checkpoint tại URL: %s", current_url)
            shot_path = capture_screenshot(driver, "session_lost", str(self.config.screenshots_dir))
            self.circuit_breaker.trip("Facebook session expired or checkpoint triggered")
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Facebook session expired or checkpoint triggered",
                screenshot_path=shot_path,
            )

        page_title = driver.title.lower() if driver.title else ""
        if any(x in current_url for x in ["neterror", "chrome-error"]) or any(
            x in page_title for x in ["không có kết nối mạng", "no internet"]
        ):
            self.logger.critical("🚨 Mất kết nối Internet hoặc không thể tải trang: %s", current_url)
            shot_path = capture_screenshot(driver, "network_error", str(self.config.screenshots_dir))
            self.circuit_breaker.trip("Network disconnection detected")
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Network disconnection detected",
                screenshot_path=shot_path,
            )
        return None

    def _sync_group_metadata(self, driver: WebDriver, group_url: str) -> None:
        """Passively extracts group title, member count, and privacy to update links.json."""
        try:
            raw_title = driver.title or ""
            try:
                h1_el = driver.find_element(By.XPATH, "//div[@role='main']//h1 | //h1")
                if h1_el and h1_el.text.strip():
                    raw_title = h1_el.text.strip()
            except Exception:
                pass

            sub_parts = []
            try:
                spans = driver.find_elements(By.XPATH, "//div[@role='main']//span")
                for sp in spans[:40]:
                    txt = sp.text.strip()
                    if txt and any(k in txt.lower() for k in ["thành viên", "member", "người tham gia", "công khai", "public", "riêng tư", "private"]):
                        if txt not in sub_parts:
                            sub_parts.append(txt)
            except Exception:
                pass
            sub_text = " · ".join(sub_parts)

            from helpers.common_helpers import parse_group_header_info, load_group_targets, save_group_targets
            from services.group_verifier import GroupMembershipVerifier
            verifier = GroupMembershipVerifier(selectors=self.selectors)
            mem_status = verifier.check_status(driver)
            info = parse_group_header_info(raw_title, sub_text, membership=mem_status.value)

            targets = load_group_targets(self.group_links_file)
            updated = False
            for t in targets:
                if t.get("url", "").rstrip("/") == group_url.rstrip("/"):
                    if info.get("name") and info["name"] != "—":
                        t["name"] = info["name"]
                    if info.get("members") and info["members"] != "—":
                        t["members"] = info["members"]
                    if info.get("privacy") and info["privacy"] != "Chưa rõ":
                        t["privacy"] = info["privacy"]
                    if mem_status and mem_status.value != "UNKNOWN":
                        t["membership"] = mem_status.value
                    from datetime import datetime, timezone
                    t["updated_at"] = datetime.now(timezone.utc).isoformat()
                    updated = True
                    break
            if updated:
                save_group_targets(self.group_links_file, targets)
        except Exception:
            pass

    def _open_composer_modal(
        self, driver: WebDriver, verifier: PostVerifier
    ) -> Optional[PostVerificationResult]:
        """Switches discussion tab if needed, opens the post creation modal, and checks for marketplace form."""
        discussion_selectors = self.selectors.get("marketplace_discussion_tab", [])
        if discussion_selectors:
            try:
                disc_tab = find_element_with_fallback(
                    driver,
                    discussion_selectors,
                    timeout=2.0,
                    fallback_timeout=1.0,
                    component_name="marketplace_discussion_tab",
                )
                self.logger.info("Phát hiện nhóm Mua/Bán - chuyển sang tab Thảo luận...")
                safe_click(driver, disc_tab)
                time.sleep(random.uniform(1.5, 2.5))
            except Exception:
                pass

        self.logger.info("Tìm và nhấp vào Content Bar để mở khung đăng bài...")
        content_bar = find_element_with_fallback(
            driver,
            self.selectors["content_bar"],
            timeout=10.0,
            fallback_timeout=2.0,
            component_name="content_bar",
        )
        safe_click(driver, content_bar)
        time.sleep(random.uniform(1.5, 2.5))

        if verifier.is_marketplace_form(driver):
            self.logger.warning("Phát hiện nhóm bắt buộc form Bán hàng (Marketplace). Bỏ qua nhóm an toàn.")
            return PostVerificationResult(
                status=PostStatus.MARKETPLACE_FORM,
                message="Nhóm bắt buộc form Marketplace, bỏ qua an toàn không tính lỗi.",
            )
        return None

    def _compose_post(
        self,
        driver: WebDriver,
        content: str,
        images: Optional[List[str]] = None,
        stop_event: Optional[Any] = None,
    ) -> None:
        """Injects text into post editor and attaches media files if present."""
        self.logger.info("Tìm khung soạn thảo văn bản trong Modal...")
        input_box = find_element_with_fallback(
            driver,
            self.selectors["create_post_modal"],
            timeout=10.0,
            fallback_timeout=2.0,
            component_name="create_post_modal",
        )

        if content and content.strip():
            self.logger.info("Tiêm nội dung bài viết (bảo toàn 100% ký tự xuống dòng và emoji)...")
            strip_nl = getattr(self.config, "strip_newlines", False)
            inject_text_native_dom(driver, input_box, content, strip_newlines=strip_nl)
            time.sleep(random.uniform(1.5, 3.0))

        if stop_event and stop_event.is_set():
            return

        if images:
            self.logger.info("Đang đính kèm %d tệp ảnh vào bài viết...", len(images))
            file_input_selectors = self.selectors.get("file_input", [
                "//div[@role='dialog']//input[@type='file']",
                "//input[@type='file']",
            ])
            photo_btn_selectors = self.selectors.get("photo_video_button", [])
            attach_media_files(driver, file_input_selectors, photo_btn_selectors, images)
            time.sleep(random.uniform(2.0, 3.5))

    def _submit_post(
        self,
        driver: WebDriver,
        group_url: str,
        stop_event: Optional[Any] = None,
    ) -> bool:
        """Handles intermediate Next button, waits for post button readiness, and submits."""
        self.logger.info("Tìm và nhấp nút Đăng (Post)...")
        next_selectors = self.selectors.get("next_button")
        if next_selectors:
            try:
                next_btn = find_element_with_fallback(
                    driver,
                    next_selectors,
                    timeout=1.5,
                    fallback_timeout=0.5,
                    component_name="next_button",
                )
                if next_btn:
                    self.logger.info("Phát hiện nút 'Tiếp' (Next), nhấp để tiếp tục bước Đăng bài...")
                    safe_click(driver, next_btn)
                    time.sleep(random.uniform(1.0, 2.0))
            except Exception:
                pass

        post_btn = find_element_with_fallback(
            driver,
            self.selectors["post_button"],
            timeout=10.0,
            fallback_timeout=2.0,
            component_name="post_button",
        )

        try:
            for _ in range(5):
                if post_btn.get_attribute("aria-disabled") != "true":
                    break
                time.sleep(1.0)
        except Exception:
            pass

        if stop_event and stop_event.is_set():
            self.logger.warning("🛑 Dừng khẩn cấp: ĐÃ CHẶN LỆNH BẤM ĐĂNG BÀI tại nhóm %s để bảo vệ tài khoản.", group_url)
            return False

        self.state_manager.record_post(group_url, "IN_PROGRESS", error="Submission initiated")
        safe_click(driver, post_btn)
        return True

    def post_to_group(
        self,
        driver: WebDriver,
        verifier: PostVerifier,
        group_url: str,
        content: str,
        images: Optional[List[str]] = None,
        stop_event: Optional[Any] = None,
    ) -> PostVerificationResult:
        """
        Navigates to a target group and performs the post flow with native DOM interaction.
        Supports both text injection and media file attachments.
        Cooperatively checks stop_event to abort before executing sensitive actions.
        """
        if stop_event and stop_event.is_set():
            self.logger.warning("🛑 Dừng khẩn cấp: Đã hủy điều hướng tới nhóm %s.", group_url)
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Dừng khẩn cấp bởi người dùng (Stop Requested)",
            )

        self.logger.info("Đang điều hướng đến nhóm: %s", group_url)
        driver.get(group_url)
        time.sleep(random.uniform(2.0, 3.5))

        if stop_event and stop_event.is_set():
            self.logger.warning("🛑 Dừng khẩn cấp: Đã hủy thao tác tại nhóm %s sau điều hướng.", group_url)
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Dừng khẩn cấp bởi người dùng (Stop Requested)",
            )

        # 1. Health pre-checks (session / network)
        health_err = self._check_navigation_health(driver)
        if health_err:
            return health_err

        # 2. Passive group metadata extraction
        self._sync_group_metadata(driver, group_url)

        if stop_event and stop_event.is_set():
            self.logger.warning("🛑 Dừng khẩn cấp: Đã hủy mở khung đăng bài tại nhóm %s.", group_url)
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Dừng khẩn cấp bởi người dùng (Stop Requested)",
            )

        # 3. Open post modal and check marketplace form
        modal_err = self._open_composer_modal(driver, verifier)
        if modal_err:
            return modal_err

        if stop_event and stop_event.is_set():
            self.logger.warning("🛑 Dừng khẩn cấp: Đã hủy tiêm nội dung tại nhóm %s.", group_url)
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Dừng khẩn cấp bởi người dùng (Stop Requested)",
            )

        # 4. Compose post: inject text & attach media
        self._compose_post(driver, content, images, stop_event)

        if stop_event and stop_event.is_set():
            self.logger.warning("🛑 Dừng khẩn cấp: Đã hủy đính kèm tệp ảnh tại nhóm %s.", group_url)
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Dừng khẩn cấp bởi người dùng (Stop Requested)",
            )

        # 5. Submit post
        submitted = self._submit_post(driver, group_url, stop_event)
        if not submitted:
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Dừng khẩn cấp: Hủy lệnh bấm đăng bài (Stop Requested)",
            )

        # 6. Verify post outcome
        if stop_event and stop_event.is_set():
            self.logger.warning("🛑 Dừng khẩn cấp: Bỏ qua chờ xác minh đăng bài tại nhóm %s.", group_url)
            return PostVerificationResult(
                status=PostStatus.FAILED,
                message="Dừng khẩn cấp: Bỏ qua xác minh (Stop Requested)",
            )

        self.logger.info("Đang xác minh kết quả bài đăng tại nhóm: %s", group_url)
        return verifier.verify_post_submission(driver, group_url)

    # ---------------- Workflow & Lifecycle Helpers ----------------

    def _check_run_preconditions(self) -> Optional[List[Dict[str, Any]]]:
        """Validates that history file is intact and account is not in ACTION_BLOCKED cooldown."""
        if getattr(self.state_manager, "is_corrupted", False):
            self.logger.critical(
                "🚨 [CORRUPTED HISTORY] File lịch sử bị lỗi cú pháp/hỏng. Dừng bot khẩn cấp để bảo vệ Cooldown 24h."
            )
            return [{"status": "FAILED", "details": "Corrupted history file detected. Stopped to prevent cooldown wipe."}]

        is_blocked, remaining_secs = self.state_manager.is_account_blocked()
        if is_blocked:
            rem_h = round(remaining_secs / 3600.0, 1)
            self.logger.critical(
                "🚨 [ACCOUNT BLOCKED] Tài khoản đang bị Facebook khóa tính năng (còn lại %.1f giờ). Dừng chạy bot.",
                rem_h,
            )
            return [{"status": "ACTION_BLOCKED", "details": f"Account temporarily blocked for next {rem_h}h"}]
        return None

    def _resolve_posting_inputs(
        self, custom_batch_size: Optional[int]
    ) -> Tuple[List[str], str, List[str], int, Optional[List[Dict[str, Any]]]]:
        """Loads group links, content, resolves/validates image files, and applies cooldown batch filtering."""
        if self.target_links is not None:
            all_groups = self.target_links
        else:
            groups_data = load_from_file(self.group_links_file)
            if "groups" in groups_data and isinstance(groups_data["groups"], list):
                all_groups = [g["url"] if isinstance(g, dict) and "url" in g else g for g in groups_data["groups"]]
            else:
                all_groups = groups_data.get("target_links", [])

        if self.post_content is not None:
            content = self.post_content
        else:
            posts_data = load_from_file(self.post_content_file)
            content = posts_data.get("content", "")

        effective_images: List[str] = []
        if self.images is not None:
            effective_images = list(self.images)
        else:
            posts_data = load_from_file(self.post_content_file) if self.post_content is None else {}
            if "images" in posts_data and isinstance(posts_data["images"], list) and posts_data["images"]:
                effective_images = list(posts_data["images"])
            else:
                images_dir = self.config.base_dir / "images"
                if images_dir.is_dir():
                    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"}
                    auto_images = sorted([
                        str(f) for f in images_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith(".")
                    ])
                    if auto_images:
                        effective_images = auto_images
                        self.logger.info(
                            "Tự động tìm thấy %d ảnh trong thư mục repo/images/: %s",
                            len(effective_images),
                            [os.path.basename(p) for p in effective_images],
                        )

        validated_images: List[str] = []
        for img_path in effective_images:
            if os.path.isabs(img_path):
                abs_img = img_path
            elif os.path.isfile(img_path):
                abs_img = str(Path(img_path).resolve())
            elif (self.config.base_dir / img_path).is_file():
                abs_img = str((self.config.base_dir / img_path).resolve())
            else:
                abs_img = str((self.config.base_dir / img_path).resolve())
            if not os.path.isfile(abs_img):
                self.logger.error("❌ Tệp hình ảnh không tồn tại: %s", abs_img)
                return [], "", [], 0, [{"status": "FAILED", "details": f"Tệp ảnh không tồn tại: {abs_img}"}]
            validated_images.append(abs_img)

        if not content and not validated_images:
            self.logger.error("❌ Nội dung bài đăng và ảnh đều rỗng. Vui lòng cấu hình bài viết.")
            return [], "", [], 0, [{"status": "FAILED", "details": "Nội dung bài đăng và ảnh đều rỗng"}]

        effective_batch_size = custom_batch_size or self.config.max_posts_per_run
        eligible_groups = self.state_manager.get_batch_targets(
            all_groups,
            max_batch_size=effective_batch_size,
            cooldown_hours=self.config.cooldown_hours,
        )
        return eligible_groups, content, validated_images, effective_batch_size, None

    def _build_dry_run_report(
        self,
        eligible_groups: List[str],
        effective_batch_size: int,
        validated_images: List[str],
        posting_tasks: Optional[List[Any]],
    ) -> List[Dict[str, Any]]:
        """Builds and logs the simulation report for --dry-run executions."""
        if posting_tasks:
            self.logger.info("🔍 [DRY-RUN] Lập kế hoạch đăng %d tác vụ luân phiên:", len(posting_tasks))
            for t in posting_tasks:
                self.logger.info(
                    "  %d. [%s] -> %s (Ảnh: %d)",
                    getattr(t, "index", 1),
                    getattr(t, "post_name", "Bài viết"),
                    getattr(t, "group_url", ""),
                    len(getattr(t, "images", [])),
                )
            return [
                {
                    "link": getattr(t, "group_url", ""),
                    "status": "DRY_RUN",
                    "details": f"Bài: {getattr(t, 'post_name', 'Bài viết')}",
                    "images": [os.path.basename(p) for p in getattr(t, "images", [])],
                    "post_name": getattr(t, "post_name", "Bài viết"),
                }
                for t in posting_tasks
            ]

        self.logger.info(
            "🔍 [DRY-RUN] Lập kế hoạch đăng %d nhóm hợp lệ (Batch Limit: %d):",
            len(eligible_groups),
            effective_batch_size,
        )
        for idx, g in enumerate(eligible_groups, 1):
            self.logger.info("  %d. %s", idx, g)
        if validated_images:
            self.logger.info(
                "  📸 Kèm theo %d ảnh: %s",
                len(validated_images),
                [os.path.basename(p) for p in validated_images],
            )
        return [
            {
                "link": g,
                "status": "DRY_RUN",
                "details": "Validated by StateManager (cooldown passed)",
                "images": [os.path.basename(p) for p in validated_images],
            }
            for g in eligible_groups
        ]

    def _ensure_authenticated(
        self,
        driver: WebDriver,
        on_login_prompt: Optional[Callable[[], bool]],
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]],
        stop_event: Optional[Any],
    ) -> bool:
        """Navigates to facebook.com and ensures an authenticated session is active."""
        driver.get("https://www.facebook.com")
        time.sleep(2.0)
        if self.check_login_status(driver):
            self.logger.info("✅ Phát hiện session Facebook hợp lệ từ ca chạy trước. Bỏ qua đăng nhập và tiến hành đăng bài.")
            return True

        self.logger.warning("⚠️ Chưa phát hiện session đăng nhập trên profile hiện tại.")
        if getattr(self.config, "headless", False):
            self.logger.warning("⚠️ Đang chạy ở chế độ Headless (ẩn Chrome) nhưng session đăng nhập chưa có hoặc đã hết hạn.")
            self.logger.info("👉 Hãy sử dụng tính năng 'Đăng Nhập Facebook' trên giao diện hoặc chạy lại không dùng --headless để đăng nhập lưu session.")
        self.logger.info("🔑 Tự động điền thông tin đăng nhập và mở phiên cho người dùng chủ động CAPTCHA / 2FA...")
        logged_in = self.perform_login_flow(
            driver,
            auto_fill=True,
            on_login_prompt=on_login_prompt,
            event_callback=event_callback,
            stop_event=stop_event,
        )
        if logged_in:
            self.logger.info("🎉 Đăng nhập thành công! Session đã được lưu vào profile cho các ca chạy tiếp theo.")
            return True
        return False

    def _execute_posting_loop(
        self,
        driver: WebDriver,
        verifier: PostVerifier,
        eligible_groups: List[str],
        content: str,
        validated_images: List[str],
        task_items: List[Any],
        stop_event: Optional[Any],
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]],
    ) -> List[Dict[str, Any]]:
        """Iterates over target groups, posting with jitter and circuit breaker protections."""
        results_report: List[Dict[str, Any]] = []

        for idx, (group_url, task_obj) in enumerate(zip(eligible_groups, task_items), 1):
            if stop_event and stop_event.is_set():
                self.logger.warning("🛑 Dừng bot theo yêu cầu (Stop Requested).")
                break

            if self.circuit_breaker.is_tripped():
                self.logger.critical("🚨 Circuit Breaker đã ngắt! Dừng toàn bộ phiên làm việc.")
                if event_callback:
                    event_callback("circuit_breaker", {"reason": "Circuit Breaker Tripped"})
                break

            active_content = getattr(task_obj, "content", content) if task_obj else content
            active_images = getattr(task_obj, "images", validated_images) if task_obj else validated_images
            active_name = getattr(task_obj, "post_name", "Bài viết") if task_obj else "Bài viết"

            self.logger.info("--- [%d/%d] Đang xử lý: %s (Bài: %s) ---", idx, len(eligible_groups), group_url, active_name)

            try:
                res = self.post_to_group(
                    driver,
                    verifier,
                    group_url,
                    active_content,
                    images=active_images,
                    stop_event=stop_event,
                )
                self.circuit_breaker.record_result(res.status, context=group_url)
                self.state_manager.record_post(group_url, res.status.value, error=res.message)
                if res.status == PostStatus.ACTION_BLOCKED:
                    self.state_manager.mark_account_blocked(self.config.cooldown_hours)

                item_report = {
                    "link": group_url,
                    "status": res.status.value,
                    "details": res.message,
                    "screenshot_path": res.screenshot_path,
                    "index": idx,
                    "total": len(eligible_groups),
                }
                results_report.append(item_report)

                if event_callback:
                    event_callback("post_result", item_report)

                if stop_event and stop_event.is_set():
                    self.logger.warning("🛑 Dừng bot theo yêu cầu (Stop Requested).")
                    break

                if self.circuit_breaker.is_tripped():
                    self.logger.critical("🚨 Ngắt mạch khẩn cấp sau trạng thái: %s", res.status.value)
                    if event_callback:
                        event_callback("circuit_breaker", {"reason": f"Tripped after {res.status.value}"})
                    break

                # Anti-ban human jitter cooldown between posts (interruptible)
                if group_url != eligible_groups[-1]:
                    jitter = random.uniform(
                        self.config.min_delay_between_posts, self.config.max_delay_between_posts
                    )
                    self.logger.info("⏳ Chờ giãn cách an toàn %0.1fs trước bài tiếp theo...", jitter)
                    sleep_end = time.time() + jitter
                    while time.time() < sleep_end:
                        if stop_event and stop_event.is_set():
                            self.logger.warning("🛑 Dừng bot trong thời gian chờ giãn cách (Stop Requested).")
                            break
                        time.sleep(min(0.5, max(0.05, sleep_end - time.time())))

            except CircuitBreakerTriggeredError as cb_err:
                self.logger.critical("🚨 Dừng bot do Circuit Breaker kích hoạt: %s", cb_err)
                if event_callback:
                    event_callback("circuit_breaker", {"reason": str(cb_err)})
                break

            except Exception as err:
                self.logger.error("❌ Lỗi ngoại lệ khi xử lý nhóm %s: %s", group_url, err)
                shot_path = capture_screenshot(
                    driver, f"error_{group_url.split('/')[-1]}", str(self.config.screenshots_dir)
                )
                self.circuit_breaker.record_result(PostStatus.FAILED, context=group_url)
                self.state_manager.record_post(group_url, PostStatus.FAILED.value, error=str(err))

                err_report = {
                    "link": group_url,
                    "status": PostStatus.FAILED.value,
                    "details": str(err),
                    "screenshot_path": shot_path,
                    "index": idx,
                    "total": len(eligible_groups),
                }
                results_report.append(err_report)

                if event_callback:
                    event_callback("post_result", err_report)

                if self.circuit_breaker.is_tripped():
                    self.logger.critical("🚨 Circuit Breaker kích hoạt sau ngưỡng lỗi liên tiếp!")
                    if event_callback:
                        event_callback("circuit_breaker", {"reason": "Tripped after consecutive errors"})
                    break

        return results_report

    def run(
        self,
        dry_run: bool = False,
        custom_batch_size: Optional[int] = None,
        stop_event: Optional[Any] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_login_prompt: Optional[Callable[[], bool]] = None,
        posting_tasks: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes the auto-posting workflow with safety gates and lifecycle management.
        """
        start_time = time.time()
        start_iso = datetime.now(timezone.utc).isoformat()

        # 1. Precondition checks (corrupted history, account blocked)
        pre_err = self._check_run_preconditions()
        if pre_err:
            return pre_err

        # 2. Resolve inputs, validate images & filter cooldown targets
        eligible_groups, content, validated_images, effective_batch_size, input_err = (
            self._resolve_posting_inputs(custom_batch_size)
        )
        if input_err:
            return input_err

        # 3. Fast-Exit check
        if not eligible_groups:
            self.logger.info(
                "⚡ [FAST-EXIT] Không có nhóm nào hợp lệ hoặc tất cả đang trong thời gian giãn cách %dh. Kết thúc phiên.",
                self.config.cooldown_hours,
            )
            return []

        # 4. Dry-Run mode
        if dry_run:
            return self._build_dry_run_report(
                eligible_groups, effective_batch_size, validated_images, posting_tasks
            )

        # 5. Browser lifecycle execution
        task_items = posting_tasks if posting_tasks is not None else [None] * len(eligible_groups)
        self.logger.info(
            "🚀 Bắt đầu phiên đăng bài thực tế: %d nhóm mục tiêu (Batch Size: %d, Ảnh: %d)",
            len(eligible_groups),
            effective_batch_size,
            len(validated_images),
        )

        with get_driver(self.config) as driver:
            verifier = PostVerifier(timeout=self.config.timeout, selectors_path=self.selectors_file)

            if not self._ensure_authenticated(driver, on_login_prompt, event_callback, stop_event):
                self.logger.critical(
                    "❌ Đăng nhập thất bại hoặc chưa hoàn tất CAPTCHA / 2FA. "
                    "Dừng bot với trạng thái NEEDS_LOGIN để bảo vệ tài khoản."
                )
                return [{"status": "NEEDS_LOGIN", "details": "Chưa xác thực đăng nhập Facebook"}]

            results_report = self._execute_posting_loop(
                driver,
                verifier,
                eligible_groups,
                content,
                validated_images,
                task_items,
                stop_event,
                event_callback,
            )

        # 6. Compute duration and persist session
        end_time = time.time()
        end_iso = datetime.now(timezone.utc).isoformat()
        duration_sec = end_time - start_time
        duration_str = format_duration(duration_sec)

        session_rec = self.state_manager.record_session(
            session_type="POST" if not dry_run else "DRY_RUN",
            results=results_report,
            start_time=start_iso,
            end_time=end_iso,
            duration_seconds=duration_sec,
        )

        # 7. Presentation summary
        print_summary_table([{"link": r["link"], "status": r["status"]} for r in results_report], duration_str=duration_str)
        if event_callback:
            event_callback("session_summary", {
                "results": results_report,
                "session": session_rec,
                "duration_seconds": duration_sec,
                "duration_formatted": duration_str,
            })
        return results_report

    def join_groups(
        self,
        target_links: Optional[List[str]] = None,
        dry_run: bool = False,
        join_limit: Optional[int] = 10,
        default_answer: str = "Tôi đồng ý tuân thủ nội quy nhóm",
        delay_min: float = 15.0,
        delay_max: float = 35.0,
        stop_event: Optional[Any] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_login_prompt: Optional[Callable[[], bool]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Coordinates the automated Facebook group join workflow.

        Features:
        - Resolves target links from arguments, instance targets, or links.json
        - Batch slicing via join_limit (default 10)
        - Fast-Delay (2.0s - 4.0s) for ALREADY_JOINED / PENDING_APPROVAL
        - Safe-Jitter (delay_min - delay_max) for newly sent requests
        - Circuit Breaker emergency cutoff on ACTION_BLOCKED
        - Persistent metadata updates to links.json (membership status)
        - Structured presentation summary via print_join_summary_table
        """
        start_time = time.time()
        start_iso = datetime.now(timezone.utc).isoformat()

        # Step 0: Check account blocked state
        is_blocked, remaining_secs = self.state_manager.is_account_blocked()
        if is_blocked:
            rem_h = round(remaining_secs / 3600.0, 1)
            self.logger.critical(
                "🚨 [ACCOUNT BLOCKED] Tài khoản đang bị Facebook khóa tính năng (còn lại %.1f giờ). Dừng chạy bot.",
                rem_h,
            )
            return [{"link": "", "status": "ACTION_BLOCKED", "details": f"Account temporarily blocked for next {rem_h}h"}]

        # Step 1: Resolve targets
        if target_links is not None:
            all_targets = list(target_links)
        elif self.target_links is not None:
            all_targets = list(self.target_links)
        else:
            raw_groups = load_group_targets(self.group_links_file)
            all_targets = [g["url"] for g in raw_groups if g.get("url")]

        if not all_targets:
            self.logger.warning("Không có nhóm nào trong danh sách mục tiêu.")
            return []

        # Step 2: Slice by join_limit
        effective_limit = join_limit if join_limit and join_limit > 0 else len(all_targets)
        batch_targets = all_targets[:effective_limit]

        self.logger.info(
            "📋 Bắt đầu ca kiểm tra & gia nhập nhóm: %d nhóm mục tiêu (Giới hạn: %d)",
            len(batch_targets),
            effective_limit,
        )

        # Step 3: Dry-run simulation
        if dry_run:
            self.logger.info("🔍 [DRY-RUN] Lập kế hoạch kiểm tra & tham gia %d nhóm:", len(batch_targets))
            for idx, g in enumerate(batch_targets, 1):
                self.logger.info("  %d. %s", idx, g)
            report = [
                {
                    "link": g,
                    "status": "DRY_RUN",
                    "details": "Simulated join check (no browser opened)",
                    "index": idx,
                    "total": len(batch_targets),
                }
                for idx, g in enumerate(batch_targets, 1)
            ]
            print_join_summary_table(report)
            return report

        # Step 4: Browser lifecycle execution
        results_report: List[Dict[str, Any]] = []

        with get_driver(self.config) as driver:
            if not self._ensure_authenticated(driver, on_login_prompt, event_callback, stop_event):
                self.logger.critical(
                    "❌ Đăng nhập thất bại hoặc chưa hoàn tất xác thực Facebook. "
                    "Dừng quy trình gia nhập nhóm với trạng thái NEEDS_LOGIN."
                )
                return [{"link": "", "status": "NEEDS_LOGIN", "details": "Chưa xác thực đăng nhập Facebook"}]

            joiner = GroupJoiner(selectors=self.selectors)

            for idx, group_url in enumerate(batch_targets, 1):
                if stop_event and stop_event.is_set():
                    self.logger.warning("🛑 Dừng ca tham gia nhóm theo yêu cầu (Stop Requested).")
                    break

                if self.circuit_breaker.is_tripped():
                    self.logger.critical("🚨 Circuit Breaker đã ngắt! Dừng toàn bộ ca làm việc.")
                    if event_callback:
                        event_callback("circuit_breaker", {"reason": "Circuit Breaker Tripped"})
                    break

                self.logger.info("--- [%d/%d] Đang kiểm tra & xử lý nhóm: %s ---", idx, len(batch_targets), group_url)

                try:
                    driver.get(group_url)
                    time.sleep(random.uniform(2.0, 3.5))

                    join_res = joiner.join_group(driver, group_url, default_answer=default_answer)

                    # Update links.json with membership
                    try:
                        stored_targets = load_group_targets(self.group_links_file)
                        updated = False
                        for t in stored_targets:
                            if normalize_url(t.get("url", "")) == normalize_url(group_url):
                                t["membership"] = join_res.status
                                updated = True
                                break
                        if updated:
                            save_group_targets(self.group_links_file, stored_targets)
                    except Exception as meta_err:
                        self.logger.debug("Không thể đồng bộ membership vào links.json: %s", meta_err)

                    report_item = {
                        "link": group_url,
                        "status": join_res.status,
                        "details": join_res.message,
                        "screenshot_path": join_res.screenshot_path,
                        "index": idx,
                        "total": len(batch_targets),
                    }
                    results_report.append(report_item)

                    if event_callback:
                        event_callback("group_join_result", report_item)

                    if join_res.status == "ACTION_BLOCKED":
                        self.state_manager.mark_account_blocked(self.config.cooldown_hours)
                        self.circuit_breaker.trip("Facebook action blocked while joining groups")
                        self.logger.critical("🚨 Kích hoạt ngắt mạch khẩn cấp do bị Facebook chặn tính năng.")
                        break

                    # Dynamic Jitter Delay (if not last item)
                    if idx < len(batch_targets):
                        if join_res.status in ("ALREADY_JOINED", "PENDING_APPROVAL"):
                            jitter = random.uniform(2.0, 4.0)
                            self.logger.info("⚡ [FAST-DELAY] Nhóm đã vào/chờ duyệt. Chuyển tiếp sau %0.1fs...", jitter)
                        else:
                            jitter = random.uniform(delay_min, delay_max)
                            self.logger.info("⏳ [SAFE-JITTER] Đã gửi yêu cầu. Nghỉ an toàn %0.1fs trước nhóm tiếp theo...", jitter)

                        sleep_end = time.time() + jitter
                        while time.time() < sleep_end:
                            if stop_event and stop_event.is_set():
                                self.logger.warning("🛑 Dừng bot trong thời gian chờ giãn cách (Stop Requested).")
                                break
                            time.sleep(min(0.5, max(0.05, sleep_end - time.time())))

                except Exception as err:
                    self.logger.error("❌ Lỗi ngoại lệ khi xử lý nhóm %s: %s", group_url, err)
                    shot_path = capture_screenshot(
                        driver, f"join_err_{group_url.split('/')[-1]}", str(self.config.screenshots_dir)
                    )
                    report_item = {
                        "link": group_url,
                        "status": "FAILED",
                        "details": str(err),
                        "screenshot_path": shot_path,
                        "index": idx,
                        "total": len(batch_targets),
                    }
                    results_report.append(report_item)
                    if event_callback:
                        event_callback("group_join_result", report_item)

        end_time = time.time()
        end_iso = datetime.now(timezone.utc).isoformat()
        duration_sec = end_time - start_time
        duration_str = format_duration(duration_sec)

        session_rec = self.state_manager.record_session(
            session_type="JOIN_GROUPS" if not dry_run else "JOIN_DRY_RUN",
            results=results_report,
            start_time=start_iso,
            end_time=end_iso,
            duration_seconds=duration_sec,
        )

        print_join_summary_table(results_report, duration_str=duration_str)
        if event_callback:
            event_callback("join_session_summary", {
                "results": results_report,
                "session": session_rec,
                "duration_seconds": duration_sec,
                "duration_formatted": duration_str,
            })
        return results_report
