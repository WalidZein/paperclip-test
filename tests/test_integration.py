"""
Integration test — produces a real 1080x1920 MP4 from a synthetic audio file.

Run with:
    INTEGRATION_TEST=1 pytest tests/test_integration.py -v

Requires: moviepy, ffmpeg, Pillow (ImageMagick optional — captions degrade gracefully).
"""
import os
import struct
import tempfile
import wave
from pathlib import Path

import pytest

SKIP = not os.environ.get("INTEGRATION_TEST")


def _make_silent_wav(path: Path, duration_s: float = 5.0, sample_rate: int = 44100) -> Path:
    """Write a silent mono WAV file."""
    n_samples = int(sample_rate * duration_s)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return path


@pytest.mark.skipif(SKIP, reason="Set INTEGRATION_TEST=1 to run")
class TestAssembleIntegration:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="va_integration_"))

    def test_produces_1080x1920_mp4(self):
        from moviepy.editor import VideoFileClip
        from video_assembly.assembler import VideoAssembler

        audio_path = self.tmp / "voice.wav"
        _make_silent_wav(audio_path, duration_s=5.0)

        metadata = {
            "duration": 5.0,
            "segments": [
                {"start": 0.0, "end": 2.5, "text": "This is a test caption"},
                {"start": 2.5, "end": 5.0, "text": "Integration verified"},
            ],
        }

        output_path = str(self.tmp / "output.mp4")

        # No API keys → falls through to black background
        assembler = VideoAssembler()
        result = assembler.assemble(
            audio_path=str(audio_path),
            timing_metadata=metadata,
            keywords=["nature"],
            output_path=output_path,
            niche="motivation",
        )

        assert os.path.exists(result), "Output MP4 does not exist"
        assert os.path.getsize(result) > 5_000, "Output file is suspiciously small"

        clip = VideoFileClip(result)
        try:
            assert clip.size == (1080, 1920), f"Expected 1080x1920, got {clip.size}"
            assert 4.0 <= clip.duration <= 6.0, f"Duration out of range: {clip.duration}"
        finally:
            clip.close()

    def test_60s_duration_cap(self):
        """A 90s audio should produce a ≤60s video."""
        from moviepy.editor import VideoFileClip
        from video_assembly.assembler import VideoAssembler

        audio_path = self.tmp / "long_voice.wav"
        _make_silent_wav(audio_path, duration_s=90.0)

        metadata = {"duration": 90.0, "segments": []}
        output_path = str(self.tmp / "capped.mp4")

        assembler = VideoAssembler()
        result = assembler.assemble(
            audio_path=str(audio_path),
            timing_metadata=metadata,
            keywords=["test"],
            output_path=output_path,
            niche="motivation",
        )

        clip = VideoFileClip(result)
        try:
            assert clip.duration <= 61.0, f"Duration not capped: {clip.duration}"
        finally:
            clip.close()

    def test_all_niche_profiles_load(self):
        """All four YAML profiles should load without error."""
        from video_assembly.config import NicheProfile

        for niche in ("motivation", "finance", "fitness", "tech"):
            profile = NicheProfile.load(niche)
            assert profile.niche == niche
            assert profile.caption.font_size > 0
            assert len(profile.video.b_roll_keywords) > 0
