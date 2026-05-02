"""
FFmpeg-based video composition pipeline for Format A (text-overlay).

Format A layout:
  - Background stock footage looped to voice duration, 9:16 crop-to-fill
  - 40% opacity black overlay across the full frame
  - Hook text centered on screen at t=0 (72pt bold white, 2px black shadow)
  - Word-by-word caption overlay synced to ElevenLabs word timestamps
  - "Follow for more" CTA in the final 3 seconds
  - Voice audio + optional music bed mixed at -18 dB
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30

DARK_OVERLAY_OPACITY = 0.4
HOOK_FONT_SIZE = 72
HOOK_DISPLAY_SECS = 2.5
CAPTION_FONT_SIZE = 48
CTA_FONT_SIZE = 54
CTA_DISPLAY_SECS = 3.0
MUSIC_BED_DB = -18

# Common bold font locations for macOS and Linux
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
]


# ── Public API ────────────────────────────────────────────────────────────────


def compose_format_a(
    background_clip: str,
    voice_audio: str,
    word_timestamps: Union[str, Path, dict],
    output_path: str,
    hook_text: str,
    cta_text: str = "Follow for more",
    music_bed: Optional[str] = None,
    tmp_dir: Optional[Path] = None,
) -> str:
    """
    Compose a Format A TikTok video using a direct FFmpeg subprocess pipeline.

    Args:
        background_clip: Path to background video (any format/resolution; will be
            looped to fill the voice duration and cropped to 9:16).
        voice_audio: Path to voice-over MP3 (sets output duration, max 60 s).
        word_timestamps: Path to JSON file or a dict.  Supported shapes:

            ``{"words": [{"word": str, "start": float, "end": float}, ...]}``

            ``{"segments": [{"start": float, "end": float, "text": str}, ...]}``

            ElevenLabs alignment:
            ``{"alignment": {"characters": [...],
                             "character_start_times_seconds": [...],
                             "character_end_times_seconds": [...]}}``

        output_path: Destination path for the 1080×1920 H.264/AAC MP4.
        hook_text: Opening sentence displayed centered at t=0 (72pt bold white).
        cta_text: Text shown in the final 3 seconds (default "Follow for more").
        music_bed: Optional path to background music file (mixed at -18 dB).
        tmp_dir: Optional temp directory for intermediate files (.ass subtitle).

    Returns:
        Resolved absolute path of the produced MP4.

    Raises:
        subprocess.CalledProcessError: FFmpeg returned non-zero exit code.
        FileNotFoundError: ``ffmpeg`` or ``ffprobe`` not found on PATH.
    """
    # Parse timestamps
    if isinstance(word_timestamps, (str, Path)):
        with open(word_timestamps) as fh:
            ts_data = json.load(fh)
    else:
        ts_data = dict(word_timestamps)

    words = _parse_word_timestamps(ts_data)

    # Duration from voice; cap at 60 s (TikTok limit)
    duration = _ffprobe_duration(voice_audio)
    if words:
        last_end = max((w["end"] for w in words), default=0.0)
        duration = max(duration, last_end)
    duration = min(duration, 60.0)
    logger.info("Composition duration: %.2fs  words: %d", duration, len(words))

    # Temp directory for intermediate files
    own_tmpdir: Optional[str] = None
    if tmp_dir is None:
        own_tmpdir = tempfile.mkdtemp(prefix="va_format_a_")
        tmp_dir = Path(own_tmpdir)
    else:
        tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Write ASS subtitle file for word captions
    ass_path = tmp_dir / "captions.ass"
    ass_path.write_text(_build_ass_content(words), encoding="utf-8")
    logger.debug("Wrote %d word caption(s) → %s", len(words), ass_path)

    font_path = _find_bold_font()
    if not font_path:
        logger.warning("No bold font file found; drawtext will rely on fontconfig")

    cmd = _build_ffmpeg_cmd(
        background_clip=background_clip,
        voice_audio=voice_audio,
        music_bed=music_bed,
        ass_path=str(ass_path),
        output_path=output_path,
        duration=duration,
        hook_text=hook_text,
        cta_text=cta_text,
        font_path=font_path,
    )

    logger.info("Running FFmpeg pipeline → %s", output_path)
    logger.debug("FFmpeg cmd: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        logger.error("FFmpeg failed (exit %d):\n%s", exc.returncode, exc.stderr[-4000:])
        raise

    logger.info("Composition complete → %s", output_path)
    return str(Path(output_path).resolve())


# ── Timestamp parsers ─────────────────────────────────────────────────────────


def _parse_word_timestamps(data: dict) -> List[dict]:
    """Normalize various timestamp dict shapes to ``[{word, start, end}]``."""
    if "words" in data:
        out = []
        for w in data["words"]:
            word = (w.get("word") or w.get("text") or "").strip()
            if word:
                out.append({"word": word, "start": float(w["start"]), "end": float(w["end"])})
        return out

    if "segments" in data:
        out = []
        for s in data["segments"]:
            text = (s.get("text") or "").strip()
            if text:
                out.append({"word": text, "start": float(s["start"]), "end": float(s["end"])})
        return out

    if "alignment" in data:
        return _parse_elevenlabs_alignment(data["alignment"])

    return []


def _parse_elevenlabs_alignment(alignment: dict) -> List[dict]:
    """Convert ElevenLabs character-level alignment to word-level timestamps."""
    chars = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])

    if not chars or len(chars) != len(starts):
        return []

    words: List[dict] = []
    current_word = ""
    word_start: Optional[float] = None
    word_end: Optional[float] = None

    for ch, s, e in zip(chars, starts, ends):
        if ch in (" ", "\n", "\t"):
            if current_word and word_start is not None:
                words.append({"word": current_word, "start": word_start, "end": word_end})
                current_word = ""
                word_start = None
        else:
            current_word += ch
            if word_start is None:
                word_start = float(s)
            word_end = float(e)

    if current_word and word_start is not None:
        words.append({"word": current_word, "start": word_start, "end": word_end})

    return words


# ── ASS subtitle generation ───────────────────────────────────────────────────


def _build_ass_content(words: List[dict]) -> str:
    """
    Generate an ASS subtitle file for word-by-word captions.

    Style: bold white 48pt, black 2px outline+shadow, bottom-center at 60px margin.
    """
    def _fmt_time(secs: float) -> str:
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = secs % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    # ASS colors: &HAABBGGRR  (white=&H00FFFFFF, black=&H00000000)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {TARGET_WIDTH}\n"
        f"PlayResY: {TARGET_HEIGHT}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{CAPTION_FONT_SIZE},"
        "&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,2,2,2,80,80,60,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = [header]
    for w in words:
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        start = float(w["start"])
        end = float(w["end"])
        if end <= start:
            continue
        # Strip ASS control characters
        text = text.replace("\\", "").replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Default,,0,0,0,,{text}")

    return "\n".join(lines) + "\n"


# ── FFmpeg helpers ────────────────────────────────────────────────────────────


def _ffprobe_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _find_bold_font() -> Optional[str]:
    """Return the first available bold TrueType font path, or None."""
    for path in _FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def _escape_drawtext(text: str) -> str:
    """Escape text for use as an FFmpeg drawtext ``text=`` value."""
    text = text.replace("\\", "\\\\")
    # Replace literal single quotes with the typographic equivalent to avoid
    # breaking FFmpeg's single-quote-delimited option values.
    text = text.replace("'", "’")
    text = text.replace(":", "\\:")
    text = text.replace("[", "\\[").replace("]", "\\]")
    text = text.replace(",", "\\,")
    return text


def _drawtext_filter(
    text: str,
    font_size: int,
    x: str,
    y: str,
    enable: str,
    font_path: Optional[str] = None,
) -> str:
    """Return a ``drawtext=...`` filter string."""
    opts: List[str] = [
        f"text='{_escape_drawtext(text)}'",
        f"fontsize={font_size}",
        "fontcolor=white",
        f"x={x}",
        f"y={y}",
        "shadowcolor=black",
        "shadowx=2",
        "shadowy=2",
        f"enable='{enable}'",
    ]
    if font_path:
        safe = font_path.replace("\\", "\\\\").replace(":", "\\:")
        opts.insert(1, f"fontfile='{safe}'")
    return "drawtext=" + ":".join(opts)


def _build_ffmpeg_cmd(
    background_clip: str,
    voice_audio: str,
    music_bed: Optional[str],
    ass_path: str,
    output_path: str,
    duration: float,
    hook_text: str,
    cta_text: str,
    font_path: Optional[str],
) -> List[str]:
    """Assemble the complete FFmpeg command list for Format A composition."""

    cta_start = max(0.0, duration - CTA_DISPLAY_SECS)

    # ── Video filter chain ────────────────────────────────────────────────────
    # 1. Normalize timestamps from stream_loop, then scale+crop to 9:16 portrait
    scale_crop = (
        f"setpts=PTS-STARTPTS,"
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},"
        f"setsar=1,"
        f"fps={TARGET_FPS}"
    )

    # 2. Dark overlay: 40% opaque black over the full frame
    dark_overlay = "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.4:t=fill"

    # 3. Hook text: 72pt centered, shown for HOOK_DISPLAY_SECS
    video_filters: List[str] = [scale_crop, dark_overlay]

    if hook_text.strip():
        hook = _drawtext_filter(
            text=hook_text,
            font_size=HOOK_FONT_SIZE,
            x="(w-text_w)/2",
            y="(h-text_h)/2",
            enable=f"between(t,0,{HOOK_DISPLAY_SECS})",
            font_path=font_path,
        )
        video_filters.append(hook)

    # 4. Word-by-word captions via ASS subtitle file
    ass_safe = ass_path.replace("\\", "\\\\").replace(":", "\\:")
    video_filters.append(f"ass={ass_safe}")

    # 5. CTA text: near bottom, shown in final CTA_DISPLAY_SECS
    if cta_text.strip():
        cta = _drawtext_filter(
            text=cta_text,
            font_size=CTA_FONT_SIZE,
            x="(w-text_w)/2",
            y="h-text_h-120",
            enable=f"gte(t,{cta_start:.3f})",
            font_path=font_path,
        )
        video_filters.append(cta)

    vf = ",".join(video_filters)

    # ── Inputs ────────────────────────────────────────────────────────────────
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",  # loop background indefinitely; -t limits output
        "-i", background_clip,
        "-i", voice_audio,
    ]
    if music_bed:
        cmd += ["-i", music_bed]

    # ── Filter complex ────────────────────────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if music_bed:
        filter_complex = (
            f"[0:v]{vf}[vout];"
            f"[1:a]anull[voice];"
            f"[2:a]volume={MUSIC_BED_DB}dB[music];"
            "[voice][music]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        audio_map = "[aout]"
    else:
        filter_complex = f"[0:v]{vf}[vout]"
        audio_map = "1:a"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        output_path,
    ]

    return cmd
