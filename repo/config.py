"""
Configuration Management for Facebook Automation Bot.

Provides an immutable, validated, single source of truth for bot parameters,
timing delays, anti-ban thresholds, and filesystem paths.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when configuration is missing, invalid, or malformed."""
    pass


@dataclass(frozen=True)
class BotConfig:
    """Immutable configuration container for FacebookBot."""

    # Credentials (validated fail-fast)
    email: str
    password: str

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

    # Absolute Filesystem Paths
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent)
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

        # Compute derived paths (object.__setattr__ used for frozen dataclass)
        resolved_base = self.base_dir.resolve()
        resolved_logs = resolved_base / "logs"
        resolved_screenshots = resolved_logs / "screenshots"
        resolved_json = resolved_base / "json"
        resolved_profile = resolved_base / "FacebookBotProfile"

        object.__setattr__(self, "base_dir", resolved_base)
        object.__setattr__(self, "logs_dir", resolved_logs)
        object.__setattr__(self, "screenshots_dir", resolved_screenshots)
        object.__setattr__(self, "json_dir", resolved_json)
        object.__setattr__(self, "profile_dir", resolved_profile)

        # Ensure runtime directories exist
        resolved_logs.mkdir(parents=True, exist_ok=True)
        resolved_screenshots.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        # Prevent credential leak in logs/terminal
        tg_info = f", telegram_admin='{self.telegram_admin_id}'" if self.telegram_admin_id else ""
        return (
            f"BotConfig(email='{self.email}', password='***', "
            f"delays=({self.min_delay_between_posts}s, {self.max_delay_between_posts}s), "
            f"max_posts_per_run={self.max_posts_per_run}, timeout={self.timeout}s, "
            f"debug_port={self.debug_port}, cooldown_hours={self.cooldown_hours}h{tg_info}, "
            f"base_dir='{self.base_dir}')"
        )

    @classmethod
    def from_env(
        cls,
        env_path: Optional[str] = None,
        validate_credentials: bool = True
    ) -> "BotConfig":
        """Load and validate configuration from .env or environment variables."""
        repo_dir = Path(__file__).resolve().parent
        env_file = (Path(env_path).resolve() if env_path else (repo_dir / ".env"))

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
            base_dir=env_file.parent if (env_path and env_file.parent != repo_dir) else repo_dir,
        )


_global_config: Optional[BotConfig] = None


def get_config(reload: bool = False, env_path: Optional[str] = None) -> BotConfig:
    """Retrieve or initialize global immutable BotConfig instance."""
    global _global_config
    if _global_config is None or reload:
        _global_config = BotConfig.from_env(env_path=env_path)
    return _global_config
