"""Unit tests for VideoAssembler pipeline."""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video_assembly.assembler import VideoAssembler
from video_assembly.stock_footage import FootageClip


class TestVideoAssemblerInit:
    def test_default_init(self):
        a = VideoAssembler()
        assert a.fetcher is not None
        assert a.profiles_dir is None

    def test_init_with_keys(self):
        a = VideoAssembler(pexels_api_key="pk", pixabay_api_key="xk")
        assert a.fetcher.pexels_key == "pk"
        assert a.fetcher.pixabay_key == "xk"

    def test_init_with_profiles_dir(self, tmp_path):
        a = VideoAssembler(profiles_dir=tmp_path)
        assert a.profiles_dir == tmp_path


class TestGetDuration:
    def setup_method(self):
        self.assembler = VideoAssembler()

    def test_uses_metadata_duration_field(self):
        assert self.assembler._get_duration("/fake/audio.mp3", {"duration": 45.0}) == 45.0

    def test_metadata_duration_coerced_to_float(self):
        assert self.assembler._get_duration("/fake/audio.mp3", {"duration": "30"}) == 30.0

    def test_fallback_to_segments_max_end(self):
        metadata = {
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "a"},
                {"start": 5.0, "end": 27.3, "text": "b"},
            ]
        }
        assert self.assembler._get_duration("/fake/audio.mp3", metadata) == 27.3

    def test_hardcoded_default_when_no_hint(self):
        assert self.assembler._get_duration("/fake/audio.mp3", {}) == 30.0


class TestAssemblePipeline:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())

    @patch("video_assembly.assembler.assemble_and_encode")
    @patch("video_assembly.assembler.StockFootageFetcher.fetch_for_duration")
    def test_assemble_calls_encode(self, mock_fetch, mock_encode):
        expected_output = str(self.tmp / "out.mp4")
        mock_fetch.return_value = [
            FootageClip(path="/tmp/clip.mp4", duration=10.0, source="pexels", keyword="nature")
        ]
        mock_encode.return_value = expected_output

        metadata = {
            "duration": 10.0,
            "segments": [{"start": 0.0, "end": 5.0, "text": "Hello"}],
        }
        assembler = VideoAssembler()
        result = assembler.assemble(
            audio_path="/fake/audio.mp3",
            timing_metadata=metadata,
            keywords=["nature"],
            output_path=expected_output,
            niche="motivation",
        )

        assert result == expected_output
        assert mock_encode.called

    @patch("video_assembly.assembler.assemble_and_encode")
    @patch("video_assembly.assembler.StockFootageFetcher.fetch_for_duration")
    def test_assemble_loads_metadata_from_json_file(self, mock_fetch, mock_encode):
        mock_fetch.return_value = []
        mock_encode.return_value = str(self.tmp / "out.mp4")

        metadata = {"duration": 5.0, "segments": []}
        json_path = self.tmp / "timing.json"
        json_path.write_text(json.dumps(metadata))

        assembler = VideoAssembler()
        assembler.assemble(
            audio_path="/fake/audio.mp3",
            timing_metadata=str(json_path),
            keywords=["test"],
            output_path=str(self.tmp / "out.mp4"),
            niche="motivation",
        )

        # Verify encode was called
        assert mock_encode.called

    @patch("video_assembly.assembler.assemble_and_encode")
    @patch("video_assembly.assembler.StockFootageFetcher.fetch_for_duration")
    def test_duration_capped_at_60s(self, mock_fetch, mock_encode):
        """Videos longer than 60s should be truncated to 60s."""
        mock_fetch.return_value = []
        mock_encode.return_value = str(self.tmp / "out.mp4")

        metadata = {"duration": 90.0, "segments": []}

        assembler = VideoAssembler()
        assembler.assemble(
            audio_path="/fake/audio.mp3",
            timing_metadata=metadata,
            keywords=["test"],
            output_path=str(self.tmp / "out.mp4"),
            niche="motivation",
        )

        # The target_duration passed to assemble_and_encode should be capped at 60
        _, kwargs = mock_encode.call_args
        target = kwargs.get("target_duration") or mock_encode.call_args[0][5]
        assert target <= 60.0

    @patch("video_assembly.assembler.assemble_and_encode")
    @patch("video_assembly.assembler.StockFootageFetcher.fetch_for_duration")
    def test_deduplicates_keywords(self, mock_fetch, mock_encode):
        mock_fetch.return_value = []
        mock_encode.return_value = str(self.tmp / "out.mp4")

        metadata = {"duration": 5.0, "segments": []}

        assembler = VideoAssembler()
        assembler.assemble(
            audio_path="/fake/audio.mp3",
            timing_metadata=metadata,
            keywords=["fitness", "workout", "FITNESS"],  # duplicate "fitness"
            output_path=str(self.tmp / "out.mp4"),
            niche="fitness",  # profile also has fitness keywords
        )

        # Should have been called — just verify deduplication didn't crash
        assert mock_fetch.called
        passed_keywords = mock_fetch.call_args[0][0]
        lower_kws = [k.lower() for k in passed_keywords]
        assert len(lower_kws) == len(set(lower_kws)), "Duplicate keywords found"
