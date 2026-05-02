"""Trending audio daily input schema, loading, and mood-based selection."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

NICHE_MOOD_MAP: dict = {
    "dark_psychology": "dramatic",
    "personal_finance": "calm",
    "ai_tech": "energetic",
}
VALID_MOODS = {"dramatic", "calm", "energetic", "neutral"}

DEFAULT_AUDIO_DIR = Path("data/trending_audio")


@dataclass
class TrendingSound:
    tiktok_id: str
    title: str
    artist: str
    mood: str
    file_path: str


@dataclass
class TrendingAudioIndex:
    date: str
    sounds: List[TrendingSound]

    @classmethod
    def load(
        cls,
        audio_dir: Path = DEFAULT_AUDIO_DIR,
        for_date: Optional[date] = None,
    ) -> Optional["TrendingAudioIndex"]:
        """Load today's trending audio JSON, or None if file is missing."""
        if for_date is None:
            for_date = date.today()
        path = Path(audio_dir) / f"{for_date.isoformat()}.json"
        if not path.exists():
            logger.warning(
                "No trending audio file for %s (%s) — proceeding without music",
                for_date, path,
            )
            return None
        with open(path) as fh:
            data = json.load(fh)
        sounds = [TrendingSound(**s) for s in data.get("sounds", [])]
        return cls(date=data["date"], sounds=sounds)


def select_audio_for_niche(
    niche: str,
    index: TrendingAudioIndex,
) -> Optional[TrendingSound]:
    """Return the best mood-matched trending sound for the given niche."""
    mood = NICHE_MOOD_MAP.get(niche, "neutral")
    # 1. Exact mood match
    for sound in index.sounds:
        if sound.mood == mood:
            return sound
    # 2. Neutral fallback
    for sound in index.sounds:
        if sound.mood == "neutral":
            return sound
    # 3. First available
    if index.sounds:
        logger.warning(
            "No mood match for niche '%s' (wanted '%s') — using first available sound",
            niche, mood,
        )
        return index.sounds[0]
    return None
