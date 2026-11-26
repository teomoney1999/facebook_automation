"""
Facebook Automation Bot - Production CLI Entry Point.

Provides a unified command-line interface supporting normal execution,
dry-run simulations, DOM health canary scans, historical status inspection,
and dynamic runtime batch limits.
"""

import argparse
import dataclasses
import logging
import os
from pathlib import Path
import sys
from typing import Any, Optional

# Ensure repo directory is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import BotConfig
from helpers.logger import setup_logger
from facebookbot import FacebookBot
from browser.dom_inspector import DOMInspector
from browser.driver_manager import get_driver
from services.selector_sync import sync_selectors
from services.state_manager import StateManager


class ProcessLock:
    """Advisory single-instance process lock to prevent concurrent executions."""

    def __init__(self, lock_file: Path):
        self.lock_file = Path(lock_file)
        self.fd = None

    def acquire(self) -> bool:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            import fcntl
            self.fd = open(self.lock_file, "a+")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd.seek(0)
            self.fd.truncate()
            self.fd.write(f"{os.getpid()}\n")
            self.fd.flush()
            return True
        except (IOError, OSError):
            if self.fd:
                try:
                    self.fd.close()
                except Exception:
                    pass
                self.fd = None
            return False
        except ImportError:
            # shortcut: non-blocking fcntl fallback on systems without fcntl
            return True

    def release(self) -> None:
        if self.fd:
            try:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                self.fd.close()
            except Exception:
                pass
            self.fd = None


