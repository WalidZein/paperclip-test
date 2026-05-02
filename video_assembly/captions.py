import logging
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CaptionSegment:
    start: float
    end: float
    text: str


def parse_timing_metadata(metadata: dict) -> List[CaptionSegment]:
    """Parse timing metadata JSON into caption segments."""
    segments = []
    for seg in metadata.get("segments", []):
        text = seg.get("text", "").strip()
        if not text:
            continue
        segments.append(
            CaptionSegment(
                start=float(seg["start"]),
                end=float(seg["end"]),
                text=text,
            )
        )
    return segments


def build_caption_clips(
    segments: List[CaptionSegment],
    config,
    video_size: Tuple[int, int],
) -> list:
    """
    Build a list of MoviePy clips (text + optional background) for each segment.
    Returns an empty list if MoviePy/ImageMagick is unavailable or there are no segments.
    """
    if not segments:
        return []

    try:
        from moviepy.editor import TextClip, ColorClip
    except ImportError:
        logger.warning("moviepy not available — captions skipped")
        return []

    width, height = video_size
    clips = []

    for seg in segments:
        duration = seg.end - seg.start
        if duration <= 0:
            continue

        try:
            txt = TextClip(
                seg.text,
                fontsize=config.font_size,
                font=config.font,
                color=config.color,
                stroke_color=config.stroke_color,
                stroke_width=config.stroke_width,
                method="caption",
                align="center",
                size=(width - 80, None),
            )
        except Exception as exc:
            # ImageMagick / font not available — try simpler fallback
            logger.warning("TextClip failed (%s); trying label method", exc)
            try:
                txt = TextClip(
                    seg.text,
                    fontsize=config.font_size,
                    color=config.color,
                    method="label",
                    align="center",
                )
            except Exception as exc2:
                logger.warning("Caption '%s' skipped: %s", seg.text[:30], exc2)
                continue

        txt_w, txt_h = txt.size

        # Position: center-screen or lower-third
        if config.position == "bottom":
            y_pos = height - txt_h - 80
            pos = ("center", y_pos)
        else:
            pos = "center"

        txt = txt.set_position(pos).set_start(seg.start).set_duration(duration)

        if config.background:
            pad = 16
            try:
                bg = ColorClip(
                    size=(txt_w + pad * 2, txt_h + pad * 2),
                    color=(0, 0, 0),
                ).set_opacity(config.background_opacity)

                if config.position == "bottom":
                    bg_pos = ("center", y_pos - pad)
                else:
                    bg_pos = "center"

                bg = bg.set_position(bg_pos).set_start(seg.start).set_duration(duration)
                clips.append(bg)
            except Exception as exc:
                logger.warning("Background clip failed: %s", exc)

        clips.append(txt)

    return clips
