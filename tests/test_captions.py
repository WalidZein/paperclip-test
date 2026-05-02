import pytest
from video_assembly.captions import parse_timing_metadata, CaptionSegment, build_caption_clips
from video_assembly.config import CaptionConfig


class TestParseTimingMetadata:
    def test_basic_parsing(self):
        metadata = {
            "segments": [
                {"start": 0.0, "end": 2.5, "text": "Hello world"},
                {"start": 2.5, "end": 5.0, "text": "Second line"},
            ]
        }
        segments = parse_timing_metadata(metadata)
        assert len(segments) == 2
        assert segments[0].start == 0.0
        assert segments[0].end == 2.5
        assert segments[0].text == "Hello world"
        assert segments[1].text == "Second line"

    def test_empty_metadata(self):
        assert parse_timing_metadata({}) == []
        assert parse_timing_metadata({"segments": []}) == []

    def test_strips_whitespace(self):
        metadata = {"segments": [{"start": 0.0, "end": 1.0, "text": "  hello  "}]}
        segs = parse_timing_metadata(metadata)
        assert segs[0].text == "hello"

    def test_skips_empty_text(self):
        metadata = {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": ""},
                {"start": 1.0, "end": 2.0, "text": "   "},
                {"start": 2.0, "end": 3.0, "text": "valid"},
            ]
        }
        segs = parse_timing_metadata(metadata)
        assert len(segs) == 1
        assert segs[0].text == "valid"

    def test_float_coercion(self):
        metadata = {"segments": [{"start": "1", "end": "3.5", "text": "test"}]}
        segs = parse_timing_metadata(metadata)
        assert segs[0].start == 1.0
        assert segs[0].end == 3.5

    def test_caption_segment_is_dataclass(self):
        seg = CaptionSegment(start=1.0, end=3.0, text="Test")
        assert seg.start == 1.0
        assert seg.end == 3.0
        assert seg.text == "Test"


class TestBuildCaptionClips:
    def test_empty_segments_returns_empty(self):
        config = CaptionConfig()
        clips = build_caption_clips([], config, (1080, 1920))
        assert clips == []

    def test_returns_list(self):
        """build_caption_clips should return a list (may be empty if moviepy/ImageMagick unavailable)."""
        config = CaptionConfig()
        segments = [CaptionSegment(0.0, 2.0, "Hello")]
        result = build_caption_clips(segments, config, (1080, 1920))
        assert isinstance(result, list)

    def test_skips_zero_duration_segment(self):
        config = CaptionConfig()
        # start == end → zero duration → should be skipped
        segments = [CaptionSegment(2.0, 2.0, "No duration")]
        result = build_caption_clips(segments, config, (1080, 1920))
        assert result == []

    def test_skips_negative_duration_segment(self):
        config = CaptionConfig()
        segments = [CaptionSegment(5.0, 3.0, "Negative")]
        result = build_caption_clips(segments, config, (1080, 1920))
        assert result == []
