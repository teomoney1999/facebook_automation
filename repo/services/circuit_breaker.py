"""
Circuit Breaker Service for Facebook Automation Bot.

Provides account protection against bans and checkpoints by implementing
an automated circuit breaker:
- Tripped immediately (OPEN) on ACTION_BLOCKED.
- Tripped when consecutive failures reach maximum threshold (default 3).
- Automatically resets consecutive failure counter on SUCCESS or PENDING_APPROVAL.
- Ignores MARKETPLACE_FORM without penalizing failure counter.
"""

from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Union

from .post_verifier import PostStatus

logger = logging.getLogger("FacebookBot.CircuitBreaker")


class CircuitBreakerState(str, Enum):
    """Circuit breaker operational state."""
    CLOSED = "CLOSED"  # Normal operation; requests allowed
    OPEN = "OPEN"      # Tripped; bot must abort immediately to protect account


class CircuitBreakerTriggeredError(Exception):
    """Raised when the circuit breaker trips, signaling emergency bot shutdown."""
    pass


class CircuitBreaker:
    """
    Manages failure tracking and automated kill-switch to protect Facebook accounts.
    """

    def __init__(self, max_consecutive_failures: int = 3):
        """
        Initialize Circuit Breaker with failure threshold.

        Args:
            max_consecutive_failures: Maximum consecutive failures before tripping (default 3).
        """
        if max_consecutive_failures < 1:
            raise ValueError(f"max_consecutive_failures must be >= 1, got {max_consecutive_failures}")

        self.max_consecutive_failures = int(max_consecutive_failures)
        self.consecutive_failures: int = 0
        self.state: CircuitBreakerState = CircuitBreakerState.CLOSED
        self.trip_reason: Optional[str] = None
        self._history: List[Dict[str, Any]] = []

    def is_tripped(self) -> bool:
        """Returns True if the circuit breaker is OPEN (tripped)."""
        return self.state == CircuitBreakerState.OPEN

    def can_proceed(self) -> bool:
        """Returns True if the bot is safe to continue posting (CLOSED)."""
        return self.state == CircuitBreakerState.CLOSED

    def record_result(
        self,
        status: Union[PostStatus, str],
        context: str = "",
        raise_on_trip: bool = False,
    ) -> bool:
        """
        Records the outcome of a post attempt and updates breaker state.

        Rules:
        1. SUCCESS / PENDING_APPROVAL: Reset consecutive_failures = 0.
        2. MARKETPLACE_FORM: Bypassed safely (does not increment failure count).
        3. ACTION_BLOCKED: Immediately trips breaker to OPEN.
        4. FAILED: Increments consecutive_failures. If >= threshold -> trips breaker to OPEN.

        Args:
            status: PostStatus enum or string value.
            context: Contextual string (e.g. group URL or error details) for logging.
            raise_on_trip: If True, raises CircuitBreakerTriggeredError immediately if tripped.

        Returns:
            True if the circuit breaker is now TRIPPED (OPEN), False otherwise.

        Raises:
            CircuitBreakerTriggeredError: If tripped and raise_on_trip is True.
        """
        # Normalize status to enum or upper string
        if isinstance(status, PostStatus):
            status_val = status.value
        else:
            status_val = str(status).strip().upper()

        self._history.append({
            "status": status_val,
            "context": context,
        })

        if status_val in (PostStatus.SUCCESS.value, PostStatus.PENDING_APPROVAL.value):
            if self.consecutive_failures > 0:
                logger.info(
                    "Circuit breaker: Post outcome '%s' received. Resetting consecutive failures (%d -> 0).",
                    status_val,
                    self.consecutive_failures,
                )
            self.consecutive_failures = 0
            return False

        if status_val == PostStatus.MARKETPLACE_FORM.value:
            logger.info("Circuit breaker: MARKETPLACE_FORM ignored. Consecutive failures remain at %d.", self.consecutive_failures)
            return False

        if status_val == PostStatus.ACTION_BLOCKED.value:
            self.state = CircuitBreakerState.OPEN
            self.trip_reason = f"CRITICAL: Account action blocked by Facebook. Context: {context}"
            logger.critical("🚨 CIRCUIT BREAKER TRIPPED! %s", self.trip_reason)
            if raise_on_trip:
                raise CircuitBreakerTriggeredError(self.trip_reason)
            return True

        if status_val in (PostStatus.FAILED.value, PostStatus.UNKNOWN.value):
            self.consecutive_failures += 1
            logger.warning(
                "Circuit breaker: Failure/Unknown recorded (%d/%d consecutive). Context: %s",
                self.consecutive_failures,
                self.max_consecutive_failures,
                context,
            )
            if self.consecutive_failures >= self.max_consecutive_failures:
                self.state = CircuitBreakerState.OPEN
                self.trip_reason = (
                    f"Threshold exceeded: {self.consecutive_failures} consecutive failures "
                    f"(max allowed: {self.max_consecutive_failures}). Context: {context}"
                )
                logger.critical("🚨 CIRCUIT BREAKER TRIPPED! %s", self.trip_reason)
                if raise_on_trip:
                    raise CircuitBreakerTriggeredError(self.trip_reason)
                return True
            return False

        # Any unrecognized status is treated conservatively as a failure
        logger.warning("Circuit breaker: Unrecognized status '%s' treated as failure.", status_val)
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.state = CircuitBreakerState.OPEN
            self.trip_reason = f"Threshold exceeded via unrecognized statuses. Context: {context}"
            if raise_on_trip:
                raise CircuitBreakerTriggeredError(self.trip_reason)
            return True
        return False

    def trip(self, reason: str, raise_on_trip: bool = False) -> None:
        """
        Manually trips the circuit breaker (e.g. on session expiration or network cutoff).

        Args:
            reason: Diagnostic explanation for tripping.
            raise_on_trip: If True, immediately raises CircuitBreakerTriggeredError.
        """
        self.state = CircuitBreakerState.OPEN
        self.trip_reason = f"Manual trip: {reason}"
        logger.critical("🚨 CIRCUIT BREAKER TRIPPED MANUALLY: %s", self.trip_reason)
        if raise_on_trip:
            raise CircuitBreakerTriggeredError(self.trip_reason)

    def reset(self) -> None:
        """Resets the circuit breaker to clean CLOSED state."""
        self.state = CircuitBreakerState.CLOSED
        self.consecutive_failures = 0
        self.trip_reason = None
        logger.info("Circuit breaker manually reset to CLOSED state.")
