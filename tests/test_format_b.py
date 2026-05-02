"""Tests for Format B: data visualization card video renderer."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video_assembly.card_renderer import (
    generate_comparison_table_card,
    generate_ranked_list_card,
    generate_tier_list_card,
    render_card,
)
from video_assembly.format_b import _smoothstep, assemble_format_b


# ── card_renderer tests ──────────────────────────────────────────────────────

class TestSmoothstep:
    def test_zero(self):
        assert _smoothstep(0.0) == 0.0

    def test_one(self):
        assert _smoothstep(1.0) == 1.0

    def test_half_symmetric(self):
        assert abs(_smoothstep(0.5) - 0.5) < 1e-9

    def test_clamps_below_zero(self):
        assert _smoothstep(-1.0) == 0.0

    def test_clamps_above_one(self):
        assert _smoothstep(2.0) == 1.0

    def test_monotone(self):
        values = [_smoothstep(t / 10) for t in range(11)]
        assert all(values[i] <= values[i + 1] for i in range(10))


class TestTierListCard:
    def test_returns_image(self):
        from PIL import Image
        tiers = {"S": ["Claude", "GPT-4"], "A": ["Gemini"], "B": ["Copilot"]}
        img = generate_tier_list_card("AI Tools", tiers)
        assert isinstance(img, Image.Image)

    def test_dimensions(self):
        from video_assembly.card_renderer import CARD_W
        tiers = {"S": ["Item1"], "A": ["Item2"]}
        img = generate_tier_list_card("Test", tiers)
        assert img.width == CARD_W
        assert img.height > 0

    def test_max_rows_limit(self):
        # 10 tiers but max_rows=4 → only 4 rendered
        tiers = {str(i): [f"item{i}"] for i in range(10)}
        img4 = generate_tier_list_card("T", tiers, max_rows=4)
        img10 = generate_tier_list_card("T", tiers, max_rows=10)
        assert img4.height < img10.height

    def test_empty_tiers(self):
        img = generate_tier_list_card("Empty", {})
        assert img is not None

    def test_save_to_file(self, tmp_path):
        out = str(tmp_path / "tier.png")
        tiers = {"S": ["Top"]}
        img = render_card("tier_list", "Test", tiers, output_path=out)
        assert Path(out).exists()
        assert Path(out).stat().st_size > 0


class TestComparisonTableCard:
    def test_returns_image(self):
        from PIL import Image
        rows = [
            {"feature": "Speed", "before": "Slow", "after": "Fast"},
            {"feature": "Cost", "before": "$$$", "after": "$"},
        ]
        img = generate_comparison_table_card("AI vs Manual", rows)
        assert isinstance(img, Image.Image)

    def test_max_rows_limit(self):
        rows = [{"feature": f"feat{i}", "before": "x", "after": "y"} for i in range(12)]
        img4 = generate_comparison_table_card("T", rows, max_rows=4)
        img12 = generate_comparison_table_card("T", rows, max_rows=12)
        assert img4.height < img12.height


class TestRankedListCard:
    def test_string_items(self):
        from PIL import Image
        img = generate_ranked_list_card("Top Tools", ["ChatGPT", "Claude", "Gemini"])
        assert isinstance(img, Image.Image)

    def test_dict_items_with_score(self):
        from PIL import Image
        items = [{"name": "Claude", "score": "9.8"}, {"name": "GPT-4", "score": "9.5"}]
        img = generate_ranked_list_card("Rankings", items)
        assert isinstance(img, Image.Image)

    def test_max_rows(self):
        items = [f"item{i}" for i in range(10)]
        img3 = generate_ranked_list_card("T", items, max_rows=3)
        img10 = generate_ranked_list_card("T", items, max_rows=10)
        assert img3.height < img10.height


class TestRenderCard:
    def test_tier_list(self):
        img = render_card("tier_list", "Test", {"S": ["A"], "B": ["C"]})
        assert img is not None

    def test_comparison_table(self):
        rows = [{"feature": "F", "before": "B", "after": "A"}]
        img = render_card("comparison_table", "Test", rows)
        assert img is not None

    def test_ranked_list(self):
        img = render_card("ranked_list", "Test", ["A", "B", "C"])
        assert img is not None

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Unknown card_type"):
            render_card("bogus_type", "T", {})

    def test_saves_file(self, tmp_path):
        out = str(tmp_path / "card.png")
        render_card("ranked_list", "Test", ["X"], output_path=out)
        assert Path(out).exists()


# ── assemble_format_b tests ──────────────────────────────────────────────────

class TestAssembleFormatB:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.metadata = {
            "duration": 10.0,
            "segments": [{"start": 0.0, "end": 5.0, "text": "Hello"}],
        }
        self.card_data = {"S": ["ChatGPT"], "A": ["Claude"]}

    @patch("video_assembly.format_b.AudioFileClip")
    @patch("video_assembly.format_b.CompositeVideoClip")
    @patch("video_assembly.format_b.ImageClip")
    @patch("video_assembly.format_b.ColorClip")
    @patch("video_assembly.format_b.render_card")
    def test_calls_write_videofile(
        self, mock_render, mock_color, mock_img, mock_composite, mock_audio
    ):
        # Setup mocks
        mock_render.return_value = MagicMock(width=1000, height=600)
        mock_color.return_value = MagicMock()
        img_clip = MagicMock()
        img_clip.with_duration.return_value = img_clip
        img_clip.with_position.return_value = img_clip
        mock_img.return_value = img_clip

        composite = MagicMock()
        composite.subclipped.return_value = composite
        composite.with_audio.return_value = composite
        mock_composite.return_value = composite

        audio = MagicMock()
        audio.duration = 10.0
        audio.subclipped.return_value = audio
        mock_audio.return_value = audio

        out = str(self.tmp / "out_b.mp4")
        assemble_format_b(
            audio_path="/fake/audio.mp3",
            timing_metadata=self.metadata,
            card_type="tier_list",
            card_data=self.card_data,
            card_title="AI Rankings",
            output_path=out,
        )

        composite.write_videofile.assert_called_once()
        call_kwargs = composite.write_videofile.call_args
        assert call_kwargs[0][0] == out

    @patch("video_assembly.format_b.AudioFileClip")
    @patch("video_assembly.format_b.CompositeVideoClip")
    @patch("video_assembly.format_b.ImageClip")
    @patch("video_assembly.format_b.ColorClip")
    @patch("video_assembly.format_b.render_card")
    def test_duration_capped_at_60s(
        self, mock_render, mock_color, mock_img, mock_composite, mock_audio
    ):
        mock_render.return_value = MagicMock(width=1000, height=600)
        mock_color.return_value = MagicMock()
        img_clip = MagicMock()
        img_clip.with_duration.return_value = img_clip
        img_clip.with_position.return_value = img_clip
        mock_img.return_value = img_clip

        composite = MagicMock()
        composite.subclipped.return_value = composite
        composite.with_audio.return_value = composite
        mock_composite.return_value = composite

        audio = MagicMock()
        audio.duration = 90.0
        audio.subclipped.return_value = audio
        mock_audio.return_value = audio

        long_meta = {"duration": 90.0, "segments": []}
        out = str(self.tmp / "out_b_long.mp4")
        assemble_format_b(
            audio_path="/fake/audio.mp3",
            timing_metadata=long_meta,
            card_type="ranked_list",
            card_data=["A", "B"],
            card_title="Top",
            output_path=out,
        )

        # subclip called with duration ≤ 60 on the composite
        args = composite.subclipped.call_args[0]
        assert args[0] <= 60.0

    @patch("video_assembly.format_b.AudioFileClip")
    @patch("video_assembly.format_b.CompositeVideoClip")
    @patch("video_assembly.format_b.ImageClip")
    @patch("video_assembly.format_b.ColorClip")
    @patch("video_assembly.format_b.render_card")
    def test_gradient_bg_uses_videoclip(
        self, mock_render, mock_color, mock_img, mock_composite, mock_audio
    ):
        """When bg_bottom_color differs from top, a VideoClip is used instead of ColorClip."""
        mock_render.return_value = MagicMock(width=1000, height=600)

        img_clip = MagicMock()
        img_clip.with_duration.return_value = img_clip
        img_clip.with_position.return_value = img_clip
        mock_img.return_value = img_clip

        composite = MagicMock()
        composite.subclipped.return_value = composite
        composite.with_audio.return_value = composite
        mock_composite.return_value = composite

        audio = MagicMock()
        audio.duration = 10.0
        audio.subclipped.return_value = audio
        mock_audio.return_value = audio

        with patch("video_assembly.format_b.VideoClip") as mock_vc:
            vc = MagicMock()
            vc.with_fps.return_value = vc
            mock_vc.return_value = vc

            out = str(self.tmp / "out_gradient.mp4")
            assemble_format_b(
                audio_path="/fake/audio.mp3",
                timing_metadata=self.metadata,
                card_type="tier_list",
                card_data={"S": ["X"]},
                card_title="T",
                output_path=out,
                bg_top_color=(18, 22, 42),
                bg_bottom_color=(50, 10, 80),
            )

        mock_vc.assert_called_once()
        mock_color.assert_not_called()
