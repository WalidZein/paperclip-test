"""Tests for PipelineScheduler batch Slack alert on high failure rate."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline.queue import JobQueue
from pipeline.runner import PipelineRunner
from pipeline.scheduler import PipelineScheduler


@pytest.fixture()
def queue(tmp_path):
    return JobQueue(tmp_path / "jobs.db")


@pytest.fixture()
def runner(tmp_path, queue):
    return PipelineRunner(
        queue=queue,
        output_base=tmp_path / "output",
        failed_base=tmp_path / "failed",
    )


BRIEF = {
    "niche": "ai_tech",
    "topic": "test topic",
    "hook": "",
    "format": "monologue",
    "length_target_sec": 30,
    "posting_time": "18:00",
    "voice_style": "energetic",
    "trending_audio": None,
}


class TestSchedulerBatchAlerts:
    @pytest.mark.asyncio
    async def test_no_alert_when_all_succeed(self, queue, runner):
        sched = PipelineScheduler(
            queue=queue, runner=runner,
            slack_webhook_url="https://hooks.slack.com/test",
            batch_alert_threshold=0.5,
        )
        queue.enqueue(BRIEF)
        queue.enqueue(BRIEF)

        with (
            patch.object(runner, "run_job", return_value="/output/path"),
            patch.object(sched._alerter, "alert_high_failure_rate") as mock_alert,
        ):
            await sched._drain_queue()

        mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_when_all_fail(self, queue, runner):
        sched = PipelineScheduler(
            queue=queue, runner=runner,
            slack_webhook_url="https://hooks.slack.com/test",
            batch_alert_threshold=0.5,
        )
        queue.enqueue(BRIEF)
        queue.enqueue(BRIEF)

        with (
            patch.object(runner, "run_job", return_value=None),
            patch.object(sched._alerter, "alert_high_failure_rate") as mock_alert,
        ):
            await sched._drain_queue()

        mock_alert.assert_called_once_with(2, 2)

    @pytest.mark.asyncio
    async def test_alert_when_rate_meets_threshold(self, queue, runner):
        # 1 success, 1 failure = 50% — meets the 0.5 threshold
        sched = PipelineScheduler(
            queue=queue, runner=runner,
            slack_webhook_url="https://hooks.slack.com/test",
            batch_alert_threshold=0.5,
        )
        queue.enqueue(BRIEF)
        queue.enqueue(BRIEF)

        with (
            patch.object(runner, "run_job", side_effect=["/out", None]),
            patch.object(sched._alerter, "alert_high_failure_rate") as mock_alert,
        ):
            await sched._drain_queue()

        mock_alert.assert_called_once_with(1, 2)

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self, queue, runner):
        # 2 successes, 0 failures = 0% < 0.5 threshold
        sched = PipelineScheduler(
            queue=queue, runner=runner,
            slack_webhook_url="https://hooks.slack.com/test",
            batch_alert_threshold=0.5,
        )
        queue.enqueue(BRIEF)
        queue.enqueue(BRIEF)

        with (
            patch.object(runner, "run_job", side_effect=["/out", "/out"]),
            patch.object(sched._alerter, "alert_high_failure_rate") as mock_alert,
        ):
            await sched._drain_queue()

        mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_alert_when_queue_empty(self, queue, runner):
        sched = PipelineScheduler(
            queue=queue, runner=runner,
            slack_webhook_url="https://hooks.slack.com/test",
        )

        with patch.object(sched._alerter, "alert_high_failure_rate") as mock_alert:
            await sched._drain_queue()

        mock_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_http_request_when_no_webhook(self, queue, runner):
        sched = PipelineScheduler(queue=queue, runner=runner, slack_webhook_url=None)
        queue.enqueue(BRIEF)

        import pipeline.alerts as alert_mod
        with (
            patch.object(runner, "run_job", return_value=None),
            patch.object(alert_mod.requests, "post") as mock_post,
        ):
            await sched._drain_queue()

        mock_post.assert_not_called()
