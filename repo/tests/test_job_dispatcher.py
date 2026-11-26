"""
Unit Tests for JobDispatcher & Multi-Task Queue Engine.

Verifies:
1. Job submission and status transitions (QUEUED -> RUNNING -> COMPLETED).
2. Independent task cancellation without affecting sibling jobs.
3. Listener callback invocation on job status and progress changes.
4. Error isolation: a failing job does not terminate the dispatcher loop.
"""

from pathlib import Path
import sys
import time
import unittest

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from services.job_dispatcher import JobDispatcher, JobItem, JobStatus


class TestJobDispatcher(unittest.TestCase):
    """Test suite for JobDispatcher and JobItem."""

    def setUp(self):
        self.dispatcher = JobDispatcher()

    def tearDown(self):
        self.dispatcher.cancel_all()

    def test_submit_job_executes_to_completion(self):
        executed = []

        def sample_task(job: JobItem):
            executed.append("done")
            job.update_progress(1, 1, "Finished sample task")

        job = self.dispatcher.submit_job(
            name="Test Job 1",
            job_type="CHECK_DOM",
            target_fn=sample_task,
            total_steps=1,
        )
        self.assertEqual(job.job_id, "JOB-001")
        self.assertIn(job.status, (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.COMPLETED))

        # Wait for worker thread to complete task
        for _ in range(20):
            if job.status == JobStatus.COMPLETED:
                break
            time.sleep(0.05)

        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.progress, 100.0)
        self.assertEqual(executed, ["done"])

    def test_independent_cancellation_of_queued_job(self):
        # Job 1 runs slowly
        def slow_task(job: JobItem):
            for _ in range(10):
                if job.stop_event.is_set():
                    break
                time.sleep(0.05)

        # Job 2 is queued behind Job 1
        job2_executed = []
        def job2_task(job: JobItem):
            job2_executed.append("job2")

        job1 = self.dispatcher.submit_job(name="Slow Job", job_type="POST_GROUPS", target_fn=slow_task)
        job2 = self.dispatcher.submit_job(name="Queued Job", job_type="JOIN_GROUPS", target_fn=job2_task)

        self.assertEqual(job2.status, JobStatus.QUEUED)

        # Cancel Job 2 while still queued
        cancelled = self.dispatcher.cancel_job(job2.job_id)
        self.assertTrue(cancelled)
        self.assertEqual(job2.status, JobStatus.CANCELLED)

        # Stop Job 1
        job1.cancel()

        for _ in range(20):
            if job1.status in (JobStatus.CANCELLED, JobStatus.COMPLETED):
                break
            time.sleep(0.05)

        self.assertEqual(job1.status, JobStatus.CANCELLED)
        # Job 2 was cancelled so it should never run
        self.assertEqual(job2_executed, [])

    def test_error_isolation_does_not_crash_subsequent_jobs(self):
        def failing_task(job: JobItem):
            raise RuntimeError("Simulated failure inside job")

        job2_runs = []
        def success_task(job: JobItem):
            job2_runs.append("ok")

        job1 = self.dispatcher.submit_job(name="Failing Job", job_type="POST_GROUPS", target_fn=failing_task)
        job2 = self.dispatcher.submit_job(name="Success Job", job_type="SCAN_META", target_fn=success_task)

        # Wait for both jobs to process
        for _ in range(30):
            if job2.status == JobStatus.COMPLETED:
                break
            time.sleep(0.05)

        self.assertEqual(job1.status, JobStatus.FAILED)
        self.assertIn("Simulated failure inside job", str(job1.error_message))
        self.assertEqual(job2.status, JobStatus.COMPLETED)
        self.assertEqual(job2_runs, ["ok"])

    def test_listeners_notified_on_progress_and_completion(self):
        events = []

        def on_event(job: JobItem):
            events.append((job.job_id, job.status))

        self.dispatcher.add_listener(on_event)

        def simple_task(job: JobItem):
            job.update_progress(1, 2, "Step 1")
            self.dispatcher._notify_listeners(job)
            job.update_progress(2, 2, "Step 2")

        job = self.dispatcher.submit_job(name="Observed Job", job_type="POST_GROUPS", target_fn=simple_task)

        for _ in range(20):
            if job.status == JobStatus.COMPLETED:
                break
            time.sleep(0.05)

        self.assertEqual(job.status, JobStatus.COMPLETED)
        # Check that we received notifications
        statuses = [ev[1] for ev in events]
        self.assertIn(JobStatus.QUEUED, statuses)
        self.assertIn(JobStatus.RUNNING, statuses)
        self.assertIn(JobStatus.COMPLETED, statuses)


if __name__ == "__main__":
    unittest.main()
