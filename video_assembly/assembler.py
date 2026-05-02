import json
import logging
from pathlib import Path
from typing import List, Optional, Union

from .config import NicheProfile
from .stock_footage import StockFootageFetcher
from .captions import parse_timing_metadata
from .encoder import assemble_and_encode
from .pipeline_format_a import compose_format_a

logger = logging.getLogger(__name__)


class VideoAssembler:
    """
    Main entry point for assembling TikTok-ready MP4 videos.

    Example::

        assembler = VideoAssembler(
            pexels_api_key="...",
            pixabay_api_key="...",
        )
        output = assembler.assemble(
            audio_path="voice.mp3",
            timing_metadata="timing.json",
            keywords=["finance", "money", "investing"],
            output_path="output.mp4",
            niche="finance",
        )
    """

    def __init__(
        self,
        pexels_api_key: Optional[str] = None,
        pixabay_api_key: Optional[str] = None,
        profiles_dir: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
    ):
        self.fetcher = StockFootageFetcher(
            pexels_api_key=pexels_api_key,
            pixabay_api_key=pixabay_api_key,
            cache_dir=cache_dir,
        )
        self.profiles_dir = profiles_dir

    def assemble(
        self,
        audio_path: str,
        timing_metadata: Union[str, dict],
        keywords: List[str],
        output_path: str,
        niche: str = "motivation",
        tmp_dir: Optional[Path] = None,
    ) -> str:
        """
        Produce a 1080×1920 TikTok-ready MP4.

        Args:
            audio_path: Path to the voice-over MP3/WAV.
            timing_metadata: Path to JSON file or dict with caption timing.
                Expected shape: {"duration": float, "segments": [{"start", "end", "text"}]}
            keywords: Script keywords used to search stock footage.
            output_path: Destination path for the output MP4.
            niche: Niche identifier (must match a YAML file in profiles/).
            tmp_dir: Optional temp directory for intermediate files.

        Returns:
            Absolute path to the produced MP4.
        """
        # Load timing metadata
        if isinstance(timing_metadata, (str, Path)):
            with open(timing_metadata) as fh:
                metadata = json.load(fh)
        else:
            metadata = timing_metadata

        target_duration = self._get_duration(audio_path, metadata)
        logger.info("Target duration: %.1fs", target_duration)

        if target_duration > 60:
            logger.warning(
                "Target duration %.1fs exceeds 60s TikTok limit — truncating to 60s",
                target_duration,
            )
            target_duration = 60.0

        # Load niche visual profile
        profile = NicheProfile.load(niche, self.profiles_dir)
        logger.info("Loaded niche profile: %s", profile.niche)

        # Merge and deduplicate keywords (script keywords first, then profile extras)
        all_keywords = keywords + profile.video.b_roll_keywords
        seen = set()
        unique_keywords = []
        for kw in all_keywords:
            if kw.lower() not in seen:
                seen.add(kw.lower())
                unique_keywords.append(kw)

        # Fetch stock footage
        logger.info("Searching stock footage for: %s", unique_keywords[:6])
        clips = self.fetcher.fetch_for_duration(unique_keywords, target_duration)
        footage_paths = [c.path for c in clips]

        if not footage_paths:
            logger.warning("No stock footage found — video will use black background")

        # Parse caption timing
        caption_segments = parse_timing_metadata(metadata)
        logger.info("Parsed %d caption segment(s)", len(caption_segments))

        # Assemble + encode
        return assemble_and_encode(
            footage_paths=footage_paths,
            audio_path=audio_path,
            caption_segments=caption_segments,
            caption_config=profile.caption,
            output_path=output_path,
            target_duration=target_duration,
            tmp_dir=tmp_dir,
        )

    def assemble_format_a(
        self,
        audio_path: str,
        word_timestamps: Union[str, dict],
        hook_text: str,
        output_path: str,
        niche: str = "motivation",
        cta_text: str = "Follow for more",
        music_bed: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        tmp_dir: Optional[Path] = None,
    ) -> str:
        """
        Fetch a background clip and produce a Format A (text-overlay) TikTok MP4.

        Wraps :func:`~.pipeline_format_a.compose_format_a` with stock footage
        fetching.  The background clip is looped by the FFmpeg pipeline, so a
        single short clip is sufficient.

        Args:
            audio_path: Path to the voice-over MP3.
            word_timestamps: Path to word-timestamp JSON or dict.  See
                :func:`~.pipeline_format_a.compose_format_a` for supported shapes.
            hook_text: Opening hook sentence shown centered at t=0.
            output_path: Destination for the 1080×1920 MP4.
            niche: Content niche for stock footage search keywords.
            cta_text: Call-to-action text shown in the final 3 seconds.
            music_bed: Optional path to background music (mixed at -18 dB).
            keywords: Extra search keywords to append to niche defaults.
            tmp_dir: Optional temp directory for intermediate files.

        Returns:
            Absolute path of the produced MP4.
        """
        profile = NicheProfile.load(niche, self.profiles_dir)
        all_keywords = (keywords or []) + profile.video.b_roll_keywords

        # Fetch a single clip; it will be looped to fill the full duration
        clips = self.fetcher.fetch_for_duration(all_keywords[:6], target_duration=15.0)
        if not clips:
            raise RuntimeError(
                f"Could not fetch any stock footage for niche={niche!r} "
                f"keywords={all_keywords[:6]!r}. Provide a background_clip manually "
                "or check PEXELS_API_KEY / PIXABAY_API_KEY."
            )

        background_clip = clips[0].path
        logger.info("Using background clip: %s", background_clip)

        return compose_format_a(
            background_clip=background_clip,
            voice_audio=audio_path,
            word_timestamps=word_timestamps,
            output_path=output_path,
            hook_text=hook_text,
            cta_text=cta_text,
            music_bed=music_bed,
            tmp_dir=tmp_dir,
        )

    def _get_duration(self, audio_path: str, metadata: dict) -> float:
        """Determine target duration from metadata hint or audio file."""
        if "duration" in metadata:
            return float(metadata["duration"])

        # Try reading from audio file
        try:
            from moviepy.editor import AudioFileClip
            audio = AudioFileClip(audio_path)
            duration = audio.duration
            audio.close()
            return duration
        except Exception as exc:
            logger.warning("Could not read audio duration: %s", exc)

        # Last resort: derive from last segment end time
        segments = metadata.get("segments", [])
        if segments:
            return float(max(s["end"] for s in segments))

        return 30.0
