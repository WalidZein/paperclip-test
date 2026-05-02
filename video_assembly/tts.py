import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io"
_TTS_PATH = "/v1/text-to-speech/{voice_id}/with-timestamps"

# Matches [HOOK], [BODY], [CTA] section markers
_SCRIPT_TAG_RE = re.compile(r"\[(?:HOOK|BODY|CTA)\]\s*", re.IGNORECASE)

# Sensible defaults; override with ELEVENLABS_VOICE_{NICHE_KEY} env vars at runtime
_BUILTIN_VOICE_IDS: Dict[str, str] = {
    "dark_psychology": "pNInz6obpgDQGcFmaJgB",   # Adam — deep, dramatic
    "personal_finance": "ErXwobaYiN019PkySvjV",  # Antoni — clear, professional
    "ai_tech": "VR6AewLTigWG4xSOukaG",           # Arnold — energetic
}


@dataclass
class VoiceProfile:
    niche: str
    voice_id: str
    stability: float
    similarity_boost: float
    style: float
    speed: float


@dataclass
class WordTimestamp:
    word: str
    start_time: float
    end_time: float


class ElevenLabsError(Exception):
    pass


def _get_profile(niche: str) -> VoiceProfile:
    """Return the hard-coded profile for *niche*, with voice_id overridable by env var."""
    key = niche.lower().replace(" & ", "_").replace(" ", "_").replace("&", "")
    env_voice = os.environ.get(f"ELEVENLABS_VOICE_{key.upper()}")
    voice_id = env_voice or _BUILTIN_VOICE_IDS.get(key)
    if voice_id is None:
        raise ValueError(f"Unknown niche '{niche}'. Known keys: {list(_BUILTIN_VOICE_IDS)}")

    profiles: Dict[str, dict] = {
        "dark_psychology": {
            "stability": 0.4,
            "similarity_boost": 0.9,
            "style": 0.8,
            "speed": 0.85,
        },
        "personal_finance": {
            "stability": 0.85,
            "similarity_boost": 0.75,
            "style": 0.0,
            "speed": 1.0,
        },
        "ai_tech": {
            "stability": 0.7,
            "similarity_boost": 0.8,
            "style": 0.0,
            "speed": 1.15,
        },
    }
    settings = profiles[key]
    return VoiceProfile(niche=key, voice_id=voice_id, **settings)


def preprocess_script(text: str) -> str:
    """Remove [HOOK]/[BODY]/[CTA] tags and normalize whitespace."""
    text = _SCRIPT_TAG_RE.sub("", text)
    # Collapse runs of blank lines introduced by removed tags
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chars_to_words(alignment: dict) -> List[WordTimestamp]:
    """Aggregate character-level ElevenLabs alignment data into word timestamps."""
    characters: List[str] = alignment.get("characters", [])
    starts: List[float] = alignment.get("character_start_times_seconds", [])
    ends: List[float] = alignment.get("character_end_times_seconds", [])

    if not characters or len(characters) != len(starts) or len(characters) != len(ends):
        return []

    words: List[WordTimestamp] = []
    buf: List[str] = []
    word_start: Optional[float] = None
    word_end: Optional[float] = None

    for char, t0, t1 in zip(characters, starts, ends):
        if char in (" ", "\n", "\t", "\r"):
            if buf:
                words.append(WordTimestamp(
                    word="".join(buf),
                    start_time=round(word_start, 3),
                    end_time=round(word_end, 3),
                ))
                buf = []
                word_start = None
                word_end = None
        else:
            if not buf:
                word_start = t0
            buf.append(char)
            word_end = t1

    if buf:
        words.append(WordTimestamp(
            word="".join(buf),
            start_time=round(word_start, 3),
            end_time=round(word_end, 3),
        ))

    return words


def generate_tts(
    script: str,
    niche: str,
    output_dir: Path,
    api_key: Optional[str] = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> Tuple[Path, Path]:
    """
    Generate speech for *script* using the voice profile for *niche*.

    Returns (mp3_path, timestamps_json_path).
    Raises ElevenLabsError on unrecoverable API failure.
    """
    resolved_key = os.environ.get("ELEVENLABS_API_KEY", "") if api_key is None else api_key
    api_key = resolved_key
    if not api_key:
        raise ElevenLabsError("ELEVENLABS_API_KEY is not set")

    profile = _get_profile(niche)
    text = preprocess_script(script)
    if not text:
        raise ValueError("Script is empty after preprocessing")

    url = f"{ELEVENLABS_API_URL}{_TTS_PATH.format(voice_id=profile.voice_id)}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": profile.stability,
            "similarity_boost": profile.similarity_boost,
            "style": profile.style,
            "use_speaker_boost": True,
        },
        "speed": profile.speed,
    }

    response_data: Optional[dict] = None
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "ElevenLabs TTS attempt %d/%d — niche=%s voice=%s",
                attempt, max_retries, profile.niche, profile.voice_id,
            )
            resp = requests.post(url, headers=headers, json=payload, timeout=60)

            if resp.status_code == 429:
                wait = retry_delay * attempt
                logger.warning("Rate limited — waiting %.1fs before retry", wait)
                time.sleep(wait)
                continue

            if not resp.ok:
                raise ElevenLabsError(
                    f"API error {resp.status_code}: {resp.text[:300]}"
                )

            response_data = resp.json()
            break

        except ElevenLabsError:
            raise
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries:
                logger.warning("Request failed (%s) — retrying in %.1fs", exc, retry_delay)
                time.sleep(retry_delay)

    if response_data is None:
        raise ElevenLabsError(
            f"ElevenLabs request failed after {max_retries} attempts: {last_exc}"
        ) from last_exc

    # Decode audio
    audio_bytes = base64.b64decode(response_data.get("audio_base64", ""))

    # Derive word timestamps from character-level alignment
    alignment = response_data.get("alignment") or response_data.get("normalized_alignment", {})
    word_timestamps = _chars_to_words(alignment)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    mp3_path = output_dir / f"audio_{profile.niche}.mp3"
    mp3_path.write_bytes(audio_bytes)
    logger.info("Audio written: %s (%d bytes)", mp3_path, len(audio_bytes))

    ts_path = output_dir / f"word_timestamps_{profile.niche}.json"
    ts_path.write_text(
        json.dumps(
            [{"word": w.word, "start_time": w.start_time, "end_time": w.end_time}
             for w in word_timestamps],
            indent=2,
        )
    )
    logger.info("Timestamps written: %s (%d words)", ts_path, len(word_timestamps))

    return mp3_path, ts_path
