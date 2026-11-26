"""
Job Dispatcher & Task Queue Engine for Facebook Automation Bot.

Provides thread-safe multi-task orchestration, non-blocking execution,
independent job cancellation, and real-time progress callbacks for the Task Manager UI.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class JobItem:
    """Represents a discrete bot execution task in the dispatcher."""

    job_id: str
    name: str
    job_type: str  # "POST_GROUPS", "JOIN_GROUPS", "SCAN_META", "CHECK_DOM"
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0  # 0.0 to 100.0%
    current_step: int = 0
    total_steps: int = 1
    details: str = "Đang trong hàng đợi..."
    created_at: float = field(default_factory=time.time)
    stop_event: threading.Event = field(default_factory=threading.Event)
    error_message: Optional[str] = None
    target_fn: Optional[Callable[..., Any]] = None
    kwargs: Dict[str, Any] = field(default_factory=dict)
    duration_str: str = "—"

    def update_progress(self, current: int, total: int, details: Optional[str] = None):
        """Updates progress metrics thread-safely."""
        self.current_step = current
        self.total_steps = max(total, 1)
        self.progress = min(100.0, max(0.0, (current / self.total_steps) * 100.0))
        if details:
            self.details = details

    def cancel(self):
        """Signals independent cancellation for this job."""
        self.stop_event.set()
        if self.status == JobStatus.QUEUED:
            self.status = JobStatus.CANCELLED
            self.details = "Đã hủy khi đang trong hàng đợi."


class JobDispatcher:
    """
    Central Coordinator managing task queues, background worker execution,
    and UI Task Manager observability.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self._jobs: Dict[str, JobItem] = {}
        self._queue: List[str] = []
        self._lock = threading.RLock()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._listeners: List[Callable[[JobItem], Any]] = []
        self._logger = logger or logging.getLogger("JobDispatcher")
        self._counter = 0

    @property
    def is_running(self) -> bool:
        """Returns True if dispatcher worker thread is active."""
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def submit_job(
        self,
        name: str,
        job_type: str,
        target_fn: Callable[..., Any],
        total_steps: int = 1,
        details: str = "Sẵn sàng chạy...",
        **kwargs: Any,
    ) -> JobItem:
        """
        Enqueues a new job and guarantees worker thread is running.
        Non-blocking: returns JobItem immediately.
        """
        with self._lock:
            self._counter += 1
            job_id = f"JOB-{self._counter:03d}"
            job = JobItem(
                job_id=job_id,
                name=name,
                job_type=job_type,
                status=JobStatus.QUEUED,
                progress=0.0,
                current_step=0,
                total_steps=max(total_steps, 1),
                details=details,
                target_fn=target_fn,
                kwargs=kwargs,
            )
            self._jobs[job_id] = job
            self._queue.append(job_id)
            self._logger.info("📋 Đã nạp tác vụ mới vào hàng đợi: %s [%s] (%s)", job_id, job_type, name)

            # Ensure dispatcher worker thread is active
            if not self.is_running:
                self._stop_event.clear()
                self._worker_thread = threading.Thread(
                    target=self._worker_loop,
                    name="JobDispatcherWorker",
                    daemon=True,
                )
                self._worker_thread.start()

        self._notify_listeners(job)
        return job

    def cancel_job(self, job_id: str) -> bool:
        """Cancels a specific job by ID without affecting others."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job.cancel()
            self._logger.warning("🛑 Đã gửi tín hiệu hủy tác vụ: %s (%s)", job_id, job.name)
        self._notify_listeners(job)
        return True

    def cancel_all(self):
        """Cancels all active and queued jobs."""
        with self._lock:
            for job in self._jobs.values():
                if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    job.cancel()
            self._logger.warning("🛑 Đã gửi tín hiệu hủy TẤT CẢ các tác vụ trong Task Manager.")

    def clear_completed(self):
        """Removes completed, cancelled, or failed jobs from history."""
        with self._lock:
            active_ids = {
                jid for jid, job in self._jobs.items()
                if job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            }
            self._jobs = {jid: j for jid, j in self._jobs.items() if jid in active_ids}
            self._queue = [jid for jid in self._queue if jid in active_ids]

    def get_jobs(self) -> List[JobItem]:
        """Returns a snapshot copy of all known jobs in chronological order."""
        with self._lock:
            return list(self._jobs.values())

    def get_job(self, job_id: str) -> Optional[JobItem]:
        """Retrieves a specific job by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def add_listener(self, listener: Callable[[JobItem], Any]):
        """Registers a callback to receive job state changes."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[JobItem], Any]):
        """Unregisters a callback."""
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify_listeners(self, job: JobItem):
        """Invokes registered callbacks thread-safely."""
        listeners = []
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(job)
            except Exception as e:
                self._logger.debug("Lỗi callback listener trong JobDispatcher: %s", e)

    def _worker_loop(self):
        """
        Sequential execution loop.
        Processes jobs from queue, prevents Chrome SingletonLock collisions,
        and provides fault-isolated execution for each task.
        """
        self._logger.info("⚡ Vòng lặp điều phối tác vụ (JobDispatcher Loop) đã khởi động.")
        while not self._stop_event.is_set():
            next_job_id: Optional[str] = None
            with self._lock:
                if self._queue:
                    next_job_id = self._queue.pop(0)

            if not next_job_id:
                # No queued jobs: break out to save idle CPU thread
                break

            with self._lock:
                job = self._jobs.get(next_job_id)

            if not job or job.stop_event.is_set():
                if job:
                    job.status = JobStatus.CANCELLED
                    self._notify_listeners(job)
                continue

            # Start execution
            job.status = JobStatus.RUNNING
            job.details = "Đang bắt đầu thực thi..."
            self._logger.info("🚀 Bắt đầu thực thi tác vụ: %s (%s)", job.job_id, job.name)
            self._notify_listeners(job)

            start_t = time.time()
            try:
                if job.target_fn:
                    job.target_fn(job, **job.kwargs)

                if job.stop_event.is_set():
                    job.status = JobStatus.CANCELLED
                    job.details = "Đã dừng theo yêu cầu."
                    self._logger.warning("🛑 Tác vụ %s đã dừng (Cancelled).", job.job_id)
                else:
                    job.status = JobStatus.COMPLETED
                    job.progress = 100.0
                    job.details = "Hoàn thành xuất sắc."
                    self._logger.info("🎉 Tác vụ %s đã hoàn thành 100%%.", job.job_id)
            except Exception as err:
                job.status = JobStatus.FAILED
                job.error_message = str(err)
                job.details = f"Lỗi: {err}"
                self._logger.error("❌ Tác vụ %s thất bại: %s", job.job_id, err, exc_info=True)
            finally:
                duration = max(0.0, time.time() - start_t)
                job.duration_str = f"{int(duration // 60)}p {int(duration % 60)}s" if duration >= 60 else f"{int(duration)}s"
                self._notify_listeners(job)

        self._logger.info("🏁 Vòng lặp điều phối tác vụ (JobDispatcher Loop) đã kết thúc.")
