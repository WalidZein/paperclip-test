import logging
import tempfile
from pathlib import Path
from typing import List, Optional

from .captions import build_caption_clips, CaptionSegment

logger = logging.getLogger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30


def assemble_and_encode(
    footage_paths: List[str],
    audio_path: str,
    caption_segments: List[CaptionSegment],
    caption_config,
    output_path: str,
    target_duration: float,
    tmp_dir: Optional[Path] = None,
    bg_audio_path: Optional[str] = None,
) -> str:
    """
    Assemble footage clips with captions and audio into a 1080×1920 H.264 MP4.

    Returns the path to the output file.
    """
    from moviepy.editor import (
        VideoFileClip,
        AudioFileClip,
        CompositeVideoClip,
        concatenate_videoclips,
        ColorClip,
    )

    if tmp_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="va_encode_"))

    logger.info(
        "Assembling %d footage clip(s) → %.1fs @ %dx%d",
        len(footage_paths), target_duration, TARGET_WIDTH, TARGET_HEIGHT,
    )

    # ── 1. Build video timeline ──────────────────────────────────────────────
    raw_clips = []
    elapsed = 0.0

    for path in footage_paths:
        if elapsed >= target_duration:
            break
        try:
            clip = VideoFileClip(path, audio=False)
            use_duration = min(clip.duration, target_duration - elapsed)
            clip = clip.subclip(0, use_duration)
            clip = _fit_to_tiktok(clip)
            raw_clips.append(clip)
            elapsed += use_duration
            logger.debug("Added clip %s (%.1fs)", path, use_duration)
        except Exception as exc:
            logger.warning("Skipping clip %s: %s", path, exc)

    # Pad with black to reach target_duration
    if elapsed < target_duration:
        pad_duration = target_duration - elapsed
        logger.info("Padding %.1fs of black background", pad_duration)
        black = ColorClip(
            size=(TARGET_WIDTH, TARGET_HEIGHT),
            color=(0, 0, 0),
            duration=pad_duration,
        )
        raw_clips.append(black)

    if not raw_clips:
        logger.warning("No clips loaded at all — using full black background")
        raw_clips = [
            ColorClip(
                size=(TARGET_WIDTH, TARGET_HEIGHT),
                color=(0, 0, 0),
                duration=target_duration,
            )
        ]

    video = concatenate_videoclips(raw_clips, method="compose")
    video = video.subclip(0, min(target_duration, video.duration))

    # ── 2. Composite captions ────────────────────────────────────────────────
    caption_clips = build_caption_clips(
        caption_segments,
        caption_config,
        video_size=(TARGET_WIDTH, TARGET_HEIGHT),
    )
    if caption_clips:
        logger.info("Compositing %d caption layer(s)", len(caption_clips))
        video = CompositeVideoClip(
            [video] + caption_clips,
            size=(TARGET_WIDTH, TARGET_HEIGHT),
        )

    # ── 3. Attach audio ──────────────────────────────────────────────────────
    if bg_audio_path:
        from .audio_mixer import mix_voice_and_music
        logger.info("Mixing voice with background music: %s", bg_audio_path)
        audio = mix_voice_and_music(audio_path, bg_audio_path, target_duration)
    else:
        audio = AudioFileClip(audio_path)
        audio = audio.subclip(0, min(audio.duration, target_duration))
    video = video.set_audio(audio)

    # ── 4. Encode to H.264 MP4 ───────────────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    logger.info("Encoding → %s", output_path)
    video.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        fps=TARGET_FPS,
        preset="fast",
        ffmpeg_params=["-crf", "23", "-pix_fmt", "yuv420p"],
        logger=None,
    )

    # ── Cleanup ──────────────────────────────────────────────────────────────
    for clip in raw_clips:
        try:
            clip.close()
        except Exception:
            pass
    audio.close()
    video.close()

    logger.info("Done: %s", output_path)
    return output_path


def _fit_to_tiktok(clip):
    """Crop-to-fill then resize to 1080×1920 without distortion."""
    target_ratio = TARGET_WIDTH / TARGET_HEIGHT  # ≈ 0.5625  (9:16 portrait)
    w, h = clip.size
    src_ratio = w / h

    if src_ratio > target_ratio:
        # Source is wider → crop sides
        new_w = int(h * target_ratio)
        x_center = w / 2
        clip = clip.crop(x1=x_center - new_w / 2, x2=x_center + new_w / 2)
    elif src_ratio < target_ratio:
        # Source is taller → crop top/bottom
        new_h = int(w / target_ratio)
        y_center = h / 2
        clip = clip.crop(y1=y_center - new_h / 2, y2=y_center + new_h / 2)

    return clip.resize((TARGET_WIDTH, TARGET_HEIGHT))
