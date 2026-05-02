import hashlib
import json
import os
import logging
import tempfile
import requests
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

PEXELS_VIDEO_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_SEARCH = "https://pixabay.com/api/videos/"

# Keywords matched to each content niche; used by get_clip() to drive Pexels search.
NICHE_KEYWORDS: dict[str, List[str]] = {
    "dark_psychology": ["dark room", "shadows", "eye closeup", "hands"],
    "dark psychology": ["dark room", "shadows", "eye closeup", "hands"],
    "finance": ["city skyline", "money", "charts", "desk"],
    "ai": ["laptop", "code", "futuristic", "circuit"],
    "tech": ["laptop", "code", "futuristic", "circuit"],
    "ai/tech": ["laptop", "code", "futuristic", "circuit"],
}

_MIN_RESOLUTION = 1080  # minimum pixels on the shorter axis (enforces "1080p" requirement)
_KEYWORD_INDEX_FILE = "keyword_index.json"


@dataclass
class FootageClip:
    path: str
    duration: float
    source: str   # "pexels" or "pixabay"
    keyword: str


class StockFootageFetcher:
    def __init__(
        self,
        pexels_api_key: Optional[str] = None,
        pixabay_api_key: Optional[str] = None,
        cache_dir: Optional[Path] = None,
    ):
        self.pexels_key = pexels_api_key or os.environ.get("PEXELS_API_KEY", "")
        self.pixabay_key = pixabay_api_key or os.environ.get("PIXABAY_API_KEY", "")
        self.cache_dir = cache_dir or Path(tempfile.mkdtemp(prefix="stock_footage_"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._keyword_index: dict[str, str] = self._load_keyword_index()

    # ── Public compositor-facing API ─────────────────────────────────────────

    def get_clip(self, niche: str, topic_tags: List[str]) -> Optional[str]:
        """Return a local path to a portrait clip matched to niche + topic tags.

        Cache hit (keyword seen before): returns within ~1s.
        Cache miss (API fetch + download): returns within ~30s.
        """
        keywords = NICHE_KEYWORDS.get(niche.lower(), []) + list(topic_tags)
        if not keywords:
            logger.warning("No keywords derived for niche=%r tags=%r", niche, topic_tags)
            return None

        # Check keyword index: return first cached path that still exists on disk.
        for kw in keywords:
            key = self._kw_hash(kw)
            cached_filename = self._keyword_index.get(key)
            if cached_filename:
                cached_path = self.cache_dir / cached_filename
                if cached_path.exists():
                    logger.debug("Cache hit for keyword=%r → %s", kw, cached_path)
                    return str(cached_path)
                # Stale entry — remove it so we re-fetch.
                del self._keyword_index[key]
                self._save_keyword_index()

        # Cache miss: fetch from Pexels for each keyword until we get a clip.
        for kw in keywords:
            clip = self._fetch_pexels(kw, max_duration=30.0)
            if clip:
                # Record keyword → filename so next call returns immediately.
                self._keyword_index[self._kw_hash(kw)] = Path(clip.path).name
                self._save_keyword_index()
                return clip.path

        logger.warning(
            "No Pexels clip found for niche=%r with keywords=%r", niche, keywords
        )
        return None

    # ── Duration-based batch fetcher (used by assembler) ────────────────────

    def fetch_for_duration(
        self, keywords: List[str], target_duration: float
    ) -> List[FootageClip]:
        """Fetch enough clips to cover target_duration seconds."""
        clips: List[FootageClip] = []
        total = 0.0
        attempts = 0
        max_attempts = max(len(keywords) * 4, 12)

        while total < target_duration and attempts < max_attempts:
            keyword = keywords[attempts % len(keywords)]
            attempts += 1
            needed = target_duration - total

            clip = self._fetch_single(keyword, max_duration=min(needed + 5, 30))
            if clip:
                clips.append(clip)
                total += clip.duration
                logger.info(
                    "Fetched clip for '%s': %.1fs (total: %.1fs / %.1fs)",
                    keyword, clip.duration, total, target_duration,
                )

        if total < target_duration:
            logger.warning(
                "Only fetched %.1fs of footage (need %.1fs) — pipeline will pad with black",
                total, target_duration,
            )

        return clips

    # ── Internal fetchers ────────────────────────────────────────────────────

    def _fetch_single(self, keyword: str, max_duration: float = 30.0) -> Optional[FootageClip]:
        """Try Pexels first, fall back to Pixabay."""
        if self.pexels_key:
            clip = self._fetch_pexels(keyword, max_duration)
            if clip:
                return clip
        if self.pixabay_key:
            clip = self._fetch_pixabay(keyword, max_duration)
            if clip:
                return clip
        return None

    def _fetch_pexels(self, keyword: str, max_duration: float) -> Optional[FootageClip]:
        try:
            resp = requests.get(
                PEXELS_VIDEO_SEARCH,
                headers={"Authorization": self.pexels_key},
                params={"query": keyword, "per_page": 15, "orientation": "portrait"},
                timeout=15,
            )
            resp.raise_for_status()
            videos = resp.json().get("videos", [])

            for video in videos:
                duration = float(video.get("duration", 0))
                if duration < 3:
                    continue
                files = video.get("video_files", [])
                chosen = self._best_pexels_file(files)
                if not chosen:
                    continue
                path = self._download(chosen["link"], f"pexels_{video['id']}.mp4")
                if path:
                    return FootageClip(
                        path=path,
                        duration=min(duration, max_duration),
                        source="pexels",
                        keyword=keyword,
                    )
        except Exception as exc:
            logger.warning("Pexels fetch failed for '%s': %s", keyword, exc)
        return None

    def _best_pexels_file(self, files: list) -> Optional[dict]:
        """Prefer portrait files ≥1080p, then landscape ≥1080p as center-crop fallback."""
        # Filter to minimum 1080p (shorter axis must be ≥ _MIN_RESOLUTION).
        hd = [
            f for f in files
            if min(f.get("width", 0), f.get("height", 0)) >= _MIN_RESOLUTION
        ]
        candidates = hd if hd else []
        if not candidates:
            return None

        portrait = [f for f in candidates if f.get("width", 1) < f.get("height", 0)]
        ranked = portrait or candidates
        ranked.sort(key=lambda f: f.get("height", 0), reverse=True)
        return ranked[0]

    def _fetch_pixabay(self, keyword: str, max_duration: float) -> Optional[FootageClip]:
        try:
            resp = requests.get(
                PIXABAY_VIDEO_SEARCH,
                params={
                    "key": self.pixabay_key,
                    "q": keyword,
                    "per_page": 8,
                    "video_type": "film",
                },
                timeout=15,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])

            for hit in hits:
                duration = float(hit.get("duration", 0))
                if duration < 3:
                    continue
                videos = hit.get("videos", {})
                url = None
                for quality in ("medium", "large", "small", "tiny"):
                    entry = videos.get(quality, {})
                    if entry.get("url"):
                        url = entry["url"]
                        break
                if not url:
                    continue
                path = self._download(url, f"pixabay_{hit['id']}.mp4")
                if path:
                    return FootageClip(
                        path=path,
                        duration=min(duration, max_duration),
                        source="pixabay",
                        keyword=keyword,
                    )
        except Exception as exc:
            logger.warning("Pixabay fetch failed for '%s': %s", keyword, exc)
        return None

    def _download(self, url: str, filename: str) -> Optional[str]:
        cache_path = self.cache_dir / filename
        if cache_path.exists():
            return str(cache_path)
        try:
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(cache_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
            return str(cache_path)
        except Exception as exc:
            logger.warning("Download failed for %s: %s", url, exc)
            if cache_path.exists():
                cache_path.unlink(missing_ok=True)
            return None

    # ── Keyword index helpers ─────────────────────────────────────────────────

    @staticmethod
    def _kw_hash(keyword: str) -> str:
        return hashlib.sha256(keyword.lower().strip().encode()).hexdigest()[:16]

    def _load_keyword_index(self) -> dict:
        index_path = self.cache_dir / _KEYWORD_INDEX_FILE
        if index_path.exists():
            try:
                return json.loads(index_path.read_text())
            except Exception:
                return {}
        return {}

    def _save_keyword_index(self) -> None:
        index_path = self.cache_dir / _KEYWORD_INDEX_FILE
        try:
            index_path.write_text(json.dumps(self._keyword_index, indent=2))
        except Exception as exc:
            logger.warning("Could not persist keyword index: %s", exc)
