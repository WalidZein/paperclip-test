"""Tests for pipeline.coordinator.RunCoordinator, pipeline.run_log, pipeline.alerts."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.alerts import SlackAlerter
from pipeline.coordinator import RunCoordinator
from pipeline.run_log import RunLog


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_YAML = textwrap.dedent("""\
    niches:
      ai_tech:
        description_template: "{topic} — AI explained 🤖"
        hashtags:
          - "#AI"
          - "#tech"
        schedule:
          cron: "0 9 * * *"
          timezone: "UTC"
""")

MOCK_SCRIPT = MagicMock()
MOCK_SCRIPT.hook = "AI just changed everything"
MOCK_SCRIPT.hashtags = ["#AI", "#tech"]
MOCK_SCRIPT.as_tagged_script.return_value = "[HOOK]\nAI changed\n[BODY]\nDetails\n[CTA]\nFollow"


@pytest.fixture()
def config_file(tmp_path):
    f = tmp_path / "niches.yaml"
    f.write_text(SAMPLE_YAML)
    return f


@pytest.fixture()
def coordinator(tmp_path, config_file):
    return RunCoordinator(
        config_path=config_file,
        output_base=tmp_path / "output",
        log_dir=tmp_path / "run_logs",
        dry_run=True,
    )


# ---------------------------------------------------------------------------
# RunLog
# ---------------------------------------------------------------------------

class TestRunLog:
    def test_new_creates_unique_ids(self):
        r1 = RunLog.new("ai_tech", "topic1")
        r2 = RunLog.new("ai_tech", "topic2")
        assert r1.run_id != r2.run_id

    def test_run_id_is_12_hex_chars(self):
        log = RunLog.new("ai_tech", "topic")
        assert len(log.run_id) == 12
        assert all(c in "0123456789abcdef" for c in log.run_id)

    def test_record_stage_appends(self):
        log = RunLog.new("ai_tech", "topic")
        log.record_stage("script_generation", "success", 1.23)
        assert len(log.stages) == 1
        s = log.stages[0]
        assert s.name == "script_generation"
        assert s.status == "success"
        assert s.duration_sec == pytest.approx(1.23)

    def test_record_stage_with_error(self):
        log = RunLog.new("ai_tech", "topic")
        log.record_stage("tts", "failed", 0.5, error="API down")
        assert log.stages[0].error == "API down"

    def test_finalize_sets_fields(self):
        log = RunLog.new("ai_tech", "topic")
        log.finalize("success", publish_url="https://tiktok.com/video/1")
        assert log.status == "success"
        assert log.finished_at is not None
        assert log.publish_url == "https://tiktok.com/video/1"

    def test_finalize_failed_sets_error(self):
        log = RunLog.new("ai_tech", "topic")
        log.finalize("failed", error="ffmpeg missing")
        assert log.status == "failed"
        assert log.error == "ffmpeg missing"

    def test_to_dict_structure(self):
        log = RunLog.new("ai_tech", "topic")
        log.record_stage("script_generation", "success", 0.5)
        log.finalize("success")
        d = log.to_dict()
        assert d["niche"] == "ai_tech"
        assert d["topic"] == "topic"
        assert d["status"] == "success"
        assert isinstance(d["stages"], list)
        assert d["stages"][0]["duration_sec"] == pytest.approx(0.5)

    def test_duration_rounded_to_3dp(self):
        log = RunLog.new("ai_tech", "topic")
        log.record_stage("tts", "success", 1.23456789)
        assert log.to_dict()["stages"][0]["duration_sec"] == pytest.approx(1.235, abs=1e-3)

    def test_write_creates_json_file(self, tmp_path):
        log = RunLog.new("ai_tech", "topic")
        log.finalize("success")
        path = log.write(tmp_path / "logs")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["run_id"] == log.run_id
        assert data["status"] == "success"

    def test_write_creates_parent_dir(self, tmp_path):
        log = RunLog.new("ai_tech", "topic")
        log.finalize("success")
        log.write(tmp_path / "deep" / "nested" / "logs")
        # No exception means parent dirs were created


# ---------------------------------------------------------------------------
# RunCoordinator — success path
# ---------------------------------------------------------------------------

class TestRunCoordinatorSuccess:
    def test_run_returns_success_dict(self, coordinator, tmp_path):
        mp3 = tmp_path / "voice.mp3"
        mp3.write_bytes(b"audio")
        ts = tmp_path / "timing.json"
        ts.write_text(json.dumps({"duration": 10.0, "segments": []}))
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")

        with (
            patch("pipeline.coordinator.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.coordinator.generate_tts", return_value=(mp3, ts)),
            patch.object(coordinator._assembler, "assemble", return_value=str(video)),
        ):
            result = coordinator.run(niche="ai_tech", topic="GPT-5 review")

        assert result["status"] == "success"
        assert result["niche"] == "ai_tech"
        assert result["topic"] == "GPT-5 review"
        assert result["run_id"] is not None

    def test_run_all_stages_present(self, coordinator, tmp_path):
        mp3 = tmp_path / "voice.mp3"
        mp3.write_bytes(b"audio")
        ts = tmp_path / "timing.json"
        ts.write_text(json.dumps({"duration": 10.0, "segments": []}))
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")

        with (
            patch("pipeline.coordinator.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.coordinator.generate_tts", return_value=(mp3, ts)),
            patch.object(coordinator._assembler, "assemble", return_value=str(video)),
        ):
            result = coordinator.run(niche="ai_tech", topic="GPT-5 review")

        stages = {s["name"]: s["status"] for s in result["stages"]}
        assert stages["script_generation"] == "success"
        assert stages["tts"] == "success"
        assert stages["video_assembly"] == "success"
        assert stages["publish"] == "skipped"

    def test_stages_have_timing(self, coordinator, tmp_path):
        mp3 = tmp_path / "voice.mp3"
        mp3.write_bytes(b"audio")
        ts = tmp_path / "timing.json"
        ts.write_text(json.dumps({"duration": 5.0, "segments": []}))
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")

        with (
            patch("pipeline.coordinator.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.coordinator.generate_tts", return_value=(mp3, ts)),
            patch.object(coordinator._assembler, "assemble", return_value=str(video)),
        ):
            result = coordinator.run(niche="ai_tech", topic="test topic")

        for stage in result["stages"]:
            assert "duration_sec" in stage
            assert stage["duration_sec"] >= 0

    def test_run_writes_json_log(self, coordinator, tmp_path):
        mp3 = tmp_path / "voice.mp3"
        mp3.write_bytes(b"audio")
        ts = tmp_path / "timing.json"
        ts.write_text(json.dumps({"duration": 5.0, "segments": []}))
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")

        with (
            patch("pipeline.coordinator.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.coordinator.generate_tts", return_value=(mp3, ts)),
            patch.object(coordinator._assembler, "assemble", return_value=str(video)),
        ):
            result = coordinator.run(niche="ai_tech", topic="test topic")

        log_files = list(coordinator.log_dir.glob("*.json"))
        assert len(log_files) == 1
        data = json.loads(log_files[0].read_text())
        assert data["run_id"] == result["run_id"]
        assert data["status"] == "success"

    def test_log_contains_timestamps(self, coordinator, tmp_path):
        mp3 = tmp_path / "voice.mp3"
        mp3.write_bytes(b"audio")
        ts = tmp_path / "timing.json"
        ts.write_text(json.dumps({"duration": 5.0, "segments": []}))
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video")

        with (
            patch("pipeline.coordinator.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.coordinator.generate_tts", return_value=(mp3, ts)),
            patch.object(coordinator._assembler, "assemble", return_value=str(video)),
        ):
            result = coordinator.run(niche="ai_tech", topic="test topic")

        assert result["started_at"] is not None
        assert result["finished_at"] is not None


# ---------------------------------------------------------------------------
# RunCoordinator — failure paths
# ---------------------------------------------------------------------------

class TestRunCoordinatorFailure:
    def test_invalid_niche_returns_failed(self, coordinator):
        result = coordinator.run(niche="nonexistent_niche", topic="some topic")
        assert result["status"] == "failed"
        assert "nonexistent_niche" in (result["error"] or "")

    def test_script_failure_returns_failed(self, coordinator):
        with patch("pipeline.coordinator.generate_script", side_effect=RuntimeError("API down")):
            result = coordinator.run(niche="ai_tech", topic="some topic")

        assert result["status"] == "failed"
        stages = {s["name"]: s for s in result["stages"]}
        assert stages["script_generation"]["status"] == "failed"
        assert "API down" in (stages["script_generation"]["error"] or "")

    def test_tts_failure_returns_failed(self, coordinator):
        with (
            patch("pipeline.coordinator.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.coordinator.generate_tts", side_effect=RuntimeError("ElevenLabs down")),
        ):
            result = coordinator.run(niche="ai_tech", topic="some topic")

        assert result["status"] == "failed"
        stages = {s["name"]: s for s in result["stages"]}
        assert stages["script_generation"]["status"] == "success"
        assert stages["tts"]["status"] == "failed"

    def test_video_failure_returns_failed(self, coordinator, tmp_path):
        mp3 = tmp_path / "voice.mp3"
        mp3.write_bytes(b"audio")
        ts = tmp_path / "timing.json"
        ts.write_text(json.dumps({"duration": 5.0, "segments": []}))

        with (
            patch("pipeline.coordinator.generate_script", return_value=MOCK_SCRIPT),
            patch("pipeline.coordinator.generate_tts", return_value=(mp3, ts)),
            patch.object(
                coordinator._assembler, "assemble", side_effect=RuntimeError("ffmpeg missing")
            ),
        ):
            result = coordinator.run(niche="ai_tech", topic="some topic")

        assert result["status"] == "failed"
        stages = {s["name"]: s for s in result["stages"]}
        assert stages["video_assembly"]["status"] == "failed"
        assert "ffmpeg missing" in (stages["video_assembly"]["error"] or "")

    def test_failure_sends_slack_alert(self, coordinator):
        with (
            patch("pipeline.coordinator.generate_script", side_effect=RuntimeError("boom")),
            patch.object(coordinator._alerter, "alert_run_failed") as mock_alert,
        ):
            coordinator.run(niche="ai_tech", topic="topic")

        mock_alert.assert_called_once()
        args = mock_alert.call_args[0]
        assert args[0] == "ai_tech"   # niche
        assert args[1] == "topic"     # topic
        assert "boom" in args[2]      # error

    def test_failure_writes_log_file(self, coordinator):
        with patch("pipeline.coordinator.generate_script", side_effect=RuntimeError("crash")):
            coordinator.run(niche="ai_tech", topic="topic")

        log_files = list(coordinator.log_dir.glob("*.json"))
        assert len(log_files) == 1
        data = json.loads(log_files[0].read_text())
        assert data["status"] == "failed"

    def test_failure_error_propagated_to_log(self, coordinator):
        with patch("pipeline.coordinator.generate_script", side_effect=RuntimeError("specific error")):
            result = coordinator.run(niche="ai_tech", topic="topic")

        assert "specific error" in (result["error"] or "")

    def test_subsequent_stage_not_run_after_failure(self, coordinator):
        with patch("pipeline.coordinator.generate_script", side_effect=RuntimeError("fail")):
            result = coordinator.run(niche="ai_tech", topic="topic")

        stage_names = [s["name"] for s in result["stages"]]
        assert "tts" not in stage_names
        assert "video_assembly" not in stage_names


# ---------------------------------------------------------------------------
# SlackAlerter
# ---------------------------------------------------------------------------

class TestSlackAlerter:
    def test_no_webhook_no_request(self, monkeypatch):
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        alerter = SlackAlerter(webhook_url=None)
        with patch("pipeline.alerts.requests.post") as mock_post:
            alerter.alert_run_failed("niche", "topic", "error", "run_id")
        mock_post.assert_not_called()

    def test_sends_when_webhook_set(self):
        alerter = SlackAlerter(webhook_url="https://hooks.slack.com/fake")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("pipeline.alerts.requests.post", return_value=mock_resp) as mock_post:
            alerter.alert_run_failed("ai_tech", "my topic", "some error", "abc123")
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert "Pipeline run failed" in payload["text"]
        assert "abc123" in payload["text"]
        assert "ai_tech" in payload["text"]

    def test_alert_does_not_raise_on_request_error(self):
        alerter = SlackAlerter(webhook_url="https://hooks.slack.com/fake")
        with patch("pipeline.alerts.requests.post", side_effect=ConnectionError("network down")):
            alerter.alert_run_failed("niche", "topic", "err", "run_id")

    def test_high_failure_rate_alert(self):
        alerter = SlackAlerter(webhook_url="https://hooks.slack.com/fake")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("pipeline.alerts.requests.post", return_value=mock_resp) as mock_post:
            alerter.alert_high_failure_rate(3, 5)
        payload = mock_post.call_args[1]["json"]
        assert "failure rate" in payload["text"].lower()
        assert "3/5" in payload["text"]

    def test_uses_env_webhook(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/env")
        alerter = SlackAlerter()
        assert alerter.webhook_url == "https://hooks.slack.com/env"
