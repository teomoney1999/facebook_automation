"""
Services Module for Facebook Automation Bot.

Exports:
- State & History: StateManager, PostRecord, normalize_url
- Post Verification: PostStatus, PostVerificationResult, PostVerifier
- Circuit Breaker: CircuitBreakerState, CircuitBreakerTriggeredError, CircuitBreaker
"""

from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitBreakerTriggeredError,
)
from .group_verifier import (
    GroupMembershipVerifier,
    MembershipStatus,
)
from .group_joiner import (
    GroupJoiner,
    JoinResult,
)
from .job_dispatcher import (
    JobDispatcher,
    JobItem,
    JobStatus,
)
from .post_verifier import (
    PostStatus,
    PostVerificationResult,
    PostVerifier,
)
from .selector_sync import sync_selectors
from .state_manager import (
    PostRecord,
    StateManager,
    normalize_url,
)
from .telegram_controller import TelegramController
from .updater import (
    UpdateInfo,
    check_for_updates,
    download_update,
    is_newer_version,
    launch_silent_installer,
    parse_version,
)

__all__ = [
    "StateManager",
    "PostRecord",
    "normalize_url",
    "PostStatus",
    "PostVerificationResult",
    "PostVerifier",
    "CircuitBreakerState",
    "CircuitBreakerTriggeredError",
    "CircuitBreaker",
    "TelegramController",
    "MembershipStatus",
    "GroupMembershipVerifier",
    "GroupJoiner",
    "JoinResult",
    "JobDispatcher",
    "JobItem",
    "JobStatus",
    "sync_selectors",
    "UpdateInfo",
    "check_for_updates",
    "download_update",
    "launch_silent_installer",
    "is_newer_version",
    "parse_version",
]
