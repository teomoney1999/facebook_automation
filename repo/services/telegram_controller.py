"""
Telegram Remote Control Service for Facebook Automation Bot.

Provides asynchronous, non-blocking remote control and real-time observability
via Telegram Bot API using Python standard library (zero external dependencies).
Supports /status, /start, /dryrun, /stop, /help commands, admin whitelist security,
and automated alert & screenshot dispatching.
"""

import json
import logging
import mimetypes
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request


class TelegramController:
    """
    Asynchronous Telegram Bot controller and notification dispatcher.
    Adheres to the Single Responsibility Principle: handles Telegram API interactions,
    admin authorization, and translates chat commands into bot actions.
    """

    API_BASE = "https://api.telegram.org/bot"

    def __init__(
        self,
        token: Optional[str] = None,
        admin_id: Optional[str] = None,
        on_start: Optional[Callable[[bool, Optional[int]], Any]] = None,
        on_stop: Optional[Callable[[], Any]] = None,
        get_status: Optional[Callable[[], Dict[str, Any]]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.token = token.strip() if token else None
        self.admin_id = str(admin_id).strip() if admin_id else None
        self.on_start = on_start
        self.on_stop = on_stop
        self.get_status = get_status
        self.logger = logger or logging.getLogger("TelegramController")

        self._stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._last_update_id = 0
        self._is_busy = False

    @property
    def is_configured(self) -> bool:
        """Returns True if bot token and admin ID are configured."""
        return bool(self.token and self.admin_id)

    @property
    def is_running(self) -> bool:
        """Returns True if polling thread is active."""
        return self._poll_thread is not None and self._poll_thread.is_alive()

    def set_busy(self, busy: bool):
        """Sets the execution state indicator for the bot."""
        self._is_busy = busy

    def start_polling(self) -> bool:
        """Starts the long-polling listener on a daemon thread."""
        if not self.is_configured:
            self.logger.info("Telegram Controller chưa được cấu hình token/admin_id, bỏ qua khởi chạy.")
            return False

        if self.is_running:
            self.logger.warning("Telegram Controller polling thread đã đang hoạt động.")
            return True

        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="TelegramPollThread",
            daemon=True,
        )
        self._poll_thread.start()
        self.logger.info("✅ Telegram Controller polling thread đã khởi động thành công.")
        return True

    def stop_polling(self, timeout: float = 3.0):
        """Stops the long-polling listener cleanly."""
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=timeout)
            self.logger.info("🛑 Telegram Controller polling thread đã dừng an toàn.")
        self._poll_thread = None

    def _api_request(
        self,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        timeout: float = 15.0,
    ) -> Optional[Dict[str, Any]]:
        """Executes a JSON POST/GET request against Telegram Bot API."""
        if not self.token:
            return None

        url = f"{self.API_BASE}{self.token}/{endpoint}"
        req_headers = {"User-Agent": "FacebookBot-TelegramController/1.0"}

        try:
            if data is not None:
                encoded_data = json.dumps(data).encode("utf-8")
                req_headers["Content-Type"] = "application/json"
                req = urllib.request.Request(url, data=encoded_data, headers=req_headers, method="POST")
            else:
                req = urllib.request.Request(url, headers=req_headers, method="GET")

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                res = json.loads(body)
                if not res.get("ok"):
                    self.logger.warning("Telegram API trả về lỗi: %s", res.get("description"))
                return res

        except urllib.error.HTTPError as http_err:
            self.logger.error("HTTP error khi gọi Telegram API (%s): %s", endpoint, http_err)
            return None
        except Exception as err:
            self.logger.error("Lỗi mạng khi kết nối Telegram API (%s): %s", endpoint, err)
            return None

    def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: str = "HTML",
    ) -> bool:
        """Sends a text message to the authorized admin or target chat."""
        target_chat = str(chat_id or self.admin_id)
        if not target_chat or not self.token:
            return False

        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode,
        }
        res = self._api_request("sendMessage", data=payload)
        return bool(res and res.get("ok"))

    def send_photo(
        self,
        photo_path: str,
        caption: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """Uploads a screenshot or image via multipart/form-data."""
        target_chat = str(chat_id or self.admin_id)
        if not target_chat or not self.token:
            return False

        file_obj = Path(photo_path)
        if not file_obj.exists():
            self.logger.warning("Không tìm thấy file ảnh để gửi: %s", photo_path)
            if caption:
                return self.send_message(f"{caption}\n(⚠️ Không tìm thấy ảnh chụp màn hình)", chat_id=target_chat)
            return False

        url = f"{self.API_BASE}{self.token}/sendPhoto"
        boundary = f"----WebKitFormBoundary{int(time.time() * 1000)}"

        try:
            with open(file_obj, "rb") as f:
                file_bytes = f.read()

            content_type = mimetypes.guess_type(str(file_obj))[0] or "image/png"
            filename = file_obj.name

            body_parts = []
            # Field: chat_id
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{target_chat}\r\n".encode("utf-8"))
            # Field: caption
            if caption:
                body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode("utf-8"))
            # Field: photo
            file_header = (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"photo\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            body_parts.append(file_header)
            body_parts.append(file_bytes)
            body_parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

            full_body = b"".join(body_parts)

            req = urllib.request.Request(
                url,
                data=full_body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": "FacebookBot-TelegramController/1.0",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=25.0) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return bool(result and result.get("ok"))

        except Exception as err:
            self.logger.error("Không thể gửi ảnh screenshot qua Telegram: %s", err)
            if caption:
                self.send_message(f"{caption}\n(Lỗi gửi ảnh: {err})", chat_id=target_chat)
            return False

    def _poll_loop(self):
        """Internal worker loop polling updates from Telegram."""
        self.logger.info("Telegram polling loop đã bắt đầu lắng nghe lệnh...")
        while not self._stop_event.is_set():
            try:
                params = {"offset": self._last_update_id + 1, "timeout": 10}
                res = self._api_request("getUpdates", data=params, timeout=20.0)

                if res and res.get("ok"):
                    updates = res.get("result", [])
                    for upd in updates:
                        self._last_update_id = max(self._last_update_id, upd.get("update_id", 0))
                        self._handle_update(upd)

            except Exception as err:
                self.logger.debug("Lỗi trong vòng lặp polling Telegram: %s", err)

            time.sleep(1.0)

    def _handle_update(self, update: Dict[str, Any]):
        """Parses and validates incoming message, enforces admin authorization."""
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        from_user = message.get("from", {})
        sender_id = str(from_user.get("id") or chat.get("id"))
        text = (message.get("text") or "").strip()

        if not text:
            return

        # Security Enforcement: Admin Whitelist verification
        if sender_id != self.admin_id:
            self.logger.warning(
                "🚨 Phát hiện truy cập trái phép từ User ID: %s (Username: @%s). Bỏ qua yêu cầu.",
                sender_id,
                from_user.get("username", "Unknown"),
            )
            self.send_message(
                "⛔ <b>Truy cập bị từ chối!</b>\nBạn không phải là Quản trị viên được ủy quyền cho hệ thống bot này.",
                chat_id=sender_id,
            )
            return

        # Command Dispatcher
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("/start", "/run"):
            self._handle_cmd_start(args)
        elif cmd == "/dryrun":
            self._handle_cmd_dryrun()
        elif cmd == "/status":
            self._handle_cmd_status()
        elif cmd in ("/stop", "/cancel"):
            self._handle_cmd_stop()
        elif cmd in ("/help", "/menu"):
            self._handle_cmd_help()
        else:
            self.send_message(
                f"❓ Không nhận diện được lệnh: <code>{text}</code>\nGõ /help để xem danh sách lệnh được hỗ trợ."
            )

    def _handle_cmd_start(self, args: List[str]):
        """Handles /start command with optional batch size."""
        if self._is_busy:
            self.send_message("⚠️ <b>Bot đang thực thi một phiên chạy khác!</b>\nVui lòng đợi hoặc dùng /stop để dừng.")
            return

        batch_size = None
        if args and args[0].isdigit():
            batch_size = int(args[0])

        self.send_message(
            f"🚀 <b>Đã tiếp nhận lệnh BẮT ĐẦU CHẠY</b>\n"
            f"• Batch Size: {batch_size or 'Mặc định'}\n"
            f"• Đang kích hoạt tiến trình Selenium..."
        )

        if self.on_start:
            self.on_start(False, batch_size)

    def _handle_cmd_dryrun(self):
        """Handles /dryrun command."""
        if self._is_busy:
            self.send_message("⚠️ <b>Bot đang bận!</b> Vui lòng chờ phiên hiện tại hoàn tất.")
            return

        self.send_message("🔍 <b>Đã tiếp nhận lệnh DRY-RUN</b>\nĐang kiểm tra các nhóm hợp lệ và cooldown 24h...")
        if self.on_start:
            self.on_start(True, None)

    def _handle_cmd_status(self):
        """Handles /status command."""
        status_info = self.get_status() if self.get_status else {}
        state_str = "🟢 Đang rảnh (IDLE)" if not self._is_busy else "🟡 Đang chạy (RUNNING)"

        msg = (
            f"📊 <b>BÁO CÁO TRẠNG THÁI BOT</b>\n"
            f"----------------------------------------\n"
            f"• Trạng thái: {state_str}\n"
            f"• Tổng nhóm theo dõi: {status_info.get('total_groups_tracked', 0)}\n"
            f"• Đăng thành công: {status_info.get('total_successful_posts', 0)}\n"
            f"• Đang Cooldown 24h: {status_info.get('active_cooldown_count', 0)}\n"
            f"• Nhóm gần nhất: <code>{status_info.get('last_posted_group', 'N/A')}</code>\n"
            f"• Thời gian: {status_info.get('last_posted_time', 'N/A')}\n"
        )
        self.send_message(msg)

    def _handle_cmd_stop(self):
        """Handles /stop command."""
        self.send_message(
            "🛑 <b>Đã phát tín hiệu DỪNG KHẨN CẤP!</b>\n"
            "Bot sẽ dừng an toàn ngay sau khi hoàn tất thao tác hiện tại..."
        )
        if self.on_stop:
            self.on_stop()

    def _handle_cmd_help(self):
        """Handles /help command."""
        help_text = (
            "🤖 <b>HƯỚNG DẪN ĐIỀU KHIỂN TỪ XA TELEGRAM</b>\n"
            "----------------------------------------\n"
            "• <code>/status</code> - Xem trạng thái bot & lịch sử cooldown 24h\n"
            "• <code>/start [số_bài]</code> - Bắt đầu phiên đăng (ví dụ: /start 5)\n"
            "• <code>/dryrun</code> - Kiểm tra nhóm đủ điều kiện không mở browser\n"
            "• <code>/stop</code> - Dừng bot khẩn cấp ngay lập tức\n"
            "• <code>/help</code> - Hiển thị menu trợ giúp này"
        )
        self.send_message(help_text)

    # ---------------- Notification Helpers ----------------

    def notify_post_result(
        self,
        group_url: str,
        status: str,
        details: Optional[str] = None,
        screenshot_path: Optional[str] = None,
    ):
        """Dispatches real-time post outcome alerts with optional screenshot."""
        emoji = {
            "SUCCESS": "✅",
            "PENDING_APPROVAL": "⏳",
            "MARKETPLACE_FORM": "⚠️",
            "ACTION_BLOCKED": "🚨",
            "FAILED": "❌",
        }.get(status, "ℹ️")

        msg = (
            f"{emoji} <b>KẾT QUẢ ĐĂNG BÀI: {status}</b>\n"
            f"• Nhóm: <code>{group_url}</code>\n"
            f"• Chi tiết: {details or 'N/A'}"
        )

        if screenshot_path and os.path.exists(screenshot_path):
            self.send_photo(screenshot_path, caption=msg)
        else:
            self.send_message(msg)

    def notify_circuit_breaker(self, reason: str, screenshot_path: Optional[str] = None):
        """Dispatches critical circuit breaker cutoff alert."""
        msg = (
            f"🚨 <b>CIRCUIT BREAKER KÍCH HOẠT!</b>\n"
            f"Hệ thống đã tự động ngắt bảo vệ tài khoản Facebook.\n"
            f"• Nguyên nhân: <i>{reason}</i>\n"
            f"• Toàn bộ tiến trình đã tạm dừng an toàn."
        )
        if screenshot_path and os.path.exists(screenshot_path):
            self.send_photo(screenshot_path, caption=msg)
        else:
            self.send_message(msg)

    def notify_session_summary(self, summary_data: List[Dict[str, Any]], duration_str: Optional[str] = None):
        """Dispatches formatted session summary report."""
        total = len(summary_data)
        success = sum(1 for r in summary_data if r.get("status") in ("SUCCESS", "JOINED", "ALREADY_JOINED", "JOINED_SUCCESS"))
        blocked = sum(1 for r in summary_data if r.get("status") == "ACTION_BLOCKED")
        failed = sum(1 for r in summary_data if r.get("status") in ("FAILED", "UNKNOWN", "CRITICAL_ERROR"))

        dur_line = f"• Thời gian hoàn thành: <b>{duration_str}</b>\n" if duration_str else ""
        msg = (
            f"📋 <b>BÁO CÁO KẾT THÚC PHIÊN ĐĂNG BÀI</b>\n"
            f"----------------------------------------\n"
            f"{dur_line}"
            f"• Tổng số bài xử lý : <b>{total}</b>\n"
            f"• Đăng thành công   : <b>{success}</b>\n"
            f"• Bị chặn tính năng : <b>{blocked}</b>\n"
            f"• Thất bại / Lỗi    : <b>{failed}</b>\n"
            f"• Trạng thái hiện tại: 🟢 Đã hoàn thành an toàn."
        )
        self.send_message(msg)
