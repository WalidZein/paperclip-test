"""Unit tests for the ElevenLabs TTS module."""
import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from video_assembly.tts import (
    ElevenLabsError,
    WordTimestamp,
    _chars_to_words,
    _get_profile,
    generate_tts,
    preprocess_script,
)


# ---------------------------------------------------------------------------
# preprocess_script
# ---------------------------------------------------------------------------


class TestPreprocessScript:
    def test_strips_hook_body_cta_tags(self):
        script = "[HOOK]\nGrab attention.\n[BODY]\nExplain thing.\n[CTA]\nFollow now."
        result = preprocess_script(script)
        assert "[HOOK]" not in result
        assert "[BODY]" not in result
        assert "[CTA]" not in result
        assert "Grab attention." in result
        assert "Explain thing." in result
        assert "Follow now." in result

    def test_case_insensitive_tags(self):
        result = preprocess_script("[hook] intro [body] middle [cta] outro")
        assert "[hook]" not in result.lower()

    def test_preserves_ellipsis_pause_markers(self):
        script = "[HOOK]\nDid you know... the truth is hidden."
        result = preprocess_script(script)
        assert "..." in result

    def test_collapses_extra_blank_lines(self):
        script = "[HOOK]\n\n\n\nContent here."
        result = preprocess_script(script)
        assert "\n\n\n" not in result

    def test_empty_after_stripping(self):
        result = preprocess_script("[HOOK][BODY][CTA]")
        assert result == ""

    def test_no_tags_passes_through(self):
        text = "Simple script with no tags."
        assert preprocess_script(text) == text


# ---------------------------------------------------------------------------
# _chars_to_words
# ---------------------------------------------------------------------------


class TestCharsToWords:
    def _alignment(self, text: str, step: float = 0.1) -> dict:
        """Build synthetic character-level alignment for *text*."""
        chars = list(text)
        starts = [round(i * step, 6) for i in range(len(chars))]
        ends = [round((i + 1) * step, 6) for i in range(len(chars))]
        return {
            "characters": chars,
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        }

    def test_single_word(self):
        words = _chars_to_words(self._alignment("hello"))
        assert len(words) == 1
        assert words[0].word == "hello"
        assert words[0].start_time == 0.0
        assert words[0].end_time == pytest.approx(0.5, abs=0.01)

    def test_two_words(self):
        words = _chars_to_words(self._alignment("hi there"))
        assert len(words) == 2
        assert words[0].word == "hi"
        assert words[1].word == "there"

    def test_leading_trailing_spaces_ignored(self):
        words = _chars_to_words(self._alignment(" hello "))
        assert len(words) == 1
        assert words[0].word == "hello"

    def test_empty_alignment(self):
        assert _chars_to_words({}) == []

    def test_mismatched_lengths(self):
        bad = {"characters": ["h"], "character_start_times_seconds": [], "character_end_times_seconds": [0.1]}
        assert _chars_to_words(bad) == []

    def test_multiline_text(self):
        words = _chars_to_words(self._alignment("line1\nline2"))
        assert len(words) == 2
        assert words[0].word == "line1"
        assert words[1].word == "line2"

    def test_timestamps_rounded_to_3dp(self):
        alignment = {
            "characters": ["a"],
            "character_start_times_seconds": [0.1234567],
            "character_end_times_seconds": [0.9876543],
        }
        words = _chars_to_words(alignment)
        assert words[0].start_time == 0.123
        assert words[0].end_time == 0.988


# ---------------------------------------------------------------------------
# _get_profile
# ---------------------------------------------------------------------------


