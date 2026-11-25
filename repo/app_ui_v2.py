"""
Facebook Automation Bot - Enterprise Desktop GUI v2.0.

Fully reproduces the 12-screen Canva design specification from
Facebook_Automation_UI_UX_v1.docx:
- Top bar with workspace title, theme toggle pill and account status badge
- 3 Tab Navigation: 01 Cấu hình & Tài khoản | 02 Nội dung & Nhóm mục tiêu | 03 Vận hành & Giám sát
- Screen 01: Side-by-side Facebook Account & Post Timing cards + Session Summary banner
- Screen 02: Multi-Post cards list, Post Editor with character counter & Image cards grid
- Screen 03: Target Groups table with filter pills & Round-Robin allocation preview
- Screen 04: Pre-flight simulation view with 4 big stat cards & checklist
- Screen 05: Live monitor with real-time KPI numbers, next post countdown & live log console
- Screen 06: Graceful stop confirmation dialog with pause icon
- Full Light & Dark theme support.
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
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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

# Ensure repo directory is in sys.path
REPO_DIR = Path(__file__).resolve().parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config import BotConfig, get_config
from helpers.common_helpers import extract_facebook_group_urls, format_duration, load_from_file, load_group_targets, parse_group_header_info
from helpers.logger import setup_logger
from facebookbot import FacebookBot
from browser.driver_manager import get_driver
from services.state_manager import StateManager, normalize_url
from services.telegram_controller import TelegramController
from services.posting_plan import (
    PostItem,
    PostingPlan,
    PostingTask,
    build_round_robin_plan,
    load_posts_collection,
    save_posts_collection,
)
from ui_theme import THEMES, apply_theme_to_styles, get_theme


class QueueLogHandler(logging.Handler):
    """Thread-safe logging handler routing log records into a FIFO queue."""

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.log_queue.put((record.levelname, msg))
        except Exception:
            self.handleError(record)


class BotWorkerThreadV2(threading.Thread):
    """
    Dedicated worker thread executing posting plan, group joining, or maintenance tasks.
    Safely decoupled from UI loop to prevent freeze.
    """

    def __init__(
        self,
        config: BotConfig,
        plan: Optional[PostingPlan] = None,
        dry_run: bool = False,
        check_dom: bool = False,
        join_groups: bool = False,
        target_links: Optional[List[str]] = None,
        join_limit: Optional[int] = 10,
        default_answer: str = "Tôi đồng ý tuân thủ nội quy nhóm",
        on_login_prompt: Optional[Callable[[], bool]] = None,
        stop_event: Optional[threading.Event] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        completion_callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(name="BotWorkerThreadV2", daemon=True)
        self.config = config
        self.plan = plan
        self.dry_run = dry_run
        self.check_dom = check_dom
        self.join_groups = join_groups
        self.target_links = target_links
        self.join_limit = join_limit
        self.default_answer = default_answer
        self.on_login_prompt = on_login_prompt
        self.stop_event = stop_event or threading.Event()
        self.event_callback = event_callback
        self.completion_callback = completion_callback
        self.logger = logger or logging.getLogger("WorkerThreadV2")
        self.start_time: float = 0.0
        self.duration_sec: float = 0.0
        self.duration_str: str = "—"

    def run(self):
        self.start_time = time.time()
        self.logger.info("⚡ [Worker V2] Bắt đầu phiên làm việc...")
        results: List[Dict[str, Any]] = []
        try:
            if self.check_dom:
                from main import run_dom_check
                code = run_dom_check(self.config, self.logger, stop_event=self.stop_event)
                results = [{"status": "CHECK_DOM", "details": f"Exit code: {code}"}]
            elif self.join_groups:
                bot = FacebookBot(config=self.config, target_links=self.target_links, custom_logger=self.logger)
                results = bot.join_groups(
                    target_links=self.target_links,
                    dry_run=self.dry_run,
                    join_limit=self.join_limit or getattr(self.config, "join_limit", 10),
                    default_answer=self.default_answer,
                    delay_min=getattr(self.config, "join_delay_min", 15.0),
                    delay_max=getattr(self.config, "join_delay_max", 35.0),
                    stop_event=self.stop_event,
                    event_callback=self.event_callback,
                    on_login_prompt=self.on_login_prompt,
                )
            else:
                bot = FacebookBot(config=self.config, custom_logger=self.logger)
                tasks = self.plan.tasks if self.plan else None
                target_links = [t.group_url for t in tasks] if tasks else None
                bot.target_links = target_links

                results = bot.run(
                    dry_run=self.dry_run,
                    stop_event=self.stop_event,
                    event_callback=self.event_callback,
                    posting_tasks=tasks,
                )
        except Exception as err:
            self.logger.critical("🚨 Lỗi ngoại lệ trong Worker Thread: %s", err, exc_info=True)
            results = [{"status": "CRITICAL_ERROR", "details": str(err)}]
        finally:
            end_time = time.time()
            self.duration_sec = max(0.0, end_time - self.start_time) if self.start_time > 0 else 0.0
            self.duration_str = format_duration(self.duration_sec)
            self.logger.info("⚡ [Worker V2] Phiên làm việc đã kết thúc trong %s.", self.duration_str)
            if self.completion_callback:
                self.completion_callback(results)


class FacebookBotGUIV2(tk.Tk if tk else object):
    """
    Enterprise Desktop GUI v2.0 matching Canva 12-screen specification.
    """

    def __init__(self, config_path: Optional[str] = None, default_theme: str = "light"):
        if not TKINTER_AVAILABLE:
            raise RuntimeError("Tkinter is not available in the current Python environment.")
        super().__init__()
        self.title("Facebook Automation - Không gian làm việc")
        self.geometry("1440x920")
        self.minsize(1080, 720)

        self.repo_dir = REPO_DIR
        self.env_file = Path(config_path).resolve() if config_path else (self.repo_dir / ".env")
        self.theme_name = default_theme
        self.t = get_theme(self.theme_name)

        # Threading & Control
        self.log_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[BotWorkerThreadV2] = None
        self.auto_scroll_logs = True

        # State tracking
        self.current_main_tab = 0  # 0: Config, 1: Content, 2: Operations
        self.current_sub_tab = 0   # 0: Posts, 1: Groups (for Tab 1)
        self.is_running_session = False
        self.active_session_type = "post"
        self.dom_check_status = "Chưa chạy"

        # Logging Setup
        self.logger = setup_logger("FacebookBotGUIV2", str(self.repo_dir / "logs" / "bot.log"))
        self.queue_handler = QueueLogHandler(self.log_queue)
        self.queue_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)-8s] %(message)s", datefmt="%H:%M:%S")
        )
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self.queue_handler)

        # File paths
        self._load_config_state()
        self.links_file = self.config.json_dir / "links.json"
        self.posts_file = self.config.json_dir / "posts.json"
        self.history_file = self.config.json_dir / "history.json"
        self.groups_meta_file = self.config.json_dir / "groups_meta.json"

        # Data collections
        self.posts: List[PostItem] = []
        self.active_post_id: Optional[str] = None
        self.target_links: List[str] = []
        self.groups_meta: Dict[str, Dict[str, Any]] = {}
        self.current_plan: Optional[PostingPlan] = None

        # Metrics for Tab 3
        self.metric_processed = 0
        self.metric_success = 0
        self.metric_pending = 0
        self.metric_remaining = 0
        self.metric_failed = 0
        self.metric_total = 0

        # Telegram integration
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

        # Styles & Layout
        self.style = ttk.Style(self)
        self._build_app_shell()
        self._load_all_data()

        # Window closing and log polling
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.after(100, self._process_log_queue)

    def _load_config_state(self):
        try:
            self.config = BotConfig.from_env(str(self.env_file), validate_credentials=False)
        except Exception as err:
            self.logger.warning("Không thể nạp hoàn chỉnh .env: %s", err)
            self.config = BotConfig(
                email="demo@example.com",
                password="password",
                base_dir=self.repo_dir,
            )

    # =========================================================================
    # APP SHELL (TOP BAR, TAB NAVIGATION & CONTAINER)
    # =========================================================================

    def _build_app_shell(self):
        """Constructs Canva-style top bar, tab bar and main content area."""
        self.configure(bg=self.t["bg"])

        # 1. Top Bar
        self.top_frame = tk.Frame(self, bg=self.t["card_bg"], height=64, padx=24, pady=12)
        self.top_frame.pack(fill="x")

        # Brand on left
        brand_frame = tk.Frame(self.top_frame, bg=self.t["card_bg"])
        brand_frame.pack(side="left")

        # [F] Icon
        icon_f = tk.Label(
            brand_frame,
            text="F",
            bg=self.t["accent"],
            fg="#FFFFFF",
            font=("SF Pro", 14, "bold"),
            width=2,
            height=1,
            relief="flat",
        )
        icon_f.pack(side="left", padx=(0, 10))

        brand_text_box = tk.Frame(brand_frame, bg=self.t["card_bg"])
        brand_text_box.pack(side="left")
        self.lbl_brand_title = tk.Label(
            brand_text_box,
            text="Facebook Automation",
            font=("SF Pro", 13, "bold"),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
        )
        self.lbl_brand_title.pack(anchor="w")
        self.lbl_brand_sub = tk.Label(
            brand_text_box,
            text="Không gian làm việc",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        )
        self.lbl_brand_sub.pack(anchor="w")

        # Right badges
        badge_frame = tk.Frame(self.top_frame, bg=self.t["card_bg"])
        badge_frame.pack(side="right")

        # Account pill
        self.pill_account = tk.Label(
            badge_frame,
            text=f"● {self.config.email or 'demo@example.com'}",
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
            font=("SF Pro", 9),
            padx=10,
            pady=4,
        )
        self.pill_account.pack(side="right", padx=(10, 0))

        # Theme toggle pill
        theme_btn_text = "GIAO DIỆN SÁNG" if self.theme_name == "light" else "GIAO DIỆN TỐI"
        self.btn_theme_pill = tk.Button(
            badge_frame,
            text=theme_btn_text,
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
            font=("SF Pro", 8, "bold"),
            relief="flat",
            bd=0,
            padx=12,
            pady=4,
            cursor="hand2",
            command=self._toggle_theme,
        )
        self.btn_theme_pill.pack(side="right")

        # 2. Tab Navigation Bar (Canva style with 01, 02, 03)
        self.tab_nav_frame = tk.Frame(self, bg=self.t["card_bg"], padx=24, pady=0)
        self.tab_nav_frame.pack(fill="x")

        # Separator line under top bar
        sep = tk.Frame(self.tab_nav_frame, bg=self.t["border_light"], height=1)
        sep.pack(fill="x", side="bottom")

        self.tab_buttons: List[tk.Label] = []
        tab_names = [
            "01    Cấu hình & Tài khoản",
            "02    Nội dung & Nhóm mục tiêu",
            "03    Vận hành & Giám sát",
        ]

        for idx, name in enumerate(tab_names):
            lbl = tk.Label(
                self.tab_nav_frame,
                text=name,
                font=("SF Pro", 10, "bold"),
                bg=self.t["card_bg"],
                fg=self.t["text_secondary"],
                padx=20,
                pady=14,
                cursor="hand2",
            )
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, i=idx: self._switch_main_tab(i))
            self.tab_buttons.append(lbl)

        # 3. Main Body Container
        self.body_container = tk.Frame(self, bg=self.t["bg"], padx=24, pady=16)
        self.body_container.pack(fill="both", expand=True)

        # 3 Screens
        self.screen_config = tk.Frame(self.body_container, bg=self.t["bg"])
        self.screen_content = tk.Frame(self.body_container, bg=self.t["bg"])
        self.screen_ops = tk.Frame(self.body_container, bg=self.t["bg"])

        self._build_screen_01_config()
        self._build_screen_02_and_03_content()
        self._build_screen_04_and_05_ops()

        self._switch_main_tab(0)

    @property
    def notebook(self):
        """Compatibility adapter for tests expecting a notebook interface."""
        outer = self
        class _NotebookAdapter:
            def index(self, which):
                return len(outer.tab_buttons)
            def tab(self, idx, option=None):
                if 0 <= idx < len(outer.tab_buttons):
                    text = outer.tab_buttons[idx].cget("text")
                    return {"text": text} if option is None else text
                return {}
            def select(self, idx):
                outer._switch_main_tab(idx)
        return _NotebookAdapter()

    def _switch_main_tab(self, tab_idx: int):
        """Switches active main tab and applies Canva active highlight styling."""
        self.current_main_tab = tab_idx

        for idx, lbl in enumerate(self.tab_buttons):
            if idx == tab_idx:
                lbl.configure(
                    bg=self.t["accent_soft"],
                    fg=self.t["accent_soft_text"],
                    relief="flat",
                )
            else:
                lbl.configure(
                    bg=self.t["card_bg"],
                    fg=self.t["text_secondary"],
                    relief="flat",
                )

        self.screen_config.pack_forget()
        self.screen_content.pack_forget()
        self.screen_ops.pack_forget()

        if tab_idx == 0:
            self.screen_config.pack(fill="both", expand=True)
        elif tab_idx == 1:
            self.screen_content.pack(fill="both", expand=True)
            self._render_content_subview()
        elif tab_idx == 2:
            self.screen_ops.pack(fill="both", expand=True)
            self._render_ops_subview()

    def _toggle_theme(self):
        """Switches between Light and Dark mode."""
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.t = get_theme(self.theme_name)
        self.btn_theme_pill.configure(
            text="GIAO DIỆN SÁNG" if self.theme_name == "light" else "GIAO DIỆN TỐI",
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
        )
        self.configure(bg=self.t["bg"])
        self.top_frame.configure(bg=self.t["card_bg"])
        self.tab_nav_frame.configure(bg=self.t["card_bg"])
        self.body_container.configure(bg=self.t["bg"])
        self.screen_config.configure(bg=self.t["bg"])
        self.screen_content.configure(bg=self.t["bg"])
        self.screen_ops.configure(bg=self.t["bg"])
        self._switch_main_tab(self.current_main_tab)
        self.logger.info("Đã chuyển theme sang: %s", self.theme_name.upper())

    # =========================================================================
    # SCREEN 01: CẤU HÌNH & TÀI KHOẢN (IMAGE 1)
    # =========================================================================

    def _build_screen_01_config(self):
        """Constructs Screen 01 according to Canva Image 1."""
        container = self.screen_config

        # Heading
        head_frame = tk.Frame(container, bg=self.t["bg"])
        head_frame.pack(fill="x", pady=(0, 16))
        tk.Label(
            head_frame,
            text="Cấu hình & Tài khoản",
            font=("SF Pro", 20, "bold"),
            bg=self.t["bg"],
            fg=self.t["text_primary"],
        ).pack(anchor="w")
        tk.Label(
            head_frame,
            text="Thiết lập tài khoản và giới hạn trước khi chuẩn bị nội dung.",
            font=("SF Pro", 10),
            bg=self.t["bg"],
            fg=self.t["text_secondary"],
        ).pack(anchor="w", pady=(2, 0))

        # 2 White Cards Row
        cards_row = tk.Frame(container, bg=self.t["bg"])
        cards_row.pack(fill="both", expand=True, pady=(0, 14))

        # CARD 1: 01 Tài khoản Facebook (Left)
        card1 = tk.Frame(cards_row, bg=self.t["card_bg"], padx=20, pady=18, relief="solid", bd=1)
        card1.configure(highlightbackground=self.t["border"], highlightcolor=self.t["border"], highlightthickness=1)
        card1.pack(side="left", fill="both", expand=True, padx=(0, 10))

        tk.Label(
            card1,
            text="01  Tài khoản Facebook",
            font=("SF Pro", 12, "bold"),
            bg=self.t["card_bg"],
            fg=self.t["accent"],
        ).pack(anchor="w", pady=(0, 12))

        # Email
        tk.Label(card1, text="Email", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w")
        self.entry_email = tk.Entry(
            card1,
            bg=self.t["input_bg"],
            fg=self.t["text_primary"],
            font=("SF Pro", 10),
            relief="flat",
            bd=6,
        )
        self.entry_email.insert(0, self.config.email or "demo@example.com")
        self.entry_email.pack(fill="x", pady=(3, 10))

        # Mật khẩu
        pwd_head = tk.Frame(card1, bg=self.t["card_bg"])
        pwd_head.pack(fill="x")
        tk.Label(pwd_head, text="Mật khẩu", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(side="left")
        self.btn_show_pwd = tk.Button(
            pwd_head,
            text="Hiện",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["accent"],
            bd=0,
            cursor="hand2",
            command=self._toggle_pwd_visibility,
        )
        self.btn_show_pwd.pack(side="right")

        self.entry_pwd = tk.Entry(
            card1,
            show="*",
            bg=self.t["input_bg"],
            fg=self.t["text_primary"],
            font=("SF Pro", 10),
            relief="flat",
            bd=6,
        )
        self.entry_pwd.insert(0, self.config.password or "")
        self.entry_pwd.pack(fill="x", pady=(3, 14))

        # Login status pill & action button
        login_row = tk.Frame(card1, bg=self.t["card_bg"])
        login_row.pack(fill="x", pady=(0, 12))

        self.pill_login_status = tk.Label(
            login_row,
            text="Chưa kiểm tra đăng nhập",
            bg=self.t["warning_bg"],
            fg=self.t["warning"],
            font=("SF Pro", 8, "bold"),
            padx=10,
            pady=4,
        )
        self.pill_login_status.pack(side="left")

        btn_logout_chrome = tk.Button(
            login_row,
            text="🚪 Đăng xuất",
            font=("SF Pro", 8, "bold"),
            bg=self.t["danger_bg"],
            fg=self.t["danger"],
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            cursor="hand2",
            command=self._start_logout_flow,
        )
        btn_logout_chrome.pack(side="right", padx=(6, 0))

        btn_login_chrome = tk.Button(
            login_row,
            text="🔑 Mở Chrome đăng nhập",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._start_login_interactive,
        )
        btn_login_chrome.pack(side="right")

        tk.Frame(card1, bg=self.t["border_light"], height=1).pack(fill="x", pady=10)
        tk.Label(
            card1,
            text="Sử dụng một tài khoản cho mỗi phiên.",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        ).pack(anchor="w")

        # CARD 2: 02 Nhịp đăng & giới hạn (Right)
        card2 = tk.Frame(cards_row, bg=self.t["card_bg"], padx=20, pady=18, relief="solid", bd=1)
        card2.configure(highlightbackground=self.t["border"], highlightcolor=self.t["border"], highlightthickness=1)
        card2.pack(side="right", fill="both", expand=True, padx=(10, 0))

        tk.Label(
            card2,
            text="02  Nhịp đăng & giới hạn",
            font=("SF Pro", 12, "bold"),
            bg=self.t["card_bg"],
            fg=self.t["accent"],
        ).pack(anchor="w", pady=(0, 12))

        grid_inputs = tk.Frame(card2, bg=self.t["card_bg"])
        grid_inputs.pack(fill="x", pady=(0, 8))

        # Row 1: Chờ tối thiểu & Chờ tối đa
        c_min = tk.Frame(grid_inputs, bg=self.t["card_bg"])
        c_min.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=4)
        tk.Label(c_min, text="Chờ tối thiểu (giây)", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w")
        self.entry_min_delay = tk.Entry(c_min, bg=self.t["input_bg"], fg=self.t["text_primary"], font=("SF Pro", 10), relief="flat", bd=6)
        self.entry_min_delay.insert(0, str(self.config.min_delay_between_posts))
        self.entry_min_delay.pack(fill="x", pady=(2, 0))

        c_max = tk.Frame(grid_inputs, bg=self.t["card_bg"])
        c_max.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4)
        tk.Label(c_max, text="Chờ tối đa (giây)", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w")
        self.entry_max_delay = tk.Entry(c_max, bg=self.t["input_bg"], fg=self.t["text_primary"], font=("SF Pro", 10), relief="flat", bd=6)
        self.entry_max_delay.insert(0, str(self.config.max_delay_between_posts))
        self.entry_max_delay.pack(fill="x", pady=(2, 0))

        # Row 2: Số lượt đăng tối đa & Thời gian chờ nhóm
        c_limit = tk.Frame(grid_inputs, bg=self.t["card_bg"])
        c_limit.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=8)
        tk.Label(c_limit, text="Số lượt đăng tối đa / phiên", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w")
        self.entry_max_posts = tk.Entry(c_limit, bg=self.t["input_bg"], fg=self.t["text_primary"], font=("SF Pro", 10), relief="flat", bd=6)
        self.entry_max_posts.insert(0, str(self.config.max_posts_per_run))
        self.entry_max_posts.pack(fill="x", pady=(2, 0))

        c_cd = tk.Frame(grid_inputs, bg=self.t["card_bg"])
        c_cd.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=8)
        tk.Label(c_cd, text="Thời gian chờ của nhóm (giờ)", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w")
        self.entry_cooldown = tk.Entry(c_cd, bg=self.t["input_bg"], fg=self.t["text_primary"], font=("SF Pro", 10), relief="flat", bd=6)
        self.entry_cooldown.insert(0, str(self.config.cooldown_hours))
        self.entry_cooldown.pack(fill="x", pady=(2, 0))

        grid_inputs.columnconfigure(0, weight=1)
        grid_inputs.columnconfigure(1, weight=1)

        tk.Label(
            card2,
            text="Thời gian tối đa phải lớn hơn hoặc bằng tối thiểu.",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        ).pack(anchor="w", pady=(0, 8))

        tk.Frame(card2, bg=self.t["border_light"], height=1).pack(fill="x", pady=6)

        # Advanced Settings row
        adv_row = tk.Frame(card2, bg=self.t["card_bg"])
        adv_row.pack(fill="x")
        tk.Label(adv_row, text="Cài đặt nâng cao", font=("SF Pro", 9, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(side="left")
        tk.Label(
            adv_row,
            text=f"Timeout {self.config.timeout}s · Cổng trình duyệt {self.config.debug_port}",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        ).pack(side="right")

        # SUMMARY BANNER (Image 1 Bottom Card)
        sum_card = tk.Frame(container, bg=self.t["accent_soft"], padx=20, pady=14, relief="flat")
        sum_card.pack(fill="x", pady=(0, 14))

        tk.Label(
            sum_card,
            text="TÓM TẮT PHIÊN",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["text_secondary"],
        ).pack(anchor="w")

        sum_metrics = tk.Frame(sum_card, bg=self.t["accent_soft"])
        sum_metrics.pack(fill="x", pady=(4, 0))

        self.lbl_sum_delay = tk.Label(sum_metrics, text="45–90 giây giữa các lượt", font=("SF Pro", 11, "bold"), bg=self.t["accent_soft"], fg=self.t["text_primary"])
        self.lbl_sum_delay.pack(side="left", padx=(0, 40))

        self.lbl_sum_limit = tk.Label(sum_metrics, text="Tối đa 10 lượt / phiên", font=("SF Pro", 11, "bold"), bg=self.t["accent_soft"], fg=self.t["text_primary"])
        self.lbl_sum_limit.pack(side="left", padx=(0, 40))

        self.lbl_sum_cd = tk.Label(sum_metrics, text="Chờ lại sau 24 giờ", font=("SF Pro", 11, "bold"), bg=self.t["accent_soft"], fg=self.t["text_primary"])
        self.lbl_sum_cd.pack(side="left")

        # FOOTER ACTION BAR
        foot_bar = tk.Frame(container, bg=self.t["bg"])
        foot_bar.pack(fill="x", side="bottom")

        btn_reload = tk.Button(
            foot_bar,
            text="Nạp cấu hình",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="solid",
            bd=1,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._reload_env_config,
        )
        btn_reload.pack(side="left", padx=(0, 8))

        btn_save = tk.Button(
            foot_bar,
            text="Lưu cấu hình",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="solid",
            bd=1,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._save_env_config,
        )
        btn_save.pack(side="left")

        btn_next = tk.Button(
            foot_bar,
            text="Tiếp tục: Nội dung & Nhóm  ➔",
            font=("SF Pro", 9, "bold"),
            bg=self.t["accent"],
            fg="#FFFFFF",
            relief="flat",
            bd=0,
            padx=18,
            pady=7,
            cursor="hand2",
            command=lambda: self._switch_main_tab(1),
        )
        btn_next.pack(side="right")

    def _toggle_pwd_visibility(self):
        if self.entry_pwd.cget("show") == "*":
            self.entry_pwd.configure(show="")
            self.btn_show_pwd.configure(text="Ẩn")
        else:
            self.entry_pwd.configure(show="*")
            self.btn_show_pwd.configure(text="Hiện")

    # =========================================================================
    # SCREEN 02 & 03: NỘI DUNG & NHÓM MỤC TIÊU (IMAGE 2 & 3)
    # =========================================================================

    def _build_screen_02_and_03_content(self):
        """Constructs Screen 02 and 03 with Sub-Tab switcher."""
        container = self.screen_content

        # Heading & Subtitle
        head_frame = tk.Frame(container, bg=self.t["bg"])
        head_frame.pack(fill="x", pady=(0, 10))

        tk.Label(
            head_frame,
            text="Nội dung & Nhóm mục tiêu",
            font=("SF Pro", 20, "bold"),
            bg=self.t["bg"],
            fg=self.t["text_primary"],
        ).pack(anchor="w")

        self.lbl_content_subtitle = tk.Label(
            head_frame,
            text="Tạo nhiều bài viết, thêm ảnh và chọn những bài sẽ dùng trong phiên.",
            font=("SF Pro", 10),
            bg=self.t["bg"],
            fg=self.t["text_secondary"],
        )
        self.lbl_content_subtitle.pack(anchor="w", pady=(2, 0))

        # Sub-tab Switcher Bar (Pill buttons + badges)
        subtab_bar = tk.Frame(container, bg=self.t["bg"])
        subtab_bar.pack(fill="x", pady=(0, 12))

        # Switcher pills
        switcher_box = tk.Frame(subtab_bar, bg=self.t["border_light"], padx=3, pady=3)
        switcher_box.pack(side="left")

        self.btn_subtab_posts = tk.Button(
            switcher_box,
            text="Bài viết · 0",
            font=("SF Pro", 9, "bold"),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="flat",
            bd=0,
            padx=14,
            pady=4,
            cursor="hand2",
            command=lambda: self._switch_content_subtab(0),
        )
        self.btn_subtab_posts.pack(side="left")

        self.btn_subtab_groups = tk.Button(
            switcher_box,
            text="Nhóm mục tiêu · 0",
            font=("SF Pro", 9),
            bg=self.t["border_light"],
            fg=self.t["text_secondary"],
            relief="flat",
            bd=0,
            padx=14,
            pady=4,
            cursor="hand2",
            command=lambda: self._switch_content_subtab(1),
        )
        self.btn_subtab_groups.pack(side="left")

        # Right Summary Pills (Image 2)
        pills_right = tk.Frame(subtab_bar, bg=self.t["bg"])
        pills_right.pack(side="right")

        self.pill_selected_posts = tk.Label(
            pills_right,
            text="0 bài đã chọn",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
            padx=10,
            pady=3,
        )
        self.pill_selected_posts.pack(side="left", padx=4)

        self.pill_total_images = tk.Label(
            pills_right,
            text="0 ảnh đính kèm",
            font=("SF Pro", 8, "bold"),
            bg=self.t["success_bg"],
            fg=self.t["success"],
            padx=10,
            pady=3,
        )
        self.pill_total_images.pack(side="left", padx=4)

        # Body Subviews Container
        self.subview_container = tk.Frame(container, bg=self.t["bg"])
        self.subview_container.pack(fill="both", expand=True)

        self.view_posts = tk.Frame(self.subview_container, bg=self.t["bg"])
        self.view_groups = tk.Frame(self.subview_container, bg=self.t["bg"])

        self._build_view_posts(self.view_posts)
        self._build_view_groups(self.view_groups)

        self._switch_content_subtab(0)

    def _switch_content_subtab(self, sub_idx: int):
        self.current_sub_tab = sub_idx
        if sub_idx == 0:
            self.btn_subtab_posts.configure(bg=self.t["card_bg"], fg=self.t["text_primary"], font=("SF Pro", 9, "bold"))
            self.btn_subtab_groups.configure(bg=self.t["border_light"], fg=self.t["text_secondary"], font=("SF Pro", 9))
            self.lbl_content_subtitle.configure(text="Tạo nhiều bài viết, thêm ảnh và chọn những bài sẽ dùng trong phiên.")
            self.view_groups.pack_forget()
            self.view_posts.pack(fill="both", expand=True)
        else:
            self.btn_subtab_posts.configure(bg=self.t["border_light"], fg=self.t["text_secondary"], font=("SF Pro", 9))
            self.btn_subtab_groups.configure(bg=self.t["card_bg"], fg=self.t["text_primary"], font=("SF Pro", 9, "bold"))
            self.lbl_content_subtitle.configure(text="Chọn nhóm nhận bài và kiểm tra thứ tự đăng của các bài đã chọn.")
            self.view_posts.pack_forget()
            self.view_groups.pack(fill="both", expand=True)

    def _render_content_subview(self):
        self._refresh_posts_tree()
        self._refresh_groups_tree()
        self._refresh_round_robin_plan()

    # --- VIEW 1: BÀI VIẾT (IMAGE 2) ---
    def _build_view_posts(self, parent: tk.Frame):
        """Builds Image 2 (Danh sách bài viết & chỉnh sửa ảnh)."""
        row = tk.Frame(parent, bg=self.t["bg"])
        row.pack(fill="both", expand=True, pady=(0, 10))

        # Left Column: Danh sách bài
        left_col = tk.Frame(row, bg=self.t["card_bg"], width=380, padx=16, pady=16, relief="solid", bd=1)
        left_col.configure(highlightbackground=self.t["border"], highlightthickness=1)
        left_col.pack(side="left", fill="both", padx=(0, 10))
        left_col.pack_propagate(False)

        col1_head = tk.Frame(left_col, bg=self.t["card_bg"])
        col1_head.pack(fill="x", pady=(0, 10))
        tk.Label(col1_head, text="Danh sách bài", font=("SF Pro", 12, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(side="left")

        btn_add = tk.Button(
            col1_head,
            text="+ Thêm bài",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent"],
            fg="#FFFFFF",
            relief="flat",
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._add_post,
        )
        btn_add.pack(side="right")

        # Search bar
        self.entry_search_posts = tk.Entry(
            left_col,
            bg=self.t["input_bg"],
            fg=self.t["text_primary"],
            font=("SF Pro", 9),
            relief="flat",
            bd=5,
        )
        self.entry_search_posts.insert(0, "Tìm bài viết...")
        self.entry_search_posts.pack(fill="x", pady=(0, 10))

        # Posts Listbox / Treeview
        tree_frame = tk.Frame(left_col, bg=self.t["card_bg"])
        tree_frame.pack(fill="both", expand=True)

        self.tree_posts = ttk.Treeview(
            tree_frame,
            columns=("sel", "id", "name", "images", "status"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        self.tree_posts.heading("sel", text="✓")
        self.tree_posts.heading("id", text="#")
        self.tree_posts.heading("name", text="Tên Bài Viết")
        self.tree_posts.heading("images", text="Ảnh")
        self.tree_posts.heading("status", text="Trạng Thái")

        self.tree_posts.column("sel", width=35, anchor="center")
        self.tree_posts.column("id", width=30, anchor="center")
        self.tree_posts.column("name", width=180)
        self.tree_posts.column("images", width=55, anchor="center")
        self.tree_posts.column("status", width=75, anchor="center")
        self.tree_posts.pack(fill="both", expand=True)

        self.tree_posts.bind("<<TreeviewSelect>>", self._on_post_selected)
        self.tree_posts.bind("<space>", lambda e: self._toggle_post_selection())
        self.tree_posts.bind("<Double-1>", lambda e: self._toggle_post_selection())

        # Post list bottom info
        self.lbl_post_list_footer = tk.Label(
            left_col,
            text="Đã chọn 0 / 0 bài",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        )
        self.lbl_post_list_footer.pack(anchor="w", pady=(8, 0))

        # Right Column: Chỉnh sửa bài
        right_col = tk.Frame(row, bg=self.t["card_bg"], padx=20, pady=16, relief="solid", bd=1)
        right_col.configure(highlightbackground=self.t["border"], highlightthickness=1)
        right_col.pack(side="right", fill="both", expand=True, padx=(10, 0))

        edit_head = tk.Frame(right_col, bg=self.t["card_bg"])
        edit_head.pack(fill="x", pady=(0, 10))

        self.lbl_edit_title = tk.Label(
            edit_head,
            text="Chỉnh sửa bài 01",
            font=("SF Pro", 12, "bold"),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
        )
        self.lbl_edit_title.pack(side="left")

        btn_dup = tk.Button(
            edit_head,
            text="Nhân bản",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="solid",
            bd=1,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._duplicate_post,
        )
        btn_dup.pack(side="right", padx=(6, 0))

        btn_del = tk.Button(
            edit_head,
            text="Xóa bài",
            font=("SF Pro", 8, "bold"),
            bg=self.t["danger_bg"],
            fg=self.t["danger"],
            relief="flat",
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._delete_post,
        )
        btn_del.pack(side="right")

        # Tên bài
        tk.Label(right_col, text="Tên bài (nội bộ)", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w")
        self.entry_post_name = tk.Entry(right_col, bg=self.t["input_bg"], fg=self.t["text_primary"], font=("SF Pro", 10), relief="flat", bd=6)
        self.entry_post_name.pack(fill="x", pady=(2, 10))

        # Nội dung
        cnt_row = tk.Frame(right_col, bg=self.t["card_bg"])
        cnt_row.pack(fill="x")
        tk.Label(cnt_row, text="Nội dung", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(side="left")
        self.lbl_char_count = tk.Label(cnt_row, text="Ký tự: 0", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["text_secondary"])
        self.lbl_char_count.pack(side="right")

        self.txt_post_content = ScrolledText(right_col, wrap="word", height=6, font=("SF Pro", 10), relief="flat", bd=4)
        self.txt_post_content.pack(fill="both", expand=True, pady=(2, 10))
        self.txt_post_content.bind("<KeyRelease>", self._update_char_counter)

        # Hình ảnh đính kèm (Grid / Listbox per Image 2)
        img_head = tk.Frame(right_col, bg=self.t["card_bg"])
        img_head.pack(fill="x")
        self.lbl_img_section = tk.Label(img_head, text="Hình ảnh · 0", font=("SF Pro", 9, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"])
        self.lbl_img_section.pack(side="left")
        tk.Label(
            img_head,
            text="Kéo để đổi thứ tự · Ảnh đầu tiên là ảnh bìa",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        ).pack(side="right")

        # Image list & add button row
        img_row = tk.Frame(right_col, bg=self.t["card_bg"])
        img_row.pack(fill="x", pady=(4, 10))

        self.lst_images = tk.Listbox(img_row, height=3, font=("SF Pro", 9), relief="solid", bd=1)
        self.lst_images.pack(side="left", fill="both", expand=True)

        img_btn_col = tk.Frame(img_row, bg=self.t["card_bg"], padx=6)
        img_btn_col.pack(side="right")

        tk.Button(img_btn_col, text="+ Thêm ảnh", font=("SF Pro", 8, "bold"), bg=self.t["accent_soft"], fg=self.t["accent_soft_text"], bd=0, padx=8, pady=3, command=self._add_images).pack(fill="x", pady=2)
        tk.Button(img_btn_col, text="Xóa ảnh", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["danger"], bd=1, padx=8, pady=2, command=self._remove_image).pack(fill="x", pady=2)
        tk.Button(img_btn_col, text="⬆️ Lên", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["text_primary"], bd=1, padx=8, pady=2, command=lambda: self._move_image(-1)).pack(fill="x", pady=2)
        tk.Button(img_btn_col, text="⬇️ Xuống", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["text_primary"], bd=1, padx=8, pady=2, command=lambda: self._move_image(1)).pack(fill="x", pady=2)

        # Editor Footer: Save Button
        edit_foot = tk.Frame(right_col, bg=self.t["card_bg"])
        edit_foot.pack(fill="x")
        self.lbl_save_status = tk.Label(edit_foot, text="Đã lưu thay đổi", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["success"])
        self.lbl_save_status.pack(side="left")

        btn_save_post = tk.Button(
            edit_foot,
            text="Lưu bài viết",
            font=("SF Pro", 9, "bold"),
            bg=self.t["accent"],
            fg="#FFFFFF",
            relief="flat",
            bd=0,
            padx=16,
            pady=6,
            cursor="hand2",
            command=self._save_current_post,
        )
        btn_save_post.pack(side="right")

        # View 1 Footer
        v1_foot = tk.Frame(parent, bg=self.t["bg"])
        v1_foot.pack(fill="x", side="bottom")

        tk.Button(
            v1_foot,
            text="← Cấu hình",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="solid",
            bd=1,
            padx=14,
            pady=6,
            cursor="hand2",
            command=lambda: self._switch_main_tab(0),
        ).pack(side="left")

        tk.Button(
            v1_foot,
            text="Tiếp tục: Nhóm mục tiêu  ➔",
            font=("SF Pro", 9, "bold"),
            bg=self.t["accent"],
            fg="#FFFFFF",
            relief="flat",
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2",
            command=lambda: self._switch_content_subtab(1),
        ).pack(side="right")

    # --- VIEW 2: NHÓM MỤC TIÊU & PHÂN BỔ (IMAGE 3) ---
    def _build_view_groups(self, parent: tk.Frame):
        """Builds Image 3 (Nhóm mục tiêu & Cách dùng bài viết)."""
        row = tk.Frame(parent, bg=self.t["bg"])
        row.pack(fill="both", expand=True, pady=(0, 10))

        # Left Column: Nhóm mục tiêu
        left_col = tk.Frame(row, bg=self.t["card_bg"], padx=16, pady=16, relief="solid", bd=1)
        left_col.configure(highlightbackground=self.t["border"], highlightthickness=1)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 10))

        g_head = tk.Frame(left_col, bg=self.t["card_bg"])
        g_head.pack(fill="x", pady=(0, 10))

        tk.Label(g_head, text="Nhóm mục tiêu", font=("SF Pro", 12, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(side="left")

        btn_imp = tk.Button(
            g_head,
            text="Import nhóm",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="solid",
            bd=1,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._paste_groups_from_clipboard,
        )
        btn_imp.pack(side="right", padx=(6, 0))

        btn_add_g = tk.Button(
            g_head,
            text="+ Thêm nhóm",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
            relief="flat",
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._add_group_prompt,
        )
        btn_add_g.pack(side="right")

        # Filter bar
        filter_row = tk.Frame(left_col, bg=self.t["card_bg"])
        filter_row.pack(fill="x", pady=(0, 10))

        entry_filter = tk.Entry(filter_row, bg=self.t["input_bg"], fg=self.t["text_primary"], font=("SF Pro", 9), relief="flat", bd=5)
        entry_filter.insert(0, "Tìm tên hoặc URL nhóm...")
        entry_filter.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.badge_g_total = tk.Label(filter_row, text="Tổng 0", font=("SF Pro", 8), bg=self.t["input_bg"], fg=self.t["text_secondary"], padx=8, pady=3)
        self.badge_g_total.pack(side="left", padx=2)

        self.badge_g_ready = tk.Label(filter_row, text="Sẵn sàng 0", font=("SF Pro", 8, "bold"), bg=self.t["success_bg"], fg=self.t["success"], padx=8, pady=3)
        self.badge_g_ready.pack(side="left", padx=2)

        self.badge_g_wait = tk.Label(filter_row, text="Đang chờ 0", font=("SF Pro", 8, "bold"), bg=self.t["warning_bg"], fg=self.t["warning"], padx=8, pady=3)
        self.badge_g_wait.pack(side="left", padx=2)

        # Groups Treeview
        g_tree_frame = tk.Frame(left_col, bg=self.t["card_bg"])
        g_tree_frame.pack(fill="both", expand=True)

        self.tree_groups = ttk.Treeview(
            g_tree_frame,
            columns=("idx", "name", "members", "privacy", "url", "membership", "cooldown"),
            show="headings",
            selectmode="extended",
            height=10,
        )
        self.tree_groups.heading("idx", text="#")
        self.tree_groups.heading("name", text="Nhóm")
        self.tree_groups.heading("members", text="Thành Viên")
        self.tree_groups.heading("privacy", text="Loại Nhóm")
        self.tree_groups.heading("url", text="URL Nhóm")
        self.tree_groups.heading("membership", text="Thành Viên?")
        self.tree_groups.heading("cooldown", text="Trạng Thái")

        self.tree_groups.column("idx", width=35, anchor="center")
        self.tree_groups.column("name", width=150)
        self.tree_groups.column("members", width=70, anchor="center")
        self.tree_groups.column("privacy", width=70, anchor="center")
        self.tree_groups.column("url", width=160)
        self.tree_groups.column("membership", width=95, anchor="center")
        self.tree_groups.column("cooldown", width=85, anchor="center")
        self.tree_groups.pack(fill="both", expand=True)

        self.tree_groups.bind("<Delete>", lambda e: self._remove_selected_group())
        self.tree_groups.bind("<BackSpace>", lambda e: self._remove_selected_group())
        self.tree_groups.bind("<Button-1>", self._on_group_tree_press)
        self.tree_groups.bind("<B1-Motion>", self._on_group_tree_drag)
        self.tree_groups.bind("<ButtonRelease-1>", self._on_group_tree_release)
        g_tree_frame.bind("<Button-1>", self._deselect_groups)
        left_col.bind("<Button-1>", self._deselect_groups)

        g_col_foot = tk.Frame(left_col, bg=self.t["card_bg"])
        g_col_foot.pack(fill="x", pady=(8, 0))

        self.lbl_g_count_info = tk.Label(g_col_foot, text="0 nhóm sẵn sàng", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["text_secondary"])
        self.lbl_g_count_info.pack(side="left")

        self.btn_post_selected = tk.Button(
            g_col_foot,
            text="🚀 Đăng bài đã chọn",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent"],
            fg="#FFFFFF",
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._start_production_flow,
        )
        self.btn_post_selected.pack(side="right", padx=(6, 0))

        self.btn_join_groups = tk.Button(
            g_col_foot,
            text="🤝 Gia nhập nhóm",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
            bd=0,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._start_join_groups_flow,
        )
        self.btn_join_groups.pack(side="right", padx=(6, 0))

        btn_scan = tk.Button(g_col_foot, text="🔍 Quét Info", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["accent"], bd=0, command=self._scan_groups_metadata)
        btn_scan.pack(side="right", padx=(6, 0))

        btn_del_g = tk.Button(g_col_foot, text="Xóa nhóm đã chọn", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["danger"], bd=0, command=self._remove_selected_group)
        btn_del_g.pack(side="right")

        # Right Column: Cách dùng bài viết
        right_col = tk.Frame(row, bg=self.t["card_bg"], width=420, padx=20, pady=16, relief="solid", bd=1)
        right_col.configure(highlightbackground=self.t["border"], highlightthickness=1)
        right_col.pack(side="right", fill="both", padx=(10, 0))
        right_col.pack_propagate(False)

        tk.Label(right_col, text="Cách dùng bài viết", font=("SF Pro", 12, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(anchor="w", pady=(0, 6))

        # Dropdown selection
        pill_mode = tk.Label(
            right_col,
            text="Luân phiên theo thứ tự  ⌵",
            font=("SF Pro", 9, "bold"),
            bg=self.t["input_bg"],
            fg=self.t["text_primary"],
            padx=12,
            pady=6,
        )
        pill_mode.pack(fill="x", pady=(0, 10))

        self.lbl_alloc_summary = tk.Label(
            right_col,
            text="0 bài đã chọn · 0 ảnh",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        )
        self.lbl_alloc_summary.pack(anchor="w", pady=(0, 8))

        # List of cycling posts
        self.lst_cycling_posts = tk.Listbox(right_col, height=5, font=("SF Pro", 9), relief="flat", bd=0, bg=self.t["card_bg"])
        self.lst_cycling_posts.pack(fill="x", pady=(0, 8))

        tk.Label(
            right_col,
            text="Hết danh sách sẽ quay lại bài đầu.",
            font=("SF Pro", 8, "italic"),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        ).pack(anchor="w", pady=(0, 12))

        # Blue highlight box
        alloc_box = tk.Frame(right_col, bg=self.t["accent_soft"], padx=14, pady=12, relief="flat")
        alloc_box.pack(fill="x", pady=(0, 12))

        tk.Label(
            alloc_box,
            text="Mỗi nhóm nhận 1 bài trong phiên.",
            font=("SF Pro", 9, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
        ).pack(anchor="w")

        self.lbl_alloc_calc = tk.Label(
            alloc_box,
            text="0 nhóm → 0 lượt đăng",
            font=("SF Pro", 8),
            bg=self.t["accent_soft"],
            fg=self.t["text_secondary"],
        )
        self.lbl_alloc_calc.pack(anchor="w", pady=(2, 0))

        # View 2 Footer
        v2_foot = tk.Frame(parent, bg=self.t["bg"])
        v2_foot.pack(fill="x", side="bottom")

        tk.Button(
            v2_foot,
            text="← Bài viết",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="solid",
            bd=1,
            padx=14,
            pady=6,
            cursor="hand2",
            command=lambda: self._switch_content_subtab(0),
        ).pack(side="left")

        tk.Button(
            v2_foot,
            text="Tiếp tục: Kiểm tra & Chạy  ➔",
            font=("SF Pro", 9, "bold"),
            bg=self.t["accent"],
            fg="#FFFFFF",
            relief="flat",
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2",
            command=lambda: self._switch_main_tab(2),
        ).pack(side="right")

    # =========================================================================
    # SCREEN 04 & 05: VẬN HÀNH & GIÁM SÁT (IMAGE 4 & 5)
    # =========================================================================

    def _build_screen_04_and_05_ops(self):
        """Builds Screen 04 (Pre-flight simulation) and Screen 05 (Live monitor)."""
        container = self.screen_ops

        # Heading & Subtitle
        head_frame = tk.Frame(container, bg=self.t["bg"])
        head_frame.pack(fill="x", pady=(0, 10))

        tk.Label(
            head_frame,
            text="Vận hành & Giám sát",
            font=("SF Pro", 20, "bold"),
            bg=self.t["bg"],
            fg=self.t["text_primary"],
        ).pack(side="left")

        self.pill_ops_mode = tk.Label(
            head_frame,
            text="MÔ PHÒNG",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
            padx=10,
            pady=3,
        )
        self.pill_ops_mode.pack(side="right")

        self.lbl_ops_subtitle = tk.Label(
            container,
            text="Kiểm tra bài viết, ảnh đính kèm và kế hoạch đăng trước khi bắt đầu.",
            font=("SF Pro", 10),
            bg=self.t["bg"],
            fg=self.t["text_secondary"],
        )
        self.lbl_ops_subtitle.pack(anchor="w", pady=(0, 10))

        # Dynamic Content Container: switches between Screen 04 and Screen 05
        self.ops_view_box = tk.Frame(container, bg=self.t["bg"])
        self.ops_view_box.pack(fill="both", expand=True)

        self.view_ops_sim = tk.Frame(self.ops_view_box, bg=self.t["bg"])
        self.view_ops_live = tk.Frame(self.ops_view_box, bg=self.t["bg"])

        self._build_screen_04_sim(self.view_ops_sim)
        self._build_screen_05_live(self.view_ops_live)

        # Footer Action Bar (Image 4 & 5 Bottom bar)
        self.ops_footer = tk.Frame(container, bg=self.t["bg"])
        self.ops_footer.pack(fill="x", side="bottom", pady=(10, 0))

        self.btn_dryrun = tk.Button(
            self.ops_footer,
            text="Chạy thử",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="solid",
            bd=1,
            padx=16,
            pady=7,
            cursor="hand2",
            command=self._start_dry_run_flow,
        )
        self.btn_dryrun.pack(side="left", padx=(0, 8))

        self.btn_canary = tk.Button(
            self.ops_footer,
            text="Kiểm tra DOM",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
            relief="solid",
            bd=1,
            padx=16,
            pady=7,
            cursor="hand2",
            command=self._start_dom_check_flow,
        )
        self.btn_canary.pack(side="left")

        self.btn_stop = tk.Button(
            self.ops_footer,
            text="Dừng",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
            relief="solid",
            bd=1,
            padx=16,
            pady=7,
            state="disabled",
            command=self._confirm_stop_session,
        )
        self.btn_stop.pack(side="right", padx=(8, 0))

        self.btn_start = tk.Button(
            self.ops_footer,
            text="Bắt đầu đăng",
            font=("SF Pro", 9, "bold"),
            bg=self.t["accent"],
            fg="#FFFFFF",
            relief="flat",
            bd=0,
            padx=22,
            pady=8,
            cursor="hand2",
            command=self._start_production_flow,
        )
        self.btn_start.pack(side="right")

        self._render_ops_subview()

    def _render_ops_subview(self):
        """Switches between Simulation Screen 04 and Running Screen 05."""
        self._refresh_round_robin_plan()
        if self.is_running_session:
            self.pill_ops_mode.configure(text="ĐANG CHẠY", bg=self.t["success_bg"], fg=self.t["success"])
            self.lbl_ops_subtitle.configure(text="Theo dõi tiến độ và nhật ký của phiên đăng hiện tại.")
            self.view_ops_sim.pack_forget()
            self.view_ops_live.pack(fill="both", expand=True)
            self.btn_start.configure(state="disabled")
            self.btn_dryrun.configure(state="disabled")
            self.btn_canary.configure(state="disabled")
            self.btn_stop.configure(
                state="normal",
                text="Dừng phiên",
                bg=self.t["danger_bg"],
                fg=self.t["danger"],
                font=("SF Pro", 9, "bold"),
            )
        else:
            self.pill_ops_mode.configure(text="MÔ PHÒNG", bg=self.t["accent_soft"], fg=self.t["accent_soft_text"])
            self.lbl_ops_subtitle.configure(text="Kiểm tra bài viết, ảnh đính kèm và kế hoạch đăng trước khi bắt đầu.")
            self.view_ops_live.pack_forget()
            self.view_ops_sim.pack(fill="both", expand=True)
            self.btn_start.configure(state="normal")
            self.btn_dryrun.configure(state="normal")
            self.btn_canary.configure(state="normal")
            self.btn_stop.configure(
                state="disabled",
                text="Dừng",
                bg=self.t["card_bg"],
                fg=self.t["text_secondary"],
                font=("SF Pro", 9),
            )

    # --- SCREEN 04: KẾT QUẢ CHẠY THỬ (IMAGE 4) ---
    def _build_screen_04_sim(self, parent: tk.Frame):
        """Builds Image 4: 4 Stat Cards, Checklist, Simulation log."""
        # Top Banner (Image 4 soft blue banner)
        banner = tk.Frame(parent, bg=self.t["accent_soft"], padx=18, pady=12, relief="flat")
        banner.pack(fill="x", pady=(0, 12))

        tk.Label(
            banner,
            text="✓  Chạy thử hoàn tất",
            font=("SF Pro", 10, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
        ).pack(anchor="w")

        self.lbl_sim_banner_desc = tk.Label(
            banner,
            text="3 bài kèm 6 ảnh; 9 nhóm được chọn. Chạy thử không mở trình duyệt và không đăng bài.",
            font=("SF Pro", 9),
            bg=self.t["accent_soft"],
            fg=self.t["text_secondary"],
        )
        self.lbl_sim_banner_desc.pack(anchor="w", pady=(2, 0))

        # 4 Stat Cards Row (Image 4)
        stat_row = tk.Frame(parent, bg=self.t["bg"])
        stat_row.pack(fill="x", pady=(0, 12))

        self.c_sim_posts = self._create_canva_stat_card(stat_row, "Bài được chọn", "3 / 4", self.t["accent"])
        self.c_sim_imgs = self._create_canva_stat_card(stat_row, "Ảnh đính kèm", "6", self.t["accent"])
        self.c_sim_groups = self._create_canva_stat_card(stat_row, "Nhóm có thể đăng", "9 / 12", self.t["success"])
        self.c_sim_turns = self._create_canva_stat_card(stat_row, "Lượt đăng dự kiến", "9 / 10", self.t["accent"])

        for c in (self.c_sim_posts, self.c_sim_imgs, self.c_sim_groups, self.c_sim_turns):
            c.pack(side="left", fill="both", expand=True, padx=4)

        # Middle Row: 2 Cards (Checklist & Trước khi bắt đầu)
        mid_row = tk.Frame(parent, bg=self.t["bg"])
        mid_row.pack(fill="x", pady=(0, 12))

        # Left: Kết quả chạy thử checklist
        c_check = tk.Frame(mid_row, bg=self.t["card_bg"], padx=16, pady=14, relief="solid", bd=1)
        c_check.configure(highlightbackground=self.t["border"], highlightthickness=1)
        c_check.pack(side="left", fill="both", expand=True, padx=(0, 6))

        tk.Label(c_check, text="Kết quả chạy thử", font=("SF Pro", 11, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(anchor="w", pady=(0, 8))

        self.lbl_chk1 = self._create_checklist_item(c_check, "3 bài đã chọn; 6 tệp ảnh hợp lệ")
        self.lbl_chk2 = self._create_checklist_item(c_check, "9 nhóm sẵn sàng; bỏ qua 3 nhóm đang chờ")
        self.lbl_chk3 = self._create_checklist_item(c_check, "Luân phiên 01 → 02 → 03; mỗi bài dùng 3 lượt")

        # Right: Trước khi bắt đầu
        c_pre = tk.Frame(mid_row, bg=self.t["card_bg"], padx=16, pady=14, relief="solid", bd=1)
        c_pre.configure(highlightbackground=self.t["border"], highlightthickness=1)
        c_pre.pack(side="right", fill="both", expand=True, padx=(6, 0))

        tk.Label(c_pre, text="Trước khi bắt đầu", font=("SF Pro", 11, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(anchor="w", pady=(0, 8))
        tk.Label(c_pre, text="45–90 giây giữa các lượt đăng", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w", pady=2)
        tk.Label(c_pre, text="Mỗi nhóm nhận 1 bài trong phiên", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w", pady=2)

        self.pill_dom_check = tk.Label(
            c_pre,
            text=f"Kiểm tra DOM: {self.dom_check_status}",
            font=("SF Pro", 8, "bold"),
            bg=self.t["warning_bg"],
            fg=self.t["warning"],
            padx=10,
            pady=4,
        )
        self.pill_dom_check.pack(anchor="w", pady=(8, 0))

        # Bottom: Nhật ký chạy thử
        c_log = tk.Frame(parent, bg=self.t["card_bg"], padx=16, pady=12, relief="solid", bd=1)
        c_log.configure(highlightbackground=self.t["border"], highlightthickness=1)
        c_log.pack(fill="both", expand=True)

        tk.Label(c_log, text="Nhật ký chạy thử", font=("SF Pro", 10, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(anchor="w", pady=(0, 4))

        self.txt_sim_log = ScrolledText(c_log, height=4, font=("Menlo", 9) if sys.platform == "darwin" else ("Consolas", 9), relief="flat", bd=0)
        self.txt_sim_log.pack(fill="both", expand=True)
        self.txt_sim_log.insert("1.0", "10:00:01    INFO    Đã chọn 3 bài viết kèm 6 ảnh\n10:00:02    INFO    Bỏ qua 3 nhóm đang chờ\n10:00:02    INFO    Kế hoạch: 9 nhóm · 1 bài kèm ảnh cho mỗi nhóm\n")

    def _create_canva_stat_card(self, parent, title: str, val: str, val_color: str) -> tk.Frame:
        f = tk.Frame(parent, bg=self.t["card_bg"], padx=16, pady=12, relief="solid", bd=1)
        f.configure(highlightbackground=self.t["border"], highlightthickness=1)
        tk.Label(f, text=title, font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="w")
        v_lbl = tk.Label(f, text=val, font=("SF Pro", 18, "bold"), bg=self.t["card_bg"], fg=val_color)
        v_lbl.pack(anchor="w", pady=(4, 0))
        f.val_label = v_lbl  # type: ignore
        return f

    def _create_checklist_item(self, parent, text: str) -> tk.Label:
        row = tk.Frame(parent, bg=self.t["card_bg"])
        row.pack(fill="x", pady=2)
        lbl = tk.Label(row, text=f"●  {text}", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"])
        lbl.pack(side="left")
        tk.Label(row, text="Hoàn tất", font=("SF Pro", 8, "bold"), bg=self.t["card_bg"], fg=self.t["success"]).pack(side="right")
        return lbl

    # --- SCREEN 05: GIÁM SÁT BÀI VIẾT THEO NHÓM (IMAGE 5) ---
    def _build_screen_05_live(self, parent: tk.Frame):
        """Builds Image 5: Live Progress Banner, Next Post Card, Live Task Table & Console."""
        # Top Live Banner (Image 5)
        live_banner = tk.Frame(parent, bg=self.t["card_bg"], padx=20, pady=14, relief="solid", bd=1)
        live_banner.configure(highlightbackground=self.t["border"], highlightthickness=1)
        live_banner.pack(fill="x", pady=(0, 10))

        left_side = tk.Frame(live_banner, bg=self.t["card_bg"])
        left_side.pack(side="left", fill="both", expand=True)

        self.lbl_live_processed = tk.Label(
            left_side,
            text="Đã xử lý 0 / 0 nhóm",
            font=("SF Pro", 14, "bold"),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
        )
        self.lbl_live_processed.pack(anchor="w")

        self.lbl_live_countdown = tk.Label(
            left_side,
            text="Đang chuẩn bị phiên đăng...",
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        )
        self.lbl_live_countdown.pack(anchor="w", pady=(2, 6))

        self.live_progress_bar = ttk.Progressbar(left_side, orient="horizontal", mode="determinate")
        self.live_progress_bar.pack(fill="x")

        # Right Live Numbers (Image 5: Đã đăng, Chờ duyệt, Còn lại, Lỗi)
        right_side = tk.Frame(live_banner, bg=self.t["card_bg"])
        right_side.pack(side="right", padx=(20, 0))

        self.num_posted = self._create_num_stat(right_side, "Đã đăng", "0", self.t["success"])
        self.num_pending = self._create_num_stat(right_side, "Chờ duyệt", "0", self.t["warning"])
        self.num_remaining = self._create_num_stat(right_side, "Còn lại", "0", self.t["accent"])
        self.num_failed = self._create_num_stat(right_side, "Lỗi", "0", self.t["danger"])

        for n in (self.num_posted, self.num_pending, self.num_remaining, self.num_failed):
            n.pack(side="left", padx=12)

        # Middle Row: Tiến độ nhóm & Bài viết tiếp theo (Image 5)
        mid_row = tk.Frame(parent, bg=self.t["bg"])
        mid_row.pack(fill="both", expand=True, pady=(0, 10))

        # Left: Tiến độ nhóm & bài viết table
        c_tasks = tk.Frame(mid_row, bg=self.t["card_bg"], padx=16, pady=12, relief="solid", bd=1)
        c_tasks.configure(highlightbackground=self.t["border"], highlightthickness=1)
        c_tasks.pack(side="left", fill="both", expand=True, padx=(0, 6))

        t_head = tk.Frame(c_tasks, bg=self.t["card_bg"])
        t_head.pack(fill="x", pady=(0, 6))
        tk.Label(t_head, text="Tiến độ nhóm & bài viết", font=("SF Pro", 11, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(side="left")
        tk.Label(t_head, text="Gần đây & tiếp theo", font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(side="right")

        self.tree_live_tasks = ttk.Treeview(
            c_tasks,
            columns=("group", "post", "status", "time"),
            show="headings",
            height=5,
        )
        self.tree_live_tasks.heading("group", text="Nhóm")
        self.tree_live_tasks.heading("post", text="Bài Viết / Ảnh")
        self.tree_live_tasks.heading("status", text="Trạng Thái")
        self.tree_live_tasks.heading("time", text="Cập Nhật")

        self.tree_live_tasks.column("group", width=180)
        self.tree_live_tasks.column("post", width=160)
        self.tree_live_tasks.column("status", width=95, anchor="center")
        self.tree_live_tasks.column("time", width=80, anchor="center")
        self.tree_live_tasks.pack(fill="both", expand=True)

        # Right: Bài viết tiếp theo (Image 5)
        c_next = tk.Frame(mid_row, bg=self.t["card_bg"], width=360, padx=16, pady=14, relief="solid", bd=1)
        c_next.configure(highlightbackground=self.t["border"], highlightthickness=1)
        c_next.pack(side="right", fill="both", padx=(6, 0))
        c_next.pack_propagate(False)

        tk.Label(c_next, text="Bài viết tiếp theo", font=("SF Pro", 11, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(anchor="w", pady=(0, 8))
        self.lbl_next_title = tk.Label(c_next, text="01 · Cẩm nang làm việc", font=("SF Pro", 12, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"])
        self.lbl_next_title.pack(anchor="w")

        self.lbl_next_dest = tk.Label(c_next, text="3 ảnh · Nhóm Cộng đồng", font=("SF Pro", 9), bg=self.t["card_bg"], fg=self.t["text_secondary"])
        self.lbl_next_dest.pack(anchor="w", pady=(2, 10))

        tk.Frame(c_next, bg=self.t["border_light"], height=1).pack(fill="x", pady=6)

        self.lbl_next_rotation_info = tk.Label(
            c_next,
            text="Đang luân phiên 3 bài · 6 ảnh",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["accent"],
        )
        self.lbl_next_rotation_info.pack(anchor="w", pady=2)

        self.lbl_next_skip_info = tk.Label(
            c_next,
            text="3 nhóm được bỏ qua do đang chờ.",
            font=("SF Pro", 8),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
        )
        self.lbl_next_skip_info.pack(anchor="w", pady=2)

        # Bottom: Nhật ký trực tiếp (Live Console)
        c_console = tk.Frame(parent, bg=self.t["card_bg"], padx=16, pady=10, relief="solid", bd=1)
        c_console.configure(highlightbackground=self.t["border"], highlightthickness=1)
        c_console.pack(fill="both", expand=True)

        c_top = tk.Frame(c_console, bg=self.t["card_bg"])
        c_top.pack(fill="x", pady=(0, 4))
        tk.Label(c_top, text="Nhật ký trực tiếp", font=("SF Pro", 10, "bold"), bg=self.t["card_bg"], fg=self.t["text_primary"]).pack(side="left")

        self.btn_auto_scroll = tk.Button(
            c_top,
            text="Tự cuộn: Bật",
            font=("SF Pro", 8, "bold"),
            bg=self.t["accent_soft"],
            fg=self.t["accent_soft_text"],
            bd=0,
            padx=10,
            pady=2,
            command=self._toggle_autoscroll,
        )
        self.btn_auto_scroll.pack(side="right")

        self.console_text = ScrolledText(
            c_console,
            bg=self.t["console_bg"],
            fg=self.t["console_fg"],
            insertbackground="white",
            font=("Menlo", 9) if sys.platform == "darwin" else ("Consolas", 9),
            height=4,
            relief="flat",
            bd=0,
        )
        self.console_text.pack(fill="both", expand=True)
        self.console_text.tag_config("INFO", foreground="#4ec9b0")
        self.console_text.tag_config("WARNING", foreground="#ce9178")
        self.console_text.tag_config("ERROR", foreground="#f44747")
        self.console_text.tag_config("CRITICAL", foreground="#ff5555", font=("Menlo", 9, "bold"))
        self.console_text.tag_config("DEBUG", foreground="#808080")

    def _create_num_stat(self, parent, title: str, val: str, color: str) -> tk.Frame:
        f = tk.Frame(parent, bg=self.t["card_bg"])
        tk.Label(f, text=title, font=("SF Pro", 8), bg=self.t["card_bg"], fg=self.t["text_secondary"]).pack(anchor="center")
        v_lbl = tk.Label(f, text=val, font=("SF Pro", 18, "bold"), bg=self.t["card_bg"], fg=color)
        v_lbl.pack(anchor="center")
        f.val_label = v_lbl  # type: ignore
        return f

    # =========================================================================
    # SCREEN 06: MODAL XÁC NHẬN DỪNG PHIÊN (IMAGE 6)
    # =========================================================================

    def _confirm_stop_session(self):
        """Displays Canva Screen 06 Pause/Stop Modal Dialog."""
        dialog = tk.Toplevel(self)
        dialog.title("Dừng phiên đăng?")
        dialog.geometry("460x280")
        dialog.configure(bg=self.t["card_bg"])
        dialog.transient(self)
        dialog.grab_set()

        # Center on parent
        x = self.winfo_x() + (self.winfo_width() - 460) // 2
        y = self.winfo_y() + (self.winfo_height() - 280) // 2
        dialog.geometry(f"+{x}+{y}")

        # Pause Icon in pink/red circle (Image 6)
        icon_f = tk.Label(
            dialog,
            text="⏸",
            font=("SF Pro", 16),
            bg=self.t["danger_bg"],
            fg=self.t["danger"],
            width=3,
            height=1,
        )
        icon_f.pack(pady=(20, 10))

        tk.Label(
            dialog,
            text="Dừng phiên đăng?",
            font=("SF Pro", 16, "bold"),
            bg=self.t["card_bg"],
            fg=self.t["text_primary"],
        ).pack()

        desc = "Hoàn tất thao tác đang xử lý rồi dừng.\nCác nhóm còn lại sẽ chưa được đăng."
        tk.Label(
            dialog,
            text=desc,
            font=("SF Pro", 9),
            bg=self.t["card_bg"],
            fg=self.t["text_secondary"],
            justify="center",
        ).pack(pady=(6, 12))

        # Status Pill
        pill_status = tk.Label(
            dialog,
            text=f"Đã xử lý {self.metric_processed}/{self.metric_total} · Còn lại {self.metric_remaining} nhóm",
            font=("SF Pro", 8, "bold"),
            bg=self.t["input_bg"],
            fg=self.t["text_secondary"],
            padx=12,
            pady=4,
        )
        pill_status.pack(pady=(0, 16))

        # Buttons (Image 6)
        btn_box = tk.Frame(dialog, bg=self.t["card_bg"])
        btn_box.pack(fill="x", padx=20)

        def do_stop():
            self.logger.warning("🛑 Người dùng xác nhận DỪNG PHIÊN (Graceful Stop).")
            self.stop_event.set()
            dialog.destroy()

        btn_continue = tk.Button(
            btn_box,
            text="Tiếp tục chạy",
            font=("SF Pro", 9),
            bg=self.t["input_bg"],
            fg=self.t["text_primary"],
            relief="flat",
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2",
            command=dialog.destroy,
        )
        btn_continue.pack(side="left", fill="x", expand=True, padx=(0, 6))

        btn_stop_now = tk.Button(
            btn_box,
            text="Dừng sau thao tác hiện tại",
            font=("SF Pro", 9, "bold"),
            bg=self.t["danger_bg"],
            fg=self.t["danger"],
            relief="flat",
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2",
            command=do_stop,
        )
        btn_stop_now.pack(side="right", fill="x", expand=True, padx=(6, 0))

    # =========================================================================
    # DATA ACTIONS & ROUND-ROBIN COMPUTATION
    # =========================================================================

    def _load_all_data(self):
        self.posts = load_posts_collection(self.posts_file)
        self.groups_meta = load_from_file(str(self.groups_meta_file))
        self.target_links = []
        if self.links_file.exists():
            try:
                data = load_from_file(str(self.links_file))
                self.target_links = data.get("target_links", [])
            except Exception as err:
                self.logger.error("Lỗi đọc links.json: %s", err)

        self._refresh_posts_tree()
        self._refresh_groups_tree()
        self._refresh_round_robin_plan()

    def _refresh_posts_tree(self):
        self.tree_posts.delete(*self.tree_posts.get_children())
        selected_count = 0
        total_imgs = 0

        for idx, p in enumerate(self.posts, 1):
            if p.is_selected:
                selected_count += 1
                total_imgs += len(p.images)
            check_str = "☑" if p.is_selected else "⬜"
            status_str = "Sẵn sàng" if p.status == "ready" else "Bản nháp"
            self.tree_posts.insert("", "end", values=(check_str, idx, p.name, f"{len(p.images)}", status_str))

        if self.posts and (not self.active_post_id or not any(p.id == self.active_post_id for p in self.posts)):
            self.active_post_id = self.posts[0].id

        self.btn_subtab_posts.configure(text=f"Bài viết · {len(self.posts)}")
        self.pill_selected_posts.configure(text=f"{selected_count} bài đã chọn")
        self.pill_total_images.configure(text=f"{total_imgs} ảnh đính kèm")
        self.lbl_post_list_footer.configure(text=f"Kéo để đổi thứ tự · Đã chọn {selected_count} / {len(self.posts)} bài")

        self._display_active_post()

    def _display_active_post(self):
        post = next((p for p in self.posts if p.id == self.active_post_id), None)
        if not post:
            return

        self.lbl_edit_title.configure(text=f"Chỉnh sửa {post.name}")
        self.entry_post_name.delete(0, tk.END)
        self.entry_post_name.insert(0, post.name)

        self.txt_post_content.delete("1.0", tk.END)
        self.txt_post_content.insert("1.0", post.content)
        self._update_char_counter()

        self.lbl_img_section.configure(text=f"Hình ảnh · {len(post.images)}")
        self.lst_images.delete(0, tk.END)
        for idx, img in enumerate(post.images, 1):
            badge = " [Ảnh bìa]" if idx == 1 else ""
            self.lst_images.insert(tk.END, f"{idx}. {os.path.basename(img)}{badge}")

    def _on_post_selected(self, event=None):
        sel = self.tree_posts.selection()
        if not sel:
            return
        idx = int(self.tree_posts.item(sel[0], "values")[1]) - 1
        if 0 <= idx < len(self.posts):
            self.active_post_id = self.posts[idx].id
            self._display_active_post()

    def _toggle_post_selection(self):
        sel = self.tree_posts.selection()
        if not sel:
            return
        idx = int(self.tree_posts.item(sel[0], "values")[1]) - 1
        if 0 <= idx < len(self.posts):
            self.posts[idx].is_selected = not self.posts[idx].is_selected
            save_posts_collection(self.posts_file, self.posts)
            self._refresh_posts_tree()
            self._refresh_round_robin_plan()

    def _add_post(self):
        new_id = f"post_{int(time.time())}"
        new_post = PostItem(
            id=new_id,
            name=f"{len(self.posts) + 1:02d} · Bài viết mới",
            content="",
            images=[],
            is_selected=True,
            status="draft",
        )
        self.posts.append(new_post)
        self.active_post_id = new_id
        save_posts_collection(self.posts_file, self.posts)
        self._refresh_posts_tree()
        self._refresh_round_robin_plan()

    def _duplicate_post(self):
        post = next((p for p in self.posts if p.id == self.active_post_id), None)
        if not post:
            return
        new_id = f"post_{int(time.time())}"
        dup = PostItem(
            id=new_id,
            name=f"{post.name} (Bản sao)",
            content=post.content,
            images=list(post.images),
            is_selected=True,
            status=post.status,
        )
        self.posts.append(dup)
        self.active_post_id = new_id
        save_posts_collection(self.posts_file, self.posts)
        self._refresh_posts_tree()
        self._refresh_round_robin_plan()

    def _delete_post(self):
        if len(self.posts) <= 1:
            messagebox.showwarning("Cảnh Báo", "Cần giữ lại ít nhất một bài viết.")
            return
        post = next((p for p in self.posts if p.id == self.active_post_id), None)
        if not post:
            return
        if not messagebox.askyesno("Xác Nhận Xóa", f"Bạn có chắc muốn xóa bài viết '{post.name}' không?"):
            return
        self.posts = [p for p in self.posts if p.id != self.active_post_id]
        self.active_post_id = self.posts[0].id if self.posts else None
        save_posts_collection(self.posts_file, self.posts)
        self._refresh_posts_tree()
        self._refresh_round_robin_plan()

    def _update_char_counter(self, event=None):
        text = self.txt_post_content.get("1.0", "end-1c")
        self.lbl_char_count.configure(text=f"Ký tự: {len(text)}")

    def _add_images(self):
        post = next((p for p in self.posts if p.id == self.active_post_id), None)
        if not post or not filedialog:
            return
        files = filedialog.askopenfilenames(
            title=f"Chọn ảnh cho {post.name}",
            filetypes=[("Hình ảnh", "*.jpg *.jpeg *.png *.webp *.heic *.bmp"), ("Tất cả", "*.*")],
        )
        if files:
            current_set = set(post.images)
            for f in files:
                if f not in current_set:
                    post.images.append(f)
            self._display_active_post()

    def _remove_image(self):
        post = next((p for p in self.posts if p.id == self.active_post_id), None)
        if not post or not post.images:
            return
        sel = self.lst_images.curselection()
        if not sel:
            return
        del post.images[sel[0]]
        self._display_active_post()

    def _move_image(self, direction: int):
        post = next((p for p in self.posts if p.id == self.active_post_id), None)
        if not post or not post.images:
            return
        sel = self.lst_images.curselection()
        if not sel:
            return
        idx = sel[0]
        new_idx = idx + direction
        if 0 <= new_idx < len(post.images):
            post.images[idx], post.images[new_idx] = post.images[new_idx], post.images[idx]
            self._display_active_post()
            self.lst_images.select_set(new_idx)

    def _save_current_post(self):
        post = next((p for p in self.posts if p.id == self.active_post_id), None)
        if not post:
            return
        post.name = self.entry_post_name.get().strip() or "Bài viết chưa đặt tên"
        post.content = self.txt_post_content.get("1.0", "end-1c")
        post.status = "ready" if (post.content.strip() or post.images) else "draft"
        save_posts_collection(self.posts_file, self.posts)
        self._refresh_posts_tree()
        self._refresh_round_robin_plan()
        self.lbl_save_status.configure(text="Đã lưu thay đổi", fg=self.t["success"])

    def _refresh_groups_tree(self):
        self.tree_groups.delete(*self.tree_groups.get_children())
        state_mgr = StateManager(str(self.history_file))
        ready_count = 0
        wait_count = 0

        group_targets = load_group_targets(str(self.links_file))
        membership_map = {}
        for g in group_targets:
            if g.get("url"):
                membership_map[normalize_url(g["url"])] = g.get("membership")

        for idx, url in enumerate(self.target_links, 1):
            in_cd, rem_secs = state_mgr.is_in_cooldown(url, cooldown_hours=self.config.cooldown_hours)
            if in_cd:
                wait_count += 1
                status_str = f"Chờ {round(rem_secs/3600.0, 1)}h"
            else:
                ready_count += 1
                status_str = "Sẵn sàng"

            meta = self.groups_meta.get(url, {})
            name = meta.get("name") or "Nhóm mẫu"

            raw_mem = membership_map.get(normalize_url(url)) or meta.get("membership")
            if raw_mem in ("ALREADY_JOINED", "JOINED_SUCCESS"):
                mem_str = "Đã vào ✅"
            elif raw_mem in ("JOIN_REQUEST_SENT", "PENDING_APPROVAL"):
                mem_str = "Chờ duyệt ⏳"
            elif raw_mem in ("NOT_JOINED", "NEEDS_JOIN"):
                mem_str = "Chưa vào ⚪"
            elif raw_mem == "ACTION_BLOCKED":
                mem_str = "Bị chặn 🚨"
            else:
                mem_str = "Chưa rõ"

            self.tree_groups.insert(
                "",
                "end",
                values=(
                    idx,
                    name,
                    meta.get("members", "—"),
                    meta.get("privacy", "—"),
                    url,
                    mem_str,
                    status_str,
                ),
            )

        self.btn_subtab_groups.configure(text=f"Nhóm mục tiêu · {len(self.target_links)}")
        self.badge_g_total.configure(text=f"Tổng {len(self.target_links)}")
        self.badge_g_ready.configure(text=f"Sẵn sàng {ready_count}")
        self.badge_g_wait.configure(text=f"Đang chờ {wait_count}")
        self.lbl_g_count_info.configure(text=f"Hiển thị {len(self.target_links)} nhóm · {ready_count} nhóm sẵn sàng")

    def _add_group_prompt(self):
        dialog = tk.Toplevel(self)
        dialog.title("Thêm Nhóm Mục Tiêu")
        dialog.geometry("520x340")
        dialog.transient(self)
        txt = ScrolledText(dialog, wrap="word", height=10)
        txt.pack(fill="both", expand=True, padx=12, pady=10)

        def do_add():
            raw = txt.get("1.0", tk.END)
            extracted = extract_facebook_group_urls(raw)
            existing = set(self.target_links)
            added = 0
            for u in extracted:
                if u not in existing:
                    existing.add(u)
                    self.target_links.append(u)
                    added += 1
            self._save_groups_file(silent=True)
            self._refresh_groups_tree()
            self._refresh_round_robin_plan()
            dialog.destroy()
            messagebox.showinfo("Thành Công", f"Đã thêm {added} nhóm mới!")

        btn_row = tk.Frame(dialog)
        btn_row.pack(fill="x", padx=12, pady=8)
        tk.Button(btn_row, text="+ Thêm vào danh sách", font=("SF Pro", 9, "bold"), bg=self.t["accent"], fg="#FFF", bd=0, padx=12, pady=5, command=do_add).pack(side="right")
        tk.Button(btn_row, text="Hủy", bd=1, padx=10, pady=5, command=dialog.destroy).pack(side="right", padx=6)

    def _paste_groups_from_clipboard(self):
        try:
            cb = self.clipboard_get()
            extracted = extract_facebook_group_urls(cb)
            existing = set(self.target_links)
            added = 0
            for u in extracted:
                if u not in existing:
                    existing.add(u)
                    self.target_links.append(u)
                    added += 1
            self._save_groups_file(silent=True)
            self._refresh_groups_tree()
            self._refresh_round_robin_plan()
            messagebox.showinfo("Clipboard", f"Đã thêm {added} nhóm từ Clipboard!")
        except Exception as e:
            messagebox.showerror("Lỗi Clipboard", str(e))

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

    def _on_group_tree_release(self, event):
        """Resets dragging state upon mouse release."""
        self._is_dragging_groups = False
        self._drag_start_item = None

    def _remove_selected_group(self):
        sel = self.tree_groups.selection()
        if not sel:
            return
        urls_to_remove = {self.tree_groups.item(it, "values")[4] for it in sel}
        self.target_links = [u for u in self.target_links if u not in urls_to_remove]
        self._save_groups_file(silent=True)
        self._refresh_groups_tree()
        self._refresh_round_robin_plan()

    def _save_groups_file(self, silent: bool = False):
        try:
            self.links_file.write_text(json.dumps({"target_links": self.target_links}, indent=4, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            self.logger.error("Lỗi lưu links.json: %s", e)

    def _scan_groups_metadata(self):
        if not self.target_links:
            return
        urls = list(self.target_links)
        self.logger.info("🔍 Bắt đầu quét thông tin %d nhóm Facebook...", len(urls))

        def _task():
            try:
                from selenium.webdriver.common.by import By
                from helpers.common_helpers import save_group_targets
                from services.group_verifier import GroupMembershipVerifier
                verifier = GroupMembershipVerifier(selectors_path=str(self.config.json_dir / "selectors.json"))
                with get_driver(self.config) as driver:
                    for idx, url in enumerate(urls, 1):
                        if self.stop_event.is_set():
                            break
                        try:
                            driver.get(url)
                            time.sleep(2.0)
                            title = driver.title or ""
                            try:
                                h1 = driver.find_element(By.XPATH, "//div[@role='main']//h1 | //h1")
                                if h1 and h1.text.strip():
                                    title = h1.text.strip()
                            except Exception:
                                pass
                            sub_text = ""
                            try:
                                spans = driver.find_elements(By.XPATH, "//div[@role='main']//span")
                                for sp in spans[:35]:
                                    t = sp.text.strip()
                                    if any(k in t.lower() for k in ["thành viên", "members", "người tham gia", "công khai", "public", "riêng tư", "private"]):
                                        sub_text = t
                                        break
                            except Exception:
                                pass
                            mem_status = verifier.check_status(driver)
                            if mem_status.value == "UNKNOWN":
                                time.sleep(1.5)
                                mem_status = verifier.check_status(driver)
                            self.groups_meta[url] = parse_group_header_info(title, sub_text, membership=mem_status.value)
                        except Exception:
                            pass
                self.groups_meta_file.write_text(json.dumps(self.groups_meta, indent=4, ensure_ascii=False), encoding="utf-8")

                # Synchronize updated metadata & membership back to links.json
                targets = load_group_targets(str(self.links_file))
                meta_by_url = {normalize_url(u): m for u, m in self.groups_meta.items()}
                for t in targets:
                    norm = normalize_url(t.get("url", ""))
                    if norm in meta_by_url:
                        m = meta_by_url[norm]
                        if m.get("name") and m["name"] != "—":
                            t["name"] = m["name"]
                        if m.get("members") and m["members"] != "—":
                            t["members"] = m["members"]
                        if m.get("privacy") and m["privacy"] != "Chưa rõ":
                            t["privacy"] = m["privacy"]
                        if m.get("membership"):
                            t["membership"] = m["membership"]
                        from datetime import datetime, timezone
                        t["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_group_targets(str(self.links_file), targets)

                self.after(0, self._refresh_groups_tree)
                self.after(0, self._refresh_round_robin_plan)
            except Exception as e:
                self.logger.error("Lỗi quét meta: %s", e)

        threading.Thread(target=_task, daemon=True, name="MetaScanThread").start()

    def _refresh_round_robin_plan(self):
        state_mgr = StateManager(str(self.history_file))
        max_posts = int(self.entry_max_posts.get() if hasattr(self, "entry_max_posts") and self.entry_max_posts.get() else self.config.max_posts_per_run)

        sel = self.tree_groups.selection() if hasattr(self, "tree_groups") else []
        if sel:
            target_groups = [self.tree_groups.item(it, "values")[4] for it in sel if len(self.tree_groups.item(it, "values")) > 4]
            if not target_groups:
                target_groups = self.target_links
        else:
            target_groups = self.target_links

        self.current_plan = build_round_robin_plan(
            posts=self.posts,
            target_groups=target_groups,
            state_manager=state_mgr,
            max_posts_per_run=max_posts,
            cooldown_hours=self.config.cooldown_hours,
            groups_meta=self.groups_meta,
        )

        plan = self.current_plan
        selected_posts = [p for p in self.posts if p.is_selected]
        total_imgs = sum(len(p.images) for p in selected_posts)

        # Update Image 3 Allocation right pane
        if hasattr(self, "lbl_alloc_summary"):
            self.lbl_alloc_summary.configure(text=f"{len(selected_posts)} bài đã chọn · {total_imgs} ảnh")
        if hasattr(self, "lst_cycling_posts"):
            self.lst_cycling_posts.delete(0, tk.END)
            for idx, p in enumerate(selected_posts, 1):
                self.lst_cycling_posts.insert(tk.END, f"{idx:02d}    {p.name}    {len(p.images)} ảnh")
        if hasattr(self, "lbl_alloc_calc"):
            self.lbl_alloc_calc.configure(text=f"{plan.eligible_groups_count} nhóm → {plan.total_runs} lượt đăng")

        # Update Image 4 Stat cards
        if hasattr(self, "c_sim_posts"):
            self.c_sim_posts.val_label.configure(text=f"{len(selected_posts)} / {len(self.posts)}")
            self.c_sim_imgs.val_label.configure(text=str(total_imgs))
            self.c_sim_groups.val_label.configure(text=f"{plan.eligible_groups_count} / {len(self.target_links)}")
            self.c_sim_turns.val_label.configure(text=f"{plan.total_runs} / {max_posts}")

        # Update Image 5 Live numbers
        if hasattr(self, "num_remaining"):
            self.num_remaining.val_label.configure(text=str(plan.total_runs))
            self.lbl_live_processed.configure(text=f"Đã xử lý 0 / {plan.total_runs} nhóm")

        # Update Image 5 Live table
        if hasattr(self, "tree_live_tasks"):
            self.tree_live_tasks.delete(*self.tree_live_tasks.get_children())
            for t in plan.tasks:
                self.tree_live_tasks.insert("", "end", values=(t.group_name, f"{t.post_name} · {len(t.images)} ảnh", "Tiếp theo", "Chờ chạy"))

    # =========================================================================
    # BOT EXECUTION WORKFLOWS
    # =========================================================================

    def _start_dry_run_flow(self):
        self._launch_execution_worker(dry_run=True, check_dom=False)

    def _start_dom_check_flow(self):
        self._launch_execution_worker(dry_run=False, check_dom=True)

    def _start_production_flow(self):
        self._launch_execution_worker(dry_run=False, check_dom=False)

    def _launch_execution_worker(self, dry_run: bool, check_dom: bool):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("Đang Chạy", "Phiên khác đang chạy.")
            return

        self._refresh_round_robin_plan()
        if not check_dom and (not self.current_plan or not self.current_plan.tasks):
            messagebox.showwarning("Không Có Tác Vụ", "Chưa có lượt đăng nào được lên lịch.")
            return

        self.stop_event.clear()
        self.is_running_session = True
        self.active_session_type = "post"
        self._render_ops_subview()

        total = len(self.current_plan.tasks) if self.current_plan else 0
        self.metric_processed = 0
        self.metric_success = 0
        self.metric_pending = 0
        self.metric_failed = 0
        self.metric_remaining = total
        self.metric_total = total

        if hasattr(self, "live_progress_bar"):
            self.live_progress_bar["value"] = 0

        self.worker_thread = BotWorkerThreadV2(
            config=self.config,
            plan=self.current_plan,
            dry_run=dry_run,
            check_dom=check_dom,
            stop_event=self.stop_event,
            event_callback=self._on_bot_event,
            completion_callback=self._on_bot_completed,
            logger=self.logger,
        )
        self.worker_thread.start()

    def _start_join_groups_flow(self):
        """Starts background worker thread to join selected or all target groups."""
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("Đang Chạy", "Phiên khác đang chạy.")
            return

        sel = self.tree_groups.selection()
        if sel:
            targets = [self.tree_groups.item(it, "values")[4] for it in sel if len(self.tree_groups.item(it, "values")) > 4]
            desc = f"{len(targets)} nhóm đang được chọn"
        else:
            targets = list(self.target_links)
            desc = f"toàn bộ {len(targets)} nhóm trong danh sách"

        if not targets:
            messagebox.showwarning("Không Có Nhóm", "Danh sách nhóm mục tiêu đang trống!")
            return

        limit = min(len(targets), int(self.config.max_posts_per_run or 10))
        confirm = messagebox.askyesno(
            "Xác nhận Gia Nhập Nhóm",
            f"Bạn có muốn bắt đầu tự động gia nhập {desc}?\n"
            f"• Giới hạn ca chạy: {limit} nhóm\n"
            f"• Safe-Jitter: 15s–35s (2s–4s cho nhóm đã vào)\n\n"
            f"Nhấn Yes để khởi chạy.",
        )
        if not confirm:
            return

        self.stop_event.clear()
        self.is_running_session = True
        self.active_session_type = "join_groups"
        self._switch_main_tab(2)
        self._render_ops_subview()

        total = limit
        self.metric_processed = 0
        self.metric_success = 0
        self.metric_pending = 0
        self.metric_failed = 0
        self.metric_remaining = total
        self.metric_total = total

        if hasattr(self, "live_progress_bar"):
            self.live_progress_bar["value"] = 0

        def _ask_login():
            return bool(messagebox.askokcancel("Đăng nhập Facebook", "Vui lòng đăng nhập trên Chrome rồi nhấn OK."))

        self.worker_thread = BotWorkerThreadV2(
            config=self.config,
            dry_run=False,
            check_dom=False,
            join_groups=True,
            target_links=targets,
            join_limit=limit,
            stop_event=self.stop_event,
            event_callback=self._on_bot_event,
            completion_callback=self._on_bot_completed,
            logger=self.logger,
            on_login_prompt=_ask_login,
        )
        self.worker_thread.start()

    def _on_bot_event(self, event_type: str, data: Dict[str, Any]):
        if event_type == "post_result":
            idx = data.get("index", 1)
            total = data.get("total", max(self.metric_total, 1))
            status = data.get("status", "UNKNOWN")
            details = data.get("details", "")

            self.metric_processed += 1
            if status == "SUCCESS":
                self.metric_success += 1
            elif status == "PENDING_APPROVAL":
                self.metric_pending += 1
            else:
                self.metric_failed += 1

            self.metric_remaining = max(0, total - self.metric_processed)
            pct = int((self.metric_processed / max(total, 1)) * 100)

            def _update():
                if hasattr(self, "num_posted"):
                    self.num_posted.val_label.configure(text=str(self.metric_success))
                if hasattr(self, "num_pending"):
                    self.num_pending.val_label.configure(text=str(self.metric_pending))
                if hasattr(self, "num_remaining"):
                    self.num_remaining.val_label.configure(text=str(self.metric_remaining))
                if hasattr(self, "num_failed"):
                    self.num_failed.val_label.configure(text=str(self.metric_failed))
                if hasattr(self, "lbl_live_processed"):
                    self.lbl_live_processed.configure(text=f"Đã xử lý {self.metric_processed} / {total} nhóm")
                if hasattr(self, "live_progress_bar"):
                    self.live_progress_bar["value"] = pct

            self.after(0, _update)

        elif event_type == "group_join_result":
            idx = data.get("index", 1)
            total = data.get("total", max(self.metric_total, 1))
            status = data.get("status", "UNKNOWN")

            self.metric_processed += 1
            if status in ("JOINED_SUCCESS", "ALREADY_JOINED"):
                self.metric_success += 1
            elif status in ("JOIN_REQUEST_SENT", "PENDING_APPROVAL"):
                self.metric_pending += 1
            else:
                self.metric_failed += 1

            self.metric_remaining = max(0, total - self.metric_processed)
            pct = int((self.metric_processed / max(total, 1)) * 100)

            def _update_join():
                if hasattr(self, "num_posted"):
                    self.num_posted.val_label.configure(text=str(self.metric_success))
                if hasattr(self, "num_pending"):
                    self.num_pending.val_label.configure(text=str(self.metric_pending))
                if hasattr(self, "num_remaining"):
                    self.num_remaining.val_label.configure(text=str(self.metric_remaining))
                if hasattr(self, "num_failed"):
                    self.num_failed.val_label.configure(text=str(self.metric_failed))
                if hasattr(self, "lbl_live_processed"):
                    self.lbl_live_processed.configure(text=f"Đã kiểm tra {self.metric_processed} / {total} nhóm")
                if hasattr(self, "live_progress_bar"):
                    self.live_progress_bar["value"] = pct

            self.after(0, _update_join)

    def _on_bot_completed(self, results: List[Dict[str, Any]]):
        def _finish():
            self.is_running_session = False
            self._render_ops_subview()
            self._refresh_groups_tree()
            dur_str = getattr(self.worker_thread, "duration_str", "—") if hasattr(self, "worker_thread") and self.worker_thread else "—"
            self.logger.info("🏁 Phiên làm việc đã hoàn tất (%d mục trong %s).", len(results), dur_str)
            if getattr(self, "active_session_type", "post") == "join_groups":
                messagebox.showinfo(
                    "Hoàn Tất Gia Nhập",
                    f"Đã hoàn tất ca gia nhập nhóm!\n"
                    f"• ⏱ Thời gian hoàn thành: {dur_str}\n"
                    f"• Thành công / Đã vào: {self.metric_success}\n"
                    f"• Chờ duyệt: {self.metric_pending}\n"
                    f"• Lỗi / Chặn: {self.metric_failed}",
                )
            else:
                messagebox.showinfo(
                    "Phiên Kết Thúc",
                    f"Đã hoàn tất phiên đăng!\n"
                    f"• ⏱ Thời gian hoàn thành: {dur_str}\n"
                    f"• Thành công: {self.metric_success}\n"
                    f"• Chờ duyệt: {self.metric_pending}\n"
                    f"• Lỗi: {self.metric_failed}",
                )
        self.after(0, _finish)

    def _toggle_autoscroll(self):
        self.auto_scroll_logs = not self.auto_scroll_logs
        self.btn_auto_scroll.configure(text="Tự cuộn: Bật" if self.auto_scroll_logs else "Tự cuộn: Tắt")

    def _process_log_queue(self):
        while not self.log_queue.empty():
            try:
                levelname, msg = self.log_queue.get_nowait()
                tag = levelname if levelname in ("INFO", "WARNING", "ERROR", "CRITICAL", "DEBUG") else "INFO"
                if hasattr(self, "console_text") and self.console_text:
                    self.console_text.insert(tk.END, msg + "\n", tag)
                    if self.auto_scroll_logs:
                        self.console_text.see(tk.END)
            except queue.Empty:
                break
        self.after(100, self._process_log_queue)

    def _save_env_config(self):
        lines = [
            f"EMAIL={self.entry_email.get().strip()}",
            f"PASSWORD={self.entry_pwd.get().strip()}",
            f"MIN_DELAY_BETWEEN_POSTS={self.entry_min_delay.get().strip()}",
            f"MAX_DELAY_BETWEEN_POSTS={self.entry_max_delay.get().strip()}",
            f"MAX_POSTS_PER_RUN={self.entry_max_posts.get().strip()}",
            f"COOLDOWN_HOURS={self.entry_cooldown.get().strip()}",
            f"DEBUG_PORT={self.config.debug_port}",
            f"TIMEOUT={self.config.timeout}",
        ]
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._load_config_state()
        self.lbl_sum_delay.configure(text=f"{self.config.min_delay_between_posts}–{self.config.max_delay_between_posts} giây giữa các lượt")
        self.lbl_sum_limit.configure(text=f"Tối đa {self.config.max_posts_per_run} lượt / phiên")
        self.lbl_sum_cd.configure(text=f"Chờ lại sau {self.config.cooldown_hours} giờ")
        messagebox.showinfo("Thành Công", "Đã lưu cấu hình .env thành công!")

    def _reload_env_config(self):
        self._load_config_state()
        self.entry_email.delete(0, tk.END)
        self.entry_email.insert(0, self.config.email)
        self.entry_pwd.delete(0, tk.END)
        self.entry_pwd.insert(0, self.config.password)
        self.entry_min_delay.delete(0, tk.END)
        self.entry_min_delay.insert(0, str(self.config.min_delay_between_posts))
        self.entry_max_delay.delete(0, tk.END)
        self.entry_max_delay.insert(0, str(self.config.max_delay_between_posts))
        self.entry_max_posts.delete(0, tk.END)
        self.entry_max_posts.insert(0, str(self.config.max_posts_per_run))
        self.entry_cooldown.delete(0, tk.END)
        self.entry_cooldown.insert(0, str(self.config.cooldown_hours))
        messagebox.showinfo("Nạp Lại", "Đã nạp lại cấu hình .env.")

    def _start_logout_flow(self):
        """Purges Facebook session out of Chrome profile to allow switching accounts."""
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("Đang Chạy", "Bot đang thực thi một phiên khác. Vui lòng đợi hoặc bấm Dừng.")
            return

        ans = messagebox.askyesno(
            "Đăng xuất Facebook",
            "Bạn có chắc muốn đăng xuất tài khoản Facebook hiện tại bằng cách xóa session khỏi Chrome?\n\n"
            "Sau khi xóa, bạn có thể nhập Email & Mật khẩu mới rồi bấm 'Mở Chrome đăng nhập'.",
        )
        if not ans:
            return

        try:
            bot = FacebookBot(config=self.config, custom_logger=self.logger)
            bot.logout()
            self.pill_login_status.configure(
                text="🚪 Đã xóa session (Chưa đăng nhập)",
                bg=self.t["warning_bg"],
                fg=self.t["warning"],
            )
            self.logger.info("🎉 [GUI v2] Đã xóa sạch session Facebook khỏi Chrome profile.")
            messagebox.showinfo(
                "Đã Đăng Xuất",
                "Đã đăng xuất Facebook thành công!\nPhiên lưu (session & cookies) trong Chrome đã được dọn sạch hoàn toàn.\n\n"
                "Bây giờ bạn có thể nhập Email & Mật khẩu của tài khoản mới rồi bấm 'Mở Chrome đăng nhập'.",
            )
        except Exception as e:
            self.logger.error("❌ Lỗi khi xóa session Chrome: %s", e)
            messagebox.showerror("Lỗi Đăng Xuất", f"Không thể xóa session Chrome: {e}")

    def _start_login_interactive(self):
        # Auto-sync credentials from UI entry fields if modified
        if hasattr(self, "entry_email") and hasattr(self, "entry_pwd"):
            email_val = self.entry_email.get().strip()
            pwd_val = self.entry_pwd.get().strip()
            if email_val and pwd_val and (email_val != self.config.email or pwd_val != self.config.password):
                lines = [
                    f"EMAIL={email_val}",
                    f"PASSWORD={pwd_val}",
                    f"MIN_DELAY_BETWEEN_POSTS={self.config.min_delay_between_posts}",
                    f"MAX_DELAY_BETWEEN_POSTS={self.config.max_delay_between_posts}",
                    f"MAX_POSTS_PER_RUN={self.config.max_posts_per_run}",
                    f"COOLDOWN_HOURS={self.config.cooldown_hours}",
                    f"DEBUG_PORT={self.config.debug_port}",
                    f"TIMEOUT={self.config.timeout}",
                ]
                self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self._load_config_state()

        self.pill_login_status.configure(text="🔑 Đang mở Chrome...", bg=self.t["accent_soft"], fg=self.t["accent_soft_text"])
        def _task():
            try:
                bot = FacebookBot(config=self.config, custom_logger=self.logger)
                def _ask():
                    ans = messagebox.askokcancel("Đăng nhập Facebook", "Vui lòng đăng nhập trên Chrome rồi nhấn OK.")
                    return bool(ans)
                ok = bot.login_interactive(auto_fill=True, on_ready_prompt=_ask)
                if ok:
                    self.after(0, lambda: self.pill_login_status.configure(text="✅ Đã đăng nhập", bg=self.t["success_bg"], fg=self.t["success"]))
                else:
                    self.after(0, lambda: self.pill_login_status.configure(text="Chưa kiểm tra đăng nhập", bg=self.t["warning_bg"], fg=self.t["warning"]))
            except Exception as e:
                self.after(0, lambda err=e: messagebox.showerror("Lỗi", str(err)))
        threading.Thread(target=_task, daemon=True).start()

    def _start_from_telegram(self, dry_run: bool, batch_size: Optional[int]):
        self.after(0, lambda: self._launch_execution_worker(dry_run=dry_run, check_dom=False))

    def _stop_from_telegram(self):
        self.after(0, self._confirm_stop_session)

    def _get_telegram_status(self) -> Dict[str, Any]:
        state_mgr = StateManager(str(self.history_file))
        return state_mgr.get_summary(self.config.cooldown_hours)

    def _on_closing(self):
        if self.worker_thread and self.worker_thread.is_alive():
            if not messagebox.askyesno("Thoát", "Bot đang chạy! Bạn có chắc muốn thoát?"):
                return
            self.stop_event.set()
        if self.telegram:
            self.telegram.stop_polling()
        self.destroy()


def run_gui_v2(config_path: Optional[str] = None):
    if not TKINTER_AVAILABLE:
        print("❌ Lỗi: Môi trường Python này chưa có thư viện Tkinter.")
        sys.exit(1)
    app = FacebookBotGUIV2(config_path=config_path)
    app.mainloop()


if __name__ == "__main__":
    run_gui_v2()
