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
from .post_verifier import (
    PostStatus,
    PostVerificationResult,
    PostVerifier,
)
from .state_manager import (
    PostRecord,
    StateManager,
    normalize_url,
)
from .telegram_controller import TelegramController

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
]
