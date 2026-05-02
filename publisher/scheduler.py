"""Cron-based scheduler for per-niche TikTok posting."""
import logging
import os
from pathlib import Path
from typing import Callable, Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import NicheConfig, load_all_niches
from .uploader import VideoUploader

logger = logging.getLogger(__name__)


class PostScheduler:
    def __init__(
        self,
        config_path: str | Path,
        video_dir: str | Path,
        access_token: Optional[str] = None,
        log_file: Optional[str | Path] = None,
        open_id: str = "",
        topic_resolver: Optional[Callable[[str, Path], tuple[Path, str]]] = None,
    ):
        """
        Args:
            config_path: path to niches YAML
            video_dir: directory where MP4 files are stored, organized as video_dir/<niche>/<file>.mp4
            access_token: TikTok access token (falls back to env var)
            log_file: path to write JSON post log
            open_id: TikTok open_id for building post URLs
            topic_resolver: callable(niche_name, video_dir) -> (video_path, topic)
                            defaults to picking the first mp4 and using its stem as topic
        """
        self.config_path = Path(config_path)
        self.video_dir = Path(video_dir)
        self.access_token = access_token
        self.log_file = log_file
        self.open_id = open_id
        self.topic_resolver = topic_resolver or self._default_topic_resolver
        self._scheduler = BlockingScheduler()

    @staticmethod
    def _default_topic_resolver(niche_name: str, video_dir: Path) -> tuple[Path, str]:
        niche_dir = video_dir / niche_name
        mp4s = sorted(niche_dir.glob("*.mp4"))
        if not mp4s:
            raise FileNotFoundError(f"No MP4 files in {niche_dir}")
        video = mp4s[0]
        return video, video.stem

    def _build_job(self, niche_name: str, niche_cfg: NicheConfig) -> Callable:
        def _post_job():
            logger.info("Scheduler triggered post for niche=%s", niche_name)
            try:
                video_path, topic = self.topic_resolver(niche_name, self.video_dir)
                uploader = VideoUploader(access_token=self.access_token, log_file=self.log_file)
                result = uploader.upload(video_path, niche_name, topic, niche_cfg, open_id=self.open_id)
                logger.info("Posted niche=%s post_id=%s url=%s", niche_name, result["post_id"], result["url"])
            except Exception as exc:
                logger.error("Scheduler post failed niche=%s: %s", niche_name, exc)

        return _post_job

    def register_niches(self, niches: Optional[dict[str, NicheConfig]] = None) -> None:
        configs = niches or load_all_niches(self.config_path)
        for name, cfg in configs.items():
            trigger = CronTrigger.from_crontab(cfg.schedule.cron, timezone=cfg.schedule.timezone)
            self._scheduler.add_job(
                self._build_job(name, cfg),
                trigger=trigger,
                id=f"post_{name}",
                name=f"Post niche={name}",
                replace_existing=True,
            )
            logger.info("Registered schedule for niche=%s cron='%s' tz=%s", name, cfg.schedule.cron, cfg.schedule.timezone)

    def run(self) -> None:
        """Start the blocking scheduler. Blocks until interrupted."""
        if not self._scheduler.get_jobs():
            self.register_niches()
        logger.info("Starting PostScheduler with %d jobs", len(self._scheduler.get_jobs()))
        self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown()
