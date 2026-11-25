"""
State and History Manager for Facebook Automation Bot.

Provides persistent tracking of group posting status, 24-hour cooldown
filtering to prevent anti-spam triggers, batch slicing, and crash-resilient
atomic JSON file storage.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union
import urllib.parse

try:
    from helpers.common_helpers import format_duration
except ImportError:
    try:
        from repo.helpers.common_helpers import format_duration
    except ImportError:
        def format_duration(seconds: float) -> str:
            sec = int(round(seconds))
            if sec <= 0:
                return "0 giây"
            if sec < 60:
                return f"{sec} giây"
            mins = sec // 60
            rem_sec = sec % 60
            if mins < 60:
                return f"{mins} phút {rem_sec} giây" if rem_sec else f"{mins} phút"
            hrs = mins // 60
            rem_mins = mins % 60
            return f"{hrs} giờ {rem_mins} phút"

logger = logging.getLogger("FacebookBot.StateManager")


def normalize_url(url: str) -> str:
    """Normalize Facebook group URL by stripping query parameters, fragments, trailing slashes."""
    if not url or not isinstance(url, str):
        return ""
    cleaned = url.strip()
    if "://" not in cleaned and ("facebook.com" in cleaned or "fb.com" in cleaned):
        cleaned = f"https://{cleaned}"
    # Strip URL fragments or query parameters
    cleaned = cleaned.split("?")[0].split("#")[0].rstrip("/")
    if "://" in cleaned:
        try:
            parsed = urllib.parse.urlsplit(cleaned)
            netloc = parsed.netloc.lower()
            if not ("facebook.com" in netloc or "fb.com" in netloc):
                logger.warning("Bỏ qua URL không thuộc Facebook: %s", url)
                return ""
            path = parsed.path.rstrip("/")
            return f"https://www.facebook.com{path}"
        except Exception:
            return ""
    return cleaned


@dataclass
class PostRecord:
    """Represents the historical state of a Facebook group post."""
    group_url: str
    last_posted_at: str  # ISO 8601 UTC string
    status: str
    post_count: int = 1
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PostRecord":
        return cls(
            group_url=data.get("group_url", ""),
            last_posted_at=data.get("last_posted_at", ""),
            status=data.get("status", "UNKNOWN"),
            post_count=data.get("post_count", 1),
            error=data.get("error")
        )


class StateManager:
    """
    Manages persistent post history with atomic writes and anti-ban cooldown rules.
    """

    def __init__(self, history_file_path: Union[str, Path]):
        self.history_path = Path(history_file_path).resolve()
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history: Dict[str, PostRecord] = {}
        self.sessions: List[Dict[str, Any]] = []
        self.account_blocked_until: Optional[str] = None
        self.is_corrupted: bool = False
        self.load_history()

    def load_history(self) -> Dict[str, PostRecord]:
        """Load history and sessions from JSON file. Initializes an empty history if file does not exist."""
        if not self.history_path.exists():
            logger.info("History file %s not found. Initializing empty history.", self.history_path)
            self.history = {}
            self.sessions = []
            self.save_history()
            return self.history

        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            raw_history = data.get("history", {}) if isinstance(data, dict) else {}
            self.account_blocked_until = data.get("account_blocked_until") if isinstance(data, dict) else None
            self.sessions = data.get("sessions", []) if isinstance(data, dict) else []
            loaded: Dict[str, PostRecord] = {}
            for raw_url, record_dict in raw_history.items():
                norm = normalize_url(raw_url)
                if norm and isinstance(record_dict, dict):
                    loaded[norm] = PostRecord.from_dict(record_dict)
            self.history = loaded

            # If sessions list is empty but historical records exist, cluster them into baseline sessions
            if not self.sessions and self.history:
                self.sessions = self._cluster_legacy_history_into_sessions()

            logger.info("Loaded %d post records (%d sessions) from %s", len(self.history), len(self.sessions), self.history_path.name)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to parse history file %s: %s. Creating backup.", self.history_path, exc)
            self.is_corrupted = True
            self._backup_corrupted_file()
            self.history = {}
            self.sessions = []
            self.save_history()

        return self.history

    def _cluster_legacy_history_into_sessions(self) -> List[Dict[str, Any]]:
        """Clusters existing history records into baseline sessions by timestamp proximity."""
        if not self.history:
            return []
        records = [r for r in self.history.values() if r.last_posted_at]
        if not records:
            return []
        records.sort(key=lambda r: r.last_posted_at or "")

        groups: List[List[PostRecord]] = []
        current: List[PostRecord] = [records[0]]

        for r in records[1:]:
            try:
                t_prev = datetime.fromisoformat(current[-1].last_posted_at)  # type: ignore
                t_curr = datetime.fromisoformat(r.last_posted_at)  # type: ignore
                diff = abs((t_curr - t_prev).total_seconds())
            except Exception:
                diff = 0
            if diff > 900:  # Gap of > 15m indicates a separate posting run
                groups.append(current)
                current = [r]
            else:
                current.append(r)
        groups.append(current)

        clustered: List[Dict[str, Any]] = []
        for idx, group_recs in enumerate(groups, 1):
            t_start = group_recs[0].last_posted_at or ""
            t_end = group_recs[-1].last_posted_at or ""
            try:
                dur = max(15.0, (datetime.fromisoformat(t_end) - datetime.fromisoformat(t_start)).total_seconds())
            except Exception:
                dur = 30.0

            results = [
                {
                    "link": r.group_url,
                    "status": r.status,
                    "details": r.error or "—",
                    "time": r.last_posted_at,
                }
                for r in group_recs
            ]
            succ = sum(1 for r in results if r["status"] == "SUCCESS")
            pend = sum(1 for r in results if r["status"] == "PENDING_APPROVAL")
            fail = len(results) - succ - pend

            clustered.append({
                "id": idx,
                "type": "POST",
                "start_time": t_start,
                "end_time": t_end,
                "duration_seconds": round(dur, 1),
                "duration_formatted": format_duration(dur),
                "total_groups": len(results),
                "success_count": succ,
                "pending_count": pend,
                "failed_count": fail,
                "results": results,
            })
        return clustered

    def record_session(
        self,
        session_type: str,
        results: List[Dict[str, Any]],
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a completed session/run with duration and group results."""
        if not hasattr(self, "sessions") or self.sessions is None:
            self.sessions = []

        now_utc = datetime.now(timezone.utc)
        end_str = end_time or now_utc.isoformat()
        start_str = start_time or end_str

        if duration_seconds is None:
            try:
                t0 = datetime.fromisoformat(start_str)
                t1 = datetime.fromisoformat(end_str)
                duration_seconds = max(0.0, (t1 - t0).total_seconds())
            except Exception:
                duration_seconds = 0.0

        dur_str = format_duration(duration_seconds)
        success_cnt = sum(1 for r in results if r.get("status") in ("SUCCESS", "JOINED", "ALREADY_JOINED", "JOINED_SUCCESS"))
        pending_cnt = sum(1 for r in results if r.get("status") in ("PENDING_APPROVAL", "REQUESTED", "JOIN_REQUEST_SENT"))
        failed_cnt = len(results) - success_cnt - pending_cnt

        session_id = len(self.sessions) + 1
        session_rec = {
            "id": session_id,
            "type": session_type,
            "start_time": start_str,
            "end_time": end_str,
            "duration_seconds": round(duration_seconds, 1),
            "duration_formatted": dur_str,
            "total_groups": len(results),
            "success_count": success_cnt,
            "pending_count": pending_cnt,
            "failed_count": max(0, failed_cnt),
            "results": results,
        }
        self.sessions.append(session_rec)
        self.save_history()
        logger.info(
            "Recorded session #%d (%s): %d groups in %s (success: %d, pending: %d, failed: %d)",
            session_id, session_type, len(results), dur_str, success_cnt, pending_cnt, failed_cnt
        )
        return session_rec

    def get_sessions(self) -> List[Dict[str, Any]]:
        """Returns list of recorded sessions, newest first."""
        if not hasattr(self, "sessions") or not self.sessions:
            return []
        return list(reversed(self.sessions))

    def _backup_corrupted_file(self) -> None:
        """Back up corrupted history file to avoid data loss before resetting."""
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_path = self.history_path.with_suffix(f".corrupted_{timestamp}.json")
            if self.history_path.exists():
                os.replace(self.history_path, backup_path)
                logger.warning("Corrupted history backed up to %s", backup_path)
        except OSError as e:
            logger.error("Failed to backup corrupted file: %s", e)

    def save_history(self) -> None:
        """
        Atomically persist history to JSON file using a temporary file and os.replace.
        Guarantees zero file corruption on unexpected process termination.
        """
        payload = {
            "version": "1.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "account_blocked_until": self.account_blocked_until,
            "history": {url: record.to_dict() for url, record in self.history.items()},
            "sessions": getattr(self, "sessions", [])
        }

        # Write to temporary file in the same directory (required for atomic os.replace across filesystems)
        dir_name = self.history_path.parent
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
                temp_path = Path(tf.name)
                json.dump(payload, tf, indent=4, ensure_ascii=False)
                tf.flush()
                os.fsync(tf.fileno())

            # Atomic swap
            os.replace(temp_path, self.history_path)
            temp_path = None
            logger.debug("Successfully saved %d history records to %s", len(self.history), self.history_path.name)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def mark_account_blocked(self, duration_hours: float = 24.0) -> None:
        """Mark account as temporarily blocked by Facebook until cooldown expires."""
        unblock_time = datetime.now(timezone.utc) + timedelta(hours=duration_hours)
        self.account_blocked_until = unblock_time.isoformat()
        self.save_history()
        logger.critical("🚨 Đã lưu trạng thái ACTION_BLOCKED của tài khoản đến: %s", self.account_blocked_until)

    def is_account_blocked(self, now: Optional[datetime] = None) -> Tuple[bool, float]:
        """Check if account is currently marked blocked from previous runs."""
        if not self.account_blocked_until:
            return False, 0.0
        try:
            blocked_until = datetime.fromisoformat(self.account_blocked_until)
            if blocked_until.tzinfo is None:
                blocked_until = blocked_until.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False, 0.0

        current_time = now if now is not None else datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        if current_time < blocked_until:
            remaining = (blocked_until - current_time).total_seconds()
            return True, remaining
        return False, 0.0

    def record_post(self, group_url: str, status: str, error: Optional[str] = None) -> PostRecord:
        """
        Record a post attempt for a group URL and immediately persist to history.
        """
        norm_url = normalize_url(group_url)
        now_str = datetime.now(timezone.utc).isoformat()

        prev_record = self.history.get(norm_url)
        count = prev_record.post_count + 1 if prev_record else 1

        record = PostRecord(
            group_url=norm_url,
            last_posted_at=now_str,
            status=status,
            post_count=count,
            error=error
        )
        self.history[norm_url] = record
        self.save_history()
        logger.info("Recorded post status '%s' for group %s (total: %d)", status, norm_url, count)
        return record

    def is_in_cooldown(
        self,
        group_url: str,
        cooldown_hours: float = 24.0,
        now: Optional[datetime] = None,
        only_successful: bool = True
    ) -> Tuple[bool, float]:
        """
        Check if a group is currently in cooldown period.

        Returns:
            Tuple[bool, float]: (is_cooldown, remaining_seconds)
            If remaining_seconds <= 0, the group is eligible for posting.
        """
        norm_url = normalize_url(group_url)
        record = self.history.get(norm_url)
        if not record or not record.last_posted_at:
            return False, 0.0

        if only_successful and record.status not in ("SUCCESS", "PENDING_APPROVAL", "ACTION_BLOCKED", "UNKNOWN", "IN_PROGRESS"):
            # Only states that indicate successful, pending, or safety-critical posts trigger cooldown
            return False, 0.0

        try:
            last_posted = datetime.fromisoformat(record.last_posted_at)
            # Ensure timezone awareness
            if last_posted.tzinfo is None:
                last_posted = last_posted.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False, 0.0

        current_time = now if now is not None else datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)

        elapsed = (current_time - last_posted).total_seconds()
        cooldown_seconds = cooldown_hours * 3600.0

        if elapsed < cooldown_seconds:
            remaining = cooldown_seconds - elapsed
            return True, remaining

        return False, 0.0

    def filter_eligible_groups(
        self,
        group_urls: List[str],
        cooldown_hours: float = 24.0,
        now: Optional[datetime] = None
    ) -> Tuple[List[str], List[dict]]:
        """
        Filter a list of group URLs into eligible and in-cooldown groups.

        Returns:
            Tuple[List[str], List[dict]]: (eligible_urls, skipped_details)
        """
        eligible: List[str] = []
        skipped: List[dict] = []

        seen = set()
        for raw_url in group_urls:
            norm_url = normalize_url(raw_url)
            if not norm_url or norm_url in seen:
                continue
            seen.add(norm_url)

            in_cd, remaining = self.is_in_cooldown(norm_url, cooldown_hours=cooldown_hours, now=now)
            if in_cd:
                remaining_hours = round(remaining / 3600.0, 1)
                skipped.append({
                    "url": norm_url,
                    "remaining_seconds": remaining,
                    "remaining_hours": remaining_hours,
                    "last_posted_at": self.history[norm_url].last_posted_at,
                    "status": self.history[norm_url].status
                })
            else:
                eligible.append(norm_url)

        return eligible, skipped

    def get_batch_targets(
        self,
        all_group_urls: List[str],
        max_batch_size: int = 10,
        cooldown_hours: float = 24.0,
        now: Optional[datetime] = None
    ) -> List[str]:
        """
        Obtain the sliced list of target groups for the current execution batch.
        Enforces deduplication, 24h cooldown, and batch slicing limit.
        """
        if max_batch_size <= 0:
            raise ValueError(f"max_batch_size phải là số nguyên dương (> 0), nhận được: {max_batch_size}")

        eligible, skipped = self.filter_eligible_groups(all_group_urls, cooldown_hours=cooldown_hours, now=now)
        logger.info(
            "Group filtering complete: %d total, %d in cooldown, %d eligible.",
            len(all_group_urls), len(skipped), len(eligible)
        )
        if not eligible:
            logger.info("Fast-Exit triggered: No eligible groups found (all in cooldown or list empty).")
            return []

        batch = eligible[:max_batch_size]
        logger.info("Selected %d groups for current batch (max limit: %d).", len(batch), max_batch_size)
        return batch

    def get_summary(self, cooldown_hours: float = 24.0) -> Dict[str, Any]:
        """
        Calculates aggregate statistics from the current history.
        """
        total_tracked = len(self.history)
        successful = sum(getattr(r, "post_count", 1) for r in self.history.values() if r.status in ("SUCCESS", "PENDING_APPROVAL"))
        in_cooldown = sum(1 for u in self.history if self.is_in_cooldown(u, cooldown_hours=cooldown_hours)[0])
        
        last_posted_group = "N/A"
        last_posted_time = "N/A"
        if self.history:
            sorted_records = sorted(
                self.history.values(),
                key=lambda r: r.last_posted_at or "",
                reverse=True,
            )
            if sorted_records and sorted_records[0].last_posted_at:
                last_posted_group = sorted_records[0].group_url
                last_posted_time = sorted_records[0].last_posted_at

        return {
            "total_groups_tracked": total_tracked,
            "total_successful_posts": successful,
            "active_cooldown_count": in_cooldown,
            "last_posted_group": last_posted_group,
            "last_posted_time": last_posted_time,
        }