def check_positive_int(value: str) -> int:
    """Validate that command-line argument is a positive integer (> 0)."""
    try:
        ivalue = int(value)
        if ivalue <= 0:
            raise argparse.ArgumentTypeError(f"'{value}' phải là số nguyên dương (> 0).")
        return ivalue
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' không phải là số nguyên hợp lệ.")


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments for the bot."""
    parser = argparse.ArgumentParser(
        description="Facebook Group Automation Bot - Production Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the workflow (filter eligible groups, check cooldowns) without opening Chrome or posting.",
    )
    parser.add_argument(
        "--check-dom",
        action="store_true",
        help="Run DOM Canary inspection on selectors to detect UI drift or broken paths without posting.",
    )
    parser.add_argument(
        "--check-membership",
        action="store_true",
        help="Khởi chạy Chrome thực tế để kiểm tra trạng thái thành viên nhóm (ALREADY_JOINED, PENDING_APPROVAL, NOT_JOINED).",
    )
    parser.add_argument(
        "--join-groups",
        action="store_true",
        help="Chạy quy trình tự động kiểm tra và gia nhập các nhóm Facebook trong danh sách.",
    )
    parser.add_argument(
        "--join-limit",
        type=check_positive_int,
        default=10,
        help="Giới hạn số lượng nhóm tối đa sẽ xử lý trong ca gia nhập (mặc định: 10).",
    )
    parser.add_argument(
        "--default-answer",
        type=str,
        default="Tôi đồng ý tuân thủ nội quy nhóm",
        help="Câu trả lời mẫu cho biểu mẫu câu hỏi gia nhập nhóm.",
    )
    parser.add_argument(
        "--join-delay-min",
        type=float,
        default=15.0,
        help="Thời gian giãn cách tối thiểu (giây) sau khi gửi yêu cầu tham gia nhóm mới.",
    )
    parser.add_argument(
        "--join-delay-max",
        type=float,
        default=35.0,
        help="Thời gian giãn cách tối đa (giây) sau khi gửi yêu cầu tham gia nhóm mới.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display historical posting status and 24h cooldown statistics from history.json.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open Chrome in interactive mode to log in to Facebook and save profile session.",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Đăng xuất tài khoản Facebook bằng cách xóa sạch session, cookies và cache khỏi Chrome profile.",
    )
    parser.add_argument(
        "--batch-size",
        type=check_positive_int,
        default=None,
        help="Override the maximum number of groups to post in this session (> 0).",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Post to a single group URL directly (Tracer bullet test mode).",
    )
    parser.add_argument(
        "--image",
        "--images",
        nargs="+",
        dest="images",
        default=None,
        help="Đường dẫn đến một hoặc nhiều file ảnh đính kèm (mặc định tự động quét repo/images/).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Chạy Chrome ở chế độ ẩn hoàn toàn (không mở cửa sổ trên màn hình).",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        default=False,
        help="Bắt buộc mở cửa sổ Chrome (ghi đè HEADLESS=true trong .env).",
    )
    parser.add_argument(
        "--headless-mode",
        choices=["new", "offscreen"],
        default=None,
        help="Chế độ ẩn trình duyệt: 'new' (tiết kiệm RAM) hoặc 'offscreen' (Ghost mode an toàn tuyệt đối).",
    )
    return parser.parse_args()


def run_dom_check(config: BotConfig, logger: logging.Logger, stop_event: Optional[Any] = None) -> int:
    """Runs a DOM health canary scan on Facebook selectors."""
    if stop_event and stop_event.is_set():
        logger.warning("🛑 Dừng kiểm tra DOM theo yêu cầu (Stop Requested).")
        return 1
    logger.info("🔍 Bắt đầu kiểm tra sức khỏe DOM Facebook (DOM Canary Check)...")
    selectors_url = os.environ.get("SELECTORS_REMOTE_URL", "").strip()
    if selectors_url:
        sync_selectors(selectors_url, config.json_dir / "selectors.json", custom_logger=logger)

    selectors_file = str(config.json_dir / "selectors.json")

    with get_driver(config) as driver:
        if stop_event and stop_event.is_set():
            logger.warning("🛑 Dừng kiểm tra DOM theo yêu cầu (Stop Requested).")
            return 1
        driver.get("https://www.facebook.com")
        if stop_event and stop_event.is_set():
            logger.warning("🛑 Dừng kiểm tra DOM theo yêu cầu (Stop Requested).")
            return 1
        inspector = DOMInspector(selectors_file=selectors_file)
        report = inspector.inspect_page(driver, check_situational=False)

    print(report.format_table())
    logger.info("--- BÁO CÁO SỨC KHỎE DOM ---")
    logger.info("Tổng component kiểm tra: %d", len(report.components))
    logger.info("Khớp selector chính: %d", len(report.passed))
    logger.info("Phát hiện trôi dạt (Fallback): %d", len(report.warned))
    logger.info("Component hỏng hoàn toàn: %d", len(report.broken))

    if not report.is_healthy:
        broken_names = [c.name for c in report.broken]
        logger.error("🚨 DOM Facebook có thành phần bị hỏng: %s", broken_names)
        return 1

    logger.info("✅ Tất cả các selector đều hoạt động ổn định!")
    return 0


def show_status(config: BotConfig, logger: logging.Logger) -> int:
    """Displays posting history summary and active cooldown counts."""
    history_file = str(config.json_dir / "history.json")
    state_mgr = StateManager(history_file)
    summary = state_mgr.get_summary(cooldown_hours=config.cooldown_hours)

    print("\n" + "=" * 55)
    print(f"{'📊 LỊCH SỬ ĐĂNG BÀI FACEBOOK':^55}")
    print("=" * 55)
    print(f"Tổng số nhóm từng tương tác : {summary.get('total_groups_tracked', 0)}")
    print(f"Tổng số lượt đăng thành công: {summary.get('total_successful_posts', 0)}")
    print(f"Số nhóm đang bị Cooldown 24h: {summary.get('active_cooldown_count', 0)}")
    print(f"Nhóm vừa đăng gần nhất      : {summary.get('last_posted_group', 'N/A')}")
    print(f"Thời điểm đăng gần nhất     : {summary.get('last_posted_time', 'N/A')}")
    print("=" * 55 + "\n")
    return 0


def run_check_membership(config: BotConfig, logger: logging.Logger, target_url: Optional[str] = None) -> int:
    """Runs a live Chrome inspection to determine group membership status."""
    import time
    from services.group_verifier import GroupMembershipVerifier
    from helpers.common_helpers import load_group_targets

    url = target_url
    if not url:
        groups = load_group_targets(str(config.json_dir / "links.json"))
        if groups and groups[0].get("url"):
            url = groups[0]["url"]
        else:
            logger.error("❌ Không tìm thấy URL nhóm nào trong links.json.")
            return 1

    logger.info("🌐 Khởi chạy Google Chrome để kiểm tra nhóm: %s", url)
    verifier = GroupMembershipVerifier(selectors_path=str(config.json_dir / "selectors.json"))

    with get_driver(config) as driver:
        driver.get(url)
        time.sleep(3.0)
        status = verifier.check_status(driver)

    print("\n" + "=" * 60)
    print(f"{'🔍 KẾT QUẢ KIỂM TRA THÀNH VIÊN TRÊN CHROME':^60}")
    print("=" * 60)
    print(f"Nhóm kiểm tra : {url}")
    print(f"Trạng thái    : {status.value}")
    if status.value == "ALREADY_JOINED":
        print("Chi tiết      : ✅ Tài khoản đã là thành viên của nhóm này.")
    elif status.value == "PENDING_APPROVAL":
        print("Chi tiết      : ⏳ Yêu cầu gia nhập đang chờ quản trị viên phê duyệt.")
    elif status.value == "NOT_JOINED":
        print("Chi tiết      : 👉 Tài khoản chưa tham gia nhóm này (có nút 'Tham gia').")
    elif status.value == "ACTION_BLOCKED":
        print("Chi tiết      : 🚨 Tài khoản đang bị Facebook tạm khóa tính năng.")
    else:
        print("Chi tiết      : ❓ Chưa nhận diện được trạng thái trên giao diện hiện tại.")
    print("=" * 60 + "\n")
    return 0


def main() -> int:
    """Main CLI execution flow."""
    args = parse_arguments()

    try:
        config = BotConfig.from_env()
    except Exception as err:
        print(f"❌ Lỗi nạp cấu hình .env: {err}", file=sys.stderr)
        return 1

    # Apply CLI headless overrides
    replace_kwargs = {}
    if getattr(args, "no_headless", False) is True:
        replace_kwargs["headless"] = False
    elif getattr(args, "headless", False) is True:
        replace_kwargs["headless"] = True

    if getattr(args, "headless_mode", None) in ("new", "offscreen"):
        replace_kwargs["headless_mode"] = args.headless_mode

    if replace_kwargs:
        if dataclasses.is_dataclass(config):
            config = dataclasses.replace(config, **replace_kwargs)
        else:
            for k, v in replace_kwargs.items():
                setattr(config, k, v)

    logger = setup_logger("FacebookBot", str(config.logs_dir / "bot.log"))
    logger.info("Cấu hình phiên chạy CLI: Headless=%s (mode=%s)", config.headless, config.headless_mode)

    if args.status:
        return show_status(config, logger)

    if args.check_dom:
        return run_dom_check(config, logger)

    if getattr(args, "check_membership", False) is True:
        return run_check_membership(config, logger, target_url=args.url)

    if getattr(args, "join_groups", False) is True:
        lock = ProcessLock(config.data_dir / "bot.lock")
        if not lock.acquire():
            logger.critical("❌ Một tiến trình bot khác đang chạy. Không thể chạy đồng thời.")
            return 1
        try:
            targets = [args.url] if args.url else None
            bot = FacebookBot(config=config, target_links=targets, custom_logger=logger)
            results = bot.join_groups(
                dry_run=args.dry_run,
                join_limit=args.join_limit,
                default_answer=args.default_answer,
                delay_min=args.join_delay_min,
                delay_max=args.join_delay_max,
            )
            if any(r.get("status") == "NEEDS_LOGIN" for r in results):
                return 3
            if any(r.get("status") == "ACTION_BLOCKED" for r in results):
                return 2
            if results and all(r.get("status") in ("FAILED", "UNKNOWN") for r in results):
                return 1
            return 0
        except Exception as err:
            logger.critical("❌ Lỗi nghiêm trọng khi thực hiện gia nhập nhóm: %s", err, exc_info=True)
            return 1
        finally:
            lock.release()

    if args.login:
        bot = FacebookBot(config=config, custom_logger=logger)
        ok = bot.login_interactive()
        return 0 if ok else 1

    if getattr(args, "logout", False) is True:
        bot = FacebookBot(config=config, custom_logger=logger)
        bot.logout()
        print("✅ Đã đăng xuất Facebook thành công (Session Chrome đã được dọn sạch).")
        return 0

    lock = ProcessLock(config.data_dir / "bot.lock")
    if not lock.acquire():
        logger.critical("❌ Một tiến trình bot khác đang chạy. Không thể chạy đồng thời.")
        return 1

    try:
        targets = [args.url] if args.url else None
        bot = FacebookBot(
            config=config,
            target_links=targets,
            images=args.images,
            custom_logger=logger,
        )
        results = bot.run(dry_run=args.dry_run, custom_batch_size=args.batch_size)

        if any(r.get("status") == "NEEDS_LOGIN" for r in results):
            return 3
        if any(r.get("status") == "ACTION_BLOCKED" for r in results) or bot.circuit_breaker.is_tripped():
            return 2
        if results and all(r.get("status") in ("FAILED", "UNKNOWN") for r in results):
            return 1
        return 0
    except Exception as err:
        logger.critical("❌ Lỗi nghiêm trọng trong quá trình vận hành bot: %s", err, exc_info=True)
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
