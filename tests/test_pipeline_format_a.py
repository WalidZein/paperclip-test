"""
Tests for video_assembly.pipeline_format_a.

Subprocess calls to ffmpeg/ffprobe are mocked throughout so these tests run
without a working FFmpeg installation.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video_assembly.pipeline_format_a import (
    _build_ass_content,
    _build_ffmpeg_cmd,
    _escape_drawtext,
    _parse_elevenlabs_alignment,
    _parse_word_timestamps,
    compose_format_a,
    CTA_DISPLAY_SECS,
    HOOK_DISPLAY_SECS,
    TARGET_FPS,
    TARGET_HEIGHT,
    TARGET_WIDTH,
)


# ── _parse_word_timestamps ────────────────────────────────────────────────────


class TestParseWordTimestamps:
    def test_words_shape(self):
        data = {
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.5},
                {"word": "world", "start": 0.5, "end": 1.0},
            ]
        }
        result = _parse_word_timestamps(data)
        assert len(result) == 2
        assert result[0] == {"word": "Hello", "start": 0.0, "end": 0.5}
        assert result[1]["word"] == "world"

    def test_words_shape_text_key_fallback(self):
        data = {"words": [{"text": "Hi", "start": 0.1, "end": 0.4}]}
        result = _parse_word_timestamps(data)
        assert result[0]["word"] == "Hi"

    def test_segments_shape(self):
        data = {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "First segment"},
                {"start": 2.0, "end": 4.5, "text": "Second segment"},
            ]
        }
        result = _parse_word_timestamps(data)
        assert len(result) == 2
        assert result[0]["word"] == "First segment"
        assert result[1]["start"] == 2.0

    def test_empty_input_returns_empty(self):
        assert _parse_word_timestamps({}) == []
        assert _parse_word_timestamps({"words": []}) == []

    def test_skips_empty_words(self):
        data = {"words": [{"word": "", "start": 0.0, "end": 0.5}, {"word": "ok", "start": 0.5, "end": 1.0}]}
        result = _parse_word_timestamps(data)
        assert len(result) == 1
        assert result[0]["word"] == "ok"

    def test_float_coercion(self):
        data = {"words": [{"word": "test", "start": "1", "end": "2.5"}]}
        result = _parse_word_timestamps(data)
        assert result[0]["start"] == 1.0
        assert result[0]["end"] == 2.5

    def test_elevenlabs_shape_delegates(self):
        data = {
            "alignment": {
                "characters": ["H", "i", " ", "y", "o", "u"],
                "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            }
        }
        result = _parse_word_timestamps(data)
        assert len(result) == 2
        assert result[0]["word"] == "Hi"
        assert result[1]["word"] == "you"


# ── _parse_elevenlabs_alignment ───────────────────────────────────────────────


class TestParseElevenlabsAlignment:
    def test_basic(self):
        alignment = {
            "characters": ["H", "e", "l", "l", "o", " ", "W", "o", "r", "l", "d"],
            "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1],
        }
        result = _parse_elevenlabs_alignment(alignment)
        assert len(result) == 2
        assert result[0]["word"] == "Hello"
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == pytest.approx(0.5)   # end time of 'o' (index 4)
        assert result[1]["word"] == "World"

    def test_empty_alignment(self):
        assert _parse_elevenlabs_alignment({}) == []

    def test_mismatched_lengths(self):
        alignment = {
            "characters": ["a", "b"],
            "character_start_times_seconds": [0.0],
            "character_end_times_seconds": [0.5],
        }
        assert _parse_elevenlabs_alignment(alignment) == []

    def test_trailing_word_without_trailing_space(self):
        alignment = {
            "characters": ["O", "k"],
            "character_start_times_seconds": [0.0, 0.1],
            "character_end_times_seconds": [0.1, 0.2],
        }
        result = _parse_elevenlabs_alignment(alignment)
        assert len(result) == 1
        assert result[0]["word"] == "Ok"


# ── _build_ass_content ────────────────────────────────────────────────────────


class TestBuildAssContent:
    def test_contains_script_info(self):
        content = _build_ass_content([])
        assert "[Script Info]" in content
        assert f"PlayResX: {TARGET_WIDTH}" in content
        assert f"PlayResY: {TARGET_HEIGHT}" in content

    def test_contains_styles(self):
        content = _build_ass_content([])
        assert "[V4+ Styles]" in content
        assert "Style: Default" in content

    def test_dialogue_lines_generated(self):
        words = [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ]
        content = _build_ass_content(words)
        assert "Dialogue:" in content
        assert "Hello" in content
        assert "world" in content

    def test_skips_empty_words(self):
        words = [{"word": "", "start": 0.0, "end": 0.5}]
        content = _build_ass_content(words)
        assert "Dialogue:" not in content

    def test_skips_zero_duration(self):
        words = [{"word": "test", "start": 1.0, "end": 1.0}]
        content = _build_ass_content(words)
        assert "Dialogue:" not in content

    def test_strips_ass_control_chars(self):
        words = [{"word": "{bold}text", "start": 0.0, "end": 1.0}]
        content = _build_ass_content(words)
        assert "{" not in content
        assert "}" not in content

    def test_time_format(self):
        words = [{"word": "test", "start": 61.5, "end": 62.25}]
        content = _build_ass_content(words)
        # 61.5s = 0:01:01.50, 62.25s = 0:01:02.25
        assert "0:01:01.50" in content
        assert "0:01:02.25" in content


# ── _escape_drawtext ──────────────────────────────────────────────────────────


class TestEscapeDrawtext:
    def test_colon_escaped(self):
        assert "\\:" in _escape_drawtext("time: now")

    def test_brackets_escaped(self):
        result = _escape_drawtext("[test]")
        assert "\\[" in result
        assert "\\]" in result

    def test_comma_escaped(self):
        assert "\\," in _escape_drawtext("a,b")

    def test_backslash_escaped(self):
        result = _escape_drawtext("a\\b")
        assert "\\\\" in result

    def test_single_quote_replaced(self):
        # Single quotes replaced with typographic equivalent, not left as-is
        result = _escape_drawtext("don't")
        assert "'" not in result


# ── _build_ffmpeg_cmd ─────────────────────────────────────────────────────────


class TestBuildFfmpegCmd:
    BASE_KWARGS = dict(
        background_clip="/bg.mp4",
        voice_audio="/voice.mp3",
        music_bed=None,
        ass_path="/tmp/captions.ass",
        output_path="/out.mp4",
        duration=30.0,
        hook_text="This is the hook",
        cta_text="Follow for more",
        font_path=None,
    )

    def test_returns_list_of_strings(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        assert isinstance(cmd, list)
        assert all(isinstance(x, str) for x in cmd)

    def test_ffmpeg_first_element(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        assert cmd[0] == "ffmpeg"

    def test_stream_loop_present(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        assert "-stream_loop" in cmd
        assert "-1" in cmd

    def test_output_path_last(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        assert cmd[-1] == "/out.mp4"

    def test_output_codec_settings(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        assert "libx264" in cmd
        assert "aac" in cmd
        assert "yuv420p" in cmd

    def test_duration_set(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        idx = cmd.index("-t")
        assert cmd[idx + 1] == "30.000"

    def test_cta_start_correct(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        fc = cmd[cmd.index("-filter_complex") + 1]
        expected_start = 30.0 - CTA_DISPLAY_SECS
        assert f"{expected_start:.3f}" in fc

    def test_hook_enable_expression(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert f"between(t,0,{HOOK_DISPLAY_SECS})" in fc

    def test_ass_filter_present(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "ass=" in fc

    def test_music_bed_adds_amix(self):
        kwargs = {**self.BASE_KWARGS, "music_bed": "/music.mp3"}
        cmd = _build_ffmpeg_cmd(**kwargs)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "amix" in fc
        assert "volume=" in fc

    def test_no_music_bed_maps_audio_directly(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        # Without music bed, audio map should reference input 1 directly
        maps = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-map"]
        assert any("1:a" in m for m in maps)

    def test_empty_hook_text_excluded(self):
        kwargs = {**self.BASE_KWARGS, "hook_text": ""}
        cmd = _build_ffmpeg_cmd(**kwargs)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "between(t,0," not in fc

    def test_dark_overlay_present(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert "drawbox" in fc
        assert "black@0.4" in fc

    def test_scale_and_crop_present(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        fc = cmd[cmd.index("-filter_complex") + 1]
        assert f"{TARGET_WIDTH}:{TARGET_HEIGHT}" in fc
        assert "crop" in fc
        assert f"fps={TARGET_FPS}" in fc

    def test_faststart_movflags(self):
        cmd = _build_ffmpeg_cmd(**self.BASE_KWARGS)
        assert "+faststart" in cmd


# ── compose_format_a (integration, subprocess mocked) ────────────────────────


class TestComposeFormatA:
    @pytest.fixture
    def word_ts(self):
        return {
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.4, "end": 0.9},
            ]
        }

    @patch("video_assembly.pipeline_format_a.subprocess.run")
    def test_runs_ffprobe_then_ffmpeg(self, mock_run, word_ts, tmp_path):
        mock_run.side_effect = [
            MagicMock(stdout="15.0\n", returncode=0),   # ffprobe
            MagicMock(returncode=0),                     # ffmpeg
        ]
        result = compose_format_a(
            background_clip="/bg.mp4",
            voice_audio="/voice.mp3",
            word_timestamps=word_ts,
            output_path=str(tmp_path / "out.mp4"),
            hook_text="Shocking truth",
            tmp_dir=tmp_path,
        )
        assert result.endswith("out.mp4")
        assert mock_run.call_count == 2
        ffmpeg_call = mock_run.call_args_list[1]
        cmd = ffmpeg_call[0][0]
        assert cmd[0] == "ffmpeg"

    @patch("video_assembly.pipeline_format_a.subprocess.run")
    def test_writes_ass_file(self, mock_run, word_ts, tmp_path):
        mock_run.side_effect = [
            MagicMock(stdout="10.0\n", returncode=0),
            MagicMock(returncode=0),
        ]
        compose_format_a(
            background_clip="/bg.mp4",
            voice_audio="/voice.mp3",
            word_timestamps=word_ts,
            output_path=str(tmp_path / "out.mp4"),
            hook_text="Hook",
            tmp_dir=tmp_path,
        )
        ass_file = tmp_path / "captions.ass"
        assert ass_file.exists()
        content = ass_file.read_text()
        assert "Hello" in content
        assert "world" in content

    @patch("video_assembly.pipeline_format_a.subprocess.run")
    def test_accepts_json_file_path(self, mock_run, word_ts, tmp_path):
        json_path = tmp_path / "ts.json"
        json_path.write_text(json.dumps(word_ts))
        mock_run.side_effect = [
            MagicMock(stdout="10.0\n", returncode=0),
            MagicMock(returncode=0),
        ]
        compose_format_a(
            background_clip="/bg.mp4",
            voice_audio="/voice.mp3",
            word_timestamps=str(json_path),
            output_path=str(tmp_path / "out.mp4"),
            hook_text="Hook",
            tmp_dir=tmp_path,
        )
        assert mock_run.call_count == 2

    @patch("video_assembly.pipeline_format_a.subprocess.run")
    def test_ffmpeg_failure_raises(self, mock_run, word_ts, tmp_path):
        mock_run.side_effect = [
            MagicMock(stdout="10.0\n", returncode=0),
            subprocess.CalledProcessError(1, "ffmpeg", stderr="error details"),
        ]
        with pytest.raises(subprocess.CalledProcessError):
            compose_format_a(
                background_clip="/bg.mp4",
                voice_audio="/voice.mp3",
                word_timestamps=word_ts,
                output_path=str(tmp_path / "out.mp4"),
                hook_text="Hook",
                tmp_dir=tmp_path,
            )

    @patch("video_assembly.pipeline_format_a.subprocess.run")
    def test_duration_capped_at_60s(self, mock_run, tmp_path):
        mock_run.side_effect = [
            MagicMock(stdout="90.0\n", returncode=0),  # long audio
            MagicMock(returncode=0),
        ]
        compose_format_a(
            background_clip="/bg.mp4",
            voice_audio="/voice.mp3",
            word_timestamps={"words": []},
            output_path=str(tmp_path / "out.mp4"),
            hook_text="Hook",
            tmp_dir=tmp_path,
        )
        ffmpeg_call = mock_run.call_args_list[1]
        cmd = ffmpeg_call[0][0]
        t_idx = cmd.index("-t")
        assert float(cmd[t_idx + 1]) == pytest.approx(60.0)
