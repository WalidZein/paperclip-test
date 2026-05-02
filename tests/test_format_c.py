"""Tests for Format C: split-screen before/after video renderer."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video_assembly.format_c import (
    LEFT_W,
    RIGHT_W,
    TARGET_HEIGHT,
    TARGET_WIDTH,
    _build_divider_clip,
    _fit_panel,
    assemble_format_c,
)


class TestPanelDimensions:
    def test_left_plus_right_equals_total(self):
        assert LEFT_W + RIGHT_W == TARGET_WIDTH

    def test_left_is_40_percent(self):
        assert abs(LEFT_W / TARGET_WIDTH - 0.40) < 0.01

    def test_right_is_60_percent(self):
        assert abs(RIGHT_W / TARGET_WIDTH - 0.60) < 0.01


class TestFitPanel:
    def _make_clip(self, w, h):
        clip = MagicMock()
        clip.size = (w, h)
        cropped = MagicMock()
        cropped.size = (w, h)
        resized = MagicMock()
        resized.size = (432, 1920)
        clip.cropped.return_value = cropped
        cropped.resized.return_value = resized
        clip.resized.return_value = resized
        return clip

    def test_wider_source_crops_sides(self):
        clip = self._make_clip(1920, 1080)
        _fit_panel(clip, LEFT_W, TARGET_HEIGHT)
        clip.cropped.assert_called_once()
        _, kwargs = clip.cropped.call_args
        assert "x1" in kwargs and "x2" in kwargs

    def test_taller_source_crops_top_bottom(self):
        clip = self._make_clip(200, 2000)
        _fit_panel(clip, LEFT_W, TARGET_HEIGHT)
        clip.cropped.assert_called_once()
        _, kwargs = clip.cropped.call_args
        assert "y1" in kwargs and "y2" in kwargs

    def test_exact_ratio_no_crop(self):
        clip = self._make_clip(LEFT_W, TARGET_HEIGHT)
        clip.cropped.return_value = clip
        _fit_panel(clip, LEFT_W, TARGET_HEIGHT)
        clip.cropped.assert_not_called()


class TestDividerClip:
    def test_returns_list(self):
        result = _build_divider_clip(10.0)
        assert isinstance(result, list)

    def test_divider_not_visible_before_t1(self):
        import numpy as np
        clips = _build_divider_clip(10.0)
        if not clips:
            pytest.skip("numpy not available")
        clip = clips[0]
        frame = clip.get_frame(0.0)
        assert frame.shape[2] == 3
        assert frame.max() == 0, "Divider should be invisible before t=1s"

    def test_divider_visible_after_slide(self):
        import numpy as np
        clips = _build_divider_clip(10.0)
        if not clips:
            pytest.skip("numpy not available")
        clip = clips[0]
        frame = clip.get_frame(2.0)
        assert frame.max() == 255, "Divider should be fully white at t=2s"

    def test_divider_partially_visible_during_slide(self):
        from video_assembly.format_c import DIVIDER_W
        import numpy as np
        clips = _build_divider_clip(10.0)
        if not clips:
            pytest.skip("numpy not available")
        clip = clips[0]
        frame = clip.get_frame(1.175)  # 0.175s into the 0.35s slide ≈ 50%
        # White pixels = visible_rows × DIVIDER_W columns; check range in row space
        white_pixels = int((frame[:, :, 0] == 255).sum())
        visible_rows = white_pixels // DIVIDER_W
        assert 0 < visible_rows < TARGET_HEIGHT


class TestAssembleFormatC:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.metadata = {
            "duration": 10.0,
            "segments": [{"start": 0.0, "end": 5.0, "text": "Before vs After"}],
        }

    def _mock_audio(self, mock_audio_cls, duration=10.0):
        audio = MagicMock()
        audio.duration = duration
        audio.subclipped.return_value = audio
        mock_audio_cls.return_value = audio
        return audio

    def _mock_composite(self, mock_composite_cls):
        composite = MagicMock()
        composite.subclipped.return_value = composite
        composite.with_audio.return_value = composite
        mock_composite_cls.return_value = composite
        return composite

    @patch("video_assembly.format_c.AudioFileClip")
    @patch("video_assembly.format_c.CompositeVideoClip")
    @patch("video_assembly.format_c._load_media_clip")
    def test_calls_write_videofile(self, mock_load, mock_composite_cls, mock_audio_cls):
        audio = self._mock_audio(mock_audio_cls)
        composite = self._mock_composite(mock_composite_cls)

        panel = MagicMock()
        panel.with_position.return_value = panel
        mock_load.return_value = panel

        out = str(self.tmp / "out_c.mp4")
        assemble_format_c(
            audio_path="/fake/audio.mp3",
            timing_metadata=self.metadata,
            before_media="/fake/before.png",
            after_media="/fake/after.png",
            output_path=out,
        )

        composite.write_videofile.assert_called_once()
        assert composite.write_videofile.call_args[0][0] == out

    @patch("video_assembly.format_c.AudioFileClip")
    @patch("video_assembly.format_c.CompositeVideoClip")
    @patch("video_assembly.format_c._load_media_clip")
    def test_duration_capped_at_60s(self, mock_load, mock_composite_cls, mock_audio_cls):
        audio = self._mock_audio(mock_audio_cls, duration=90.0)
        composite = self._mock_composite(mock_composite_cls)

        panel = MagicMock()
        panel.with_position.return_value = panel
        mock_load.return_value = panel

        long_meta = {"duration": 90.0, "segments": []}
        out = str(self.tmp / "out_c_long.mp4")
        assemble_format_c(
            audio_path="/fake/audio.mp3",
            timing_metadata=long_meta,
            before_media="/fake/before.png",
            after_media="/fake/after.png",
            output_path=out,
        )

        args = composite.subclipped.call_args[0]
        assert args[0] <= 60.0

    @patch("video_assembly.format_c.AudioFileClip")
    @patch("video_assembly.format_c.CompositeVideoClip")
    @patch("video_assembly.format_c._load_media_clip")
    def test_two_panels_loaded(self, mock_load, mock_composite_cls, mock_audio_cls):
        self._mock_audio(mock_audio_cls)
        self._mock_composite(mock_composite_cls)

        panel = MagicMock()
        panel.with_position.return_value = panel
        mock_load.return_value = panel

        out = str(self.tmp / "two_panels.mp4")
        assemble_format_c(
            audio_path="/fake/audio.mp3",
            timing_metadata=self.metadata,
            before_media="/fake/before.mp4",
            after_media="/fake/after.mp4",
            output_path=out,
        )

        assert mock_load.call_count == 2
        calls = [c[0][0] for c in mock_load.call_args_list]
        assert "/fake/before.mp4" in calls
        assert "/fake/after.mp4" in calls

    @patch("video_assembly.format_c.AudioFileClip")
    @patch("video_assembly.format_c.CompositeVideoClip")
    @patch("video_assembly.format_c._load_media_clip")
    def test_panels_sized_correctly(self, mock_load, mock_composite_cls, mock_audio_cls):
        self._mock_audio(mock_audio_cls)
        self._mock_composite(mock_composite_cls)

        panel = MagicMock()
        panel.with_position.return_value = panel
        mock_load.return_value = panel

        out = str(self.tmp / "panel_sizes.mp4")
        assemble_format_c(
            audio_path="/fake/audio.mp3",
            timing_metadata=self.metadata,
            before_media="/fake/before.png",
            after_media="/fake/after.png",
            output_path=out,
        )

        sizes = [(c[0][2], c[0][3]) for c in mock_load.call_args_list]
        assert sizes[0] == (LEFT_W, TARGET_HEIGHT), "Before panel must use LEFT_W"
        assert sizes[1] == (RIGHT_W, TARGET_HEIGHT), "After panel must use RIGHT_W"

    @patch("video_assembly.format_c.AudioFileClip")
    @patch("video_assembly.format_c.CompositeVideoClip")
    @patch("video_assembly.format_c._load_media_clip")
    def test_after_panel_positioned_at_left_w(self, mock_load, mock_composite_cls, mock_audio_cls):
        self._mock_audio(mock_audio_cls)
        self._mock_composite(mock_composite_cls)

        panel = MagicMock()
        panel.with_position.return_value = panel
        mock_load.return_value = panel

        out = str(self.tmp / "right_pos.mp4")
        assemble_format_c(
            audio_path="/fake/audio.mp3",
            timing_metadata=self.metadata,
            before_media="/fake/before.png",
            after_media="/fake/after.png",
            output_path=out,
        )

        positions = [c[0][0] for c in panel.with_position.call_args_list]
        assert (LEFT_W, 0) in positions, f"After panel must start at x={LEFT_W}, got {positions}"

    @patch("video_assembly.format_c.AudioFileClip")
    @patch("video_assembly.format_c.CompositeVideoClip")
    @patch("video_assembly.format_c._load_media_clip")
    def test_loads_from_json_file(self, mock_load, mock_composite_cls, mock_audio_cls):
        self._mock_audio(mock_audio_cls)
        self._mock_composite(mock_composite_cls)

        panel = MagicMock()
        panel.with_position.return_value = panel
        mock_load.return_value = panel

        json_path = self.tmp / "timing.json"
        json_path.write_text(json.dumps(self.metadata))

        out = str(self.tmp / "from_file.mp4")
        assemble_format_c(
            audio_path="/fake/audio.mp3",
            timing_metadata=str(json_path),
            before_media="/fake/before.png",
            after_media="/fake/after.png",
            output_path=out,
        )

        assert mock_load.call_count == 2
