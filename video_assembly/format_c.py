"""
Format C: Split-screen before/after video renderer.

Produces a 1080×1920 MP4 with:
  - Left panel  (40 % width, 432 px): BEFORE media + red label
  - Right panel (60 % width, 648 px): AFTER  media + green label
  - Animated vertical divider line that slides in from top at t=1 s
  - Word-by-word captions synced from ElevenLabs timing metadata
  - CTA overlay on the final 3 s (shared spec with Format A/B)
"""
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, TextClip, VideoClip, VideoFileClip

from .captions import build_caption_clips, parse_timing_metadata
from .config import CaptionConfig
from .encoder import TARGET_FPS, TARGET_HEIGHT, TARGET_WIDTH
from .format_b import _build_cta_clip  # shared CTA helper

logger = logging.getLogger(__name__)

LEFT_RATIO = 0.40
LEFT_W = int(TARGET_WIDTH * LEFT_RATIO)   # 432
RIGHT_W = TARGET_WIDTH - LEFT_W           # 648

DIVIDER_W = 4                             # pixels wide
DIVIDER_SLIDE_START = 1.0                 # seconds when divider starts sliding
DIVIDER_SLIDE_DURATION = 0.35             # seconds to fully appear

LABEL_FONT_SIZE = 48
LABEL_PADDING = 24
CTA_DURATION = 3.0


def _fit_panel(clip, panel_w: int, panel_h: int):
    """Resize-and-crop-fill a MoviePy clip to (panel_w × panel_h)."""
    w, h = clip.size
    target_ratio = panel_w / panel_h
    src_ratio = w / h

    if src_ratio > target_ratio:
        new_w = int(h * target_ratio)
        x_center = w / 2
        clip = clip.cropped(x1=x_center - new_w / 2, x2=x_center + new_w / 2)
    elif src_ratio < target_ratio:
        new_h = int(w / target_ratio)
        y_center = h / 2
        clip = clip.cropped(y1=y_center - new_h / 2, y2=y_center + new_h / 2)

    return clip.resized((panel_w, panel_h))


def _load_media_clip(media_path: str, duration: float, panel_w: int, panel_h: int):
    """
    Load before/after media as a MoviePy clip cropped to the panel size.
    Accepts static images (.png/.jpg/.jpeg) or video files (.mp4/.mov/.avi).
    """
    from moviepy import concatenate_videoclips

    path = Path(media_path)
    suffix = path.suffix.lower()

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        clip = ImageClip(str(path)).with_duration(duration)
    else:
        raw = VideoFileClip(str(path), audio=False)
        if raw.duration < duration:
            repeats = int(duration / raw.duration) + 1
            clip = concatenate_videoclips([raw] * repeats)
        else:
            clip = raw
        clip = clip.subclipped(0, duration)

    return _fit_panel(clip, panel_w, panel_h)


def _build_divider_clip(video_duration: float) -> list:
    """
    Return a list containing a white vertical divider VideoClip whose height
    grows from 0 to TARGET_HEIGHT starting at DIVIDER_SLIDE_START.
    """
    try:
        import numpy as np
    except ImportError:
        return []

    x_pos = LEFT_W - DIVIDER_W // 2

    def make_frame(t):
        frame = np.zeros((TARGET_HEIGHT, DIVIDER_W, 3), dtype=np.uint8)
        elapsed = t - DIVIDER_SLIDE_START
        if elapsed <= 0:
            return frame
        progress = min(1.0, elapsed / DIVIDER_SLIDE_DURATION)
        visible_h = int(TARGET_HEIGHT * progress)
        frame[:visible_h, :] = 255
        return frame

    clip = (
        VideoClip(make_frame, duration=video_duration)
        .with_fps(TARGET_FPS)
        .with_position((x_pos, 0))
    )
    return [clip]


def _build_label_clip(text: str, color: str, x: int, video_duration: float) -> list:
    """Return a TextClip for BEFORE/AFTER labels pinned to the top of a panel."""
    try:
        lbl = TextClip(
            font="Arial-Bold",
            text=text,
            font_size=LABEL_FONT_SIZE,
            color=color,
            stroke_color="black",
            stroke_width=2,
            duration=video_duration,
        )
    except Exception as exc:
        logger.warning("Label clip %r failed: %s", text, exc)
        return []

    return [lbl.with_position((x + LABEL_PADDING, LABEL_PADDING))]


def assemble_format_c(
    audio_path: str,
    timing_metadata: Union[str, dict],
    before_media: str,
    after_media: str,
    output_path: str,
    cta_text: str = "Follow for more AI tips!",
    tmp_dir: Optional[Path] = None,
) -> str:
    """
    Assemble a Format C split-screen before/after video.

    Args:
        audio_path: Path to ElevenLabs voice-over MP3/WAV.
        timing_metadata: Path to timing JSON or dict with "segments"/"duration".
        before_media: Path to before-state image or video clip.
        after_media: Path to after-state image or video clip.
        output_path: Destination MP4 path.
        cta_text: Call-to-action overlay shown in the final 3 s.
        tmp_dir: Optional temp directory for intermediate files.

    Returns:
        Absolute path to the produced MP4.
    """
    if tmp_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="va_fmt_c_"))
    else:
        tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # ── Load timing metadata ──────────────────────────────────────────────────
    if isinstance(timing_metadata, (str, Path)):
        with open(timing_metadata) as fh:
            metadata = json.load(fh)
    else:
        metadata = dict(timing_metadata)

    audio_clip = AudioFileClip(audio_path)
    audio_duration = audio_clip.duration
    audio_clip.close()

    if "duration" in metadata:
        target_duration = min(float(metadata["duration"]), 60.0)
    else:
        target_duration = min(audio_duration, 60.0)

    logger.info(
        "Format C: duration=%.1fs  before=%s  after=%s",
        target_duration, before_media, after_media,
    )

    # ── Load and position panel clips ─────────────────────────────────────────
    left_clip = _load_media_clip(before_media, target_duration, LEFT_W, TARGET_HEIGHT)
    right_clip = _load_media_clip(after_media, target_duration, RIGHT_W, TARGET_HEIGHT)

    left_clip = left_clip.with_position((0, 0))
    right_clip = right_clip.with_position((LEFT_W, 0))

    # ── Labels ────────────────────────────────────────────────────────────────
    before_label = _build_label_clip("BEFORE", "red", 0, target_duration)
    after_label = _build_label_clip("AFTER", "#00cc44", LEFT_W, target_duration)

    # ── Animated divider ──────────────────────────────────────────────────────
    divider_clips = _build_divider_clip(target_duration)

    # ── Caption clips ─────────────────────────────────────────────────────────
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

    # ── Composite ─────────────────────────────────────────────────────────────
    layers = (
        [left_clip, right_clip]
        + before_label
        + after_label
        + divider_clips
        + caption_clips
        + cta_clips
    )
    video = CompositeVideoClip(layers, size=(TARGET_WIDTH, TARGET_HEIGHT))
    video = video.subclipped(0, target_duration)

    # ── Attach audio ──────────────────────────────────────────────────────────
    audio = AudioFileClip(audio_path)
    audio = audio.subclipped(0, min(audio.duration, target_duration))
    video = video.with_audio(audio)

    # ── Encode ────────────────────────────────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    logger.info("Format C: encoding → %s", output_path)
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

    logger.info("Format C done: %s", output_path)
    return str(Path(output_path).resolve())
