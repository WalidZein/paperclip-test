"""Pipeline scheduler: drains queue every 4 hours, 2 concurrent jobs, 6 videos/day."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .alerts import SlackAlerter
from .queue import JobQueue
from .runner import PipelineRunner

logger = logging.getLogger(__name__)

# Drain every 4 hours — guarantees ≤ 6 windows/day and keeps spacing even.
# Override via PIPELINE_DRAIN_INTERVAL_HOURS env var.
_DEFAULT_DRAIN_HOURS = 4
_MAX_CONCURRENT = 2
# Hard safety cap: do not produce more than this many videos in one calendar day.
_DAILY_CAP = 6


class PipelineScheduler:
    """Wraps APScheduler to drain the job queue on a cron schedule."""

    # Fire a Slack alert if this fraction or more of a batch fails.
    _DEFAULT_ALERT_THRESHOLD = 0.5

    def __init__(
        self,
        queue: JobQueue,
        runner: PipelineRunner,
        drain_interval_hours: Optional[int] = None,
        daily_cap: int = _DAILY_CAP,
        report_path: Optional[str | Path] = None,
        slack_webhook_url: Optional[str] = None,
        batch_alert_threshold: float = _DEFAULT_ALERT_THRESHOLD,
    ) -> None:
        self.queue = queue
        self.runner = runner
        self.daily_cap = daily_cap
        self.report_path = Path(report_path) if report_path else Path("daily_report.txt")
        self._alerter = SlackAlerter(webhook_url=slack_webhook_url)
        self._batch_alert_threshold = batch_alert_threshold
        drain_hours = drain_interval_hours or int(
            os.environ.get("PIPELINE_DRAIN_INTERVAL_HOURS", _DEFAULT_DRAIN_HOURS)
        )
        self._drain_hours = drain_hours
        self._scheduler = AsyncIOScheduler()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Register jobs and start the scheduler (non-blocking)."""
        self._scheduler.add_job(
            self._drain_queue,
            IntervalTrigger(hours=self._drain_hours),
            id="drain_queue",
            name="Drain pipeline queue",
            replace_existing=True,
            max_instances=1,
        )
        # Daily report at 23:00 UTC
        self._scheduler.add_job(
            self._write_daily_report,
            CronTrigger(hour=23, minute=0, timezone="UTC"),
            id="daily_report",
            name="Write daily run report",
            replace_existing=True,
        )
        # Re-queue any jobs stuck in RUNNING on startup (after a prior crash)
        stuck = self.queue.reset_stuck_running()
        if stuck:
            logger.warning("Re-queued %d stuck RUNNING jobs from previous session", stuck)

        self._scheduler.start()
        logger.info(
            "PipelineScheduler started — drain every %dh, max %d concurrent, %d/day cap",
            self._drain_hours,
            _MAX_CONCURRENT,
            self.daily_cap,
        )

    def shutdown(self, wait: bool = True) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    # ------------------------------------------------------------------
    # Core drain logic
    # ------------------------------------------------------------------

    async def _drain_queue(self) -> None:
        """Dequeue up to 2 jobs; skip if daily cap already reached."""
        completed_today = len(self.queue.get_completed_today())
        if completed_today >= self.daily_cap:
            logger.info(
                "Daily cap reached (%d/%d) — skipping drain", completed_today, self.daily_cap
            )
            return

        slots = min(_MAX_CONCURRENT, self.daily_cap - completed_today)
        jobs = self.queue.dequeue_batch(slots)

        if not jobs:
            logger.debug("Queue empty — nothing to drain")
            return

        logger.info("Draining %d job(s) concurrently", len(jobs))
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

        async def _bounded(job: dict) -> Optional[str]:
            async with semaphore:
                return await self.runner.run_job(job)

        results = await asyncio.gather(*[_bounded(j) for j in jobs], return_exceptions=True)
        succeeded = sum(1 for r in results if isinstance(r, str))
        failed = len(jobs) - succeeded
        logger.info("Drain complete — %d/%d succeeded", succeeded, len(jobs))

        if failed > 0:
            rate = failed / len(jobs)
            if rate >= self._batch_alert_threshold:
                logger.warning(
                    "Batch failure rate %.0f%% exceeds threshold %.0f%% — sending Slack alert",
                    rate * 100,
                    self._batch_alert_threshold * 100,
                )
                self._alerter.alert_high_failure_rate(failed, len(jobs))

    # ------------------------------------------------------------------
    # Immediate one-shot drain (used by CLI `run-now`)
    # ------------------------------------------------------------------

    async def drain_now(self) -> None:
        await self._drain_queue()

    # ------------------------------------------------------------------
    # Daily report
    # ------------------------------------------------------------------

    async def _write_daily_report(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        completed = self.queue.get_completed_today()
        failed = self.queue.get_failed_today()

        lines = [
            f"# Daily Pipeline Report — {today}",
            f"Completed: {len(completed)}  Failed: {len(failed)}",
            "",
        ]
        if completed:
            lines.append("## Completed")
            for job in completed:
                lines.append(f"  [job {job['id']}] {job.get('output_path', '?')}")
        if failed:
            lines.append("")
            lines.append("## Failed")
            for job in failed:
                brief = {}
                try:
                    brief = __import__("json").loads(job.get("brief_json", "{}"))
                except Exception:
                    pass
                lines.append(
                    f"  [job {job['id']}] niche={brief.get('niche','?')} "
                    f"topic={brief.get('topic','?')}"
                )

        report = "\n".join(lines) + "\n"
        self.report_path.write_text(report, encoding="utf-8")
        logger.info("Daily report written to %s", self.report_path)