class TestGetProfile:
    def test_dark_psychology_settings(self):
        p = _get_profile("dark_psychology")
        assert p.stability == 0.4
        assert p.similarity_boost == 0.9
        assert p.style == 0.8
        assert p.speed == pytest.approx(0.85)

    def test_personal_finance_settings(self):
        p = _get_profile("personal_finance")
        assert p.stability == 0.85
        assert p.similarity_boost == 0.75
        assert p.speed == pytest.approx(1.0)

    def test_ai_tech_settings(self):
        p = _get_profile("ai_tech")
        assert p.stability == 0.7
        assert p.similarity_boost == 0.8
        assert p.speed == pytest.approx(1.15)

    def test_unknown_niche_raises(self):
        with pytest.raises(ValueError, match="Unknown niche"):
            _get_profile("cooking")

    def test_env_var_overrides_voice_id(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_VOICE_AI_TECH", "custom_voice_id_xyz")
        p = _get_profile("ai_tech")
        assert p.voice_id == "custom_voice_id_xyz"


# ---------------------------------------------------------------------------
# generate_tts
# ---------------------------------------------------------------------------


def _make_api_response(text: str = "Hello world") -> dict:
    """Build a realistic-looking ElevenLabs with-timestamps response."""
    chars = list(text)
    step = 0.1
    starts = [round(i * step, 6) for i in range(len(chars))]
    ends = [round((i + 1) * step, 6) for i in range(len(chars))]
    fake_audio = b"ID3" + b"\x00" * 100  # minimal fake MP3
    return {
        "audio_base64": base64.b64encode(fake_audio).decode(),
        "alignment": {
            "characters": chars,
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends,
        },
    }


class TestGenerateTts:
    def test_happy_path_dark_psychology(self, tmp_path):
        script = "[HOOK]\nDid you know... power corrupts.\n[BODY]\nIt really does.\n[CTA]\nFollow for more."
        api_resp = _make_api_response("Did you know power corrupts It really does Follow for more")

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = api_resp

        with patch("video_assembly.tts.requests.post", return_value=mock_resp) as mock_post:
            mp3, ts = generate_tts(script, "dark_psychology", tmp_path, api_key="test_key")

        assert mp3.exists()
        assert ts.exists()
        assert mp3.suffix == ".mp3"
        assert "dark_psychology" in mp3.name
        data = json.loads(ts.read_text())
        assert isinstance(data, list)
        assert all("word" in item and "start_time" in item and "end_time" in item for item in data)

    def test_happy_path_personal_finance(self, tmp_path):
        api_resp = _make_api_response("Save invest grow")
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = api_resp

        with patch("video_assembly.tts.requests.post", return_value=mock_resp):
            mp3, ts = generate_tts("[HOOK]\nSave.\n[BODY]\nInvest.\n[CTA]\nGrow.", "personal_finance", tmp_path, api_key="k")

        assert "personal_finance" in mp3.name
        assert "personal_finance" in ts.name

    def test_happy_path_ai_tech(self, tmp_path):
        api_resp = _make_api_response("AI is fast")
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = api_resp

        with patch("video_assembly.tts.requests.post", return_value=mock_resp):
            mp3, ts = generate_tts("[HOOK]\nAI is fast.\n[CTA]\nSubscribe.", "ai_tech", tmp_path, api_key="k")

        assert "ai_tech" in mp3.name

    def test_correct_voice_settings_sent(self, tmp_path):
        api_resp = _make_api_response("hello")
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = api_resp

        with patch("video_assembly.tts.requests.post", return_value=mock_resp) as mock_post:
            generate_tts("[HOOK]\nhello", "personal_finance", tmp_path, api_key="k")

        _, kwargs = mock_post.call_args
        body = kwargs["json"]
        vs = body["voice_settings"]
        assert vs["stability"] == 0.85
        assert vs["similarity_boost"] == 0.75
        assert body["speed"] == pytest.approx(1.0)

    def test_retries_on_network_error(self, tmp_path):
        api_resp = _make_api_response("hello")
        ok_resp = MagicMock(ok=True, status_code=200)
        ok_resp.json.return_value = api_resp

        call_count = 0

        def flaky_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise requests.ConnectionError("timeout")
            return ok_resp

        with patch("video_assembly.tts.requests.post", side_effect=flaky_post):
            with patch("video_assembly.tts.time.sleep"):
                mp3, _ = generate_tts("[HOOK]\nhello", "ai_tech", tmp_path, api_key="k", max_retries=3, retry_delay=0)

        assert call_count == 3
        assert mp3.exists()

    def test_raises_after_max_retries(self, tmp_path):
        with patch("video_assembly.tts.requests.post", side_effect=requests.ConnectionError("no network")):
            with patch("video_assembly.tts.time.sleep"):
                with pytest.raises(ElevenLabsError, match="failed after"):
                    generate_tts("[HOOK]\nhello", "ai_tech", tmp_path, api_key="k", max_retries=2, retry_delay=0)

    def test_rate_limit_retries_and_succeeds(self, tmp_path):
        api_resp = _make_api_response("hello")
        ok_resp = MagicMock(ok=True, status_code=200)
        ok_resp.json.return_value = api_resp
        rate_resp = MagicMock(ok=False, status_code=429)

        responses = [rate_resp, ok_resp]
        with patch("video_assembly.tts.requests.post", side_effect=responses):
            with patch("video_assembly.tts.time.sleep"):
                mp3, _ = generate_tts("[HOOK]\nhello", "ai_tech", tmp_path, api_key="k", max_retries=3, retry_delay=0)

        assert mp3.exists()

    def test_api_error_raises_immediately(self, tmp_path):
        err_resp = MagicMock(ok=False, status_code=401, text="Unauthorized")
        with patch("video_assembly.tts.requests.post", return_value=err_resp):
            with pytest.raises(ElevenLabsError, match="401"):
                generate_tts("[HOOK]\nhello", "dark_psychology", tmp_path, api_key="bad_key")

    def test_missing_api_key_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        with pytest.raises(ElevenLabsError, match="ELEVENLABS_API_KEY"):
            generate_tts("[HOOK]\nhello", "ai_tech", tmp_path)

    def test_empty_script_raises(self, tmp_path):
        with pytest.raises(ValueError, match="empty"):
            generate_tts("[HOOK][BODY][CTA]", "ai_tech", tmp_path, api_key="k")

    def test_output_dir_created(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        api_resp = _make_api_response("hi")
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = api_resp

        with patch("video_assembly.tts.requests.post", return_value=mock_resp):
            generate_tts("[HOOK]\nhi", "ai_tech", nested, api_key="k")

        assert nested.exists()
