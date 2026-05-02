"""
Format B: Data visualization card video renderer.

Produces a 1080×1920 MP4 where a Pillow-generated data card slides in from the
right (400ms ease-in-out), sits on a solid-color or gradient background, and is
narrated by the voice-over audio.  Word-by-word captions and a CTA overlay on the
final 3 s match Format A's shared spec.
"""
import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoClip,
)

from .card_renderer import render_card
from .captions import build_caption_clips, parse_timing_metadata
from .config import CaptionConfig
from .encoder import TARGET_FPS, TARGET_HEIGHT, TARGET_WIDTH

logger = logging.getLogger(__name__)

SLIDE_DURATION = 0.4   # seconds for card slide-in
CTA_DURATION = 3.0     # final seconds showing CTA overlay


def _smoothstep(t: float) -> float:
    """Cubic smoothstep easing (ease-in-out), t clamped to [0, 1]."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _gradient_bg_array(
    width: int,
    height: int,
    top_color: Tuple[int, int, int],
    bottom_color: Tuple[int, int, int],
):
    """Return an H×W×3 numpy uint8 array with a vertical linear gradient."""
    import numpy as np

    r = np.linspace(top_color[0], bottom_color[0], height, dtype=np.float32)
    g = np.linspace(top_color[1], bottom_color[1], height, dtype=np.float32)
    b = np.linspace(top_color[2], bottom_color[2], height, dtype=np.float32)
    row = np.stack([r, g, b], axis=-1).astype(np.uint8)
    return np.broadcast_to(row[:, None, :], (height, width, 3)).copy()


def _build_cta_clip(
    cta_text: str,
    duration: float,
    video_duration: float,
    video_size: Tuple[int, int],
) -> list:
    """Return a list with a CTA TextClip shown for the last `duration` seconds."""
    width, height = video_size
    start = max(0.0, video_duration - duration)

    try:
        txt = TextClip(
            font="Arial-Bold",
            text=cta_text,
            font_size=52,
            color="white",
            stroke_color="black",
            stroke_width=3,
            method="caption",
            text_align="center",
            size=(width - 80, None),
            duration=duration,
        )
    except Exception:
        try:
            txt = TextClip(
                font="Arial-Bold",
                text=cta_text,
                font_size=52,
                color="white",
                duration=duration,
            )
        except Exception as exc:
            logger.warning("CTA clip failed: %s", exc)
            return []

    _, txt_h = txt.size
    y_pos = height - txt_h - 60
    return [txt.with_position(("center", y_pos)).with_start(start)]


def assemble_format_b(
    audio_path: str,
    timing_metadata: Union[str, dict],
    card_type: str,
    card_data: Union[Dict, List],
    card_title: str,
    output_path: str,
    bg_top_color: Tuple[int, int, int] = (18, 22, 42),
    bg_bottom_color: Optional[Tuple[int, int, int]] = None,
    cta_text: str = "Follow for more AI tips!",
    tmp_dir: Optional[Path] = None,
) -> str:
    """
    Assemble a Format B data card video.

    Args:
        audio_path: Path to ElevenLabs voice-over MP3/WAV.
        timing_metadata: Path to timing JSON or dict with "segments"/"duration".
        card_type: "tier_list", "comparison_table", or "ranked_list".
        card_data: Data payload for the card (type-specific; see card_renderer).
        card_title: Title text displayed on the card.
        output_path: Destination MP4 path.
        bg_top_color: Top background color (RGB).
        bg_bottom_color: Bottom gradient color; falls back to solid if None.
        cta_text: Call-to-action overlay shown in the final 3 s.
        tmp_dir: Optional temp directory for intermediate files.

    Returns:
        Absolute path to the produced MP4.
    """
    if tmp_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="va_fmt_b_"))
    else:
        tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # ── Load timing metadata ─────────────────────────────────────────────────
    if isinstance(timing_metadata, (str, Path)):
        with open(timing_metadata) as fh:
            metadata = json.load(fh)
    else:
        metadata = dict(timing_metadata)

    # Determine target duration
    audio_clip = AudioFileClip(audio_path)
    audio_duration = audio_clip.duration
    audio_clip.close()

    if "duration" in metadata:
        target_duration = min(float(metadata["duration"]), 60.0)
    else:
        target_duration = min(audio_duration, 60.0)

    logger.info("Format B: duration=%.1fs  card_type=%s", target_duration, card_type)

    # ── Generate card PNG ────────────────────────────────────────────────────
    card_png = str(tmp_dir / "card.png")
    card_img = render_card(card_type, card_title, card_data, output_path=card_png)
    card_w, card_h = card_img.width, card_img.height

    # ── Build background clip ────────────────────────────────────────────────
    use_gradient = bg_bottom_color is not None and bg_bottom_color != bg_top_color
    if use_gradient:
        grad_frame = _gradient_bg_array(TARGET_WIDTH, TARGET_HEIGHT, bg_top_color, bg_bottom_color)

        def _grad_frame(_t):
            return grad_frame

        bg_clip = VideoClip(_grad_frame, duration=target_duration).with_fps(TARGET_FPS)
    else:
        bg_clip = ColorClip(
            size=(TARGET_WIDTH, TARGET_HEIGHT),
            color=bg_top_color,
            duration=target_duration,
        )

    # ── Animate card slide-in ─────────────────────────────────────────────────
    card_x_final = (TARGET_WIDTH - card_w) // 2
    card_y = (TARGET_HEIGHT - card_h) // 2

    def _card_pos(t):
        progress = _smoothstep(t / SLIDE_DURATION) if t < SLIDE_DURATION else 1.0
        x = int(TARGET_WIDTH + (card_x_final - TARGET_WIDTH) * progress)
        return (x, card_y)

    card_clip = (
        ImageClip(card_png)
        .with_duration(target_duration)
        .with_position(_card_pos)
    )

    # ── Caption clips ────────────────────────────────────────────────────────
    caption_segments = parse_timing_metadata(metadata)
    logger.info("Parsed %d caption segment(s)", len(caption_segments))

    caption_cfg = CaptionConfig(
        font="Arial-Bold",
        font_size=58,
        color="white",
        stroke_color="black",
        stroke_width=3,
        position="bottom",
        background=True,
        background_opacity=0.65,
    )
    caption_clips = build_caption_clips(
        caption_segments, caption_cfg, video_size=(TARGET_WIDTH, TARGET_HEIGHT)
    )

    # ── CTA overlay ───────────────────────────────────────────────────────────
    cta_clips = _build_cta_clip(cta_text, CTA_DURATION, target_duration, (TARGET_WIDTH, TARGET_HEIGHT))

    # ── Composite all layers ──────────────────────────────────────────────────
    layers = [bg_clip, card_clip] + caption_clips + cta_clips
    video = CompositeVideoClip(layers, size=(TARGET_WIDTH, TARGET_HEIGHT))
    video = video.subclipped(0, target_duration)

    # ── Attach audio ──────────────────────────────────────────────────────────
    audio = AudioFileClip(audio_path)
    audio = audio.subclipped(0, min(audio.duration, target_duration))
    video = video.with_audio(audio)

    # ── Encode to H.264 MP4 ───────────────────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    logger.info("Format B: encoding → %s", output_path)
    video.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        fps=TARGET_FPS,
        preset="fast",
        ffmpeg_params=["-crf", "23", "-pix_fmt", "yuv420p"],
        logger=None,
    )

    try:
        audio.close()
        video.close()
    except Exception:
        pass

    logger.info("Format B done: %s", output_path)
    return str(Path(output_path).resolve())
