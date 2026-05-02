"""Unit tests for VideoUploader with mocked TikTok API."""
import json
import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from publisher.config import NicheConfig, ScheduleConfig
from publisher.tiktok_client import TikTokAPIError
from publisher.uploader import VideoUploader


NICHE_CFG = NicheConfig(
    name="cooking",
    description_template="How to make {topic} 🍳",
    hashtags=["#cooking", "#recipe"],
    schedule=ScheduleConfig(cron="0 9 * * *", timezone="UTC"),
    privacy_level="PUBLIC_TO_EVERYONE",
)

SAMPLE_YAML = textwrap.dedent("""\
    niches:
      cooking:
        description_template: "How to make {topic} 🍳"
        hashtags:
          - "#cooking"
        schedule:
          cron: "0 9 * * *"
          timezone: "UTC"
""")


@pytest.fixture
def video_file(tmp_path):
    p = tmp_path / "pasta.mp4"
    p.write_bytes(b"fake mp4 content")
    return p


@pytest.fixture
def config_file(tmp_path):
    f = tmp_path / "niches.yaml"
    f.write_text(SAMPLE_YAML)
    return f


@pytest.fixture
def uploader(tmp_path):
    log = tmp_path / "posts.jsonl"
    return VideoUploader(access_token="fake_token", log_file=log)


class TestVideoUploaderSuccess:
    def test_upload_success(self, uploader, video_file):
        status = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_abc"}
        with patch.object(uploader.client, "publish_video", return_value=status):
            result = uploader.upload(video_file, "cooking", "pasta", NICHE_CFG, open_id="user123")

        assert result["post_id"] == "post_abc"
        assert "user123" in result["url"]
        assert "post_abc" in result["url"]

    def test_upload_logs_success(self, tmp_path, video_file):
        log_file = tmp_path / "posts.jsonl"
        uploader = VideoUploader(access_token="fake_token", log_file=log_file)
        status = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_xyz"}

        with patch.object(uploader.client, "publish_video", return_value=status):
            uploader.upload(video_file, "cooking", "ramen", NICHE_CFG)

        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["event"] == "publish_success"
        assert entry["niche"] == "cooking"
        assert entry["topic"] == "ramen"
        assert entry["post_id"] == "post_xyz"

    def test_upload_missing_video_raises(self, uploader, tmp_path):
        missing = tmp_path / "nonexistent.mp4"
        with pytest.raises(FileNotFoundError, match="nonexistent.mp4"):
            uploader.upload(missing, "cooking", "pasta", NICHE_CFG)


class TestVideoUploaderRetry:
    def test_retries_on_api_error(self, uploader, video_file, tmp_path):
        fail = TikTokAPIError("rate limited", error_code="rate_limit_exceeded")
        success = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_ok"}
        side_effects = [fail, fail, success]

        with patch.object(uploader.client, "publish_video", side_effect=side_effects), \
             patch("publisher.uploader.time.sleep"):
            result = uploader.upload(video_file, "cooking", "pasta", NICHE_CFG)

        assert result["post_id"] == "post_ok"

    def test_exhausts_retries_and_raises(self, uploader, video_file):
        fail = TikTokAPIError("server error", http_status=500)
        with patch.object(uploader.client, "publish_video", side_effect=fail), \
             patch("publisher.uploader.time.sleep"):
            with pytest.raises(TikTokAPIError, match="server error"):
                uploader.upload(video_file, "cooking", "pasta", NICHE_CFG)

    def test_logs_failures_on_retry(self, tmp_path, video_file):
        log_file = tmp_path / "posts.jsonl"
        uploader = VideoUploader(access_token="fake_token", log_file=log_file)

        fail = TikTokAPIError("timeout", http_status=504)
        success = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_retry"}

        with patch.object(uploader.client, "publish_video", side_effect=[fail, success]), \
             patch("publisher.uploader.time.sleep"):
            uploader.upload(video_file, "cooking", "soup", NICHE_CFG)

        lines = [json.loads(l) for l in log_file.read_text().strip().splitlines()]
        events = [l["event"] for l in lines]
        assert "publish_failure" in events
        assert "publish_retry" in events
        assert "publish_success" in events

    def test_exponential_backoff_timing(self, uploader, video_file):
        fail = TikTokAPIError("error")
        success = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_bt"}

        sleep_calls = []
        with patch.object(uploader.client, "publish_video", side_effect=[fail, fail, success]), \
             patch("publisher.uploader.time.sleep", side_effect=lambda t: sleep_calls.append(t)):
            uploader.upload(video_file, "cooking", "bread", NICHE_CFG)

        assert len(sleep_calls) == 2
        # BASE_BACKOFF ** 1 = 2, BASE_BACKOFF ** 2 = 4
        assert sleep_calls[0] == pytest.approx(2.0)
        assert sleep_calls[1] == pytest.approx(4.0)


class TestVideoUploaderFactory:
    def test_from_config(self, config_file, video_file, tmp_path):
        log_file = tmp_path / "posts.jsonl"
        status = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_fc"}

        with patch("publisher.uploader.TikTokContentClient.publish_video", return_value=status):
            result = VideoUploader.from_config(
                config_path=config_file,
                niche_name="cooking",
                video_path=video_file,
                topic="omelette",
                access_token="fake_token",
                log_file=log_file,
            )

        assert result["post_id"] == "post_fc"

    def test_requires_token(self, monkeypatch):
        monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
        with pytest.raises(ValueError, match="TIKTOK_ACCESS_TOKEN"):
            VideoUploader(access_token=None)

    def test_uses_env_token(self, monkeypatch):
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "env_token_xyz")
        u = VideoUploader()
        assert u.client.access_token == "env_token_xyz"
