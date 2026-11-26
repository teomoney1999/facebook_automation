"""
Facebook Automation Bot - Desktop Graphical User Interface (GUI).

Provides a modern, multi-tabbed desktop control center:
- Tab 1: Environment & System Configuration (.env manager)
- Tab 2: Content & Target Groups Management (JSON editor & 24h cooldown view)
- Tab 3: Real-Time Operational Dashboard & Colored Live Log Console (Thread-Safe)
Integrated with Telegram Remote Control service and Pragmatic error resilience.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time
import webbrowser
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
    TKINTER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    tk = None
    filedialog = None
    messagebox = None
    ttk = None
    ScrolledText = None
    TKINTER_AVAILABLE = False
from typing import Any, Dict, List, Optional, Tuple

# Ensure repo directory is in sys.path
REPO_DIR = Path(__file__).resolve().parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig, ConfigurationError, get_config, get_default_env_path
from helpers.common_helpers import (
    ensure_group_targets_file,
    extract_facebook_group_urls,
    format_duration,
    load_from_file,
    load_group_targets,
    parse_group_header_info,
    save_group_targets,
)
from helpers.logger import setup_logger
from facebookbot import FacebookBot
from browser.driver_manager import get_driver
from services.job_dispatcher import JobDispatcher, JobItem, JobStatus
from services.state_manager import StateManager
from services.telegram_controller import TelegramController
from services.posting_plan import (
    PostItem,
    PostingPlan,
    build_round_robin_plan,
    load_posts_collection,
    save_posts_collection,
)


class QueueLogHandler(logging.Handler):
    """Custom logging handler routing log records into a thread-safe FIFO queue."""

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.log_queue.put((record.levelname, msg))
        except Exception:
            self.handleError(record)


class BotWorkerThread(threading.Thread):
    """
    Dedicated worker thread executing the Selenium bot orchestration.
    Prevents UI freeze and handles graceful interrupt signals.
    """

    def __init__(
        self,
        config: BotConfig,
        dry_run: bool = False,
        custom_batch_size: Optional[int] = None,
        check_dom: bool = False,
        join_mode: bool = False,
        stop_event: Optional[threading.Event] = None,
        event_callback: Optional[Any] = None,
        completion_callback: Optional[Any] = None,
        logger: Optional[logging.Logger] = None,
        images: Optional[List[str]] = None,
        target_links: Optional[List[str]] = None,
        post_content: Optional[str] = None,
        posting_tasks: Optional[Any] = None,
        join_delay_min: Optional[float] = None,
        join_delay_max: Optional[float] = None,
        headless: Optional[bool] = None,
    ):
        super().__init__(name="BotWorkerThread", daemon=True)
        self.config = config
        self.dry_run = dry_run
        self.custom_batch_size = custom_batch_size
        self.check_dom = check_dom
        self.join_mode = join_mode
        self.images = images
        self.target_links = target_links
        self.post_content = post_content
        self.posting_tasks = posting_tasks
        self.join_delay_min = join_delay_min
        self.join_delay_max = join_delay_max
        self.headless = headless
        self.stop_event = stop_event or threading.Event()
        self.event_callback = event_callback
        self.completion_callback = completion_callback
        self.logger = logger or logging.getLogger("WorkerThread")
        self.start_time: float = 0.0
        self.duration_sec: float = 0.0
        self.duration_str: str = "—"

    def run(self):
        self.start_time = time.time()
        self.logger.info("⚡ Worker Thread bắt đầu thực thi tác vụ...")
        results = []
        import dataclasses
        effective_config = (
            dataclasses.replace(self.config, headless=self.headless)
            if (self.headless is not None and dataclasses.is_dataclass(self.config))
            else self.config
        )
        try:
            if self.check_dom:
                from main import run_dom_check
                code = run_dom_check(effective_config, self.logger, stop_event=self.stop_event)
                results = [{"status": "CHECK_DOM", "details": f"Exit code: {code}"}]
            elif self.join_mode:
                bot = FacebookBot(
                    config=effective_config,
                    target_links=self.target_links,
                    custom_logger=self.logger,
                )
                effective_min = self.join_delay_min if self.join_delay_min is not None else getattr(effective_config, "join_delay_min", 15.0)
                effective_max = self.join_delay_max if self.join_delay_max is not None else getattr(effective_config, "join_delay_max", 35.0)
                effective_limit = self.custom_batch_size or getattr(effective_config, "join_limit", 10)
                results = bot.join_groups(
                    target_links=self.target_links,
                    dry_run=self.dry_run,
                    join_limit=effective_limit,
                    delay_min=effective_min,
                    delay_max=effective_max,
                    stop_event=self.stop_event,
                    event_callback=self.event_callback,
                )
            else:
                bot = FacebookBot(
                    config=effective_config,
                    target_links=self.target_links,
                    images=self.images,
                    custom_logger=self.logger,
                )
                if self.posting_tasks:
                    bot.target_links = [t.group_url for t in self.posting_tasks]
                results = bot.run(
                    dry_run=self.dry_run,
                    custom_batch_size=self.custom_batch_size,
                    stop_event=self.stop_event,
                    event_callback=self.event_callback,
                )
        except Exception as err:
            self.logger.critical("🚨 Lỗi ngoại lệ trong Worker Thread: %s", err, exc_info=True)
            results = [{"status": "CRITICAL_ERROR", "details": str(err)}]
        finally:
            end_time = time.time()
            self.duration_sec = max(0.0, end_time - self.start_time) if self.start_time > 0 else 0.0
            self.duration_str = format_duration(self.duration_sec)
            self.logger.info("⚡ Worker Thread đã hoàn thành trong %s.", self.duration_str)
            if self.completion_callback:
                self.completion_callback(results)


class UpdateDialog(tk.Toplevel if tk else object):
    """Modal dialog displaying new version release notes and download progress."""

    def __init__(self, parent, update_info, logger=None):
        if not TKINTER_AVAILABLE:
            return
        super().__init__(parent)
        self.title("🚀 Cập Nhật Ứng Dụng Mới")
        self.geometry("520x400")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.update_info = update_info
        self.logger = logger
        self.is_downloading = False

        self._build_ui()

    def _build_ui(self):
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        title_lbl = ttk.Label(
            container,
            text=f"✨ Đã có phiên bản mới: v{self.update_info.version}",
            font=("Arial", 13, "bold"),
            foreground="#0b6623",
        )
        title_lbl.pack(anchor="w", pady=(0, 4))

        if self.update_info.release_date:
            date_lbl = ttk.Label(
                container,
                text=f"Ngày phát hành: {self.update_info.release_date}",
                font=("Arial", 9, "italic"),
                foreground="gray",
            )
            date_lbl.pack(anchor="w", pady=(0, 10))

        # Changelog frame
        lbl_changelog = ttk.Label(container, text="Các tính năng mới & cải tiến:", font=("Arial", 10, "bold"))
        lbl_changelog.pack(anchor="w", pady=(4, 4))

        txt_frame = ttk.Frame(container)
        txt_frame.pack(fill="both", expand=True, pady=(0, 10))
        changelog_text = ScrolledText(txt_frame, height=7, wrap="word", font=("Arial", 10))
        changelog_text.pack(fill="both", expand=True)

        if self.update_info.changelog:
            for item in self.update_info.changelog:
                changelog_text.insert("end", f"• {item}\n")
        else:
            changelog_text.insert("end", "• Bản cập nhật cải thiện độ ổn định và sửa lỗi.\n")
        changelog_text.config(state="disabled")

        # Progress frame (hidden by default)
        self.prog_frame = ttk.Frame(container)
        self.progress_bar = ttk.Progressbar(self.prog_frame, mode="determinate")
        self.progress_bar.pack(fill="x", expand=True, side="left", padx=(0, 8))
        self.progress_lbl = ttk.Label(self.prog_frame, text="0%", font=("Arial", 9))
        self.progress_lbl.pack(side="right")

        # Buttons
        self.btn_frame = ttk.Frame(container)
        self.btn_frame.pack(fill="x", pady=(10, 0))

        self.btn_update = ttk.Button(
            self.btn_frame,
            text="⚡ Cập Nhật Ngay",
            style="Action.TButton",
            command=self._start_download,
        )
        self.btn_update.pack(side="right", padx=(8, 0))

        self.btn_close = ttk.Button(
            self.btn_frame,
            text="Để sau",
            command=self.destroy,
        )
        self.btn_close.pack(side="right")

    def _start_download(self):
        if self.is_downloading:
            return
        self.is_downloading = True
        self.btn_update.config(state="disabled")
        self.btn_close.config(state="disabled")
        self.prog_frame.pack(fill="x", pady=(4, 10))

        def _worker():
            dest_dir = Path(tempfile.gettempdir()) / "FacebookBotUpdates"
            dest_file = dest_dir / f"FacebookBotSetup-v{self.update_info.version}.exe"

            def _on_progress(downloaded, total):
                if total > 0:
                    pct = int((downloaded / total) * 100)
                    self.after(0, lambda: self._update_progress_ui(pct, downloaded, total))

            from services.updater import download_update

            success = download_update(
                download_url=self.update_info.download_url,
                dest_path=dest_file,
                expected_sha256=self.update_info.sha256 or None,
                progress_callback=_on_progress,
                custom_logger=self.logger,
            )

            if success:
                self.after(0, lambda: self._on_download_complete(dest_file))
            else:
                self.after(0, self._on_download_failed)

        threading.Thread(target=_worker, daemon=True, name="UpdateDownloadWorker").start()

    def _update_progress_ui(self, pct, downloaded, total):
        try:
            self.progress_bar["value"] = pct
            mb_down = downloaded / (1024 * 1024)
            mb_tot = total / (1024 * 1024)
            self.progress_lbl.config(text=f"{pct}% ({mb_down:.1f}/{mb_tot:.1f} MB)")
        except Exception:
            pass

    def _on_download_complete(self, installer_path):
        try:
            self.progress_lbl.config(text="100% - Đang khởi chạy nâng cấp...")
            messagebox.showinfo(
                "Khởi Chạy Cập Nhật",
                "Đã tải xong bộ cài mới. Ứng dụng sẽ tự động đóng và nâng cấp ngầm trong 5 giây.",
                parent=self,
            )
            from services.updater import launch_silent_installer
            launch_silent_installer(installer_path, exit_current=True, custom_logger=self.logger)
        except Exception as err:
            if self.logger:
                self.logger.error("Lỗi khi mở bộ cài đặt: %s", err)
            self._on_download_failed()

    def _on_download_failed(self):
        try:
            self.is_downloading = False
            self.btn_update.config(state="normal")
            self.btn_close.config(state="normal")
            messagebox.showerror(
                "Lỗi Cập Nhật",
                "Tải bộ cài đặt thất bại hoặc sai mã kiểm tra SHA-256. Vui lòng thử lại sau.",
                parent=self,
            )
        except Exception:
            pass


class FacebookBotGUI(tk.Tk if tk else object):
    """Enterprise Desktop GUI for Facebook Automation Bot."""

    def __init__(self, config_path: Optional[str] = None):
        if not TKINTER_AVAILABLE:
            raise RuntimeError("Tkinter is not available in the current Python environment.")
        super().__init__()
        self.title("Facebook Automation Bot - Enterprise Edition")
        self.geometry("980x720")
        self.minsize(880, 620)

        self.repo_dir = REPO_DIR
        self.env_file = Path(config_path).resolve() if config_path else get_default_env_path(base_dir=self.repo_dir)

        # Threading & Control constructs
        self.log_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[BotWorkerThread] = None

        # Setup Logging with QueueHandler
        log_dir = self.env_file.parent / "logs" if self.env_file.parent != self.repo_dir else (self.repo_dir / "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logger("FacebookBotGUI", str(log_dir / "bot.log"))
        self.queue_handler = QueueLogHandler(self.log_queue)
        self.queue_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)-8s] %(message)s", datefmt="%H:%M:%S")
        )
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self.queue_handler)

        # Load Configuration
        self._load_config_state()
        self.links_file = self.config.json_dir / "links.json"
        self.posts_file = self.config.json_dir / "posts.json"
        self.history_file = self.config.json_dir / "history.json"
        self.groups_meta_file = self.config.json_dir / "groups_meta.json"
        self.posts: List[PostItem] = []
        self.active_draft_idx: int = 0
        self.tray_icon = None
        self.var_headless = tk.BooleanVar(value=getattr(self.config, "headless", False))

        # Initialize Multi-Task Job Dispatcher (Task Manager Engine)
        self.dispatcher = JobDispatcher(logger=self.logger)
        self.dispatcher.add_listener(lambda job: self.after(0, self._on_dispatcher_job_update, job))

        # Initialize Telegram Controller
        self.telegram = TelegramController(
            token=self.config.telegram_bot_token,
            admin_id=self.config.telegram_admin_id,
            on_start=self._start_from_telegram,
            on_stop=self._stop_from_telegram,
            get_status=self._get_telegram_status,
            logger=self.logger,
        )
        if self.telegram.is_configured:
            self.telegram.start_polling()

        # Setup UI Visuals
        self._setup_styles()
        self._create_widgets()

        # Bind Window Close Event and macOS Dock click
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        try:
            self.createcommand("::tk::mac::ReopenApplication", self._restore_from_background)
        except Exception:
            pass

        # Start Log Polling Loop, Selector Hot-Patch and Binary Update Check
        self.after(100, self._process_log_queue)
        self.after(3000, self._check_hotpatch_selectors)
        self.after(5000, lambda: self._on_check_update_clicked(silent=True))

    def _check_hotpatch_selectors(self):
        """Asynchronously syncs DOM selectors from cloud if SELECTORS_REMOTE_URL is set."""
        def _worker():
            url = os.environ.get("SELECTORS_REMOTE_URL", "").strip()
            if not url:
                return
            try:
                from services.selector_sync import sync_selectors
                sel_path = self.config.json_dir / "selectors.json"
                if sync_selectors(url, sel_path, custom_logger=self.logger):
                    self.logger.info("⚡ Giao diện đã tự động cập nhật bộ chọn Facebook DOM mới nhất từ cloud.")
            except Exception as err:
                self.logger.debug("Lỗi khi kiểm tra hot-patch selectors: %s", err)

        threading.Thread(target=_worker, daemon=True, name="SelectorSyncWorker").start()

    def _setup_styles(self):
        """Configures ttk styles for clean visual appearance."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TNotebook.Tab", font=("Arial", 11, "bold"), padding=[12, 6])
        style.configure("Header.TLabel", font=("Arial", 13, "bold"))
        style.configure("SubHeader.TLabel", font=("Arial", 10, "italic"))
        style.configure("Action.TButton", font=("Arial", 10, "bold"), padding=6)
        style.configure("Danger.TButton", font=("Arial", 10, "bold"), foreground="red", padding=6)

    def _load_config_state(self):
        """Loads configuration from environment or sets safe defaults."""
        try:
            self.config = BotConfig.from_env(str(self.env_file), validate_credentials=False)
        except Exception as err:
            self.logger.warning("Không thể nạp hoàn chỉnh .env: %s", err)
            self.config = BotConfig(
                email="user@example.com",
                password="password",
                base_dir=self.repo_dir,
            )

    def _create_widgets(self):
        """Constructs tabbed interface and bottom status footer."""
        # Bottom footer bar with version and update check
        self.footer_frame = ttk.Frame(self, padding=(10, 4))
        self.footer_frame.pack(side="bottom", fill="x")

        ver_text = f"Facebook Automation Bot v{getattr(self.config, 'app_version', '2.0.0')}"
        self.version_lbl = ttk.Label(self.footer_frame, text=ver_text, font=("Arial", 9, "bold"))
        self.version_lbl.pack(side="left")

        self.update_status_lbl = ttk.Label(self.footer_frame, text="", font=("Arial", 9, "italic"), foreground="gray")
        self.update_status_lbl.pack(side="left", padx=15)

        self.btn_check_update = ttk.Button(self.footer_frame, text="🔄 Kiểm tra cập nhật", command=self._on_check_update_clicked)
        self.btn_check_update.pack(side="right")

        self.btn_hide_bg_footer = ttk.Button(self.footer_frame, text="🕶️ ẨN XUỐNG NỀN", command=self._hide_to_background)
        self.btn_hide_bg_footer.pack(side="right", padx=5)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        # Tabs
        self.tab_config = ttk.Frame(self.notebook)
        self.tab_content = ttk.Frame(self.notebook)
        self.tab_history = ttk.Frame(self.notebook)
        self.tab_dashboard = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_dashboard, text="📊 Live Dashboard & Logs")
        self.notebook.add(self.tab_history, text="📋 Tổng Kết Theo Lần Đăng")
        self.notebook.add(self.tab_content, text="📝 Nội Dung & Danh Sách Nhóm")
        self.notebook.add(self.tab_config, text="⚙️ Cấu Hình Hệ Thống (.env)")

        self._build_tab_dashboard()
        self._build_tab_history()
        self._build_tab_content()
        self._build_tab_config()

    def _on_check_update_clicked(self, silent: bool = False):
        """Checks for software updates asynchronously."""
        update_url = getattr(self.config, "update_url", None) or os.environ.get("UPDATE_CHECK_URL", "").strip()
        if not update_url:
            if not silent:
                messagebox.showinfo(
                    "Cập Nhật Ứng Dụng",
                    f"Bạn đang sử dụng phiên bản v{getattr(self.config, 'app_version', '2.0.0')}.\n"
                    "Chưa cấu hình đường dẫn UPDATE_CHECK_URL trong .env.",
                )
            return

        try:
            self.btn_check_update.config(state="disabled")
            self.update_status_lbl.config(text="Đang kiểm tra cập nhật...")
        except Exception:
            pass

        def _worker():
            try:
                from services.updater import check_for_updates
                info = check_for_updates(
                    current_version=getattr(self.config, "app_version", "2.0.0"),
                    update_url=update_url,
                    custom_logger=self.logger,
                )
                self.after(0, lambda: self._handle_update_result(info, silent=silent))
            except Exception as err:
                self.logger.error("Lỗi kiểm tra cập nhật: %s", err)
                self.after(0, lambda: self._handle_update_result(None, error=str(err), silent=silent))

        threading.Thread(target=_worker, daemon=True, name="AppUpdateCheckWorker").start()

    def _handle_update_result(self, update_info, error=None, silent=False):
        try:
            self.btn_check_update.config(state="normal")
            self.update_status_lbl.config(text="")
        except Exception:
            pass

        if error:
            if not silent:
                messagebox.showwarning("Kiểm Tra Cập Nhật", f"Không thể kiểm tra cập nhật lúc này: {error}")
            return

        if update_info:
            self.logger.info("Phát hiện bản cập nhật mới v%s!", update_info.version)
            UpdateDialog(self, update_info, logger=self.logger)
        else:
            if not silent:
                messagebox.showinfo(
                    "Phiên Bản Mới Nhất",
                    f"Bạn đang ở phiên bản mới nhất (v{getattr(self.config, 'app_version', '2.0.0')}).",
                )

    # =========================================================================
    # TAB 1: CẤU HÌNH .ENV
    # =========================================================================

    def _build_tab_config(self):
        """Constructs Configuration tab with form validation."""
        frame = ttk.Frame(self.tab_config, padding=15)
        frame.pack(fill="both", expand=True)

        header = ttk.Label(frame, text="Quản Lý Cấu Hình & Khóa Bảo Mật (.env)", style="Header.TLabel")
        header.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 15))

        self.cfg_entries: Dict[str, ttk.Entry] = {}

        fields = [
            ("EMAIL", "Facebook Email / SĐT:", self.config.email, False),
            ("PASSWORD", "Facebook Mật khẩu:", self.config.password, True),
            ("MIN_DELAY_BETWEEN_POSTS", "Thời gian nghỉ tối thiểu khi đăng (s):", str(self.config.min_delay_between_posts), False),
            ("MAX_DELAY_BETWEEN_POSTS", "Thời gian nghỉ tối đa khi đăng (s):", str(self.config.max_delay_between_posts), False),
            ("MAX_POSTS_PER_RUN", "Số bài tối đa mỗi phiên:", str(self.config.max_posts_per_run), False),
            ("JOIN_DELAY_MIN", "Giãn cách tối thiểu khi tham gia nhóm (s):", str(getattr(self.config, "join_delay_min", 15.0)), False),
            ("JOIN_DELAY_MAX", "Giãn cách tối đa khi tham gia nhóm (s):", str(getattr(self.config, "join_delay_max", 35.0)), False),
            ("JOIN_LIMIT", "Số nhóm tham gia tối đa mỗi ca:", str(getattr(self.config, "join_limit", 10)), False),
            ("COOLDOWN_HOURS", "Giờ giãn cách nhóm / Chặn (Cooldown):", str(self.config.cooldown_hours), False),
            ("DEBUG_PORT", "Cổng gỡ lỗi Chrome (Debug Port):", str(self.config.debug_port), False),
            ("TELEGRAM_BOT_TOKEN", "Telegram Bot Token (tùy chọn):", self.config.telegram_bot_token or "", True),
            ("TELEGRAM_ADMIN_ID", "Telegram Admin Chat ID (tùy chọn):", self.config.telegram_admin_id or "", False),
        ]

        for idx, (key, label_text, default_val, is_secret) in enumerate(fields, 1):
            lbl = ttk.Label(frame, text=label_text, font=("Arial", 10))
            lbl.grid(row=idx, column=0, sticky="w", pady=6, padx=(0, 10))

            entry = ttk.Entry(frame, width=45, show="*" if is_secret else "")
            entry.insert(0, default_val)
            entry.grid(row=idx, column=1, sticky="w", pady=6)
            self.cfg_entries[key] = entry

            if is_secret:
                show_btn = ttk.Checkbutton(
                    frame,
                    text="Hiện",
                    command=lambda e=entry: e.configure(show="" if e.cget("show") == "*" else "*"),
                )
                show_btn.grid(row=idx, column=2, sticky="w", padx=5)

        # Buttons Panel
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=len(fields) + 1, column=0, columnspan=3, pady=20, sticky="w")

        save_btn = ttk.Button(btn_frame, text="💾 Lưu Cấu Hình (.env)", style="Action.TButton", command=self._save_env_config)
        save_btn.pack(side="left", padx=(0, 10))

        reload_btn = ttk.Button(btn_frame, text="🔄 Tải Lại Từ File", command=self._reload_env_config)
        reload_btn.pack(side="left", padx=(0, 10))

        test_tg_btn = ttk.Button(btn_frame, text="🔔 Thử Kết Nối Telegram", command=self._test_telegram_connection)
        test_tg_btn.pack(side="left")

    def _save_env_config(self):
        """Validates and persists fields into .env file."""
        email = self.cfg_entries["EMAIL"].get().strip()
        pwd = self.cfg_entries["PASSWORD"].get().strip()

        if not email or not pwd:
            messagebox.showerror("Lỗi Cấu Hình", "EMAIL và PASSWORD không được để trống!")
            return

        lines = [
            "# Facebook Automation Bot Configuration",
            f"EMAIL={email}",
            f"PASSWORD={pwd}",
            f"MIN_DELAY_BETWEEN_POSTS={self.cfg_entries['MIN_DELAY_BETWEEN_POSTS'].get().strip()}",
            f"MAX_DELAY_BETWEEN_POSTS={self.cfg_entries['MAX_DELAY_BETWEEN_POSTS'].get().strip()}",
            f"MAX_POSTS_PER_RUN={self.cfg_entries['MAX_POSTS_PER_RUN'].get().strip()}",
            f"JOIN_DELAY_MIN={self.cfg_entries['JOIN_DELAY_MIN'].get().strip()}",
            f"JOIN_DELAY_MAX={self.cfg_entries['JOIN_DELAY_MAX'].get().strip()}",
            f"JOIN_LIMIT={self.cfg_entries['JOIN_LIMIT'].get().strip()}",
            f"COOLDOWN_HOURS={self.cfg_entries['COOLDOWN_HOURS'].get().strip()}",
            f"DEBUG_PORT={self.cfg_entries['DEBUG_PORT'].get().strip()}",
            f"TELEGRAM_BOT_TOKEN={self.cfg_entries['TELEGRAM_BOT_TOKEN'].get().strip()}",
            f"TELEGRAM_ADMIN_ID={self.cfg_entries['TELEGRAM_ADMIN_ID'].get().strip()}",
        ]

        try:
            self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self._load_config_state()
            messagebox.showinfo("Thành Công", "Đã lưu cấu hình .env thành công!")
            self.logger.info("✅ Cấu hình .env đã được cập nhật thành công từ Desktop GUI.")
        except Exception as err:
            messagebox.showerror("Lỗi Ghi File", f"Không thể lưu file .env: {err}")

    def _reload_env_config(self):
        """Reloads inputs from .env file."""
        self._load_config_state()
        self.cfg_entries["EMAIL"].delete(0, tk.END)
        self.cfg_entries["EMAIL"].insert(0, self.config.email)
        self.cfg_entries["PASSWORD"].delete(0, tk.END)
        self.cfg_entries["PASSWORD"].insert(0, self.config.password)
        self.cfg_entries["MIN_DELAY_BETWEEN_POSTS"].delete(0, tk.END)
        self.cfg_entries["MIN_DELAY_BETWEEN_POSTS"].insert(0, str(self.config.min_delay_between_posts))
        self.cfg_entries["MAX_DELAY_BETWEEN_POSTS"].delete(0, tk.END)
        self.cfg_entries["MAX_DELAY_BETWEEN_POSTS"].insert(0, str(self.config.max_delay_between_posts))
        self.cfg_entries["MAX_POSTS_PER_RUN"].delete(0, tk.END)
        self.cfg_entries["MAX_POSTS_PER_RUN"].insert(0, str(self.config.max_posts_per_run))
        if "JOIN_DELAY_MIN" in self.cfg_entries:
            self.cfg_entries["JOIN_DELAY_MIN"].delete(0, tk.END)
            self.cfg_entries["JOIN_DELAY_MIN"].insert(0, str(getattr(self.config, "join_delay_min", 15.0)))
        if "JOIN_DELAY_MAX" in self.cfg_entries:
            self.cfg_entries["JOIN_DELAY_MAX"].delete(0, tk.END)
            self.cfg_entries["JOIN_DELAY_MAX"].insert(0, str(getattr(self.config, "join_delay_max", 35.0)))
        if "JOIN_LIMIT" in self.cfg_entries:
            self.cfg_entries["JOIN_LIMIT"].delete(0, tk.END)
            self.cfg_entries["JOIN_LIMIT"].insert(0, str(getattr(self.config, "join_limit", 10)))
        self.cfg_entries["COOLDOWN_HOURS"].delete(0, tk.END)
        self.cfg_entries["COOLDOWN_HOURS"].insert(0, str(self.config.cooldown_hours))
        self.cfg_entries["DEBUG_PORT"].delete(0, tk.END)
        self.cfg_entries["DEBUG_PORT"].insert(0, str(self.config.debug_port))
        self.cfg_entries["TELEGRAM_BOT_TOKEN"].delete(0, tk.END)
        self.cfg_entries["TELEGRAM_BOT_TOKEN"].insert(0, self.config.telegram_bot_token or "")
        self.cfg_entries["TELEGRAM_ADMIN_ID"].delete(0, tk.END)
        self.cfg_entries["TELEGRAM_ADMIN_ID"].insert(0, self.config.telegram_admin_id or "")
        messagebox.showinfo("Nạp Lại", "Đã nạp lại cấu hình từ file .env.")

    def _test_telegram_connection(self):
        """Tests sending a sample ping to Telegram."""
        token = self.cfg_entries["TELEGRAM_BOT_TOKEN"].get().strip()
        admin_id = self.cfg_entries["TELEGRAM_ADMIN_ID"].get().strip()

        if not token or not admin_id:
            messagebox.showwarning("Thiếu Thông Tin", "Vui lòng nhập cả Telegram Bot Token và Admin ID.")
            return

        tester = TelegramController(token=token, admin_id=admin_id, logger=self.logger)
        ok = tester.send_message("🔔 <b>[TEST]</b> Kết nối thành công từ Facebook Automation Bot GUI!")
        if ok:
            messagebox.showinfo("Thành Công", "Đã gửi thông báo kiểm tra đến Telegram thành công!")
        else:
            messagebox.showerror("Thất Bại", "Không thể gửi tin nhắn Telegram. Vui lòng kiểm tra lại Token và Chat ID.")

    # =========================================================================
    # TAB: LỊCH SỬ & TỔNG KẾT THEO LẦN ĐĂNG
    # =========================================================================

    def _build_tab_history(self):
        """Constructs Recent Posting History tab with Per-Session Summary."""
        frame = ttk.Frame(self.tab_history, padding=12)
        frame.pack(fill="both", expand=True)

        # Header Frame
        hdr_frame = ttk.Frame(frame)
        hdr_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(hdr_frame, text="📋 Tổng Kết Theo Lần Đăng", style="Header.TLabel").pack(side="left")
        self.lbl_history_stats = ttk.Label(hdr_frame, text="Tổng: 0 | Thành công: 0 | Chờ duyệt: 0 | Thất bại: 0", font=("Arial", 10, "bold"))
        self.lbl_history_stats.pack(side="right")

        # Action Toolbar
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, 8))

        ttk.Label(toolbar, text="Chọn Lần Đăng:", font=("Arial", 9, "bold")).pack(side="left", padx=(0, 5))
        self.cbo_history_sessions = ttk.Combobox(toolbar, state="readonly", width=42)
        self.cbo_history_sessions.pack(side="left", padx=(0, 10))
        self.cbo_history_sessions.bind("<<ComboboxSelected>>", self._on_history_session_changed)

        ttk.Button(toolbar, text="🔄 Làm Mới", command=self._refresh_history_tab).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="🌐 Mở Link Nhóm", command=self._open_history_group_url).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="🧹 Xóa Lịch Sử", command=self._clear_history_data).pack(side="right")

        # Prominent Summary Card for Selected Session
        self.frame_session_card = tk.Frame(frame, bg="#e8f5e9", relief="solid", bd=1, padx=12, pady=8)
        self.frame_session_card.pack(fill="x", pady=(0, 8))

        card_top = tk.Frame(self.frame_session_card, bg="#e8f5e9")
        card_top.pack(fill="x")
        self.lbl_session_title = tk.Label(card_top, text="🏁 Lần Đăng: Chưa có dữ liệu", font=("Arial", 11, "bold"), bg="#e8f5e9", fg="#1b5e20")
        self.lbl_session_title.pack(side="left")
        self.lbl_session_duration = tk.Label(card_top, text="⏱ Thời gian hoàn thành: —", font=("Arial", 10, "bold"), bg="#e8f5e9", fg="#0d47a1")
        self.lbl_session_duration.pack(side="right")

        card_bot = tk.Frame(self.frame_session_card, bg="#e8f5e9")
        card_bot.pack(fill="x", pady=(4, 0))
        self.lbl_session_stats = tk.Label(card_bot, text="Tổng: 0 nhóm | ✅ Thành công: 0 | ⏳ Chờ duyệt: 0 | ❌ Thất bại: 0", font=("Arial", 9), bg="#e8f5e9", fg="#333333")
        self.lbl_session_stats.pack(side="left")
        self.lbl_session_time_range = tk.Label(card_bot, text="Thời điểm: —", font=("Arial", 8, "italic"), bg="#e8f5e9", fg="#666666")
        self.lbl_session_time_range.pack(side="right")

        # History Treeview
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)

        tree_scroll_y = ttk.Scrollbar(tree_frame, orient="vertical")
        tree_scroll_y.pack(side="right", fill="y")
        tree_scroll_x = ttk.Scrollbar(tree_frame, orient="horizontal")
        tree_scroll_x.pack(side="bottom", fill="x")

        self.tree_history = ttk.Treeview(
            tree_frame,
            columns=("idx", "time", "group", "status", "count", "details"),
            show="headings",
            selectmode="browse",
            yscrollcommand=tree_scroll_y.set,
            xscrollcommand=tree_scroll_x.set,
        )
        tree_scroll_y.config(command=self.tree_history.yview)
        tree_scroll_x.config(command=self.tree_history.xview)

        self.tree_history.heading("idx", text="#")
        self.tree_history.heading("time", text="Thời Gian")
        self.tree_history.heading("group", text="Nhóm Facebook Mục Tiêu")
        self.tree_history.heading("status", text="Trạng Thái")
        self.tree_history.heading("count", text="Lần Đăng")
        self.tree_history.heading("details", text="Chi Tiết / Phản Hồi Từ Facebook")

        self.tree_history.column("idx", width=40, anchor="center")
        self.tree_history.column("time", width=160, anchor="center")
        self.tree_history.column("group", width=280)
        self.tree_history.column("status", width=140, anchor="center")
        self.tree_history.column("count", width=80, anchor="center")
        self.tree_history.column("details", width=360)
        self.tree_history.pack(fill="both", expand=True)

        self.tree_history.bind("<Double-1>", lambda e: self._open_history_group_url())
        self.tree_history.bind("<Return>", lambda e: self._open_history_group_url())

        # Tag styles for colors
        self.tree_history.tag_configure("SUCCESS", foreground="#2e7d32")
        self.tree_history.tag_configure("PENDING_APPROVAL", foreground="#ef6c00")
        self.tree_history.tag_configure("ACTION_BLOCKED", foreground="#c62828")
        self.tree_history.tag_configure("FAILED", foreground="#d32f2f")
        self.tree_history.tag_configure("MARKETPLACE_FORM", foreground="#6a1b9a")

        self._refresh_history_tab()

    def _refresh_history_tab(self):
        """Populates history dropdown and treeview with session summary data."""
        if not hasattr(self, "tree_history"):
            return
        state_mgr = StateManager(str(self.history_file))
        sessions = state_mgr.get_sessions()  # newest first
        self._history_sessions = sessions
        self._session_combo_map = {}

        combo_values = ["🌐 [Tất cả các lần đăng]"]
        if sessions:
            for s in sessions:
                s_id = s.get("id", 1)
                dur = s.get("duration_formatted") or format_duration(s.get("duration_seconds", 0))
                tot = s.get("total_groups", len(s.get("results", [])))
                t_raw = s.get("start_time", "")
                t_fmt = t_raw[:19].replace("T", " ") if t_raw else ""
                label = f"Lần #{s_id} - {t_fmt} (⏱ {dur} - {tot} nhóm)"
                self._session_combo_map[label] = s
                combo_values.append(label)

        current_val = self.cbo_history_sessions.get()
        self.cbo_history_sessions["values"] = combo_values

        if current_val in combo_values:
            self.cbo_history_sessions.set(current_val)
        else:
            self.cbo_history_sessions.set("🌐 [Tất cả các lần đăng]")

        self._render_current_history_session()

    def _on_history_session_changed(self, event=None):
        """Triggered when user selects a different session in the combobox."""
        self._render_current_history_session()

    def _render_current_history_session(self):
        """Renders either the selected session or all sessions in the Treeview and Summary Card."""
        if not hasattr(self, "tree_history"):
            return
        self.tree_history.delete(*self.tree_history.get_children())
        selected_label = self.cbo_history_sessions.get()

        sessions = getattr(self, "_history_sessions", [])

        if selected_label and selected_label != "🌐 [Tất cả các lần đăng]" and selected_label in getattr(self, "_session_combo_map", {}):
            # Render SINGLE session
            s = self._session_combo_map[selected_label]
            s_id = s.get("id", 1)
            s_type = "Đăng bài" if s.get("type") in ("POST", "DRY_RUN") else "Gia nhập nhóm"
            dur_str = s.get("duration_formatted") or format_duration(s.get("duration_seconds", 0))
            succ = s.get("success_count", 0)
            pend = s.get("pending_count", 0)
            fail = s.get("failed_count", 0)
            total_g = s.get("total_groups", len(s.get("results", [])))

            t_start = s.get("start_time", "")[:19].replace("T", " ")
            t_end = s.get("end_time", "")[:19].replace("T", " ")

            self.lbl_session_title.config(text=f"🏁 Lần Đăng #{s_id} ({s_type})", fg="#1b5e20")
            self.lbl_session_duration.config(text=f"⏱ Thời gian hoàn thành: {dur_str}", fg="#0d47a1")
            self.lbl_session_stats.config(
                text=f"Tổng: {total_g} nhóm | ✅ Thành công: {succ} | ⏳ Chờ duyệt: {pend} | ❌ Thất bại: {fail}"
            )
            self.lbl_session_time_range.config(text=f"Bắt đầu: {t_start} → Kết thúc: {t_end}")
            self.lbl_history_stats.config(
                text=f"Lần #{s_id} (⏱ {dur_str}) | Thành công: {succ} | Chờ duyệt: {pend} | Thất bại: {fail}"
            )

            # Insert groups of this session
            for idx, r in enumerate(s.get("results", []), 1):
                url = r.get("link", "")
                st = r.get("status", "UNKNOWN")
                err = r.get("details", "") or r.get("error", "—")
                raw_time = r.get("time") or s.get("start_time", "")
                t_fmt = raw_time[:19].replace("T", " ") if raw_time else "—"

                st_disp, tag = self._format_status_badge(st)
                self.tree_history.insert(
                    "", "end",
                    values=(idx, t_fmt, url, st_disp, f"Lần #{s_id}", err),
                    tags=(tag,)
                )
        else:
            # Render ALL sessions (or fallback to legacy history)
            if sessions:
                tot_dur = sum(s.get("duration_seconds", 0) for s in sessions)
                tot_dur_str = format_duration(tot_dur)
                tot_succ = sum(s.get("success_count", 0) for s in sessions)
                tot_pend = sum(s.get("pending_count", 0) for s in sessions)
                tot_fail = sum(s.get("failed_count", 0) for s in sessions)
                tot_g = sum(s.get("total_groups", 0) for s in sessions)

                self.lbl_session_title.config(text=f"🏁 Tất Cả Các Lần Đăng ({len(sessions)} lần)", fg="#1b5e20")
                self.lbl_session_duration.config(text=f"⏱ Tổng thời gian: {tot_dur_str}", fg="#0d47a1")
                self.lbl_session_stats.config(
                    text=f"Tổng: {tot_g} nhóm | ✅ Thành công: {tot_succ} | ⏳ Chờ duyệt: {tot_pend} | ❌ Thất bại: {tot_fail}"
                )
                self.lbl_session_time_range.config(text=f"Số lần đăng đã lưu: {len(sessions)}")
                self.lbl_history_stats.config(
                    text=f"Tổng: {tot_g} nhóm ({len(sessions)} lần đăng) | ✅ {tot_succ} | ⏳ {tot_pend} | ❌ {tot_fail}"
                )

                row_idx = 1
                for s in sessions:
                    s_id = s.get("id", 1)
                    for r in s.get("results", []):
                        url = r.get("link", "")
                        st = r.get("status", "UNKNOWN")
                        err = r.get("details", "") or r.get("error", "—")
                        raw_time = r.get("time") or s.get("start_time", "")
                        t_fmt = raw_time[:19].replace("T", " ") if raw_time else "—"

                        st_disp, tag = self._format_status_badge(st)
                        self.tree_history.insert(
                            "", "end",
                            values=(row_idx, t_fmt, url, st_disp, f"Lần #{s_id}", err),
                            tags=(tag,)
                        )
                        row_idx += 1
            else:
                # Legacy fallback from history dict
                data = load_from_file(str(self.history_file))
                history_dict = data.get("history", {}) if isinstance(data, dict) else {}
                items = list(history_dict.items())
                items.sort(key=lambda item: item[1].get("last_posted_at", "") if isinstance(item[1], dict) else "", reverse=True)

                succ = 0
                pend = 0
                fail = 0

                for idx, (url, rec) in enumerate(items, 1):
                    if not isinstance(rec, dict):
                        continue
                    status = rec.get("status", "UNKNOWN")
                    raw_time = rec.get("last_posted_at", "")
                    formatted_time = raw_time
                    if raw_time:
                        try:
                            dt = datetime.fromisoformat(raw_time)
                            local_dt = dt.astimezone()
                            formatted_time = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            formatted_time = raw_time[:19].replace("T", " ")

                    p_cnt = rec.get("post_count", 1)
                    err = rec.get("error") or "—"

                    st_disp, tag = self._format_status_badge(status)
                    if tag == "SUCCESS": succ += 1
                    elif tag == "PENDING_APPROVAL": pend += 1
                    else: fail += 1

                    self.tree_history.insert(
                        "", "end",
                        values=(idx, formatted_time, url, st_disp, p_cnt, err),
                        tags=(tag,)
                    )

                self.lbl_session_title.config(text="🏁 Lịch Sử Đăng Bài", fg="#1b5e20")
                self.lbl_session_duration.config(text="⏱ Thời gian hoàn thành: —", fg="#0d47a1")
                self.lbl_session_stats.config(text=f"Tổng: {len(items)} | ✅ {succ} | ⏳ {pend} | ❌ {fail}")
                self.lbl_history_stats.config(text=f"Tổng: {len(items)} | Thành công: {succ} | Chờ duyệt: {pend} | Thất bại: {fail}")

    def _format_status_badge(self, status: str) -> Tuple[str, str]:
        """Maps status string to human label and treeview tag."""
        st = status.upper()
        if st in ("SUCCESS", "JOINED", "ALREADY_JOINED", "JOINED_SUCCESS"):
            return "✅ Thành Công", "SUCCESS"
        if st in ("PENDING_APPROVAL", "REQUESTED", "JOIN_REQUEST_SENT"):
            return "⏳ Chờ Duyệt", "PENDING_APPROVAL"
        if st == "ACTION_BLOCKED":
            return "🚫 Bị Chặn Tính Năng", "ACTION_BLOCKED"
        if st == "MARKETPLACE_FORM":
            return "🏷️ Form Bán Hàng", "MARKETPLACE_FORM"
        return "❌ Thất Bại", "FAILED"

    def _open_history_group_url(self):
        """Opens URL of selected history entry in browser."""
        sel = self.tree_history.selection()
        if not sel:
            messagebox.showinfo("Thông Báo", "Vui lòng chọn một dòng lịch sử để mở link.")
            return
        vals = self.tree_history.item(sel[0], "values")
        for v in vals:
            if isinstance(v, str) and v.startswith("http"):
                self._open_url_in_browser(v)
                return

    def _clear_history_data(self):
        """Prompts confirmation and clears history.json and all sessions."""
        if messagebox.askyesno("Xác Nhận Xóa", "Bạn có chắc muốn xóa toàn bộ lịch sử đăng bài không?\nLưu ý: Thời gian giãn cách 24h và các lần đăng cũng sẽ được đặt lại."):
            state_mgr = StateManager(str(self.history_file))
            state_mgr.history = {}
            state_mgr.sessions = []
            state_mgr.save_history()
            self._refresh_history_tab()
            self._load_groups_and_posts()
            messagebox.showinfo("Thành Công", "Đã xóa toàn bộ lịch sử đăng bài.")

    # =========================================================================
    # TAB 3: NỘI DUNG & NHÓM MỤC TIÊU
    # =========================================================================

    def _build_tab_content(self):
        """Constructs Content & Groups tab."""
        paned = ttk.PanedWindow(self.tab_content, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        # Left: Groups List
        left_frame = ttk.Frame(paned, padding=5)
        paned.add(left_frame, weight=1)

        # Header with quick scan & join buttons
        grp_hdr_frame = ttk.Frame(left_frame)
        grp_hdr_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(grp_hdr_frame, text="Danh Sách Nhóm Mục Tiêu (links.json)", style="Header.TLabel").pack(side="left")
        self.btn_join_top = ttk.Button(
            grp_hdr_frame,
            text="🤝 Tham Gia Nhóm",
            style="Action.TButton",
            command=self._start_join_groups_flow,
        )
        self.btn_join_top.pack(side="right", padx=(4, 0))
        self.btn_scan_meta_top = ttk.Button(
            grp_hdr_frame,
            text="🔍 Cập Nhật Info Nhóm",
            style="Action.TButton",
            command=self._scan_groups_metadata,
        )
        self.btn_scan_meta_top.pack(side="right")

        # Groups Treeview
        tree_scroll = ttk.Scrollbar(left_frame)
        tree_scroll.pack(side="right", fill="y")

        self.tree_groups = ttk.Treeview(
            left_frame,
            columns=("idx", "name", "members", "privacy", "membership", "url", "cooldown"),
            show="headings",
            selectmode="extended",
            yscrollcommand=tree_scroll.set,
            height=15,
        )
        tree_scroll.config(command=self.tree_groups.yview)

        self.tree_groups.heading("idx", text="#")
        self.tree_groups.heading("name", text="Tên Nhóm")
        self.tree_groups.heading("members", text="Thành Viên")
        self.tree_groups.heading("privacy", text="Loại Nhóm")
        self.tree_groups.heading("membership", text="Tham Gia")
        self.tree_groups.heading("url", text="Đường Dẫn URL Nhóm")
        self.tree_groups.heading("cooldown", text="Cooldown 24h")

        self.tree_groups.column("idx", width=35, anchor="center")
        self.tree_groups.column("name", width=140)
        self.tree_groups.column("members", width=70, anchor="center")
        self.tree_groups.column("privacy", width=75, anchor="center")
        self.tree_groups.column("membership", width=95, anchor="center")
        self.tree_groups.column("url", width=200)
        self.tree_groups.column("cooldown", width=100, anchor="center")
        self.tree_groups.pack(fill="both", expand=True, pady=5)

        # Keyboard & mouse shortcuts
        self.tree_groups.bind("<Delete>", lambda e: self._remove_selected_group())
        self.tree_groups.bind("<BackSpace>", lambda e: self._remove_selected_group())
        self.tree_groups.bind("<Control-v>", lambda e: self._paste_groups_from_clipboard())
        self.tree_groups.bind("<Command-v>", lambda e: self._paste_groups_from_clipboard())
        self.tree_groups.bind("<Double-1>", self._on_group_row_double_click)
        self.tree_groups.bind("<Button-1>", self._on_group_tree_press)
        self.tree_groups.bind("<B1-Motion>", self._on_group_tree_drag)
        self.tree_groups.bind("<ButtonRelease-1>", self._on_group_tree_click)
        self.tree_groups.bind("<Return>", self._on_group_row_double_click)
        left_frame.bind("<Button-1>", self._deselect_groups)
        grp_hdr_frame.bind("<Button-1>", self._deselect_groups)

        # Context Menu on right click
        self.group_context_menu = tk.Menu(self, tearoff=0)
        self.group_context_menu.add_command(label="🚀 Đăng Bài Cho Nhóm Này", command=self._start_post_to_selected_groups)
        self.group_context_menu.add_command(label="🤝 Tham Gia Nhóm Này", command=self._start_join_groups_flow)
        self.group_context_menu.add_command(label="🌐 Mở Trong Trình Duyệt", command=self._open_selected_group_url)
        self.group_context_menu.add_command(label="🔍 Quét Thông Tin Nhóm", command=self._scan_groups_metadata)
        self.group_context_menu.add_separator()
        self.group_context_menu.add_command(label="❌ Xóa Khỏi Danh Sách", command=self._remove_selected_group)

        def _show_context_menu(event):
            item = self.tree_groups.identify_row(event.y)
            if item:
                if item not in self.tree_groups.selection():
                    self.tree_groups.selection_set(item)
                self.group_context_menu.post(event.x_root, event.y_root)

        self.tree_groups.bind("<Button-2>", _show_context_menu)
        self.tree_groups.bind("<Button-3>", _show_context_menu)

        # Buttons under groups (Organized in 2 clear rows to prevent clipping)
        grp_btn_row1 = ttk.Frame(left_frame)
        grp_btn_row1.pack(fill="x", pady=(4, 2))

        ttk.Button(grp_btn_row1, text="➕ Thêm Nhóm...", command=self._add_group_prompt).pack(side="left", padx=2, fill="x", expand=True)
        ttk.Button(grp_btn_row1, text="📋 Dán Clipboard", command=self._paste_groups_from_clipboard).pack(side="left", padx=2, fill="x", expand=True)
        ttk.Button(grp_btn_row1, text="❌ Xóa Chọn", command=self._remove_selected_group).pack(side="left", padx=2, fill="x", expand=True)
        ttk.Button(grp_btn_row1, text="🔄 Tải Lại", command=self._reload_groups_file).pack(side="left", padx=2, fill="x", expand=True)
        ttk.Button(grp_btn_row1, text="💾 Lưu File", style="Action.TButton", command=self._save_groups_file).pack(side="left", padx=2, fill="x", expand=True)

        # Quick Join Timing & Batch Config Row (Cooldown & Delays)
        grp_join_cfg_row = ttk.Frame(left_frame)
        grp_join_cfg_row.pack(fill="x", pady=(2, 2))

        ttk.Label(grp_join_cfg_row, text="⏱ Giãn cách join:", font=("Arial", 9, "bold")).pack(side="left", padx=(2, 2))
        self.entry_join_min = ttk.Spinbox(grp_join_cfg_row, from_=1.0, to=300.0, width=5)
        self.entry_join_min.set(str(getattr(self.config, "join_delay_min", 15.0)))
        self.entry_join_min.pack(side="left", padx=1)
        ttk.Label(grp_join_cfg_row, text="s -", font=("Arial", 9)).pack(side="left", padx=1)
        self.entry_join_max = ttk.Spinbox(grp_join_cfg_row, from_=1.0, to=600.0, width=5)
        self.entry_join_max.set(str(getattr(self.config, "join_delay_max", 35.0)))
        self.entry_join_max.pack(side="left", padx=1)
        ttk.Label(grp_join_cfg_row, text="s | Tối đa:", font=("Arial", 9)).pack(side="left", padx=(4, 2))
        self.entry_join_limit = ttk.Spinbox(grp_join_cfg_row, from_=1, to=100, width=4)
        self.entry_join_limit.set(str(getattr(self.config, "join_limit", 10)))
        self.entry_join_limit.pack(side="left", padx=1)
        ttk.Label(grp_join_cfg_row, text="nhóm/ca", font=("Arial", 9)).pack(side="left", padx=2)

        grp_btn_row2 = ttk.Frame(left_frame)
        grp_btn_row2.pack(fill="x", pady=(2, 4))

        self.btn_post_selected = ttk.Button(
            grp_btn_row2,
            text="🚀 Đăng Bài Đã Chọn",
            style="Action.TButton",
            command=self._start_post_to_selected_groups,
        )
        self.btn_post_selected.pack(side="left", padx=2, fill="x", expand=True)

        self.btn_scan_meta = ttk.Button(
            grp_btn_row2,
            text="🔍 Tìm & Cập Nhật Info",
            style="Action.TButton",
            command=self._scan_groups_metadata,
        )
        self.btn_scan_meta.pack(side="left", padx=2, fill="x", expand=True)

        self.btn_join_groups = ttk.Button(
            grp_btn_row2,
            text="🤝 Tham Gia Nhóm",
            style="Action.TButton",
            command=self._start_join_groups_flow,
        )
        self.btn_join_groups.pack(side="left", padx=2, fill="x", expand=True)

        ttk.Button(grp_btn_row2, text="🌐 Mở Link Trình Duyệt", command=self._open_selected_group_url).pack(side="left", padx=2)

        # Right: Content & Drafts Editor
        right_frame = ttk.Frame(paned, padding=5)
        paned.add(right_frame, weight=1)

        # Header Frame: Title + Stats
        draft_hdr = ttk.Frame(right_frame)
        draft_hdr.pack(fill="x", pady=(0, 4))
        ttk.Label(draft_hdr, text="📝 Quản Lý Bản Nháp Bài Đăng (posts.json)", style="Header.TLabel").pack(side="left")
        self.lbl_draft_stats = ttk.Label(draft_hdr, text="1 bản nháp", font=("Arial", 9, "italic"))
        self.lbl_draft_stats.pack(side="right")

        # Draft Toolbar: Selector + Action buttons
        draft_toolbar = ttk.Frame(right_frame)
        draft_toolbar.pack(fill="x", pady=2)
        ttk.Label(draft_toolbar, text="Bản nháp:", font=("Arial", 9, "bold")).pack(side="left", padx=(0, 4))
        self.cbo_drafts = ttk.Combobox(draft_toolbar, state="readonly", width=22)
        self.cbo_drafts.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.cbo_drafts.bind("<<ComboboxSelected>>", self._on_draft_selected_from_combobox)

        ttk.Button(draft_toolbar, text="➕ Thêm", command=self._add_draft).pack(side="left", padx=2)
        ttk.Button(draft_toolbar, text="📋 Nhân Bản", command=self._duplicate_draft).pack(side="left", padx=2)
        ttk.Button(draft_toolbar, text="❌ Xóa", command=self._delete_draft).pack(side="left", padx=2)

        # Draft Properties: Name + Active Checkbox + Char counter
        draft_prop = ttk.Frame(right_frame)
        draft_prop.pack(fill="x", pady=3)
        ttk.Label(draft_prop, text="Tiêu đề:", font=("Arial", 9)).pack(side="left", padx=(0, 4))
        self.entry_draft_name = ttk.Entry(draft_prop, width=20)
        self.entry_draft_name.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.entry_draft_name.bind("<KeyRelease>", self._on_draft_name_changed)

        self.chk_draft_selected_var = tk.BooleanVar(value=True) if tk else None
        self.chk_draft_selected = ttk.Checkbutton(
            draft_prop,
            text="☑ Chọn để đăng",
            variable=self.chk_draft_selected_var,
            command=self._on_draft_selected_toggle,
        )
        self.chk_draft_selected.pack(side="left", padx=(0, 6))

        self.lbl_char_count = ttk.Label(draft_prop, text="Ký tự: 0", font=("Arial", 9, "italic"))
        self.lbl_char_count.pack(side="right")

        # Content Text Area
        self.txt_content = ScrolledText(right_frame, wrap="word", font=("Arial", 10), height=8)
        self.txt_content.pack(fill="both", expand=True, pady=3)
        self.txt_content.bind("<KeyRelease>", self._update_char_counter)

        # Attached Images Section
        img_section = ttk.LabelFrame(right_frame, text="📸 Ảnh Đính Kèm Của Bản Nháp (Images)", padding=5)
        img_section.pack(fill="x", pady=4)

        img_list_frame = ttk.Frame(img_section)
        img_list_frame.pack(fill="x", pady=2)

        self.lst_images = tk.Listbox(img_list_frame, height=3, font=("Arial", 9))
        img_scroll = ttk.Scrollbar(img_list_frame, orient="vertical", command=self.lst_images.yview)
        self.lst_images.config(yscrollcommand=img_scroll.set)
        self.lst_images.pack(side="left", fill="x", expand=True)
        img_scroll.pack(side="right", fill="y")

        img_btn_frame = ttk.Frame(img_section)
        img_btn_frame.pack(fill="x", pady=2)
        ttk.Button(img_btn_frame, text="➕ Thêm Ảnh...", command=self._add_images_prompt).pack(side="left", padx=2)
        ttk.Button(img_btn_frame, text="❌ Xóa Ảnh", command=self._remove_selected_image).pack(side="left", padx=2)
        ttk.Button(img_btn_frame, text="🧹 Xóa Tất Cả", command=self._clear_all_images).pack(side="left", padx=2)

        # Bottom Bar: Summary info + Save button
        content_btn_frame = ttk.Frame(right_frame)
        content_btn_frame.pack(fill="x", pady=5)
        self.lbl_drafts_summary = ttk.Label(content_btn_frame, text="Đã chọn: 1 / 1 bản nháp để đăng", font=("Arial", 9, "bold"))
        self.lbl_drafts_summary.pack(side="left", padx=2)
        ttk.Button(content_btn_frame, text="💾 Lưu Bản Nháp", style="Action.TButton", command=self._save_posts_file).pack(side="right")

        # Load initial data
        self._load_groups_and_posts()

    def _load_groups_and_posts(self):
        """Loads links.json and posts.json into the UI."""
        # 1. Load groups (Ensure file exists with valid contract schema if missing)
        ensure_group_targets_file(str(self.links_file))
        self.tree_groups.delete(*self.tree_groups.get_children())
        state_mgr = StateManager(str(self.history_file))

        if self.links_file.exists():
            try:
                targets = load_group_targets(str(self.links_file))
                for idx, g in enumerate(targets, 1):
                    url = g["url"]
                    in_cd, rem_secs = state_mgr.is_in_cooldown(url, cooldown_hours=self.config.cooldown_hours)
                    if in_cd:
                        rem_h = round(rem_secs / 3600.0, 1)
                        status_str = f"⏳ Chờ {rem_h}h"
                    else:
                        status_str = "🟢 Sẵn sàng"

                    g_name = g.get("name", "—")
                    g_members = g.get("members", "—")
                    g_privacy = g.get("privacy", "—")

                    # Membership Status Badge
                    raw_mb = g.get("membership")
                    if raw_mb in ("ALREADY_JOINED", "JOINED_SUCCESS"):
                        membership_str = "✅ Đã vào"
                    elif raw_mb in ("PENDING_APPROVAL", "JOIN_REQUEST_SENT"):
                        membership_str = "⏳ Chờ duyệt"
                    elif raw_mb in ("NOT_JOINED", "NEEDS_JOIN"):
                        membership_str = "❌ Chưa vào"
                    elif raw_mb == "ACTION_BLOCKED":
                        membership_str = "🚫 Bị chặn"
                    else:
                        membership_str = "—"

                    self.tree_groups.insert(
                        "", "end",
                        values=(idx, g_name, g_members, g_privacy, membership_str, url, status_str),
                    )
            except Exception as err:
                self.logger.error("Lỗi đọc links.json: %s", err)

        # 2. Load post drafts collection
        try:
            self.posts = load_posts_collection(str(self.posts_file))
        except Exception as err:
            self.logger.error("Lỗi đọc posts.json: %s", err)
            self.posts = []

        if not self.posts:
            self.posts = [
                PostItem(
                    id="post_1",
                    name="Bản nháp 01",
                    content="",
                    images=[],
                    is_selected=True,
                    status="draft",
                )
            ]

        self.active_draft_idx = min(max(0, self.active_draft_idx), len(self.posts) - 1)
        self._refresh_drafts_ui()

    def _refresh_drafts_ui(self):
        """Refreshes combobox, status labels, and loads active draft into controls."""
        if not hasattr(self, "cbo_drafts") or not self.posts:
            return

        self.active_draft_idx = min(max(0, self.active_draft_idx), len(self.posts) - 1)
        self._load_active_draft_to_controls()
        self._update_draft_combobox_titles()

    def _load_active_draft_to_controls(self):
        """Loads the active draft data into entry, text, checkbox, and image listbox."""
        if not self.posts or self.active_draft_idx >= len(self.posts):
            return
        active_post = self.posts[self.active_draft_idx]

        if hasattr(self, "entry_draft_name"):
            self.entry_draft_name.delete(0, tk.END)
            self.entry_draft_name.insert(0, active_post.name)

        if hasattr(self, "chk_draft_selected_var") and self.chk_draft_selected_var is not None:
            self.chk_draft_selected_var.set(active_post.is_selected)

        self.txt_content.delete("1.0", tk.END)
        self.txt_content.insert("1.0", active_post.content)
        self._update_char_counter()

        self.lst_images.delete(0, tk.END)
        for img in active_post.images:
            self.lst_images.insert(tk.END, img)

    def _sync_active_draft_from_controls(self):
        """Syncs GUI controls back into current PostItem."""
        if not hasattr(self, "posts") or not self.posts or self.active_draft_idx >= len(self.posts):
            return
        curr = self.posts[self.active_draft_idx]
        if hasattr(self, "entry_draft_name"):
            name_val = self.entry_draft_name.get().strip()
            if name_val:
                curr.name = name_val
        if hasattr(self, "chk_draft_selected_var") and self.chk_draft_selected_var is not None:
            curr.is_selected = bool(self.chk_draft_selected_var.get())
        if hasattr(self, "txt_content"):
            curr.content = self.txt_content.get("1.0", "end-1c")
        if hasattr(self, "lst_images"):
            curr.images = list(self.lst_images.get(0, tk.END))
        curr.status = "ready" if (curr.content.strip() or curr.images) else "draft"

    def _update_draft_combobox_titles(self):
        """Updates combobox labels and summary counts."""
        if not hasattr(self, "posts") or not self.posts or not hasattr(self, "cbo_drafts"):
            return
        vals = []
        selected_cnt = 0
        for idx, p in enumerate(self.posts, 1):
            if p.is_selected:
                selected_cnt += 1
            check_mark = "✓" if p.is_selected else " "
            status_text = "Sẵn sàng" if p.status == "ready" else "Nháp"
            vals.append(f"[{idx:02d}] {p.name} [{check_mark}] ({len(p.images)} ảnh) · {status_text}")

        self.cbo_drafts["values"] = vals
        if 0 <= self.active_draft_idx < len(vals):
            self.cbo_drafts.current(self.active_draft_idx)

        if hasattr(self, "lbl_drafts_summary"):
            self.lbl_drafts_summary.config(
                text=f"Đã chọn: {selected_cnt} / {len(self.posts)} bản nháp để đăng"
            )
        if hasattr(self, "lbl_draft_stats"):
            self.lbl_draft_stats.config(
                text=f"Bản nháp {self.active_draft_idx + 1} / {len(self.posts)}"
            )

    def _on_draft_selected_from_combobox(self, event=None):
        """Handles switching drafts via combobox dropdown."""
        self._sync_active_draft_from_controls()
        new_idx = self.cbo_drafts.current()
        if 0 <= new_idx < len(self.posts):
            self.active_draft_idx = new_idx
            self._load_active_draft_to_controls()
            self._update_draft_combobox_titles()

    def _on_draft_selected_toggle(self):
        """Toggles active draft's is_selected state."""
        self._sync_active_draft_from_controls()
        self._update_draft_combobox_titles()

    def _on_draft_name_changed(self, event=None):
        """Updates title in real time."""
        self._sync_active_draft_from_controls()
        self._update_draft_combobox_titles()

    def _add_draft(self):
        """Creates a brand new empty draft."""
        self._sync_active_draft_from_controls()
        new_id = f"post_{int(time.time())}_{len(self.posts) + 1}"
        new_post = PostItem(
            id=new_id,
            name=f"Bản nháp {len(self.posts) + 1:02d}",
            content="",
            images=[],
            is_selected=True,
            status="draft",
        )
        self.posts.append(new_post)
        self.active_draft_idx = len(self.posts) - 1
        save_posts_collection(self.posts_file, self.posts)
        self._refresh_drafts_ui()
        self.logger.info("Đã tạo bản nháp mới: %s", new_post.name)

    def _duplicate_draft(self):
        """Duplicates the currently active draft."""
        self._sync_active_draft_from_controls()
        if not self.posts or self.active_draft_idx >= len(self.posts):
            return
        curr = self.posts[self.active_draft_idx]
        new_id = f"post_{int(time.time())}_{len(self.posts) + 1}"
        dup_post = PostItem(
            id=new_id,
            name=f"{curr.name} (Bản sao)",
            content=curr.content,
            images=list(curr.images),
            is_selected=True,
            status=curr.status,
        )
        self.posts.append(dup_post)
        self.active_draft_idx = len(self.posts) - 1
        save_posts_collection(self.posts_file, self.posts)
        self._refresh_drafts_ui()
        self.logger.info("Đã nhân bản bài viết: %s -> %s", curr.name, dup_post.name)

    def _delete_draft(self):
        """Deletes the currently active draft."""
        if len(self.posts) <= 1:
            messagebox.showwarning("Cảnh Báo", "Cần giữ lại ít nhất một bản nháp bài viết.")
            return
        curr = self.posts[self.active_draft_idx]
        if not messagebox.askyesno("Xác Nhận Xóa", f"Bạn có chắc muốn xóa bản nháp '{curr.name}' không?"):
            return
        del self.posts[self.active_draft_idx]
        self.active_draft_idx = max(0, self.active_draft_idx - 1)
        save_posts_collection(self.posts_file, self.posts)
        self._refresh_drafts_ui()
        self.logger.info("Đã xóa bản nháp: %s", curr.name)

    def _add_images_prompt(self):
        """Prompt file dialog to select image files."""
        if not filedialog:
            return
        files = filedialog.askopenfilenames(
            title="Chọn tệp hình ảnh đính kèm",
            filetypes=[
                ("Hình ảnh", "*.jpg *.jpeg *.png *.webp *.heic *.bmp"),
                ("Tất cả tệp", "*.*"),
            ],
        )
        if files:
            current_imgs = set(self.lst_images.get(0, tk.END))
            for f in files:
                if f not in current_imgs:
                    self.lst_images.insert(tk.END, f)
            self._sync_active_draft_from_controls()
            self._update_draft_combobox_titles()
            self.logger.info("Đã thêm %d ảnh vào bản nháp hiện tại.", len(files))

    def _remove_selected_image(self):
        """Removes highlighted image from Listbox."""
        sel = self.lst_images.curselection()
        if not sel:
            messagebox.showinfo("Thông Báo", "Vui lòng chọn một ảnh để xóa.")
            return
        self.lst_images.delete(sel[0])
        self._sync_active_draft_from_controls()
        self._update_draft_combobox_titles()

    def _clear_all_images(self):
        """Clears all images from Listbox."""
        self.lst_images.delete(0, tk.END)
        self._sync_active_draft_from_controls()
        self._update_draft_combobox_titles()

    def _update_char_counter(self, event=None):
        """Updates character count label and syncs draft content."""
        text = self.txt_content.get("1.0", "end-1c")
        self.lbl_char_count.config(text=f"Ký tự: {len(text)}")
        if hasattr(self, "posts") and self.posts and 0 <= self.active_draft_idx < len(self.posts):
            self.posts[self.active_draft_idx].content = text
            self.posts[self.active_draft_idx].status = "ready" if (text.strip() or self.posts[self.active_draft_idx].images) else "draft"

    def _get_group_url_from_item(self, item) -> str:
        """Safely extracts group URL from a tree_groups row."""
        try:
            vals = self.tree_groups.item(item, "values")
            for v in vals:
                if str(v).startswith("http"):
                    return str(v)
        except Exception:
            pass
        return ""

    def _start_join_groups_flow(self):
        """
        Coordinates the flow to verify and join Facebook groups.
        If a group is already joined, it skips; if not, it executes the join group flow.
        """
        selected_items = self.tree_groups.selection()
        target_items = selected_items if selected_items else self.tree_groups.get_children()

        if not target_items:
            messagebox.showinfo("Thông Báo", "Danh sách nhóm mục tiêu đang rỗng.")
            return

        target_urls = [self._get_group_url_from_item(it) for it in target_items if self._get_group_url_from_item(it)]
        if not target_urls:
            messagebox.showwarning("Cảnh Báo", "Không tìm thấy URL hợp lệ trong các nhóm đã chọn.")
            return

        # Pre-check local status to inform user
        already_joined_cnt = 0
        for it in target_items:
            vals = self.tree_groups.item(it, "values")
            for v in vals:
                if "Đã vào" in str(v):
                    already_joined_cnt += 1
                    break

        if already_joined_cnt == len(target_urls) and len(target_urls) > 0:
            if not messagebox.askyesno(
                "Đã Tham Gia",
                f"Tất cả {len(target_urls)} nhóm được chọn đều đã có trạng thái 'Đã vào'.\n\n"
                "Bạn có muốn kiểm tra lại trạng thái thực tế trên Facebook không?"
            ):
                return

        join_min = None
        join_max = None
        join_limit = None
        if hasattr(self, "entry_join_min") and hasattr(self, "entry_join_max"):
            try:
                join_min = float(self.entry_join_min.get().strip())
                join_max = float(self.entry_join_max.get().strip())
            except ValueError:
                pass
        if hasattr(self, "entry_join_limit"):
            try:
                join_limit = int(self.entry_join_limit.get().strip())
            except ValueError:
                pass

        eff_min = join_min if join_min is not None else getattr(self.config, "join_delay_min", 15.0)
        eff_max = join_max if join_max is not None else getattr(self.config, "join_delay_max", 35.0)
        eff_limit = join_limit if join_limit is not None else getattr(self.config, "join_limit", 10)

        self.logger.info(
            "🤝 Bắt đầu quy trình kiểm tra & tham gia cho %d nhóm mục tiêu (Giãn cách: %.1f-%.1fs, Tối đa: %d nhóm)...",
            len(target_urls),
            eff_min,
            eff_max,
            eff_limit,
        )
        self._launch_worker(
            dry_run=False,
            check_dom=False,
            join_mode=True,
            custom_targets=target_urls,
            batch_size=eff_limit,
            join_delay_min=eff_min,
            join_delay_max=eff_max,
        )

    def _start_post_to_selected_groups(self):
        """
        Initiates posting workflow specifically for the group(s) selected in the treeview.
        Checks cooldown and asks user confirmation before overriding cooldown if needed.
        """
        selected_items = self.tree_groups.selection()
        if not selected_items:
            messagebox.showinfo("Thông Báo", "Vui lòng chọn ít nhất một nhóm trong danh sách để đăng bài.")
            return

        target_urls = [self._get_group_url_from_item(it) for it in selected_items if self._get_group_url_from_item(it)]
        if not target_urls:
            messagebox.showwarning("Cảnh Báo", "Không tìm thấy URL hợp lệ trong các nhóm đã chọn.")
            return

        # Pre-check cooldown
        state_mgr = StateManager(str(self.history_file))
        cd_groups = []
        for u in target_urls:
            in_cd, last_time = state_mgr.is_in_cooldown(u, cooldown_hours=self.config.cooldown_hours)
            if in_cd:
                cd_groups.append((u, last_time))

        override_cooldown = False
        if cd_groups:
            msg = f"Có {len(cd_groups)}/{len(target_urls)} nhóm đang trong thời gian giãn cách Cooldown ({self.config.cooldown_hours}h):\n"
            for u, t in cd_groups[:3]:
                if isinstance(t, (int, float)):
                    rem_m = int(t // 60)
                    rem_h = rem_m // 60
                    t_str = f"còn lại {rem_h}h {rem_m % 60}p" if rem_h > 0 else f"còn lại {rem_m} phút"
                elif hasattr(t, "strftime"):
                    t_str = t.strftime("%H:%M %d/%m")
                else:
                    t_str = str(t) if t else "gần đây"
                msg += f"• {u} ({t_str})\n"
            if len(cd_groups) > 3:
                msg += f"... và {len(cd_groups) - 3} nhóm khác.\n"
            msg += "\nBạn có muốn bỏ qua Cooldown và tiếp tục đăng bài vào nhóm này không?"
            if not messagebox.askyesno("Cảnh Báo Cooldown", msg):
                return
            override_cooldown = True

        self.logger.info("🚀 Bắt đầu đăng bài cho %d nhóm đã chọn: %s", len(target_urls), ", ".join(target_urls))
        self._launch_worker(
            dry_run=False,
            check_dom=False,
            batch_size=len(target_urls),
            custom_targets=target_urls,
            override_cooldown=override_cooldown,
        )

    def _add_group_prompt(self):
        """Dialog to paste/add multiple Facebook group URLs at once."""
        dialog = tk.Toplevel(self)
        dialog.title("Thêm Nhóm Mục Tiêu (Hỗ trợ dán hàng loạt)")
        dialog.geometry("540x360")
        dialog.transient(self)

        ttk.Label(
            dialog,
            text="Dán danh sách link nhóm Facebook vào đây (mỗi link 1 dòng hoặc cách nhau bởi dấu cách, phẩy):",
            font=("Arial", 10),
        ).pack(pady=(12, 4), padx=12, anchor="w")

        txt = ScrolledText(dialog, wrap="word", height=10, font=("Arial", 10))
        txt.pack(fill="both", expand=True, padx=12, pady=5)
        txt.focus()

        btn_row = ttk.Frame(dialog)
        btn_row.pack(fill="x", padx=12, pady=10)

        def paste_cb():
            try:
                txt.insert(tk.END, self.clipboard_get() + "\n")
            except Exception:
                pass

        ttk.Button(btn_row, text="📋 Dán từ Clipboard", command=paste_cb).pack(side="left")

        def do_add():
            raw = txt.get("1.0", tk.END)
            extracted = extract_facebook_group_urls(raw)
            if not extracted:
                messagebox.showwarning("Không Có Link", "Không tìm thấy URL nhóm Facebook hợp lệ nào. Vui lòng kiểm tra lại.")
                return

            existing = set()
            for item in self.tree_groups.get_children():
                u = self._get_group_url_from_item(item)
                if u:
                    existing.add(u)

            state_mgr = StateManager(str(self.history_file))
            added = 0

            for url in extracted:
                if url not in existing:
                    existing.add(url)
                    idx = len(self.tree_groups.get_children()) + 1
                    in_cd, rem_secs = state_mgr.is_in_cooldown(url, cooldown_hours=self.config.cooldown_hours)
                    status_str = f"⏳ Chờ {round(rem_secs/3600.0, 1)}h" if in_cd else "🟢 Sẵn sàng"
                    self.tree_groups.insert(
                        "", "end",
                        values=(idx, "—", "—", "—", "—", url, status_str),
                    )
                    added += 1

            self._save_groups_file(silent=True)
            self.logger.info("Đã thêm %d nhóm mới vào danh sách (tự động đồng bộ links.json).", added)
            dialog.destroy()
            messagebox.showinfo("Thành Công", f"Đã trích xuất và thêm {added} nhóm mới thành công!")

        ttk.Button(btn_row, text="➕ Thêm Vào Danh Sách", style="Action.TButton", command=do_add).pack(side="right")
        ttk.Button(btn_row, text="Hủy", command=dialog.destroy).pack(side="right", padx=5)

        txt.bind("<Control-Return>", lambda e: do_add())
        txt.bind("<Command-Return>", lambda e: do_add())

    def _remove_selected_group(self):
        """Removes highlighted group(s) from Treeview and auto-syncs links.json."""
        selected = self.tree_groups.selection()
        if not selected:
            messagebox.showinfo("Thông Báo", "Vui lòng chọn ít nhất một nhóm để xóa.")
            return
        for item in selected:
            self.tree_groups.delete(item)
        # Re-index
        for idx, item in enumerate(self.tree_groups.get_children(), 1):
            vals = list(self.tree_groups.item(item, "values"))
            vals[0] = idx
            self.tree_groups.item(item, values=vals)
        # Auto-sync
        self._save_groups_file(silent=True)
        self.logger.info("Đã xóa %d nhóm khỏi danh sách (tự động đồng bộ links.json).", len(selected))

    def _paste_groups_from_clipboard(self):
        """Pastes and sanitizes URLs from clipboard, auto-syncing to links.json."""
        try:
            cb_text = self.clipboard_get()
            extracted = extract_facebook_group_urls(cb_text)
            if not extracted:
                messagebox.showinfo("Clipboard", "Không tìm thấy URL nhóm Facebook hợp lệ trong Clipboard.")
                return

            existing = set()
            for item in self.tree_groups.get_children():
                u = self._get_group_url_from_item(item)
                if u:
                    existing.add(u)

            state_mgr = StateManager(str(self.history_file))
            added_count = 0

            for url in extracted:
                if url not in existing:
                    existing.add(url)
                    idx = len(self.tree_groups.get_children()) + 1
                    in_cd, rem_secs = state_mgr.is_in_cooldown(url, cooldown_hours=self.config.cooldown_hours)
                    status_str = f"⏳ Chờ {round(rem_secs/3600.0, 1)}h" if in_cd else "🟢 Sẵn sàng"
                    self.tree_groups.insert(
                        "", "end",
                        values=(idx, "—", "—", "—", "—", url, status_str),
                    )
                    added_count += 1

            self._save_groups_file(silent=True)
            self.logger.info("Đã dán và tự động lưu %d nhóm mới từ Clipboard.", added_count)
            messagebox.showinfo("Thành Công", f"Đã trích xuất và thêm {added_count} nhóm mới từ Clipboard (Đã tự động lưu).")
        except Exception as err:
            messagebox.showerror("Lỗi Clipboard", f"Không thể đọc Clipboard: {err}")

    def _open_url_in_browser(self, url: str):
        """Opens given URL in system default browser (Chrome)."""
        if not url or not url.startswith("http"):
            return
        try:
            self.logger.info("🌐 Mở liên kết Facebook trong trình duyệt: %s", url)
            webbrowser.open(url)
        except Exception as err:
            self.logger.warning("Không thể mở trình duyệt cho link %s: %s", url, err)

    def _open_selected_group_url(self):
        """Opens selected group URL from tree_groups in browser."""
        selected = self.tree_groups.selection()
        if not selected:
            messagebox.showinfo("Thông Báo", "Vui lòng chọn ít nhất một nhóm để mở link.")
            return
        for item in selected:
            url = self._get_group_url_from_item(item)
            if url:
                self._open_url_in_browser(url)

    def _on_group_row_double_click(self, event=None):
        """Handler for double click or Enter key on group row."""
        item = self.tree_groups.focus()
        if item:
            url = self._get_group_url_from_item(item)
            if url:
                self._open_url_in_browser(url)

    def _deselect_groups(self, event=None):
        """Clears selection in target groups Treeview when clicking outside."""
        if hasattr(self, "tree_groups"):
            self.tree_groups.selection_set([])
            self._drag_start_item = None
            self._is_dragging_groups = False

    def _on_group_tree_press(self, event):
        """Handles mouse press: deselects when clicking empty space, prepares drag-select."""
        region = self.tree_groups.identify_region(event.x, event.y)
        if region in ("heading", "separator"):
            return
        item = self.tree_groups.identify_row(event.y)
        if not item:
            self._deselect_groups()
            return "break"
        self._drag_start_item = item
        self._is_dragging_groups = False
        self._drag_start_ctrl = bool(event.state & (0x0004 | 0x0008))
        self._drag_initial_sel = set(self.tree_groups.selection())

    def _on_group_tree_drag(self, event):
        """Selects multiple groups by dragging mouse over rows."""
        children = self.tree_groups.get_children("")
        if not children:
            return
        curr = self.tree_groups.identify_row(event.y)
        if not getattr(self, "_drag_start_item", None):
            if curr and curr in children:
                self._drag_start_item = curr
            else:
                return
        if self._drag_start_item not in children:
            return

        h = self.tree_groups.winfo_height()
        if not curr:
            if event.y > h:
                curr = children[-1]
                self.tree_groups.yview_scroll(1, "units")
            elif event.y < 0:
                curr = children[0]
                self.tree_groups.yview_scroll(-1, "units")
            else:
                curr = children[-1]
        if curr not in children:
            return

        if curr != self._drag_start_item:
            self._is_dragging_groups = True

        idx_start = children.index(self._drag_start_item)
        idx_curr = children.index(curr)
        low, high = min(idx_start, idx_curr), max(idx_start, idx_curr)
        dragged_items = children[low : high + 1]

        if getattr(self, "_drag_start_ctrl", False):
            self.tree_groups.selection_set(list(self._drag_initial_sel.union(dragged_items)))
        else:
            self.tree_groups.selection_set(dragged_items)

    def _on_group_tree_click(self, event):
        """Handler for click on group Treeview; if URL column clicked and not dragging, opens in browser."""
        was_dragging = getattr(self, "_is_dragging_groups", False)
        self._is_dragging_groups = False
        self._drag_start_item = None
        if was_dragging:
            return "break"
        region = self.tree_groups.identify_region(event.x, event.y)
        if region == "cell":
            col = self.tree_groups.identify_column(event.x)
            if col in ("#5", "#6"):
                item = self.tree_groups.identify_row(event.y)
                if item:
                    url = self._get_group_url_from_item(item)
                    if url:
                        self._open_url_in_browser(url)

    def _reload_groups_file(self):
        """Reloads groups from links.json into the UI."""
        self._load_groups_and_posts()
        count = len(self.tree_groups.get_children())
        self.logger.info("🔄 Đã nạp lại %d nhóm từ file links.json vào GUI.", count)
        messagebox.showinfo("Nạp Lại", f"Đã nạp lại {count} nhóm từ file links.json thành công!")

    def _save_groups_file(self, silent: bool = False):
        """Persists groups into links.json with structured metadata and timestamps."""
        groups = []
        now_iso = datetime.now(timezone.utc).isoformat()
        existing_targets = {t["url"].rstrip("/"): t for t in load_group_targets(str(self.links_file))}

        for item in self.tree_groups.get_children():
            vals = self.tree_groups.item(item, "values")
            url = self._get_group_url_from_item(item)
            if not url:
                continue
            clean_u = url.rstrip("/")
            prev_updated = existing_targets.get(clean_u, {}).get("updated_at")
            prev_membership = existing_targets.get(clean_u, {}).get("membership")
            name_val = vals[1] if len(vals) > 1 else "—"

            if name_val and name_val != "—":
                up_at = prev_updated or now_iso
            else:
                up_at = None

            # Membership from row badge or fallback
            membership_val = prev_membership
            row_mb = str(vals[4]) if len(vals) > 4 else ""
            if "Đã vào" in row_mb:
                membership_val = "ALREADY_JOINED"
            elif "Chờ duyệt" in row_mb:
                membership_val = "PENDING_APPROVAL"
            elif "Chưa vào" in row_mb:
                membership_val = "NOT_JOINED"
            elif "Bị chặn" in row_mb:
                membership_val = "ACTION_BLOCKED"

            groups.append({
                "url": url,
                "name": name_val,
                "members": vals[2] if len(vals) > 2 else "—",
                "privacy": vals[3] if len(vals) > 3 else "—",
                "membership": membership_val,
                "updated_at": up_at,
            })

        try:
            save_group_targets(str(self.links_file), groups)
            if not silent:
                messagebox.showinfo("Đã Lưu", f"Đã lưu {len(groups)} nhóm vào links.json!")
            self.logger.info("Đã lưu danh sách %d nhóm từ Desktop GUI vào links.json.", len(groups))
        except Exception as err:
            if not silent:
                messagebox.showerror("Lỗi Ghi File", f"Không thể lưu links.json: {err}")

    def _set_scan_buttons_state(self, state: str):
        """Enables or disables metadata scan, post selected, and group join buttons."""
        for btn_name in ["btn_scan_meta", "btn_scan_meta_top", "btn_join_groups", "btn_join_top", "btn_post_selected"]:
            btn = getattr(self, btn_name, None)
            if btn:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass

    def _scan_groups_metadata(self):
        """Scans Facebook groups in Chrome to fetch Name, Member count, and Privacy, saving to links.json."""
        selected = self.tree_groups.selection()
        target_items = selected if selected else self.tree_groups.get_children()

        if not target_items:
            messagebox.showinfo("Thông Báo", "Danh sách nhóm đang rỗng.")
            return

        urls_to_scan = [self._get_group_url_from_item(it) for it in target_items if self._get_group_url_from_item(it)]
        self._set_scan_buttons_state("disabled")
        self.logger.info("🔍 Bắt đầu quét thông tin cho %d nhóm Facebook...", len(urls_to_scan))

        def _task():
            try:
                from selenium.webdriver.common.by import By
                from services.group_verifier import GroupMembershipVerifier
                verifier = GroupMembershipVerifier(selectors_path=str(self.config.json_dir / "selectors.json"))
                with get_driver(self.config) as driver:
                    for idx, url in enumerate(urls_to_scan, 1):
                        if self.stop_event.is_set():
                            self.logger.warning("🛑 Dừng quét thông tin nhóm theo yêu cầu.")
                            break
                        self.logger.info("🔍 [%d/%d] Đang tải thông tin nhóm: %s", idx, len(urls_to_scan), url)
                        try:
                            driver.get(url)
                            time.sleep(2.5)
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

                            mem_status = verifier.check_status(driver)
                            if mem_status.value == "UNKNOWN":
                                time.sleep(1.5)
                                mem_status = verifier.check_status(driver)
                            raw_mb = mem_status.value
                            if raw_mb in ("ALREADY_JOINED", "JOINED_SUCCESS"):
                                membership_str = "✅ Đã vào"
                            elif raw_mb in ("PENDING_APPROVAL", "JOIN_REQUEST_SENT"):
                                membership_str = "⏳ Chờ duyệt"
                            elif raw_mb in ("NOT_JOINED", "NEEDS_JOIN"):
                                membership_str = "❌ Chưa vào"
                            elif raw_mb == "ACTION_BLOCKED":
                                membership_str = "🚫 Bị chặn"
                            else:
                                membership_str = "—"

                            info = parse_group_header_info(raw_title, sub_text, membership=raw_mb)

                            def update_row(u=url, inf=info, mb_badge=membership_str):
                                for item in self.tree_groups.get_children():
                                    if self._get_group_url_from_item(item) == u:
                                        vals = list(self.tree_groups.item(item, "values"))
                                        vals[1] = inf.get("name", "—")
                                        vals[2] = inf.get("members", "—")
                                        vals[3] = inf.get("privacy", "—")
                                        vals[4] = mb_badge
                                        self.tree_groups.item(item, values=vals)
                                        break
                            self.after(0, update_row)

                        except Exception as e:
                            self.logger.warning("Không thể đọc thông tin nhóm %s: %s", url, e)

                def _finish():
                    self._save_groups_file(silent=True)
                    self._set_scan_buttons_state("normal")
                    self.logger.info("✅ Hoàn tất quét thông tin nhóm (đã lưu vào links.json).")
                    messagebox.showinfo("Thành Công", f"Đã quét và lưu thông tin cho {len(urls_to_scan)} nhóm vào links.json thành công!")

                self.after(0, _finish)
            except Exception as err:
                self.logger.error("Lỗi khi quét thông tin nhóm: %s", err)
                self.after(0, lambda: self._set_scan_buttons_state("normal"))

        t = threading.Thread(target=_task, daemon=True, name="GroupMetaScannerThread")
        t.start()

    def _save_posts_file(self):
        """Persists all post drafts into posts.json with backward compatibility."""
        self._sync_active_draft_from_controls()
        if not hasattr(self, "posts") or not self.posts:
            content = self.txt_content.get("1.0", "end-1c") if hasattr(self, "txt_content") else ""
            images = list(self.lst_images.get(0, tk.END)) if hasattr(self, "lst_images") else []
            self.posts = [
                PostItem(
                    id="post_1",
                    name="Bản nháp 01",
                    content=content,
                    images=images,
                    is_selected=True,
                    status="ready" if (content.strip() or images) else "draft",
                )
            ]

        # Check if at least one post has content or images
        has_content_or_img = any(p.content.strip() or p.images for p in self.posts)
        if not has_content_or_img:
            messagebox.showwarning("Cảnh Báo", "Tất cả các bản nháp đều đang để trống nội dung và ảnh!")

        try:
            save_posts_collection(self.posts_file, self.posts)
            self._update_draft_combobox_titles()
            messagebox.showinfo(
                "Đã Lưu",
                f"Đã lưu thành công {len(self.posts)} bản nháp vào posts.json!"
            )
            self.logger.info("Đã lưu %d bản nháp bài viết vào posts.json từ Desktop GUI.", len(self.posts))
        except Exception as err:
            messagebox.showerror("Lỗi Ghi File", f"Không thể lưu posts.json: {err}")

    # =========================================================================
    # TAB 3: LIVE DASHBOARD & COLORED LOG CONSOLE
    # =========================================================================

    def _build_tab_dashboard(self):
        """Constructs Dashboard & Real-Time Console."""
        dash = ttk.Frame(self.tab_dashboard, padding=10)
        dash.pack(fill="both", expand=True)

        # 1. KPI Cards Row
        kpi_frame = ttk.Frame(dash)
        kpi_frame.pack(fill="x", pady=(0, 10))

        self.card_status = self._create_kpi_card(kpi_frame, "TRẠNG THÁI BOT", "🟢 IDLE", "#e1f5fe")
        self.card_progress = self._create_kpi_card(kpi_frame, "TIẾN ĐỘ PHIÊN", "0 / 0", "#e8f5e9")
        self.card_cooldown = self._create_kpi_card(kpi_frame, "ĐANG COOLDOWN 24H", "0 nhóm", "#fff3e0")
        self.card_circuit = self._create_kpi_card(kpi_frame, "CIRCUIT BREAKER", "🟢 BÌNH THƯỜNG", "#f3e5f5")

        self.card_status.pack(side="left", fill="both", expand=True, padx=4)
        self.card_progress.pack(side="left", fill="both", expand=True, padx=4)
        self.card_cooldown.pack(side="left", fill="both", expand=True, padx=4)
        self.card_circuit.pack(side="left", fill="both", expand=True, padx=4)

        # 2. Controls & Actions Rows
        ctrl_frame = ttk.Frame(dash, padding=5)
        ctrl_frame.pack(fill="x", pady=(0, 10))

        # Row 1: Primary Execution Controls
        ctrl_row1 = ttk.Frame(ctrl_frame)
        ctrl_row1.pack(fill="x", pady=(0, 5))

        self.btn_start = ttk.Button(ctrl_row1, text="▶️ BẮT ĐẦU CHẠY", style="Action.TButton", command=self._start_bot_flow)
        self.btn_start.pack(side="left", padx=5)

        self.btn_dryrun = ttk.Button(ctrl_row1, text="🔍 CHẠY THỬ (DRY-RUN)", command=self._start_dry_run_flow)
        self.btn_dryrun.pack(side="left", padx=5)

        self.btn_canary = ttk.Button(ctrl_row1, text="🩺 KIỂM TRA DOM", command=self._start_dom_check_flow)
        self.btn_canary.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(ctrl_row1, text="🛑 DỪNG KHẨN CẤP", style="Danger.TButton", state="disabled", command=self._stop_bot_flow)
        self.btn_stop.pack(side="right", padx=5)

        self.btn_hide_bg = ttk.Button(ctrl_row1, text="🕶️ ẨN XUỐNG NỀN", command=self._hide_to_background)
        self.btn_hide_bg.pack(side="right", padx=5)

        # Row 2: Account & Settings Controls
        ctrl_row2 = ttk.Frame(ctrl_frame)
        ctrl_row2.pack(fill="x")

        self.btn_login = ttk.Button(ctrl_row2, text="🔑 ĐĂNG NHẬP FACEBOOK", command=self._start_login_flow)
        self.btn_login.pack(side="left", padx=5)

        self.btn_logout = ttk.Button(ctrl_row2, text="🚪 ĐĂNG XUẤT", command=self._start_logout_flow)
        self.btn_logout.pack(side="left", padx=5)

        # Batch override spinbox
        ttk.Label(ctrl_row2, text="Batch Limit:").pack(side="left", padx=(15, 2))
        self.spn_batch = ttk.Spinbox(ctrl_row2, from_=1, to=100, width=5)
        self.spn_batch.set(self.config.max_posts_per_run)
        self.spn_batch.pack(side="left")

        # Headless mode toggle
        self.chk_headless = ttk.Checkbutton(
            ctrl_row2,
            text="Ẩn Chrome",
            variable=self.var_headless,
        )
        self.chk_headless.pack(side="left", padx=(10, 5))

        # 3. Progress Bar
        self.progress_bar = ttk.Progressbar(dash, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 10))

        # 3.5. Task Manager Bar / Panel (Quản lý các công việc đang chạy song song/hàng đợi)
        self.task_manager_frame = ttk.LabelFrame(
            dash, text="⚡ BẢNG QUẢN LÝ CÔNG VIỆC (TASK MANAGER)", padding=(10, 6)
        )
        self.task_manager_frame.pack(fill="x", pady=(0, 10))
        self._setup_task_manager_panel(self.task_manager_frame)

        # 4. Live Console Frame
        console_hdr = ttk.Frame(dash)
        console_hdr.pack(fill="x")
        ttk.Label(console_hdr, text="Nhật Ký Hoạt Động Trực Tiếp (Live Console)", style="Header.TLabel").pack(side="left")
        ttk.Button(console_hdr, text="🧹 Xóa Log", command=self._clear_console).pack(side="right")

        self.console_text = ScrolledText(
            dash,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            font=("Menlo", 9) if sys.platform == "darwin" else ("Consolas", 9),
            height=14,
            wrap="word",
        )
        self.console_text.pack(fill="both", expand=True, pady=(5, 0))

        # Color Tags Mapping
        self.console_text.tag_config("INFO", foreground="#4ec9b0")
        self.console_text.tag_config("WARNING", foreground="#ce9178")
        self.console_text.tag_config("ERROR", foreground="#f44747")
        self.console_text.tag_config("CRITICAL", foreground="#ff5555", font=("Menlo", 9, "bold"))
        self.console_text.tag_config("DEBUG", foreground="#808080")

        self._refresh_kpi_cards()

    def _create_kpi_card(self, parent, title: str, initial_val: str, bg_color: str) -> tk.Frame:
        """Constructs an individual KPI visual card."""
        f = tk.Frame(parent, bg=bg_color, relief="solid", bd=1, padx=10, pady=8)
        lbl_title = tk.Label(f, text=title, font=("Arial", 8, "bold"), bg=bg_color, fg="#555555")
        lbl_title.pack(anchor="w")
        lbl_val = tk.Label(f, text=initial_val, font=("Arial", 14, "bold"), bg=bg_color, fg="#111111")
        lbl_val.pack(anchor="w", pady=(4, 0))
        f.val_label = lbl_val  # type: ignore
        return f

    def _refresh_kpi_cards(self):
        """Updates KPI numbers from StateManager."""
        try:
            state_mgr = StateManager(str(self.history_file))
            summary = state_mgr.get_summary(self.config.cooldown_hours)
            active_cd = summary.get("active_cooldown_count", 0)
            self.card_cooldown.val_label.config(text=f"{active_cd} nhóm")  # type: ignore
        except Exception:
            pass

    def _clear_console(self):
        """Clears text area."""
        self.console_text.delete("1.0", tk.END)

    def _setup_task_manager_panel(self, parent: ttk.LabelFrame):
        """Builds the Task Manager bar displaying active/queued jobs with individual cancel buttons."""
        top_row = ttk.Frame(parent)
        top_row.pack(fill="x", pady=(0, 4))

        self.lbl_tm_summary = ttk.Label(
            top_row, text="Chưa có tác vụ nào trong hàng đợi.", font=("Arial", 9, "bold")
        )
        self.lbl_tm_summary.pack(side="left")

        ttk.Button(
            top_row, text="🧹 Dọn Dẹp Đã Xong", command=self._clear_finished_dispatcher_jobs
        ).pack(side="right", padx=(5, 0))

        ttk.Button(
            top_row, text="🛑 Dừng Tất Cả", style="Danger.TButton", command=self._stop_bot_flow
        ).pack(side="right")

        # Container for job cards
        self.tm_items_container = ttk.Frame(parent)
        self.tm_items_container.pack(fill="x", expand=True)

        self._refresh_task_manager_ui()

    def _clear_finished_dispatcher_jobs(self):
        """Cleans up completed/cancelled/failed jobs from dispatcher and refreshes UI."""
        if hasattr(self, "dispatcher"):
            self.dispatcher.clear_completed()
            self._refresh_task_manager_ui()

    def _on_dispatcher_job_update(self, job: JobItem):
        """Callback invoked whenever any job in dispatcher updates its state."""
        self._refresh_task_manager_ui()

    def _refresh_task_manager_ui(self):
        """Renders live job items in the Task Manager Panel."""
        if not hasattr(self, "tm_items_container") or not self.tm_items_container.winfo_exists():
            return

        for child in self.tm_items_container.winfo_children():
            child.destroy()

        jobs = self.dispatcher.get_jobs() if hasattr(self, "dispatcher") else []
        running_cnt = sum(1 for j in jobs if j.status == JobStatus.RUNNING)
        queued_cnt = sum(1 for j in jobs if j.status == JobStatus.QUEUED)
        done_cnt = sum(1 for j in jobs if j.status in (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED))

        if not jobs:
            self.lbl_tm_summary.config(text="Chưa có tác vụ nào trong hàng đợi.")
            empty_lbl = ttk.Label(
                self.tm_items_container,
                text="ℹ️ Bạn có thể bấm 'BẮT ĐẦU CHẠY', 'CHẠY THỬ' hoặc 'THAM GIA NHÓM' để thêm tác vụ chạy song song.",
                foreground="#888888",
            )
            empty_lbl.pack(anchor="w", pady=4)
            return

        self.lbl_tm_summary.config(
            text=f"Đang chạy: {running_cnt} | Hàng đợi: {queued_cnt} | Đã hoàn tất: {done_cnt}"
        )

        for job in jobs[-5:]:
            row = ttk.Frame(self.tm_items_container)
            row.pack(fill="x", pady=2)

            status_color = "#4ec9b0"
            if job.status == JobStatus.RUNNING:
                status_text = "[ĐANG CHẠY]"
                status_color = "#32a852"
            elif job.status == JobStatus.QUEUED:
                status_text = "[HÀNG ĐỢI]"
                status_color = "#e6a100"
            elif job.status == JobStatus.COMPLETED:
                status_text = "[HOÀN TẤT]"
                status_color = "#1877f2"
            elif job.status == JobStatus.CANCELLED:
                status_text = "[ĐÃ HỦY]"
                status_color = "#888888"
            else:
                status_text = "[LỖI]"
                status_color = "#d9534f"

            lbl_st = tk.Label(row, text=status_text, fg=status_color, font=("Arial", 9, "bold"), width=12, anchor="w")
            lbl_st.pack(side="left")

            lbl_nm = ttk.Label(row, text=f"{job.name} ({job.current_step}/{job.total_steps})", font=("Arial", 9, "bold"), width=28)
            lbl_nm.pack(side="left", padx=5)

            pbar = ttk.Progressbar(row, orient="horizontal", mode="determinate", length=130)
            pbar["value"] = job.progress
            pbar.pack(side="left", padx=5)

            lbl_pct = ttk.Label(row, text=f"{int(job.progress)}%", width=5)
            lbl_pct.pack(side="left")

            details_str = job.details
            if len(details_str) > 35:
                details_str = details_str[:32] + "..."
            lbl_det = ttk.Label(row, text=details_str, foreground="#666666", width=30)
            lbl_det.pack(side="left", padx=5)

            if job.status in (JobStatus.RUNNING, JobStatus.QUEUED):
                btn_cancel = ttk.Button(
                    row,
                    text="🛑 Dừng",
                    width=7,
                    command=lambda jid=job.job_id: self.dispatcher.cancel_job(jid),
                )
                btn_cancel.pack(side="right", padx=2)
            else:
                lbl_dur = ttk.Label(row, text=job.duration_str, foreground="#888888")
                lbl_dur.pack(side="right", padx=4)

    def _process_log_queue(self):
        """Pulls log items from queue and writes them to ScrolledText with formatting."""
        while not self.log_queue.empty():
            try:
                levelname, msg = self.log_queue.get_nowait()
                tag = levelname if levelname in ("INFO", "WARNING", "ERROR", "CRITICAL", "DEBUG") else "INFO"
                self.console_text.insert(tk.END, msg + "\n", tag)
                self.console_text.see(tk.END)
            except queue.Empty:
                break
        self.after(100, self._process_log_queue)

    # =========================================================================
    # BOT EXECUTION ORCHESTRATION & THREADING
    # =========================================================================

    def _start_bot_flow(self):
        """Initiates production posting workflow (targets selected groups if any, else all groups)."""
        selected = self.tree_groups.selection() if hasattr(self, "tree_groups") else ()
        if selected:
            self._start_post_to_selected_groups()
        else:
            self._launch_worker(dry_run=False, check_dom=False)

    def _start_dry_run_flow(self):
        """Initiates dry-run simulation."""
        self._launch_worker(dry_run=True, check_dom=False)

    def _start_dom_check_flow(self):
        """Initiates DOM health canary inspection."""
        self._launch_worker(dry_run=False, check_dom=True)

    def _start_login_flow(self):
        """Launches interactive login in a worker thread to authenticate Facebook."""
        is_busy = (self.worker_thread and self.worker_thread.is_alive()) or (
            hasattr(self, "dispatcher") and self.dispatcher.is_running
        )
        if is_busy:
            messagebox.showwarning("Đang Chạy", "Bot đang thực thi các tác vụ. Vui lòng đợi hoặc bấm Dừng trước khi đăng nhập.")
            return

        try:
            if hasattr(self, "cfg_entries") and "EMAIL" in self.cfg_entries and "PASSWORD" in self.cfg_entries:
                new_email = self.cfg_entries["EMAIL"].get().strip()
                new_pwd = self.cfg_entries["PASSWORD"].get().strip()
                if new_email and new_pwd and (new_email != self.config.email or new_pwd != self.config.password):
                    self._save_env_config()
            self._load_config_state()
            self.config = BotConfig.from_env(str(self.env_file))
        except Exception as err:
            messagebox.showerror("Lỗi Cấu Hình", f"Cấu hình lỗi: {err}")
            return

        self.stop_event.clear()
        self.btn_start.configure(state="disabled")
        self.btn_login.configure(state="disabled")
        self.btn_logout.configure(state="disabled")
        self.btn_dryrun.configure(state="disabled")
        self.btn_canary.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.card_status.val_label.config(text="🔑 ĐANG ĐĂNG NHẬP")  # type: ignore

        def _task():
            try:
                bot = FacebookBot(config=self.config, custom_logger=self.logger)

                def _gui_prompt() -> bool:
                    result = [False]
                    ev = threading.Event()

                    def _ask():
                        ans = messagebox.askokcancel(
                            "🔑 Xác Nhận Đăng Nhập Facebook",
                            "Trình duyệt Chrome của bot đã được mở.\n\n"
                            "1. Vui lòng kiểm tra màn hình Chrome và đăng nhập vào tài khoản Facebook.\n"
                            "2. Hoàn tất mã xác thực 2FA hoặc CAPTCHA (nếu có).\n"
                            "3. Khi đã vào được trang chủ (Bảng tin) Facebook, nhấn 'OK' tại đây để lưu phiên đăng nhập.\n\n"
                            "Nhấn 'Cancel' nếu muốn hủy bỏ.",
                        )
                        result[0] = bool(ans)
                        ev.set()

                    self.after(0, _ask)
                    while not ev.is_set():
                        if self.stop_event.is_set():
                            return False
                        ev.wait(timeout=0.3)
                    return result[0]

                success = bot.login_interactive(
                    auto_fill=True,
                    on_ready_prompt=_gui_prompt,
                    stop_event=self.stop_event,
                )
                if success:
                    self.logger.info("🎉 [GUI] Phiên đăng nhập Facebook đã được lưu thành công vào profile.")
                    self.after(0, lambda: messagebox.showinfo("Thành Công", "Đăng nhập Facebook thành công! Phiên đã được lưu vào profile."))
                elif self.stop_event.is_set():
                    self.logger.warning("🛑 [GUI] Đã hủy phiên đăng nhập Facebook theo yêu cầu dừng khẩn cấp.")
                else:
                    self.logger.warning("⚠️ [GUI] Chưa thể xác thực phiên đăng nhập Facebook.")
                    self.after(0, lambda: messagebox.showwarning("Chưa Đăng Nhập", "Chưa phát hiện phiên đăng nhập hợp lệ. Vui lòng thử lại."))
            except Exception as e:
                self.logger.error("❌ Lỗi trong phiên đăng nhập: %s", e)
                self.after(0, lambda err=e: messagebox.showerror("Lỗi", f"Lỗi đăng nhập: {err}"))
            finally:
                self.after(0, self._restore_ui_after_login)

        t = threading.Thread(target=_task, daemon=True, name="LoginWorkerThread")
        self.worker_thread = t  # type: ignore
        t.start()

    def _restore_ui_after_login(self):
        """Restores UI controls after interactive login completes."""
        self.btn_start.configure(state="normal")
        self.btn_login.configure(state="normal")
        self.btn_logout.configure(state="normal")
        self.btn_dryrun.configure(state="normal")
        self.btn_canary.configure(state="normal")
        self._set_scan_buttons_state("normal")
        self.btn_stop.configure(state="disabled")
        if self.stop_event.is_set():
            self.card_status.val_label.config(text="🛑 ĐÃ DỪNG")  # type: ignore
        else:
            self.card_status.val_label.config(text="🟢 IDLE")  # type: ignore
        self.worker_thread = None

    def _start_logout_flow(self):
        """Purges Facebook session out of Chrome profile to allow switching accounts."""
        is_busy = (self.worker_thread and self.worker_thread.is_alive()) or (
            hasattr(self, "dispatcher") and self.dispatcher.is_running
        )
        if is_busy:
            messagebox.showwarning("Đang Chạy", "Bot đang thực thi các tác vụ. Vui lòng dừng các tác vụ trước khi đăng xuất.")
            return

        ans = messagebox.askyesno(
            "🚪 Xác Nhận Đăng Xuất",
            "Bạn có chắc muốn đăng xuất tài khoản Facebook hiện tại bằng cách xóa session khỏi Chrome?\n\n"
            "Thao tác này sẽ xóa sạch cookies và phiên lưu trong Chrome profile, giúp bạn sẵn sàng nhập và đăng nhập tài khoản khác.",
        )
        if not ans:
            return

        try:
            bot = FacebookBot(config=self.config, custom_logger=self.logger)
            bot.logout()
            self.card_status.val_label.config(text="🚪 ĐÃ ĐĂNG XUẤT")  # type: ignore
            self.logger.info("🎉 [GUI] Đã đăng xuất Facebook thành công. Session Chrome đã được xóa sạch.")
            messagebox.showinfo(
                "Đã Đăng Xuất",
                "Đã đăng xuất Facebook thành công!\nPhiên lưu (session & cookies) trong Chrome đã được dọn sạch.\n\n"
                "Bạn có thể vào tab 'Cấu Hình Hệ Thống' để đổi Email/Mật khẩu của tài khoản mới rồi nhấn Đăng Nhập lại.",
            )
        except Exception as e:
            self.logger.error("❌ Lỗi khi đăng xuất Facebook: %s", e)
            messagebox.showerror("Lỗi Đăng Xuất", f"Không thể xóa session Chrome: {e}")

    def _launch_worker(
        self,
        dry_run: bool,
        check_dom: bool,
        batch_size: Optional[int] = None,
        join_mode: bool = False,
        custom_targets: Optional[List[str]] = None,
        join_delay_min: Optional[float] = None,
        join_delay_max: Optional[float] = None,
        override_cooldown: bool = False,
    ):
        """Spawns or dispatches the background worker task via JobDispatcher."""
        try:
            if hasattr(self, "cfg_entries") and "EMAIL" in self.cfg_entries and "PASSWORD" in self.cfg_entries:
                new_email = self.cfg_entries["EMAIL"].get().strip()
                new_pwd = self.cfg_entries["PASSWORD"].get().strip()
                if new_email and new_pwd and (new_email != self.config.email or new_pwd != self.config.password):
                    self._save_env_config()
            self._load_config_state()
            self.config = BotConfig.from_env(str(self.env_file))
        except Exception as err:
            messagebox.showerror("Lỗi Cấu Hình", f"Không thể khởi động bot vì cấu hình lỗi: {err}")
            return

        effective_batch = batch_size or int(self.spn_batch.get() or self.config.max_posts_per_run)

        # UI State updates
        self.stop_event.clear()
        self.btn_login.configure(state="disabled")
        self.btn_logout.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        if join_mode:
            mode_str = "🤝 JOIN NHÓM"
            job_type = "JOIN_GROUPS"
            total_steps = len(custom_targets) if custom_targets else effective_batch
            job_name = f"Tham gia nhóm ({total_steps} nhóm)"
        elif dry_run:
            mode_str = "🔍 DRY-RUN"
            job_type = "DRY_RUN"
            total_steps = effective_batch
            job_name = f"Chạy thử Dry-run ({effective_batch} bài)"
        elif check_dom:
            mode_str = "🩺 CHECK DOM"
            job_type = "DOM_CHECK"
            total_steps = 1
            job_name = "Kiểm tra cấu trúc DOM"
        else:
            mode_str = "🚀 ĐĂNG BÀI"
            job_type = "POST"
            target_count = len(custom_targets) if custom_targets else effective_batch
            total_steps = target_count
            job_name = f"Đăng bài viết ({target_count} nhóm)"

        if not (hasattr(self, "dispatcher") and self.dispatcher.is_running):
            self.card_status.val_label.config(text=f"🟡 RUNNING ({mode_str})")  # type: ignore

        if self.telegram:
            self.telegram.set_busy(True)

        self._sync_active_draft_from_controls()
        active_draft = self.posts[self.active_draft_idx] if (hasattr(self, "posts") and self.posts and 0 <= self.active_draft_idx < len(self.posts)) else None
        gui_images = active_draft.images if active_draft else (list(self.lst_images.get(0, tk.END)) if hasattr(self, "lst_images") else None)
        post_content = active_draft.content if active_draft else (self.txt_content.get("1.0", "end-1c") if hasattr(self, "txt_content") else None)

        live_links = custom_targets if custom_targets is not None else [
            self._get_group_url_from_item(item) for item in self.tree_groups.get_children() if self._get_group_url_from_item(item)
        ]

        posting_tasks = None
        if not join_mode and hasattr(self, "posts") and self.posts:
            plan = build_round_robin_plan(
                posts=self.posts,
                target_groups=live_links,
                state_manager=StateManager(str(self.history_file)),
                max_posts_per_run=effective_batch,
                cooldown_hours=0 if override_cooldown else self.config.cooldown_hours,
            )
            posting_tasks = plan.tasks

        import dataclasses
        headless_val = self.var_headless.get() if hasattr(self, "var_headless") else getattr(self.config, "headless", False)
        worker_config = dataclasses.replace(
            self.config,
            cooldown_hours=0 if override_cooldown else self.config.cooldown_hours,
            headless=headless_val,
        )

        def _dispatcher_task(job: JobItem):
            self.after(0, lambda: self.card_status.val_label.config(text=f"🟡 RUNNING ({mode_str})"))  # type: ignore

            def _on_event(event_type: str, data: Dict[str, Any]):
                if event_type == "post_result":
                    idx = data.get("index", 1)
                    tot = data.get("total", job.total_steps or 1)
                    target_url = data.get("link", "")
                    job.update_progress(idx, tot, details=f"Đã xử lý: {target_url[:25]}...")
                    if hasattr(self, "dispatcher"):
                        self.dispatcher._notify_listeners(job)
                self._on_bot_event(event_type, data)

            def _on_completed(results: List[Dict[str, Any]]):
                self._on_bot_completed(results)

            worker = BotWorkerThread(
                config=worker_config,
                dry_run=dry_run,
                custom_batch_size=effective_batch,
                check_dom=check_dom,
                join_mode=join_mode,
                stop_event=job.stop_event,
                event_callback=_on_event,
                completion_callback=_on_completed,
                logger=self.logger,
                images=gui_images,
                target_links=live_links,
                post_content=post_content,
                posting_tasks=posting_tasks,
                join_delay_min=join_delay_min,
                join_delay_max=join_delay_max,
                headless=headless_val,
            )
            self.worker_thread = worker
            worker.run()

        if hasattr(self, "dispatcher"):
            job = self.dispatcher.submit_job(
                name=job_name,
                job_type=job_type,
                target_fn=_dispatcher_task,
                total_steps=total_steps,
            )
            self.logger.info("📋 Đã nạp tác vụ '%s' (#%s) vào Task Manager.", job_name, job.job_id)
        else:
            self.worker_thread = BotWorkerThread(
                config=worker_config,
                dry_run=dry_run,
                custom_batch_size=effective_batch,
                check_dom=check_dom,
                join_mode=join_mode,
                stop_event=self.stop_event,
                event_callback=self._on_bot_event,
                completion_callback=self._on_bot_completed,
                logger=self.logger,
                images=gui_images,
                target_links=live_links,
                post_content=post_content,
                posting_tasks=posting_tasks,
                join_delay_min=join_delay_min,
                join_delay_max=join_delay_max,
                headless=headless_val,
            )
            self.worker_thread.start()

    def _stop_bot_flow(self):
        """Signals stop request to the worker thread and all dispatcher tasks."""
        self.logger.warning("🛑 Người dùng nhấn DỪNG KHẨN CẤP trên Desktop GUI.")
        self.stop_event.set()
        if hasattr(self, "dispatcher"):
            self.dispatcher.cancel_all()
        self.btn_stop.configure(state="disabled")
        self.card_status.val_label.config(text="🛑 ĐANG DỪNG KHẨN CẤP...")  # type: ignore

    def _on_bot_event(self, event_type: str, data: Dict[str, Any]):
        """Callback handling granular bot events (called from worker thread)."""
        if event_type == "post_result":
            idx = data.get("index", 1)
            total = data.get("total", 1)
            status = data.get("status", "UNKNOWN")
            pct = int((idx / max(total, 1)) * 100)
            self.after(0, lambda: self._update_progress_ui(idx, total, pct))
            self.after(0, self._refresh_history_tab)

            # Dispatch to Telegram
            if self.telegram and self.telegram.is_configured:
                self.telegram.notify_post_result(
                    group_url=data.get("link", ""),
                    status=status,
                    details=data.get("details"),
                    screenshot_path=data.get("screenshot_path"),
                )

        elif event_type == "circuit_breaker":
            self.after(0, lambda: self.card_circuit.val_label.config(text="🚨 KÍCH HOẠT (TRIPPED)"))  # type: ignore
            if self.telegram and self.telegram.is_configured:
                self.telegram.notify_circuit_breaker(data.get("reason", "Triggered"))

        elif event_type == "login_action_required":
            self.after(0, lambda: self.card_status.val_label.config(text="🔑 CHỜ 2FA / CAPTCHA"))  # type: ignore

        elif event_type in ("session_summary", "join_session_summary"):
            if self.telegram and self.telegram.is_configured:
                self.telegram.notify_session_summary(
                    data.get("results", []),
                    duration_str=data.get("duration_formatted")
                )

    def _update_progress_ui(self, current: int, total: int, pct: int):
        """Thread-safe progress updates."""
        self.progress_bar["value"] = pct
        self.card_progress.val_label.config(text=f"{current} / {total} ({pct}%)")  # type: ignore

    def _on_bot_completed(self, results: List[Dict[str, Any]]):
        """Callback when worker thread completes."""
        self.after(0, lambda: self._reset_ui_after_run(results))

    def _reset_ui_after_run(self, results: List[Dict[str, Any]]):
        """Restores controls and updates metrics."""
        is_dispatcher_busy = hasattr(self, "dispatcher") and self.dispatcher.is_running
        if not is_dispatcher_busy:
            self.btn_start.configure(state="normal")
            self.btn_login.configure(state="normal")
            self.btn_logout.configure(state="normal")
            self.btn_dryrun.configure(state="normal")
            self.btn_canary.configure(state="normal")
            self._set_scan_buttons_state("normal")
            self.btn_stop.configure(state="disabled")

            if self.stop_event.is_set():
                self.card_status.val_label.config(text="🛑 ĐÃ DỪNG")  # type: ignore
            else:
                self.card_status.val_label.config(text="🟢 IDLE")  # type: ignore
        else:
            self.btn_stop.configure(state="normal")
            self.card_status.val_label.config(text="🟡 ĐANG CHẠY HÀNG ĐỢI...")  # type: ignore

        dur_str = getattr(self.worker_thread, "duration_str", "—") if self.worker_thread else "—"

        if self.stop_event.is_set():
            self.card_status.val_label.config(text="🛑 ĐÃ DỪNG")  # type: ignore
        else:
            self.card_status.val_label.config(text="🟢 IDLE")  # type: ignore
        self._refresh_kpi_cards()
        self._load_groups_and_posts()  # Refresh cooldown statuses in Tab 3
        self._refresh_history_tab()  # Refresh Recent Results Tab

        if self.telegram:
            self.telegram.set_busy(False)

        # Count results
        success_cnt = sum(1 for r in results if r.get("status") in ("SUCCESS", "JOINED", "ALREADY_JOINED", "JOINED_SUCCESS"))
        pending_cnt = sum(1 for r in results if r.get("status") in ("PENDING_APPROVAL", "REQUESTED", "JOIN_REQUEST_SENT"))
        failed_cnt = len(results) - success_cnt - pending_cnt

        self.logger.info(
            "🏁 Phiên làm việc đã kết thúc. Thời gian hoàn thành: %s. Tổng kết quả: %d mục (Thành công: %d, Chờ duyệt: %d, Thất bại: %d).",
            dur_str, len(results), success_cnt, pending_cnt, max(0, failed_cnt)
        )

        is_dom_check = len(results) == 1 and results[0].get("status") == "CHECK_DOM"
        if results and not is_dom_check:
            if self.stop_event.is_set():
                messagebox.showwarning(
                    "Phiên Đã Dừng",
                    f"🛑 Đã dừng phiên làm việc theo yêu cầu khẩn cấp.\n\n"
                    f"⏱ Thời gian thực hiện: {dur_str}\n"
                    f"• Đã xử lý: {len(results)} nhóm (Thành công: {success_cnt}, Chờ duyệt: {pending_cnt}, Thất bại: {max(0, failed_cnt)})\n\n"
                    f"👉 Chi tiết đã được cập nhật tại Tab '📋 Tổng Kết Theo Lần Đăng'."
                )
            else:
                messagebox.showinfo(
                    "Hoàn Thành Công Việc",
                    f"🎉 ĐÃ HOÀN THÀNH CÔNG VIỆC!\n\n"
                    f"⏱ Thời gian hoàn thành: {dur_str}\n\n"
                    f"📊 Kết quả phiên làm việc:\n"
                    f"• Tổng số nhóm đã xử lý: {len(results)}\n"
                    f"• ✅ Thành công: {success_cnt}\n"
                    f"• ⏳ Chờ duyệt: {pending_cnt}\n"
                    f"• ❌ Thất bại: {max(0, failed_cnt)}\n\n"
                    f"👉 Chi tiết từng nhóm đã được cập nhật tại Tab '📋 Tổng Kết Theo Lần Đăng'."
                )

    # =========================================================================
    # TELEGRAM DISPATCH INTERFACE
    # =========================================================================

    def _start_from_telegram(self, dry_run: bool, batch_size: Optional[int]):
        """Triggered when Telegram receives /start or /dryrun."""
        self.after(0, lambda: self._launch_worker(dry_run=dry_run, check_dom=False, batch_size=batch_size))

    def _stop_from_telegram(self):
        """Triggered when Telegram receives /stop."""
        self.after(0, self._stop_bot_flow)

    def _get_telegram_status(self) -> Dict[str, Any]:
        """Provides status summary for Telegram /status."""
        state_mgr = StateManager(str(self.history_file))
        return state_mgr.get_summary(self.config.cooldown_hours)

    # =========================================================================
    # BACKGROUND EXECUTION & SYSTEM TRAY
    # =========================================================================

    def _hide_to_background(self):
        """Hides the main window to run silently in the background."""
        self.withdraw()
        self.logger.info("🕶️ Đã ẩn giao diện Facebook Bot xuống nền. Các tác vụ vẫn tiếp tục chạy.")
        self._setup_tray_icon()

    def _restore_from_background(self, *args):
        """Restores the main window from background."""
        if hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.stop()
                self.tray_icon = None
            except Exception:
                pass
        self.deiconify()
        self.lift()
        self.focus_force()
        self.logger.info("🖥️ Đã khôi phục giao diện Facebook Bot từ chế độ chạy ngầm.")

    def _setup_tray_icon(self):
        """Sets up a system tray icon if pystray and PIL are available."""
        try:
            import pystray
            from PIL import Image, ImageDraw

            icon_img = None
            icon_path = self.repo_dir / "images" / "icon.png"
            if icon_path.exists():
                try:
                    icon_img = Image.open(str(icon_path))
                except Exception:
                    pass

            if icon_img is None:
                icon_img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
                draw = ImageDraw.Draw(icon_img)
                draw.ellipse((4, 4, 60, 60), fill=(24, 119, 242))
                draw.text((26, 16), "F", fill=(255, 255, 255))

            menu = pystray.Menu(
                pystray.MenuItem("🖥️ Mở Giao Diện", lambda icon, item: self.after(0, self._restore_from_background)),
                pystray.MenuItem(
                    "📊 Trạng Thái",
                    pystray.Menu(
                        pystray.MenuItem(
                            lambda text: "Đang Chạy" if ((hasattr(self, "dispatcher") and self.dispatcher.is_running) or (self.worker_thread and self.worker_thread.is_alive())) else "Đang Nghỉ",
                            None,
                            enabled=False,
                        )
                    ),
                ),
                pystray.MenuItem("🛑 Dừng Bot", lambda icon, item: self.after(0, self._stop_bot_flow)),
                pystray.MenuItem("🚪 Thoát Hoàn Toàn", lambda icon, item: self.after(0, self._force_exit)),
            )
            self.tray_icon = pystray.Icon("FacebookBot", icon_img, "Facebook Automation Bot", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True, name="SystemTrayThread").start()
        except Exception as e:
            self.logger.debug("System tray (pystray) không khả dụng: %s", e)
            self.tray_icon = None

    def _force_exit(self):
        """Force stops bot and closes application from tray."""
        self.stop_event.set()
        if hasattr(self, "dispatcher"):
            self.dispatcher.cancel_all()
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
        if hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.stop()
                self.tray_icon = None
            except Exception:
                pass
        self.destroy()
        sys.exit(0)

    def _on_closing(self):
        """Ensures safe teardown or prompt to minimize to background on window close."""
        is_busy = (hasattr(self, "dispatcher") and self.dispatcher.is_running) or (
            self.worker_thread and self.worker_thread.is_alive()
        )
        if is_busy:
            ans = messagebox.askyesnocancel(
                "Đóng Ứng Dụng",
                "Bot đang chạy các tác vụ nền!\n\n"
                "• Chọn [Yes]  : ẨN XUỐNG NỀN để bot tiếp tục chạy.\n"
                "• Chọn [No]   : DỪNG BOT và THOÁT hoàn toàn.\n"
                "• Chọn [Cancel]: Hủy thao tác (ở lại giao diện).",
            )
            if ans is None:
                # Cancel clicked: return to UI
                return
            elif ans is True:
                # Yes clicked: minimize to background
                self._hide_to_background()
                return

            # No clicked: stop bot and proceed to exit
            self.stop_event.set()
            if hasattr(self, "dispatcher"):
                self.dispatcher.cancel_all()
            if self.worker_thread and self.worker_thread.is_alive():
                self.worker_thread.join(timeout=2.0)

        if self.telegram:
            self.telegram.stop_polling()

        if hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

        if hasattr(self, "queue_handler"):
            logging.getLogger().removeHandler(self.queue_handler)

        self.destroy()


def run_gui(config_path: Optional[str] = None):
    """Entry point for Desktop GUI."""
    if not TKINTER_AVAILABLE:
        print("❌ Lỗi: Môi trường Python này chưa có thư viện Tkinter (_tkinter).")
        print("👉 Hướng dẫn: Khởi chạy với Python có Tkinter (ví dụ: python3.14 repo/app_ui.py hoặc cài python-tk).")
        sys.exit(1)
    app = FacebookBotGUI(config_path=config_path)
    app.mainloop()


if __name__ == "__main__":
    run_gui()
