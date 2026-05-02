"""Mix voice-over with a background music bed: -18 dB, fade, loop, ducking."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from moviepy.audio.AudioClip import AudioClip

logger = logging.getLogger(__name__)

# Mixing constants
MUSIC_BED_DB = -18.0        # flat music level under voice
DUCK_DB = -3.0              # additional attenuation during loud voice
DUCK_THRESHOLD_DB = -12.0   # voice RMS threshold that triggers ducking (per window)
FADE_IN_SEC = 0.5
FADE_OUT_SEC = 1.0
SAMPLE_RATE = 44100
ANALYSIS_WINDOW_SEC = 0.01  # 10 ms analysis windows for ducking


def mix_voice_and_music(
    voice_path: str,
    music_path: str,
    target_duration: float,
) -> "AudioClip":
    """
    Return a MoviePy AudioClip with voice-over mixed over background music.

    Music is placed at -18 dB, looped if shorter than target_duration,
    with 0.5 s fade-in and 1 s fade-out.  Where the voice RMS exceeds
    -12 dBFS the music is ducked by an additional -3 dB with a 50 ms
    attack / 200 ms release envelope.
    """
    from moviepy.editor import AudioFileClip, CompositeAudioClip

    voice = AudioFileClip(voice_path)
    music_raw = AudioFileClip(music_path)

    # Loop or trim music to cover the full target duration
    music = _loop_to_duration(music_raw, target_duration)

    # -18 dB base gain
    base_gain = 10 ** (MUSIC_BED_DB / 20)  # ≈ 0.1259
    music = music.volumex(base_gain)

    # Fades applied to music only
    music = music.audio_fadein(FADE_IN_SEC).audio_fadeout(FADE_OUT_SEC)

    # Ducking: lower music where voice is loud
    try:
        music = _apply_ducking(voice, music)
    except Exception as exc:
        logger.warning("Ducking skipped (non-fatal): %s", exc)

    mixed = CompositeAudioClip([voice, music])
    mixed = mixed.subclip(0, target_duration)

    music_raw.close()
    return mixed


# ── Helpers ──────────────────────────────────────────────────────────────────


def _loop_to_duration(clip, target_duration: float):
    """Return clip looped to exactly target_duration."""
    from moviepy.audio.fx.audio_loop import audio_loop

    if clip.duration >= target_duration:
        return clip.subclip(0, target_duration)
    return audio_loop(clip, duration=target_duration)


def _apply_ducking(voice_clip, music_clip) -> "AudioClip":
    """
    Return a new AudioArrayClip with the music level reduced wherever the
    voice exceeds DUCK_THRESHOLD_DB.  Uses 10 ms analysis windows with an
    asymmetric one-pole smoother (50 ms attack, 200 ms release).
    """
    from moviepy.audio.AudioClip import AudioArrayClip

    fps = SAMPLE_RATE
    threshold = 10 ** (DUCK_THRESHOLD_DB / 20)  # amplitude threshold
    duck_factor = 10 ** (DUCK_DB / 20)           # ≈ 0.7079

    voice_arr = voice_clip.to_soundarray(fps=fps)   # (N, C) or (N,)
    music_arr = music_clip.to_soundarray(fps=fps)

    # Align to the shorter of the two
    n = min(len(voice_arr), len(music_arr))
    voice_arr = voice_arr[:n]
    music_arr = music_arr[:n].copy()

    # Mono amplitude envelope of voice
    if voice_arr.ndim == 2:
        voice_mono = np.abs(voice_arr).max(axis=1)
    else:
        voice_mono = np.abs(voice_arr)

    # Per-window gain (10 ms windows = ~441 samples at 44100 Hz)
    window = max(1, int(fps * ANALYSIS_WINDOW_SEC))
    n_windows = n // window
    window_gains = np.ones(n_windows + 1)
    for i in range(n_windows):
        sl = slice(i * window, (i + 1) * window)
        rms = np.sqrt(np.mean(voice_mono[sl] ** 2))
        if rms > threshold:
            window_gains[i] = duck_factor

    # Asymmetric one-pole smoothing at window rate
    # attack ≈ 50 ms → ~5 windows;  release ≈ 200 ms → ~20 windows
    attack_alpha = 0.5
    release_alpha = 0.1
    smoothed = window_gains.copy()
    for i in range(1, len(smoothed)):
        alpha = attack_alpha if smoothed[i] < smoothed[i - 1] else release_alpha
        smoothed[i] = (1 - alpha) * smoothed[i - 1] + alpha * smoothed[i]

    # Expand window gains to per-sample
    gain_samples = np.repeat(smoothed[:n_windows], window)
    remainder = n - len(gain_samples)
    if remainder > 0:
        gain_samples = np.append(gain_samples, np.full(remainder, smoothed[-1]))

    # Apply gain to music array
    if music_arr.ndim == 2:
        music_arr *= gain_samples[:, np.newaxis]
    else:
        music_arr *= gain_samples

    return AudioArrayClip(music_arr.astype(np.float32), fps=fps)
