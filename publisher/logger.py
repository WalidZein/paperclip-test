"""Structured JSON post logger."""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_stdout_logger = logging.getLogger("publisher.posts")
_stdout_logger.setLevel(logging.INFO)
if not _stdout_logger.handlers:
    _stdout_logger.addHandler(logging.StreamHandler(sys.stdout))


class PostLogger:
    def __init__(self, log_file: Optional[str | Path] = None):
        self.log_file = Path(log_file) if log_file else None

    def _write(self, entry: dict) -> None:
        line = json.dumps(entry)
        _stdout_logger.info(line)
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a") as f:
                f.write(line + "\n")

    def log_success(
        self,
        niche: str,
        topic: str,
        post_id: str,
        url: str,
        video_path: str,
    ) -> None:
        self._write(
            {
                "event": "publish_success",
                "niche": niche,
                "topic": topic,
                "post_id": post_id,
                "url": url,
                "video_path": str(video_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def log_failure(
        self,
        niche: str,
        topic: str,
        error: str,
        attempt: int,
        video_path: str,
    ) -> None:
        self._write(
            {
                "event": "publish_failure",
                "niche": niche,
                "topic": topic,
                "error": error,
                "attempt": attempt,
                "video_path": str(video_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def log_retry(self, niche: str, topic: str, attempt: int, wait_seconds: float) -> None:
        self._write(
            {
                "event": "publish_retry",
                "niche": niche,
                "topic": topic,
                "attempt": attempt,
                "wait_seconds": wait_seconds,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
