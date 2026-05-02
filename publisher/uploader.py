"""Video uploader with retry logic and structured logging."""
import os
import time
from pathlib import Path
from typing import Optional

from .config import NicheConfig, load_niche_config
from .logger import PostLogger
from .tiktok_client import TikTokAPIError, TikTokContentClient

MAX_RETRIES = 3
BASE_BACKOFF = 2.0  # seconds; actual wait = BASE_BACKOFF ** attempt


def _build_post_url(open_id: str, post_id: str) -> str:
    return f"https://www.tiktok.com/@{open_id}/video/{post_id}"


class VideoUploader:
    def __init__(
        self,
        access_token: Optional[str] = None,
        log_file: Optional[str | Path] = None,
    ):
        token = access_token or os.environ.get("TIKTOK_ACCESS_TOKEN")
        if not token:
            raise ValueError("TIKTOK_ACCESS_TOKEN env var or access_token param required")
        self.client = TikTokContentClient(token)
        self.logger = PostLogger(log_file)

    def upload(
        self,
        video_path: str | Path,
        niche_name: str,
        topic: str,
        niche_config: NicheConfig,
        open_id: str = "",
    ) -> dict:
        """Upload one MP4 and return {"post_id": ..., "url": ...}.

        Retries up to MAX_RETRIES times with exponential backoff.
        Raises the last exception if all retries are exhausted.
        """
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {path}")

        title = niche_config.format_title(topic)
        last_error: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                status = self.client.publish_video(
                    video_path=path,
                    title=title,
                    privacy_level=niche_config.privacy_level,
                    disable_duet=niche_config.disable_duet,
                    disable_comment=niche_config.disable_comment,
                    disable_stitch=niche_config.disable_stitch,
                )
                post_id = str(
                    status.get("publicaly_available_post_id")
                    or status.get("post_id")
                    or status.get("publish_id")
                    or "unknown"
                )
                url = _build_post_url(open_id, post_id) if open_id else f"https://www.tiktok.com/video/{post_id}"
                self.logger.log_success(niche_name, topic, post_id, url, path)
                return {"post_id": post_id, "url": url}

            except (TikTokAPIError, Exception) as exc:
                last_error = exc
                self.logger.log_failure(niche_name, topic, str(exc), attempt, path)
                if attempt < MAX_RETRIES:
                    wait = BASE_BACKOFF**attempt
                    self.logger.log_retry(niche_name, topic, attempt + 1, wait)
                    time.sleep(wait)

        raise last_error  # type: ignore[misc]

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        niche_name: str,
        video_path: str | Path,
        topic: str,
        access_token: Optional[str] = None,
        log_file: Optional[str | Path] = None,
        open_id: str = "",
    ) -> dict:
        """Convenience factory: load config, create uploader, upload, return result."""
        niche_cfg = load_niche_config(config_path, niche_name)
        uploader = cls(access_token=access_token, log_file=log_file)
        return uploader.upload(video_path, niche_name, topic, niche_cfg, open_id=open_id)
