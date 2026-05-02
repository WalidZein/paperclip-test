"""Tests for pipeline.queue and pipeline.runner (mocked external calls)."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.queue import DONE, FAILED, PENDING, RUNNING, JobQueue
from pipeline.runner import PipelineRunner, _slugify


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    return tmp_path / "test_jobs.db"


@pytest.fixture()
def queue(tmp_db):
    return JobQueue(tmp_db)


@pytest.fixture()
def sample_brief():
    return {
        "niche": "ai_tech",
        "topic": "GPT-5 just dropped — here's what changes",
        "hook": "AI just changed everything",
        "format": "monologue",
        "length_target_sec": 30,
        "voice_style": "energetic",
        "posting_time": "18:00",
        "trending_audio": None,
    }


# ---------------------------------------------------------------------------
# JobQueue
# ---------------------------------------------------------------------------

class TestJobQueue:
    def test_enqueue_returns_id(self, queue, sample_brief):
        job_id = queue.enqueue(sample_brief)
        assert isinstance(job_id, int)
        assert job_id >= 1

    def test_multiple_enqueue_increments(self, queue, sample_brief):
        id1 = queue.enqueue(sample_brief)
        id2 = queue.enqueue(sample_brief)
        assert id2 > id1

    def test_dequeue_batch_claims_pending(self, queue, sample_brief):
        queue.enqueue(sample_brief)
        queue.enqueue(sample_brief)
        queue.enqueue(sample_brief)

        batch = queue.dequeue_batch(limit=2)
        assert len(batch) == 2
        for job in batch:
            assert job["status"] == RUNNING

    def test_dequeue_does_not_exceed_limit(self, queue, sample_brief):
        for _ in range(5):
            queue.enqueue(sample_brief)
        batch = queue.dequeue_batch(limit=2)
        assert len(batch) == 2

    def test_dequeue_empty_queue_returns_empty(self, queue):
        assert queue.dequeue_batch(limit=2) == []

    def test_mark_done(self, queue, sample_brief):
        job_id = queue.enqueue(sample_brief)
        queue.dequeue_batch(1)
        queue.mark_done(job_id, "/output/dir")
        stats = queue.get_stats()
        assert stats.get(DONE) == 1

    def test_mark_failed_first_attempt_re_queues(self, queue, sample_brief):
        job_id = queue.enqueue(sample_brief)
        queue.dequeue_batch(1)
        queue.mark_failed(job_id, "some error", retry=True)
        stats = queue.get_stats()
        # Should be re-queued as pending for the retry
        assert stats.get(PENDING) == 1

    def test_mark_failed_second_attempt_stays_failed(self, queue, sample_brief):
        job_id = queue.enqueue(sample_brief)
        queue.dequeue_batch(1)
        queue.mark_failed(job_id, "error 1", retry=True)
        # second failure
        queue.dequeue_batch(1)
        queue.mark_failed(job_id, "error 2", retry=True)
        stats = queue.get_stats()
        assert stats.get(FAILED) == 1
        assert stats.get(PENDING, 0) == 0

    def test_get_pending_count(self, queue, sample_brief):
        queue.enqueue(sample_brief)
        queue.enqueue(sample_brief)
        assert queue.get_pending_count() == 2

    def test_reset_stuck_running(self, queue, sample_brief):
        queue.enqueue(sample_brief)
        queue.dequeue_batch(1)  # moves to RUNNING
        n = queue.reset_stuck_running()
        assert n == 1
        assert queue.get_pending_count() == 1

    def test_get_stats_empty(self, queue):
        assert queue.get_stats() == {}


# ---------------------------------------------------------------------------
# _slugify helper
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self):
        s = _slugify("GPT-5 just dropped — here's what changes")
        assert "'" not in s
        assert "—" not in s

    def test_max_length(self):
        long = "a" * 100
        assert len(_slugify(long)) <= 48


# ---------------------------------------------------------------------------
# PipelineRunner (mocked external I/O)
# ---------------------------------------------------------------------------

MOCK_SCRIPT = MagicMock()
MOCK_SCRIPT.hook = "AI just changed everything"
MOCK_SCRIPT.body = "Here is why..."
MOCK_SCRIPT.cta = "Follow for more"
MOCK_SCRIPT.data_card = ""
MOCK_SCRIPT.hashtags = ["#AI", "#tech"]
MOCK_SCRIPT.niche = "ai_tech"
MOCK_SCRIPT.as_tagged_script.return_value = "[HOOK]\nAI just changed\n[BODY]\nHere\n[CTA]\nFollow"


@pytest.fixture()
def runner(tmp_path, queue):
    return PipelineRunner(
        queue=queue,
        output_base=tmp_path / "output",
        failed_base=tmp_path / "failed",
    )


class TestPipelineRunner:
    def test_slugify_in_output_path(self):
        slug = _slugify("GPT-5 just dropped — here's what changes")
        assert len(slug) <= 48
        assert " " not in slug

    @pytest.mark.asyncio
    async def test_run_job_success(self, runner, queue, sample_brief, tmp_path):
        job_id = queue.enqueue(sample_brief)
        jobs = queue.dequeue_batch(1)
        job = jobs[0]

        mp3_path = tmp_path / "voice.mp3"
        mp3_path.write_bytes(b"fakeaudio")
        ts_path = tmp_path / "timing.json"
        ts_path.write_text(
            json.dumps({"duration": 10.0, "segments": [{"start": 0, "end": 10, "text": "AI"}]}),
            encoding="utf-8",
        )
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fakevideo")

        with (
            patch("pipeline.runner.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.runner.generate_tts", return_value=(mp3_path, ts_path)),
            patch.object(runner._assembler, "assemble", return_value=str(video_path)),
        ):
            result = await runner.run_job(job)

        assert result is not None
        output_dir = Path(result)
        assert (output_dir / "metadata.json").exists()
        assert (output_dir / "script.txt").exists()
        assert (output_dir / "voice.mp3").exists()

        meta = json.loads((output_dir / "metadata.json").read_text())
        assert meta["niche"] == "ai_tech"
        assert isinstance(meta["hashtags"], list)

        stats = queue.get_stats()
        assert stats.get(DONE) == 1

    @pytest.mark.asyncio
    async def test_run_job_failure_triggers_retry(self, runner, queue, sample_brief):
        job_id = queue.enqueue(sample_brief)
        jobs = queue.dequeue_batch(1)
        job = jobs[0]

        with patch("pipeline.runner.generate_script", side_effect=RuntimeError("API down")):
            result = await runner.run_job(job)

        assert result is None
        # Job should be re-queued (retry_count=0 → first failure → pending)
        stats = queue.get_stats()
        assert stats.get(PENDING) == 1

    @pytest.mark.asyncio
    async def test_failed_bundle_written(self, runner, queue, sample_brief, tmp_path):
        job_id = queue.enqueue(sample_brief)
        jobs = queue.dequeue_batch(1)
        job = jobs[0]

        with patch("pipeline.runner.generate_script", side_effect=ValueError("bad brief")):
            await runner.run_job(job)

        failed_dir = runner.failed_base / str(job_id)
        assert (failed_dir / "brief.json").exists()
        assert (failed_dir / "error.log").exists()
        assert "bad brief" in (failed_dir / "error.log").read_text()


# ---------------------------------------------------------------------------
# Concurrency sanity: two jobs run concurrently without queue corruption
# ---------------------------------------------------------------------------

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_two_jobs_run_concurrently(self, runner, queue, sample_brief, tmp_path):
        for _ in range(2):
            queue.enqueue(sample_brief)
        jobs = queue.dequeue_batch(2)
        assert len(jobs) == 2

        mp3_path = tmp_path / "voice.mp3"
        mp3_path.write_bytes(b"audio")
        ts_path = tmp_path / "timing.json"
        ts_path.write_text(
            json.dumps({"duration": 10.0, "segments": [{"start": 0, "end": 10, "text": "x"}]}),
            encoding="utf-8",
        )
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"video")

        with (
            patch("pipeline.runner.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.runner.generate_tts", return_value=(mp3_path, ts_path)),
            patch.object(runner._assembler, "assemble", return_value=str(video_path)),
        ):
            results = await asyncio.gather(*[runner.run_job(j) for j in jobs])

        assert all(r is not None for r in results)
        stats = queue.get_stats()
        assert stats.get(DONE) == 2
