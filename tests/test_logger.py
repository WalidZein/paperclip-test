"""Unit tests for PostLogger."""
import json
from pathlib import Path

import pytest

from publisher.logger import PostLogger


@pytest.fixture
def log_file(tmp_path):
    return tmp_path / "posts.jsonl"


def test_log_success_writes_json(log_file):
    logger = PostLogger(log_file)
    logger.log_success("cooking", "pasta", "post_123", "https://tiktok.com/video/123", "/videos/pasta.mp4")

    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["event"] == "publish_success"
    assert entry["niche"] == "cooking"
    assert entry["topic"] == "pasta"
    assert entry["post_id"] == "post_123"
    assert entry["url"] == "https://tiktok.com/video/123"
    assert "timestamp" in entry


def test_log_failure_writes_json(log_file):
    logger = PostLogger(log_file)
    logger.log_failure("cooking", "pasta", "rate limited", 1, "/videos/pasta.mp4")

    entry = json.loads(log_file.read_text().strip())
    assert entry["event"] == "publish_failure"
    assert entry["error"] == "rate limited"
    assert entry["attempt"] == 1


def test_log_retry_writes_json(log_file):
    logger = PostLogger(log_file)
    logger.log_retry("cooking", "pasta", 2, 4.0)

    entry = json.loads(log_file.read_text().strip())
    assert entry["event"] == "publish_retry"
    assert entry["wait_seconds"] == 4.0


def test_log_multiple_appends(log_file):
    logger = PostLogger(log_file)
    logger.log_success("cooking", "pasta", "p1", "url1", "v1.mp4")
    logger.log_failure("cooking", "risotto", "error", 1, "v2.mp4")

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    events = [json.loads(l)["event"] for l in lines]
    assert events == ["publish_success", "publish_failure"]


def test_log_no_file_does_not_crash():
    logger = PostLogger(log_file=None)
    logger.log_success("cooking", "pasta", "p1", "url1", "v1.mp4")


def test_log_creates_parent_dirs(tmp_path):
    deep_log = tmp_path / "a" / "b" / "posts.jsonl"
    logger = PostLogger(deep_log)
    logger.log_success("cooking", "pasta", "p1", "url1", "v1.mp4")
    assert deep_log.exists()
