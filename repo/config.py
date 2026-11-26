"""
Configuration Management for Facebook Automation Bot.

Provides an immutable, validated, single source of truth for bot parameters,
timing delays, anti-ban thresholds, and filesystem paths.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import sys
from typing import Optional
from dotenv import load_dotenv


def get_user_data_dir(app_name: str = "FacebookAutomationBot", base_dir: Optional[Path] = None) -> Path:
    """
    Resolves persistent user data directory (Zero Data Loss across app updates).

    Priority:
    1. FACEBOOK_BOT_DATA_DIR environment variable (explicit override/testing)
    2. Frozen package mode (PyInstaller sys.frozen == True):
       - Windows: %LOCALAPPDATA%\\<app_name>
       - macOS: ~/Library/Application Support/<app_name>
       - Linux: ~/.local/share/<app_name> (or $XDG_DATA_HOME/<app_name>)
    3. Developer mode: base_dir or repo directory
    """
    env_override = os.environ.get("FACEBOOK_BOT_DATA_DIR")
    if env_override and env_override.strip():
        target = Path(env_override.strip()).resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        target = base / app_name
        target.mkdir(parents=True, exist_ok=True)
        return target

    return base_dir.resolve() if base_dir else Path(__file__).resolve().parent


def get_default_env_path(base_dir: Optional[Path] = None) -> Path:
    """Returns the default path to .env file based on environment (frozen vs dev)."""
    resolved_base = base_dir.resolve() if base_dir else Path(__file__).resolve().parent
    data_dir = get_user_data_dir(base_dir=resolved_base)
    if (data_dir / ".env").exists():
        return data_dir / ".env"
    return resolved_base / ".env"


def _seed_template_files(src_base: Path, dst_data: Path) -> None:
    """
    Copies default JSON templates and starter .env to user data directory
    if they do not already exist, preventing missing file errors on fresh installs.
    """
    dst_json = dst_data / "json"
    dst_json.mkdir(parents=True, exist_ok=True)
    src_json = src_base / "json"

    if src_json.is_dir():
        for filename in ("selectors.json", "links.json", "posts.json", "history.json", "groups_meta.json"):
            dst_file = dst_json / filename
            src_file = src_json / filename
            if not dst_file.exists() and src_file.exists():
                try:
                    shutil.copy2(src_file, dst_file)
                except Exception:
                    pass

    # Ensure links.json exists with valid empty schema contract if missing
    dst_links = dst_json / "links.json"
    if not dst_links.exists():
        try:
            dst_links.write_text('{\n    "groups": [],\n    "target_links": []\n}\n', encoding="utf-8")
        except Exception:
            pass

    dst_env = dst_data / ".env"
    if not dst_env.exists():
        src_env = src_base / ".env"
        src_env_ex = src_base / ".env.example"
        if src_env.exists():
            try:
                shutil.copy2(src_env, dst_env)
            except Exception:
                pass
        elif src_env_ex.exists():
            try:
                shutil.copy2(src_env_ex, dst_env)
            except Exception:
                pass


class ConfigurationError(ValueError):
    """Raised when configuration is missing, invalid, or malformed."""
    pass


APP_VERSION = "2.0.0"
DEFAULT_UPDATE_CHECK_URL = (
    "https://raw.githubusercontent.com/teomoney1999/facebook_automation/main/version.json"
)


@dataclass(frozen=True)
class BotConfig:
    """Immutable configuration container for FacebookBot."""

    # Application Version & Update
    app_version: str = APP_VERSION
    update_url: Optional[str] = DEFAULT_UPDATE_CHECK_URL

    # Credentials (validated fail-fast)
    email: str = ""
    password: str = ""

    # Timing & Anti-ban Parameters
    min_delay_between_posts: float = 45.0
    max_delay_between_posts: float = 90.0
    max_posts_per_run: int = 10
    timeout: int = 15
    debug_port: int = 9224
    cooldown_hours: int = 24
    strip_newlines: bool = False

    # Group Joining Timing & Batch Parameters
    join_delay_min: float = 15.0
    join_delay_max: float = 35.0
    join_limit: int = 10

    # Telegram Remote Control (Optional)
    telegram_bot_token: Optional[str] = None
    telegram_admin_id: Optional[str] = None

    # Headless / Silent Browser Execution
    headless: bool = False
    headless_mode: str = "new"

    # Absolute Filesystem Paths
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent)
    data_dir: Optional[Path] = None
    logs_dir: Path = field(init=False)
    screenshots_dir: Path = field(init=False)
    json_dir: Path = field(init=False)
    profile_dir: Path = field(init=False)

    def __post_init__(self):
        # Fail fast on credentials
        if not self.email or not self.email.strip():
            raise ConfigurationError("EMAIL is missing or empty. Please set it in .env")
        if not self.password or not self.password.strip():
            raise ConfigurationError("PASSWORD is missing or empty. Please set it in .env")

        # Validate anti-ban & timing constraints
        if self.min_delay_between_posts < 0:
            raise ConfigurationError("MIN_DELAY_BETWEEN_POSTS must be non-negative.")
        if self.max_delay_between_posts < self.min_delay_between_posts:
            raise ConfigurationError(
                f"MAX_DELAY_BETWEEN_POSTS ({self.max_delay_between_posts}s) "
                f"cannot be smaller than MIN_DELAY_BETWEEN_POSTS ({self.min_delay_between_posts}s)."
            )
        if self.max_posts_per_run <= 0:
            raise ConfigurationError("MAX_POSTS_PER_RUN must be greater than 0.")
        if self.timeout <= 0:
            raise ConfigurationError("TIMEOUT must be greater than 0.")
        if self.cooldown_hours <= 0:
            raise ConfigurationError("COOLDOWN_HOURS must be greater than 0.")
        if self.join_delay_min < 0:
            raise ConfigurationError("JOIN_DELAY_MIN must be non-negative.")
        if self.join_delay_max < self.join_delay_min:
            raise ConfigurationError(
                f"JOIN_DELAY_MAX ({self.join_delay_max}s) cannot be smaller than JOIN_DELAY_MIN ({self.join_delay_min}s)."
            )
        if self.join_limit <= 0:
            raise ConfigurationError("JOIN_LIMIT must be greater than 0.")
        if not (1024 <= self.debug_port <= 65535):
            raise ConfigurationError(f"DEBUG_PORT must be between 1024 and 65535, got {self.debug_port}.")

        # Validate Telegram credentials pairing if configured
        has_token = bool(self.telegram_bot_token and self.telegram_bot_token.strip())
        has_admin = bool(self.telegram_admin_id and self.telegram_admin_id.strip())
        if has_token != has_admin:
            raise ConfigurationError(
                "Both TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_ID must be provided together if Telegram is enabled."
            )

        # Validate headless mode
        if self.headless_mode not in ("new", "offscreen"):
            raise ConfigurationError(
                f"HEADLESS_MODE must be either 'new' or 'offscreen', got '{self.headless_mode}'."
            )

        # Compute derived paths (object.__setattr__ used for frozen dataclass)
        resolved_base = self.base_dir.resolve()
        resolved_data = (
            self.data_dir.resolve()
            if self.data_dir is not None
            else get_user_data_dir(base_dir=resolved_base)
        )
        resolved_logs = resolved_data / "logs"
        resolved_screenshots = resolved_logs / "screenshots"
        resolved_json = resolved_data / "json"
        resolved_profile = resolved_data / "FacebookBotProfile"

        object.__setattr__(self, "base_dir", resolved_base)
        object.__setattr__(self, "data_dir", resolved_data)
        object.__setattr__(self, "logs_dir", resolved_logs)
        object.__setattr__(self, "screenshots_dir", resolved_screenshots)
        object.__setattr__(self, "json_dir", resolved_json)
        object.__setattr__(self, "profile_dir", resolved_profile)

        # Ensure runtime directories exist
        resolved_data.mkdir(parents=True, exist_ok=True)
        resolved_logs.mkdir(parents=True, exist_ok=True)
        resolved_screenshots.mkdir(parents=True, exist_ok=True)
        resolved_json.mkdir(parents=True, exist_ok=True)

        # Auto-seed template files if data_dir is separate from base_dir
        if resolved_data != resolved_base:
            _seed_template_files(resolved_base, resolved_data)

    def __repr__(self) -> str:
        # Prevent credential leak in logs/terminal
        tg_info = f", telegram_admin='{self.telegram_admin_id}'" if self.telegram_admin_id else ""
        return (
            f"BotConfig(email='{self.email}', password='***', "
            f"delays=({self.min_delay_between_posts}s, {self.max_delay_between_posts}s), "
            f"max_posts_per_run={self.max_posts_per_run}, timeout={self.timeout}s, "
            f"debug_port={self.debug_port}, cooldown_hours={self.cooldown_hours}h{tg_info}, "
            f"headless={self.headless} ({self.headless_mode}), "
            f"base_dir='{self.base_dir}', data_dir='{self.data_dir}')"
        )

    @classmethod
    def from_env(
        cls,
        env_path: Optional[str] = None,
        validate_credentials: bool = True,
        data_dir: Optional[Path] = None,
    ) -> "BotConfig":
        """Load and validate configuration from .env or environment variables."""
        repo_dir = Path(__file__).resolve().parent
        inferred_base = (
            Path(env_path).resolve().parent
            if (env_path and Path(env_path).resolve().parent != repo_dir)
            else repo_dir
        )
        resolved_data_dir = (
            Path(data_dir).resolve()
            if data_dir
            else get_user_data_dir(base_dir=inferred_base)
        )

        if env_path:
            env_file = Path(env_path).resolve()
        elif (resolved_data_dir / ".env").exists():
            env_file = resolved_data_dir / ".env"
        else:
            env_file = repo_dir / ".env"

        if env_file.exists():
            load_dotenv(dotenv_path=env_file, override=False)
        else:
            load_dotenv()

        email = os.getenv("EMAIL", "").strip()
        password = os.getenv("PASSWORD", "").strip()

        if not validate_credentials:
            email = email or "dry_run@example.com"
            password = password or "dummy_password"

        def _get_float(key: str, default: float) -> float:
            val = os.getenv(key)
            if val is not None and val.strip() != "":
                try:
                    return float(val.strip())
                except ValueError:
                    raise ConfigurationError(
                        f"Environment variable '{key}' must be a valid float, got '{val}'."
                    )
            return default

        def _get_int(key: str, default: int) -> int:
            val = os.getenv(key)
            if val is not None and val.strip() != "":
                try:
                    return int(val.strip())
                except ValueError:
                    raise ConfigurationError(
                        f"Environment variable '{key}' must be a valid integer, got '{val}'."
                    )
            return default

        return cls(
            email=email,
            password=password,
            min_delay_between_posts=_get_float("MIN_DELAY_BETWEEN_POSTS", 45.0),
            max_delay_between_posts=_get_float("MAX_DELAY_BETWEEN_POSTS", 90.0),
            max_posts_per_run=_get_int("MAX_POSTS_PER_RUN", 10),
            timeout=_get_int("TIMEOUT", 15),
            debug_port=_get_int("DEBUG_PORT", 9224),
            cooldown_hours=_get_int("COOLDOWN_HOURS", 24),
            join_delay_min=_get_float("JOIN_DELAY_MIN", 15.0),
            join_delay_max=_get_float("JOIN_DELAY_MAX", 35.0),
            join_limit=_get_int("JOIN_LIMIT", 10),
            strip_newlines=os.getenv("STRIP_NEWLINES", "false").strip().lower() in ("true", "1", "yes"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None,
            telegram_admin_id=os.getenv("TELEGRAM_ADMIN_ID", "").strip() or None,
            headless=os.getenv("HEADLESS", "false").strip().lower() in ("true", "1", "yes"),
            headless_mode=os.getenv("HEADLESS_MODE", "new").strip().lower() or "new",
            update_url=os.getenv("UPDATE_CHECK_URL", "").strip() or DEFAULT_UPDATE_CHECK_URL,
            base_dir=inferred_base,
            data_dir=resolved_data_dir,
        )


_global_config: Optional[BotConfig] = None


def get_config(
    reload: bool = False,
    env_path: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> BotConfig:
    """Retrieve or initialize global immutable BotConfig instance."""
    global _global_config
    if _global_config is None or reload:
        _global_config = BotConfig.from_env(env_path=env_path, data_dir=data_dir)
    return _global_config
